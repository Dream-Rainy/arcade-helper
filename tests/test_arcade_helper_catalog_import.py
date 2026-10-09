"""Catalog acceptance checks: identity continuity, atomicity, and search isolation."""

from copy import deepcopy
import asyncio

import pytest
from tortoise import Tortoise

from arcade_helper import ArcadeHelperClient
from arcade_helper.catalog import CatalogRecord
from arcade_helper.catalog.service import _MemorySongRepository
from arcade_helper.core.song import SongData
from arcade_helper.core.song_identity import parse_chart_title
from arcade_helper.games.arcade_songs import ARCADE_SONGS_SITES_URL
from arcade_helper.search import SongSearchService
from arcade_helper.storage.tortoise import (
    CatalogConflictError, TortoiseCatalogStore, initialize_catalog_storage,
    migrate_catalog_identities,
)
from arcade_helper.storage.tortoise.models import ChuniSong, MaiSong
from arcade_helper.storage.tortoise.catalog_models import CatalogImport, GameSong, MusicWork, SongReference


async def test_persistent_catalog_preserves_identity_and_rolls_back(tmp_path, monkeypatch):
    database_url = f"sqlite://{tmp_path / 'catalog.sqlite3'}"
    await Tortoise.init(config={
        "connections": {"default": database_url, "peer": database_url},
        "apps": {"models": {"models": ["arcade_helper.storage.tortoise.models", "arcade_helper.storage.tortoise.catalog_models"], "default_connection": "default"}},
    })
    try:
        await initialize_catalog_storage()
        store = TortoiseCatalogStore()
        payload = {"songs": [
            {"title": "Give It Up", "artist": "Artist", "sheets": []},
            {"title": "[未知标记][双人] Give It Up", "artist": "Artist", "sheets": []},
        ]}
        calls = []

        async def get_json(url, **kwargs):
            calls.append(url)
            if url == ARCADE_SONGS_SITES_URL:
                return [{"gameCode": "sdvx", "dataSourceUrl": "https://test.invalid/sdvx"}]
            return deepcopy(payload)

        client = ArcadeHelperClient(http_get_json=get_json, local_data_dir=tmp_path, catalog_store=store)
        await client.lifecycle.start()
        preview = await client.catalog.preview_import("sdvx")
        assert preview.created == 2 and preview.dry_run
        assert await GameSong.all().count() == await CatalogImport.all().count() == 0
        assert await store.get_revision() == 0
        first = await client.catalog.import_catalog("sdvx")
        songs = await client.catalog.load_all_songs("sdvx")
        ids = {song.title: song.id for song in songs.values()}
        assert len(set(song.work_id for song in songs.values())) == 2
        assert songs[ids["[未知标记][双人] Give It Up"]].chart_variant == ("未知标记", "双人")

        # Reordering and repeated rows must not rebind source identities.
        payload["songs"].reverse()
        payload["songs"].append(deepcopy(payload["songs"][0]))
        repeated = await client.catalog.import_catalog("sdvx")
        assert repeated.song_ids == first.song_ids
        assert repeated.revision == first.revision and repeated.unchanged == 2
        assert await GameSong.all().count() == await MusicWork.all().count() == 2
        # Lightweight persistent sync still publishes and advances the dataset version.
        async def no_readback(*args, **kwargs):
            pytest.fail("lightweight sync must not reload the full catalog")
        with monkeypatch.context() as patch:
            patch.setattr(client.catalog, "load_all_songs", no_readback)
            light = await client.catalog.sync_game_from_remote("sdvx", include_songs=False)
            assert light.ok and light.songs is None
        assert set(await client.catalog.load_song_index("sdvx")) == set(ids.values())
        assert await store.get_sync_version("sdvx") == repeated.sync_version + 1
        before_reads = len(calls)
        assert (await client.catalog.arcade_songs.search("sdvx", "Give It Up"))[0].song_id == ids["Give It Up"]
        assert len(calls) == before_reads  # Persistent reads never refetch implicitly.

        # A different instance updates metadata; the existing search sees the new revision.
        peer = TortoiseCatalogStore(connection_name="peer")
        payload["songs"] = payload["songs"][:2]
        payload["songs"][1]["bpm"] = 190
        payload["songs"][1]["sheets"] = [{"type": "std", "difficulty": "expert", "level": "15"}]
        updated = await peer.import_arcade_catalog("sdvx", payload)
        assert updated.updated == 1
        assert updated.song_ids == first.song_ids  # Adding a chart does not change identity.
        assert (await client.catalog.search_song("sdvx", "Give It Up"))[0].song_data.bpm == 190
        with pytest.raises(CatalogConflictError):
            await peer.import_arcade_catalog("sdvx", payload, expected_sync_version=first.sync_version)

        good_snapshot = (await CatalogImport.get(game_code="sdvx")).raw_payload
        # Renames, conflicting duplicates, malformed rows and empty fetches cannot retire old IDs.
        for bad_payload, reason in [
            ({"songs": [{"title": "Renamed", "artist": "Artist", "sheets": []}]}, "possible_rename"),
            ({"songs": [payload["songs"][1], {**payload["songs"][1], "bpm": 200}]}, "duplicate_identity"),
            ({"songs": [None]}, None),
            ({"songs": []}, None),
        ]:
            report = await store.import_arcade_catalog("sdvx", bad_payload)
            assert report.missing_deferred and report.withdrawn == 0
            if reason:
                assert report.pending[0]["reason"] == reason
            assert set(await store.load_all_songs("sdvx")) == set(ids.values())
            assert (await CatalogImport.get(game_code="sdvx")).raw_payload == good_snapshot
        assert await store.get_revision() == updated.revision

        # Fail after writing facts but before the snapshot commit: all writes must roll back.
        snapshot = await store.get_import("sdvx")
        async def fail_snapshot(*args, **kwargs):
            raise RuntimeError("simulated storage failure")
        with monkeypatch.context() as patch:
            patch.setattr(CatalogImport, "create", fail_snapshot)
            with pytest.raises(RuntimeError, match="simulated storage failure"):
                await store.import_arcade_catalog("other", {"songs": [payload["songs"][1]]})
        assert await GameSong.all().count() == await MusicWork.all().count() == 2
        assert await SongReference.all().count() == 2
        assert await store.get_revision() == updated.revision
        assert await store.get_import("sdvx") == snapshot

        # A complete, unambiguous removal retires the reference, never deletes its identity.
        retired = await store.import_arcade_catalog("sdvx", {"songs": [payload["songs"][1]]})
        assert retired.withdrawn == 1
        assert set(await store.load_all_songs("sdvx")) == {ids["Give It Up"]}
        assert await GameSong.all().count() == 2

        # Native backfill is repeatable and preserves legacy IDs/tables, including excluded temporary rows.
        await MaiSong.create(id=42, title="Native", artist="Artist")
        await ChuniSong.create(id=42, title="Native", artist="Artist")
        await MaiSong.create(id=10_000_000, title="Temporary", artist="Artist")
        await migrate_catalog_identities(store=store, dry_run=False)
        native_id = await store.resolve_reference("maimai", "42", provider="catalog", namespace="native")
        assert native_id is not None
        assert await store.resolve_reference("chunithm", "42", provider="catalog", namespace="native") != native_id
        assert (await client.catalog.get_song_by_id("maimai", 42)).id == 42
        assert (await store.get_song_by_id("maimai", native_id)).title == "Native"
        assert await store.resolve_reference("maimai", "10000000", provider="catalog", namespace="native") is None
        revision = await store.get_revision()
        await migrate_catalog_identities(store=store, dry_run=False)
        assert await store.get_revision() == revision
        assert await MaiSong.all().count() == 2

        # Changed alias projections invalidate a previously built index in another instance.
        record = CatalogRecord(SongData(id=42, title="Example", artist="Artist", aliases=["Before alias"]), {}, "42")
        await store.import_records("cache-game", [record], provider="test")
        assert (await client.catalog.search_song("cache-game", "beforealias"))[0].matched_text == "Before alias"
        changed_record = CatalogRecord(record.song.model_copy(update={"aliases": ["After alias"]}), {}, "42")
        await peer.import_records("cache-game", [changed_record], provider="test")
        assert (await client.catalog.search_song("cache-game", "afteralias"))[0].matched_text == "After alias"

        # Two independent SQLite connections compete with the same fetch revision.
        revision = await store.get_revision()
        outcomes = await asyncio.gather(
            store.import_arcade_catalog("race-a", {"songs": [payload["songs"][1]]}, expected_sync_version=0),
            peer.import_arcade_catalog("race-a", {"songs": [payload["songs"][1]]}, expected_sync_version=0),
            return_exceptions=True,
        )
        assert sum(isinstance(result, CatalogConflictError) for result in outcomes) == 1
        assert sum(not isinstance(result, Exception) for result in outcomes) == 1
        assert await store.get_revision() == revision + 1

        # Unrelated datasets do not invalidate one another's fetch tokens.
        outcomes = await asyncio.gather(
            store.import_arcade_catalog("parallel-a", payload, expected_sync_version=0),
            peer.import_arcade_catalog("parallel-b", payload, expected_sync_version=0),
        )
        assert all(result.created == 2 for result in outcomes)

        # Only title or nonempty artist equality with a missing record suspends a new entry.
        await store.import_arcade_catalog("rename", {"songs": [{"title": "Old", "artist": "A"}]})
        for candidate in ({"title": "Old", "artist": "B"}, {"title": "New", "artist": "A"}):
            report = await store.import_arcade_catalog("rename", {"songs": [candidate]})
            assert report.pending[0]["reason"] == "possible_rename"
        report = await store.import_arcade_catalog("rename", {"songs": [{"title": "Unrelated", "artist": "B"}]})
        assert report.created == report.withdrawn == 1 and not report.pending

        # Warm fuzzy and marker queries reuse their index; candidate details may be hydrated.
        import arcade_helper.search.song_query as search_module
        built = 0
        original_build = search_module._build_search_index
        def count_build(*args):
            nonlocal built
            built += 1
            return original_build(*args)
        monkeypatch.setattr(search_module, "_build_search_index", count_build)
        for _ in range(2):
            await client.catalog.search_song("parallel-a", "Give It Up")
            await client.catalog.search_song("parallel-a", "Give It Upp")
            await client.catalog.search_song("parallel-a", "[未知标记][双人] Give It Upp")
        assert built == 2  # General and chart-marker indexes, once each.
        await client.lifecycle.stop()
    finally:
        await Tortoise.close_connections()


