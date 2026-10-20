"""Каталоги (catalog/*.sqlite, только чтение), индекс в памяти, поиск и справедливый случайный выбор.

Выбор трека: сначала равновероятно выбирается ИСПОЛНИТЕЛЬ среди всех исполнителей выбранных
жанров (популярность и размер дискографии не дают преимущества), затем равновероятно — его трек.
Недавние исполнители, недавние треки и другие версии недавно сыгранной песни пропускаются.
"""
import json
import random
import re
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

RU_VALUES = {"ru", "rus", "russia", "россия", "российская федерация", "russian federation"}
SKIP_FILES = {"app_state"}


def norm_title(s):
    s = (s or "").casefold()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"\s[-–—]\s.*$", " ", s)
    return re.sub(r"[\W_]+", " ", s).strip()


class Library:
    def __init__(self, cat_dir: Path):
        self.dir = cat_dir
        self.lock = threading.Lock()
        self.tracks, self.by_genre, self.artists, self.albums, self.catalogs = {}, {}, {}, {}, []
        self.built_at = 0
        self.rng = random.Random()

    # ------------------------------------------------------------ индекс
    def catalog_files(self):
        return sorted(p for p in self.dir.glob("*.sqlite") if p.stem not in SKIP_FILES)

    @staticmethod
    def _open(path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)

    def rebuild(self, titles=None):
        titles = titles or {}
        tracks, by_genre, artists, albums, catalogs = {}, defaultdict(lambda: defaultdict(list)), {}, {}, []
        for path in self.catalog_files():
            name = path.stem
            try:
                db = self._open(path)
                meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
                names = dict(db.execute("SELECT id, name FROM artists"))
                rows = db.execute(
                    "SELECT t.id, t.title, t.version, t.artists, t.main_artist_id, t.album_id, t.album_genre, "
                    "t.duration_ms, al.title, al.year, al.type FROM tracks t LEFT JOIN albums al ON al.id=t.album_id "
                    "WHERE t.scope=1").fetchall()
                stat = db.execute(
                    "SELECT (SELECT COUNT(*) FROM artists WHERE scope=1), "
                    "(SELECT COUNT(*) FROM artists WHERE scope=1 AND disco_status='done'), "
                    "(SELECT COUNT(*) FROM artists WHERE scope=1 AND disco_status='pending'), "
                    "(SELECT COUNT(*) FROM albums WHERE scope=1), (SELECT COUNT(*) FROM runs)").fetchone()
                db.close()
            except sqlite3.Error as e:
                catalogs.append({"name": name, "error": str(e)})
                continue
            n_before = len(tracks)
            for tid, title, ver, arts, aid, alid, genre, dur, altitle, year, altype in rows:
                if tid in tracks:          # трек уже есть в другом каталоге
                    continue
                key = f"{name}:{genre}"
                rec = {"id": tid, "title": title, "version": ver, "artists": arts, "artist_id": aid,
                       "album_id": alid, "album": altitle, "year": year, "album_type": altype,
                       "genre": genre, "duration_ms": dur, "catalog": name, "title_norm": norm_title(title),
                       "_s": f"{title} {ver or ''} {arts} {altitle or ''}".casefold()}
                tracks[tid] = rec
                by_genre[key][aid].append(tid)
                if aid is not None:
                    a = artists.setdefault(aid, {"id": aid, "name": names.get(aid) or (arts or "").split(", ")[0],
                                                 "tracks": 0, "catalogs": set()})
                    a["tracks"] += 1
                    a["catalogs"].add(name)
                if alid is not None:
                    al = albums.setdefault(alid, {"id": alid, "title": altitle, "year": year, "type": altype,
                                                  "genre": genre, "artist_id": aid, "artists": arts, "tracks": []})
                    al["tracks"].append(tid)
            scope = json.loads(meta.get("scope", "[]"))
            catalogs.append({
                "name": name, "path": str(path), "size_mb": round(path.stat().st_size / 1e6, 1),
                "roots": json.loads(meta.get("roots", "[]")), "scope": scope,
                "tracks": len(tracks) - n_before, "artists": stat[0], "discographies": stat[1],
                "queue": stat[2], "albums": stat[3], "runs": stat[4],
                "genres": sorted(
                    [{"key": f"{name}:{g}", "id": g, "title": titles.get(g, g),
                      "artists": len(by_genre.get(f"{name}:{g}", {})),
                      "tracks": sum(len(v) for v in by_genre.get(f"{name}:{g}", {}).values())}
                     for g in scope if by_genre.get(f"{name}:{g}")], key=lambda x: -x["tracks"])})
        for a in artists.values():
            a["catalogs"] = sorted(a["catalogs"])
            a["_s"] = (a["name"] or "").casefold()
        with self.lock:
            self.tracks, self.by_genre, self.artists, self.albums, self.catalogs = \
                tracks, {k: dict(v) for k, v in by_genre.items()}, artists, albums, catalogs
            self.built_at = time.time()
        return self.catalogs

    # ------------------------------------------------------------ данные для UI
    def public(self, tid):
        t = self.tracks.get(str(tid))
        return {k: v for k, v in t.items() if not k.startswith("_")} if t else None

    def search(self, q, limit=60):
        q = (q or "").casefold().strip()
        if not q:
            return {"artists": [], "albums": [], "tracks": []}
        with self.lock:
            tracks, artists, albums = self.tracks, self.artists, self.albums

        def rank(text):
            return 0 if text.startswith(q) else 1

        arts = sorted((a for a in artists.values() if q in a["_s"]), key=lambda a: (rank(a["_s"]), -a["tracks"]))
        trs = sorted((t for t in tracks.values() if q in t["_s"]), key=lambda t: (rank(t["_s"]), t["title"] or ""))
        als = sorted((al for al in albums.values() if q in (al["title"] or "").casefold()
                      or q in (al["artists"] or "").casefold()), key=lambda al: (rank((al["title"] or "").casefold()),
                                                                                -(al["year"] or 0)))
        return {"artists": [{k: v for k, v in a.items() if k != "_s"} for a in arts[:limit]],
                "albums": [self._album_brief(al) for al in als[:limit]],
                "tracks": [self.public(t["id"]) for t in trs[:limit * 2]]}

    def _album_brief(self, al):
        out = {k: (len(v) if k == "tracks" else v) for k, v in al.items()}
        out["cover_track"] = al["tracks"][0] if al["tracks"] else None
        return out

    def artist(self, aid):
        a = self.artists.get(int(aid))
        if not a:
            return None
        tids = [t for t in self.tracks.values() if t["artist_id"] == int(aid)]
        als = {}
        for t in tids:
            if t["album_id"] in self.albums:
                als[t["album_id"]] = self._album_brief(self.albums[t["album_id"]])
        return {"artist": {k: v for k, v in a.items() if k != "_s"},
                "albums": sorted(als.values(), key=lambda x: -(x["year"] or 0)),
                "tracks": [self.public(t["id"]) for t in sorted(tids, key=lambda t: (-(t["year"] or 0), t["title"] or ""))]}

    def album(self, alid):
        al = self.albums.get(int(alid))
        if not al:
            return None
        return {"album": self._album_brief(al), "tracks": [self.public(t) for t in al["tracks"]]}

    def random_artists(self, n=24):
        with self.lock:
            ids = list(self.artists)
        return [{k: v for k, v in self.artists[i].items() if k != "_s"} for i in self.rng.sample(ids, min(n, len(ids)))]

    # ------------------------------------------------------------ справедливый выбор
    def eligible_artists(self, keys, ru_ids=None):
        with self.lock:
            by_genre = self.by_genre
        keys = [k for k in keys if k in by_genre] or list(by_genre)
        pool = defaultdict(list)
        for k in keys:
            for aid, tids in by_genre[k].items():
                if ru_ids and aid in ru_ids:
                    continue
                pool[aid].extend(tids)
        return pool

    def pick(self, keys, state, settings, ru_ids=None, exclude_tracks=(), exclude_artists=()):
        pool = self.eligible_artists(keys, ru_ids)
        if not pool:
            return None
        cooldown = min(int(settings.get("artist_cooldown", 150)), max(0, len(pool) // 3))
        recent_a = set(state.recent_artists(cooldown)) | set(exclude_artists)
        recent_t = state.recent_tracks(int(settings.get("track_cooldown_days", 30))) | set(map(str, exclude_tracks))
        recent_titles = state.recent_titles(7)
        bad = state.unavailable()
        candidates = [a for a in pool if a not in recent_a] or list(pool)
        for _ in range(40):
            aid = self.rng.choice(candidates)
            tids = [t for t in pool[aid] if t not in bad and t not in recent_t
                    and (aid, self.tracks[t]["title_norm"]) not in recent_titles]
            if tids:
                return self.public(self.rng.choice(tids))
        # все кандидаты недавно звучали — берём любой доступный трек, давно не звучавший
        aid = self.rng.choice(candidates)
        tids = [t for t in pool[aid] if t not in bad] or pool[aid]
        return self.public(self.rng.choice(tids))

    def ru_ids(self, countries):
        return {aid for aid, cs in countries.items() if any((c or "").strip().casefold() in RU_VALUES for c in cs)}
