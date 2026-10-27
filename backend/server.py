"""DeepWave — локальный сервис для Electron-интерфейса.

Запускается приложением автоматически:  python backend/server.py --port 0 --watch-stdin
(одноразовый ключ передаётся в переменной окружения DEEPWAVE_TOKEN)
Слушает только 127.0.0.1; каждый запрос должен содержать ключ (заголовок X-Token или ?t=).
"""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ym_auth                      # noqa: E402
import crawler                      # noqa: E402
import scripts                      # noqa: E402
from state import State             # noqa: E402
from library import Library         # noqa: E402
from audio import Audio, Unavailable  # noqa: E402
from library import country_blocked  # noqa: E402
from country_names import all_countries  # noqa: E402
from jobs import AuthFlow, CrawlJob, CountriesJob, artist_meta_rows  # noqa: E402

log = logging.getLogger("deepwave")


class App:
    def __init__(self, root: Path):
        self.root = root
        self.cat_dir = root / "catalog"
        self.cat_dir.mkdir(exist_ok=True)
        self.state = State(self.cat_dir / "app_state.sqlite")
        self.library = Library(self.cat_dir)
        self.client, self.account, self.conn_error = None, None, None
        self.audio = Audio(lambda: self.client, self.state)
        self.auth = AuthFlow(on_success=self.connect)
        self.crawl = CrawlJob(on_progress=None, on_finish=self.rebuild)
        self.countries = CountriesJob(self.state, self.library, lambda: self.client)
        self._blocked = None                       # кеш фильтра стран: (ключ настроек, множество)
        self.countries.on_change = self._invalidate_filter
        self._maint_lock = threading.Lock()
        self._extend_lock = threading.Lock()
        self.rebuild()
        threading.Thread(target=self.connect, daemon=True).start()
        threading.Thread(target=self._refresher, daemon=True).start()
        threading.Thread(target=self._maintenance_loop, daemon=True).start()

    def connect(self):
        if not ym_auth.has_token():
            self.client, self.account, self.conn_error = None, None, None
            return
        try:
            self.client, self.account = ym_auth.connect(interactive=False)
            self.conn_error = None
            self._load_genres()
        except Exception as e:
            self.client = None
            self.conn_error = ym_auth.redact(f"{type(e).__name__}: {str(e)[:160]}")

    def _load_genres(self):
        try:
            genres = self.client.genres()
        except Exception:
            return
        titles, tree = {}, []

        def walk(g):
            titles[g.id] = g.title
            return {"id": g.id, "title": g.title, "sub": [walk(s) for s in (g.sub_genres or [])]}
        tree = [walk(g) for g in genres]
        self.state.kv("genre_titles", titles)
        self.state.kv("genre_tree", tree)
        self.rebuild()

    def genre_parents(self):
        parents, order = {}, []
        for g in self.state.kv("genre_tree") or []:
            order.append(g["id"])
            parents[g["id"]] = None

            def walk(node, root):
                for sub in node.get("sub") or []:
                    parents[sub["id"]] = root
                    walk(sub, root)
            walk(g, g["id"])
        return parents, order

    def rebuild(self):
        parents, order = self.genre_parents()
        self.library.rebuild(self.state.kv("genre_titles") or {}, parents, order)
        self._invalidate_filter()
        self.auto_countries()

    def auto_countries(self):
        """Новые исполнители проверяются сами, если пользователь уже запускал определение стран
        и фильтр стран включён. Проверка идёт в фоне, 1 запрос в секунду."""
        try:
            if self.client and self.state.kv("countries_started") and self.state.settings().get("country_mode") != "all":
                self.countries.start(auto=True, musicbrainz=False)    # MusicBrainz — по кнопке
        except Exception as e:
            log.warning("страны: автозапуск не удался: %s", e)

    def maintain(self):
        """Обслуживание: срок хранения истории, устаревшие ссылки на обложки, сжатие базы состояния."""
        with self._maint_lock:
            try:
                res = self.state.maintain()
                log.info("обслуживание: %s", res)
                return res
            except Exception as e:
                log.warning("обслуживание не удалось: %s", e)
                return {"error": str(e)}

    def _maintenance_loop(self):
        time.sleep(45)                  # не мешать запуску: первая очистка через 45 с, затем раз в 6 часов
        while True:
            self.maintain()
            time.sleep(6 * 3600)

    def save_settings(self, patch):
        before = self.state.settings().get("history_months")
        res = self.state.set_settings(patch)
        if res.get("history_months") != before:          # срок хранения изменён — очистить сразу, в фоне
            threading.Thread(target=self.maintain, daemon=True).start()
        self._invalidate_filter()
        if any(k.startswith("country") for k in patch):
            self.auto_countries()
        return res

    def _refresher(self):
        while True:                     # во время сбора каталог пополняется — обновляем индекс
            time.sleep(90)
            if self.crawl.info.get("running"):
                try:
                    self.rebuild()
                except Exception:
                    pass

    # ------------------------------------------------------------------ выбор и подготовка трека
    def _invalidate_filter(self):
        self._blocked = None

    def blocked(self, settings):
        """Исполнители, которых не пропускает фильтр стран (кешируется до изменения настроек или данных)."""
        key = (settings.get("country_mode"), tuple(settings.get("country_list") or []),
               bool(settings.get("country_unknown", True)), self.library.built_at)
        cached = self._blocked
        if cached and cached[0] == key:
            return cached[1]
        ids = country_blocked(self.library.artists.keys(), self.state.artist_countries(), settings)
        self._blocked = (key, ids)
        return ids

    def extend_catalog(self, b):
        """Добавить жанры (со всеми поджанрами) в существующий каталог: резервная копия + пересчёт."""
        name = re.sub(r"[^\w+-]", "_", (b.get("catalog") or "").strip())
        roots = [g for g in (b.get("genres") or []) if isinstance(g, str) and re.match(r"^[\w-]+$", g)]
        path = self.cat_dir / f"{name}.sqlite"
        if not name or not path.exists() or not roots:
            return {"error": "укажите каталог и жанры"}
        if self.crawl.info.get("running"):
            return {"error": "сначала остановите сбор каталога"}
        parents, _ = self.genre_parents()
        if not parents:
            return {"error": "список жанров Яндекса ещё не загружен — войдите в Яндекс"}
        with self._extend_lock:
            try:
                res = crawler.extend_catalog(path, roots, parents, log.info)
            except SystemExit:
                return {"error": "этот каталог сейчас собирается в другом окне (crawler.py)"}
            except crawler.Fatal as e:
                return {"error": str(e)}
        self.rebuild()
        res["catalogs"] = self.library.catalogs
        return res

    def pool(self, keys):
        settings = self.state.settings()
        return self.library.pool_summary(keys, self.blocked(settings),
                                         scripts.exclusion_mask(settings.get("title_scripts_excluded")))

    def countries_status(self):
        """Один формат ответа для всех запросов о странах (интерфейс рассчитывает на поле filter)."""
        return {**self.countries.status(), "filter": self.filter_summary()}

    def filter_summary(self, settings=None):
        settings = settings or self.state.settings()
        n = len(self.library.artists)
        b = len(self.blocked(settings))
        return {"artists": n, "blocked": b, "allowed": n - b}

    def prepare(self, rec):
        """Проверяет доступность (получает ссылку) и добавляет обложку."""
        url, info = self.audio.resolve(rec["id"])
        return {**rec, **info, "cover": self.audio.cover(rec["id"]), "stream": f"/stream/{rec['id']}"}

    def next_track(self, body):
        settings = self.state.settings()
        keys = body.get("genres") or settings.get("genres") or []
        exclude = set(map(str, body.get("exclude") or []))
        blocked = self.blocked(settings)
        smask = scripts.exclusion_mask(settings.get("title_scripts_excluded"))
        last_err = None
        for _ in range(8):
            rec = self.library.pick(keys, self.state, settings, blocked, exclude, body.get("exclude_artists") or [],
                                    script_mask=smask)
            if rec is None:
                if not self.library.eligible_artists(keys):
                    return {"error": "в выбранных жанрах нет треков"}
                if smask and not self.library.eligible_artists(keys, None, smask):
                    return {"error": "фильтр письменности названий исключил все треки выбранных жанров — "
                                     "измените его в настройках", "filter": True}
                return {"error": "фильтры стран и письменности исключили всех исполнителей выбранных жанров — "
                                 "измените их в настройках", "filter": True}
            try:
                return self.prepare(rec)
            except Unavailable as e:
                self.state.mark_unavailable(rec["id"], str(e))
                exclude.add(rec["id"])
                last_err = str(e)
            except PermissionError:
                return {"error": "auth"}
            except Exception as e:
                last_err = ym_auth.redact(f"{type(e).__name__}: {str(e)[:120]}")
                exclude.add(rec["id"])
                time.sleep(0.5)
        return {"error": f"не удалось получить трек: {last_err}", "retry": True}

    def artist_images(self, ids):
        ids = [int(i) for i in ids if str(i).lstrip("-").isdigit()][:200]
        known = self.state.artist_covers(ids)
        need = [i for i in ids if i not in known]
        if need and self.client is not None:
            for i in range(0, len(need), 100):
                try:
                    rows = artist_meta_rows(self.client, need[i:i + 100])
                    self.state.set_artist_meta(rows)
                    known.update({aid: cover for aid, _, cover in rows})
                except Exception:
                    break
        return {str(k): v for k, v in known.items() if v}

    def status(self):
        return {"auth": "ok" if self.client else ("offline" if self.conn_error else
                                                  ("none" if not ym_auth.has_token() else "connecting")),
                "account": self.account, "conn_error": self.conn_error, "root": str(self.root),
                "catalogs": len(self.library.catalogs), "tracks": len(self.library.tracks),
                "artists": len(self.library.artists), "crawl_running": bool(self.crawl.info.get("running")),
                "stats": self.state.stats(), "python": sys.version.split()[0]}


