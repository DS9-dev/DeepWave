"""
Исследовательский скрипт: подключение к Яндекс Музыке через yandex-music
и получение треков жанра Rock.

Запуск:  python rock_probe.py            (обычный прогон)
         python rock_probe.py --logout   (удалить сохранённый токен)

Токен:
  * берётся из переменной окружения YANDEX_MUSIC_TOKEN, если она задана;
  * иначе из файла ~/.yandex_music_token.json (вне папки проекта);
  * иначе запускается OAuth Device Flow: вы открываете страницу Яндекса
    в своём браузере, вводите короткий код и подтверждаете вход.
    Логин и пароль вводятся только на сайте Яндекса, скрипт их не видит.
  Токен никогда не печатается и не пишется в лог/результаты.
"""
import csv
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import yandex_music
    from yandex_music import Client
except ImportError:
    sys.exit("Не установлена библиотека: выполните  pip install -U yandex-music")

GENRE_ID = "rock"
TARGET_TRACKS = 50
TOKEN_FILE = Path.home() / ".yandex_music_token.json"
OUT_DIR = Path(__file__).resolve().parent / "results"

report = {"started": datetime.now().isoformat(timespec="seconds"),
          "library_version": getattr(yandex_music, "__version__", "?"),
          "python": sys.version.split()[0], "steps": {}, "errors": []}
_secrets = set()


class RedactFilter(logging.Filter):
    """Вырезает токены из любых сообщений лога (на всякий случай)."""
    def filter(self, record):
        msg = record.getMessage()
        for s in _secrets:
            if s and s in msg:
                record.msg, record.args = msg.replace(s, "***"), ()
        return True


OUT_DIR.mkdir(exist_ok=True)
log = logging.getLogger("probe")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.FileHandler(OUT_DIR / "run_log.txt", "w", "utf-8"),
                              logging.StreamHandler(sys.stdout)])
for h in logging.getLogger().handlers:
    h.addFilter(RedactFilter())
log.setLevel(logging.INFO)


def err(step, e):
    text = f"{type(e).__name__}: {e}"
    for s in _secrets:
        text = text.replace(s, "***") if s else text
    report["errors"].append({"step": step, "error": text})
    log.warning("[%s] ошибка: %s", step, text)


# ---------- 1-3. Авторизация ----------
def load_token():
    env = os.environ.get("YANDEX_MUSIC_TOKEN")
    if env:
        return env, "env"
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text("utf-8"))["access_token"], "file"
        except Exception as e:
            err("load_token", e)
    return None, None


