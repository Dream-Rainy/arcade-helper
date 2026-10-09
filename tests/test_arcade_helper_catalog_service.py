from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_search_in_another_game_does_not_wait_for_blocked_query():
    from arcade_helper.search.song_query import SongSearchService
    entered, release = asyncio.Event(), asyncio.Event()
    service = SongSearchService(SimpleNamespace())
    async def blocked(*, game_code):
        entered.set()
        await release.wait()
        return game_code
    async def quick(*, game_code):
        return game_code
    task = asyncio.create_task(service._run(blocked, game_code='maimai'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert await asyncio.wait_for(service._run(quick, game_code='sdvx'), 1) == 'sdvx'
        assert not task.done()
    finally:
        release.set()
        await task

from arcade_helper.catalog import SongCatalogService
from arcade_helper.core.context import CatalogContext
from arcade_helper.games.arcade_songs import ARCADE_SONGS_SITES_URL, ArcadeSongData, ArcadeSongSheet, ArcadeSongsProvider
from arcade_helper.providers import ProviderOperation, ProviderRegistry


@pytest.mark.asyncio
async def test_index_singleflight_does_not_block_exact_queries_or_publish_obsolete_workers(monkeypatch):
    from arcade_helper.core.song import SongData
    from arcade_helper.search import song_query as module

    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    state = {"revision": 1, "title": "Old Song", "pause": True}
    identities = SimpleNamespace(_db=object())
    built = []
    builder = module._build_search_index

    def paused(*args):
        built.append(args[1].copy())
        if state["pause"]:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        return builder(*args)

    async def songs(game):
        return {1: SongData(id=1, title=state["title"], aliases=[])}

    async def full(game, sid):
        return SongData(id=sid, title=state["title"], image_name="full.jpg")

    async def exact(game, title):
        return [(1, state["title"])] if title == state["title"] else []

    async def revision():
        return state["revision"]

    repository = SimpleNamespace(identities=identities, get_revision=revision,
        load_search_songs=songs, load_all_songs=songs, get_song_by_id=full, query_title_exact=exact)
    service = module.SongSearchService(repository)
    monkeypatch.setattr(module, "_build_search_index", paused)
    first = asyncio.create_task(service._warm_search_index("sdvx"))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(service._warm_search_index("sdvx"))
        result = await asyncio.wait_for(service.query_song_by_title_exact("Old Song", game_code="sdvx"), 1)
        assert result[0].song_data.image_name == "full.jpg" and not first.done()
        assert await asyncio.wait_for(service.query_song_by_id(1, game_code="maimai"), 1)
        assert len(built) == 1
        state.update(title="New Song", pause=False)
        identities._db = object()  # Connection changes even with an identical revision.
        await asyncio.wait_for(service._warm_search_index("sdvx"), 2)
        release.set()
        await asyncio.gather(first, second)
        assert service._cache.indexes[("sdvx", None)].index == {1: "New Song"}
        # Cancellation during a third build must not publish its eventual output.
        release.clear()
        entered.clear()
        state.update(revision=2, title="Cancelled Song", pause=True)
        first = asyncio.create_task(service._warm_search_index("sdvx"))
        await asyncio.wait_for(entered.wait(), 2)
        await service._stop_index_builds()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert ("sdvx", None) not in service._cache.indexes
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await service._stop_index_builds()


@pytest.mark.asyncio
async def test_light_candidates_keep_separate_legacy_aliases_and_return_full_songs():
    from arcade_helper.core.song import SongData, SongSheet
    from arcade_helper.search import song_query as module
    reads = []

    async def light(game):
        return {sid: SongData(id=sid, title=f"Other {sid}") for sid in (1, 2)}

    async def aliases(game):
        return [(1, "fumoffu"), (2, "fumoffu")]

    async def full(game, sid):
        reads.append(sid)
        return SongData(id=sid, title=f"Other {sid}", image_name=f"{sid}.jpg", aliases=["fumoffu"],
            difficulties={"standard": [SongSheet(type="standard", difficulty="master", level="15")]})

    repository = SimpleNamespace(load_search_songs=light, load_all_songs=light,
                                 load_alias_records=aliases, get_song_by_id=full)
    service = module.SongSearchService(repository)
    results = await service.query_song_fuzzy("fumoffu", game_code="sdvx")
    assert {r.song_id for r in results} == {1, 2}
    assert sorted(reads) == [1, 2]
    assert all(r.song_data.image_name and r.song_data.difficulties["standard"][0].level == "15" for r in results)
    token = module._repository_var.set(repository)
    try:
        legacy = await module.query_song_fuzzy("fumoffu", game_code="sdvx")
        assert all(r.song_data.image_name and r.song_data.difficulties for r in legacy)
    finally:
        module._repository_var.reset(token)


@pytest.mark.asyncio
async def test_maimai_xml_loading_keeps_event_loop_responsive_and_preserves_data(monkeypatch):
    from arcade_helper.games.maimai import data_fetcher as fetcher
    from arcade_helper.games.maimai.schemas import MaiSongData, MaiSongSheet

    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    entered = asyncio.Queue()
    gates = []
    maps = {1: SimpleNamespace(map_name='Map')}
    treasures = {2: SimpleNamespace(music_id=7, music_name=' Sample ')}
    bonuses = {3: SimpleNamespace(music_names={8: ' Bonus '}, map_name='Bonus Map')}
    counts = {'tap': 12, 'hold': 3, 'slide': 4, 'break': 1, 'touch': 2}
    music = {7: {'title': 'Sample', 'artist': 'XML Artist', 'bpm': 180,
                  'difficulties': {'standard': [{'note_counts': counts}]}}}
    phases = {
        'scan_map_directory': maps,
        'extract_music_ids_from_maps': {7: 1},
        'scan_map_treasure_directory': treasures,
        'scan_map_bonus_music_directory': bonuses,
        'scan_music_directory': music,
        '_enrich_with_music_xml': None,
    }
    for name, result in phases.items():
        original = getattr(fetcher, name)
        def blocked(*args, _name=name, _result=result, _original=original):
            gate = threading.Event()
            gates.append(gate)
            loop.call_soon_threadsafe(entered.put_nowait, (_name, gate))
            assert gate.wait(5), 'XML blocked the event loop instead of allowing the command coroutine to run'
            if _name == '_enrich_with_music_xml':
                return _original(*args)
            return _result
        monkeypatch.setattr(fetcher, name, blocked)

    callbacks = []
    async def sync_maps(data):
        assert asyncio.get_running_loop() is loop and threading.get_ident() == main_thread
        callbacks.append(('maps', data))
    async def sync_treasures(data):
        assert asyncio.get_running_loop() is loop and threading.get_ident() == main_thread
        callbacks.append(('treasures', data))
    async def get_json(url, **kwargs):
        return {'data': {'fixture': 1}} if 'config' in url else {'aliases': [], 'content': []}
    def merge(*args):
        assert args[3:] == ({7: 1}, {1: 'Map'}, {'sample': 7, 'bonus': 8}, {8: 'Bonus Map'})
        return {10000001: MaiSongData(id=10000001, title='Sample', artist='',
                difficulties={'standard': [MaiSongSheet(type='std', difficulty='basic', level='1', level_value=1)]})}
    monkeypatch.setattr(fetcher, '_merge_dxrating_and_lxns', merge)
    context = CatalogContext(http_get_json=get_json, ingame_data_base_dir='fixture')
    task = asyncio.create_task(fetcher.fetch_maimai_catalog(context, sync_map_data=sync_maps, sync_map_treasure_data=sync_treasures))
    try:
        for expected in phases:
            name, gate = await asyncio.wait_for(entered.get(), 5)
            assert name == expected and not task.done()
            # This is the command coroutine: it must execute while disk/XML work is still blocked.
            await asyncio.sleep(0)
            assert not gate.is_set()
            gate.set()
        result = await asyncio.wait_for(task, 5)
    finally:
        for gate in gates:
            gate.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert callbacks == [('maps', maps), ('treasures', treasures)]
    assert list(result.songs) == [7]  # XML title matching corrected the synthetic song ID.
    song = result.songs[7]
    assert (song.artist, song.bpm) == ('XML Artist', 180)
    assert song.difficulties['standard'][0].note_counts == counts


@pytest.mark.asyncio
async def test_chunithm_xml_scan_keeps_event_loop_responsive(monkeypatch):
    from arcade_helper.games.chunithm import data_fetcher as fetcher

    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    xml_data = {7: {'title': 'Sample'}}

    def scan(path):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), 'CHUNITHM XML scan blocked the event loop'
        return xml_data

    async def get_json(*args, **kwargs):
        return {'songs': [], 'aliases': []}

    async def fetch_arcade(*args):
        return {'songs': []}

    def merge(arcade, lxns, xml, logger):
        assert xml is xml_data
        return {}

    monkeypatch.setattr(fetcher, 'scan_music_directory', scan)
    monkeypatch.setattr(fetcher, '_fetch_arcade_data_json', fetch_arcade)
    monkeypatch.setattr(fetcher, 'merge_chuni_arcade_and_lxns', merge)
    context = CatalogContext(http_get_json=get_json, ingame_data_base_dir='fixture')
    task = asyncio.create_task(fetcher.fetch_chunithm_catalog(context))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not task.done()
        release.set()
        assert await asyncio.wait_for(task, 5) == {}
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
@pytest.mark.parametrize("include_songs", [True, False])
async def test_catalog_service_syncs_provider_catalog_into_store(include_songs, monkeypatch) -> None:
    service, provider, store = _service()

    if not include_songs:
        async def forbidden(*args, **kwargs):
            pytest.fail("background sync must not read back full songs")
        monkeypatch.setattr(service, "load_all_songs", forbidden)
    result = await service.sync_game_from_remote("maimai", current_data_version=3, **({"include_songs": False} if not include_songs else {}))

    assert result.ok
    assert result.data_version == 7
    assert result.songs == ({101: _Song("song")} if include_songs else None)
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
