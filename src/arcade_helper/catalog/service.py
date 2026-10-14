"""Provider-backed song catalog service."""

from __future__ import annotations

import asyncio
import traceback
import warnings
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from time import monotonic
from typing import Any, Literal

from arcade_helper.core.errors import CatalogSyncError, StorageNotConfiguredError
from arcade_helper.core.song import SongData
from arcade_helper.catalog.importer import CatalogImportReport, CatalogRecord, prepare_native_catalog
from arcade_helper.core.song_identity import normalize_width
from arcade_helper.aliases import AliasService
from arcade_helper.games.arcade_songs import ARCADE_SONGS_CACHE_TTL_SECONDS
from arcade_helper.search import SongQueryResult, SongSearchService
from arcade_helper.search.song_query import invalidate_alias_cache


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
    import_report: CatalogImportReport | None = None

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
        catalog_store: Any | None = None,
        logger: Any | None = None,
    ) -> None:
        self.providers = providers
        self.songs = song_store or _default_song_store(logger)
        self.identities = catalog_store
        self.aliases = AliasService(catalog_store)
        self.search = SongSearchService(self)
        self.arcade_songs = ArcadeSongsCatalogService(self)
        self.logger = logger
        self._sync_locks: dict[str, asyncio.Lock] = {}
        self._catalog_tasks: dict[str, asyncio.Task] = {}
        self._catalog_failed_at: dict[str, float] = {}
        self._catalog_ready: dict[str, tuple] = {}
        self._catalog_games: tuple[str, ...] | None = None
        self._catalog_slots = asyncio.Semaphore(2)
        self._closed = False

    def _can_prepare_catalogs(self) -> bool:
        return self.identities is not None and any(
            route.operation.key == "arcade_songs.catalog.payload" for route in self.providers.operations())

    async def _catalog_state(self, game: str) -> str:
        generation = (getattr(self.identities, "_db", None), await self.get_revision(),
                      await self.identities.get_sync_version(game))
        if self._catalog_ready.get(game) == generation:
            return "ready"
        report = await self.identities.get_import(game)
        if report is None:
            state = "ready" if await self.identities.load_song_index(game) else "missing"
        else:
            outcome = report.get("outcome")
            if outcome == "complete" or (outcome is None and not report.get("pending") and not report.get("invalid") and not report.get("missing_deferred")):
                state = "ready"
            else:
                count = report.get("available_song_count")
                if count is None:
                    count = len(await self.identities.load_song_index(game))
                state = "partial" if count else "missing"
        if state == "ready":
            self._catalog_ready[game] = generation
        return state

    async def _generic_games(self, *, force_refresh: bool = False) -> tuple[str, ...]:
        if self._catalog_games is None:
            sites = await self.arcade_songs.sites(force_refresh=force_refresh)
            self._catalog_games = tuple(dict.fromkeys(
                code for site in sites if isinstance(site, dict) and not site.get("isHidden")
                and isinstance(code := site.get("gameCode"), str) and 0 < len(code) <= 32
                and code == code.strip().lower() and code not in {"maimai", "chunithm"}
            ))
        return self._catalog_games

    async def _prepare_generic_catalog(self, game: str, force_refresh: bool):
        async with self._lock_for_game(game):
            if not force_refresh and await self._catalog_state(game) != "missing":
                return await self.identities.get_import(game)
            async with self._catalog_slots:
                try:
                    report = await self.import_catalog(game, force_refresh=force_refresh)
                except Exception as exc:
                    from arcade_helper.storage.tortoise.catalog_store import CatalogConflictError
                    # Another process may have won publication with the same fetch token.
                    if isinstance(exc, CatalogConflictError) and await self._catalog_state(game) != "missing":
                        return await self.identities.get_import(game)
                    raise CatalogSyncError(f"Catalog {game} preparation failed: {exc}") from exc
            if report.outcome == "unusable":
                raise CatalogSyncError(f"Catalog {game} has no usable records: {report.pending_reasons}; invalid={len(report.invalid)}")
            return asdict(report)

    def _start_catalog_task(self, game: str, *, force_refresh: bool = False):
        if self._closed:
            raise CatalogSyncError("Catalog service is closed")
        task = self._catalog_tasks.get(game)
        if task is not None and not task.cancelled():
            if not task.done():
                return task
            if task.exception() is not None:
                self._catalog_failed_at.setdefault(game, monotonic())
            if not force_refresh and task.exception() is not None and monotonic() - self._catalog_failed_at.get(game, 0) < 30:
                return task
        task = asyncio.create_task(self._prepare_generic_catalog(game, force_refresh))
        self._catalog_tasks[game] = task
        def completed(done):
            if not done.cancelled() and done.exception() is not None:
                self._catalog_failed_at[game] = monotonic()
        task.add_done_callback(completed)
        return task

    async def _ensure_generic_catalog(self, game_code: str) -> None:
        game = str(game_code).strip().lower()
        if game in {"maimai", "chunithm"} or not self._can_prepare_catalogs():
            return
        if await self._catalog_state(game) == "missing":
            await asyncio.shield(self._start_catalog_task(game))

    async def prepare_catalogs(self, game_codes=None, *, force_refresh: bool = False) -> dict:
        """Prepare persistent generic catalogs; failures name the unavailable games."""
        if not self._can_prepare_catalogs():
            raise StorageNotConfiguredError("Catalog preparation requires persistent storage and an arcade-songs provider")
        if force_refresh and game_codes is None:
            self._catalog_games = None
        if isinstance(game_codes, str):
            game_codes = [game_codes]
        games = tuple(dict.fromkeys(str(game).strip().lower() for game in (game_codes if game_codes is not None else await self._generic_games(force_refresh=force_refresh))))
        if any(not game or len(game) > 32 for game in games):
            raise ValueError("Invalid game code")
        tasks = [self._start_catalog_task(game, force_refresh=force_refresh) for game in games if game not in {"maimai", "chunithm"}]
        results = await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        selected = [game for game in games if game not in {"maimai", "chunithm"}]
        failures = {game: str(result) for game, result in zip(selected, results) if isinstance(result, BaseException)}
        if failures:
            raise CatalogSyncError(f"Catalog preparation failed: {failures}")
        return dict(zip(selected, results))

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._catalog_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.search._stop_index_builds()
        if self.identities is not None and getattr(self.identities, "_text_search", None) is not None:
            await self.identities.text_search._stop_index_builds()

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
            songs = await self.arcade_songs._remote_catalog(game_code)
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
        include_songs: bool = True,
    ) -> CatalogSyncResult:
        """Persist a remote catalog; omit the full readback when include_songs=False."""
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
                if self.identities is not None and game_code not in {"maimai", "chunithm"}:
                    report = await self.import_catalog(game_code)
                    return CatalogSyncResult(
                        game_code=game_code, provider="arcade_songs", operation=operation,
                        status="success", started_at=started_at, finished_at=datetime.now(),
                        songs=await self.identities.load_all_songs(game_code) if include_songs else None,
                        import_report=report,
                    )
                alias_versions = await self.aliases.source_versions(game_code) if self._aliases_enabled() else None
                sync_version = await self.identities.get_sync_version(game_code, provider="catalog", namespace="native") if self._aliases_enabled() else None
                fetched = await self.fetch_game_raw_data(
                    game_code,
                    current_data_version=current_data_version,
                )
                await self.sync_songs(game_code, fetched.songs, expected_sync_version=sync_version, expected_alias_versions=alias_versions)
                return CatalogSyncResult(
                    game_code=game_code,
                    provider=fetched.provider,
                    operation=operation,
                    status="success",
                    started_at=started_at,
                    finished_at=datetime.now(),
                    songs=(await self.load_all_songs(game_code) if self._aliases_enabled() else fetched.songs) if include_songs else None,
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

    async def sync_songs(self, game_code: str, song_data: dict[int, Any], *, expected_sync_version=None, expected_alias_versions=None) -> None:
        """Persist a game song catalog directly."""
        store = self._require_song_store()
        from arcade_helper.storage.tortoise.song_store import TortoiseSongStore
        if self._aliases_enabled():
            from tortoise.transactions import in_transaction

            records, invalid = prepare_native_catalog(game_code, song_data)
            if not records:
                raise CatalogSyncError(f"No usable {game_code} songs: " + "; ".join(invalid))
            usable = {int(record.native_key): record.song for record in records}
            async with in_transaction(self.identities.connection_name):
                await self.identities.import_records(game_code, records, provider="catalog", namespace="native", complete=False,
                    invalid=invalid, alias_complete=True, expected_sync_version=expected_sync_version, expected_alias_versions=expected_alias_versions)
                if isinstance(store, TortoiseSongStore):
                    await store.sync_songs(game_code, usable, write_aliases=False)
                else:
                    await store.sync_songs(game_code, usable)
            if invalid and self.logger is not None:
                self.logger.warning(f"[{game_code}] skipped {len(invalid)} invalid catalog values: " + "; ".join(invalid))
            self.invalidate_search_cache(game_code)
            return
        if isinstance(store, TortoiseSongStore):
            song_data = {key: CatalogRecord(song, {}, str(key)).song for key, song in song_data.items()}
        await store.sync_songs(game_code, song_data)
        if self.identities is not None and game_code in {"maimai", "chunithm"}:
            from arcade_helper.storage.tortoise.catalog_migration import migrate_catalog_identities
            await migrate_catalog_identities(store=self.identities, song_store=self.songs, dry_run=False, games=(game_code,))
        self.invalidate_search_cache(game_code)

    def _aliases_enabled(self) -> bool:
        return bool(self.identities is not None and getattr(self.identities, "enable_aliases", False))

    async def search_all(self, query, *, game_code=None, text_only=False):
        return await self._query_catalog(query, game_code=game_code, text_only=text_only)

    async def resolve_alias(self, query, *, game_code=None):
        return await self._query_catalog(query, game_code=game_code, aliases_only=True)

    async def _query_catalog(self, query, *, game_code=None, text_only=False, aliases_only=False):
        if not self._aliases_enabled():
            raise StorageNotConfiguredError("Enable aliases on a persistent catalog store first")
        unavailable = []
        missing = []
        if game_code is not None:
            game_code = str(game_code).strip().lower()
            await self._ensure_generic_catalog(game_code)
            if self._can_prepare_catalogs() and game_code not in {"maimai", "chunithm"} and await self._catalog_state(game_code) == "partial":
                unavailable.append(game_code)
        elif self._can_prepare_catalogs():
            for game in await self._generic_games():
                state = await self._catalog_state(game)
                if state != "ready":
                    unavailable.append(game)
                if state == "missing":
                    missing.append(game)
        result = (await self.aliases.resolve_alias(query, game_code=game_code) if aliases_only else
                  await self.aliases.search_all(query, game_code=game_code, text_only=text_only))
        # Finish fact reads before imports can occupy the shared SQLite connection.
        for game in missing:
            self._start_catalog_task(game)
        if unavailable and not result.candidates:
            raise CatalogSyncError(f"Catalogs initializing or unavailable: {', '.join(unavailable)}; retry or await prepare_catalogs()")
        if unavailable:
            result = (replace(result, inferred_game=None, resolution="ambiguous", inference_reason="catalog_incomplete",
                              unavailable_games=tuple(unavailable)) if game_code is None else
                      replace(result, unavailable_games=tuple(unavailable)))
        return result

    async def get_effective_aliases(self, game_song_id):
        return await self.aliases.get_effective_aliases(game_song_id)

    async def get_revision(self) -> int | None:
        return await self.identities.get_revision() if self.identities is not None else None

    async def import_catalog(
        self, game_code: str, *, dry_run: bool = False, force_refresh: bool = True,
        namespace: str = "default",
    ) -> CatalogImportReport:
        """Fetch and import an arcade-songs snapshot with stable internal IDs."""
        if self.identities is None:
            raise StorageNotConfiguredError("Persistent import requires catalog_store")
        if game_code in {"maimai", "chunithm"}:
            raise ValueError("Use native sync/backfill for maimai and chunithm")
        route = self.providers.require("arcade_songs.catalog.payload", game_code=game_code)
        sync_version = await self.identities.get_sync_version(game_code, provider=route.provider.name, namespace=namespace)
        alias_versions = await self.aliases.source_versions(game_code) if self._aliases_enabled() else None
        method = self._provider_method(route.provider, route.operation.method)
        payload = await method(game_code, force_refresh=force_refresh)
        report = await self.identities.import_arcade_catalog(
            game_code, payload, provider=route.provider.name, namespace=namespace,
            expected_sync_version=sync_version, dry_run=dry_run,
            expected_alias_versions=alias_versions,
        )
        if not dry_run:
            self.invalidate_search_cache(game_code)
            self._catalog_failed_at.pop(game_code, None)
        return report

    async def preview_import(self, game_code: str, *, force_refresh: bool = True) -> CatalogImportReport:
        return await self.import_catalog(game_code, dry_run=True, force_refresh=force_refresh)

    def _read_store(self, game_code: str) -> Any:
        if self.identities is not None and game_code not in {"maimai", "chunithm"}:
            return self.identities
        return self._require_song_store()

    async def get_song_by_id(self, game_code: str, song_id: int):
        """Return one song by game code and song id."""
        await self._ensure_generic_catalog(game_code)
        song = await self._read_store(game_code).get_song_by_id(game_code, song_id)
        if song is not None and self._aliases_enabled() and game_code in {"maimai", "chunithm"}:
            from arcade_helper.storage.tortoise.catalog_models import SongReference
            identities = await SongReference.filter(
                provider="catalog", namespace="native", game_code=game_code, key_kind="native",
                external_key=str(song_id), present=True, game_song__active=True,
            ).using_db(self.identities._db).values_list("game_song_id", flat=True)
            if not identities:
                return None
            identity = identities[0]
            evidence = await self.aliases.get_effective_aliases(identity)
            song.title = normalize_width(song.title)
            song.aliases = list(dict.fromkeys(e["text"] for e in evidence if e["kind"] == "alias"))
        return song

    async def get_songs_by_ids(self, game_code: str, song_ids: list[int]) -> dict[int, SongData]:
        """Return full songs in requested ID order, omitting missing/inactive IDs.

        Native games use public native IDs; older stores fall back to single reads.
        Known-ID hydration does not prepare catalogs or scan their full indexes.
        """
        ids = list(dict.fromkeys(song_ids))
        if not ids:
            return {}
        store = self._read_store(game_code)
        batch = getattr(store, "get_songs_by_ids", None)
        for _ in range(3):
            db = getattr(self.identities, "_db", None)
            before = await self.get_revision()
            if batch is None:
                songs = {}
                for sid in ids:
                    song = await store.get_song_by_id(game_code, sid)
                    if song is not None:
                        songs[sid] = song
            else:
                songs = await batch(game_code, ids)
            if self._aliases_enabled() and game_code in {"maimai", "chunithm"} and songs:
                from arcade_helper.storage.tortoise.catalog_models import SongReference
                from arcade_helper.storage.tortoise.alias_store import TortoiseAliasStore
                refs = await SongReference.filter(
                    provider="catalog", namespace="native", game_code=game_code, key_kind="native",
                    external_key__in=[str(sid) for sid in songs], present=True, game_song__active=True,
                ).using_db(db).order_by("id").values_list("external_key", "game_song_id")
                mapped = {}
                for native, identity in refs:
                    mapped.setdefault(int(native), identity)
                evidence = (await TortoiseAliasStore(self.identities).projection(_song_ids=list(set(mapped.values())))
                            if mapped else {})
                songs = {sid: song for sid, song in songs.items() if sid in mapped}
                for sid, song in songs.items():
                    song.title = normalize_width(song.title)
                    song.aliases = list(dict.fromkeys(
                        e["text"] for e in evidence.get(mapped[sid], []) if e["kind"] == "alias"
                    ))
            if await self.get_revision() == before and getattr(self.identities, "_db", None) is db:
                return {sid: songs[sid] for sid in ids if sid in songs}
        raise CatalogSyncError("Catalog changed repeatedly while loading; retry")

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
        await self._ensure_generic_catalog(game_code)
        songs = await self._read_store(game_code).load_all_songs(game_code)
        if self._aliases_enabled() and game_code in {"maimai", "chunithm"}:
            aliases = await self.identities.native_aliases(game_code)
            songs = {key: song for key, song in songs.items() if key in aliases}
            for key, song in songs.items():
                song.title = normalize_width(song.title)
                song.aliases = aliases.get(key, [])
        return songs

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        """Load the song id to title index for a game."""
        await self._ensure_generic_catalog(game_code)
        if self._aliases_enabled():
            records = list((await self.identities.load_song_index(game_code)).items())
            return dict(await self._native_matches(game_code, records))
        return await self._read_store(game_code).load_song_index(game_code)

    async def _native_matches(self, game_code, records):
        if game_code not in {"maimai", "chunithm"}:
            return records
        from arcade_helper.storage.tortoise.catalog_models import SongReference
        refs = await SongReference.filter(provider="catalog", namespace="native", game_code=game_code,
                                         present=True, game_song_id__in={sid for sid, _ in records}).using_db(
            self.identities._db
        ).values_list("game_song_id", "external_key")
        native_ids = {}
        for target, native in refs:
            if native.isdecimal():
                native_ids.setdefault(target, []).append(int(native))
        return list(dict.fromkeys((native, text) for sid, text in records for native in native_ids.get(sid, [])))

    async def query_title_exact(self, game_code: str, title: str):
        await self._ensure_generic_catalog(game_code)
        if self._aliases_enabled():
            return await self._native_matches(game_code, await self.identities.query_title_exact(game_code, title))
        store = self._read_store(game_code)
        query = getattr(store, "query_title_exact", None)
        if query is not None:
            return await query(game_code, title)
        return [(sid, text) for sid, text in (await store.load_song_index(game_code)).items() if text.lower() == title.lower()]

    async def load_search_songs(self, game_code: str):
        """Read text metadata for ranking and background vector preparation."""
        await self._ensure_generic_catalog(game_code)
        if self._aliases_enabled():
            songs = await self.identities.load_search_songs(game_code)
            mapped = await self._native_matches(game_code, [(sid, str(sid)) for sid in songs])
            return {sid: songs[int(identity)].model_copy(update={"id": sid}) for sid, identity in mapped}
        store = self._read_store(game_code)
        load = getattr(store, "load_search_songs", store.load_all_songs)
        return await load(game_code)

    async def query_alias_exact(self, game_code: str, alias_lower: str) -> list[tuple[int, str]]:
        """Return alias records matching a normalized alias exactly."""
        await self._ensure_generic_catalog(game_code)
        if self._aliases_enabled():
            return await self._native_matches(game_code, await self.identities.query_alias_exact(game_code, alias_lower))
        return await self._read_store(game_code).query_alias_exact(game_code, alias_lower)

    async def load_alias_records(self, game_code: str) -> list[tuple[int, str]]:
        """Load all alias records for a game."""
        await self._ensure_generic_catalog(game_code)
        if self._aliases_enabled() and game_code in {"maimai", "chunithm"}:
            return [(key, text) for key, aliases in (await self.identities.native_aliases(game_code)).items() for text in aliases]
        return await self._read_store(game_code).load_alias_records(game_code)

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int) -> list[str]:
        """Return aliases attached to one song."""
        await self._ensure_generic_catalog(game_code)
        if self._aliases_enabled() and game_code in {"maimai", "chunithm"}:
            song = await self.get_song_by_id(game_code, song_id)
            return song.aliases if song else []
        return await self._read_store(game_code).get_song_aliases_for_song_id(game_code, song_id)

    async def get_song_with_difficulty(
        self,
        game_code: str,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
    ) -> dict | None:
        """Return one song plus the requested difficulty sheet."""
        await self._ensure_generic_catalog(game_code)
        result = await self._read_store(game_code).get_song_with_difficulty(
            game_code,
            song_id,
            song_type=song_type,
            level_index=level_index,
        )
        if result and self._aliases_enabled() and game_code in {"maimai", "chunithm"}:
            song = await self.get_song_by_id(game_code, song_id)
            if song is None:
                return None
            result["song_data"].title = song.title
            result["song_data"].aliases = song.aliases
        return result

    async def search_song_aliases(self, game_code: str, song_query: str | int) -> dict | None:
        """Return alias information for a song query."""
        return await self.search.get_song_aliases(song_query, game_code=game_code)

    async def get_song_aliases(self, game_code: str, song_query: str | int) -> dict | None:
        """Alias for ``search_song_aliases``."""
        return await self.search_song_aliases(game_code, song_query)

    def invalidate_search_cache(self, game_code: str | None = None) -> None:
        """Clear cached alias/search data for one game or all games."""
        invalidate_alias_cache(game_code)
        if game_code is None:
            self._catalog_ready.clear()
        else:
            self._catalog_ready.pop(game_code, None)

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
        self._catalogs: dict[str, tuple[float, dict[int, SongData]]] = {}
        self._searches: dict[str, SongSearchService] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def sites(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Fetch generic arcade-songs site metadata."""
        route = self._catalog.providers.require("arcade_songs.catalog.sites")
        method = self._catalog._provider_method(route.provider, route.operation.method)
        sites = await method(force_refresh=True) if force_refresh else await method()
        return sites if isinstance(sites, list) else []

    async def catalog(
        self,
        game_code: str,
        *,
        force_refresh: bool = False,
    ) -> dict[int, SongData]:
        """Read the persistent catalog, or fetch an instance-cached read-only catalog."""
        if self._catalog.identities is not None and game_code not in {"maimai", "chunithm"}:
            if force_refresh:
                await self._catalog.import_catalog(game_code)
            return await self._catalog.load_all_songs(game_code)
        return await self._remote_catalog(game_code, force_refresh=force_refresh)

    async def _remote_catalog(self, game_code: str, *, force_refresh: bool = False) -> dict[int, SongData]:
        await self._load_remote_catalog(game_code, force_refresh=force_refresh)
        return {key: song.model_copy(deep=True) for key, song in self._catalogs[game_code][1].items()}

    async def _load_remote_catalog(self, game_code: str, *, force_refresh: bool = False) -> None:
        async with self._locks.setdefault(game_code, asyncio.Lock()):
            cached = self._catalogs.get(game_code)
            if not force_refresh and cached is not None and monotonic() - cached[0] < ARCADE_SONGS_CACHE_TTL_SECONDS:
                return
            songs = await self._fetch_remote(game_code, force_refresh=force_refresh)
            self._catalogs[game_code] = (monotonic(), songs)
            self._searches[game_code] = SongSearchService(_MemorySongRepository(songs))

    async def _fetch_remote(self, game_code: str, *, force_refresh: bool) -> dict[int, SongData]:
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
        if self._catalog.identities is not None and game_code not in {"maimai", "chunithm"}:
            if force_refresh:
                await self._catalog.import_catalog(game_code)
            return await self._catalog.search_song(game_code, query)
        await self._load_remote_catalog(game_code, force_refresh=force_refresh)
        return await self._searches[game_code].search_song(query, game_code=game_code)


async def _noop_sync(*args: Any, **kwargs: Any) -> None:
    return None


class _MemorySongRepository:
    def __init__(self, songs: dict[int, SongData]) -> None:
        self.songs = songs

    async def get_song_by_id(self, game_code: str, song_id: int) -> SongData | None:
        song = self.songs.get(song_id)
        return song.model_copy(deep=True) if song is not None else None

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
