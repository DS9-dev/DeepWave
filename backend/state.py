"""Состояние приложения: настройки, история, статистика, кеш обложек, недоступные треки, страны исполнителей.

Хранится отдельно от каталогов (catalog/app_state.sqlite), каталоги пишет только сборщик.

История устроена в два уровня:
  * plays        — подробный журнал прослушиваний (для раздела «История»), хранится ограниченный срок;
  * track_stats  — долгосрочная статистика по каждому треку (сколько раз, когда последний раз, сколько
                   дослушано). Она не очищается, и именно по ней работает защита от повторов, поэтому
                   очистка журнала не возвращает в эфир недавно звучавшие треки.
"""
import json
import os
import sqlite3
import threading
import time

DEFAULTS = {
    "genres": [],                 # выбранные жанры: ["allrock:rusrock", ...] (каталог:жанр)
    "volume": 0.8,
    "exclude_ru": False,          # устарело: переносится в country_mode/country_list
    "country_mode": "all",        # all | include | exclude
    "country_list": [],           # ISO-коды стран (как в MusicBrainz): ["RU", "SU", ...]
    "country_unknown": True,      # пропускать исполнителей с неизвестной страной (в режимах include/exclude)
    "artist_cooldown": 150,       # сколько последних исполнителей не повторять
    "track_cooldown_days": 30,    # трек не повторяется N дней
    "theme": "auto",
    "history_months": 12,         # срок хранения подробной истории: 3, 6, 12 или 0 (бессрочно)
}
HISTORY_CHOICES = (3, 6, 12, 0)
COUNTRY_MODES = ("all", "include", "exclude")
MONTH = 30.44 * 86400
COVER_TTL = 120 * 86400           # ссылка на обложку, не использованная 120 дней, удаляется (её можно получить снова)
UNAVAILABLE_TTL = 60 * 86400      # отметка «трек недоступен» действует 14 дней, хранится 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS plays(
  id INTEGER PRIMARY KEY, track_id TEXT, catalog TEXT, artist_id INTEGER, title TEXT, artists TEXT,
  title_norm TEXT, started_at REAL, listened_s REAL DEFAULT 0, duration_s REAL, status TEXT DEFAULT 'playing',
  origin TEXT);
CREATE INDEX IF NOT EXISTS ix_plays_time ON plays(started_at);
CREATE INDEX IF NOT EXISTS ix_plays_track ON plays(track_id);
CREATE TABLE IF NOT EXISTS track_stats(
  track_id TEXT PRIMARY KEY, artist_id INTEGER, title_norm TEXT, plays INTEGER DEFAULT 0, finished INTEGER DEFAULT 0,
  skipped INTEGER DEFAULT 0, listened_s REAL DEFAULT 0, first_played REAL, last_played REAL);
