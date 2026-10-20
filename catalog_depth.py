"""
Эксперимент 2: глубина каталога Яндекс Музыки для жанра.

  python catalog_depth.py                 # жанр allrock, стандартный бюджет (~15 мин)
  python catalog_depth.py --genre allrock --scale 0.3   # быстрый прогон
  python catalog_depth.py --genre jazz    # любой id из client.genres()

Проверяемые способы обхода (у каждого свой лимит запросов):
  1. metatag_albums   — жанровая страница, все поджанры, сортировки popular/new, пагинация
  2. metatag_artists  — исполнители жанра, пагинация
  3. discography      — полный список треков исполнителя (artists_tracks, все страницы)
                        + дискография по альбомам (artists_direct_albums)
  4. similar_crawl    — обход графа «похожие исполнители» с фильтром по жанру
  5. labels           — каталоги лейблов, найденных в жанровых альбомах
  6. radio_session    — радио-сессия genre:<жанр> (incognito, без влияния на рекомендации)
  7. playlists        — плейлисты жанра и их треки
  8. album_sample     — треки альбомов с глубоких страниц жанра (проверка track_count)

Токен берётся тем же способом, что и в rock_probe.py (ym_auth.py). Не выводится.
Результаты: results_depth/<genre>/
"""
import argparse
import csv
import json
import logging
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    import yandex_music
    from ym_auth import connect, RedactFilter, redact
except ImportError as e:
    sys.exit(f"Ошибка импорта ({e}). Выполните: pip install -U yandex-music")

ap = argparse.ArgumentParser()
ap.add_argument("--genre", default="allrock")
ap.add_argument("--scale", type=float, default=1.0, help="множитель лимитов запросов")
ap.add_argument("--delay", type=float, default=0.15, help="пауза между запросами, с")
ap.add_argument("--seed", type=int, default=42)
A = ap.parse_args()
random.seed(A.seed)

BUDGET = {k: max(3, int(v * A.scale)) for k, v in {
    "metatag_albums": 160, "metatag_artists": 40, "discography": 220, "similar_crawl": 120,
    "labels": 60, "radio_session": 60, "playlists": 40, "album_sample": 25}.items()}

OUT = Path(__file__).resolve().parent / "results_depth" / A.genre
OUT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.FileHandler(OUT / "run_log.txt", "w", "utf-8"),
                              logging.StreamHandler(sys.stdout)])
for h in logging.getLogger().handlers:
    h.addFilter(RedactFilter())
log = logging.getLogger("depth")
log.setLevel(logging.INFO)

# ------------------------------------------------------------------ хранилище
TRACKS, ALBUMS, ARTISTS = {}, {}, {}
STATS = defaultdict(lambda: {"requests": 0, "errors": Counter(), "seconds": 0.0,
                             "new_tracks": 0, "new_albums": 0, "new_artists": 0})
CURVE = []           # (method, req_in_method, global_unique_tracks)
FAMILY = set()
SKIPPED_ALIEN = Counter()   # треки чужих жанров, встреченные методом
STOP = False


def call(method, fn, *a, **kw):
    """Один запрос к API с учётом бюджета, ошибок и паузы."""
    global STOP
    st = STATS[method]
    if STOP or st["requests"] >= BUDGET.get(method, 10 ** 9):
        return None
    st["requests"] += 1
    t0 = time.time()
    try:
        return fn(*a, **kw)
    except Exception as e:
        msg = redact(f"{type(e).__name__}: {str(e)[:160]}")
        st["errors"][msg] += 1
        if "Unauthorized" in type(e).__name__:
            STOP = True
            log.error("Токен недействителен — остановка. Запустите: python rock_probe.py --logout")
        return None
    finally:
        st["seconds"] += time.time() - t0
        time.sleep(A.delay)


def budget_left(method):
    return not STOP and STATS[method]["requests"] < BUDGET.get(method, 0)


def snap(method):
    CURVE.append((method, STATS[method]["requests"], len(TRACKS)))


def names(objs):
    return ", ".join(o.name for o in (objs or []) if getattr(o, "name", None))


