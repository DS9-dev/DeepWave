"""Состояние приложения: настройки, история, обложки, недоступные треки, страны исполнителей.

Хранится отдельно от каталогов (catalog/app_state.sqlite), каталоги пишет только сборщик.
"""
import json
import sqlite3
import threading
import time

DEFAULTS = {
    "genres": [],                 # выбранные жанры: ["allrock:rusrock", ...] (каталог:жанр)
    "volume": 0.8,
    "exclude_ru": False,
    "artist_cooldown": 150,       # сколько последних исполнителей не повторять
    "track_cooldown_days": 30,    # трек не повторяется N дней
    "theme": "auto",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS plays(
  id INTEGER PRIMARY KEY, track_id TEXT, catalog TEXT, artist_id INTEGER, title TEXT, artists TEXT,
  title_norm TEXT, started_at REAL, listened_s REAL DEFAULT 0, duration_s REAL, status TEXT DEFAULT 'playing',
  origin TEXT);
CREATE INDEX IF NOT EXISTS ix_plays_time ON plays(started_at);
CREATE INDEX IF NOT EXISTS ix_plays_track ON plays(track_id);
CREATE TABLE IF NOT EXISTS covers(track_id TEXT PRIMARY KEY, url TEXT, album_cover TEXT, at REAL);
CREATE TABLE IF NOT EXISTS unavailable(track_id TEXT PRIMARY KEY, reason TEXT, at REAL);
CREATE TABLE IF NOT EXISTS artist_meta(artist_id INTEGER PRIMARY KEY, countries TEXT, fetched_at REAL, cover TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class State:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if "cover" not in {r[1] for r in self.db.execute("PRAGMA table_info(artist_meta)")}:
            self.db.execute("ALTER TABLE artist_meta ADD COLUMN cover TEXT")
        # записи, оставшиеся «играющими» после прошлого закрытия приложения
        self.db.execute("UPDATE plays SET status='stopped' WHERE status='playing'")
        self.lock = threading.RLock()

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
        return out

    def set_settings(self, patch):
        with self.lock:
            for k, v in patch.items():
                if k in DEFAULTS:
                    self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, json.dumps(v)))
        return self.settings()

    def kv(self, k, v=None):
        if v is None:
            r = self.q("SELECT v FROM kv WHERE k=?", k)
            return json.loads(r[0][0]) if r else None
        self.x("INSERT OR REPLACE INTO kv VALUES(?,?)", k, json.dumps(v))

    # --- история
    def play_start(self, t, origin):
        cur = self.x("INSERT INTO plays(track_id,catalog,artist_id,title,artists,title_norm,started_at,duration_s,origin) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", t["id"], t.get("catalog"), t.get("artist_id"), t.get("title"),
                     t.get("artists"), t.get("title_norm"), time.time(), (t.get("duration_ms") or 0) / 1000, origin)
        return cur.lastrowid

    def play_end(self, play_id, listened, status):
        self.x("UPDATE plays SET listened_s=?, status=? WHERE id=?", float(listened or 0), status, int(play_id))

    def history(self, limit=200, offset=0):
        rows = self.q("SELECT id, track_id, catalog, artist_id, title, artists, started_at, listened_s, duration_s, "
                      "status, origin FROM plays ORDER BY id DESC LIMIT ? OFFSET ?", limit, offset)
        keys = ["play_id", "id", "catalog", "artist_id", "title", "artists", "started_at", "listened_s",
                "duration_s", "status", "origin"]
        return [dict(zip(keys, r)) for r in rows]

    def recent_artists(self, n):
        return [r[0] for r in self.q("SELECT artist_id FROM plays WHERE artist_id IS NOT NULL "
                                     "ORDER BY id DESC LIMIT ?", n)]

    def recent_tracks(self, days):
        since = time.time() - days * 86400
        return {r[0] for r in self.q("SELECT track_id FROM plays WHERE started_at>=?", since)}

    def recent_titles(self, days=7):
        since = time.time() - days * 86400
        return {(r[0], r[1]) for r in self.q("SELECT artist_id, title_norm FROM plays WHERE started_at>=?", since)}

    def stats(self):
        r = self.q("SELECT COUNT(*), COUNT(DISTINCT artist_id), COALESCE(SUM(listened_s),0) FROM plays")[0]
        return {"plays": r[0], "artists": r[1], "hours": round(r[2] / 3600, 1)}

    # --- обложки и недоступные треки
    def cover(self, track_id):
        r = self.q("SELECT url FROM covers WHERE track_id=?", str(track_id))
        return r[0][0] if r else None

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

    def countries(self):
        return {r[0]: json.loads(r[1] or "[]") for r in self.q("SELECT artist_id, countries FROM artist_meta")}
