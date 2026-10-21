"""Определение страны исполнителя по открытой базе MusicBrainz.

Правила надёжности:
  * имя исполнителя в Яндекс Музыке должно совпасть с именем или псевдонимом в MusicBrainz;
  * совпадения имени недостаточно: хотя бы один альбом исполнителя из нашего каталога должен найтись
    среди релизов этого исполнителя в MusicBrainz (так отсекаются одноимённые группы);
  * если подтверждено несколько разных исполнителей с разными странами — страна не присваивается;
  * страна берётся из поля country, а если указан город/регион — по цепочке «входит в» до страны;
  * язык песен, жанр и написание имени для определения страны НЕ используются.

Нагрузка: не чаще 1 запроса в секунду (правила MusicBrainz), представление приложения в User-Agent,
повторы с паузой при 503/429/сетевых ошибках.
"""
import re
import time

import requests

from country_names import to_iso

MB = "https://musicbrainz.org/ws/2"
USER_AGENT = "DeepWave/1.2 ( https://github.com/DS9-dev/DeepWave )"
LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


GENERIC = {norm for norm in (
    "greatesthits", "thebestof", "bestof", "best", "live", "hits", "collection", "thecollection",
    "anthology", "demo", "demos", "ep", "singles", "thesingles", "unplugged", "acoustic", "remastered",
    "лучшее", "избранное", "лучшиепесни", "концерт", "live", "сборник", "демо", "антология")}


class MBError(Exception):
    """Сервис недоступен после повторов — артиста нужно проверить позже."""


def esc(s):
    return LUCENE_SPECIAL.sub(r"\\\1", s or "")


def norm_name(s):
    s = (s or "").casefold().replace("ё", "е").replace("&", " and ")
    s = re.sub(r"^the\s+", "", s.strip())
    return re.sub(r"[\W_]+", "", s)


def norm_title(s):
    s = (s or "").casefold().replace("ё", "е")
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"\s[-–—]\s.*$", " ", s)
    return re.sub(r"[\W_]+", "", s)


class MusicBrainz:
    def __init__(self, min_interval=1.1, timeout=20, session=None):
        self.http = session or requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.min_interval, self.timeout = min_interval, timeout
        self._last = 0.0
        self.requests = 0

    def get(self, path, params):
        params = {**params, "fmt": "json"}
        for attempt in range(5):
            wait = self._last + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.requests += 1
            try:
                r = self.http.get(f"{MB}{path}", params=params, timeout=self.timeout)
            except requests.RequestException:
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 3 * 2 ** attempt))       # MusicBrainz просит снизить частоту
                continue
            raise MBError(f"HTTP {r.status_code}")
        raise MBError("MusicBrainz недоступен")

    def search_artists(self, name, limit=10):
        q = f'artist:"{esc(name)}" OR alias:"{esc(name)}"'
        return (self.get("/artist", {"query": q, "limit": limit}) or {}).get("artists", [])

    def confirm(self, mbids, titles):
        """Один запрос: какие из наших альбомов есть у каких кандидатов. -> {mbid: [наши названия]}"""
        want = {}
        for t in titles:
            k = norm_title(t)
            if k and k not in want:
                want[k] = t
        if not want or not mbids:
            return {}
        q = ("(" + " OR ".join(f"arid:{m}" for m in mbids) + ") AND ("
             + " OR ".join(f'releasegroup:"{esc(t)}"' for t in list(want.values())[:8]) + ")")
        res = self.get("/release-group", {"query": q, "limit": 50}) or {}
        hits = {}
        for rg in res.get("release-groups", []):
            k = norm_title(rg.get("title"))
            if k not in want:
                continue
            for ac in rg.get("artist-credit") or []:
                aid = (ac.get("artist") or {}).get("id") if isinstance(ac, dict) else None
                if aid in mbids and want[k] not in hits.setdefault(aid, []):
                    hits[aid].append(want[k])
        return hits

    def area(self, area_id):
        return self.get(f"/area/{area_id}", {"inc": "area-rels"})


