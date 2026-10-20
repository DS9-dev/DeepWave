"""
DeepWave — накопительный сборщик каталога Яндекс Музыки по жанрам.

Каталог хранится в одном файле SQLite (catalog/<имя>.sqlite) и пополняется
между запусками. Остановка в любой момент (Ctrl+C) безопасна.

Команды (PowerShell, из папки проекта):
  python crawler.py genres                              # список id жанров
  python crawler.py run                                 # сбор (allrock, 300 запросов)
  python crawler.py run --max-requests 1500             # длинный сбор
  python crawler.py run --max-minutes 120 --max-requests 100000
  python crawler.py run --genres allrock,alternative,punk,metal,indie --catalog rock_wide
  python crawler.py status                              # прогресс и статистика
  python crawler.py export                              # CSV-выгрузка

Ctrl+C один раз — дождаться конца текущей задачи и выйти;
Ctrl+C второй раз — выйти немедленно (незавершённая задача будет повторена).
"""
import argparse
import csv
import json
import os
import random
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAT_DIR = ROOT / "catalog"
SOURCES = ["label", "new_release", "similar", "coartist", "seed"]   # очередь исполнителей по кругу
SCHEDULE = ["disco", "disco", "similar", "disco", "label", "disco", "similar", "label"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS artists(
  id INTEGER PRIMARY KEY, name TEXT, genres TEXT,
  scope INTEGER,                     -- 1 жанр подходит, 0 нет, NULL неизвестно
  source TEXT, depth INTEGER DEFAULT 0, found_run INTEGER, found_at TEXT,
  disco_status TEXT DEFAULT 'pending', disco_tracks INTEGER, disco_in_scope INTEGER, disco_at TEXT,
  similar_status TEXT DEFAULT 'pending', errors INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS albums(
  id INTEGER PRIMARY KEY, title TEXT, version TEXT, year INTEGER, genre TEXT, type TEXT,
  track_count INTEGER, likes_count INTEGER, main_artist_id INTEGER, scope INTEGER,
  found_run INTEGER, found_at TEXT);
CREATE TABLE IF NOT EXISTS album_labels(album_id INTEGER, label_id INTEGER, PRIMARY KEY(album_id, label_id));
CREATE TABLE IF NOT EXISTS labels(
  id INTEGER PRIMARY KEY, name TEXT, status TEXT DEFAULT 'pending', next_page INTEGER DEFAULT 0,
  albums_seen INTEGER DEFAULT 0, albums_in_scope INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, found_run INTEGER);
CREATE TABLE IF NOT EXISTS tracks(
  id TEXT PRIMARY KEY, title TEXT, version TEXT, duration_ms INTEGER, album_id INTEGER,
  main_artist_id INTEGER, artists TEXT, album_genre TEXT, scope INTEGER, artist_rank INTEGER,
  found_run INTEGER, found_at TEXT);
CREATE TABLE IF NOT EXISTS track_artists(track_id TEXT, artist_id INTEGER, PRIMARY KEY(track_id, artist_id));
CREATE TABLE IF NOT EXISTS seeds(
  key TEXT PRIMARY KEY, status TEXT DEFAULT 'pending', next_offset INTEGER DEFAULT 0,
  items INTEGER DEFAULT 0, total INTEGER, done_at TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, started TEXT, finished TEXT, args TEXT, requests INTEGER DEFAULT 0,
  errors INTEGER DEFAULT 0, tasks INTEGER DEFAULT 0, new_artists INTEGER DEFAULT 0,
  new_albums INTEGER DEFAULT 0, new_tracks INTEGER DEFAULT 0, stop_reason TEXT);
CREATE INDEX IF NOT EXISTS ix_art_q ON artists(scope, disco_status, source);
CREATE INDEX IF NOT EXISTS ix_trk_artist ON tracks(main_artist_id);
CREATE INDEX IF NOT EXISTS ix_ta_artist ON track_artists(artist_id);
"""


def now():
    return datetime.now().isoformat(timespec="seconds")


# ======================================================================= API
class Fatal(Exception):
    pass


class Budget(Exception):
    pass


class TaskFailed(Exception):
    pass


class Api:
    """Все запросы идут через call(): пауза, ретраи с backoff, учёт бюджета."""

    def __init__(self, client, delay, max_requests, deadline, log):
        self.c, self.delay, self.max, self.deadline, self.log = client, delay, max_requests, deadline, log
        self.requests = self.errors = self.consec_fail = 0
        self._last = 0.0
        try:
            from yandex_music import exceptions as ex
            self.ex = ex
        except Exception:
            self.ex = None

    def _is(self, e, name):
        cls = getattr(self.ex, name, None) if self.ex else None
        return (cls is not None and isinstance(e, cls)) or type(e).__name__ == name

    def call(self, fn, *a, **kw):
        for attempt in range(5):
            if self.requests >= self.max:
                raise Budget("лимит запросов")
            if self.deadline and time.time() > self.deadline:
                raise Budget("лимит времени")
            wait = self._last + self.delay * random.uniform(0.8, 1.3) - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.requests += 1
            try:
                res = fn(*a, **kw)
                self.consec_fail = 0
                return res
            except Exception as e:
                self.errors += 1
                if self._is(e, "UnauthorizedError"):
                    raise Fatal("токен недействителен: python rock_probe.py --logout, затем запустите снова")
                if self._is(e, "NotFoundError") or self._is(e, "BadRequestError"):
                    return None                      # объекта нет — не повторяем
                msg = str(e)[:150]
                transient = (self._is(e, "TimedOutError") or self._is(e, "NetworkError")
                             or any(x in msg for x in ("429", "500", "502", "503", "504", "timed out")))
                if not transient:
                    raise TaskFailed(f"{type(e).__name__}: {msg}")
                self.consec_fail += 1
                if "429" in msg:
                    self.delay = min(self.delay * 1.5, 5.0)
                pause = min(120, 3 * 2 ** attempt) + random.uniform(0, 2)
                self.log(f"  сеть/сервер: {type(e).__name__} {msg[:80]} — пауза {pause:.0f} c (попытка {attempt + 1}/5)")
                if self.consec_fail >= 15:
                    raise Fatal("API недоступен (15 ошибок подряд). Запустите позже — прогресс сохранён.")
                time.sleep(pause)
        raise TaskFailed("исчерпаны повторы")


# ======================================================================= хранилище
class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.run_id = None
        self.scope = set()
        self.new = {"artists": 0, "albums": 0, "tracks": 0}

    def _migrate(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(artists)")}
        if "disco_page" not in cols:          # v3: отложенные страницы больших нежанровых дискографий
            self.db.execute("ALTER TABLE artists ADD COLUMN disco_page INTEGER DEFAULT 0")
        if self.one("SELECT v FROM meta WHERE k='schema'") == "2":
            return
        # v1 ставил в очередь соавторов треков чужих жанров и соавторов с известными чужими жанрами
        linked = """EXISTS (SELECT 1 FROM track_artists ta JOIN tracks t ON t.id=ta.track_id
                            WHERE ta.artist_id=artists.id AND t.scope=1)"""
        overlap = """EXISTS (SELECT 1 FROM json_each(artists.genres) g WHERE g.value IN
                             (SELECT j.value FROM meta m, json_each(m.v) j WHERE m.k='scope'))"""
        base = "disco_status='pending' AND source='coartist'"
        self.db.execute(f"UPDATE artists SET scope=1 WHERE {base} AND scope IS NULL AND {linked}")
        self.db.execute(f"UPDATE artists SET scope=0 WHERE {base} AND scope IS NULL")
        self.db.execute(f"UPDATE artists SET scope=0 WHERE {base} AND scope=1 AND genres!='[]' AND NOT {overlap}")
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('schema','2')")

    def q(self, sql, *p):
        return self.db.execute(sql, p).fetchall()

    def one(self, sql, *p):
        r = self.db.execute(sql, p).fetchone()
        return r[0] if r else None

    def meta(self, k, v=None):
        if v is None:
            return self.one("SELECT v FROM meta WHERE k=?", k)
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))

    def begin(self):
        self.db.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.db.execute("COMMIT")

    def rollback(self):
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")

    # --- сущности (идемпотентно; повтор задачи не создаёт дублей)
    def artist(self, a, source, scope_hint=None, depth=0, strong=False):
        """strong=True — исполнитель альбома нужного жанра (жанр подтверждён альбомом);
        иначе подсказка слабая (соавтор трека) и известные чужие жанры её перевешивают."""
        if a is None or getattr(a, "id", None) is None:
            return
        aid, genres = int(a.id), list(getattr(a, "genres", None) or [])
        if strong and scope_hint == 1:
            scope = 1
        elif genres:
            scope = 1 if set(genres) & self.scope else 0
        else:
            scope = scope_hint
        row = self.db.execute("SELECT scope, genres FROM artists WHERE id=?", (aid,)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO artists(id,name,genres,scope,source,depth,found_run,found_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", (aid, a.name, json.dumps(genres, ensure_ascii=False),
                                                        scope, source, depth, self.run_id, now()))
            self.new["artists"] += 1
            return
        old_scope, old_genres = row
        if scope == 1 and old_scope != 1 or (old_scope is None and scope is not None):
            self.db.execute("UPDATE artists SET scope=? WHERE id=?", (scope, aid))
        if genres and old_genres in (None, "[]"):
            self.db.execute("UPDATE artists SET genres=? WHERE id=?", (json.dumps(genres, ensure_ascii=False), aid))

    def album(self, al, source, register_artists=True):
        if al is None or getattr(al, "id", None) is None:
            return None
        genre = al.genre or ""
        scope = 1 if genre in self.scope else 0
        arts = list(al.artists or [])
        cur = self.db.execute(
            "INSERT OR IGNORE INTO albums(id,title,version,year,genre,type,track_count,likes_count,main_artist_id,"
            "scope,found_run,found_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(al.id), al.title, getattr(al, "version", None), al.year, genre, al.type, al.track_count,
             getattr(al, "likes_count", None), int(arts[0].id) if arts and arts[0].id else None, scope,
             self.run_id, now()))
        if cur.rowcount:
            self.new["albums"] += 1
        elif getattr(al, "likes_count", None) is not None:
            self.db.execute("UPDATE albums SET likes_count=? WHERE id=?", (al.likes_count, int(al.id)))
        if scope:
            for lb in getattr(al, "labels", None) or []:
                lid = getattr(lb, "id", None)
                if lid is None:
                    continue
                self.db.execute("INSERT OR IGNORE INTO labels(id,name,found_run) VALUES(?,?,?)",
                                (int(lid), getattr(lb, "name", None), self.run_id))
                self.db.execute("INSERT OR IGNORE INTO album_labels VALUES(?,?)", (int(al.id), int(lid)))
            if register_artists:
                for a in arts:
                    self.artist(a, source, scope_hint=1, strong=True)
        return scope

    def track(self, t, source, rank=None):
        if t is None or getattr(t, "id", None) is None:
            return 0
        al = (t.albums or [None])[0]
        genre = (al.genre if al else "") or ""
        scope = 1 if genre in self.scope else 0
        arts = [a for a in (t.artists or []) if getattr(a, "id", None) is not None]
        if al is not None:
            self.album(al, source, register_artists=False)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO tracks(id,title,version,duration_ms,album_id,main_artist_id,artists,album_genre,"
            "scope,artist_rank,found_run,found_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(t.id), t.title, getattr(t, "version", None), getattr(t, "duration_ms", None),
             int(al.id) if al and al.id else None, int(arts[0].id) if arts else None,
             ", ".join(a.name or "" for a in arts), genre, scope, rank, self.run_id, now()))
        if cur.rowcount:
            self.new["tracks"] += 1
        elif rank is not None:
            self.db.execute("UPDATE tracks SET artist_rank=MIN(COALESCE(artist_rank,?),?) WHERE id=?",
                            (rank, rank, str(t.id)))
        for a in arts:
            self.db.execute("INSERT OR IGNORE INTO track_artists VALUES(?,?)", (str(t.id), int(a.id)))
            if scope:   # соавторы треков нужного жанра — новые кандидаты; прочие только связываются
                self.artist(a, "coartist", scope_hint=1)
        return scope


# ======================================================================= задачи
class Crawler:
    def __init__(self, client, store, api, args, log):
        self.c, self.s, self.api, self.a, self.log = client, store, api, args, log
        self.src_i = 0
        self.stop = False
        self.cur = None

    # --- 0. жанровое дерево
    def resolve_scope(self, wanted):
        stored = self.s.meta("scope")
        if stored:
            scope = set(json.loads(stored))
            if wanted and set(wanted) - scope - set(json.loads(self.s.meta("roots") or "[]")):
                raise Fatal(f"Каталог создан для жанров {self.s.meta('roots')}. Для других жанров "
                            f"укажите новый --catalog.")
            self.s.scope = scope
            return
        genres = self.api.call(self.c.genres) or []
        flat = {}

        def walk(g, parent):
            flat[g.id] = parent
            for sub in g.sub_genres or []:
                walk(sub, g.id)
        for g in genres:
            walk(g, None)
        bad = [w for w in wanted if w not in flat]
        if bad:
            raise Fatal(f"Неизвестные жанры: {bad}. Список: python crawler.py genres")

        def fam(gid):
            out = {gid}
            for k, p in flat.items():
                if p == gid:
                    out |= fam(k)
            return out
        scope = set().union(*(fam(w) for w in wanted))
        self.s.meta("scope", json.dumps(sorted(scope)))
        self.s.meta("roots", json.dumps(wanted))
        self.s.scope = scope
        self.log(f"Жанры каталога ({len(scope)}): {', '.join(sorted(scope))}")

    def ensure_seeds(self):
        for g in sorted(self.s.scope):
            for key in (f"mt_albums:{g}:popular", f"mt_albums:{g}:new", f"mt_artists:{g}"):
                self.s.db.execute("INSERT OR IGNORE INTO seeds(key) VALUES(?)", (key,))
        self.s.db.execute("INSERT OR IGNORE INTO seeds(key) VALUES('new_releases')")
        # новинки обновляются раз в сутки
        last = self.s.one("SELECT done_at FROM seeds WHERE key='new_releases'")
        if last and datetime.fromisoformat(last) < datetime.now() - timedelta(days=1):
            self.s.db.execute("UPDATE seeds SET status='pending', next_offset=0 WHERE key='new_releases'")

    # --- 1. стартовые точки (жанровые страницы, новинки) — постранично
    def task_seed(self):
        row = self.s.q("SELECT key,next_offset FROM seeds WHERE status='pending' ORDER BY random() LIMIT 1")
        if not row:
            return False
        key, off = row[0]
        self.cur = ("seeds", key)
        if key == "new_releases":
            return self._seed_new_releases(off)
        kind, g, *rest = key.split(":")
        if kind == "mt_albums":
            res = self.api.call(self.c.metatag_albums, g, sort_by=rest[0], offset=off, limit=100)
            items = getattr(res, "albums", None) or []
            self.s.begin()
            for al in items:
                self.s.album(al, "seed")
        else:
            res = self.api.call(self.c.metatag_artists, g, period="month", offset=off, limit=50)
            items = getattr(res, "artists", None) or []
            self.s.begin()
            for it in items:
                self.s.artist(getattr(it, "artist", None) or it, "seed", scope_hint=1)
        total = getattr(getattr(res, "pager", None), "total", None)
        page = 100 if kind == "mt_albums" else 50
        done = len(items) < page or off >= 5000
        self.s.db.execute("UPDATE seeds SET next_offset=?, items=items+?, total=?, status=?, done_at=? WHERE key=?",
                          (off + page, len(items), total, "done" if done else "pending", now(), key))
        self.s.commit()
        return True

    def _seed_new_releases(self, off):
        ids = list(getattr(self.api.call(self.c.new_releases), "new_releases", None) or [])
        chunk = ids[off:off + 100]
        albums = self.api.call(self.c.albums, chunk) if chunk else []
        self.s.begin()
        for al in albums or []:
            self.s.album(al, "new_release")
        done = off + 100 >= len(ids)
        self.s.db.execute("UPDATE seeds SET next_offset=?, items=items+?, total=?, status=?, done_at=? "
                          "WHERE key='new_releases'", (0 if done else off + 100, len(chunk), len(ids),
                                                         "done" if done else "pending", now()))
        self.s.commit()
        return True

    # --- 2. полная дискография исполнителя
    def pick_artist(self):
        # раз в 8 задач — исполнитель с неизвестным жанром (проверяется его дискографией)
        if random.random() < 0.125:
            r = self.s.q("SELECT id FROM artists WHERE scope IS NULL AND disco_status='pending' "
                         "ORDER BY random() LIMIT 1")
            if r:
                return r[0][0]
        for i in range(len(SOURCES)):           # источники по кругу: популярность не даёт приоритета
            src = SOURCES[(self.src_i + i) % len(SOURCES)]
            r = self.s.q("SELECT id FROM artists WHERE scope=1 AND disco_status='pending' AND source=? "
                         "ORDER BY found_run, random() LIMIT 1", src)
            if r:
                self.src_i = (self.src_i + i + 1) % len(SOURCES)
                return r[0][0]
        r = self.s.q("SELECT id FROM artists WHERE scope IS NULL AND disco_status='pending' LIMIT 1")
        return r[0][0] if r else None

    def task_disco(self):
        aid = self.pick_artist()
        if aid is None:
            return False
        self.cur = ("disco", aid)
        tracks, page, total = [], 0, None
        tagged = self.s.one("SELECT genres FROM artists WHERE id=?", aid) or "[]"
        tagged = bool(set(json.loads(tagged)) & self.s.scope)
        while page < 40:
            res = self.api.call(self.c.artists_tracks, aid, page=page, page_size=100)
            got = getattr(res, "tracks", None) or []
            total = getattr(getattr(res, "pager", None), "total", None) or total
            tracks += got
            page += 1
            if len(got) < 100 or (total is not None and len(tracks) >= total):
                break
            if page == 1 and not tagged:
                # большая дискография, а на первой странице почти нет нужного жанра:
                # остальные страницы откладываются (не отбрасываются) до опустения основной очереди
                fit = sum(1 for t in got if t.albums and (t.albums[0].genre or "") in self.s.scope)
                if fit < 0.1 * len(got):
                    self.s.begin()
                    in_scope = sum(self.s.track(t, "coartist", rank=i + 1) for i, t in enumerate(tracks))
                    self.s.db.execute("UPDATE artists SET disco_status='partial', disco_page=1, disco_tracks=?, "
                                      "disco_in_scope=?, disco_at=?, scope=CASE WHEN ?>0 THEN 1 ELSE scope END "
                                      "WHERE id=?", (len(tracks), in_scope, now(), in_scope, aid))
                    self.s.commit()
                    return True
        self.s.begin()
        in_scope = sum(self.s.track(t, "coartist", rank=i + 1) for i, t in enumerate(tracks))
        new_scope = 1 if in_scope else 0     # жанр исполнителя подтверждается его треками
        self.s.db.execute("UPDATE artists SET disco_status='done', disco_tracks=?, disco_in_scope=?, disco_at=?, "
                          "scope=? WHERE id=?", (len(tracks), in_scope, now(), new_scope, aid))
        self.s.commit()
        return True

    def task_deep(self):
        """Догрузка отложенных дискографий: до 5 страниц за задачу, с продолжением."""
        r = self.s.q("SELECT id, disco_page, disco_tracks, disco_in_scope FROM artists WHERE disco_status='partial' "
                     "ORDER BY disco_at LIMIT 1")
        if not r:
            return False
        aid, page, n, fit = r[0]
        self.cur = ("disco", aid)
        tracks, done = [], False
        for _ in range(5):
            res = self.api.call(self.c.artists_tracks, aid, page=page, page_size=100)
            got = getattr(res, "tracks", None) or []
            tracks += [(page * 100 + i + 1, t) for i, t in enumerate(got)]
            page += 1
            if len(got) < 100 or page >= 40:
                done = True
                break
        self.s.begin()
        add = sum(self.s.track(t, "coartist", rank=rank) for rank, t in tracks)
        fit, n = (fit or 0) + add, (n or 0) + len(tracks)
        self.s.db.execute("UPDATE artists SET disco_status=?, disco_page=?, disco_tracks=?, disco_in_scope=?, disco_at=?, "
                          "scope=CASE WHEN ?>0 THEN 1 WHEN ? THEN 0 ELSE scope END WHERE id=?",
                          ("done" if done else "partial", page, n, fit, now(), fit, done, aid))
        self.s.commit()
        return True

    # --- 3. похожие исполнители
    def task_similar(self):
        r = self.s.q("SELECT id, depth FROM artists WHERE scope=1 AND similar_status='pending' "
                     "ORDER BY disco_status='done' DESC, random() LIMIT 1")
        if not r:
            return False
        aid, depth = r[0]
        self.cur = ("similar", aid)
        res = self.api.call(self.c.artists_similar, aid)
        self.s.begin()
        for a in getattr(res, "similar_artists", None) or []:
            self.s.artist(a, "similar", depth=(depth or 0) + 1)
        self.s.db.execute("UPDATE artists SET similar_status='done' WHERE id=?", (aid,))
        self.s.commit()
        return True

    # --- 4. каталоги лейблов — постранично
    def task_label(self):
        r = self.s.q("SELECT id, next_page FROM labels WHERE status='pending' ORDER BY random() LIMIT 1")
        if not r:
            return False
        lid, page = r[0]
        self.cur = ("labels", lid)
        res = self.api.call(self.c.label_albums, lid, page=page, page_size=100)
        albums = getattr(res, "albums", None) or []
        self.s.begin()
        fit = sum(self.s.album(al, "label") or 0 for al in albums)
        seen, fit_total = self.s.q("SELECT albums_seen+?, albums_in_scope+? FROM labels WHERE id=?",
                                   len(albums), fit, lid)[0]
        low_yield = page >= 1 and fit_total < 0.05 * seen      # общий лейбл, жанра почти нет
        done = len(albums) < 100 or page >= 50 or low_yield
        self.s.db.execute("UPDATE labels SET next_page=?, albums_seen=albums_seen+?, albums_in_scope=albums_in_scope+?, "
                          "status=? WHERE id=?", (page + 1, len(albums), fit, "done" if done else "pending", lid))
        self.s.commit()
        return True

    # --- главный цикл
    def run(self):
        self.ensure_seeds()
        handlers = {"seed": self.task_seed, "disco": self.task_disco, "deep": self.task_deep,
                    "similar": self.task_similar, "label": self.task_label}
        tasks, slot, last_print = 0, 0, 0
        reason = "очередь пуста"
        try:
            while not self.stop:
                # стартовые точки — в первую очередь (их мало)
                order = SCHEDULE[slot:] + SCHEDULE[:slot]
                slot = (slot + 1) % len(SCHEDULE)
                # очередь дискографий длиннее --queue-target: расширение только раз в 10 задач,
                # чтобы обработка догоняла обнаружение и каждый найденный исполнитель дождался своей очереди
                if order[0] != "disco" and tasks % 10 and self.s.one(
                        "SELECT COUNT(*) FROM artists WHERE scope=1 AND disco_status='pending'") > self.a.queue_target:
                    order = ["disco"] + order
                order = (["deep"] + order) if tasks % 20 == 19 else order + ["deep"]   # отложенные — редко или в конце
                if self.s.one("SELECT 1 FROM seeds WHERE status='pending' LIMIT 1"):
                    order = ["seed"] + order
                did = False
                for kind in dict.fromkeys(order):
                    try:
                        if handlers[kind]():
                            did = True
                            break
                    except TaskFailed as e:
                        self.s.rollback()
                        self.log(f"  задача {kind} не удалась: {e}")
                        self._mark_failed(kind)
                        did = True
                        break
                if not did:
                    break
                tasks += 1
                self.flush_run(tasks)
                if time.time() - last_print > self.a.progress:
                    self.progress()
                    last_print = time.time()
            else:
                reason = "остановлено пользователем (Ctrl+C)"
        except Budget as e:
            self.s.rollback()
            reason = str(e)
        except Fatal as e:
            self.s.rollback()
            reason = f"ошибка: {e}"
        except KeyboardInterrupt:
            self.s.rollback()
            reason = "прервано (Ctrl+C ×2), незавершённая задача будет повторена"
        self.flush_run(tasks, reason)
        self.progress()
        self.log(f"Остановка: {reason}")
        return reason

    def _mark_failed(self, kind):
        """Объект, на котором задача упала 3 раза, помечается error и больше не выбирается."""
        if not self.cur:
            return
        table, oid = self.cur
        db = self.s.db
        if table == "seeds":
            db.execute("UPDATE seeds SET status='error', done_at=? WHERE key=?", (now(), oid))
        elif table == "labels":
            db.execute("UPDATE labels SET errors=errors+1, status=CASE WHEN errors+1>=3 THEN 'error' "
                       "ELSE status END WHERE id=?", (oid,))
        else:
            col = "disco_status" if table == "disco" else "similar_status"
            db.execute(f"UPDATE artists SET errors=errors+1, {col}=CASE WHEN errors+1>=3 THEN 'error' "
                       f"ELSE {col} END WHERE id=?", (oid,))

    def flush_run(self, tasks, reason=None):
        s = self.s
        s.db.execute("UPDATE runs SET requests=?, errors=?, tasks=?, new_artists=?, new_albums=?, new_tracks=?, "
                     "finished=?, stop_reason=COALESCE(?, stop_reason) WHERE id=?",
                     (self.api.requests, self.api.errors, tasks, s.new["artists"], s.new["albums"], s.new["tracks"],
                      now(), reason, s.run_id))

    def progress(self):
        s = self.s
        art_in = s.one("SELECT COUNT(*) FROM artists WHERE scope=1")
        done = s.one("SELECT COUNT(*) FROM artists WHERE scope=1 AND disco_status='done'")
        trk = s.one("SELECT COUNT(*) FROM tracks WHERE scope=1")
        self.log(f"[{datetime.now():%H:%M:%S}] запросов {self.api.requests}/{self.a.max_requests} | "
                 f"исполнители жанра: {art_in} (дискографий {done}) | треки жанра: {trk} | "
                 f"за запуск +{s.new['artists']} исп. +{s.new['tracks']} тр.")


# ======================================================================= команды
def open_store(args):
    name = args.catalog or "+".join(sorted(args.genres.split(",")))
    path = CAT_DIR / f"{name}.sqlite"
    return Store(path), path


class Lock:
    """Не даёт запустить два сборщика на один каталог одновременно."""

    def __init__(self, path):
        self.f = open(path.with_suffix(".lock"), "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit("Этот каталог уже собирается в другом окне.")


def cmd_run(args, client=None, log=print):
    store, path = open_store(args)
    lock = Lock(path)
    if client is None:
        from ym_auth import connect
        client, info = connect()
        log(f"Подключено (Плюс: {info['has_plus']}). Каталог: {path}")
    cur = store.db.execute("INSERT INTO runs(started, args) VALUES(?,?)", (now(), json.dumps(vars(args), default=str)))
    store.run_id = cur.lastrowid
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    api = Api(client, args.delay, args.max_requests, deadline, log)
    cr = Crawler(client, store, api, args, log)

    def on_sigint(sig, frame):
        if cr.stop:
            raise KeyboardInterrupt
        cr.stop = True
        log("\nCtrl+C: завершаю текущую задачу и сохраняю… (ещё раз Ctrl+C — выйти сразу)")
    old = signal.signal(signal.SIGINT, on_sigint)
    try:
        cr.resolve_scope([g.strip() for g in args.genres.split(",") if g.strip()])
        reason = cr.run()
    except Fatal as e:
        reason = str(e)
        log(f"Остановка: {e}")
        cr.flush_run(0, reason)
    finally:
        signal.signal(signal.SIGINT, old)
        store.rollback()
        store.db.close()
        lock.f.close()
    return reason


def cmd_status(args, log=print):
    store, path = open_store(args)
    if not path.exists() or not store.one("SELECT COUNT(*) FROM runs"):
        log("Каталог ещё пуст.")
        return
    q, one = store.q, store.one
    log(f"Каталог: {path}  ({path.stat().st_size / 1e6:.1f} МБ)")
    log(f"Жанры: {', '.join(json.loads(store.meta('scope') or '[]'))}")
    log(f"Исполнители жанра: {one('SELECT COUNT(*) FROM artists WHERE scope=1')}, "
        f"дискографий собрано: {one('SELECT COUNT(*) FROM artists WHERE scope=1 AND disco_status=?', 'done')}, "
        f"жанр не определён: {one('SELECT COUNT(*) FROM artists WHERE scope IS NULL')}, "
        f"вне жанра: {one('SELECT COUNT(*) FROM artists WHERE scope=0')}")
    log(f"Треки: {one('SELECT COUNT(*) FROM tracks')} (жанра: {one('SELECT COUNT(*) FROM tracks WHERE scope=1')}), "
        f"альбомы жанра: {one('SELECT COUNT(*) FROM albums WHERE scope=1')}, "
        f"лейблы: {one('SELECT COUNT(*) FROM labels')} (пройдено {one('SELECT COUNT(*) FROM labels WHERE status=?', 'done')})")
    log(f"Очередь дискографий: {one('SELECT COUNT(*) FROM artists WHERE scope=1 AND disco_status=?', 'pending')}, "
        f"очередь похожих: {one('SELECT COUNT(*) FROM artists WHERE scope=1 AND similar_status=?', 'pending')}, "
        f"отложенных больших дискографий: {one('SELECT COUNT(*) FROM artists WHERE disco_status=?', 'partial')}, "
        f"ошибочных: {one('SELECT COUNT(*) FROM artists WHERE disco_status=? OR similar_status=?', 'error', 'error')}")
    log(f"Стартовые точки: {one('SELECT COUNT(*) FROM seeds WHERE status=?', 'done')}/{one('SELECT COUNT(*) FROM seeds')}")
    log("\nИсполнители жанра по способу обнаружения (найдено / дискографий / медиана лайков их альбомов):")
    for src, n, d in q("SELECT source, COUNT(*), SUM(disco_status='done') FROM artists WHERE scope=1 "
                       "GROUP BY source ORDER BY 2 DESC"):
        likes = [r[0] for r in q("SELECT al.likes_count FROM albums al JOIN artists ar ON ar.id=al.main_artist_id "
                                 "WHERE ar.source=? AND al.scope=1 AND al.likes_count IS NOT NULL", src)]
        med = sorted(likes)[len(likes) // 2] if likes else "-"
        log(f"  {src:12} {n:7} / {d or 0:6} / {med}")
    log("\nЗапуски:")
    for r in q("SELECT id, started, requests, tasks, new_artists, new_tracks, stop_reason FROM runs ORDER BY id"):
        rate = f"{100 * r[4] / r[2]:.0f} исп./100 запр." if r[2] else ""
        log(f"  #{r[0]} {r[1]}  запросов {r[2]}, задач {r[3]}, +{r[4]} исп., +{r[5]} тр. {rate}  [{r[6]}]")
    store.db.close()


def cmd_export(args, log=print):
    store, path = open_store(args)
    out = path.parent / f"{path.stem}_export"
    out.mkdir(exist_ok=True)
    for name, sql in [
        ("tracks.csv", "SELECT t.id, t.title, t.version, t.artists, t.main_artist_id, t.album_id, al.title, al.year, "
                       "t.album_genre, t.scope, t.artist_rank, t.duration_ms, t.found_run FROM tracks t "
                       "LEFT JOIN albums al ON al.id=t.album_id"),
        ("artists.csv", "SELECT id, name, genres, scope, source, depth, disco_status, disco_tracks, disco_in_scope, "
                        "found_run FROM artists"),
        ("albums.csv", "SELECT id, title, version, year, genre, type, track_count, likes_count, main_artist_id, scope "
                       "FROM albums")]:
        cur = store.db.execute(sql)
        with open(out / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur)
    log(f"Выгружено в {out}")


def cmd_genres(args, log=print):
    from ym_auth import connect
    client, _ = connect()

    def walk(g, lvl):
        log(f"{'  ' * lvl}{g.id:22} {g.title}")
        for s in g.sub_genres or []:
            walk(s, lvl + 1)
    for g in client.genres():
        walk(g, 0)


def main(argv=None):
    p = argparse.ArgumentParser(description="Накопительный сборщик каталога Яндекс Музыки")
    p.add_argument("command", choices=["run", "status", "export", "genres"])
    p.add_argument("--genres", default="allrock", help="id жанров через запятую (с поджанрами)")
    p.add_argument("--catalog", default=None, help="имя каталога (по умолчанию из жанров)")
    p.add_argument("--max-requests", type=int, default=300)
    p.add_argument("--max-minutes", type=float, default=0)
    p.add_argument("--delay", type=float, default=0.6, help="минимальная пауза между запросами, с")
    p.add_argument("--progress", type=float, default=20, help="печать прогресса раз в N секунд")
    p.add_argument("--queue-target", type=int, default=500,
                   help="при большей очереди дискографий поиск новых исполнителей замедляется")
    args = p.parse_args(argv)
    import logging
    logging.getLogger("yandex_music").setLevel(logging.ERROR)   # без простыней schema-mismatch
    {"run": cmd_run, "status": cmd_status, "export": cmd_export, "genres": cmd_genres}[args.command](args)


if __name__ == "__main__":
    main()
