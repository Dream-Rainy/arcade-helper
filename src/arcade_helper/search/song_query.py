"""乐曲查询服务：支持 ID、标题、别名的模糊查询。

提供统一的查询接口，返回匹配的乐曲数据列表。
"""

from contextvars import ContextVar
from dataclasses import replace
import asyncio
from weakref import WeakSet
from typing import Any, Protocol
from collections import Counter
from time import monotonic
from uuid import uuid4
import logging

from rapidfuzz import fuzz, process

from arcade_helper.core.errors import ArcadeHelperError
from arcade_helper.core.song import SongData
from arcade_helper.core.song_identity import parse_chart_title
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
from .index import _SearchDocument, _SearchIndex, _build_search_index
from .normalization import (
    GameCode,
    _append_choice,
    _ascii_tokens,
    _get_pinyin,
    _has_cjk,
    _normalize_for_matching,
    _normalize_game_code,
    _unique_texts,
)
from .scoring import (
    BM25_MIN_QUERY_COVERAGE,
    _alias_ambiguity_penalty,
    _apply_coverage_guard,
    _bm25_query_terms,
    _bm25_query_coverage,
    _bm25_score,
    _cap_weak_compact_match_score,
    _compute_composite_score,
    _get_effective_threshold,
    _get_fuzzy_scorer,
    _get_symmetric_coverage,
    _has_multiple_search_terms,
    _is_short_ascii_query,
    _literal_query_covered,
    _short_text_score,
    is_short_song_query,
)

logger = logging.getLogger(__name__)

class SongSearchRepository(Protocol):
    async def get_song_by_id(self, game_code: str, song_id: int) -> SongData | None:
        """Return one song by game/id."""

    async def get_songs_by_ids(self, game_code: str, song_ids: list[int]) -> dict[int, SongData]:
        """Optional full-song batch read; older repositories use single reads."""

    async def load_all_songs(self, game_code: str) -> dict[int, SongData]:
        """Return all songs for a game."""

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        """Return song id to title mapping for a game."""

    async def query_title_exact(self, game_code: str, title: str) -> list[tuple[int, str]]:
        """Optional SQL fast path; older repositories fall back to their index."""

    async def load_search_songs(self, game_code: str) -> dict[int, SongData]:
        """Optional text-only metadata; older repositories may return full songs."""

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


class _SearchCache:
    def __init__(self):
        self.aliases = {}
        self.frequencies = {}
        self.alias_times = {}
        self.indexes = {}
        self.index_times = {}
        self.generations = {}
        self.epochs = Counter()
        self.builds = {}

    def clear(self, game_code=None):
        games = {key[0] for key in self.builds} | set(self.generations)
        if game_code is not None:
            games = {_normalize_game_code(game_code)}
        for game in games:
            self.epochs[game] += 1
        for mapping in (self.aliases, self.frequencies, self.alias_times, self.indexes, self.index_times):
            for key in list(mapping):
                if game_code is None or key[0] == _normalize_game_code(game_code):
                    del mapping[key]


_cache_var: ContextVar[_SearchCache | None] = ContextVar("song_search_cache", default=None)
_variant_var: ContextVar[tuple[str, ...] | None] = ContextVar("song_search_variant", default=None)
_candidate_var: ContextVar[dict | None] = ContextVar("song_search_candidates", default=None)
_live_caches: WeakSet = WeakSet()
_default_cache = _SearchCache()
_live_caches.add(_default_cache)


def _cache() -> _SearchCache:
    return _cache_var.get() or _default_cache


def _matches_variant(song: SongData) -> bool:
    required = _variant_var.get()
    if required is None:
        return True
    actual = song.chart_variant if "chart_variant" in song.model_fields_set else parse_chart_title(song.title)[1]
    return actual is not None and tuple(actual) == required


