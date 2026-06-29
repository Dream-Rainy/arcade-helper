from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from arcade_helper.catalog import SongCatalogService
from arcade_helper.core.context import CatalogContext
from arcade_helper.games.arcade_songs import ARCADE_SONGS_SITES_URL, ArcadeSongData, ArcadeSongSheet, ArcadeSongsProvider
from arcade_helper.providers import ProviderOperation, ProviderRegistry


@dataclass
class _MaimaiCatalogResult:
    songs: dict[int, Any]
    data_version: int


class _Provider:
    name = "fake"
    priority = 10

    def __init__(self) -> None:
        self.catalog_calls: list[dict[str, Any]] = []
        self.collection_calls: list[int] = []

    async def maimai_catalog(self, **kwargs: Any) -> _MaimaiCatalogResult:
        self.catalog_calls.append(kwargs)
        if kwargs.get("sync_map_data"):
            await kwargs["sync_map_data"]({1: "map"})
        if kwargs.get("sync_map_treasure_data"):
            await kwargs["sync_map_treasure_data"]({2: "treasure"})
        return _MaimaiCatalogResult(songs={101: _Song("song")}, data_version=7)

    async def song_collections(self, song_id: int) -> list[dict[str, Any]]:
        self.collection_calls.append(song_id)
        return [{"song_id": song_id}]


@dataclass
class _Song:
    title: str


class _Store:
    def __init__(self) -> None:
        self.synced: list[tuple[str, dict[int, Any]]] = []
        self.maps: list[dict[int, Any]] = []
        self.treasures: list[dict[int, Any]] = []
        self.collections: list[tuple[int, list[dict[str, Any]]]] = []

    async def sync_songs(self, game_code: str, songs: dict[int, Any]) -> None:
        self.synced.append((game_code, songs))

    async def sync_maimai_maps(self, map_data: dict[int, Any]) -> None:
        self.maps.append(map_data)

    async def sync_maimai_map_treasures(self, treasure_data: dict[int, Any]) -> None:
        self.treasures.append(treasure_data)

    async def sync_maimai_collections(self, song_id: int, collections: list[dict[str, Any]]) -> None:
        self.collections.append((song_id, collections))

    async def get_song_by_id(self, game_code: str, song_id: int):
        return {"game": game_code, "id": song_id}

    async def load_all_songs(self, game_code: str):
        return {1: {"game": game_code}}

    async def load_song_index(self, game_code: str):
        return {1: game_code}

    async def query_alias_exact(self, game_code: str, alias_lower: str):
        return [(1, alias_lower)]

    async def load_alias_records(self, game_code: str):
        return [(1, game_code)]

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int):
        return [f"{game_code}:{song_id}"]

    async def get_song_with_difficulty(
        self,
        game_code: str,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
    ):
        return {"game": game_code, "id": song_id, "type": song_type, "level": level_index}


def _service() -> tuple[SongCatalogService, _Provider, _Store]:
    provider = _Provider()
    store = _Store()
    registry = ProviderRegistry()
    registry.register(
        provider,
        [
            ProviderOperation(
                key="maimai.catalog.songs",
                method="maimai_catalog",
                game_code="maimai",
            ),
            ProviderOperation(
                key="maimai.catalog.collections",
                method="song_collections",
                game_code="maimai",
            ),
        ],
    )
    return SongCatalogService(providers=registry, song_store=store), provider, store


@pytest.mark.asyncio
async def test_catalog_service_syncs_provider_catalog_into_store() -> None:
    service, provider, store = _service()

    result = await service.sync_game_from_remote("maimai", current_data_version=3)

    assert result.ok
    assert result.data_version == 7
    assert result.songs == {101: _Song("song")}
    assert provider.catalog_calls[0]["current_data_version"] == 3
    assert store.synced == [("maimai", {101: _Song("song")})]
    assert store.maps == [{1: "map"}]
    assert store.treasures == [{2: "treasure"}]


@pytest.mark.asyncio
async def test_catalog_service_returns_already_running_for_concurrent_sync() -> None:
    service, _provider, _store = _service()
    lock = service._lock_for_game("maimai")
    await lock.acquire()
    try:
        result = await service.sync_game_from_remote("maimai")
    finally:
        lock.release()

    assert result.status == "already_running"


@pytest.mark.asyncio
async def test_catalog_service_delegates_queries_to_store() -> None:
    service, _provider, _store = _service()

    assert await service.get_song_by_id("maimai", 10) == {"game": "maimai", "id": 10}
    assert await service.load_song_index("chunithm") == {1: "chunithm"}
    assert await service.query_alias_exact("maimai", "alias") == [(1, "alias")]


@pytest.mark.asyncio
async def test_catalog_service_fetches_and_syncs_collections() -> None:
    service, provider, store = _service()

    collections = await service.fetch_maimai_song_collections(123)
    await service.sync_maimai_collections(123, collections)

    assert provider.collection_calls == [123]
    assert store.collections == [(123, [{"song_id": 123}])]