def device_flow():
    client = Client()

    def on_code(code):
        print("\n=== Требуется вход в Яндекс ===")
        print(f"1. Откройте в браузере: {code.verification_url}")
        print(f"2. Введите код: {code.user_code}")
        print("3. Подтвердите доступ. Скрипт ждёт подтверждения...\n")

    tok = client.device_auth(on_code=on_code)
    _secrets.update({tok.access_token, getattr(tok, "refresh_token", None)})
    data = {"access_token": tok.access_token,
            "refresh_token": getattr(tok, "refresh_token", None),
            "expires_in": getattr(tok, "expires_in", None),
            "obtained_at": int(time.time())}
    TOKEN_FILE.write_text(json.dumps(data), "utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    log.info("Токен получен и сохранён в %s (значение не выводится)", TOKEN_FILE)
    return tok.access_token


def connect():
    token, src = load_token()
    if not token:
        token, src = device_flow(), "device_flow"
    _secrets.add(token)
    client = Client(token).init()
    st = client.me
    acc = st.account
    plus = getattr(getattr(st, "plus", None), "has_plus", None)
    info = {"token_source": src, "uid": acc.uid, "login": getattr(acc, "login", None),
            "display_name": getattr(acc, "display_name", None), "has_plus": plus}
    report["steps"]["auth"] = info
    log.info("Подключено: %s (uid %s), Плюс: %s", info["login"] or info["display_name"], acc.uid, plus)
    return client


# ---------- 4. Жанры ----------
def find_genre(client):
    genres = client.genres()
    rock = next((g for g in genres if g.id == GENRE_ID), None)
    if rock is None:  # вдруг rock вложен
        for g in genres:
            rock = next((s for s in g.sub_genres or [] if s.id == GENRE_ID), None)
            if rock:
                break

    def ids(g):
        out = {g.id}
        for s in g.sub_genres or []:
            out |= ids(s)
        return out

    family = ids(rock) if rock else {GENRE_ID}
    report["steps"]["genres"] = {
        "total_top_level": len(genres),
        "all_ids": [g.id for g in genres],
        "rock_found": rock is not None,
        "rock_title": rock.title if rock else None,
        "rock_subgenres": sorted(family - {GENRE_ID}),
        "rock_has_radio_icon": bool(rock and rock.radio_icon),
    }
    log.info("Жанров верхнего уровня: %d; Rock найден: %s; поджанры: %s",
             len(genres), rock is not None, ", ".join(sorted(family - {GENRE_ID})) or "-")
    return family


# ---------- 5. Треки ----------
def track_row(t, source):
    albums = t.albums or []
    artists = t.artists or []
    return {"source": source, "track_id": str(t.id), "title": t.title,
            "artists": ", ".join(a.name for a in artists if a.name),
            "artist_ids": ",".join(str(a.id) for a in artists),
            "album_id": str(albums[0].id) if albums else "",
            "album_title": albums[0].title if albums else "",
            "album_genre": (albums[0].genre or "") if albums else "",
            "artist_genres": ",".join(sorted({g for a in artists for g in (a.genres or [])})),
            "year": (albums[0].year or "") if albums else ""}


def from_radio(client, limit):
    """Станция «Моя волна»-радио по жанру: genre:rock."""
    rows, seen, queue, empty = [], set(), None, 0
    for _ in range(25):
        res = client.rotor_station_tracks(f"genre:{GENRE_ID}", queue=queue)
        new = 0
        for item in (res.sequence or []):
            t = item.track
            if t and str(t.id) not in seen:
                seen.add(str(t.id)); rows.append(track_row(t, "radio genre:rock")); new += 1
        if not res.sequence:
            break
        queue = res.sequence[-1].track.id
        empty = empty + 1 if new == 0 else 0
        if len(rows) >= limit or empty >= 3:
            break
        time.sleep(0.3)
    return rows[:limit]


def from_metatag(client, limit, per_album=3):
    """Жанровая страница (metatag 'rock'): популярные альбомы -> их треки."""
    res = client.metatag_albums(GENRE_ID, sort_by="popular", limit=25)
    albums = getattr(res, "albums", None) or []
    rows = []
    for a in albums:
        full = client.albums_with_tracks(a.id)
        tracks = [t for vol in (full.volumes or []) for t in vol][:per_album]
        rows += [track_row(t, "metatag rock / popular albums") for t in tracks]
        if len(rows) >= limit:
            break
        time.sleep(0.2)
    return rows[:limit]


def from_text_search(client, limit=20):
    """Для сравнения: обычный текстовый поиск слова 'rock'."""
    res = client.search("rock", type_="track")
    tracks = res.tracks.results if res and res.tracks else []
    return [track_row(t, "text search 'rock'") for t in tracks[:limit]]


# ---------- 6. Проверка жанра ----------
def verify(rows, family):
    if not rows:
        return {}
    by_album = sum(1 for r in rows if r["album_genre"] in family)
    by_artist = sum(1 for r in rows if set(r["artist_genres"].split(",")) & family)
    either = sum(1 for r in rows if r["album_genre"] in family
                 or set(r["artist_genres"].split(",")) & family)
    return {"tracks": len(rows),
            "album_genre_in_rock_family": by_album,
            "artist_genre_in_rock_family": by_artist,
            "rock_by_album_or_artist": either,
            "share_rock_%": round(100 * either / len(rows), 1),
            "album_genre_distribution": dict(Counter(r["album_genre"] or "(пусто)" for r in rows).most_common())}


def main():
    if "--logout" in sys.argv:
        TOKEN_FILE.unlink(missing_ok=True)
        print("Сохранённый токен удалён.")
        return
    try:
        client = connect()
    except Exception as e:
        err("auth", e)
        print("Не удалось подключиться. Если токен устарел: python rock_probe.py --logout")
        return finish([])
    try:
        family = find_genre(client)
    except Exception as e:
        err("genres", e); family = {GENRE_ID}

    all_rows = []
    for name, fn, args in [("radio", from_radio, (TARGET_TRACKS,)),
                           ("metatag", from_metatag, (TARGET_TRACKS,)),
                           ("text_search", from_text_search, ())]:
        t0 = time.time()
        try:
            rows = fn(client, *args)
            all_rows += rows
            report["steps"][name] = {"ok": True, "seconds": round(time.time() - t0, 1),
                                     "verification": verify(rows, family)}
            log.info("%s: получено %d треков", name, len(rows))
        except Exception as e:
            err(name, e)
            report["steps"][name] = {"ok": False}
    finish(all_rows)


def finish(rows):
    report["finished"] = datetime.now().isoformat(timespec="seconds")
    (OUT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    (OUT_DIR / "rock_tracks.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
    if rows:
        with open(OUT_DIR / "rock_tracks.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
            w.writeheader(); w.writerows(rows)
    print("\n=== Итог ===")
    for k, v in report["steps"].items():
        if isinstance(v, dict) and "verification" in v:
            ver = v["verification"]
            print(f"{k:12} треков: {ver.get('tracks', 0):3}  доля рока: {ver.get('share_rock_%', '-')}%")
    print(f"Ошибок: {len(report['errors'])}. Результаты: {OUT_DIR}")
    for r in [r for r in rows if r["source"].startswith("radio")][:10]:
        print(f"  {r['track_id']:>10}  {r['artists']} — {r['title']}  [{r['album_genre']}]")


if __name__ == "__main__":
    main()