class SongSearchService:
    """Search with instance-owned caches and optional repository revisions."""

    def __init__(self, repository: SongSearchRepository) -> None:
        self.repository = repository
        self._cache = _SearchCache()
        _live_caches.add(self._cache)
        self._locks = {}

    async def _run(self, method, *args, game_code="maimai", hydrate=True, **kwargs):
        game_code = _normalize_game_code(game_code)
        async with self._locks.setdefault(game_code, asyncio.Lock()):
            token = _repository_var.set(self.repository)
            cache_token = _cache_var.set(self._cache)
            markers = parse_chart_title(args[0])[1] if args and isinstance(args[0], str) else ()
            variant_token = _variant_var.set(markers or None)
            candidate_token = _candidate_var.set(None)
            try:
                for _ in range(3):
                    before = await _ensure_generation(game_code)
                    _candidate_var.set({"full": {}, "light": {}, "deferred": set(), "hydrate": hydrate}
                                       if method in (search_song, query_song_fuzzy) else None)
                    # A source-confirmed literal bracket title must remain searchable.
                    literal = False
                    if markers:
                        songs = await _search_songs(_normalize_game_code(game_code))
                        literal = any(
                            song.title.strip().casefold() == args[0].strip().casefold()
                            and song.chart_variant == () and "chart_variant" in song.model_fields_set
                            for song in songs.values()
                        )
                    _variant_var.set(None if literal else markers or None)
                    result = await method(*args, game_code=game_code, **kwargs)
                    if hydrate:
                        result = await _hydrate_candidates(result, game_code)
                    if await _generation() == before:
                        return result
                    self._cache.clear(game_code)
                raise ArcadeHelperError("Catalog changed repeatedly during search; retry")
            finally:
                _candidate_var.reset(candidate_token)
                _variant_var.reset(variant_token)
                _cache_var.reset(cache_token)
                _repository_var.reset(token)

    async def search_song(self, query: str | int, *, game_code: str = "maimai") -> list[SongQueryResult]:
        return await self._run(search_song, query, game_code=game_code)

    async def search_candidates(self, query: str | int, *, game_code: str = "maimai") -> list[SongQueryResult]:
        """Text-only candidates with metadata, without local vectors or payload hydration."""
        return await self._run(search_song, query, game_code=game_code, hydrate=False, allow_embedding=False)

    async def query_song_by_id(self, song_id: int, *, game_code: str = "maimai") -> SongQueryResult | None:
        return await self._run(query_song_by_id, song_id, game_code=game_code)

    async def query_song_by_title_exact(self, title: str, *, game_code: str = "maimai") -> list[SongQueryResult]:
        return await self._run(query_song_by_title_exact, title, game_code=game_code)

    async def query_song_by_alias_exact(self, alias: str, *, game_code: str = "maimai") -> list[SongQueryResult]:
        return await self._run(query_song_by_alias_exact, alias, game_code=game_code)

    async def query_song_fuzzy(self, query: str, threshold: float = 80, *, game_code: str = "maimai") -> list[SongQueryResult]:
        return await self._run(query_song_fuzzy, query, threshold=threshold, game_code=game_code)

    async def get_song_with_difficulty(self, song_id: int, song_type: str = "standard", level_index: int = 3, *, game_code: str = "maimai") -> dict | None:
        return await self._run(get_song_with_difficulty, song_id, song_type=song_type, level_index=level_index, game_code=game_code)

    async def get_song_aliases(self, song_query: str | int, *, game_code: str = "maimai") -> dict | None:
        return await self._run(get_song_aliases, song_query, game_code=game_code)

    def invalidate_alias_cache(self, game_code: str | None = None) -> None:
        self._cache.clear(game_code)

    async def _warm_search_index(self, game_code: str) -> None:
        """Prewarm without holding the foreground game lock."""
        token = _repository_var.set(self.repository)
        cache_token = _cache_var.set(self._cache)
        variant_token = _variant_var.set(None)
        candidate_token = _candidate_var.set(None)
        try:
            await _get_search_index(game_code, max_age=ALIAS_CACHE_TTL_SECONDS - 30)
        finally:
            _candidate_var.reset(candidate_token)
            _variant_var.reset(variant_token)
            _cache_var.reset(cache_token)
            _repository_var.reset(token)

    async def _stop_index_builds(self) -> None:
        tasks = [entry[2] for entry in self._cache.builds.values()]
        self._cache.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _repository() -> SongSearchRepository:
    repository = _repository_var.get()
    if repository is None:
        raise SongSearchNotConfiguredError(
            "Song search requires SongSearchService or a configured repository context"
        )
    return repository


async def _search_songs(game_code: str) -> dict[int, SongData]:
    repository = _repository()
    load = getattr(repository, "load_search_songs", repository.load_all_songs)
    return await load(_normalize_game_code(game_code))


async def _song_index(game_code: str) -> dict[int, str]:
    if _variant_var.get() is not None:
        return {key: song.title for key, song in (await _search_songs(game_code)).items() if _matches_variant(song)}
    return await _repository().load_song_index(_normalize_game_code(game_code))


