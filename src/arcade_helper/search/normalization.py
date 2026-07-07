"""Text normalization helpers for song search."""

from __future__ import annotations

import re
import unicodedata

import zhconv
from pypinyin import Style, lazy_pinyin

from .constants import ASCII_STOPWORDS

GameCode = str

_pinyin_cache: dict[str, tuple[str, str]] = {}
_simplified_cache: dict[str, str] = {}
_normalized_cache: dict[str, str] = {}


def _normalize_game_code(game_code: str) -> GameCode:
    return str(game_code).strip().lower()


def _is_cjk_char(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _is_kanji(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
    )


def _has_cjk(text: str) -> bool:
    return any(_is_cjk_char(char) for char in text)


def _get_pinyin(text: str) -> tuple[str, str]:
    if text in _pinyin_cache:
        return _pinyin_cache[text]

    initials_chars: list[str] = []
    full_chars: list[str] = []

    for char in text:
        if _is_kanji(char):
            first = lazy_pinyin(char, style=Style.FIRST_LETTER)
            full = lazy_pinyin(char, style=Style.NORMAL)
            if first:
                initials_chars.append(first[0].lower())
            if full:
                full_chars.append(full[0].lower())
        elif char.isascii() and char.isalpha():
            initials_chars.append(char.lower())
            full_chars.append(char.lower())

    result = ("".join(initials_chars), "".join(full_chars))
    _pinyin_cache[text] = result
    return result


def _normalize_to_simplified(text: str) -> str:
    if text in _simplified_cache:
        return _simplified_cache[text]
    result = zhconv.convert(text, "zh-hans")
    _simplified_cache[text] = result
    return result


def _normalize_for_matching(text: str) -> str:
    if text in _normalized_cache:
        return _normalized_cache[text]

    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = _normalize_to_simplified(normalized)
    separators = {" ", "\t", "\n", "\r", "　", "·", "・", "･", "-", "_"}
    normalized = "".join(ch for ch in normalized if ch not in separators)
    _normalized_cache[text] = normalized
    return normalized


def _ascii_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = _normalize_to_simplified(normalized)
    return re.findall(r"[a-z0-9]+", normalized)


def _is_meaningful_ascii_token(token: str) -> bool:
    if len(token) <= 2:
        return False
    if token in ASCII_STOPWORDS:
        return False
    if token.isdigit():
        return False
    return True


def _meaningful_ascii_tokens(text: str) -> list[str]:
    return [token for token in _ascii_tokens(text) if _is_meaningful_ascii_token(token)]


def _first_meaningful_ascii_token(text: str) -> str | None:
    for token in _ascii_tokens(text):
        if _is_meaningful_ascii_token(token):
            return token
    return None


def _unique_texts(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return tuple(result)


def _cjk_search_terms(text: str) -> list[str]:
    normalized = _normalize_for_matching(text)
    chars = [char for char in normalized if _is_cjk_char(char)]
    if len(chars) < 2:
        return chars
    return chars + ["".join(chars[index:index + 2]) for index in range(len(chars) - 1)]


def _search_terms(text: str) -> list[str]:
    return _meaningful_ascii_tokens(text) + _cjk_search_terms(text)


def _weighted_terms(*groups: tuple[int, list[str]]) -> tuple[str, ...]:
    terms: list[str] = []
    for weight, group_terms in groups:
        for term in group_terms:
            terms.extend([term] * weight)
    return tuple(terms)


def _append_choice(
    choices: dict[str, list[tuple[int, str]]],
    key: str,
    value: tuple[int, str],
) -> None:
    if key:
        choices.setdefault(key, []).append(value)


def _is_romaji_alias(alias: str, title: str, aliases: tuple[str, ...]) -> bool:
    return (
        alias.isascii()
        and any(ch.isalpha() for ch in alias)
        and (_has_cjk(title) or any(_has_cjk(item) for item in aliases))
    )