async def test_search_keeps_ambiguous_candidates_and_variant_constraints():
    songs = {
        1: SongData(id=1, title="東京", aliases=["Common-name", "共享別名"]),
        2: SongData(id=2, title="冬京", aliases=["Common name", "共享別名"]),
        3: SongData(id=3, title="[未知][双人] Give It Up", aliases=["Give It Up"]),
        4: SongData(id=4, title="Give It Up", aliases=["Give It Up"]),
        5: SongData(id=5, title="[Literal] Title", chart_variant=()),
    }
    service = SongSearchService(_MemorySongRepository(songs))
    for query in ("commonname", "gongxiangbieming", "dongjing"):
        results = await service.search_song(query, game_code="sdvx")
        assert {item.song_id for item in results} >= {1, 2}, query
    variant = await service.search_song("[未知][双人] Give It Upp", game_code="sdvx")
    assert {item.song_id for item in variant} == {3}
    assert (await service.search_song("[Literal] Title", game_code="sdvx"))[0].song_id == 5
    other = SongSearchService(_MemorySongRepository({8: SongData(id=8, title="Other", aliases=["Common name"])}))
    assert {item.song_id for item in await other.search_song("commonname", game_code="sdvx")} == {8}
    assert {item.song_id for item in await service.search_song("commonname", game_code="sdvx")} == {1, 2}


