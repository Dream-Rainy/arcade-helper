"""乐曲查询服务：支持 ID、标题、别名的模糊查询。

提供统一的查询接口，返回匹配的乐曲数据列表。
"""

from contextvars import ContextVar
from typing import Any, Protocol
from collections import Counter
from time import monotonic
from uuid import uuid4
import logging

from rapidfuzz import fuzz, process

from arcade_helper.core.errors import ArcadeHelperError
from arcade_helper.core.song import SongData
from arcade_helper.search.models import MatchType, SongQueryResult
from .constants import (
    ALIAS_CACHE_TTL_SECONDS,
    ALIAS_SCORE_PENALTY,
    BM25_TOP_CANDIDATES,
    FUZZY_THRESHOLD,
    MAX_RESULTS,
    TITLE_SCORE_BOOST,
)
from .embedding import (
    _cosine_similarity,
    _embedding_path,
    _embedding_threshold,
    _load_embedding_index,
    _truthy_env,
)
from .index import _SearchDocument, _SearchIndex, _build_search_document
from .normalization import (
    GameCode,
    _append_choice,
    _ascii_tokens,
    _first_meaningful_ascii_token,
    _get_pinyin,
    _has_cjk,
    _meaningful_ascii_tokens,
    _normalize_for_matching,
    _normalize_game_code,
    _normalize_to_simplified,
    _unique_texts,
)
from .scoring import (
    _alias_ambiguity_penalty,
    _apply_coverage_guard,
    _bm25_query_terms,
    _bm25_score,
    _cap_weak_compact_match_score,
    _compute_composite_score,
    _get_effective_threshold,
    _get_fuzzy_scorer,
    _get_symmetric_coverage,
    _has_multiple_search_terms,
    _is_short_ascii_query,
)

logger = logging.getLogger(__name__)

class SongSearchRepository(Protocol):
    async def get_song_by_id(self, game_code: str, song_id: int) -> SongData | None:
        """Return one song by game/id."""

    async def load_all_songs(self, game_code: str) -> dict[int, SongData]:
        """Return all songs for a game."""

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        """Return song id to title mapping for a game."""

    async def query_alias_exact(self, game_code: str, alias_lower: str) -> list[tuple[int, str]]:
        """Return exact alias matches."""

    async def load_alias_records(self, game_code: str) -> list[tuple[int, str]]:
        """Return all alias rows as ``(song_id, alias)``."""

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int) -> list[str]:
        """Return display aliases for one song."""

    async def get_song_with_difficulty(
        self,
        game_code: str,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
    ) -> dict | None:
        """Return song data with selected difficulty metadata."""


_repository_var: ContextVar[SongSearchRepository | None] = ContextVar(
    "arcade_helper_song_search_repository",
    default=None,
)


class SongSearchNotConfiguredError(ArcadeHelperError):
    """Raised when module-level search functions are used without a repository."""


class SongSearchService:
    """Provider-independent song search backed by a catalog repository."""

    def __init__(self, repository: SongSearchRepository) -> None:
        self.repository = repository

    async def search_song(
        self,
        query: str | int,
        *,
        game_code: str = "maimai",
    ) -> list["SongQueryResult"]:
        """Search songs by id, exact text, alias, or fuzzy text."""
        token = _repository_var.set(self.repository)
        try:
            return await search_song(query, game_code=game_code)
        finally:
            _repository_var.reset(token)

    async def query_song_by_id(
        self,
        song_id: int,
        *,
        game_code: str = "maimai",
    ) -> "SongQueryResult | None":
        """Return one song search result by exact id."""
        token = _repository_var.set(self.repository)
        try:
            return await query_song_by_id(song_id, game_code=game_code)
        finally:
            _repository_var.reset(token)

    async def query_song_by_title_exact(
        self,
        title: str,
        *,
        game_code: str = "maimai",
    ) -> list["SongQueryResult"]:
        """Return exact-title song matches."""
        token = _repository_var.set(self.repository)
        try:
            return await query_song_by_title_exact(title, game_code=game_code)
        finally:
            _repository_var.reset(token)

    async def query_song_by_alias_exact(
        self,
        alias: str,
        *,
        game_code: str = "maimai",
    ) -> list["SongQueryResult"]:
        """Return exact-alias song matches."""
        token = _repository_var.set(self.repository)
        try:
            return await query_song_by_alias_exact(alias, game_code=game_code)
        finally:
            _repository_var.reset(token)

    async def query_song_fuzzy(
        self,
        query: str,
        threshold: float = 80,
        *,
        game_code: str = "maimai",
    ) -> list["SongQueryResult"]:
        """Return fuzzy title/alias song matches."""
        token = _repository_var.set(self.repository)
        try:
            return await query_song_fuzzy(query, threshold=threshold, game_code=game_code)
        finally:
            _repository_var.reset(token)

    async def get_song_with_difficulty(
        self,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
        *,
        game_code: str = "maimai",
    ) -> dict | None:
        """Return a song with selected difficulty metadata."""
        token = _repository_var.set(self.repository)
        try:
            return await get_song_with_difficulty(
                song_id,
                song_type=song_type,
                level_index=level_index,
                game_code=game_code,
            )
        finally:
            _repository_var.reset(token)

    async def get_song_aliases(
        self,
        song_query: str | int,
        *,
        game_code: str = "maimai",
    ) -> dict | None:
        """Return alias information for a song query."""
        token = _repository_var.set(self.repository)
        try:
            return await get_song_aliases(song_query, game_code=game_code)
        finally:
            _repository_var.reset(token)

    def invalidate_alias_cache(self, game_code: str | None = None) -> None:
        """Clear cached alias data for one game or all games."""
        invalidate_alias_cache(game_code)


_alias_choices_cache_by_game: dict[str, dict[str, tuple[int, str]]] = {}
_alias_freq_cache_by_game: dict[str, Counter[str]] = {}
_alias_cache_loaded_at_by_game: dict[str, float] = {}
_search_index_cache_by_game: dict[str, "_SearchIndex"] = {}
_search_index_loaded_at_by_game: dict[str, float] = {}


def _repository() -> SongSearchRepository:
    repository = _repository_var.get()
    if repository is None:
        raise SongSearchNotConfiguredError(
            "Song search requires SongSearchService or a configured repository context"
        )
    return repository


async def _song_store(game_code: str) -> dict[int, SongData]:
    return await _repository().load_all_songs(_normalize_game_code(game_code))


async def _song_index(game_code: str) -> dict[int, str]:
    return await _repository().load_song_index(_normalize_game_code(game_code))


