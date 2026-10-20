"""Фоновые задачи: вход через код Яндекса, сбор каталога (crawler.py), определение стран исполнителей."""
import argparse
import json
import threading
import time
import traceback
from collections import deque

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
    """Определение стран исполнителей (поле Artist.countries) пакетами по 100."""

    def __init__(self, get_client, state, library):
        self.get_client, self.state, self.library = get_client, state, library
        self.thread, self.stop_flag = None, False
        self.info = {"running": False}

    def coverage(self):
        known = self.state.countries()
        ids = list(self.library.artists)
        checked = [a for a in ids if a in known]
        with_c = [a for a in checked if known[a]]
        values = {}
        for a in with_c:
            for c in known[a]:
                values[c] = values.get(c, 0) + 1
        ru = self.library.ru_ids(known)
        return {"artists": len(ids), "checked": len(checked), "with_country": len(with_c),
                "coverage_pct": round(100 * len(with_c) / len(checked), 1) if checked else None,
                "ru_artists": len([a for a in ids if a in ru]),
                "top_values": sorted(values.items(), key=lambda kv: -kv[1])[:15]}

    def status(self):
        return {**self.info, **self.coverage()}

    def start(self):
        if self.thread and self.thread.is_alive():
            return self.status()
        self.stop_flag = False
        self.info = {"running": True, "done": 0, "errors": 0}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self.status()

    def _run(self):
        try:
            known = self.state.countries()
            todo = [a for a in self.library.artists if a not in known]
            self.info["todo"] = len(todo)
            client = self.get_client()
            for i in range(0, len(todo), 100):
                if self.stop_flag or client is None:
                    break
                chunk = todo[i:i + 100]
                try:
                    self.state.set_artist_meta(artist_meta_rows(client, chunk))
                except Exception:
                    self.info["errors"] += 1
                    time.sleep(3)
                self.info["done"] = min(len(todo), i + 100)
                time.sleep(0.6)
        finally:
            self.info["running"] = False

    def stop(self):
        self.stop_flag = True
