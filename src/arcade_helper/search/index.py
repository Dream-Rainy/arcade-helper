"""Search index data structures and document building."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from arcade_helper.core.song import SongData

from .normalization import (
    _append_choice,
    _has_cjk,
    _normalize_to_simplified,
    _get_pinyin,
    _is_romaji_alias,
    _normalize_for_matching,
    _search_terms,
    _unique_texts,
    _weighted_terms,
)


@dataclass(frozen=True, slots=True)
class _SearchDocument:
    song_id: int
    title: str
    confidence: float
    bm25_counts: dict[str, int]
    bm25_len: int


@dataclass(frozen=True, slots=True)
class _SearchIndex:
    songs: dict[int, SongData]
    index: dict[int, str]
    alias_choices_norm: dict[str, list[tuple[int, str]]]
    alias_freq: Counter[str]
    title_choices_norm: dict[str, tuple[tuple[int, str], ...]]
    title_pinyin_initials_norm: dict[str, tuple[tuple[int, str], ...]]
    title_full_pinyin_norm: dict[str, list[tuple[int, str]]]
    alias_pinyin_initials_norm: dict[str, tuple[tuple[int, str], ...]]
    alias_full_pinyin_norm: dict[str, list[tuple[int, str]]]
    romaji_alias_choices_norm: dict[str, tuple[tuple[int, str], ...]]
    simplified_title_choices: dict[str, list[tuple[int, str]]]
    simplified_alias_choices: dict[str, list[tuple[int, str]]]
    documents: dict[int, _SearchDocument]
    bm25_doc_freq: Counter[str]
    bm25_avg_len: float


def _build_search_document(
    song_id: int,
    artist: str,
    title: str,
    aliases: tuple[str, ...],
) -> tuple[_SearchDocument, tuple[str, ...]]:
    artist = str(artist or "")
    initials, full_py = _get_pinyin(" ".join([title, *aliases]))
    pinyin_initials = _normalize_for_matching(initials)
    pinyin_full = _normalize_for_matching(full_py)
    romaji_aliases = tuple(alias for alias in aliases if _is_romaji_alias(alias, title, aliases))
    bm25_terms = _weighted_terms(
        (3, _search_terms(title)),
        (2, [term for alias in aliases for term in _search_terms(alias)]),
        (1, _search_terms(artist)),
        (1, _search_terms(pinyin_initials)),
        (1, _search_terms(pinyin_full)),
        (1, [term for alias in romaji_aliases for term in _search_terms(alias)]),
    )
    document = _SearchDocument(
        song_id=song_id,
        title=title,
        confidence=1.0,
        bm25_counts=dict(Counter(bm25_terms)),
        bm25_len=len(bm25_terms),
    )
    return document, romaji_aliases


def _build_search_index(
    songs: dict[int, tuple[str, tuple[str, ...]]], index: dict[int, str], alias_records: list[tuple[int, str]],
) -> _SearchIndex:
    """Build an owned snapshot in a worker; never touch live repository/cache state."""
    alias_choices_norm: dict[str, list[tuple[int, str]]] = {}
    alias_norm_list = []
    aliases_by_song_id: dict[int, list[str]] = {}
    for song_id, alias in alias_records:
        if song_id not in index or not alias:
            continue
        alias_norm = _normalize_for_matching(str(alias))
        if not alias_norm:
            continue
        alias_norm_list.append(alias_norm)
        value = (song_id, str(alias))
        if value not in alias_choices_norm.setdefault(alias_norm, []):
            alias_choices_norm[alias_norm].append(value)
            aliases_by_song_id.setdefault(song_id, []).append(str(alias))
    alias_freq = Counter(alias_norm_list)
    for song_id, (_, aliases) in songs.items():
        aliases_by_song_id.setdefault(song_id, []).extend(aliases)
    title_choices_lists: dict[str, list[tuple[int, str]]] = {}
    title_pinyin_initials_lists: dict[str, list[tuple[int, str]]] = {}
    title_full_pinyin_norm: dict[str, list[tuple[int, str]]] = {}
    alias_pinyin_initials_lists: dict[str, list[tuple[int, str]]] = {}
    simplified_title_choices: dict[str, list[tuple[int, str]]] = {}
    romaji_alias_choices_lists: dict[str, list[tuple[int, str]]] = {}
    documents: dict[int, _SearchDocument] = {}
    bm25_doc_freq: Counter[str] = Counter()
    bm25_total_len = 0
    for song_id, title in index.items():
        if not title:
            continue
        aliases = _unique_texts(aliases_by_song_id.get(song_id, []))
        document, romaji_aliases = _build_search_document(song_id, songs.get(song_id, ("", ()))[0], title, aliases)
        documents[song_id] = document
        for romaji_alias in romaji_aliases:
            romaji_norm = _normalize_for_matching(romaji_alias)
            _append_choice(romaji_alias_choices_lists, romaji_norm, (song_id, romaji_alias))
        bm25_doc_freq.update(document.bm25_counts.keys())
        bm25_total_len += document.bm25_len
        title_norm = _normalize_for_matching(title)
        _append_choice(title_choices_lists, title_norm, (song_id, title))
        if not _has_cjk(title):
            continue
        initials, full_py = _get_pinyin(title)
        _append_choice(title_pinyin_initials_lists, _normalize_for_matching(initials), (song_id, title))
        full_norm = _normalize_for_matching(full_py)
        if full_norm:
            _append_choice(title_full_pinyin_norm, full_norm, (song_id, title))
        simplified_norm = _normalize_for_matching(_normalize_to_simplified(title))
        if simplified_norm and simplified_norm != title_norm:
            _append_choice(simplified_title_choices, simplified_norm, (song_id, title))

    alias_full_pinyin_norm: dict[str, list[tuple[int, str]]] = {}
    simplified_alias_choices: dict[str, list[tuple[int, str]]] = {}
    for alias_norm, (song_id, original_alias) in ((key, value) for key, values in alias_choices_norm.items() for value in values):
        if not _has_cjk(original_alias):
            continue
        initials, full_py = _get_pinyin(original_alias)
        _append_choice(
            alias_pinyin_initials_lists,
            _normalize_for_matching(initials),
            (song_id, original_alias),
        )
        full_norm = _normalize_for_matching(full_py)
        if full_norm:
            _append_choice(alias_full_pinyin_norm, full_norm, (song_id, original_alias))
        simplified_norm = _normalize_for_matching(_normalize_to_simplified(original_alias))
        if simplified_norm and simplified_norm != alias_norm:
            _append_choice(simplified_alias_choices, simplified_norm, (song_id, original_alias))

    return _SearchIndex(
        songs={},
        index=index,
        alias_choices_norm=alias_choices_norm,
        alias_freq=alias_freq,
        title_choices_norm={
            key: tuple(values)
            for key, values in title_choices_lists.items()
        },
        title_pinyin_initials_norm={
            key: tuple(values)
            for key, values in title_pinyin_initials_lists.items()
        },
        title_full_pinyin_norm=title_full_pinyin_norm,
        alias_pinyin_initials_norm={
            key: tuple(values)
            for key, values in alias_pinyin_initials_lists.items()
        },
        alias_full_pinyin_norm=alias_full_pinyin_norm,
        romaji_alias_choices_norm={
            key: tuple(values)
            for key, values in romaji_alias_choices_lists.items()
        },
        simplified_title_choices=simplified_title_choices,
        simplified_alias_choices=simplified_alias_choices,
        documents=documents,
        bm25_doc_freq=bm25_doc_freq,
        bm25_avg_len=(bm25_total_len / len(documents)) if documents else 0.0,
    )
