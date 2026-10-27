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

import scripts
from pathlib import Path

SKIP_FILES = {"app_state"}


def country_blocked(artist_ids, countries, settings):
    """Множество исполнителей, которых фильтр стран не пропускает.

    countries — только достоверно определённые страны {artist_id: множество ISO-кодов}. Если у исполнителя
    несколько стран, он считается выбранным, если хотя бы одна из них в списке (в режиме «исключить» —
    исключается). Неизвестная страна никогда не считается совпадением со списком: такие исполнители
    проходят или нет по настройке country_unknown.
    """
    mode = settings.get("country_mode", "all")
    if mode not in ("include", "exclude"):
        return set()
    chosen = {str(c).upper() for c in settings.get("country_list") or []}
    allow_unknown = bool(settings.get("country_unknown", True))
    out = set()
    for aid in artist_ids:
        cs = countries.get(aid)
        if not cs:
            if not allow_unknown:
                out.add(aid)
        elif (mode == "include") != bool(cs & chosen):
            out.add(aid)
    return out


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
        self._pool_cache = {}

    # ------------------------------------------------------------ индекс
    def catalog_files(self):
        return sorted(p for p in self.dir.glob("*.sqlite") if p.stem not in SKIP_FILES)

    @staticmethod
    def _open(path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)

    def rebuild(self, titles=None, parents=None, order=None):
        """titles: {жанр: название}; parents: {жанр: родительский жанр или None}; order: порядок корней."""
        titles, parents, order = titles or {}, parents or {}, order or []
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
                       "_sm": scripts.mask(f"{title or ''} {ver or ''}"),
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
            genres = []
            for g in scope:
                bucket = by_genre.get(f"{name}:{g}")
                if not bucket:
                    continue
                parent = parents.get(g) or g                 # корневой жанр: сам себе группа
                title = titles.get(g, g)
                genres.append({"key": f"{name}:{g}", "id": g, "title": title, "parent": parent,
                               "general": parent == g,       # альбомы с жанром-корнем без уточнения
                               "artists": len(bucket), "tracks": sum(len(v) for v in bucket.values())})
            genres.sort(key=lambda x: (not x["general"], -x["tracks"]))
            groups = []
            for parent in sorted({g["parent"] for g in genres},
                                 key=lambda p: (order.index(p) if p in order else len(order), p)):
                gs = [g for g in genres if g["parent"] == parent]
                uniq = set().union(*(by_genre[g["key"]].keys() for g in gs))
                groups.append({"id": parent, "title": titles.get(parent, parent), "keys": [g["key"] for g in gs],
                               "artists": len(uniq), "tracks": sum(g["tracks"] for g in gs)})
            cat_artists = set().union(*(by_genre[g["key"]].keys() for g in genres)) if genres else set()
            catalogs.append({
                "name": name, "path": str(path), "size_mb": round(path.stat().st_size / 1e6, 1),
                "roots": json.loads(meta.get("roots", "[]")), "scope": scope,
                "tracks": len(tracks) - n_before, "artists": stat[0], "candidates": stat[0],
                "playable_artists": len(cat_artists), "discographies": stat[1],
                "queue": stat[2], "albums": stat[3], "runs": stat[4],
                "genres": sorted(genres, key=lambda x: -x["tracks"]), "groups": groups})
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
        if not t:
            return None
        out = {k: v for k, v in t.items() if not k.startswith("_")}
        out["scripts"] = scripts.names(t["_sm"])
        return out

    def script_counts(self):
        """Сколько треков каталога содержат буквы каждой письменности."""
        with self.lock:
            tracks = self.tracks
        counts = defaultdict(int)
        for t in tracks.values():
            m = t["_sm"]
            if m:
                for sid in scripts.names(m):
                    counts[sid] += 1
        return {"total": len(tracks), "counts": dict(counts)}

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
    def pool_summary(self, keys, blocked=None, script_mask=0):
        """Сколько уникальных исполнителей и треков реально доступно для выбранных жанров с учётом фильтров."""
        full = self.eligible_artists(keys)
        ok = self.eligible_artists(keys, blocked, script_mask)
        by_script = self.eligible_artists(keys, None, script_mask) if script_mask else full
        n = lambda pool: sum(len(v) for v in pool.values())
        return {"artists": len(full), "tracks": n(full),
                "allowed_artists": len(ok), "allowed_tracks": n(ok),
                "blocked_artists": len([a for a in full if blocked and a in blocked]),
                "script_excluded_tracks": n(full) - n(by_script),
                "script_excluded_artists": len(full) - len(by_script)}

    def eligible_artists(self, keys, blocked=None, script_mask=0):
        """{исполнитель: [треки]} для выбранных жанров. Исполнители из исключённых стран и треки с буквами
        исключённых письменностей отбрасываются; исполнитель без подходящих треков в пул не попадает."""
        with self.lock:
            by_genre, tracks = self.by_genre, self.tracks
        keys = [k for k in keys if k in by_genre] or list(by_genre)
        ck = (tuple(sorted(keys)), hash(frozenset(blocked)) if blocked else 0, script_mask, id(by_genre))
        hit = self._pool_cache.get(ck)
        if hit is not None:
            return hit
        pool = defaultdict(list)
        for k in keys:
            for aid, tids in by_genre[k].items():
                if blocked and aid in blocked:
                    continue
                if script_mask:
                    tids = [t for t in tids if not tracks[t]["_sm"] & script_mask]
                    if not tids:
                        continue
                pool[aid].extend(tids)
        pool = dict(pool)
        if len(self._pool_cache) > 8:
            self._pool_cache.clear()
        self._pool_cache[ck] = pool                   # только для чтения: pick() пул не изменяет
        return pool

    def pick(self, keys, state, settings, blocked=None, exclude_tracks=(), exclude_artists=(), script_mask=0):
        pool = self.eligible_artists(keys, blocked, script_mask)
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
