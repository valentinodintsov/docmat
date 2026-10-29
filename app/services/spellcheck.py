"""Поиск орфографических ошибок по словарям Hunspell (через Enchant)."""

import re
from dataclasses import asdict, dataclass
from functools import lru_cache

import enchant

from app.config import settings

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:[-'’][A-Za-zА-Яа-яЁё]+)*")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")
MAX_SUGGESTIONS = 5


@dataclass
class SpellError:
    start: int  # смещения в единицах UTF-16 — так их понимает JavaScript
    end: int
    word: str
    suggestions: list[str]


@lru_cache(maxsize=1)
def _dictionaries() -> dict[str, enchant.Dict]:
    return {lang: enchant.Dict(lang) for lang in settings.spell_langs if enchant.dict_exists(lang)}


def _dict_for(word: str) -> enchant.Dict | None:
    prefix = "ru" if _CYRILLIC.search(word) else "en"
    return next((d for lang, d in _dictionaries().items() if lang.startswith(prefix)), None)


def _is_mixed_script(word: str) -> bool:
    # «Пpивeт» с латинскими p и e — типичная ошибка OCR, словарь её не поймает.
    return bool(_CYRILLIC.search(word) and _LATIN.search(word))


@lru_cache(maxsize=50_000)
def check_word(word: str) -> bool:
    if _is_mixed_script(word):
        return False
    if len(word) < 2 or (word.isupper() and len(word) <= 5):
        return True  # однобуквенные слова и аббревиатуры не проверяются
    d = _dict_for(word)
    if d is None:
        return True
    variants = {word, word.lower(), word.capitalize(), word.replace("’", "'")}
    if any(d.check(v) for v in variants):
        return True
    parts = re.split(r"[-'’]", word)
    return len(parts) > 1 and all(check_word(p) for p in parts if p)


@lru_cache(maxsize=10_000)
def suggest(word: str) -> tuple[str, ...]:
    if _is_mixed_script(word):
        # Заменяем похожие латинские буквы на кириллические.
        fixed = word.translate(str.maketrans("aceopxyAEKMHOPCTXB", "асеорхуАЕКМНОРСТХВ"))
        return (fixed,) if not _is_mixed_script(fixed) else ()
    d = _dict_for(word)
    return tuple(d.suggest(word)[:MAX_SUGGESTIONS]) if d else ()


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def find_errors(text: str) -> list[SpellError]:
    errors = []
    pos, offset16 = 0, 0
    for m in WORD_RE.finditer(text):
        offset16 += _utf16_len(text[pos : m.start()])
        word = m.group()
        length16 = _utf16_len(word)
        if not check_word(word):
            errors.append(SpellError(offset16, offset16 + length16, word, list(suggest(word))))
        offset16 += length16
        pos = m.end()
    return errors


def find_errors_json(text: str) -> list[dict]:
    return [asdict(e) for e in find_errors(text)]


def match_case(template: str, word: str) -> str:
    if template.isupper():
        return word.upper()
    if template[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def edit_distance(a: str, b: str) -> int:
    """Расстояние Дамерау — Левенштейна (с перестановкой соседних букв)."""
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def safe_replacement(word: str) -> str | None:
    """Вариант, который можно подставить без участия человека, или None.

    Слова с заглавной буквой в середине («РосМа», «iPhone») обычно оказываются
    названиями или искажениями латиницы — словарь их только испортит.
    Короткие слова допускают одну правку, длинные — две.
    """
    variants = suggest(word)
    if not variants:
        return None
    if _is_mixed_script(word):
        return variants[0]
    if any(c.isupper() for c in word[1:]) and not word.isupper():
        return None
    best = variants[0]
    if " " in best:
        return None  # «доклад чик» вместо «докладчик» — словарь разбил слово
    limit = 1 if len(word) <= 5 else 2
    return best if edit_distance(word.lower(), best.lower()) <= limit else None


def autocorrect(text: str) -> str:
    """Заменяет ошибочные слова надёжными вариантами из словаря; сомнительные оставляет."""
    out, pos = [], 0
    for m in WORD_RE.finditer(text):
        word = m.group()
        if not check_word(word) and (replacement := safe_replacement(word)):
            out.append(text[pos : m.start()])
            out.append(match_case(word, replacement))
            pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def is_available() -> bool:
    return bool(_dictionaries())