def add_artist(a, source):
    if a is None or getattr(a, "id", None) is None:
        return False
    aid = int(a.id)
    rec = ARTISTS.get(aid)
    if rec is None:
        ARTISTS[aid] = {"id": aid, "name": a.name, "genres": list(a.genres or []),
                        "likes_count": getattr(a, "likes_count", None), "sources": {source}}
        STATS[source]["new_artists"] += 1
        return True
    if a.genres and not rec["genres"]:
        rec["genres"] = list(a.genres)
    rec["sources"].add(source)
    return False


def add_album(al, source):
    if al is None or getattr(al, "id", None) is None:
        return False
    rec = ALBUMS.get(al.id)
    if rec is None:
        labels = []
        for lb in (al.labels or []):
            labels.append({"id": getattr(lb, "id", None), "name": getattr(lb, "name", lb)})
        ALBUMS[al.id] = {"id": al.id, "title": al.title, "artists": names(al.artists),
                         "artist_ids": [a.id for a in (al.artists or [])],
                         "genre": al.genre or "", "year": al.year, "type": al.type or "",
                         "track_count": al.track_count or 0, "likes_count": al.likes_count,
                         "bests": list(al.bests or []), "labels": labels, "sources": {source}}
        STATS[source]["new_albums"] += 1
        for a in al.artists or []:
            add_artist(a, source)
        return True
    ALBUMS[al.id]["sources"].add(source)
    return False


def add_track(t, source, artist_rank=None):
    if t is None or getattr(t, "id", None) is None:
        return False
    tid = str(t.id)
    alb = (t.albums or [None])[0]
    genre = (alb.genre if alb else "") or ""
    rec = TRACKS.get(tid)
    if rec is None:
        TRACKS[tid] = {"track_id": tid, "title": t.title, "artists": names(t.artists),
                       "artist_ids": [int(a.id) for a in (t.artists or []) if a.id is not None],
                       "album_id": alb.id if alb else None, "album_title": alb.title if alb else "",
                       "year": alb.year if alb else None, "album_genre": genre,
                       "in_family": genre in FAMILY, "duration_s": (t.duration_ms or 0) // 1000,
                       "best_flag": bool(getattr(t, "best", False)),
                       "artist_rank": artist_rank, "sources": {source}}
        STATS[source]["new_tracks"] += 1
        if genre not in FAMILY:
            SKIPPED_ALIEN[source] += 1
        for a in t.artists or []:
            add_artist(a, source)
        return True
    rec["sources"].add(source)
    if artist_rank is not None and (rec["artist_rank"] is None or artist_rank < rec["artist_rank"]):
        rec["artist_rank"] = artist_rank
    return False


# ------------------------------------------------------------------ жанры
def genre_tree(client):
    genres = client.genres()
    flat = {}

    def walk(g, parent):
        flat[g.id] = {"title": g.title, "parent": parent}
        for s in g.sub_genres or []:
            walk(s, g.id)

    for g in genres:
        walk(g, None)
    if A.genre not in flat:
        sys.exit(f"Жанр '{A.genre}' не найден. Доступные: {', '.join(sorted(flat))}")

    def fam(gid):
        out = {gid}
        for k, v in flat.items():
            if v["parent"] == gid:
                out |= fam(k)
        return out

    FAMILY.update(fam(A.genre))
    log.info("Жанр %s: семейство %d id: %s", A.genre, len(FAMILY), ", ".join(sorted(FAMILY)))
    return {gid: flat[gid]["title"] for gid in sorted(FAMILY)}


