"""Аудио: штатное получение mp3 через download-info (без шифрования), обложки, проверка доступности.

Прямые ссылки короткоживущие, поэтому проигрыватель ходит через локальный прокси /stream/<id>,
который при каждом запросе (в т.ч. перемотке после долгой паузы) берёт свежую ссылку.
"""
import threading
import time

import requests


class Unavailable(Exception):
    pass


class Audio:
    def __init__(self, get_client, state):
        self.get_client = get_client          # клиент может смениться после входа/выхода
        self.state = state
        self.http = requests.Session()
        self.cache = {}                       # track_id -> (url, info, t)
        self.lock = threading.Lock()

    def resolve(self, track_id, fresh=False, max_age=45):
        track_id = str(track_id)
        with self.lock:
            hit = self.cache.get(track_id)
            if hit and not fresh and time.time() - hit[2] < max_age:
                return hit[0], hit[1]
        client = self.get_client()
        if client is None:
            raise PermissionError("не выполнен вход")
        try:
            infos = client.tracks_download_info(track_id) or []
        except Exception as e:
            if type(e).__name__ in ("NotFoundError", "BadRequestError"):
                raise Unavailable(type(e).__name__)
            raise
        full = [i for i in infos if not i.preview]
        if not full:
            raise Unavailable("нет полной версии" if infos else "нет вариантов загрузки")
        best = sorted(full, key=lambda i: (i.codec == "mp3", i.bitrate_in_kbps), reverse=True)[0]
        url = best.get_direct_link()
        info = {"codec": best.codec, "bitrate": best.bitrate_in_kbps}
        with self.lock:
            self.cache[track_id] = (url, info, time.time())
            if len(self.cache) > 300:
                for k, _ in sorted(self.cache.items(), key=lambda kv: kv[1][2])[:100]:
                    self.cache.pop(k, None)
        return url, info

    def cover(self, track_id):
        url = self.state.cover(track_id)
        if url is not None:
            return url or None
        client = self.get_client()
        if client is None:
            return None
        try:
            t = (client.tracks([str(track_id)]) or [None])[0]
            uri = getattr(t, "cover_uri", None) or (getattr(t.albums[0], "cover_uri", None) if t and t.albums else None)
            url = f"https://{uri.replace('%%', '400x400')}" if uri else ""
        except Exception:
            return None
        self.state.set_cover(track_id, url)
        return url or None

    def covers(self, track_ids):
        """Пакетно — для списков (до 100 треков за запрос)."""
        out, need = {}, []
        for tid in track_ids:
            u = self.state.cover(tid)
            if u is None:
                need.append(str(tid))
            elif u:
                out[str(tid)] = u
        client = self.get_client()
        if need and client is not None:
            try:
                for t in client.tracks(need[:100]) or []:
                    uri = getattr(t, "cover_uri", None) or (t.albums[0].cover_uri if t.albums else None)
                    url = f"https://{uri.replace('%%', '200x200')}" if uri else ""
                    self.state.set_cover(t.id, url.replace("200x200", "400x400"))
                    if url:
                        out[str(t.id)] = url.replace("200x200", "400x400")
            except Exception:
                pass
        return out

    def open_stream(self, track_id, range_header=None):
        """Возвращает ответ upstream; при истёкшей ссылке один раз берёт новую."""
        hdr = {"Range": range_header} if range_header else {}
        for attempt in (0, 1):
            url, _ = self.resolve(track_id, fresh=attempt == 1)
            up = self.http.get(url, headers=hdr, stream=True, timeout=(10, 30))
            if up.status_code in (403, 404, 410) and attempt == 0:
                up.close()
                continue
            return up
        return up
