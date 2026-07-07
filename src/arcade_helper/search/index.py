"""Search index data structures and document building."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from arcade_helper.core.song import SongData

from .normalization import (
    _ascii_tokens,
    _get_pinyin,
    _is_romaji_alias,
    _normalize_for_matching,
    _search_terms,
    _unique_texts,
    _weighted_terms,
)


@dataclass(frozen=True)
class _SearchDocument:
    song_id: int
    title: str
    aliases: tuple[str, ...]
    artist: str
    normalized_title: str
    normalized_aliases: tuple[str, ...]
    ascii_tokens: tuple[str, ...]
    pinyin_initials: str
    pinyin_full: str
    romaji_aliases: tuple[str, ...]
    source: str
    confidence: float
    bm25_terms: tuple[str, ...]
    bm25_counts: Counter[str]
    bm25_len: int


@dataclass(frozen=True)
class _SearchIndex:
    index: dict[int, str]
    alias_choices_norm: dict[str, tuple[int, str]]
    alias_freq: Counter[str]
    title_choices_norm: dict[str, tuple[tuple[int, str], ...]]
    title_pinyin_initials_norm: dict[str, tuple[tuple[int, str], ...]]
    title_full_pinyin_norm: dict[str, tuple[int, str]]
    alias_pinyin_initials_norm: dict[str, tuple[tuple[int, str], ...]]
    alias_full_pinyin_norm: dict[str, tuple[int, str]]
    romaji_alias_choices_norm: dict[str, tuple[tuple[int, str], ...]]
    simplified_title_choices: dict[str, tuple[int, str]]
    simplified_alias_choices: dict[str, tuple[int, str]]
    documents: dict[int, _SearchDocument]
    bm25_doc_freq: Counter[str]
    bm25_avg_len: float


def _build_search_document(
    song_id: int,
    song_data: SongData | None,
    title: str,
    aliases: tuple[str, ...],
) -> _SearchDocument:
    artist = str(getattr(song_data, "artist", "") or "")
    normalized_title = _normalize_for_matching(title)
    normalized_aliases = tuple(_normalize_for_matching(alias) for alias in aliases)
    initials, full_py = _get_pinyin(" ".join([title, *aliases]))
    pinyin_initials = _normalize_for_matching(initials)
    pinyin_full = _normalize_for_matching(full_py)
    romaji_aliases = tuple(alias for alias in aliases if _is_romaji_alias(alias, title, aliases))
    ascii_tokens = tuple(_unique_texts(_ascii_tokens(" ".join([title, artist, *aliases]))))
    bm25_terms = _weighted_terms(
        (3, _search_terms(title)),
        (2, [term for alias in aliases for term in _search_terms(alias)]),
        (1, _search_terms(artist)),
        (1, _search_terms(pinyin_initials)),
        (1, _search_terms(pinyin_full)),
        (1, [term for alias in romaji_aliases for term in _search_terms(alias)]),
    )
    return _SearchDocument(
        song_id=song_id,
        title=title,
        aliases=aliases,
        artist=artist,
        normalized_title=normalized_title,
        normalized_aliases=normalized_aliases,
        ascii_tokens=ascii_tokens,
        pinyin_initials=pinyin_initials,
        pinyin_full=pinyin_full,
        romaji_aliases=romaji_aliases,
        source="catalog",
        confidence=1.0,
        bm25_terms=bm25_terms,
        bm25_counts=Counter(bm25_terms),
        bm25_len=len(bm25_terms),
    )