# ------------------------------------------------------------------ 1. metatag albums
def m_metatag_albums(client):
    m, info = "metatag_albums", {}
    per = max(2, BUDGET[m] // (2 * len(FAMILY)))
    for gid in sorted(FAMILY):
        for sort in ("popular", "new"):
            limit, got, total, pages = 100, 0, None, 0
            for p in range(per):
                res = call(m, client.metatag_albums, gid, sort_by=sort, offset=p * limit, limit=limit)
                if res is None and p == 0 and limit == 100:      # вдруг limit=100 не принят
                    limit = 25
                    res = call(m, client.metatag_albums, gid, sort_by=sort, offset=0, limit=limit)
                items = getattr(res, "albums", None) or []
                pager = getattr(res, "pager", None)
                total = getattr(pager, "total", None) or total
                new = sum(add_album(a, m) for a in items)
                got += len(items); pages += 1
                snap(m)
                if len(items) < limit or new == 0:
                    break
            info[f"{gid}/{sort}"] = {"albums_returned": got, "pages": pages, "pager_total": total,
                                     "limit_used": limit, "exhausted": got < pages * limit}
    return info


# ------------------------------------------------------------------ 2. metatag artists
def m_metatag_artists(client):
    m, info, order = "metatag_artists", {}, []
    per = max(2, BUDGET[m] // len(FAMILY))
    for gid in sorted(FAMILY):
        got, total = 0, None
        for p in range(per):
            res = call(m, client.metatag_artists, gid, period="month", offset=p * 50, limit=50)
            items = getattr(res, "artists", None) or []
            total = getattr(getattr(res, "pager", None), "total", None) or total
            for it in items:
                art = getattr(it, "artist", None) or it
                add_artist(art, m)
                if getattr(art, "id", None) is not None:
                    order.append(int(art.id))
                for t in getattr(it, "tracks", None) or []:
                    add_track(t, m)
            got += len(items)
            snap(m)
            if len(items) < 50:
                break
        info[gid] = {"artists_returned": got, "pager_total": total,
                     "item_type": type(items[0]).__name__ if items else None}
    return info, list(dict.fromkeys(order))


# ------------------------------------------------------------------ 3. discography
def m_discography(client, artist_order):
    """Полный список треков исполнителя + сверка с дискографией по альбомам."""
    m = "discography"
    fam_artists = [a for a in ARTISTS.values() if set(a["genres"]) & FAMILY]
    pool = list(dict.fromkeys(artist_order + [a["id"] for a in fam_artists]))
    head, tail = pool[:15], pool[15:]
    random.shuffle(tail)
    sample = head + tail                # 15 самых популярных + случайные из глубины
    per_artist = []
    for aid in sample:
        if not budget_left(m):
            break
        listed, total, page, deep = 0, None, 0, 0
        while budget_left(m):
            res = call(m, client.artists_tracks, aid, page=page, page_size=100)
            tracks = getattr(res, "tracks", None) or []
            total = getattr(getattr(res, "pager", None), "total", None) or total
            for i, t in enumerate(tracks):
                rank = page * 100 + i + 1
                add_track(t, m, artist_rank=rank)
                deep += rank > 20
            listed += len(tracks); page += 1
            snap(m)
            if len(tracks) < 100:
                break
        res = call(m, client.artists_direct_albums, aid, page=0, page_size=100)
        albums = getattr(res, "albums", None) or []
        for al in albums:
            add_album(al, m)
        per_artist.append({"artist_id": aid, "name": ARTISTS.get(aid, {}).get("name"),
                           "is_top15": aid in head, "tracks_listed": listed, "pager_total": total,
                           "tracks_beyond_top20": deep, "direct_albums": len(albums),
                           "direct_albums_track_sum": sum(a.track_count or 0 for a in albums)})
    return per_artist


# ------------------------------------------------------------------ 4. similar crawl
def m_similar_crawl(client, artist_order):
    m = "similar_crawl"
    seeds = artist_order[:5] + random.sample(artist_order[5:], min(5, max(0, len(artist_order) - 5)))
    frontier, seen, levels = list(seeds), set(seeds), Counter()
    depth = {a: 0 for a in seeds}
    in_fam = out_fam = no_genre = 0
    while frontier and budget_left(m):
        aid = frontier.pop(0)
        res = call(m, client.artists_similar, aid)
        sims = getattr(res, "similar_artists", None) or []
        for s in sims:
            if s.id is None or int(s.id) in seen:
                continue
            sid = int(s.id)
            seen.add(sid)
            g = set(s.genres or [])
            if not g:
                no_genre += 1
            elif g & FAMILY:
                in_fam += 1
                depth[sid] = depth[aid] + 1
                levels[depth[sid]] += 1
                add_artist(s, m)
                frontier.append(sid)
            else:
                out_fam += 1
        snap(m)
    return {"seeds": len(seeds), "artists_discovered_in_genre": in_fam,
            "discovered_other_genres": out_fam, "discovered_without_genre": no_genre,
            "frontier_left": len(frontier), "by_depth": dict(sorted(levels.items())),
            "new_vs_known_before": sum(1 for a in ARTISTS.values()
                                       if a["sources"] == {m})}


# ------------------------------------------------------------------ 5. labels
def m_labels(client):
    m = "labels"
    cnt, lname = Counter(), {}
    for al in ALBUMS.values():
        if al["genre"] in FAMILY:
            for lb in al["labels"]:
                if lb["id"] is not None:
                    cnt[lb["id"]] += 1; lname[lb["id"]] = lb["name"]
    top = [l for l, _ in cnt.most_common(6)]
    rest = [l for l in cnt if l not in top]
    picks = top + random.sample(rest, min(6, len(rest)))
    out = []
    for lid in picks:
        total, fam_albums, fam_tracks, pages = 0, 0, 0, 0
        for p in range(4):
            res = call(m, client.label_albums, lid, page=p, page_size=100)
            albums = getattr(res, "albums", None) or []
            for al in albums:
                if (al.genre or "") in FAMILY:
                    fam_albums += 1; fam_tracks += al.track_count or 0
                    add_album(al, m)
            total += len(albums); pages += 1
            snap(m)
            if len(albums) < 100:
                break
        out.append({"label_id": lid, "name": lname[lid], "albums_seen": total, "pages": pages,
                    "albums_in_genre": fam_albums, "tracks_in_genre_est": fam_tracks})
    return {"labels_found_in_genre_albums": len(cnt), "checked": out}


# ------------------------------------------------------------------ 6. radio session
def m_radio_session(client):
    m = "radio_session"
    station = f"genre:{A.genre}"
    s = call(m, client.rotor_session_new, seeds=[station], include_tracks_in_response=True,
             incognito=True)
    if s is None or not getattr(s, "radio_session_id", None):
        # запасной вариант: подстанция rock
        station = "genre:rock"
        s = call(m, client.rotor_session_new, seeds=[station], include_tracks_in_response=True,
                 incognito=True)
    if s is None or not getattr(s, "radio_session_id", None):
        return {"ok": False, "station": station}
    sid, seen_ids, per_batch, dry = s.radio_session_id, [], [], 0

    def take(seq):
        new = 0
        for it in seq or []:
            t = getattr(it, "track", None)
            if t is not None:
                seen_ids.append(f"{t.id}:{t.albums[0].id}" if t.albums else str(t.id))
                new += add_track(t, m)
        return new, len(seq or [])

    n, tot = take(s.sequence); per_batch.append([n, tot]); snap(m)
    while budget_left(m) and dry < 6:
        r = call(m, client.rotor_session_tracks, sid, queue=seen_ids[-50:])
        n, tot = take(getattr(r, "sequence", None))
        per_batch.append([n, tot]); snap(m)
        dry = dry + 1 if n == 0 else 0
    uniq = [t for t in TRACKS.values() if m in t["sources"]]
    return {"ok": True, "station": station, "batches": len(per_batch),
            "tracks_received": sum(b[1] for b in per_batch),
            "unique_tracks": len(uniq), "new_per_batch": [b[0] for b in per_batch],
            "unique_artists": len({a for t in uniq for a in t["artist_ids"]})}


# ------------------------------------------------------------------ 7. playlists
def m_playlists(client):
    m = "playlists"
    pls = []
    for sort in ("popular", "new"):
        for p in range(3):
            res = call(m, client.metatag_playlists, A.genre, sort_by=sort, offset=p * 50, limit=50)
            if not getattr(res, "playlists", None) and p == 0 and A.genre == "allrock":
                res = call(m, client.metatag_playlists, "rock", sort_by=sort, offset=0, limit=50)
            items = getattr(res, "playlists", None) or []
            pls += items
            if len(items) < 50:
                break
    pls = list({(getattr(getattr(p, "owner", None), "uid", None), p.kind): p
                for p in pls if getattr(p, "kind", None) is not None}.values())
    random.shuffle(pls)
    checked = []
    for pl in pls:
        if not budget_left(m):
            break
        shorts = call(m, pl.fetch_tracks) or []
        direct = [ts.track for ts in shorts if getattr(ts, "track", None)]
        if not direct:
            ids = [ts.track_id for ts in shorts][:200]
            direct = call(m, client.tracks, ids) or [] if ids else []
        new = sum(add_track(t, m) for t in direct)
        checked.append({"title": pl.title, "tracks": len(shorts), "new_unique": new})
        snap(m)
    return {"playlists_found": len(pls), "checked": checked}


# ------------------------------------------------------------------ 8. album sample
def m_album_sample(client):
    m = "album_sample"
    fam = [a for a in ALBUMS.values() if a["genre"] in FAMILY and a["track_count"]]
    random.shuffle(fam)
    ok = mismatch = 0
    for al in fam:
        if not budget_left(m):
            break
        full = call(m, client.albums_with_tracks, al["id"])
        tracks = [t for vol in (getattr(full, "volumes", None) or []) for t in vol]
        for t in tracks:
            add_track(t, m)
        ok += len(tracks) == al["track_count"]; mismatch += len(tracks) != al["track_count"]
        snap(m)
    return {"albums_fetched": ok + mismatch, "track_count_matches": ok, "mismatches": mismatch}


# ------------------------------------------------------------------ итоги
def diversity(rows):
    if not rows:
        return {}
    c = Counter(a for r in rows for a in r["artist_ids"][:1])
    if not c:
        return {"tracks": len(rows)}
    top10 = sum(n for _, n in c.most_common(10))
    return {"tracks": len(rows), "artists": len(c), "tracks_per_artist_avg": round(len(rows) / len(c), 2),
            "max_tracks_one_artist": c.most_common(1)[0][1], "top10_artists_share_%": round(100 * top10 / len(rows), 1),
            "in_genre_%": round(100 * sum(r["in_family"] for r in rows) / len(rows), 1)}


def main():
    t_start = time.time()
    client, acc = connect()
    log.info("Подключено (uid %s, Плюс: %s). Бюджеты запросов: %s", acc["uid"], acc["has_plus"], BUDGET)
    rep = {"started": datetime.now().isoformat(timespec="seconds"), "genre": A.genre,
           "library": getattr(yandex_music, "__version__", "?"), "budget": BUDGET,
           "account": {"has_plus": acc["has_plus"]}, "methods": {}}
    rep["family"] = genre_tree(client)

    steps = [("metatag_albums", lambda: m_metatag_albums(client))]
    order = []
    for name, fn in steps:
        rep["methods"][name] = run(name, fn)
    res = run("metatag_artists", lambda: m_metatag_artists(client))
    if isinstance(res, tuple):
        res, order = res
    rep["methods"]["metatag_artists"] = res
    if not order:   # запасной список исполнителей — из альбомов
        order = [aid for al in ALBUMS.values() if al["genre"] in FAMILY for aid in al["artist_ids"]]
        order = list(dict.fromkeys(int(x) for x in order if x is not None))
    for name, fn in [("discography", lambda: m_discography(client, order)),
                     ("similar_crawl", lambda: m_similar_crawl(client, order)),
                     ("labels", lambda: m_labels(client)),
                     ("radio_session", lambda: m_radio_session(client)),
                     ("playlists", lambda: m_playlists(client)),
                     ("album_sample", lambda: m_album_sample(client))]:
        rep["methods"][name] = run(name, fn)

    # --- сводка
    fam_albums = [a for a in ALBUMS.values() if a["genre"] in FAMILY]
    rows = list(TRACKS.values())
    disc = [r for r in rows if r["artist_rank"] is not None]
    rep["stats"] = {k: {**v, "errors": dict(v["errors"]), "seconds": round(v["seconds"], 1),
                        "alien_genre_tracks": SKIPPED_ALIEN[k]} for k, v in STATS.items()}
    rep["totals"] = {
        "requests": sum(v["requests"] for v in STATS.values()),
        "minutes": round((time.time() - t_start) / 60, 1),
        "unique_tracks": len(rows),
        "unique_tracks_in_genre": sum(r["in_family"] for r in rows),
        "unique_albums_in_genre": len(fam_albums),
        "tracks_in_genre_albums_est": sum(a["track_count"] for a in fam_albums),
        "unique_artists_seen": len(ARTISTS),
        "artists_with_genre_tag": sum(1 for a in ARTISTS.values() if set(a["genres"]) & FAMILY),
        "album_genre_distribution": dict(Counter(a["genre"] for a in fam_albums).most_common()),
        "album_types": dict(Counter(a["type"] or "album" for a in fam_albums).most_common()),
        "album_years": dict(sorted(Counter((a["year"] or 0) // 10 * 10 for a in fam_albums).items())),
    }
    rep["diversity_by_source"] = {s: diversity([r for r in rows if s in r["sources"]])
                                  for s in STATS}
    rep["diversity_all_in_genre"] = diversity([r for r in rows if r["in_family"]])
    # popularity: второстепенно, только как сведения о доступных сигналах
    rep["popularity_signals"] = {
        "discography_tracks": len(disc),
        "rank_beyond_20_%": round(100 * sum(r["artist_rank"] > 20 for r in disc) / len(disc), 1) if disc else None,
        "track_best_flag_true": sum(r["best_flag"] for r in rows),
        "albums_with_bests": sum(1 for a in fam_albums if a["bests"]),
        "albums_with_likes_count": sum(1 for a in fam_albums if a["likes_count"] is not None),
        "artists_with_likes_count": sum(1 for a in ARTISTS.values() if a["likes_count"] is not None),
    }
    src_sets = {s: {r["track_id"] for r in rows if s in r["sources"]} for s in STATS}
    rep["overlap_tracks"] = {f"{a}&{b}": len(src_sets[a] & src_sets[b])
                             for a in src_sets for b in src_sets if a < b and src_sets[a] & src_sets[b]}
    rep["finished"] = datetime.now().isoformat(timespec="seconds")
    save(rep)


def run(name, fn):
    t0 = time.time()
    log.info("→ %s (лимит %d запросов)", name, BUDGET.get(name, 0))
    try:
        r = fn()
    except Exception as e:
        log.warning("%s упал: %s", name, redact(f"{type(e).__name__}: {e}"))
        r = {"crashed": redact(f"{type(e).__name__}: {e}")}
    st = STATS[name]
    log.info("  %s: запросов %d, новых треков %d, альбомов %d, исполнителей %d, ошибок %d, %.0f c",
             name, st["requests"], st["new_tracks"], st["new_albums"], st["new_artists"],
             sum(st["errors"].values()), time.time() - t0)
    return r


def save(rep):
    def ser(o):
        return sorted(o) if isinstance(o, set) else str(o)
    (OUT / "summary.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=ser), "utf-8")
    with open(OUT / "tracks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["track_id", "title", "artists", "album_id", "album_title", "year", "album_genre",
                    "in_genre", "artist_rank", "duration_s", "sources"])
        for r in TRACKS.values():
            w.writerow([r["track_id"], r["title"], r["artists"], r["album_id"], r["album_title"], r["year"],
                        r["album_genre"], int(r["in_family"]), r["artist_rank"] or "", r["duration_s"],
                        "|".join(sorted(r["sources"]))])
    with open(OUT / "albums.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["album_id", "title", "artists", "genre", "year", "type", "track_count",
                    "likes_count", "labels", "sources"])
        for a in ALBUMS.values():
            w.writerow([a["id"], a["title"], a["artists"], a["genre"], a["year"], a["type"], a["track_count"],
                        a["likes_count"] if a["likes_count"] is not None else "",
                        "|".join(str(l["name"]) for l in a["labels"]), "|".join(sorted(a["sources"]))])
    with open(OUT / "curve.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["method", "requests_in_method", "global_unique_tracks"])
        w.writerows(CURVE)
    t = rep["totals"]
    print("\n=== Итог ===")
    print(f"Запросов: {t['requests']}, время: {t['minutes']} мин")
    print(f"Уникальных треков получено: {t['unique_tracks']} (жанра: {t['unique_tracks_in_genre']})")
    print(f"Альбомов жанра найдено: {t['unique_albums_in_genre']}, в них треков (оценка): {t['tracks_in_genre_albums_est']}")
    print(f"Исполнителей: {t['unique_artists_seen']}")
    for k, v in rep["stats"].items():
        print(f"  {k:16} запр {v['requests']:4}  треков +{v['new_tracks']:5}  альбомов +{v['new_albums']:5}"
              f"  исп +{v['new_artists']:4}  ошибок {sum(v['errors'].values())}")
    print(f"Файлы: {OUT}")


if __name__ == "__main__":
    main()