async def test_read_only_catalog_reuses_normalization_and_index(tmp_path, monkeypatch):
    import arcade_helper.games.arcade_songs as provider_module
    import arcade_helper.search.song_query as search_module

    counts = {"normalize": 0, "index": 0}
    normalize = provider_module.normalize_arcade_songs_catalog
    build_index = search_module._build_search_index
    def counted_normalize(*args):
        counts["normalize"] += 1
        return normalize(*args)
    def counted_index(*args):
        counts["index"] += 1
        return build_index(*args)
    monkeypatch.setattr(provider_module, "normalize_arcade_songs_catalog", counted_normalize)
    monkeypatch.setattr(search_module, "_build_search_index", counted_index)
    title = "Alpha Track"
    async def get_json(url, **kwargs):
        if url == ARCADE_SONGS_SITES_URL:
            return [{"gameCode": "sdvx", "dataSourceUrl": "https://test.invalid/sdvx"}]
        return {"songs": [{"title": title, "artist": "Artist", "sheets": []}]}
    client = ArcadeHelperClient(http_get_json=get_json, local_data_dir=tmp_path)
    await client.lifecycle.start()
    for _ in range(2):
        result = await client.catalog.arcade_songs.search("sdvx", "alphatrack")
        assert result[0].title == title
    assert counts == {"normalize": 1, "index": 1}
    title = "Beta Track"
    assert (await client.catalog.arcade_songs.search("sdvx", "betatrack", force_refresh=True))[0].title == title
    assert counts == {"normalize": 2, "index": 2}
    await client.lifecycle.stop()


