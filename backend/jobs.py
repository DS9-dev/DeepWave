"""Фоновые задачи: вход через код Яндекса, сбор каталога (crawler.py), определение стран исполнителей."""
import argparse
import json
import os
import threading
import time
import traceback
from collections import deque

import countries as C
import crawler
import ym_auth


class AuthFlow:
    def __init__(self, on_success):
        self.on_success = on_success
        self.state = {"status": "idle"}
        self.cancel = False
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return self.state
        self.cancel = False
        self.state = {"status": "starting"}
        ready = threading.Event()

        def on_code(code):
            self.state = {"status": "pending", "verification_url": code.verification_url,
                          "user_code": code.user_code, "expires_at": time.time() + (code.expires_in or 300)}
            ready.set()

        def work():
            try:
                ym_auth.device_flow(on_code, should_cancel=lambda: self.cancel)
                self.on_success()
                self.state = {"status": "ok"}
            except Exception as e:
                self.state = {"status": "error", "error": ym_auth.redact(f"{type(e).__name__}: {str(e)[:160]}")}
            ready.set()

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        ready.wait(20)
        return self.state

    def stop(self):
        self.cancel = True


class CrawlJob:
    """Запуск crawler.py в фоновом потоке приложения с тем же каталогом, блокировкой и форматом базы."""

    def __init__(self, on_progress, on_finish):
        self.on_progress, self.on_finish = on_progress, on_finish
        self.thread, self.cr, self.log = None, None, deque(maxlen=300)
        self.info = {"running": False}

    def _log(self, msg):
        self.log.append({"t": time.strftime("%H:%M:%S"), "msg": ym_auth.redact(str(msg)).strip()})

    def status(self):
        st = dict(self.info)
        cr = self.cr
        if cr is not None and st.get("running"):
            st.update(requests=cr.api.requests, max_requests=cr.a.max_requests, errors=cr.api.errors,
                      new_artists=cr.s.new["artists"], new_tracks=cr.s.new["tracks"],
                      new_albums=cr.s.new["albums"], stopping=cr.stop,
                      elapsed_s=round(time.time() - st.get("started", time.time())))
        st["log"] = list(self.log)[-80:]
        return st

    def start(self, catalog, genres, max_requests=1500, max_minutes=0):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("сбор уже идёт")
        args = argparse.Namespace(command="run", genres=",".join(genres), catalog=catalog,
                                  max_requests=int(max_requests), max_minutes=float(max_minutes or 0),
                                  delay=0.6, progress=10, queue_target=500)
        self.log.clear()
        self.info = {"running": True, "catalog": catalog, "genres": genres, "started": time.time()}
        self.thread = threading.Thread(target=self._run, args=(args,), daemon=True)
        self.thread.start()
        return self.status()

    def _run(self, args):
        reason, lock, store = None, None, None
        try:
            store, path = crawler.open_store(args)
            try:
                lock = crawler.Lock(path)
            except SystemExit:
                raise RuntimeError("этот каталог уже собирается в другом окне (crawler.py)")
            client = ym_auth.new_client()
            cur = store.db.execute("INSERT INTO runs(started, args) VALUES(?,?)",
                                   (crawler.now(), json.dumps({**vars(args), "origin": "app"})))
            store.run_id = cur.lastrowid
            deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
            api = crawler.Api(client, args.delay, args.max_requests, deadline, self._log)
            self.cr = crawler.Crawler(client, store, api, args, self._log)
            self._log(f"Каталог {path.name}: запуск, лимит {args.max_requests} запросов")
            try:
                self.cr.resolve_scope([g for g in args.genres.split(",") if g])
                reason = self.cr.run()
            except crawler.Fatal as e:
                reason = str(e)
                self.cr.flush_run(0, reason)
        except Exception as e:
            reason = f"ошибка: {ym_auth.redact(str(e))}"
            self._log(reason)
            self._log(ym_auth.redact(traceback.format_exc(limit=3)))
        finally:
            if store is not None:
                try:
                    store.rollback()
                    store.db.close()
                except Exception:
                    pass
            if lock is not None:
                lock.f.close()
            final = self.status()
            final.update(running=False, reason=reason, finished=time.time())
            try:
                self.on_finish()            # индекс библиотеки обновляется до объявления «готово»
            except Exception:
                pass
            self.info, self.cr = final, None
            self._log(f"Готово: {reason}")

    def stop(self):
        if self.cr is not None:
            self.cr.stop = True
            self._log("Остановка: завершаю текущую задачу и сохраняю…")


def artist_meta_rows(client, ids):
    """Один пакетный запрос: страны и фото исполнителей."""
    res = client.artists(ids) or []
    got = {}
    for a in res:
        if not a or a.id is None:
            continue
        uri = getattr(getattr(a, "cover", None), "uri", None) or getattr(a, "og_image", None)
        cover = f"https://{uri.replace('%%', '300x300')}" if uri else ""
        got[int(a.id)] = (list(getattr(a, "countries", None) or []), cover)
    return [(aid, *got.get(int(aid), ([], ""))) for aid in ids]