CREATE INDEX IF NOT EXISTS ix_stats_last ON track_stats(last_played);
CREATE TABLE IF NOT EXISTS covers(track_id TEXT PRIMARY KEY, url TEXT, album_cover TEXT, at REAL);
CREATE TABLE IF NOT EXISTS unavailable(track_id TEXT PRIMARY KEY, reason TEXT, at REAL);
CREATE TABLE IF NOT EXISTS artist_meta(artist_id INTEGER PRIMARY KEY, countries TEXT, fetched_at REAL, cover TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS artist_country(
  artist_id INTEGER PRIMARY KEY, status TEXT, country TEXT, source TEXT, mbid TEXT, mb_name TEXT,
  note TEXT, checked_at REAL, attempts INTEGER DEFAULT 0, raw TEXT, albums TEXT);
"""


class State:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA journal_size_limit=8388608")      # журнал WAL после сброса не больше 8 МБ
        self.db.executescript(SCHEMA)
        if "cover" not in {r[1] for r in self.db.execute("PRAGMA table_info(artist_meta)")}:
            self.db.execute("ALTER TABLE artist_meta ADD COLUMN cover TEXT")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(artist_country)")}
        for col in ("raw", "albums"):
            if col not in have:
                self.db.execute(f"ALTER TABLE artist_country ADD COLUMN {col} TEXT")
        # записи, оставшиеся «играющими» после прошлого закрытия приложения
        self.db.execute("UPDATE plays SET status='stopped' WHERE status='playing'")
        self.lock = threading.RLock()
        self._migrate_stats()
        self.migrate_country_setting()

    def _migrate_stats(self):
        """Однократно: статистика по трекам из уже накопленной истории (без потери данных)."""
        if self.kv("schema_stats") == 1:
            return
        with self.lock:
            self.db.execute("BEGIN")
            self.db.execute("""
                INSERT OR REPLACE INTO track_stats(track_id, artist_id, title_norm, plays, finished, skipped, listened_s,
                                                   first_played, last_played)
                SELECT track_id, MAX(artist_id), MAX(title_norm), COUNT(*), SUM(status='finished'),
                       SUM(status='skipped'), COALESCE(SUM(listened_s), 0), MIN(started_at), MAX(started_at)
                FROM plays WHERE track_id IS NOT NULL GROUP BY track_id""")
            self.db.execute("INSERT OR REPLACE INTO kv VALUES('schema_stats', '1')")
            self.db.execute("COMMIT")

    def q(self, sql, *p):
        with self.lock:
            return self.db.execute(sql, p).fetchall()

    def x(self, sql, *p):
        with self.lock:
            return self.db.execute(sql, p)

    # --- настройки
    def settings(self):
        out = dict(DEFAULTS)
        for k, v in self.q("SELECT k, v FROM settings"):
            try:
                out[k] = json.loads(v)
            except ValueError:
                pass
        if out.get("history_months") not in HISTORY_CHOICES:
            out["history_months"] = DEFAULTS["history_months"]
        if out.get("country_mode") not in COUNTRY_MODES:
            out["country_mode"] = "all"
        if not isinstance(out.get("country_list"), list):
            out["country_list"] = []
        return out

    def set_settings(self, patch):
        with self.lock:
            for k, v in patch.items():
                if k == "history_months" and v not in HISTORY_CHOICES:
                    continue
                if k == "country_mode" and v not in COUNTRY_MODES:
                    continue
                if k == "country_list":
                    if not isinstance(v, list):
                        continue
                    v = sorted({str(c).strip().upper() for c in v if 2 <= len(str(c).strip()) <= 3})
                if k == "country_unknown":
                    v = bool(v)
                if k in DEFAULTS:
                    self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, json.dumps(v)))
        return self.settings()

    def kv(self, k, v=None):
        if v is None:
            r = self.q("SELECT v FROM kv WHERE k=?", k)
            return json.loads(r[0][0]) if r else None
        self.x("INSERT OR REPLACE INTO kv VALUES(?,?)", k, json.dumps(v))

    # --- история и статистика
    def play_start(self, t, origin):
        now = time.time()
        with self.lock:
            self.db.execute("BEGIN")
            cur = self.db.execute(
                "INSERT INTO plays(track_id,catalog,artist_id,title,artists,title_norm,started_at,duration_s,origin) "
                "VALUES(?,?,?,?,?,?,?,?,?)", (t["id"], t.get("catalog"), t.get("artist_id"), t.get("title"),
                                              t.get("artists"), t.get("title_norm"), now,
                                              (t.get("duration_ms") or 0) / 1000, origin))
            self.db.execute(
                "INSERT INTO track_stats(track_id, artist_id, title_norm, plays, first_played, last_played) "
                "VALUES(?,?,?,1,?,?) ON CONFLICT(track_id) DO UPDATE SET plays=plays+1, last_played=excluded.last_played, "
                "artist_id=COALESCE(excluded.artist_id, artist_id), title_norm=COALESCE(excluded.title_norm, title_norm)",
                (str(t["id"]), t.get("artist_id"), t.get("title_norm"), now, now))
            self.db.execute("COMMIT")
        return cur.lastrowid

    def play_end(self, play_id, listened, status):
        listened = float(listened or 0)
        with self.lock:
            row = self.db.execute("SELECT track_id, listened_s, status FROM plays WHERE id=?", (int(play_id),)).fetchone()
            if not row:
                return
            tid, old_listened, old_status = row
            self.db.execute("BEGIN")
            self.db.execute("UPDATE plays SET listened_s=?, status=? WHERE id=?", (listened, status, int(play_id)))
            first_end = old_status in ("playing", "stopped", None)
            self.db.execute(
                "UPDATE track_stats SET listened_s=listened_s+?, finished=finished+?, skipped=skipped+? WHERE track_id=?",
                (max(0.0, listened - (old_listened or 0)), int(first_end and status == "finished"),
                 int(first_end and status == "skipped"), tid))
            self.db.execute("COMMIT")

    def history(self, limit=200, offset=0):
        rows = self.q("SELECT id, track_id, catalog, artist_id, title, artists, started_at, listened_s, duration_s, "
                      "status, origin FROM plays ORDER BY id DESC LIMIT ? OFFSET ?", limit, offset)
        keys = ["play_id", "id", "catalog", "artist_id", "title", "artists", "started_at", "listened_s",
                "duration_s", "status", "origin"]
        return [dict(zip(keys, r)) for r in rows]

    def recent_artists(self, n):
        """n последних различных исполнителей — по долгосрочной статистике."""
        if n <= 0:
            return []
        out, seen = [], set()
        for (aid,) in self.q("SELECT artist_id FROM track_stats WHERE artist_id IS NOT NULL "
                             "ORDER BY last_played DESC LIMIT ?", n * 4):
            if aid not in seen:
                seen.add(aid)
                out.append(aid)
                if len(out) >= n:
                    break
        return out

    def recent_tracks(self, days):
        since = time.time() - days * 86400
        return {r[0] for r in self.q("SELECT track_id FROM track_stats WHERE last_played>=?", since)}

    def recent_titles(self, days=7):
        since = time.time() - days * 86400
        return {(r[0], r[1]) for r in self.q("SELECT artist_id, title_norm FROM track_stats WHERE last_played>=?", since)}

    def stats(self):
        r = self.q("SELECT COALESCE(SUM(plays),0), COUNT(DISTINCT artist_id), COALESCE(SUM(listened_s),0), COUNT(*) "
                   "FROM track_stats")[0]
        return {"plays": r[0], "artists": r[1], "hours": round(r[2] / 3600, 1), "tracks": r[3]}

    # --- обслуживание
    def prune_history(self, months=None):
        """Удаляет подробные записи старше срока хранения. Статистика по трекам не затрагивается."""
        months = self.settings()["history_months"] if months is None else months
        if not months:
            return 0
        cutoff = time.time() - months * MONTH
        return self.x("DELETE FROM plays WHERE started_at < ? AND status != 'playing'", cutoff).rowcount

    def prune_caches(self):
        now = time.time()
        covers = self.x("DELETE FROM covers WHERE at < ?", now - COVER_TTL).rowcount
        unavailable = self.x("DELETE FROM unavailable WHERE at < ?", now - UNAVAILABLE_TTL).rowcount
        return {"covers": covers, "unavailable": unavailable}

    def compact(self):
        """Сброс журнала WAL и уплотнение файла, если в нём много свободного места (быстро: база небольшая)."""
        with self.lock:
            free, total = (self.db.execute("PRAGMA freelist_count").fetchone()[0],
                           self.db.execute("PRAGMA page_count").fetchone()[0])
            vacuumed = total > 256 and free > total * 0.25
            if vacuumed:
                self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return vacuumed

    def maintain(self):
        t0 = time.time()
        removed = self.prune_history()
        caches = self.prune_caches()
        vacuumed = self.compact()
        res = {"history_removed": removed, **caches, "vacuumed": vacuumed, "at": time.time(),
               "seconds": round(time.time() - t0, 3)}
        self.kv("last_maintenance", res)
        return res

    def clear_cache(self):
        """«Очистить кеш»: только данные, которые можно получить снова (ссылки на обложки)."""
        n = self.x("DELETE FROM covers").rowcount
        self.compact()
        return n

    def storage(self):
        size = sum(os.path.getsize(p) for p in (str(self.path), f"{self.path}-wal", f"{self.path}-shm") if os.path.exists(p))
        oldest = self.q("SELECT MIN(started_at), COUNT(*) FROM plays")[0]
        return {"history_rows": oldest[1], "history_oldest": oldest[0], "history_months": self.settings()["history_months"],
                "stats": self.stats(), "covers_cached": self.q("SELECT COUNT(*) FROM covers")[0][0],
                "state_db_bytes": size, "last_maintenance": self.kv("last_maintenance")}

    # --- обложки и недоступные треки
    def cover(self, track_id):
        r = self.q("SELECT url, at FROM covers WHERE track_id=?", str(track_id))
        if not r:
            return None
        url, at = r[0]
        if at is None or at < time.time() - 7 * 86400:      # недавно использованные обложки не устаревают
            self.x("UPDATE covers SET at=? WHERE track_id=?", time.time(), str(track_id))
        return url

    def set_cover(self, track_id, url):
        self.x("INSERT OR REPLACE INTO covers(track_id,url,at) VALUES(?,?,?)", str(track_id), url, time.time())

    def mark_unavailable(self, track_id, reason):
        self.x("INSERT OR REPLACE INTO unavailable VALUES(?,?,?)", str(track_id), reason[:200], time.time())

    def unavailable(self, days=14):
        since = time.time() - days * 86400
        return {r[0] for r in self.q("SELECT track_id FROM unavailable WHERE at>=?", since)}

    # --- страны исполнителей
    def set_artist_meta(self, rows):
        """rows: (artist_id, countries, cover_url) — данные одного пакетного запроса client.artists()."""
        with self.lock:
            self.db.execute("BEGIN")
            for aid, countries, cover in rows:
                self.db.execute("INSERT OR REPLACE INTO artist_meta(artist_id,countries,fetched_at,cover) VALUES(?,?,?,?)",
                                (int(aid), json.dumps(countries, ensure_ascii=False), time.time(), cover or ""))
            self.db.execute("COMMIT")

    def artist_covers(self, ids):
        if not ids:
            return {}
        out = {}
        for i in range(0, len(ids), 500):
            chunk = [int(x) for x in ids[i:i + 500]]
            q = ",".join("?" * len(chunk))
            out.update({r[0]: r[1] for r in self.q(f"SELECT artist_id, cover FROM artist_meta WHERE artist_id IN ({q})", *chunk)})
        return out

    # --- страны исполнителей (MusicBrainz); статус error — сбой связи, проверяется при следующем запуске
    def set_artist_country(self, aid, res):
        """res: status, countries (ISO-коды), source, mbid, mb_name, note, raw (как прислал источник), albums."""
        cs = ",".join(res.get("countries") or ([res["country"]] if res.get("country") else []))
        self.x("INSERT INTO artist_country(artist_id,status,country,source,mbid,mb_name,note,checked_at,attempts,raw,albums) "
               "VALUES(?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT(artist_id) DO UPDATE SET status=excluded.status, "
               "country=excluded.country, source=excluded.source, mbid=excluded.mbid, mb_name=excluded.mb_name, "
               "note=excluded.note, checked_at=excluded.checked_at, attempts=artist_country.attempts+1, "
               "raw=COALESCE(excluded.raw, artist_country.raw), albums=COALESCE(excluded.albums, artist_country.albums)",
               int(aid), res.get("status"), cs or None, res.get("source"), res.get("mbid"), res.get("mb_name"),
               (res.get("note") or "")[:200], time.time(),
               json.dumps(res["raw"], ensure_ascii=False) if res.get("raw") is not None else None,
               json.dumps(res["albums"][:10], ensure_ascii=False) if res.get("albums") else None)

    def artist_country_rows(self):
        """{artist_id: (status, country, checked_at, source)}"""
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in
                self.q("SELECT artist_id, status, country, checked_at, source FROM artist_country")}

    def artist_country_albums(self, ids):
        out = {}
        ids = list(ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for aid, al in self.q(f"SELECT artist_id, albums FROM artist_country WHERE albums IS NOT NULL "
                                  f"AND artist_id IN ({q})", *chunk):
                out[aid] = json.loads(al)
        return out

    def artist_countries(self):
        """{artist_id: frozenset(ISO-кодов)} — только достоверно определённые."""
        return {r[0]: frozenset(r[1].split(",")) for r in self.q(
            "SELECT artist_id, country FROM artist_country WHERE status='matched' AND country IS NOT NULL AND country<>''")}

    def migrate_country_setting(self):
        """Старая галочка «исключать российских» → режим «Исключить выбранные страны» с Россией."""
        if self.kv("country_setting_migrated"):
            return
        have = {r[0] for r in self.q("SELECT k FROM settings")}
        if "country_mode" not in have and self.settings().get("exclude_ru"):
            self.set_settings({"country_mode": "exclude", "country_list": ["RU"], "country_unknown": True})
        self.kv("country_setting_migrated", 1)