def test_chart_markers_are_not_a_whitelist_or_destructive_title_cleanup():
    assert parse_chart_title("[未知][双人] Song") == ("Song", ("未知", "双人"))
    for title in ("Song [奏]", "[] Song", "[奏", "[奏]", "[奏][] Song"):
        assert parse_chart_title(title) == (title, ())
    assert parse_chart_title("[Actual Title] Song", literal=True) == ("[Actual Title] Song", ())
    normal = CatalogRecord(SongData(id=1, title="Song", artist="Artist"), {})
    marked = CatalogRecord(SongData(id=1, title="[自由标记] Song", artist="Artist"), {})
    assert normal.external_key != marked.external_key


async def test_work_matching_yields_during_candidates_and_loads_only_possible_merge_payloads(tmp_path, monkeypatch):
    import arcade_helper.catalog.matcher as matcher
    from arcade_helper.storage.tortoise import MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE
    from arcade_helper.storage.tortoise.alias_models import WorkCandidate
    await Tortoise.init(db_url=f"sqlite://{tmp_path / 'matching.sqlite3'}", modules={"models": [MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE]})
    try:
        await initialize_catalog_storage()
        entries = [(f"Unrelated title {i:03}", "sdvx", [], False) for i in range(65)]
        entries += [(title, game, variant, music) for title, variant, music in [
            ("Merge Song", [], False), ("Variant Song", [], True),
            ("[Literal] Tune", [], False), ("Unknown Song", None, False),
        ] for game in ("maimai", "chunithm")]
        works = [MusicWork(display_title=title, artist="Artist") for title, *_ in entries]
        await MusicWork.bulk_create(works)
        await GameSong.bulk_create([GameSong(work_id=work.id, title=title, artist="Artist", game_code=game,
            chart_variant=variant, catalog_payload={"music_variant": music, "unused": "x" * 20000})
            for work, (title, game, variant, music) in zip(works, entries)])
        db = Tortoise.get_connection("default")
        extract = matcher.process.extract
        calls, heartbeats, payload_rows = 0, [], []
        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return extract(*args, **kwargs)
        query = db.execute_query
        async def measured(sql, *args, **kwargs):
            result = await query(sql, *args, **kwargs)
            if sql.startswith("SELECT") and 'FROM "game_songs"' in sql and '"catalog_payload"' in sql:
                payload_rows.append(len(result[1]))
                assert '"id" IN' in sql  # No full-catalog payload scan.
            return result
        done = False
        async def pulse():
            while not done:
                heartbeats.append(calls)
                await asyncio.sleep(0)
        monkeypatch.setattr(matcher.process, "extract", counted)
        monkeypatch.setattr(db, "execute_query", measured)
        heartbeat = asyncio.create_task(pulse())
        try:
            assert await matcher.match_catalog(db) == 2
        finally:
            done = True
            await heartbeat
        assert any(0 < count < calls for count in heartbeats)
        assert sum(payload_rows) == 6  # Exact/title/artist/variant candidates only.
        for title, merged in [("Merge Song", True), ("[Literal] Tune", True), ("Variant Song", False), ("Unknown Song", False)]:
            rows = await GameSong.filter(title=title).values_list("work_id", flat=True)
            assert (len(set(rows)) == 1) is merged
        assert await WorkCandidate.filter(status="pending").count() >= 2
    finally:
        await Tortoise.close_connections()