class CountriesJob:
    """Определение стран исполнителей в фоне, по одному исполнителю, с сохранением после каждого.

    Этап 1 — Яндекс Музыка: страна из карточки исполнителя (brief-info, поле artist.countries).
    Этап 2 — MusicBrainz, только для тех, у кого Яндекс страну не указал: имя + сверка альбомов
             (countries.resolve), не чаще 1 запроса в секунду.

    Статусы в artist_country:
      matched        — страна определена (source = yandex | musicbrainz);
      no_yandex      — Яндекс страну не указал, ждёт проверки MusicBrainz;
      nomatch, ambiguous, unverified, matched_no_country — не определена (повтор через RECHECK_DAYS
                       или по кнопке «Перепроверить неопределённых»);
      error          — сбой связи, повторяется при следующем запуске.
    Новые исполнители (после пополнения каталога) не имеют записи и попадают в этап 1 автоматически.
    """
    RECHECK_DAYS = 90
    MAX_FAILS = 6                  # подряд сбоев связи — этап приостанавливается до следующего запуска
    YANDEX_INTERVAL = float(os.environ.get("DEEPWAVE_YANDEX_INTERVAL", 0.35))   # пауза между запросами (тесты: 0)
    UNRESOLVED = ("nomatch", "ambiguous", "unverified", "matched_no_country")

    def __init__(self, state, library, get_client=None, mb_factory=None):
        self.state, self.library = state, library
        self.get_client = get_client or (lambda: None)
        self.mb_factory = mb_factory or C.MusicBrainz
        self.thread, self.stop_flag = None, False
        self.info = {"running": False}
        self.lock = threading.Lock()
        self.on_change = None

    # ---------------------------------------------------------------- что проверять
    def plan(self, recheck=False):
        rows = self.state.artist_country_rows()
        limit = time.time() - self.RECHECK_DAYS * 86400
        ya, mb = [], []
        for aid in list(self.library.artists):
            r = rows.get(aid)
            if r is None or r[0] == "error":
                ya.append(aid)
            elif r[0] == "no_yandex":
                mb.append(aid)
            elif r[0] in self.UNRESOLVED and (recheck or (r[2] or 0) < limit):
                ya.append(aid)                       # сначала снова Яндекс: данные могли появиться
        return ya, mb

    def coverage(self):
        rows = self.state.artist_country_rows()
        ids = list(self.library.artists)
        st, by_country, by_source = {}, {}, {}
        for aid in ids:
            r = rows.get(aid)
            k = r[0] if r else "unchecked"
            st[k] = st.get(k, 0) + 1
            if r and r[0] == "matched" and r[1]:
                by_source[r[3]] = by_source.get(r[3], 0) + 1
                for c in r[1].split(","):
                    by_country[c] = by_country.get(c, 0) + 1
        known = st.get("matched", 0)
        checked = len(ids) - st.get("unchecked", 0) - st.get("error", 0)
        ya, mb = self.plan()
        return {"artists": len(ids), "checked": checked, "known": known, "unknown": len(ids) - known,
                "known_pct": round(100 * known / len(ids), 1) if ids else None,
                "known_of_checked_pct": round(100 * known / checked, 1) if checked else None,
                "statuses": st, "by_source": by_source,
                "countries": sorted(by_country.items(), key=lambda kv: -kv[1]),
                "pending": len(ya) + len(mb), "pending_yandex": len(ya), "pending_mb": len(mb)}

    def status(self):
        return {**self.info, **self.coverage()}

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, recheck=False, auto=False, musicbrainz=True):
        with self.lock:
            if self.running():
                return self.status()
            ya, mb = self.plan(recheck)
            if auto and not ya and not mb:
                return self.status()
            if auto:
                mb = []                    # автоматически — только Яндекс для новых; MusicBrainz по кнопке
            self.stop_flag = False
            self.info = {"running": True, "auto": auto, "recheck": recheck, "phase": "yandex",
                         "done": 0, "todo": len(ya) + (len(mb) if musicbrainz else 0), "found": 0,
                         "found_yandex": 0, "found_mb": 0, "errors": 0, "started_at": time.time(),
                         "current": None, "message": None, "musicbrainz": musicbrainz}
            if not auto:
                self.state.kv("countries_started", 1)        # пользователь запускал — дальше новые сами
            self.thread = threading.Thread(target=self._run, args=(ya, mb if musicbrainz else []), daemon=True)
            self.thread.start()
        return self.status()

    # ---------------------------------------------------------------- выполнение
    def _tick(self, total_left):
        self.info["done"] += 1
        el = time.time() - self.info["started_at"]
        self.info["eta_s"] = round(el / max(1, self.info["done"]) * total_left)

    def _found(self, src):
        self.info["found"] += 1
        self.info[f"found_{src}"] += 1
        if self.on_change:
            self.on_change()

    def _run(self, ya, mb):
        try:
            if ya:
                client = self.get_client()
                if client is None:
                    self.info["message"] = "нет входа в Яндекс — этап Яндекса пропущен"
                    self.info["todo"] -= len(ya)
                else:
                    mb = mb + self._run_yandex(client, ya)
            if mb and not self.stop_flag and self.info.get("musicbrainz"):
                self.info["phase"] = "musicbrainz"
                self.info["todo"] = self.info["done"] + len(mb)
                self._run_mb(mb)
        except Exception as e:
            self.info["message"] = f"ошибка: {type(e).__name__}: {str(e)[:120]}"
        finally:
            self.info["running"] = False
            self.info["current"] = None
            if self.on_change:
                self.on_change()

    def _run_yandex(self, client, todo):
        """-> исполнители без страны у Яндекса (для MusicBrainz)."""
        left_for_mb, fails = [], 0
        for n, aid in enumerate(todo):
            if self.stop_flag:
                self.info["message"] = "остановлено"
                break
            a = self.library.artists.get(aid)
            if not a:
                self._tick(len(todo) - n - 1)
                continue
            self.info["current"] = a.get("name")
            try:
                y = C.yandex_brief(client, aid)
                fails = 0
            except Exception as e:
                fails += 1
                self.info["errors"] += 1
                name = type(e).__name__
                if name == "NotFoundError":
                    self.state.set_artist_country(aid, {"status": "no_yandex", "source": "yandex",
                                                        "note": "карточка не найдена"})
                    left_for_mb.append(aid)
                else:
                    self.state.set_artist_country(aid, {"status": "error", "source": "yandex",
                                                        "note": ym_auth.redact(f"{name}: {str(e)[:100]}")})
                    time.sleep(min(30, 2 ** fails))
                if fails >= self.MAX_FAILS:
                    self.info["message"] = "Яндекс не отвечает — проверка приостановлена, продолжится при следующем запуске"
                    return []
                self._tick(len(todo) - n - 1)
                continue
            if y["countries"]:
                self.state.set_artist_country(aid, {"status": "matched", "countries": y["countries"], "source": "yandex",
                                                    "raw": y["raw"], "albums": y["albums"]})
                self._found("yandex")
            else:
                self.state.set_artist_country(aid, {"status": "no_yandex", "source": "yandex", "raw": y["raw"],
                                                    "albums": y["albums"],
                                                    "note": f"нераспознанное значение: {y['raw']}" if y["raw"] else ""})
                left_for_mb.append(aid)
            self._tick(len(todo) - n - 1)
            time.sleep(self.YANDEX_INTERVAL)
        return left_for_mb

    def _album_titles(self, ids):
        """Названия альбомов для сверки: сначала из карточки Яндекса (основные альбомы), затем из каталога."""
        from_ya = self.state.artist_country_albums(ids)
        cat = {}
        order = {"album": 0, None: 1, "": 1, "single": 2, "compilation": 3}
        for al in list(self.library.albums.values()):
            if al.get("artist_id") in ids and al.get("title"):
                cat.setdefault(al["artist_id"], []).append(al)
        out = {}
        for aid in ids:
            titles = list(from_ya.get(aid, []))
            for al in sorted(cat.get(aid, []), key=lambda a: (order.get(a.get("type"), 1), -(a.get("year") or 0))):
                if al["title"] not in titles:
                    titles.append(al["title"])
            out[aid] = titles[:8]
        return out

    def _run_mb(self, todo):
        mb, cache, fails = self.mb_factory(), {}, 0
        titles = self._album_titles(set(todo))
        for n, aid in enumerate(todo):
            if self.stop_flag:
                self.info["message"] = "остановлено"
                break
            a = self.library.artists.get(aid)
            if not a:
                self._tick(len(todo) - n - 1)
                continue
            self.info["current"] = a.get("name")
            try:
                res = C.resolve(mb, a.get("name"), titles.get(aid, []), cache)
                fails = 0
            except Exception as e:                       # сеть / MusicBrainz перегружен
                res = {"status": "no_yandex", "note": f"MusicBrainz: {type(e).__name__}"}
                fails += 1
                self.info["errors"] += 1
            res["source"] = "musicbrainz"
            res.setdefault("countries", [res["country"]] if res.get("country") else [])
            self.state.set_artist_country(aid, res)
            if res.get("status") == "matched":
                self._found("mb")
            self.info["requests_mb"] = mb.requests
            self._tick(len(todo) - n - 1)
            if fails >= self.MAX_FAILS:
                self.info["message"] = "MusicBrainz недоступен — проверка приостановлена, продолжится при следующем запуске"
                break

    def stop(self):
        self.stop_flag = True
