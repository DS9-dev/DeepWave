"""Письменности в названиях треков (для фильтра «Письменность названий треков»).

Учитываются только буквы (общая категория Unicode L*): цифры, пробелы, знаки препинания, символы, эмодзи
и комбинируемые знаки сами по себе ничего не определяют. Название приводится к NFC, поэтому разложенные
формы («и» + кратка) и составные дают одно и то же.

Список расширяемый: достаточно добавить запись в SCRIPTS (id, название, пример, диапазоны). Буквы, не
попавшие ни в один диапазон, относятся к «Прочим письменностям». Порядок и id не менять — по ним хранятся
настройки, а биты масок вычисляются заново при каждом построении индекса.
"""
import unicodedata

# (id, название для пользователя, пример, диапазоны кодовых точек включительно)
SCRIPTS = [
    ("cyrillic", "Кириллица", "Аа", [(0x0400, 0x052F), (0x1C80, 0x1C8F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F),
                                      (0x1E030, 0x1E08F)]),
    ("latin", "Латиница", "Aa", [(0x0041, 0x005A), (0x0061, 0x007A), (0x00AA, 0x00AA), (0x00BA, 0x00BA),
                                  (0x00C0, 0x024F), (0x0250, 0x02AF), (0x1D00, 0x1DBF), (0x1E00, 0x1EFF),
                                  (0x2C60, 0x2C7F), (0xA720, 0xA7FF), (0xAB30, 0xAB6F), (0xFB00, 0xFB06),
                                  (0xFF21, 0xFF3A), (0xFF41, 0xFF5A)]),
    ("greek", "Греческая", "Αα", [(0x0370, 0x03FF), (0x1F00, 0x1FFF)]),
    ("arabic", "Арабская", "ع", [(0x0600, 0x06FF), (0x0750, 0x077F), (0x0870, 0x08FF), (0xFB50, 0xFDFF),
                                  (0xFE70, 0xFEFF)]),
    ("hebrew", "Иврит", "א", [(0x0590, 0x05FF), (0xFB1D, 0xFB4F)]),
    ("han", "Китайские иероглифы", "漢", [(0x2E80, 0x2FDF), (0x3005, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B),
                                           (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x323AF)]),
    ("kana", "Японская (кана)", "かカ", [(0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F),
                                          (0x1B000, 0x1B16F)]),
    ("hangul", "Корейская", "한", [(0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7FF),
                                    (0xFFA0, 0xFFDC)]),
    ("armenian", "Армянская", "Աա", [(0x0530, 0x058F), (0xFB13, 0xFB17)]),
    ("georgian", "Грузинская", "ა", [(0x10A0, 0x10FF), (0x1C90, 0x1CBF), (0x2D00, 0x2D2F)]),
    ("indic", "Индийские", "क", [(0x0900, 0x0DFF), (0xA8E0, 0xA8FF), (0x11000, 0x111FF)]),
    ("thai", "Тайская и лаосская", "ก", [(0x0E00, 0x0EFF)]),
    ("other", "Прочие письменности", "", []),
]
IDS = [s[0] for s in SCRIPTS]
BIT = {sid: 1 << i for i, sid in enumerate(IDS)}
LATIN, OTHER = BIT["latin"], BIT["other"]

_RANGES = sorted((lo, hi, BIT[sid]) for sid, _, _, rr in SCRIPTS for lo, hi in rr)
_cache = {}


def _char_bit(ch):
    b = _cache.get(ch)
    if b is None:
        b = 0
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            cp = ord(ch)
            for lo, hi, bit in _RANGES:
                if lo <= cp <= hi:
                    b = bit
                    break
            else:
                # знаки-модификаторы вне диапазонов (ʼ, ˈ, ᵒ…) общие для многих письменностей — не учитываются
                b = 0 if cat == "Lm" else OTHER
        _cache[ch] = b
    return b


def mask(text):
    """Битовая маска письменностей, буквы которых есть в тексте (0 — букв нет)."""
    if not text:
        return 0
    if text.isascii():                       # быстрый путь: большинство названий
        return LATIN if any(c.isalpha() for c in text) else 0
    m = 0
    for ch in unicodedata.normalize("NFC", text):
        m |= _char_bit(ch)
    return m


def names(m):
    return [sid for sid in IDS if m & BIT[sid]]


def exclusion_mask(ids):
    m = 0
    for sid in ids or []:
        m |= BIT.get(sid, 0)
    return m


def catalog():
    """[{id, title, sample}] — для настроек."""
    return [{"id": sid, "title": t, "sample": smp} for sid, t, smp, _ in SCRIPTS]