async def _song_by_id(song_id: int, game_code: str) -> SongData | None:
    return await _repository().get_song_by_id(_normalize_game_code(game_code), song_id)


def _song_title(song_data: SongData, game_code: str) -> str:
    return str(getattr(song_data, "title", "") or "")


def _build_trace_id(query: str | int) -> str:
    q = str(query)
    q = q.replace("\n", " ").replace("\r", " ")
    q = q[:16]
    return f"{q}:{uuid4().hex[:8]}"


def invalidate_alias_cache(game_code: str | None = None) -> None:
    """失效别名缓存（曲库同步后调用）。"""
    global _alias_choices_cache_by_game, _alias_freq_cache_by_game, _alias_cache_loaded_at_by_game
    global _search_index_cache_by_game, _search_index_loaded_at_by_game

    if game_code is None:
        _alias_choices_cache_by_game = {}
        _alias_freq_cache_by_game = {}
        _alias_cache_loaded_at_by_game = {}
        _search_index_cache_by_game = {}
        _search_index_loaded_at_by_game = {}
        logger.debug("[alias_cache] invalidated (all games)")
        return

    gc = _normalize_game_code(game_code)
    _alias_choices_cache_by_game.pop(gc, None)
    _alias_freq_cache_by_game.pop(gc, None)
    _alias_cache_loaded_at_by_game.pop(gc, None)
    _search_index_cache_by_game.pop(gc, None)
    _search_index_loaded_at_by_game.pop(gc, None)
    logger.debug(f"[alias_cache] invalidated game={gc}")


async def _get_alias_cache(
    game_code: str = "maimai",
    force_refresh: bool = False,
) -> tuple[dict[str, tuple[int, str]], Counter[str]]:
    gc = _normalize_game_code(game_code)
    now = monotonic()

    alias_choices_norm = _alias_choices_cache_by_game.get(gc)
    alias_freq = _alias_freq_cache_by_game.get(gc)
    loaded_at = _alias_cache_loaded_at_by_game.get(gc, 0.0)

    has_cache = bool(alias_choices_norm)
    cache_fresh = (
        not force_refresh
        and has_cache
        and (now - loaded_at) < ALIAS_CACHE_TTL_SECONDS
        and alias_freq is not None
    )

    if cache_fresh:
        logger.debug(
            f"[alias_cache][{gc}] hit entries={len(alias_choices_norm)} age={now - loaded_at:.1f}s"
        )
        return alias_choices_norm, alias_freq

    alias_records = await _repository().load_alias_records(gc)

    alias_choices_norm = {}
    alias_norm_list: list[str] = []

    for song_id, alias in alias_records:
        if not alias:
            continue
        alias_norm = _normalize_for_matching(str(alias))
        if not alias_norm:
            continue
        alias_norm_list.append(alias_norm)
        if alias_norm not in alias_choices_norm:
            alias_choices_norm[alias_norm] = (song_id, str(alias))

    alias_freq = Counter(alias_norm_list)
    _alias_choices_cache_by_game[gc] = alias_choices_norm
    _alias_freq_cache_by_game[gc] = alias_freq
    _alias_cache_loaded_at_by_game[gc] = now

    logger.debug(
        f"[alias_cache][{gc}] reload entries={len(alias_choices_norm)} total_rows={len(alias_norm_list)}"
    )
    return alias_choices_norm, alias_freq


