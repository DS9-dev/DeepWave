"""
DeepWave (исследование) — проверка воспроизведения треков из локального каталога.

  python play_probe.py probe            # проверить 30 случайных треков (≈1–2 мин)
  python play_probe.py probe --n 50 --expiry   # + проверка срока жизни ссылки (+6 мин)
  python play_probe.py player           # тестовый проигрыватель в браузере (Ctrl+C — выход)

Каталог читается только для чтения (catalog/allrock.sqlite не изменяется).
Аудио берётся штатным способом API (download-info → mp3/aac без шифрования) с вашей подпиской.
Прослушивания в историю Яндекса не отправляются. Токен нигде не выводится.
Результаты: results_play/
"""
import argparse
import json
import random
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from ym_auth import connect, redact

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_play"
OUT.mkdir(exist_ok=True)


# ------------------------------------------------------------------ каталог
class Catalog:
    """Случайный выбор: сначала исполнитель, потом его трек; без недавних повторов."""

    def __init__(self, path, recent_artists=200):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        self.artists = [r[0] for r in self.db.execute(
            "SELECT DISTINCT main_artist_id FROM tracks WHERE scope=1 AND main_artist_id IS NOT NULL")]
        self.recent = deque(maxlen=min(recent_artists, max(1, len(self.artists) // 2)))
        self.played = set()

    def pick(self):
        with self.lock:
            for _ in range(50):
                aid = random.choice(self.artists)
                if aid in self.recent:
                    continue
                rows = self.db.execute(
                    "SELECT t.id, t.title, t.version, t.artists, t.duration_ms, al.title, al.year, t.album_genre "
                    "FROM tracks t LEFT JOIN albums al ON al.id=t.album_id WHERE t.main_artist_id=? AND t.scope=1",
                    (aid,)).fetchall()
                rows = [r for r in rows if r[0] not in self.played]
                if not rows:
                    continue
                r = random.choice(rows)
                self.recent.append(aid)
                self.played.add(r[0])
                return {"id": r[0], "title": r[1] + (f" ({r[2]})" if r[2] else ""), "artists": r[3],
                        "duration_ms": r[4], "album": r[5], "year": r[6], "genre": r[7]}
        return None


# ------------------------------------------------------------------ аудио
class Audio:
    def __init__(self, client):
        self.c = client
        self.http = requests.Session()
        self.cache = {}          # track_id -> (url, info, t)
        self.lock = threading.Lock()

    def resolve(self, track_id, fresh=False):
        """Свежая прямая ссылка на лучший mp3/aac. Ссылки короткоживущие — кэш 30 с."""
        with self.lock:
            hit = self.cache.get(track_id)
            if hit and not fresh and time.time() - hit[2] < 30:
                return hit[0], hit[1]
        infos = self.c.tracks_download_info(track_id) or []
        if not infos:
            raise RuntimeError("нет вариантов загрузки")
        opts = sorted(infos, key=lambda i: (not i.preview, i.codec == "mp3", i.bitrate_in_kbps), reverse=True)
        best = opts[0]
        url = best.get_direct_link()
        info = {"codec": best.codec, "bitrate": best.bitrate_in_kbps, "preview": best.preview,
                "options": [f"{i.codec}/{i.bitrate_in_kbps}{'/preview' if i.preview else ''}" for i in infos]}
        with self.lock:
            self.cache[track_id] = (url, info, time.time())
        return url, info


# ------------------------------------------------------------------ 1. probe
def cmd_probe(args):
    client, acc = connect()
    cat = Catalog(ROOT / "catalog" / f"{args.catalog}.sqlite")
    audio = Audio(client)
    print(f"Подключено, Плюс: {acc['has_plus']}. Исполнителей с рок-треками: {len(cat.artists)}")
    picks = [cat.pick() for _ in range(args.n)]
    meta = {str(t.id): t for t in (client.tracks([p["id"] for p in picks]) or [])}
    rows = []
    for i, p in enumerate(picks, 1):
        t = meta.get(str(p["id"]))
        row = {**p, "available": getattr(t, "available", None),
               "available_for_premium": getattr(t, "available_for_premium_users", None),
               "api_duration_ms": getattr(t, "duration_ms", None)}
        t0 = time.time()
        try:
            url, info = audio.resolve(p["id"])
            row.update(info, resolve_s=round(time.time() - t0, 2))
            t1 = time.time()
            r = audio.http.get(url, headers={"Range": "bytes=0-65535"}, timeout=15)
            row.update(http=r.status_code, first_bytes_s=round(time.time() - t1, 2),
                       content_type=r.headers.get("Content-Type"), accept_ranges=r.headers.get("Accept-Ranges"),
                       size=int(r.headers.get("Content-Range", "/0").split("/")[-1] or 0),
                       mp3_header=r.content[:3] in (b"ID3",) or r.content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
            # грубая оценка длительности по размеру и битрейту — отличает 30-с превью от полного трека
            if row["size"] and row["bitrate"]:
                row["est_duration_s"] = round(row["size"] * 8 / (row["bitrate"] * 1000))
            mid = row["size"] // 2 if row.get("size") else 0
            if mid:       # перемотка = запрос из середины файла
                r2 = audio.http.get(url, headers={"Range": f"bytes={mid}-{mid + 1023}"}, timeout=15)
                row["seek_http"] = r2.status_code
            row["host"] = urlparse(url).hostname
        except Exception as e:
            row["error"] = redact(f"{type(e).__name__}: {str(e)[:150]}")
        rows.append(row)
        status = row.get("error") or f"{row.get('codec')}/{row.get('bitrate')} HTTP {row.get('http')} " \
                                     f"{row.get('est_duration_s', '?')} c (в каталоге {(p['duration_ms'] or 0) // 1000} c)"
        print(f"{i:3}. {p['artists'][:30]:30} — {p['title'][:35]:35} {status}")
        time.sleep(0.4)

    expiry = []
    if args.expiry:
        ok = [r for r in rows if r.get("http") in (200, 206)]
        if ok:
            url, _ = audio.resolve(ok[0]["id"], fresh=True)
            t0 = time.time()
            for wait in (60, 180, 360):
                time.sleep(max(0, t0 + wait - time.time()))
                code = audio.http.get(url, headers={"Range": "bytes=0-1023"}, timeout=15).status_code
                expiry.append({"after_s": wait, "http": code})
                print(f"Ссылка через {wait} c: HTTP {code}")

    good = [r for r in rows if r.get("http") in (200, 206)]
    full = [r for r in good if r.get("est_duration_s", 0) >= 0.8 * ((r["duration_ms"] or 1) / 1000)]
    summary = {"when": datetime.now().isoformat(timespec="seconds"), "has_plus": acc["has_plus"],
               "checked": len(rows), "playable": len(good), "full_length": len(full),
               "errors": [r["error"] for r in rows if "error" in r],
               "codecs": sorted({r.get("codec") for r in good}), "bitrates": sorted({r.get("bitrate") for r in good}),
               "options_seen": sorted({o for r in rows for o in r.get("options", [])}),
               "seek_ok": sum(1 for r in good if r.get("seek_http") == 206),
               "avg_resolve_s": round(sum(r["resolve_s"] for r in good) / max(1, len(good)), 2),
               "avg_first_bytes_s": round(sum(r["first_bytes_s"] for r in good) / max(1, len(good)), 2),
               "hosts": sorted({r.get("host") for r in good}), "link_expiry": expiry, "tracks": rows}
    (OUT / "probe.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), "utf-8")
    print(f"\nВоспроизводимы: {len(good)}/{len(rows)}, полной длины: {len(full)}, перемотка: {summary['seek_ok']}, "
          f"кодеки: {summary['codecs']} {summary['bitrates']} кбит/с")
    print(f"Ссылка: {summary['avg_resolve_s']} c, первые байты: {summary['avg_first_bytes_s']} c. "
          f"Файл: {OUT / 'probe.json'}")


# ------------------------------------------------------------------ 2. player
PAGE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>DeepWave — тест воспроизведения</title>
<style>
:root{color-scheme:light dark;--bg:#f5f5f7;--card:#fff;--fg:#1d1d1f;--mut:#86868b;--acc:#0a84ff}
@media(prefers-color-scheme:dark){:root{--bg:#1c1c1e;--card:#2c2c2e;--fg:#f5f5f7;--mut:#98989d}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px -apple-system,"Segoe UI",system-ui,sans-serif;
display:flex;justify-content:center;padding:40px 16px}
.card{background:var(--card);border-radius:18px;box-shadow:0 8px 30px #0002;padding:24px;width:min(560px,100%)}
h1{font-size:13px;color:var(--mut);font-weight:600;margin:0 0 16px;text-transform:uppercase;letter-spacing:.06em}
.t{font-size:22px;font-weight:650;margin:0}.a{color:var(--mut);margin:4px 0 16px}
audio{width:100%}.row{display:flex;gap:8px;margin:14px 0}
button{flex:1;border:0;border-radius:10px;padding:10px;background:var(--acc);color:#fff;font:inherit;cursor:pointer}
button.s{background:#8883;color:var(--fg)}label{color:var(--mut);font-size:13px}
#log{font:12px ui-monospace,Consolas,monospace;color:var(--mut);max-height:220px;overflow:auto;white-space:pre-wrap}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0;font-size:12px;color:var(--mut)}
.stats b{display:block;font-size:18px;color:var(--fg)}
</style></head><body><div class="card">
<h1>Тест воспроизведения · случайный рок</h1>
<p class="t" id="title">—</p><p class="a" id="artist">Нажмите «Старт»</p>
<audio id="a0" controls preload="auto"></audio><audio id="a1" preload="auto" hidden></audio>
<div class="row"><button id="start">Старт</button><button class="s" id="next">Следующий ⏭</button></div>
<label><input type="checkbox" id="direct"> прямые ссылки Яндекса (без локального прокси)</label>
<div class="stats"><div><b id="sPlayed">0</b>сыграно</div><div><b id="sErr">0</b>ошибок</div>
<div><b id="sStart">–</b>старт, с</div><div><b id="sGap">–</b>пауза, с</div></div>
<div id="log"></div></div>
<script>
const $=id=>document.getElementById(id);let cur=$('a0'),nxt=$('a1'),curT=null,nxtT=null,played=0,errs=0,seqErr=0,busy=false,starts=[],gaps=[],endedAt=0;
function log(m,ev){const l=$('log');l.textContent=new Date().toLocaleTimeString()+' '+m+'\n'+l.textContent;
 fetch('/api/log',{method:'POST',body:JSON.stringify({ev:ev||'info',msg:m,track:curT&&curT.id})})}
async function getNext(){const r=await fetch('/api/next?mode='+($('direct').checked?'direct':'proxy'));return r.json()}
async function preload(){try{nxtT=await getNext();nxt.src=nxtT.src;nxt.load();}catch(e){log('ошибка выбора: '+e,'error')}}
function show(t){$('title').textContent=t.title;$('artist').textContent=t.artists+' · '+(t.album||'')+(t.year?' · '+t.year:'');
 document.title=t.artists+' — '+t.title;
 if('mediaSession'in navigator)navigator.mediaSession.metadata=new MediaMetadata({title:t.title,artist:t.artists,album:t.album||''})}
async function playNext(){
 if(busy)return;busy=true;try{await doNext()}finally{busy=false}}
async function doNext(){
 for(let i=0;i<5;i++){if(nxtT)break;await preload()}
 if(!nxtT){log('не удалось получить трек','fatal');return}
 const t0=performance.now();[cur,nxt]=[nxt,cur];curT=nxtT;nxtT=null;
 nxt.pause();nxt.hidden=true;nxt.controls=false;cur.hidden=false;cur.controls=true;show(curT);
 try{await cur.play();const s=(performance.now()-t0)/1000;starts.push(s);$('sStart').textContent=(starts.reduce((a,b)=>a+b)/starts.length).toFixed(2);
  if(endedAt){gaps.push((performance.now()-endedAt)/1000);$('sGap').textContent=(gaps.reduce((a,b)=>a+b)/gaps.length).toFixed(2);endedAt=0}
  log('▶ '+curT.artists+' — '+curT.title+' (старт '+s.toFixed(2)+' с)','play')}
 catch(e){errs++;seqErr++;$('sErr').textContent=errs;log('не воспроизводится: '+e.message,'error');
  if(seqErr>=5){log('5 ошибок подряд — остановка','fatal');return}return doNext()}
 seqErr=0;preload()}
for(const el of [$('a0'),$('a1')]){
 el.addEventListener('ended',()=>{if(el!==cur)return;played++;$('sPlayed').textContent=played;endedAt=performance.now();log('⏹ дослушан','ended');playNext()});
 el.addEventListener('error',()=>{if(el!==cur||busy)return;errs++;$('sErr').textContent=errs;log('ошибка аудио: код '+(el.error&&el.error.code),'error');playNext()});
 el.addEventListener('stalled',()=>{if(el===cur)log('stalled на '+el.currentTime.toFixed(1)+' с','stall')});
 el.addEventListener('waiting',()=>{if(el===cur)log('буферизация на '+el.currentTime.toFixed(1)+' с','waiting')});
 el.addEventListener('seeked',()=>{if(el===cur)log('перемотка → '+el.currentTime.toFixed(1)+' с','seek')});
 el.addEventListener('pause',()=>{if(el===cur&&!el.ended)log('пауза','pause')});}
$('start').onclick=()=>{$('start').disabled=true;playNext()};$('next').onclick=()=>{log('⏭ пропуск','skip');playNext()};
if('mediaSession'in navigator)navigator.mediaSession.setActionHandler('nexttrack',()=>playNext());
</script></body></html>"""


def cmd_player(args):
    client, acc = connect()
    cat = Catalog(ROOT / "catalog" / f"{args.catalog}.sqlite")
    audio = Audio(client)
    logf = open(OUT / "player_log.jsonl", "a", encoding="utf-8")
    loglock = threading.Lock()

    def jlog(rec):
        with loglock:
            logf.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")
            logf.flush()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                b = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path == "/api/next":
                mode = parse_qs(u.query).get("mode", ["proxy"])[0]
                for _ in range(5):
                    t = cat.pick()
                    try:
                        url, info = audio.resolve(t["id"])
                    except Exception as e:
                        jlog({"ev": "resolve_error", "track": t["id"], "err": redact(str(e)[:150])})
                        continue
                    t.update(info, src=url if mode == "direct" else f"/stream/{t['id']}", mode=mode)
                    jlog({"ev": "next", "track": t["id"], "mode": mode, "codec": info["codec"],
                          "bitrate": info["bitrate"], "artists": t["artists"], "title": t["title"]})
                    return self._json(t)
                self._json({"error": "не удалось получить трек"}, 502)
            elif u.path.startswith("/stream/"):
                self.stream(u.path.split("/")[-1])
            else:
                self.send_error(404)

        def stream(self, tid):
            """Прокси с поддержкой Range: свежая ссылка при каждом обращении, повтор при 403/410."""
            hdr = {"Range": self.headers["Range"]} if self.headers.get("Range") else {}
            for attempt in (0, 1):
                try:
                    url, _ = audio.resolve(tid, fresh=attempt == 1)
                    up = audio.http.get(url, headers=hdr, stream=True, timeout=20)
                except Exception as e:
                    jlog({"ev": "proxy_error", "track": tid, "err": redact(str(e)[:150])})
                    return self.send_error(502)
                if up.status_code in (403, 404, 410) and attempt == 0:
                    jlog({"ev": "link_expired", "track": tid, "http": up.status_code})
                    continue
                break
            self.send_response(up.status_code)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if up.headers.get(h):
                    self.send_header(h, up.headers[h])
            if "Accept-Ranges" not in up.headers:
                self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            try:
                for chunk in up.iter_content(64 * 1024):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass                     # браузер оборвал поток (перемотка/переключение) — это нормально
            finally:
                up.close()

        def do_POST(self):
            if self.path == "/api/log":
                n = int(self.headers.get("Content-Length", 0))
                try:
                    jlog({"src": "page", **json.loads(self.rfile.read(n) or b"{}")})
                except Exception:
                    pass
                self.send_response(204)
                self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Плюс: {acc['has_plus']}. Исполнителей: {len(cat.artists)}. Проигрыватель: {url}")
    print("Закройте вкладку и нажмите Ctrl+C для выхода. Лог: results_play/player_log.jsonl")
    jlog({"ev": "session_start", "has_plus": acc["has_plus"], "artists": len(cat.artists)})
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        jlog({"ev": "session_end"})
        srv.server_close()
        print("Остановлено.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["probe", "player"])
    p.add_argument("--catalog", default="allrock")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--expiry", action="store_true", help="проверить срок жизни прямой ссылки (+6 мин)")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    import logging
    logging.getLogger("yandex_music").setLevel(logging.ERROR)
    {"probe": cmd_probe, "player": cmd_player}[a.command](a)


if __name__ == "__main__":
    main()