async def _song_by_id(song_id: int, game_code: str) -> SongData | None:
    candidates = _candidate_var.get()
    if candidates is not None and song_id in candidates["full"]:
        song = candidates["full"][song_id]
    elif candidates is not None and song_id in candidates["light"]:
        song = candidates["light"][song_id]
        candidates["deferred"].add(song_id)
    elif candidates is not None and not candidates["hydrate"]:
        repository = _repository()
        batch = getattr(repository, "get_search_songs_by_ids", None)
        songs = await batch(game_code, [song_id]) if batch else await _search_songs(game_code)
        candidates["light"].update(songs)
        song = songs.get(song_id)
    else:
        song = await _repository().get_song_by_id(_normalize_game_code(game_code), song_id)
        if candidates is not None:
            candidates["full"][song_id] = song
    return song if song is not None and _matches_variant(song) else None


async def _hydrate_candidates(result, game_code):
    candidates = _candidate_var.get()
    if candidates is None or not isinstance(result, list):
        return result
    ids = list(dict.fromkeys(r.song_id for r in result if r.song_id in candidates["deferred"]))
    if not ids:
        return result
    repository = _repository()
    batch = getattr(repository, "get_songs_by_ids", None)
    if batch is not None:
        songs = await batch(game_code, ids)
    else:
        songs = {sid: await repository.get_song_by_id(game_code, sid) for sid in ids}
    candidates["full"].update(songs)
    return [replace(r, song_data=songs[r.song_id], title=songs[r.song_id].title)
            if r.song_id in ids else r for r in result
            if r.song_id not in ids or (songs.get(r.song_id) is not None and _matches_variant(songs[r.song_id]))]


async def _generation():
    repository = _repository()
    revision = getattr(repository, "get_revision", None)
    value = await revision() if revision else None
    identities = getattr(repository, "identities", None)
    if identities is None:
        identities = getattr(getattr(repository, "service", None), "identities", None)
    return (id(repository), getattr(identities, "_db", getattr(repository, "_db", None)), value)


async def _ensure_generation(game_code):
    generation = await _generation()
    cache = _cache()
    if cache.generations.get(game_code) != generation:
        cache.clear(game_code)
        cache.generations[game_code] = generation
    return generation


def _song_title(song_data: SongData, game_code: str) -> str:
    return str(getattr(song_data, "title", "") or "")


def _build_trace_id(query: str | int) -> str:
    q = str(query)
    q = q.replace("\n", " ").replace("\r", " ")
    q = q[:16]
    return f"{q}:{uuid4().hex[:8]}"


def invalidate_alias_cache(game_code: str | None = None) -> None:
    """Compatibility invalidation across live services without sharing cached data."""
    for cache in list(_live_caches):
        cache.clear(game_code)


async def _get_alias_cache(
    game_code: str = "maimai", force_refresh: bool = False,
) -> tuple[dict[str, list[tuple[int, str]]], Counter[str]]:
    index = await _get_search_index(game_code, max_age=0 if force_refresh else ALIAS_CACHE_TTL_SECONDS)
    return index.alias_choices_norm, index.alias_freq


async def _get_search_index(game_code: str = "maimai", *, max_age=ALIAS_CACHE_TTL_SECONDS) -> _SearchIndex:
    gc = _normalize_game_code(game_code)
    cache = _cache()
    cache_key = (gc, _variant_var.get())
    for _ in range(3):
        generation = await _ensure_generation(gc)
        epoch = cache.epochs[gc]
        cached = cache.indexes.get(cache_key)
        if cached is not None and monotonic() - cache.index_times.get(cache_key, 0) < max_age:
            candidates = _candidate_var.get()
            if candidates is not None:
                candidates["light"].update(cached.songs)
            return cached
        entry = cache.builds.get(cache_key)
        if entry is None or entry[:2] != (generation, epoch):
            task = asyncio.create_task(_prepare_search_index(gc, cache_key, generation, epoch))
            entry = (generation, epoch, task)
            cache.builds[cache_key] = entry
        task = entry[2]
        try:
            index = await task
        finally:
            if task.done() and cache.builds.get(cache_key) is entry:
                del cache.builds[cache_key]
        if index is not None:
            candidates = _candidate_var.get()
            if candidates is not None:
                candidates["light"].update(index.songs)
            return index
    raise ArcadeHelperError("Catalog changed repeatedly while building search index; retry")