def make_handler(app: App, token: str):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # -------------------------------------------------------------- утилиты
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token, Range")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def _authorized(self, query):
            return self.headers.get("X-Token") == token or query.get("t", [None])[0] == token

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._authorized(q):
                return self._json({"error": "forbidden"}, 403)
            body = self._body() if method == "POST" else {}
            try:
                if u.path.startswith("/stream/"):
                    return self._stream(u.path.rsplit("/", 1)[-1])
                res = self._api(method, u.path, q, body)
                if res is None:
                    return self._json({"error": "not found"}, 404)
                self._json(res)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as e:
                log.error(ym_auth.redact(traceback.format_exc()))
                try:
                    self._json({"error": ym_auth.redact(f"{type(e).__name__}: {str(e)[:200]}")}, 500)
                except Exception:
                    pass

        # -------------------------------------------------------------- API
        def _api(self, m, path, q, b):
            one = lambda k, d=None: q.get(k, [d])[0]
            r = re.match(r"^/api/(artist|album|track)/(\w+)$", path)
            if m == "GET" and r:
                kind, oid = r.groups()
                if kind == "artist":
                    return app.library.artist(oid)
                if kind == "album":
                    return app.library.album(oid)
                rec = app.library.public(oid)
                if rec is None:
                    return None
                try:
                    return app.prepare(rec)
                except Unavailable as e:
                    app.state.mark_unavailable(oid, str(e))
                    return {**rec, "error": "unavailable"}
            routes = {
                ("GET", "/api/status"): lambda: app.status(),
                ("POST", "/api/auth/start"): lambda: app.auth.start(),
                ("GET", "/api/auth/state"): lambda: {**app.auth.state, **app.status()},
                ("POST", "/api/auth/cancel"): lambda: (app.auth.stop(), {"ok": True})[1],
                ("POST", "/api/auth/logout"): lambda: self._logout(),
                ("POST", "/api/reconnect"): lambda: (app.connect(), app.status())[1],
                ("GET", "/api/catalogs"): lambda: {"catalogs": app.library.catalogs, "built_at": app.library.built_at},
                ("GET", "/api/genre-tree"): lambda: {"tree": app.state.kv("genre_tree") or []},
                ("POST", "/api/next"): lambda: app.next_track(b),
                ("POST", "/api/covers"): lambda: app.audio.covers(b.get("ids") or []),
                ("POST", "/api/artist-images"): lambda: app.artist_images(b.get("ids") or []),
                ("GET", "/api/search"): lambda: app.library.search(one("q", ""), int(one("limit", 60))),
                ("GET", "/api/discover"): lambda: {"artists": app.library.random_artists(int(one("n", 24)))},
                ("GET", "/api/history"): lambda: {"items": app.state.history(int(one("limit", 200)), int(one("offset", 0))),
                                                  "stats": app.state.stats()},
                ("POST", "/api/history/start"): lambda: self._play_start(b),
                ("POST", "/api/history/end"): lambda: (app.state.play_end(b["play_id"], b.get("listened_s"),
                                                                          b.get("status", "finished")), {"ok": True})[1],
                ("GET", "/api/settings"): lambda: app.state.settings(),
                ("POST", "/api/settings"): lambda: app.save_settings(b),
                ("GET", "/api/storage"): lambda: app.state.storage(),
                ("POST", "/api/storage/clear-cache"): lambda: {"covers_removed": app.state.clear_cache(), **app.state.storage()},
                ("POST", "/api/storage/maintain"): lambda: {**app.maintain(), **app.state.storage()},
                ("GET", "/api/crawl"): lambda: app.crawl.status(),
                ("POST", "/api/crawl/start"): lambda: self._crawl_start(b),
                ("POST", "/api/crawl/stop"): lambda: (app.crawl.stop(), app.crawl.status())[1],
                ("POST", "/api/catalog/extend"): lambda: app.extend_catalog(b),
                ("POST", "/api/pool"): lambda: app.pool(b.get("genres") or []),
                ("GET", "/api/scripts"): lambda: {"scripts": scripts.catalog(), **app.library.script_counts()},
                ("POST", "/api/rebuild"): lambda: (app.rebuild(), {"catalogs": app.library.catalogs})[1],
                ("GET", "/api/countries/names"): lambda: {"names": all_countries()},
                ("GET", "/api/countries"): lambda: app.countries_status(),
                ("POST", "/api/countries/start"): lambda: (app.countries.start(
                    recheck=bool(b.get("recheck")), musicbrainz=b.get("musicbrainz", True) is not False),
                    app.countries_status())[1],
                ("POST", "/api/countries/stop"): lambda: (app.countries.stop(), app.countries_status())[1],
                ("POST", "/api/shutdown"): lambda: self._shutdown(),
            }
            fn = routes.get((m, path))
            return fn() if fn else None

        def _logout(self):
            app.crawl.stop()
            ym_auth.forget_token()
            app.client, app.account = None, None
            return app.status()

        def _play_start(self, b):
            rec = app.library.public(b.get("track_id")) or {"id": str(b.get("track_id"))}
            return {"play_id": app.state.play_start(rec, b.get("origin", "radio"))}

        def _crawl_start(self, b):
            if app.client is None:
                return {"error": "сначала войдите в Яндекс"}
            name = re.sub(r"[^\w+-]", "_", (b.get("catalog") or "").strip())
            genres = [g for g in (b.get("genres") or []) if re.match(r"^[\w-]+$", g)]
            existing = {c["name"]: c for c in app.library.catalogs}
            if name in existing:
                genres = existing[name].get("roots") or genres
            if not name or not genres:
                return {"error": "укажите каталог и жанры"}
            try:
                return app.crawl.start(name, genres, b.get("max_requests") or 1500, b.get("max_minutes") or 0)
            except RuntimeError as e:
                return {"error": str(e)}

        def _shutdown(self):
            app.crawl.stop()
            app.countries.stop()
            threading.Thread(target=lambda: (time.sleep(0.3), self.server.shutdown()), daemon=True).start()
            return {"ok": True}

        # -------------------------------------------------------------- поток
        def _stream(self, tid):
            try:
                up = app.audio.open_stream(tid, self.headers.get("Range"))
            except Unavailable as e:
                app.state.mark_unavailable(tid, str(e))
                return self._json({"error": "unavailable"}, 404)
            except Exception as e:
                return self._json({"error": ym_auth.redact(str(e)[:120])}, 502)
            self.send_response(up.status_code)
            self._cors()
            self.send_header("Content-Type", "audio/mpeg")
            for h in ("Content-Length", "Content-Range"):
                if up.headers.get(h):
                    self.send_header(h, up.headers[h])
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for chunk in up.iter_content(64 * 1024):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass                    # перемотка/переключение: браузер закрыл соединение
            finally:
                up.close()
                self.close_connection = True
    return H


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--token", default=os.environ.get("DEEPWAVE_TOKEN") or os.environ.get("MUSIC_APP_TOKEN", ""))
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--watch-stdin", action="store_true",
                   help="завершиться, когда закроется stdin (родительское приложение закрылось или упало)")
    a = p.parse_args()
    if not a.token:
        sys.exit("нужен --token")
    root = Path(a.root)
    (root / "catalog").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[RotatingFileHandler(root / "catalog" / "app_backend.log", maxBytes=1_000_000,
                                                      backupCount=1, encoding="utf-8")])
    for h in logging.getLogger().handlers:
        h.addFilter(ym_auth.RedactFilter())
    logging.getLogger("yandex_music").setLevel(logging.ERROR)
    app = App(root)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app, a.token))
    srv.daemon_threads = True
    if a.watch_stdin:
        def watch():
            try:
                while sys.stdin.buffer.read(1024):
                    pass
            except Exception:
                pass
            log.info("родительский процесс закрыт — завершаюсь")
            app.crawl.stop()
            srv.shutdown()
        threading.Thread(target=watch, daemon=True).start()
    print(f"READY {srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        app.crawl.stop()
        t = app.crawl.thread
        if t and t.is_alive():
            t.join(timeout=60)          # даём сборщику дописать текущую задачу
        srv.server_close()


if __name__ == "__main__":
    main()