@pytest.mark.asyncio
async def test_catalog_service_fetches_generic_arcade_songs_catalog() -> None:
    async def get_json(url: str, **kwargs: Any) -> Any:
        if url == ARCADE_SONGS_SITES_URL:
            return [
                {
                    "gameCode": "sdvx",
                    "dataSourceUrl": "https://example.test/sdvx",
                }
            ]
        if url == "https://example.test/sdvx/data.json":
            return {
                "songs": [
                    {
                        "songId": "42",
                        "title": "Test Track",
                        "artist": "Artist",
                        "bpm": 180,
                        "imageName": "jacket.png",
                        "sheets": [
                            {
                                "type": "std",
                                "difficulty": "exhaust",
                                "level": "17",
                                "levelValue": 17,
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(url)

    registry = ProviderRegistry()
    provider = ArcadeSongsProvider(CatalogContext(http_get_json=get_json))
    registry.register(provider, provider.operations)
    service = SongCatalogService(providers=registry, song_store=_Store())

    result = await service.fetch_game_raw_data("sdvx")
    direct = await service.arcade_songs.catalog("sdvx")

    song = result.songs[1]
    assert result.provider == "arcade_songs"
    assert isinstance(song, ArcadeSongData)
    assert direct[1].title == "Test Track"
    assert song.title == "Test Track"
    assert getattr(song, "song_id", None) is None
    assert song.image_name == "jacket.png"
    assert isinstance(song.difficulties["standard"][0], ArcadeSongSheet)
    assert song.difficulties["standard"][0].difficulty == "exhaust"


@pytest.mark.asyncio
async def test_catalog_service_fetches_arcade_songs_sites() -> None:
    async def get_json(url: str, **kwargs: Any) -> Any:
        assert url == ARCADE_SONGS_SITES_URL
        return [{"gameCode": "maimai"}]

    registry = ProviderRegistry()
    provider = ArcadeSongsProvider(CatalogContext(http_get_json=get_json))
    registry.register(provider, provider.operations)
    service = SongCatalogService(providers=registry, song_store=_Store())

    assert await service.arcade_songs.sites() == [{"gameCode": "maimai"}]


@pytest.mark.asyncio
async def test_catalog_service_searches_arcade_songs_without_song_store(tmp_path: Path) -> None:
    async def get_json(url: str, **kwargs: Any) -> Any:
        if url == ARCADE_SONGS_SITES_URL:
            return [{"gameCode": "sdvx", "dataSourceUrl": "https://example.test/sdvx"}]
        if url == "https://example.test/sdvx/data.json":
            return {
                "songs": [
                    {"songId": "not-stable", "title": "Alpha Track", "sheets": []},
                    {"songId": "42", "title": "Beta Melody", "sheets": []},
                ]
            }
        raise AssertionError(url)

    registry = ProviderRegistry()
    provider = ArcadeSongsProvider(CatalogContext(http_get_json=get_json), cache_dir=tmp_path)
    registry.register(provider, provider.operations)
    service = SongCatalogService(providers=registry, song_store=_Store())

    results = await service.arcade_songs.search("sdvx", "Beta")

    assert results
    assert results[0].song_id == 2
    assert results[0].song_data.title == "Beta Melody"
    assert getattr(results[0].song_data, "song_id", None) is None


@pytest.mark.asyncio
async def test_arcade_songs_catalog_uses_disk_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    async def get_json(url: str, **kwargs: Any) -> Any:
        calls.append(url)
        if url == ARCADE_SONGS_SITES_URL:
            return [{"gameCode": "sdvx", "dataSourceUrl": "https://example.test/sdvx"}]
        if url == "https://example.test/sdvx/data.json":
            return {"songs": [{"songId": "100", "title": f"Song {calls.count(url)}", "sheets": []}]}
        raise AssertionError(url)

    registry = ProviderRegistry()
    provider = ArcadeSongsProvider(CatalogContext(http_get_json=get_json), cache_dir=tmp_path)
    registry.register(provider, provider.operations)
    service = SongCatalogService(providers=registry, song_store=_Store())

    first = await service.arcade_songs.catalog("sdvx")
    second = await service.arcade_songs.catalog("sdvx")
    refreshed = await service.arcade_songs.catalog("sdvx", force_refresh=True)

    assert first[1].title == "Song 1"
    assert second[1].title == "Song 1"
    assert refreshed[1].title == "Song 2"
    assert calls.count("https://example.test/sdvx/data.json") == 2


@pytest.mark.asyncio
async def test_arcade_songs_flat_methods_are_deprecated() -> None:
    async def get_json(url: str, **kwargs: Any) -> Any:
        if url == ARCADE_SONGS_SITES_URL:
            return [{"gameCode": "sdvx", "dataSourceUrl": "https://example.test/sdvx"}]
        if url == "https://example.test/sdvx/data.json":
            return {"songs": [{"title": "Beta Melody", "sheets": []}]}
        raise AssertionError(url)

    registry = ProviderRegistry()
    provider = ArcadeSongsProvider(CatalogContext(http_get_json=get_json))
    registry.register(provider, provider.operations)
    service = SongCatalogService(providers=registry, song_store=_Store())

    with pytest.warns(DeprecationWarning):
        assert await service.fetch_arcade_songs_sites() == [
            {"gameCode": "sdvx", "dataSourceUrl": "https://example.test/sdvx"}
        ]
    with pytest.warns(DeprecationWarning):
        assert (await service.fetch_arcade_songs_catalog("sdvx"))[1].title == "Beta Melody"
    with pytest.warns(DeprecationWarning):
        assert (await service.search_arcade_songs("sdvx", "Beta"))[0].song_id == 1