async def _prepare_search_index(gc, cache_key, generation, epoch):
    cache = _cache()
    songs = {sid: song for sid, song in (await _search_songs(gc)).items() if _matches_variant(song)}
    titles = {sid: song.title for sid, song in songs.items()} if songs else await _song_index(gc)
    # A default empty aliases field is not evidence that an older repository
    # supplied its separate aliases. Explicitly populated fields avoid that read.
    if songs and all("aliases" in song.model_fields_set for song in songs.values()):
        records = [(sid, text) for sid, song in songs.items() for text in song.aliases]
    else:
        records = await _repository().load_alias_records(gc)
    # Copy only text metadata. The worker owns every mutable input it receives.
    owned = {sid: (song.artist, tuple(song.aliases))
             for sid, song in songs.items()}
    index = await asyncio.to_thread(_build_search_index, owned, dict(titles), list(records))
    if await _generation() != generation or cache.epochs[gc] != epoch:
        return None
    # Reuse the event-loop-owned lightweight models; the worker only saw text.
    index = replace(index, songs=songs)
    now = monotonic()
    cache.indexes[cache_key] = index
    cache.index_times[cache_key] = now
    cache.aliases[cache_key] = index.alias_choices_norm
    cache.frequencies[cache_key] = index.alias_freq
    cache.alias_times[cache_key] = now
    logger.debug(f"[search_index][{gc}] reload titles={len(index.index)} aliases={len(index.alias_choices_norm)}")
    return index


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
    return await _song_by_id(song_id, game_code)


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
    query = getattr(_repository(), "query_title_exact", None)
    matches = await query(_normalize_game_code(game_code), title) if query is not None else [
        (sid, text) for sid, text in (await _song_index(game_code)).items() if text.lower() == title_lower
    ]

    for song_id, song_title in matches:
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

    for song_id, alias_original in search_index.alias_choices_norm.get(query_norm, ()):
        if song_id not in seen_song_ids:
            song_data = await _song_by_id(song_id, game_code)
            if song_data:
                seen_song_ids.add(song_id)
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

    query_counts = Counter(query_terms)
    scored: list[tuple[float, float, _SearchDocument]] = []
    effective_threshold = _get_effective_threshold(_normalize_for_matching(query), threshold)
    total_docs = len(search_index.documents)
    for document in search_index.documents.values():
        raw_score = _bm25_score(
            query_counts,
            document,
            total_docs=total_docs,
            doc_freq=search_index.bm25_doc_freq,
            avg_len=search_index.bm25_avg_len,
        )
        if raw_score > 0:
            if _bm25_query_coverage(query_counts, document) < BM25_MIN_QUERY_COVERAGE:
                continue
            final_score = min(95.0, 60.0 + raw_score * 15.0) * document.confidence
            if final_score >= effective_threshold:
                scored.append((raw_score, final_score, document))

    if not scored:
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[SongQueryResult] = []
    seen_song_ids: set[int] = set()
    for _, final_score, document in scored[:BM25_TOP_CANDIDATES]:
        if document.song_id in seen_song_ids:
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


def _bm25_is_confident(results: list[SongQueryResult]) -> bool:
    if len(results) <= 1:
        return bool(results)
    return (results[0].match_score - results[1].match_score) >= 8.0


async def _query_song_embedding(
    query: str,
    *,
    game_code: str = "maimai",
) -> list[SongQueryResult] | None:
    if not _truthy_env("SONG_SEARCH_EMBEDDING_ENABLED"):
        return None
    path = _embedding_path()
    if path is None:
        return None

    gc = _normalize_game_code(game_code)
    index = _load_embedding_index(path)
    query_vector = index.query_vectors_by_game.get(gc, {}).get(_normalize_for_matching(query))
    if query_vector is None:
        return None

    scored: list[tuple[float, int]] = []
    validated = False
    for song_id, song_vector in index.song_vectors_by_game.get(gc, {}).items():
        if len(query_vector) != len(song_vector):
            continue
        validated = True
        similarity = _cosine_similarity(query_vector, song_vector)
        score = similarity * 100.0
        if score >= _embedding_threshold():
            scored.append((score, song_id))
    if not validated:
        return None

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