def area_country(mb, area, cache, depth=0):
    """ISO-код страны для области MusicBrainz: страна сама или по цепочке «входит в»."""
    if not area or not area.get("id") or depth > 6:
        return None
    aid = area["id"]
    if aid in cache:
        return cache[aid]
    codes = area.get("iso-3166-1-codes")
    if area.get("type") == "Country" and codes:
        cache[aid] = to_iso(codes[0])
        return cache[aid]
    full = mb.area(aid)
    result = None
    if full:
        codes = full.get("iso-3166-1-codes")
        if full.get("type") == "Country" and codes:
            result = to_iso(codes[0])
        else:
            for rel in full.get("relations", []):
                if rel.get("type") == "part of" and rel.get("direction") == "backward" and rel.get("area"):
                    result = area_country(mb, rel["area"], cache, depth + 1)
                    if result:
                        break
    cache[aid] = result
    return result


def candidate_country(mb, cand, cache):
    if to_iso(cand.get("country")):
        return to_iso(cand["country"])
    for key in ("area", "begin-area"):
        c = area_country(mb, cand.get(key), cache)
        if c:
            return c
    return None


def resolve(mb, name, titles, area_cache):
    """status ∈ matched | matched_no_country | nomatch | ambiguous | unverified; country — ISO-код или None."""
    key = norm_name(name)
    if not key:
        return {"status": "nomatch", "note": "пустое имя"}
    cands = []
    for c in mb.search_artists(name):
        names = [c.get("name"), c.get("sort-name")] + [a.get("name") for a in c.get("aliases") or []]
        if any(norm_name(n) == key for n in names if n):
            cands.append(c)
    if not cands:
        return {"status": "nomatch", "note": "в MusicBrainz нет исполнителя с таким именем"}
    if not titles:
        return {"status": "unverified", "note": "нет альбомов для сверки", "candidates": len(cands)}
    cands = cands[:5]
    hits = mb.confirm([c["id"] for c in cands], titles)
    # одно совпадение по «общему» названию (одноимённый альбом, Greatest Hits, Live…) — слабое доказательство
    def strong(h):
        return len(h) >= 2 or any(norm_title(t) not in GENERIC and norm_title(t) != key for t in h)
    confirmed = [(c, hits[c["id"]]) for c in cands if hits.get(c["id"]) and strong(hits[c["id"]])]
    weak = [c for c in cands if hits.get(c["id"]) and not strong(hits[c["id"]])]
    if not confirmed and weak:
        return {"status": "unverified", "note": "совпало только общее название альбома", "candidates": len(cands)}
    if not confirmed:
        return {"status": "nomatch", "note": f"одноимённые исполнители есть ({len(cands)}), но альбомы не совпали",
                "candidates": len(cands)}
    # кандидат без страны конфликта не создаёт; разные известные страны — создают
    scored = [(candidate_country(mb, c, area_cache), c, h) for c, h in confirmed]
    known = {k for k, _, _ in scored if k}
    if len(known) > 1:
        return {"status": "ambiguous", "note": "альбомы совпали у нескольких исполнителей с разными странами",
                "candidates": len(cands)}
    scored.sort(key=lambda x: (x[0] is None, -len(x[2])))
    country, c, hits = scored[0]
    return {"status": "matched" if country else "matched_no_country", "country": country, "mbid": c["id"], "mb_name": c.get("name"),
            "disambiguation": c.get("disambiguation"), "albums_matched": hits[:3], "candidates": len(cands)}


# ---------------------------------------------------------------- Яндекс Музыка: /artists/{id}/brief-info
def yandex_brief(client, artist_id):
    """Страна из карточки исполнителя Яндекса (поле artist.countries приходит только в brief-info,
    пакетный /artists его не заполняет). -> {countries: [ISO], raw: [...], albums: [названия]}"""
    data = client._request.get(f"{client.base_url}/artists/{int(artist_id)}/brief-info") or {}
    artist = data.get("artist") or {}
    raw = list(artist.get("countries") or [])
    codes = []
    for v in raw:
        c = to_iso(v)
        if c and c not in codes:
            codes.append(c)
    albums = [a.get("title") for a in (data.get("albums") or []) + (data.get("also_albums") or data.get("alsoAlbums") or [])
              if isinstance(a, dict) and a.get("title")]
    return {"countries": codes, "raw": raw, "albums": albums[:10]}
