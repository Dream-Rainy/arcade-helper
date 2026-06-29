"""Provider-backed song catalog service."""

from __future__ import annotations

import asyncio
import traceback
import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from arcade_helper.core.errors import CatalogSyncError, StorageNotConfiguredError
from arcade_helper.core.song import SongData
from arcade_helper.search import SongQueryResult, SongSearchService


CatalogSyncStatus = Literal["success", "failed", "already_running"]


@dataclass(frozen=True)
class CatalogFetchResult:
    """Raw catalog data fetched from a provider."""

    game_code: str
    songs: dict[int, Any]
    provider: str
    data_version: int | None = None


@dataclass(frozen=True)
class CatalogSyncResult:
    """Result for one game catalog sync."""

    game_code: str
    provider: str
    operation: str
    status: CatalogSyncStatus
    started_at: datetime
    finished_at: datetime
    songs: dict[int, Any] | None = None
    data_version: int | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        """Whether the sync completed successfully."""
        return self.status == "success"


class SongCatalogService:
    """Unified song catalog entrypoint.

    Use this service for song lookup/search/sync. Dedicated game clients handle
    player/API calls; arcade-songs is only a generic catalog provider behind this
    service.
    """

    def __init__(
        self,
        *,
        providers: Any,
        song_store: Any | None = None,
        logger: Any | None = None,
    ) -> None:
        self.providers = providers
        self.songs = song_store or _default_song_store(logger)
        self.search = SongSearchService(self)
        self.arcade_songs = ArcadeSongsCatalogService(self)
        self.logger = logger
        self._sync_locks: dict[str, asyncio.Lock] = {}

    def _operation_for_catalog(self, game_code: str) -> str:
        if game_code == "maimai":
            return "maimai.catalog.songs"
        if game_code == "chunithm":
            return "chunithm.catalog.songs"
        return "arcade_songs.catalog.songs"

    def _lock_for_game(self, game_code: str) -> asyncio.Lock:
        lock = self._sync_locks.get(game_code)
        if lock is None:
            lock = asyncio.Lock()
            self._sync_locks[game_code] = lock
        return lock

    @staticmethod
    def _provider_method(provider: Any, method_path: str) -> Any:
        target = getattr(provider, "client", provider)
        for part in method_path.split("."):
            target = getattr(target, part)
        return target

    async def fetch_game_raw_data(
        self,
        game_code: str,
        *,
        current_data_version: int | None = None,
    ) -> CatalogFetchResult:
        """Fetch raw song catalog data from the selected provider without storing it."""
        operation = self._operation_for_catalog(game_code)
        route = self.providers.require(operation, game_code=game_code)

        if game_code not in {"maimai", "chunithm"}:
            songs = await self.arcade_songs.catalog(game_code)
            return CatalogFetchResult(
                game_code=game_code,
                songs=songs,
                provider=route.provider.name,
            )

        method = self._provider_method(route.provider, route.operation.method)
        if game_code == "maimai":
            songs = self._optional_song_store()
            result = await method(
                current_data_version=current_data_version,
                sync_map_data=songs.sync_maimai_maps if songs is not None else _noop_sync,
                sync_map_treasure_data=songs.sync_maimai_map_treasures if songs is not None else _noop_sync,
            )
            return CatalogFetchResult(
                game_code=game_code,
                songs=result.songs,
                provider=route.provider.name,
                data_version=result.data_version,
            )

        songs = await method()
        return CatalogFetchResult(
            game_code=game_code,
            songs=songs,
            provider=route.provider.name,
        )

    async def fetch_arcade_songs_sites(self) -> list[dict[str, Any]]:
        """Deprecated: use ``catalog.arcade_songs.sites()``."""
        warnings.warn(
            "fetch_arcade_songs_sites() is deprecated; use catalog.arcade_songs.sites()",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.arcade_songs.sites()

    async def fetch_arcade_songs_catalog(
        self,
        game_code: str,
        *,
        force_refresh: bool = False,
    ) -> dict[int, SongData]:
        """Deprecated: use ``catalog.arcade_songs.catalog(game_code)``."""
        warnings.warn(
            "fetch_arcade_songs_catalog() is deprecated; use catalog.arcade_songs.catalog(game_code)",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.arcade_songs.catalog(game_code, force_refresh=force_refresh)

    async def search_arcade_songs(
        self,
        game_code: str,
        query: str | int,
        *,
        force_refresh: bool = False,
    ) -> list[SongQueryResult]:
        """Deprecated: use ``catalog.arcade_songs.search(game_code, query)``."""
        warnings.warn(
            "search_arcade_songs() is deprecated; use catalog.arcade_songs.search(game_code, query)",
            DeprecationWarning,
            stacklevel=2,
        )
        return await self.arcade_songs.search(game_code, query, force_refresh=force_refresh)

    async def sync_game_from_remote(
        self,
        game_code: str,
        *,
        current_data_version: int | None = None,
    ) -> CatalogSyncResult:
        """Fetch a game catalog and persist it through the configured song store."""
        operation = self._operation_for_catalog(game_code)
        started_at = datetime.now()
        lock = self._lock_for_game(game_code)
        if lock.locked():
            return CatalogSyncResult(
                game_code=game_code,
                provider="",
                operation=operation,
                status="already_running",
                started_at=started_at,
                finished_at=datetime.now(),
            )

        async with lock:
            try:
                fetched = await self.fetch_game_raw_data(
                    game_code,
                    current_data_version=current_data_version,
                )
                await self._require_song_store().sync_songs(game_code, fetched.songs)
                return CatalogSyncResult(
                    game_code=game_code,
                    provider=fetched.provider,
                    operation=operation,
                    status="success",
                    started_at=started_at,
                    finished_at=datetime.now(),
                    songs=fetched.songs,
                    data_version=fetched.data_version,
                )
            except Exception as e:  # noqa: BLE001
                if self.logger is not None:
                    self.logger.error(f"[{game_code}] catalog sync failed: {e}")
                return CatalogSyncResult(
                    game_code=game_code,
                    provider="",
                    operation=operation,
                    status="failed",
                    started_at=started_at,
                    finished_at=datetime.now(),
                    error=CatalogSyncError(str(e)),
                )

    async def fetch_maimai_song_collections(self, song_id: int) -> list[dict[str, Any]]:
        """Fetch maimai collection data for one song from the provider."""
        route = self.providers.require("maimai.catalog.collections", game_code="maimai")
        method = self._provider_method(route.provider, route.operation.method)
        try:
            result = await method(song_id)
            return result if isinstance(result, list) else []
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            if self.logger is not None:
                self.logger.warning(f"获取乐曲 {song_id} 的收藏信息失败: {e}")
            return []

    async def sync_maimai_collections(self, song_id: int, collections: list[dict[str, Any]]) -> None:
        """Persist maimai collection data for one song."""
        await self._require_song_store().sync_maimai_collections(song_id, collections)

    async def sync_songs(self, game_code: str, song_data: dict[int, Any]) -> None:
        """Persist a game song catalog directly."""
        await self._require_song_store().sync_songs(game_code, song_data)

    async def get_song_by_id(self, game_code: str, song_id: int):
        """Return one song by game code and song id."""
        return await self._require_song_store().get_song_by_id(game_code, song_id)

    async def search_song(
        self,
        game_code: str,
        query: str | int,
    ) -> list[SongQueryResult]:
        """Search songs by id, title, alias, or fuzzy text."""
        return await self.search.search_song(query, game_code=game_code)

    async def query_song_by_id(self, game_code: str, song_id: int) -> SongQueryResult | None:
        """Search the catalog by exact song id."""
        return await self.search.query_song_by_id(song_id, game_code=game_code)

    async def query_song_by_title_exact(self, game_code: str, title: str) -> list[SongQueryResult]:
        """Search the catalog by exact title."""
        return await self.search.query_song_by_title_exact(title, game_code=game_code)

    async def query_song_by_alias_exact(self, game_code: str, alias: str) -> list[SongQueryResult]:
        """Search the catalog by exact alias."""
        return await self.search.query_song_by_alias_exact(alias, game_code=game_code)

    async def query_song_fuzzy(
        self,
        game_code: str,
        query: str,
        threshold: float = 80,
    ) -> list[SongQueryResult]:
        """Search the catalog by fuzzy title/alias matching."""
        return await self.search.query_song_fuzzy(query, threshold=threshold, game_code=game_code)

    async def load_all_songs(self, game_code: str):
        """Load all songs for a game from the configured song store."""
        return await self._require_song_store().load_all_songs(game_code)

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        """Load the song id to title index for a game."""
        return await self._require_song_store().load_song_index(game_code)

    async def query_alias_exact(self, game_code: str, alias_lower: str) -> list[tuple[int, str]]:
        """Return alias records matching a normalized alias exactly."""
        return await self._require_song_store().query_alias_exact(game_code, alias_lower)

    async def load_alias_records(self, game_code: str) -> list[tuple[int, str]]:
        """Load all alias records for a game."""
        return await self._require_song_store().load_alias_records(game_code)

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int) -> list[str]:
        """Return aliases attached to one song."""
        return await self._require_song_store().get_song_aliases_for_song_id(game_code, song_id)

    async def get_song_with_difficulty(
        self,
        game_code: str,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
    ) -> dict | None:
        """Return one song plus the requested difficulty sheet."""
        return await self._require_song_store().get_song_with_difficulty(
            game_code,
            song_id,
            song_type=song_type,
            level_index=level_index,
        )

    async def search_song_aliases(self, game_code: str, song_query: str | int) -> dict | None:
        """Return alias information for a song query."""
        return await self.search.get_song_aliases(song_query, game_code=game_code)

    async def get_song_aliases(self, game_code: str, song_query: str | int) -> dict | None:
        """Alias for ``search_song_aliases``."""
        return await self.search_song_aliases(game_code, song_query)

    def invalidate_search_cache(self, game_code: str | None = None) -> None:
        """Clear cached alias/search data for one game or all games."""
        self.search.invalidate_alias_cache(game_code)

    def _optional_song_store(self) -> Any | None:
        return self.songs

    def _require_song_store(self) -> Any:
        if self.songs is None:
            raise StorageNotConfiguredError(
                "Song catalog storage requires an injected song_store or the storage-tortoise extra."
            )
        return self.songs


class ArcadeSongsCatalogService:
    """Generic arcade-songs catalog access behind ``SongCatalogService``."""

    def __init__(self, catalog: SongCatalogService) -> None:
        self._catalog = catalog

    async def sites(self) -> list[dict[str, Any]]:
        """Fetch generic arcade-songs site metadata."""
        route = self._catalog.providers.require("arcade_songs.catalog.sites")
        method = self._catalog._provider_method(route.provider, route.operation.method)
        sites = await method()
        return sites if isinstance(sites, list) else []

    async def catalog(
        self,
        game_code: str,
        *,
        force_refresh: bool = False,
    ) -> dict[int, SongData]:
        """Fetch one generic arcade-songs catalog without persisting it."""
        route = self._catalog.providers.require("arcade_songs.catalog.songs", game_code=game_code)
        method = self._catalog._provider_method(route.provider, route.operation.method)
        return await method(game_code, force_refresh=force_refresh)

    async def search(
        self,
        game_code: str,
        query: str | int,
        *,
        force_refresh: bool = False,
    ) -> list[SongQueryResult]:
        """Search one generic arcade-songs catalog loaded on demand."""
        songs = await self.catalog(game_code, force_refresh=force_refresh)
        repository = _MemorySongRepository(songs)
        search = SongSearchService(repository)
        search.invalidate_alias_cache(game_code)
        try:
            return await search.search_song(query, game_code=game_code)
        finally:
            search.invalidate_alias_cache(game_code)


async def _noop_sync(*args: Any, **kwargs: Any) -> None:
    return None


class _MemorySongRepository:
    def __init__(self, songs: dict[int, SongData]) -> None:
        self.songs = songs

    async def get_song_by_id(self, game_code: str, song_id: int) -> SongData | None:
        return self.songs.get(song_id)

    async def load_all_songs(self, game_code: str) -> dict[int, SongData]:
        return self.songs

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        return {song_id: song.title for song_id, song in self.songs.items()}

    async def query_alias_exact(self, game_code: str, alias_lower: str) -> list[tuple[int, str]]:
        return [
            (song_id, alias)
            for song_id, song in self.songs.items()
            for alias in (song.aliases or [])
            if alias.lower() == alias_lower
        ]

    async def load_alias_records(self, game_code: str) -> list[tuple[int, str]]:
        return [
            (song_id, alias)
            for song_id, song in self.songs.items()
            for alias in (song.aliases or [])
        ]

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int) -> list[str]:
        song = self.songs.get(song_id)
        return list(song.aliases or []) if song is not None else []

    async def get_song_with_difficulty(
        self,
        game_code: str,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
    ) -> dict | None:
        song = self.songs.get(song_id)
        if song is None:
            return None
        sheets = (song.difficulties or {}).get(song_type, [])
        return {
            "song_data": song,
            "target_difficulty": sheets[level_index] if level_index < len(sheets) else None,
        }


def _default_song_store(logger: Any | None) -> Any | None:
    try:
        from arcade_helper.storage.tortoise import TortoiseSongStore
    except ImportError:
        return None
    return TortoiseSongStore(logger=logger)


__all__ = [
    "ArcadeSongsCatalogService",
    "CatalogFetchResult",
    "CatalogSyncResult",
    "CatalogSyncStatus",
    "SongCatalogService",
]