async def _query_song_alias_fuzzy(
    query: str,
    query_norm: str,
    search_index: _SearchIndex,
    threshold: float,
    *,
    game_code: str,
    trace_id: str,
) -> tuple[list[SongQueryResult], list[SongQueryResult]]:
    effective_threshold = _get_effective_threshold(query_norm, threshold)
    for romaji in (True, False):
        if romaji and (not query_norm.isascii() or _is_short_ascii_query(query_norm)):
            continue
        choices = search_index.romaji_alias_song_ids if romaji else search_index.alias_choices_norm
        matches = process.extract(
            query_norm, choices.keys(),
            scorer=fuzz.partial_ratio if romaji else _get_fuzzy_scorer(query_norm),
            limit=MAX_RESULTS * (3 if romaji else 4), score_cutoff=effective_threshold,
        )
        results: list[SongQueryResult] = []
        seen: set[int] = set()
        for alias_norm, raw_score, _ in matches:
            for song_id, original_alias in search_index.alias_choices_norm[alias_norm]:
                if song_id in seen or (romaji and song_id not in choices[alias_norm]):
                    continue
                if not _literal_query_covered(query, original_alias):
                    continue
                composite, ratio, partial, dice = _compute_composite_score(query_norm, alias_norm, raw_score)
                ambiguity = 1.0 if romaji else _alias_ambiguity_penalty(alias_norm, search_index.alias_freq.get(alias_norm, 1))
                guard_score = _apply_coverage_guard(raw_score if romaji else composite, query_norm, alias_norm)
                final_score = min(96.0, guard_score * 0.90) if romaji else guard_score * ALIAS_SCORE_PENALTY * ambiguity
                if not romaji:
                    final_score = _cap_weak_compact_match_score(final_score, query_norm, original_alias)
                reason = "ambiguity" if ambiguity <= 0 else "coverage" if guard_score <= 0 else "threshold" if final_score < threshold else ""
                _log_match_decision(
                    strategy="romaji" if romaji else "alias_raw", trace_id=trace_id,
                    query=query, candidate=original_alias, raw_score=raw_score,
                    composite_score=composite, guard_score=guard_score, final_score=final_score,
                    threshold=threshold, norm_query=query_norm, norm_candidate=alias_norm,
                    coverage=_get_symmetric_coverage(query_norm, alias_norm),
                    ratio=ratio, partial=partial, dice=dice,
                    alias_freq=search_index.alias_freq.get(alias_norm, 1), reason=reason,
                )
                if reason:
                    continue
                song_data = await _song_by_id(song_id, game_code)
                if not song_data:
                    continue
                seen.add(song_id)
                results.append(SongQueryResult(
                    song_id=song_id, title=_song_title(song_data, game_code),
                    match_type=MatchType.ROMAJI if romaji else MatchType.FUZZY_ALIAS,
                    match_score=final_score, matched_text=original_alias, song_data=song_data,
                ))
                if romaji and len(results) >= MAX_RESULTS:
                    return results, []
        if romaji and results:
            return results, []
        if not romaji:
            return [], results
    return [], []