async def _get_search_index(game_code: str = "maimai") -> _SearchIndex:
    gc = _normalize_game_code(game_code)
    now = monotonic()
    cached = _search_index_cache_by_game.get(gc)
    loaded_at = _search_index_loaded_at_by_game.get(gc, 0.0)
    if cached is not None and (now - loaded_at) < ALIAS_CACHE_TTL_SECONDS:
        return cached

    songs = await _song_store(gc)
    index = {
        song_id: _song_title(song_data, gc)
        for song_id, song_data in songs.items()
    } if songs else await _song_index(gc)
    alias_choices_norm, alias_freq = await _get_alias_cache(gc)
    aliases_by_song_id: dict[int, list[str]] = {}
    for _, (song_id, original_alias) in alias_choices_norm.items():
        aliases_by_song_id.setdefault(song_id, []).append(original_alias)
    for song_id, song_data in songs.items():
        aliases_by_song_id.setdefault(song_id, []).extend(getattr(song_data, "aliases", None) or [])

    title_choices_lists: dict[str, list[tuple[int, str]]] = {}
    title_pinyin_initials_lists: dict[str, list[tuple[int, str]]] = {}
    title_full_pinyin_norm: dict[str, tuple[int, str]] = {}
    alias_pinyin_initials_lists: dict[str, list[tuple[int, str]]] = {}
    simplified_title_choices: dict[str, tuple[int, str]] = {}
    romaji_alias_choices_lists: dict[str, list[tuple[int, str]]] = {}
    documents: dict[int, _SearchDocument] = {}
    bm25_doc_freq: Counter[str] = Counter()
    bm25_total_len = 0
    for song_id, title in index.items():
        if not title:
            continue
        aliases = _unique_texts(aliases_by_song_id.get(song_id, []))
        document = _build_search_document(song_id, songs.get(song_id), title, aliases)
        documents[song_id] = document
        for romaji_alias in document.romaji_aliases:
            romaji_norm = _normalize_for_matching(romaji_alias)
            _append_choice(romaji_alias_choices_lists, romaji_norm, (song_id, romaji_alias))
        bm25_doc_freq.update(set(document.bm25_terms))
        bm25_total_len += document.bm25_len
        title_norm = _normalize_for_matching(title)
        _append_choice(title_choices_lists, title_norm, (song_id, title))
        if not _has_cjk(title):
            continue
        initials, full_py = _get_pinyin(title)
        _append_choice(title_pinyin_initials_lists, _normalize_for_matching(initials), (song_id, title))
        full_norm = _normalize_for_matching(full_py)
        if full_norm and full_norm not in title_full_pinyin_norm:
            title_full_pinyin_norm[full_norm] = (song_id, title)
        simplified_norm = _normalize_for_matching(_normalize_to_simplified(title))
        if simplified_norm and simplified_norm != title_norm and simplified_norm not in simplified_title_choices:
            simplified_title_choices[simplified_norm] = (song_id, title)

    alias_full_pinyin_norm: dict[str, tuple[int, str]] = {}
    simplified_alias_choices: dict[str, tuple[int, str]] = {}
    for alias_norm, (song_id, original_alias) in alias_choices_norm.items():
        if not _has_cjk(original_alias):
            continue
        initials, full_py = _get_pinyin(original_alias)
        _append_choice(
            alias_pinyin_initials_lists,
            _normalize_for_matching(initials),
            (song_id, original_alias),
        )
        full_norm = _normalize_for_matching(full_py)
        if full_norm and full_norm not in alias_full_pinyin_norm:
            alias_full_pinyin_norm[full_norm] = (song_id, original_alias)
        simplified_norm = _normalize_for_matching(_normalize_to_simplified(original_alias))
        if simplified_norm and simplified_norm != alias_norm and simplified_norm not in simplified_alias_choices:
            simplified_alias_choices[simplified_norm] = (song_id, original_alias)

    search_index = _SearchIndex(
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
    _search_index_cache_by_game[gc] = search_index
    _search_index_loaded_at_by_game[gc] = now
    logger.debug(
        f"[search_index][{gc}] reload titles={len(title_choices_lists)} aliases={len(alias_choices_norm)}"
    )
    return search_index


def _strip_query_prefix_for_retry(query: str) -> str:
    """兜底重试用：剥离难度/版本前缀（白/紫/红/黄/绿/标/标准/dx）。"""
    prefixes = ("白", "紫", "红", "黄", "绿", "标" ,"标准", "dx")
    current = query.lstrip()
    original = current

    while current:
        lowered = current.lower()
        matched = False
        for prefix in prefixes:
            if lowered.startswith(prefix):
                current = current[len(prefix):].lstrip()
                matched = True
                break
        if not matched:
            break

    if not current:
        return ""
    return current if current != original else ""


def _log_match_decision(
    strategy: str,
    trace_id: str,
    query: str,
    candidate: str,
    raw_score: float,
    composite_score: float,
    guard_score: float,
    final_score: float,
    threshold: float,
    norm_query: str,
    norm_candidate: str,
    coverage: float,
    ratio: float,
    partial: float,
    dice: float,
    alias_freq: int = 0,
    reason: str = "",
) -> None:
    """输出结构化匹配日志，便于排查误匹配。"""
    decision = "ACCEPT" if final_score >= threshold else "REJECT"
    logger.debug(
        f"[fuzzy][trace={trace_id}] strategy={strategy} query={query!r} norm_query={norm_query!r} "
        f"candidate={candidate!r} norm_candidate={norm_candidate!r} raw={raw_score:.1f} "
        f"ratio={ratio:.1f} partial={partial:.1f} dice={dice:.1f} composite={composite_score:.1f} "
        f"coverage={coverage:.3f} alias_freq={alias_freq} guard={guard_score:.1f} final={final_score:.1f} "
        f"threshold={threshold:.1f} reason={reason or '-'} -> {decision}"
    )


async def get_song_data_from_id(
    song_id: int,
    *,
    game_code: str = "maimai",
) -> SongData | None:
    """从本地曲库缓存中按 id 获取乐曲数据。

    纯领域能力：不依赖 OAuth/用户会话/绑定等平台功能。
    仅要求对应游戏曲库已由启动流程载入 plugin_data。
    """
    return (await _song_store(game_code)).get(song_id)


async def get_song_data(
    query_info: Any,
    *,
    game_code: str = "maimai",
) -> SongData | None:
    """解析查询入参并返回乐曲数据。

    当前仅支持 song id（int）；后续可以扩展：别名、关键字、模糊搜索等。
    """

    if isinstance(query_info, int):
        return await get_song_data_from_id(query_info, game_code=game_code)
    return None


async def query_song_by_id(
    song_id: int,
    *,
    game_code: str = "maimai",
) -> SongQueryResult | None:
    """通过 ID 精确查询乐曲。

    Args:
        song_id: 乐曲 ID

    Returns:
        查询结果，未找到则返回 None
    """
    logger.debug(f"查询乐曲 ID: {song_id} (game={game_code})")
    song_data = await _song_by_id(song_id, game_code)
    if song_data:
        t = _song_title(song_data, game_code)
        logger.debug(f"找到乐曲: {t}")
        return SongQueryResult(
            song_id=song_id,
            title=t,
            match_type=MatchType.EXACT_ID,
            match_score=100.0,
            matched_text=str(song_id),
            song_data=song_data,
        )
    logger.debug(f"未找到乐曲 ID: {song_id}")
    return None


async def query_song_by_title_exact(
    title: str,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    """通过标题精确查询乐曲（不区分大小写）。

    Args:
        title: 乐曲标题

    Returns:
        匹配的乐曲列表
    """
    results = []
    title_lower = title.lower()
    index = await _song_index(game_code)

    for song_id, song_title in index.items():
        if song_title.lower() == title_lower:
            song_data = await _song_by_id(song_id, game_code)
            if not song_data:
                continue
            results.append(SongQueryResult(
                song_id=song_id,
                title=song_title,
                match_type=MatchType.EXACT_TITLE,
                match_score=100.0,
                matched_text=song_title,
                song_data=song_data,
            ))

    return results


async def query_song_by_alias_exact(
    alias: str,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    """通过别名精确查询乐曲（不区分大小写）。

    Args:
        alias: 别名

    Returns:
        匹配的乐曲列表
    """
    results: list[SongQueryResult] = []
    alias_lower = alias.lower()

    matched_aliases = await _repository().query_alias_exact(game_code, alias_lower)
    for song_id, alias_original in matched_aliases:
        song_data = await _song_by_id(song_id, game_code)
        if not song_data:
            continue
        results.append(
            SongQueryResult(
                song_id=song_id,
                title=_song_title(song_data, game_code),
                match_type=MatchType.EXACT_ALIAS,
                match_score=100.0,
                matched_text=str(alias_original),
                song_data=song_data,
            )
        )

    return results


async def _query_song_by_normalized_exact(
    query: str,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    query_norm = _normalize_for_matching(query)
    if not query_norm:
        return []

    search_index = await _get_search_index(game_code)
    results: list[SongQueryResult] = []
    seen_song_ids: set[int] = set()

    for song_id, title in search_index.title_choices_norm.get(query_norm, ()):
        song_data = await _song_by_id(song_id, game_code)
        if song_data:
            seen_song_ids.add(song_id)
            results.append(SongQueryResult(
                song_id=song_id,
                title=title,
                match_type=MatchType.EXACT_TITLE,
                match_score=100.0,
                matched_text=title,
                song_data=song_data,
            ))

    alias_match = search_index.alias_choices_norm.get(query_norm)
    if alias_match is not None:
        song_id, alias_original = alias_match
        if song_id not in seen_song_ids:
            song_data = await _song_by_id(song_id, game_code)
            if song_data:
                results.append(SongQueryResult(
                    song_id=song_id,
                    title=_song_title(song_data, game_code),
                    match_type=MatchType.EXACT_ALIAS,
                    match_score=100.0,
                    matched_text=str(alias_original),
                    song_data=song_data,
                ))

    return results


async def _query_song_bm25(
    query: str,
    threshold: float = FUZZY_THRESHOLD,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    search_index = await _get_search_index(game_code)
    query_terms = _bm25_query_terms(query)
    if not query_terms:
        return []

    scored: list[tuple[float, _SearchDocument]] = []
    total_docs = len(search_index.documents)
    for document in search_index.documents.values():
        raw_score = _bm25_score(
            query_terms,
            document,
            total_docs=total_docs,
            doc_freq=search_index.bm25_doc_freq,
            avg_len=search_index.bm25_avg_len,
        )
        if raw_score > 0:
            scored.append((raw_score, document))

    if not scored:
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    effective_threshold = _get_effective_threshold(_normalize_for_matching(query), threshold)
    results: list[SongQueryResult] = []
    seen_song_ids: set[int] = set()
    for raw_score, document in scored[:BM25_TOP_CANDIDATES]:
        if document.song_id in seen_song_ids:
            continue
        final_score = min(95.0, 60.0 + raw_score * 15.0) * document.confidence
        if final_score < effective_threshold:
            continue
        song_data = await _song_by_id(document.song_id, game_code)
        if not song_data:
            continue
        seen_song_ids.add(document.song_id)
        results.append(SongQueryResult(
            song_id=document.song_id,
            title=document.title,
            match_type=MatchType.BM25,
            match_score=final_score,
            matched_text=document.title,
            song_data=song_data,
        ))
        if len(results) >= MAX_RESULTS:
            break
    return results


async def _query_song_embedding(
    query: str,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    if not _truthy_env("SONG_SEARCH_EMBEDDING_ENABLED"):
        return []
    path = _embedding_path()
    if path is None:
        return []

    gc = _normalize_game_code(game_code)
    index = _load_embedding_index(path)
    query_vector = index.query_vectors_by_game.get(gc, {}).get(_normalize_for_matching(query))
    if query_vector is None:
        return []

    scored: list[tuple[float, int]] = []
    for song_id, song_vector in index.song_vectors_by_game.get(gc, {}).items():
        similarity = _cosine_similarity(query_vector, song_vector)
        score = similarity * 100.0
        if score >= _embedding_threshold():
            scored.append((score, song_id))

    scored.sort(reverse=True)
    results: list[SongQueryResult] = []
    seen_song_ids: set[int] = set()
    for score, song_id in scored:
        if song_id in seen_song_ids:
            continue
        song_data = await _song_by_id(song_id, gc)
        if not song_data:
            continue
        seen_song_ids.add(song_id)
        results.append(SongQueryResult(
            song_id=song_id,
            title=_song_title(song_data, gc),
            match_type=MatchType.EMBEDDING,
            match_score=round(score, 3),
            matched_text=query,
            song_data=song_data,
        ))
        if len(results) >= MAX_RESULTS:
            break
    return results


async def _query_song_romaji(
    query: str,
    threshold: float = FUZZY_THRESHOLD,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    query_norm = _normalize_for_matching(query)
    if not query_norm or not query_norm.isascii() or _is_short_ascii_query(query_norm):
        return []

    search_index = await _get_search_index(game_code)
    choices = search_index.romaji_alias_choices_norm
    if not choices:
        return []

    matches = process.extract(
        query_norm,
        choices.keys(),
        scorer=fuzz.partial_ratio,
        limit=MAX_RESULTS * 3,
        score_cutoff=_get_effective_threshold(query_norm, threshold),
    )
    results: list[SongQueryResult] = []
    seen_song_ids: set[int] = set()
    for alias_norm, raw_score, _ in matches:
        for song_id, original_alias in choices[alias_norm]:
            if song_id in seen_song_ids:
                continue
            guard_score = _apply_coverage_guard(raw_score, query_norm, alias_norm)
            final_score = min(96.0, guard_score * 0.90)
            if final_score < threshold:
                continue
            song_data = await _song_by_id(song_id, game_code)
            if not song_data:
                continue
            seen_song_ids.add(song_id)
            results.append(SongQueryResult(
                song_id=song_id,
                title=_song_title(song_data, game_code),
                match_type=MatchType.ROMAJI,
                match_score=final_score,
                matched_text=original_alias,
                song_data=song_data,
            ))
            if len(results) >= MAX_RESULTS:
                return results
    return results


async def query_song_fuzzy(
    query: str,
    threshold: float = FUZZY_THRESHOLD,
    trace_id: str = "",
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    """模糊查询乐曲（标题 + 别名 + 拼音 + 简体化归一）。"""
    results: list[SongQueryResult] = []
    seen_song_ids: set[int] = set()
    search_index = await _get_search_index(game_code)
    index = search_index.index
    alias_choices_norm = search_index.alias_choices_norm
    alias_freq = search_index.alias_freq
    phase_stats: dict[str, dict[str, int]] = {}

    def _bump_phase(phase: str, key: str) -> None:
        if phase not in phase_stats:
            phase_stats[phase] = {
                "candidates": 0,
                "accepted": 0,
                "reject_coverage": 0,
                "reject_ambiguity": 0,
                "reject_threshold": 0,
            }
        phase_stats[phase][key] += 1

    query_norm = _normalize_for_matching(query)
    scorer = _get_fuzzy_scorer(query_norm)
    effective_threshold = _get_effective_threshold(query_norm, threshold)
    query_is_ascii = query_norm.isascii() and not _has_cjk(query_norm)
    query_has_cjk = _has_cjk(query_norm)

    if trace_id:
        logger.debug(
            f"[fuzzy][trace={trace_id}] start query={query!r} norm_query={query_norm!r} "
            f"threshold={threshold:.1f} effective_threshold={effective_threshold:.1f} "
            f"scorer={getattr(scorer, '__name__', str(scorer))}"
        )

    # ── 0. 标题 token 边界匹配（短 ASCII 查询优先保留词边界语义）──────────────
    if _is_short_ascii_query(query_norm):
        for song_id, title in index.items():
            if not title:
                continue
            first_token = _first_meaningful_ascii_token(title)
            tokens = _meaningful_ascii_tokens(title)
            if query_norm == first_token:
                strategy = "title_head_token"
                final_score = 99.0
            elif query_norm in tokens:
                strategy = "title_token"
                final_score = 93.0
            else:
                continue

            _bump_phase(strategy, "candidates")
            song_data = await _song_by_id(song_id, game_code)
            if not song_data:
                continue
            coverage = _get_symmetric_coverage(query_norm, query_norm)
            reason = ""
            if final_score < threshold:
                reason = "threshold"
                _bump_phase(strategy, "reject_threshold")
            else:
                _bump_phase(strategy, "accepted")
                seen_song_ids.add(song_id)
                results.append(SongQueryResult(
                    song_id=song_id,
                    title=title,
                    match_type=MatchType.FUZZY_TITLE,
                    match_score=final_score,
                    matched_text=title,
                    song_data=song_data,
                ))
            _log_match_decision(
                strategy=strategy,
                trace_id=trace_id,
                query=query,
                candidate=title,
                raw_score=final_score,
                composite_score=final_score,
                guard_score=final_score,
                final_score=final_score,
                threshold=threshold,
                norm_query=query_norm,
                norm_candidate=query_norm,
                coverage=coverage,
                ratio=final_score,
                partial=final_score,
                dice=0.0,
                reason=reason,
            )

    # ── 1. 标题原文匹配 ───────────────────────────────────────────────────────
    title_choices_norm = search_index.title_choices_norm
    if title_choices_norm and query_norm:
        title_matches = process.extract(
            query_norm,
            title_choices_norm.keys(),
            scorer=scorer,
            limit=MAX_RESULTS * 3,
            score_cutoff=effective_threshold,
        )
        for title_norm, raw_score, _ in title_matches:
            for song_id, title in title_choices_norm[title_norm]:
                _bump_phase("title_raw", "candidates")
                if song_id in seen_song_ids:
                    continue
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue

                composite, ratio, partial, dice = _compute_composite_score(query_norm, title_norm, raw_score)
                coverage = _get_symmetric_coverage(query_norm, title_norm)
                guard_score = _apply_coverage_guard(composite, query_norm, title_norm)
                final_score = min(guard_score * TITLE_SCORE_BOOST, 100.0)
                final_score = _cap_weak_compact_match_score(final_score, query_norm, title)

                reason = ""
                if guard_score <= 0:
                    reason = "coverage"
                    _bump_phase("title_raw", "reject_coverage")
                elif final_score < threshold:
                    reason = "threshold"
                    _bump_phase("title_raw", "reject_threshold")
                else:
                    _bump_phase("title_raw", "accepted")
                    seen_song_ids.add(song_id)
                    results.append(SongQueryResult(
                        song_id=song_id,
                        title=title,
                        match_type=MatchType.FUZZY_TITLE,
                        match_score=final_score,
                        matched_text=title,
                        song_data=song_data,
                    ))

                _log_match_decision(
                    strategy="title_raw",
                    trace_id=trace_id,
                    query=query,
                    candidate=title,
                    raw_score=raw_score,
                    composite_score=composite,
                    guard_score=guard_score,
                    final_score=final_score,
                    threshold=threshold,
                    norm_query=query_norm,
                    norm_candidate=title_norm,
                    coverage=coverage,
                    ratio=ratio,
                    partial=partial,
                    dice=dice,
                    reason=reason,
                )

    # ── 2. 别名原文匹配 + 歧义惩罚 ───────────────────────────────────────────
    if _is_short_ascii_query(query_norm):
        for alias_norm, (song_id, original_alias) in alias_choices_norm.items():
            if song_id in seen_song_ids:
                continue
            first_token = _first_meaningful_ascii_token(original_alias)
            tokens = _meaningful_ascii_tokens(original_alias)
            if query_norm == first_token:
                strategy = "alias_head_token"
                raw_score = 97.0
            elif query_norm in tokens:
                strategy = "alias_token"
                raw_score = 94.0
            else:
                continue

            _bump_phase(strategy, "candidates")
            song_data = await _song_by_id(song_id, game_code)
            if not song_data:
                continue
            ambiguity = _alias_ambiguity_penalty(alias_norm, alias_freq.get(alias_norm, 1))
            final_score = raw_score * ALIAS_SCORE_PENALTY * ambiguity
            coverage = _get_symmetric_coverage(query_norm, query_norm)

            reason = ""
            if ambiguity <= 0:
                reason = "ambiguity"
                _bump_phase(strategy, "reject_ambiguity")
            elif final_score < threshold:
                reason = "threshold"
                _bump_phase(strategy, "reject_threshold")
            else:
                _bump_phase(strategy, "accepted")
                seen_song_ids.add(song_id)
                results.append(SongQueryResult(
                    song_id=song_id,
                    title=_song_title(song_data, game_code),
                    match_type=MatchType.FUZZY_ALIAS,
                    match_score=final_score,
                    matched_text=original_alias,
                    song_data=song_data,
                ))
            _log_match_decision(
                strategy=strategy,
                trace_id=trace_id,
                query=query,
                candidate=original_alias,
                raw_score=raw_score,
                composite_score=raw_score,
                guard_score=raw_score,
                final_score=final_score,
                threshold=threshold,
                norm_query=query_norm,
                norm_candidate=query_norm,
                coverage=coverage,
                ratio=raw_score,
                partial=raw_score,
                dice=0.0,
                alias_freq=alias_freq.get(alias_norm, 1),
                reason=reason,
            )

    if alias_choices_norm and query_norm:
        alias_matches = process.extract(
            query_norm,
            alias_choices_norm.keys(),
            scorer=scorer,
            limit=MAX_RESULTS * 4,
            score_cutoff=effective_threshold,
        )
        for alias_norm, raw_score, _ in alias_matches:
            _bump_phase("alias_raw", "candidates")
            song_id, original_alias = alias_choices_norm[alias_norm]
            if song_id in seen_song_ids:
                continue
            song_data = await _song_by_id(song_id, game_code)
            if not song_data:
                continue

            composite, ratio, partial, dice = _compute_composite_score(query_norm, alias_norm, raw_score)
            coverage = _get_symmetric_coverage(query_norm, alias_norm)
            guard_score = _apply_coverage_guard(composite, query_norm, alias_norm)
            ambiguity = _alias_ambiguity_penalty(alias_norm, alias_freq.get(alias_norm, 1))
            final_score = guard_score * ALIAS_SCORE_PENALTY * ambiguity
            final_score = _cap_weak_compact_match_score(final_score, query_norm, original_alias)

            reason = ""
            if ambiguity <= 0:
                reason = "ambiguity"
                _bump_phase("alias_raw", "reject_ambiguity")
            elif guard_score <= 0:
                reason = "coverage"
                _bump_phase("alias_raw", "reject_coverage")
            elif final_score < threshold:
                reason = "threshold"
                _bump_phase("alias_raw", "reject_threshold")
            else:
                _bump_phase("alias_raw", "accepted")
                seen_song_ids.add(song_id)
                results.append(SongQueryResult(
                    song_id=song_id,
                    title=_song_title(song_data, game_code),
                    match_type=MatchType.FUZZY_ALIAS,
                    match_score=final_score,
                    matched_text=original_alias,
                    song_data=song_data,
                ))

            _log_match_decision(
                strategy="alias_raw",
                trace_id=trace_id,
                query=query,
                candidate=original_alias,
                raw_score=raw_score,
                composite_score=composite,
                guard_score=guard_score,
                final_score=final_score,
                threshold=threshold,
                norm_query=query_norm,
                norm_candidate=alias_norm,
                coverage=coverage,
                ratio=ratio,
                partial=partial,
                dice=dice,
                alias_freq=alias_freq.get(alias_norm, 1),
                reason=reason,
            )

    # ── 3. 拼音匹配（仅 ASCII 查询）──────────────────────────────────────────
    if query_is_ascii and len(query_norm) >= 2:
        query_ascii = query_norm

        for song_id, title in search_index.title_pinyin_initials_norm.get(query_ascii, ()):
            if song_id in seen_song_ids:
                continue
            _bump_phase("title_pinyin_initials", "candidates")
            coverage = _get_symmetric_coverage(query_ascii, query_ascii)
            final_score = 98.0
            reason = "" if final_score >= threshold else "threshold"
            if final_score >= threshold:
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue
                _bump_phase("title_pinyin_initials", "accepted")
                seen_song_ids.add(song_id)
                results.append(SongQueryResult(
                    song_id=song_id,
                    title=title,
                    match_type=MatchType.PINYIN_INITIALS,
                    match_score=final_score,
                    matched_text=title,
                    song_data=song_data,
                ))
            else:
                _bump_phase("title_pinyin_initials", "reject_threshold")
            _log_match_decision(
                strategy="title_pinyin_initials",
                trace_id=trace_id,
                query=query,
                candidate=title,
                raw_score=98.0,
                composite_score=98.0,
                guard_score=98.0,
                final_score=final_score,
                threshold=threshold,
                norm_query=query_ascii,
                norm_candidate=query_ascii,
                coverage=coverage,
                ratio=98.0,
                partial=98.0,
                dice=0.0,
                reason=reason,
            )

        title_full_pinyin_norm = search_index.title_full_pinyin_norm
        if title_full_pinyin_norm:
            py_title_matches = process.extract(
                query_ascii,
                title_full_pinyin_norm.keys(),
                scorer=fuzz.partial_ratio,
                limit=MAX_RESULTS * 3,
                score_cutoff=effective_threshold,
            )
            for full_norm, raw_score, _ in py_title_matches:
                _bump_phase("title_pinyin_full", "candidates")
                song_id, title = title_full_pinyin_norm[full_norm]
                if song_id in seen_song_ids:
                    continue
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue
                composite, ratio, partial, dice = _compute_composite_score(query_ascii, full_norm, raw_score)
                coverage = _get_symmetric_coverage(query_ascii, full_norm)
                guard_score = _apply_coverage_guard(composite, query_ascii, full_norm)
                final_score = min(guard_score * TITLE_SCORE_BOOST, 100.0)

                reason = ""
                if guard_score <= 0:
                    reason = "coverage"
                    _bump_phase("title_pinyin_full", "reject_coverage")
                elif final_score < threshold:
                    reason = "threshold"
                    _bump_phase("title_pinyin_full", "reject_threshold")
                else:
                    _bump_phase("title_pinyin_full", "accepted")
                    seen_song_ids.add(song_id)
                    results.append(SongQueryResult(
                        song_id=song_id,
                        title=title,
                        match_type=MatchType.PINYIN_FULL,
                        match_score=final_score,
                        matched_text=title,
                        song_data=song_data,
                    ))
                _log_match_decision(
                    strategy="title_pinyin_full",
                    trace_id=trace_id,
                    query=query,
                    candidate=title,
                    raw_score=raw_score,
                    composite_score=composite,
                    guard_score=guard_score,
                    final_score=final_score,
                    threshold=threshold,
                    norm_query=query_ascii,
                    norm_candidate=full_norm,
                    coverage=coverage,
                    ratio=ratio,
                    partial=partial,
                    dice=dice,
                    reason=reason,
                )

        for song_id, original_alias in search_index.alias_pinyin_initials_norm.get(query_ascii, ()):
            if song_id in seen_song_ids:
                continue
            _bump_phase("alias_pinyin_initials", "candidates")
            song_data = await _song_by_id(song_id, game_code)
            if not song_data:
                continue
            alias_norm = _normalize_for_matching(original_alias)
            ambiguity = _alias_ambiguity_penalty(alias_norm, alias_freq.get(alias_norm, 1))
            final_score = 95.0 * ALIAS_SCORE_PENALTY * ambiguity
            coverage = _get_symmetric_coverage(query_ascii, query_ascii)
            reason = ""
            if ambiguity <= 0:
                reason = "ambiguity"
                _bump_phase("alias_pinyin_initials", "reject_ambiguity")
            elif final_score < threshold:
                reason = "threshold"
                _bump_phase("alias_pinyin_initials", "reject_threshold")
            else:
                _bump_phase("alias_pinyin_initials", "accepted")
                seen_song_ids.add(song_id)
                results.append(SongQueryResult(
                    song_id=song_id,
                    title=_song_title(song_data, game_code),
                    match_type=MatchType.PINYIN_INITIALS,
                    match_score=final_score,
                    matched_text=original_alias,
                    song_data=song_data,
                ))
            _log_match_decision(
                strategy="alias_pinyin_initials",
                trace_id=trace_id,
                query=query,
                candidate=original_alias,
                raw_score=95.0,
                composite_score=95.0,
                guard_score=95.0,
                final_score=final_score,
                threshold=threshold,
                norm_query=query_ascii,
                norm_candidate=query_ascii,
                coverage=coverage,
                ratio=95.0,
                partial=95.0,
                dice=0.0,
                alias_freq=alias_freq.get(alias_norm, 1),
                reason=reason,
            )

        alias_full_pinyin_norm = search_index.alias_full_pinyin_norm
        if alias_full_pinyin_norm:
            py_alias_matches = process.extract(
                query_ascii,
                alias_full_pinyin_norm.keys(),
                scorer=fuzz.partial_ratio,
                limit=MAX_RESULTS * 4,
                score_cutoff=effective_threshold,
            )
            for full_norm, raw_score, _ in py_alias_matches:
                _bump_phase("alias_pinyin_full", "candidates")
                song_id, original_alias = alias_full_pinyin_norm[full_norm]
                if song_id in seen_song_ids:
                    continue
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue
                alias_norm = _normalize_for_matching(original_alias)
                ambiguity = _alias_ambiguity_penalty(alias_norm, alias_freq.get(alias_norm, 1))
                composite, ratio, partial, dice = _compute_composite_score(query_ascii, full_norm, raw_score)
                coverage = _get_symmetric_coverage(query_ascii, full_norm)
                guard_score = _apply_coverage_guard(composite, query_ascii, full_norm)
                final_score = guard_score * ALIAS_SCORE_PENALTY * ambiguity

                reason = ""
                if ambiguity <= 0:
                    reason = "ambiguity"
                    _bump_phase("alias_pinyin_full", "reject_ambiguity")
                elif guard_score <= 0:
                    reason = "coverage"
                    _bump_phase("alias_pinyin_full", "reject_coverage")
                elif final_score < threshold:
                    reason = "threshold"
                    _bump_phase("alias_pinyin_full", "reject_threshold")
                else:
                    _bump_phase("alias_pinyin_full", "accepted")
                    seen_song_ids.add(song_id)
                    results.append(SongQueryResult(
                        song_id=song_id,
                        title=_song_title(song_data, game_code),
                        match_type=MatchType.PINYIN_FULL,
                        match_score=final_score,
                        matched_text=original_alias,
                        song_data=song_data,
                    ))
                _log_match_decision(
                    strategy="alias_pinyin_full",
                    trace_id=trace_id,
                    query=query,
                    candidate=original_alias,
                    raw_score=raw_score,
                    composite_score=composite,
                    guard_score=guard_score,
                    final_score=final_score,
                    threshold=threshold,
                    norm_query=query_ascii,
                    norm_candidate=full_norm,
                    coverage=coverage,
                    ratio=ratio,
                    partial=partial,
                    dice=dice,
                    alias_freq=alias_freq.get(alias_norm, 1),
                    reason=reason,
                )

    # ── 4. 简体化归一匹配（仅含 CJK 查询，补充召回）──────────────────────────
    if query_has_cjk:
        query_simplified = _normalize_for_matching(_normalize_to_simplified(query_norm))
        simplified_scorer = _get_fuzzy_scorer(query_simplified)

        simplified_title_choices = search_index.simplified_title_choices
        if simplified_title_choices and query_simplified:
            simp_title_matches = process.extract(
                query_simplified,
                simplified_title_choices.keys(),
                scorer=simplified_scorer,
                limit=MAX_RESULTS * 3,
                score_cutoff=effective_threshold,
            )
            for simp_norm, raw_score, _ in simp_title_matches:
                _bump_phase("title_simplified", "candidates")
                song_id, original_title = simplified_title_choices[simp_norm]
                if song_id in seen_song_ids:
                    continue
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue
                composite, ratio, partial, dice = _compute_composite_score(query_simplified, simp_norm, raw_score)
                coverage = _get_symmetric_coverage(query_simplified, simp_norm)
                guard_score = _apply_coverage_guard(composite, query_simplified, simp_norm)
                final_score = min(guard_score * TITLE_SCORE_BOOST, 100.0)

                reason = ""
                if guard_score <= 0:
                    reason = "coverage"
                    _bump_phase("title_simplified", "reject_coverage")
                elif final_score < threshold:
                    reason = "threshold"
                    _bump_phase("title_simplified", "reject_threshold")
                else:
                    _bump_phase("title_simplified", "accepted")
                    seen_song_ids.add(song_id)
                    results.append(SongQueryResult(
                        song_id=song_id,
                        title=original_title,
                        match_type=MatchType.SIMPLIFIED,
                        match_score=final_score,
                        matched_text=original_title,
                        song_data=song_data,
                    ))
                _log_match_decision(
                    strategy="title_simplified",
                    trace_id=trace_id,
                    query=query,
                    candidate=original_title,
                    raw_score=raw_score,
                    composite_score=composite,
                    guard_score=guard_score,
                    final_score=final_score,
                    threshold=threshold,
                    norm_query=query_simplified,
                    norm_candidate=simp_norm,
                    coverage=coverage,
                    ratio=ratio,
                    partial=partial,
                    dice=dice,
                    reason=reason,
                )

        simplified_alias_choices = search_index.simplified_alias_choices
        if simplified_alias_choices and query_simplified:
            simp_alias_matches = process.extract(
                query_simplified,
                simplified_alias_choices.keys(),
                scorer=simplified_scorer,
                limit=MAX_RESULTS * 4,
                score_cutoff=effective_threshold,
            )
            for simp_norm, raw_score, _ in simp_alias_matches:
                _bump_phase("alias_simplified", "candidates")
                song_id, original_alias = simplified_alias_choices[simp_norm]
                if song_id in seen_song_ids:
                    continue
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue
                alias_norm = _normalize_for_matching(original_alias)
                ambiguity = _alias_ambiguity_penalty(alias_norm, alias_freq.get(alias_norm, 1))
                composite, ratio, partial, dice = _compute_composite_score(query_simplified, simp_norm, raw_score)
                coverage = _get_symmetric_coverage(query_simplified, simp_norm)
                guard_score = _apply_coverage_guard(composite, query_simplified, simp_norm)
                final_score = guard_score * ALIAS_SCORE_PENALTY * ambiguity

                reason = ""
                if ambiguity <= 0:
                    reason = "ambiguity"
                    _bump_phase("alias_simplified", "reject_ambiguity")
                elif guard_score <= 0:
                    reason = "coverage"
                    _bump_phase("alias_simplified", "reject_coverage")
                elif final_score < threshold:
                    reason = "threshold"
                    _bump_phase("alias_simplified", "reject_threshold")
                else:
                    _bump_phase("alias_simplified", "accepted")
                    seen_song_ids.add(song_id)
                    results.append(SongQueryResult(
                        song_id=song_id,
                        title=_song_title(song_data, game_code),
                        match_type=MatchType.SIMPLIFIED,
                        match_score=final_score,
                        matched_text=original_alias,
                        song_data=song_data,
                    ))
                _log_match_decision(
                    strategy="alias_simplified",
                    trace_id=trace_id,
                    query=query,
                    candidate=original_alias,
                    raw_score=raw_score,
                    composite_score=composite,
                    guard_score=guard_score,
                    final_score=final_score,
                    threshold=threshold,
                    norm_query=query_simplified,
                    norm_candidate=simp_norm,
                    coverage=coverage,
                    ratio=ratio,
                    partial=partial,
                    dice=dice,
                    alias_freq=alias_freq.get(alias_norm, 1),
                    reason=reason,
                )

    for phase, stat in phase_stats.items():
        if trace_id:
            logger.debug(
                f"[fuzzy][trace={trace_id}] phase={phase} candidates={stat['candidates']} accepted={stat['accepted']} "
                f"reject_coverage={stat['reject_coverage']} reject_ambiguity={stat['reject_ambiguity']} "
                f"reject_threshold={stat['reject_threshold']}"
            )

    results.sort(key=lambda x: x.match_score, reverse=True)
    if trace_id:
        logger.debug(f"[fuzzy][trace={trace_id}] done results={len(results)}")
    return results[:MAX_RESULTS]


async def search_song(
    query: str | int,
    _prefix_retry: bool = True,
    _trace_id: str | None = None,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult]:
    """统一乐曲搜索入口。

    搜索优先级：
    1. 如果是整数，尝试 ID 精确匹配（命中即返回）
    2. 标题精确匹配（命中即返回，不再查别名）
    3. 别名精确匹配（命中即返回）
    4. 归一化精确匹配（NFKC / 简繁 / 分隔符）
    5. 罗马字/ASCII transliteration 别名
    6. BM25 候选召回（多词 title / artist / alias 查询）
    7. 模糊匹配（标题 + 别名 + 拼音 + 简体化归一）
    8. 可选本地 embedding 兜底（默认关闭）

    Args:
        query: 查询字符串或乐曲 ID
        game_code: ``maimai`` 或 ``chunithm``

    Returns:
        匹配的乐曲列表（按相关性排序）
    """
    gc = _normalize_game_code(game_code)
    trace_id = _trace_id or _build_trace_id(query)

    def _finish(results: list[SongQueryResult]) -> list[SongQueryResult]:
        return results

    logger.debug(
        f"[search][trace={trace_id}] game={gc} query={query!r} prefix_retry={_prefix_retry}"
    )

    # 1. 尝试 ID 匹配（如果是数字）
    if isinstance(query, int):
        result = await query_song_by_id(query, game_code=gc)
        if result:
            return _finish([result])
    elif isinstance(query, str) and query.isdigit():
        result = await query_song_by_id(int(query), game_code=gc)
        if result:
            return _finish([result])

    query_str = str(query).strip()
    if not query_str:
        return _finish([])

    # 2. 标题精确匹配（命中即返回，不再查别名）
    exact_title_results = await query_song_by_title_exact(query_str, game_code=gc)
    if exact_title_results:
        return _finish(exact_title_results)

    # 3. 别名精确匹配
    exact_alias_results = await query_song_by_alias_exact(query_str, game_code=gc)
    if exact_alias_results:
        return _finish(exact_alias_results)

    normalized_exact_results = await _query_song_by_normalized_exact(query_str, game_code=gc)
    if normalized_exact_results:
        return _finish(normalized_exact_results)

    # 5. 罗马字/ASCII transliteration 别名
    romaji_results = await _query_song_romaji(query_str, game_code=gc)
    if romaji_results:
        return _finish(romaji_results)

    # 6. BM25 候选召回（短 ASCII 查询保留词边界 fuzzy 保护）
    if _has_multiple_search_terms(query_str):
        bm25_results = await _query_song_bm25(query_str, game_code=gc)
        if bm25_results:
            return _finish(bm25_results)

    # 7. 模糊匹配
    fuzzy_results = await query_song_fuzzy(query_str, trace_id=trace_id, game_code=gc)
    if fuzzy_results:
        return _finish(fuzzy_results)

    # 8. 可选本地 embedding 兜底（默认关闭）
    embedding_results = await _query_song_embedding(query_str, game_code=gc)
    if embedding_results:
        return _finish(embedding_results)

    # 9. 无结果时兜底：剥离难度/版本前缀重试一次（避免误伤真实歌名）
    if _prefix_retry and isinstance(query, str):
        stripped_query = _strip_query_prefix_for_retry(query_str)
        if stripped_query:
            logger.debug(
                f"[search][trace={trace_id}] prefix_retry triggered "
                f"original={query_str!r} stripped={stripped_query!r}"
            )
            retry_results = await search_song(
                stripped_query,
                _prefix_retry=False,
                _trace_id=trace_id,
                game_code=gc,
            )
            return _finish(retry_results)

    return _finish([])


async def get_song_with_difficulty(
    song_id: int,
    song_type: str = "standard",
    level_index: int = 3,
    game_code: str = "maimai",
) -> dict | None:
    """获取乐曲数据并附加指定难度信息。

    Args:
        song_id: 乐曲 ID
        song_type: 谱面类型 ("standard" / "dx")
        level_index: 难度索引 (0=Basic, 1=Advanced, 2=Expert, 3=Master, 4=Re:Master)

    Returns:
        乐曲数据（包含 target_difficulty 字段）
    """
    gc = _normalize_game_code(game_code)
    return await _repository().get_song_with_difficulty(
        gc,
        song_id,
        song_type=song_type,
        level_index=level_index,
    )


async def get_song_aliases(
    song_query: str | int,
    *,
    game_code: str = "maimai",
) -> dict | None:
    """获取歌曲的所有别名。

    Args:
        song_query: 查询字符串或乐曲 ID
        game_code: ``maimai`` 或 ``chunithm``

    Returns:
        {
            "song_id": int,
            "title": str,
            "aliases": list[str]  # maimai: 含 LXNS/柚子查/dxrating；chunithm: 来自 DB 别名表或内存
        }
    """
    gc = _normalize_game_code(game_code)
    logger.debug(f"查询歌曲别名: {song_query} (game={gc})")

    results = await search_song(song_query, game_code=gc)

    if not results:
        logger.debug(f"未找到歌曲: {song_query}")
        return None

    result = results[0]
    song_id = result.song_id
    title = result.title

    aliases = await _repository().get_song_aliases_for_song_id(gc, song_id)

    logger.info(f"找到歌曲 [{song_id}] {title} 的 {len(aliases)} 个别名 (game={gc})")

    return {
        "song_id": song_id,
        "title": title,
        "aliases": aliases,
    }
