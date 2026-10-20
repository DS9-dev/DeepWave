"""Общая авторизация DeepWave, crawler.py и исследовательских скриптов.

Источники токена по порядку:
  1. переменная окружения YANDEX_MUSIC_TOKEN;
  2. ~/.yandex_music_token.dpapi — токен, зашифрованный Windows DPAPI (расшифровать
     может только ваша учётная запись Windows на этом компьютере);
  3. старый ~/.yandex_music_token.json — на Windows автоматически переносится в DPAPI и удаляется;
  4. OAuth Device Flow (код вводится на сайте Яндекса).
Токен никогда не выводится.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

from yandex_music import Client

LEGACY_FILE = Path.home() / ".yandex_music_token.json"
SECURE_FILE = Path.home() / ".yandex_music_token.dpapi"
TOKEN_FILE = LEGACY_FILE            # совместимость со старыми скриптами
SECRETS = set()
IS_WIN = sys.platform == "win32"


class RedactFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        for s in SECRETS:
            if s and s in msg:
                record.msg, record.args = msg.replace(s, "***"), ()
                msg = record.msg
        return True


def redact(text):
    for s in SECRETS:
        if s:
            text = text.replace(s, "***")
    return text


# ------------------------------------------------------------------ DPAPI (Windows)
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    desc = ctypes.c_wchar_p("music-app") if protect else None
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    if not fn(ctypes.byref(src), desc, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError("DPAPI: не удалось обработать токен")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def save_token(data: dict):
    raw = json.dumps(data).encode()
    if IS_WIN:
        try:
            enc = _dpapi(raw, True)
            assert _dpapi(enc, False) == raw          # проверка: расшифровывается обратно
            SECURE_FILE.write_bytes(enc)
            LEGACY_FILE.unlink(missing_ok=True)
            return
        except Exception as e:                     # DPAPI недоступен — не ломаем вход
            logging.getLogger("deepwave").warning("DPAPI недоступен (%s), токен сохранён в файл", type(e).__name__)
    if not IS_WIN or not SECURE_FILE.exists():  # файл с правами только владельца
        LEGACY_FILE.write_bytes(raw)
        try:
            os.chmod(LEGACY_FILE, 0o600)
        except OSError:
            pass


def _load():
    env = os.environ.get("YANDEX_MUSIC_TOKEN")
    if env:
        return env, "env"
    if IS_WIN and SECURE_FILE.exists():
        try:
            return json.loads(_dpapi(SECURE_FILE.read_bytes(), False))["access_token"], "dpapi"
        except Exception:
            pass
    if LEGACY_FILE.exists():
        data = json.loads(LEGACY_FILE.read_text("utf-8"))
        if IS_WIN:
            try:
                save_token(data)            # перенос в зашифрованное хранилище
                return data["access_token"], "dpapi"
            except Exception:
                pass
        return data["access_token"], "file"
    return None, None


def has_token():
    return _load()[0] is not None


def forget_token():
    SECURE_FILE.unlink(missing_ok=True)
    LEGACY_FILE.unlink(missing_ok=True)


def device_flow(on_code, should_cancel=None):
    """Вход через код на сайте Яндекса. on_code(code) показывает пользователю адрес и код."""
    tok = Client().device_auth(on_code=on_code, should_cancel=should_cancel)
    SECRETS.update({tok.access_token, getattr(tok, "refresh_token", None)})
    save_token({"access_token": tok.access_token, "refresh_token": getattr(tok, "refresh_token", None),
                "expires_in": getattr(tok, "expires_in", None), "obtained_at": int(time.time())})
    return tok.access_token


def _console_flow():
    def on_code(code):
        print("\n=== Требуется вход в Яндекс ===")
        print(f"1. Откройте в браузере: {code.verification_url}")
        print(f"2. Введите код: {code.user_code}")
        print("3. Подтвердите доступ. Скрипт ждёт...\n")
    return device_flow(on_code)


def connect(interactive=True):
    """Возвращает (client, info) — info без токена."""
    token, src = _load()
    if not token:
        if not interactive:
            raise PermissionError("нет токена")
        token, src = _console_flow(), "device_flow"
    SECRETS.add(token)
    client = Client(token).init()
    acc = client.me.account
    info = {"token_source": src, "uid": acc.uid, "login": getattr(acc, "login", None),
            "name": getattr(acc, "display_name", None) or getattr(acc, "login", None),
            "has_plus": getattr(getattr(client.me, "plus", None), "has_plus", None)}
    return client, info


def new_client():
    """Отдельный клиент (например, для фонового сбора в своём потоке)."""
    token, _ = _load()
    if not token:
        raise PermissionError("нет токена")
    SECRETS.add(token)
    return Client(token).init()