async def _query_song_short(
    query_norm: str, search_index: _SearchIndex, threshold: float, *, game_code: str,
) -> list[SongQueryResult]:
    best: dict[int, tuple[float, MatchType, str]] = {}

    def add(song_id, score, match_type, text):
        current = best.get(song_id)
        if score >= threshold and (current is None or score > current[0]):
            best[song_id] = (score, match_type, text)

    for song_id, title in search_index.index.items():
        add(song_id, _short_text_score(query_norm, title), MatchType.FUZZY_TITLE, title)
    for alias_norm, entries in search_index.alias_choices_norm.items():
        for song_id, alias in entries:
            score = _short_text_score(query_norm, alias) * ALIAS_SCORE_PENALTY
            score *= _alias_ambiguity_penalty(alias_norm, search_index.alias_freq.get(alias_norm, 1))
            add(song_id, score, MatchType.FUZZY_ALIAS, alias)
    if query_norm.isascii() and len(query_norm) >= 2:
        for choices, match_type, score in (
            (search_index.title_pinyin_initials_norm, MatchType.PINYIN_INITIALS, 98.0),
            (search_index.title_full_pinyin_norm, MatchType.PINYIN_FULL, 98.0),
            (search_index.alias_pinyin_initials_norm, MatchType.PINYIN_INITIALS, 95.0 * ALIAS_SCORE_PENALTY),
            (search_index.alias_full_pinyin_norm, MatchType.PINYIN_FULL, 95.0 * ALIAS_SCORE_PENALTY),
        ):
            for song_id, text in choices.get(query_norm, ()):
                add(song_id, score, match_type, text)
    results = []
    for song_id, (score, match_type, text) in sorted(best.items(), key=lambda row: row[1][0], reverse=True):
        song = await _song_by_id(song_id, game_code)
        if song:
            results.append(SongQueryResult(song_id, _song_title(song, game_code), match_type, score, text, song))
        if len(results) >= MAX_RESULTS:
            break
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
    if is_short_song_query(query):
        return await _query_song_short(query_norm, search_index, threshold, game_code=game_code)
    scorer = _get_fuzzy_scorer(query_norm)
    effective_threshold = _get_effective_threshold(query_norm, threshold)
    query_is_ascii = query_norm.isascii() and not _has_cjk(query_norm)

    if trace_id:
        logger.debug(
            f"[fuzzy][trace={trace_id}] start query={query!r} norm_query={query_norm!r} "
            f"threshold={threshold:.1f} effective_threshold={effective_threshold:.1f} "
            f"scorer={getattr(scorer, '__name__', str(scorer))}"
        )

    romaji_results, alias_results = await _query_song_alias_fuzzy(
        query, query_norm, search_index, threshold, game_code=game_code, trace_id=trace_id,
    )
    if romaji_results:
        return romaji_results

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
                if not _literal_query_covered(query, title):
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
    for result in alias_results:
        if result.song_id not in seen_song_ids:
            seen_song_ids.add(result.song_id)
            results.append(result)

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
                for song_id, title in title_full_pinyin_norm[full_norm]:
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
                for song_id, original_alias in alias_full_pinyin_norm[full_norm]:
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
    allow_embedding: bool = True,
) -> list[SongQueryResult]:
    """统一乐曲搜索入口。

    搜索优先级：
    1. 如果是整数，尝试 ID 精确匹配（命中即返回）
    2. 标题精确匹配（命中即返回，不再查别名）
    3. 别名精确匹配（命中即返回）
    4. 归一化精确匹配（NFKC / 简繁 / 分隔符）
    5. 模糊匹配（罗马字别名 + 标题 + 别名 + 拼音，共享归一化索引）
    6. BM25 候选召回（完整查询覆盖检查）
    7. 可选本地 embedding 兜底（默认关闭）

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

    # 1. 数字查询：并行尝试 ID 精确匹配与标题精确匹配，避免数字 ID 与数字曲名冲突
    #    （例如某曲目标题恰好是另一曲目的 ID）时标题匹配永远无法被触达。
    numeric_query: str | None = None
    if isinstance(query, int):
        numeric_query = str(query)
    elif isinstance(query, str) and query.isdigit():
        numeric_query = query

    if numeric_query is not None:
        id_result = await query_song_by_id(int(numeric_query), game_code=gc)
        title_results = await query_song_by_title_exact(numeric_query, game_code=gc)
        combined: list[SongQueryResult] = []
        seen_ids: set[int] = set()
        if id_result:
            combined.append(id_result)
            seen_ids.add(id_result.song_id)
        for r in title_results:
            if r.song_id not in seen_ids:
                combined.append(r)
                seen_ids.add(r.song_id)
        if combined:
            return _finish(combined)

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

    # 5. 模糊匹配（罗马字别名在共享别名匹配中优先处理）
    fuzzy_results = await query_song_fuzzy(query_str, trace_id=trace_id, game_code=gc)
    if fuzzy_results:
        return _finish(fuzzy_results)

    # 6. BM25 兜底召回；多候选差距太小时先让 embedding 接管。
    bm25_results: list[SongQueryResult] = []
    if not is_short_song_query(query_str) and _has_multiple_search_terms(query_str):
        bm25_results = await _query_song_bm25(query_str, game_code=gc)
        if _bm25_is_confident(bm25_results):
            return _finish(bm25_results)

    # 7. 可选本地 embedding 兜底（默认关闭）
    embedding_results = None if not allow_embedding or is_short_song_query(query_str) else await _query_song_embedding(query_str, game_code=gc)
    if embedding_results:
        return _finish(embedding_results)

    if bm25_results and embedding_results is None:
        return _finish(bm25_results)

    # 8. 无结果时兜底：剥离难度/版本前缀重试一次（避免误伤真实歌名）
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
                allow_embedding=allow_embedding,
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
