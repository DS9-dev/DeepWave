"""Проверка определения стран исполнителей на небольшой выборке (НЕ на всём каталоге).

  python country_probe.py              # ~70 исполнителей, 3–6 минут
  python country_probe.py --random 40  # больше случайных малоизвестных
  python country_probe.py --yandex 200 # полнота поля «страны» в brief-info Яндекса (без MusicBrainz)

Что делает:
  1. Смотрит, есть ли страна в ответах Яндекс Музыки (пакетный /artists и brief-info) — для 8 исполнителей.
  2. Для выборки (известные исполнители разных стран, одноимённые, случайные) определяет страну через
     MusicBrainz: имя + сверка альбомов. Не чаще 1 запроса в секунду.
Пишет country_probe_results.json рядом со скриптом. Токен не выводится и не записывается.
Каталог и история не изменяются (каталог открывается только для чтения).
"""
import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import countries as C          # noqa: E402
import country_names as N      # noqa: E402

KNOWN = ["Nirvana", "The Beatles", "Queen", "Radiohead", "Muse", "Coldplay", "Oasis", "Placebo", "U2",
         "The Cranberries", "Foo Fighters", "Linkin Park", "Tool", "Nickelback", "Three Days Grace", "Rush",
         "Danko Jones", "AC/DC", "INXS", "Midnight Oil", "Scorpions", "Tokio Hotel", "Måneskin", "Europe",
         "Kansas", "Boston", "Asia", "Berlin", "Genesis", "Bush", "Live", "Clutch", "Smash Mouth",
         "Кино", "Король и Шут", "Сплин", "Алиса", "ДДТ", "Аквариум", "Машина времени", "Агата Кристи",
         "Nautilus Pompilius", "Мумий Тролль", "Би-2", "Звери", "Земфира", "Мельница", "Нервы", "Пилот",
         "Пикник", "Кукрыниксы", "Ленинград", "Танцы Минус", "Ляпис Трубецкой", "Океан Ельзи"]


