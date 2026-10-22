"""Исследование жанров Яндекс Музыки (только чтение, ~100–200 запросов, 1–3 минуты).

  python genre_probe.py

1. Полный ответ /genres: глубина дерева, скрытые жанры, служебные поля.
2. /landing3/metatags: какие метатеги (жанры, настроения, эпохи…) показывает Яндекс и есть ли там
   более дробные рок-направления, чем в /genres.
3. Для рок-родственных жанров и метатегов: заголовок, дочерние элементы, число альбомов и исполнителей
   на странице метатега (pager.total).
Пишет genre_probe.json рядом со скриптом. Каталог и настройки не изменяются, токен не выводится.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ROCKISH = ("rock", "рок", "punk", "панк", "indie", "инди", "altern", "альтерн", "metal", "метал", "grunge", "гранж",
           "garage", "гараж", "psych", "психодел", "shoegaz", "шугейз", "emo", "эмо", "hardcore", "хардкор", "post",
           "пост", "surf", "серф", "britpop", "бритпоп", "goth", "гот", "noise", "нойз", "wave", "вейв", "ska", "ска",
           "blues", "блюз", "folk", "фолк", "stoner", "стоунер", "prog", "прог", "math", "dream", "sludge", "doom",
           "industrial", "индастриал", "lo-fi", "lofi", "art", "арт", "krautrock", "glam", "глэм", "rockabilly")


def main():
    import ym_auth
    client, _ = ym_auth.connect(interactive=False)
    base = client.base_url
    out, n = {}, [0]

    def get(path, params=None):
        n[0] += 1
        time.sleep(0.35)
        try:
            return client._request.get(f"{base}{path}", params or {})
        except Exception as e:
            return {"_error": f"{type(e).__name__}: {str(e)[:120]}"}

    # 1. /genres целиком
    genres = get("/genres")
    out["genres_raw"] = genres
    flat, depth = {}, [0]

    def walk(g, lvl, parent):
        gid = g.get("id")
        flat[gid] = {"title": g.get("title"), "parent": parent, "level": lvl,
                     "keys": sorted(k for k in g if k not in ("subGenres", "sub_genres", "images", "radioIcon", "radio_icon"))}
        depth[0] = max(depth[0], lvl)
        for s in g.get("subGenres") or g.get("sub_genres") or []:
            walk(s, lvl + 1, gid)
    for g in genres if isinstance(genres, list) else []:
        walk(g, 0, None)
    print(f"/genres: {len(flat)} жанров, глубина {depth[0] + 1} уровня; поля: {sorted(set(k for v in flat.values() for k in v['keys']))}")
    out["genres_flat"] = flat

    # 2. landing3/metatags
    mt = get("/landing3/metatags")
    out["metatags_raw"] = mt
    found = {}

    def collect(o, path=""):
        if isinstance(o, dict):
            mid = o.get("id")
            if isinstance(mid, str) and o.get("title"):
                found.setdefault(mid, {"title": o.get("title"), "path": path})
            for k, v in o.items():
                collect(v, f"{path}/{k}")
        elif isinstance(o, list):
            for v in o:
                collect(v, path)
    collect(mt)
    groups = {}
    for mid, v in found.items():
        groups.setdefault(v["path"], []).append(f"{mid} ({v['title']})")
    print(f"\n/landing3/metatags: {len(found)} элементов")
    for p, items in groups.items():
        print(f"  {p}: {len(items)} — {', '.join(items[:60])}{' …' if len(items) > 60 else ''}")
    out["metatags_found"] = found

    # 3. рок-родственные жанры и метатеги: страницы, дети, объём
    rock_family = {k for k, v in flat.items() if v["parent"] in ("allrock", "alternative", "indie", "punk", "metal")
                   or k in ("allrock", "alternative", "indie", "punk", "metal", "ska", "blues", "folkgenre")}
    rockish_mt = {mid for mid, v in found.items()
                  if any(w in (mid + " " + str(v["title"])).casefold() for w in ROCKISH)}
    cand = sorted(rock_family | rockish_mt)
    print(f"\n3) Проверяю {len(cand)} жанров/метатегов…")
    pages = {}
    for mid in cand:
        page = get(f"/metatags/{mid}")
        if isinstance(page, dict) and "_error" in page:
            pages[mid] = {"error": page["_error"]}
            print(f"  {mid:24} ошибка {page['_error'][:60]}")
            continue
        page = page or {}
        tree = page.get("tree") or []
        children = []

        def kids(o):
            for t in o or []:
                children.append(f"{t.get('navigationId') or t.get('navigation_id') or t.get('id')}:{t.get('title')}")
                kids(t.get("children") or [])
        kids(tree)
        al = get(f"/metatags/{mid}/albums", {"offset": 0, "limit": 1})
        ar = get(f"/metatags/{mid}/artists", {"offset": 0, "limit": 1})
        tot = lambda r: ((r or {}).get("pager") or {}).get("total") if isinstance(r, dict) else None
        pages[mid] = {"title": (page.get("title") or {}).get("title") if isinstance(page.get("title"), dict) else page.get("title"),
                      "in_genres": mid in flat, "genre_parent": (flat.get(mid) or {}).get("parent"),
                      "children": children[:80], "albums_total": tot(al), "artists_total": tot(ar),
                      "keys": sorted(page.keys())}
        p = pages[mid]
        print(f"  {mid:24} {str(p['title'])[:26]:26} жанр:{'да' if p['in_genres'] else 'нет':3} "
              f"альбомов {p['albums_total']}, исполнителей {p['artists_total']}, дочерних {len(children)}"
              f"{' — ' + ', '.join(children[:12]) if children else ''}")
    out["pages"] = pages
    out["requests"] = n[0]
    (ROOT / "genre_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(f"\nЗапросов: {n[0]}. Результат: genre_probe.json")


if __name__ == "__main__":
    main()