async def test_batched_import_keeps_source_priority_and_reads_snapshots_only_when_required(tmp_path, monkeypatch):
    from arcade_helper.catalog.importer import content_key
    await Tortoise.init(db_url=f"sqlite://{tmp_path / 'batches.sqlite3'}", modules={"models": [
        "arcade_helper.storage.tortoise.models", "arcade_helper.storage.tortoise.catalog_models"]})
    try:
        await initialize_catalog_storage()
        store = TortoiseCatalogStore()
        records = [CatalogRecord(SongData(id=i, title=f"Song {i}", artist="Artist", bpm=100, chart_variant=()), {"native": i}, str(i)) for i in range(258)]
        first = await store.import_records("sdvx", records, provider="test")
        priority = CatalogRecord(SongData(id=0, title="Priority Song", artist="Artist", bpm=777, chart_variant=()), {}, "0")
        await SongReference.create(game_song_id=first.song_ids["0"], provider="aaa", namespace="default", game_code="sdvx",
            reference_key=content_key(["aaa", "default", "sdvx", "native", "0"]), external_key="0", key_kind="native",
            identity_material=priority.identity, payload=priority.song.model_dump(mode="json"), raw_payload={})
        changed = [CatalogRecord(r.song.model_copy(update={"bpm": 200}), r.raw, r.native_key) for r in records]
        from tortoise.backends.sqlite.client import SqliteClient
        query, query_dict = SqliteClient.execute_query, SqliteClient.execute_query_dict
        reads = []
        async def measured(self, sql, *args, **kwargs):
            result = await query(self, sql, *args, **kwargs)
            if sql.startswith("SELECT"):
                reads.append((sql, len(result[1])))
            return result
        async def measured_dict(self, sql, *args, **kwargs):
            result = await query_dict(self, sql, *args, **kwargs)
            reads.append((sql, len(result)))
            return result
        with monkeypatch.context() as patch:
            patch.setattr(SqliteClient, "execute_query", measured)
            patch.setattr(SqliteClient, "execute_query_dict", measured_dict)
            updated = await store.import_records("sdvx", changed, provider="test")
            assert updated.song_ids == first.song_ids and updated.updated == 258
            assert await store.get_sync_version("sdvx", provider="test") == updated.sync_version
            assert (await store.get_import("sdvx", provider="test"))["updated"] == 258
        assert all('"raw_payload"' not in sql and '"observed_payload"' not in sql for sql, _ in reads if 'FROM "catalog_imports"' in sql)
        reference_reads = [(sql, count) for sql, count in reads if 'FROM "song_references"' in sql]
        assert '"payload"' not in reference_reads[0][0]
        assert all(count <= 257 for sql, count in reference_reads if '"payload"' in sql)
        assert (await store.get_song_by_id("sdvx", first.song_ids["0"])).title == "Priority Song"
        assert (await store.get_song_by_id("sdvx", first.song_ids["257"])).bpm == 200
        repeated = await store.import_records("sdvx", changed, provider="test")
        assert repeated.unchanged == 258 and repeated.revision == updated.revision
        removed = await store.import_records("sdvx", [], provider="aaa", allow_empty=True)
        assert removed.withdrawn == 1
        restored = await store.get_song_by_id("sdvx", first.song_ids["0"])
        assert restored.title == "Song 0" and restored.bpm == 200
        snapshot = (await CatalogImport.get(provider="test")).raw_payload
        await store.import_records("sdvx", changed[:1], provider="test", complete=False)
        assert (await CatalogImport.get(provider="test")).raw_payload == snapshot
        assert len(await store.load_song_index("sdvx")) == 258
    finally:
        await Tortoise.close_connections()


async def test_legacy_registration_does_not_create_catalog_tables(tmp_path):
    from arcade_helper.core.errors import StorageNotConfiguredError
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'legacy.sqlite3'}",
        modules={"models": ["arcade_helper.storage.tortoise.models"]},
    )
    try:
        await Tortoise.generate_schemas(safe=True)
        with pytest.raises(StorageNotConfiguredError, match="CATALOG_MODEL_MODULE"):
            await initialize_catalog_storage()
        rows = await Tortoise.get_connection("default").execute_query_dict("SELECT name FROM sqlite_master WHERE type='table'")
        assert not {"music_works", "game_songs", "song_references", "catalog_imports", "alias_revision"} & {row["name"] for row in rows}
    finally:
        await Tortoise.close_connections()


async def test_catalog_invalidation_reaches_host_owned_search_service():
    from arcade_helper.catalog import SongCatalogService
    from arcade_helper.providers import ProviderRegistry
    repository = _MemorySongRepository({1: SongData(id=1, title="Unrelated", aliases=["Old alias"])})
    catalog = SongCatalogService(providers=ProviderRegistry(), song_store=repository)
    host_search = SongSearchService(repository)
    await host_search.search_song("oldalias", game_code="quality")
    repository.songs[1].aliases = ["共享別名", "Shared alias"]
    catalog.invalidate_search_cache("quality")
    for query in ("gongxiangbieming", "shared alias"):
        result = await host_search.query_song_fuzzy(query, game_code="quality")
        assert {item.song_id for item in result} == {1}