def find(obj, path=""):
    """Все ключи, похожие на страну, в сыром ответе."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if any(w in k.lower() for w in ("countr", "region", "location", "origin")):
                out.append((p, v if not isinstance(v, (dict, list)) or len(json.dumps(v, ensure_ascii=False)) < 200
                            else "…"))
            out += find(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:30]):
            out += find(v, f"{path}[{i}]")
    return out


def catalog_artists():
    arts = {}
    for p in sorted((ROOT / "catalog").glob("*.sqlite")):
        if p.stem == "app_state":
            continue
        db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        for aid, name in db.execute("SELECT id, name FROM artists WHERE scope=1"):
            arts.setdefault(aid, {"id": aid, "name": name, "db": p})
        db.close()
    return arts


def album_titles(a, n=8):
    db = sqlite3.connect(f"file:{a['db']}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT title, type FROM albums WHERE main_artist_id=? ORDER BY (type='compilation'), "
        "(type='single'), COALESCE(likes_count,0) DESC", (a["id"],)).fetchall()
    db.close()
    seen, out = set(), []
    for t, _ in rows:
        k = C.norm_title(t)
        if k and k not in seen:
            seen.add(k)
            out.append(t)
    return out[:n]


def yandex_raw(ids):
    try:
        import ym_auth
        client, _ = ym_auth.connect(interactive=False)
    except Exception as e:
        return {"error": f"нет входа в Яндекс: {type(e).__name__}"}
    res = {}
    base = client.base_url
    for aid in ids:
        r = {}
        for name, url in (("artists", f"{base}/artists?artist-ids={aid}"),
                          ("brief-info", f"{base}/artists/{aid}/brief-info")):
            try:
                data = client._request.get(url)
                r[name] = find(data) or "полей страны нет"
            except Exception as e:
                r[name] = f"ошибка {type(e).__name__}"
        res[aid] = r
        time.sleep(0.5)
    return res


def yandex_probe(arts, sample):
    """brief-info для выборки: полнота, время, объём ответа, сравнение с MusicBrainz из прошлого запуска."""
    import ym_auth
    client, _ = ym_auth.connect(interactive=False)
    base = client.base_url
    prev = {}
    pf = ROOT / "country_probe_results.json"
    if pf.exists():
        prev = {r["id"]: r for r in json.loads(pf.read_text("utf-8")).get("results", [])}
    import requests
    http = requests.Session()
    http.headers.update(client._request.headers)
    out, t0, values = [], time.time(), {}
    for i, a in enumerate(sample, 1):
        r = {"id": a["id"], "name": a["name"], "group": a["group"]}
        t = time.time()
        try:
            resp = http.get(f"{base}/artists/{a['id']}/brief-info", timeout=20)
            r["http"] = resp.status_code
            r["bytes"] = len(resp.content)
            data = resp.json().get("result") or {}
            raw = (data.get("artist") or {}).get("countries")
            r["raw"] = raw
            r["iso"] = [N.to_iso(v) for v in raw or []]
            for v in raw or []:
                values[v] = values.get(v, 0) + 1
        except Exception as e:
            r["error"] = type(e).__name__
        r["ms"] = round((time.time() - t) * 1000)
        p = prev.get(a["id"])
        if p:
            r["mb"] = p.get("country") if p.get("status") == "matched" else None
        out.append(r)
        print(f"  {i:3}/{len(sample)} {a['name'][:30]:30} {str(r.get('raw') or '—')[:40]:40} "
              f"{','.join(c or '?' for c in r.get('iso') or []) or '—':8} MB: {r.get('mb', '')}  {r['ms']} мс")
        time.sleep(0.4)
    def pct(g):
        ok = sum(1 for r in g if r.get("raw"))
        return f"{ok}/{len(g)} = {100 * ok / max(1, len(g)):.0f}%"
    print()
    for grp in ("известный", "случайный (как в прошлый раз)", "случайный (новый)"):
        g = [r for r in out if r["group"].startswith(grp)]
        if g:
            print(f"  {grp}: страна у Яндекса есть {pct(g)}")
    print(f"  всего: {pct(out)}")
    both = [r for r in out if r.get("mb") and r.get("iso")]
    agree = [r for r in both if r["mb"] in r["iso"]]
    print(f"  совпадение с MusicBrainz: {len(agree)}/{len(both)}")
    for r in both:
        if r not in agree:
            print(f"     расхождение: {r['name']}: Яндекс {r['raw']} / MusicBrainz {r['mb']}")
    unmapped = sorted(v for v in values if not N.to_iso(v))
    print(f"  разные значения: {len(values)}; не сопоставлены с кодом страны: {unmapped}")
    multi = sum(1 for r in out if r.get("raw") and len(r["raw"]) > 1)
    ms = sorted(r["ms"] for r in out)
    kb = sorted(r.get("bytes", 0) for r in out)
    print(f"  несколько стран у одного исполнителя: {multi}; ответ: медиана {ms[len(ms) // 2]} мс, "
          f"{kb[len(kb) // 2] // 1024} КБ; всего {time.time() - t0:.0f} с")
    (ROOT / "country_probe_yandex.json").write_text(json.dumps(
        {"results": out, "values": values, "unmapped": unmapped}, ensure_ascii=False, indent=1), "utf-8")
    print("\nРезультат: country_probe_yandex.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--yandex", type=int, default=0, help="проверить brief-info Яндекса: столько новых случайных")
    args = ap.parse_args()

    arts = catalog_artists()
    print(f"Исполнителей в каталоге: {len(arts)}")
    by_name = {}
    for a in arts.values():
        by_name.setdefault(a["name"], []).append(a)
    sample = []
    for n in KNOWN:
        for a in by_name.get(n, []):
            sample.append(dict(a, group="известный" + (" (одноимённые в каталоге)" if len(by_name[n]) > 1 else "")))
    taken = {a["id"] for a in sample}
    rest = [a for a in arts.values() if a["id"] not in taken]
    random.Random(args.seed).shuffle(rest)
    sample += [dict(a, group="случайный") for a in rest[:args.random]]
    if args.yandex:
        for a in sample:
            if a["group"] == "случайный":
                a["group"] = "случайный (как в прошлый раз)"
        taken = {a["id"] for a in sample}
        fresh = [a for a in arts.values() if a["id"] not in taken]
        random.Random(1000 + args.seed).shuffle(fresh)
        sample += [dict(a, group="случайный (новый)") for a in fresh[:args.yandex]]
        print(f"Выборка: {len(sample)}, только Яндекс (brief-info), ≈{len(sample) * 0.6 / 60:.0f} мин\n")
        return yandex_probe(arts, sample)
    print(f"Выборка: {len(sample)} (известных {len(sample) - min(args.random, len(rest))}, случайных "
          f"{min(args.random, len(rest))})\n")

    print("1) Есть ли страна в ответах Яндекс Музыки…")
    raw = yandex_raw([a["id"] for a in sample[:8]])
    for k, v in raw.items():
        print("  ", k, v)

    print("\n2) MusicBrainz (1 запрос/с)…")
    mb, cache, out = C.MusicBrainz(), {}, []
    t0 = time.time()
    for i, a in enumerate(sample, 1):
        titles = album_titles(a)
        try:
            r = C.resolve(mb, a["name"], titles, cache)
        except C.MBError as e:
            r = {"status": "error", "note": str(e)}
        r.update(id=a["id"], name=a["name"], group=a["group"], our_albums=titles[:4])
        out.append(r)
        print(f"  {i:3}/{len(sample)} {a['name'][:30]:30} {r['status']:10} {r.get('country') or '—':4} "
              f"{(r.get('mb_name') or '') + (' — ' + r['disambiguation'] if r.get('disambiguation') else '')}"[:150])

    total = len(out)
    st = {}
    for r in out:
        st[r["status"]] = st.get(r["status"], 0) + 1
    with_c = sum(1 for r in out if r["status"] == "matched" and r.get("country"))
    for grp in ("известный", "случайный"):
        g = [r for r in out if r["group"].startswith(grp)]
        if g:
            ok = sum(1 for r in g if r["status"] == "matched" and r.get("country"))
            print(f"\n  {grp}: страна определена {ok}/{len(g)} = {100 * ok / len(g):.0f}%")
    print(f"  всего: {with_c}/{total} = {100 * with_c / max(1, total):.0f}%;  статусы: {st}")
    print(f"  запросов к MusicBrainz: {mb.requests}, время {time.time() - t0:.0f} с "
          f"(≈{(time.time() - t0) / max(1, total):.1f} с на исполнителя)")
    (ROOT / "country_probe_results.json").write_text(json.dumps(
        {"yandex_raw": {str(k): v for k, v in raw.items()}, "results": out, "statuses": st,
         "requests": mb.requests, "seconds": round(time.time() - t0)}, ensure_ascii=False, indent=1,
        default=str), "utf-8")
    print("\nРезультат: country_probe_results.json")


if __name__ == "__main__":
    main()
