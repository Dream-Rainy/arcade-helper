"""Acceptance scenarios for shared provenance and human moderation."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from tortoise import Tortoise
from tortoise.exceptions import DoesNotExist

from arcade_helper.aliases import AliasService, AliasObservation, AliasPermissionError, AliasConflictError
from arcade_helper.catalog import CatalogRecord
from arcade_helper.core.song import SongData
from arcade_helper.storage.tortoise import (
    MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE,
    TortoiseCatalogStore, initialize_catalog_storage, upgrade_catalog_schema,
)
from arcade_helper.storage.tortoise.alias_models import AliasRequest, AliasEvent, AliasEvidence, AliasSource
from arcade_helper.storage.tortoise.catalog_models import GameSong, MusicWork


async def assert_text_projection_matches_evidence(store):
    from arcade_helper.storage.tortoise.alias_store import TortoiseAliasStore
    aliases = TortoiseAliasStore(store)
    rich = await aliases.projection()
    assert await aliases._alias_texts() == {
        sid: list(dict.fromkeys(e["text"] for e in evidence if e["kind"] == "alias")) for sid, evidence in rich.items()
    }


def policy(actor, action, game):
    return action == "submit" or actor in {10, 11} or (actor == 12 and action == "review" and game == "maimai")


@pytest.fixture
async def catalog(tmp_path):
    url = f"sqlite://{tmp_path / 'aliases.sqlite3'}"
    connection = json.loads(os.environ['ARCADE_HELPER_TEST_DB_CONFIG']) if os.environ.get('ARCADE_HELPER_TEST_DB_CONFIG') else url
    admin, schema = None, None
    if isinstance(connection, dict):
        import asyncpg
        schema = 'alias_case_' + uuid4().hex
        credentials = connection['credentials']
        admin = await asyncpg.connect(**{k: v for k, v in credentials.items() if k in {'host', 'port', 'user', 'password', 'database'}})
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        connection = {**connection, 'credentials': {**credentials, 'schema': schema}}
    try:
        await Tortoise.init(config={"connections": {"default": connection, "peer": connection}, "apps": {
            "models": {"models": [MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE], "default_connection": "default"},
        }})
        await initialize_catalog_storage()
        store = TortoiseCatalogStore(enable_aliases=True)
        yield store, AliasService(store, policy=policy)
    finally:
        await Tortoise.close_connections()
        if admin is not None:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()


async def import_song(store, game, *, title="Shared Song", artist="Artist", aliases=(), source=None, native="1"):
    song = SongData(id=int(native), title=title, artist=artist,
                   alias_observations=[AliasObservation(source_key=source or f"lxns:{game}", text=text) for text in aliases],
                   alias_complete_sources=[source or f"lxns:{game}"])
    report = await store.import_records(game, [CatalogRecord(song, song.model_dump(mode="json"), native)],
                                        provider="catalog", namespace="native", complete=False, alias_complete=True)
    return report.song_ids[native]


async def test_text_mode_reuses_prewarm_and_invalidates_alias_metadata(catalog, monkeypatch):
    store, aliases = catalog
    sid = await import_song(store, "sdvx", title="Transcend Lights", aliases=["shared nickname"])
    await store.text_search._warm_search_index("sdvx")
    index = store.text_search._cache.indexes[("sdvx", None)]
    full_reads = []
    original = store.get_songs_by_ids
    async def full(*args):
        full_reads.append(args)
        return await original(*args)
    monkeypatch.setattr(store, "get_songs_by_ids", full)
    assert (await aliases.search_all("Transcend Light", text_only=True)).candidates[0].game_song_id == sid
    assert index is store.text_search._cache.indexes[("sdvx", None)] and not full_reads
    await import_song(store, "sdvx", title="Transcend Lights", aliases=["changed nickname"])
    assert not (await aliases.search_all("shared nickname", text_only=True)).candidates
    assert (await aliases.search_all("changed nickname", text_only=True)).candidates[0].game_song_id == sid


async def approve(service, operation, payload, *, actor=1, reviewer=10, key=None):
    request = await service.submit(actor_id=actor, operation=operation, payload=payload,
                                   idempotency_key=key or f"{operation}-{await AliasRequest.all().count()}")
    await service.review(request, actor_id=reviewer, approve=True, expected_version=1, reason="checked")
    return request


async def test_arcade_source_continuity_and_partial_recovery(catalog):
    from arcade_helper.catalog.importer import prepare_arcade_catalog
    from arcade_helper.games.arcade_songs import normalize_arcade_song
    from arcade_helper.storage.tortoise.catalog_models import SongReference, CatalogImport

    store, service = catalog
    old = {"songId": "cat's scat", "title": "cat's scat", "artist": "Q-Mex", "version": "Home", "sheets": []}
    unrelated = {"songId": "old", "title": "Unrelated old", "artist": "Other", "sheets": []}
    legacy = await store.import_records("popn", [CatalogRecord(normalize_arcade_song(r, song_id=0), r) for r in (old, unrelated)],
                                        provider="arcade_songs", raw_payload={"songs": [old, unrelated]})
    anchor = legacy.song_ids[CatalogRecord(normalize_arcade_song(old, song_id=0), old).external_key]
    ref = await SongReference.get(game_song_id=anchor)
    title_evidence = await AliasEvidence.get(anchor_id=anchor, kind="title")
    await approve(service, "RETRACT_EVIDENCE", {"evidence_id": title_evidence.id})
    await approve(service, "ADD_ALIAS", {"game_song_id": anchor, "text": "Legacy Nick"})
    await approve(service, "BLOCK_ALIAS", {"game_song_id": anchor, "text": "Legacy Nick"})
    newer = {**old, "songId": "cat's scat (2)", "version": "Peace"}
    # New edition before old edition must not steal or retire the existing anchor.
    migrated = await store.import_arcade_catalog("popn", {"songs": [newer, old]})
    assert not migrated.pending and migrated.created == 1 and migrated.withdrawn == 1
    assert (await SongReference.get(id=ref.id)).game_song_id == anchor
    assert (await AliasEvidence.get(id=title_evidence.id)).moderation_state == "revoked"
    assert await AliasEvidence.filter(anchor_id=anchor, kind="title").count() == 1
    assert not (await service.resolve_alias("Legacy Nick")).candidates
    result = await service.search_all("cat's scat", game_code="popn")
    assert len(result.candidates) == 2 and anchor in {c.game_song_id for c in result.candidates}
    repeat = await store.import_arcade_catalog("popn", {"songs": [old, newer]})
    assert repeat.unchanged == repeat.source_record_count == repeat.processed_record_count == 2
    assert repeat.available_song_count == 2 and repeat.revision == migrated.revision
    await import_song(store, "jubeat", title="cat's scat", artist="Q-Mex")
    assert len(set(await GameSong.filter(title="cat's scat").values_list("work_id", flat=True))) == 3

    missing = {"songId": "concon", "title": "concon〜INAReMIX〜", "artist": None, "sheets": []}
    first = await store.import_arcade_catalog("gitadora", {"songs": [missing]})
    song_id = next(iter(first.song_ids.values()))
    assert first.outcome == "complete" and not first.pending
    assert (await service.search_all(missing["title"])).candidates[0].game_song_id == song_id
    enriched = await store.import_arcade_catalog("gitadora", {"songs": [{**missing, "artist": "Composer", "bpm": 190}]})
    assert list(enriched.song_ids.values()) == [song_id] and enriched.updated == 1
    assert not enriched.pending

    # A real conflicting group keeps related records, while unrelated missing data retires.
    base = {"title": "Conflict", "artist": "A", "sheets": []}
    vanished = {"title": "Vanished", "artist": "B", "sheets": []}
    await store.import_arcade_catalog("test", {"songs": [base, vanished]})
    partial = await store.import_arcade_catalog("test", {"songs": [base, {**base, "bpm": 200}]})
    assert partial.withdrawn == 1 and partial.pending_record_count == 2 and partial.outcome == "partial"
    pending = partial.pending[0]
    await approve(service, "MAP_SOURCE_RECORD", {"game_code": "test", "provider": "arcade_songs",
        "external_key": pending["external_key"], "record_selector": pending["records"][1]["record_selector"], "decision": "reject"})
    resolved = await store.import_arcade_catalog("test", {"songs": [base, {**base, "bpm": 200}]})
    assert resolved.outcome == "complete" and not resolved.pending and resolved.rejected_record_count == 1
    assert resolved.source_record_count == resolved.processed_record_count + resolved.rejected_record_count == 2
    assert (await CatalogImport.get(game_code="test")).raw_payload is not None


async def test_catalog_preparation_singleflight_failures_and_shutdown(catalog, tmp_path, monkeypatch):
    from arcade_helper import ArcadeHelperClient
    from arcade_helper.core.errors import CatalogSyncError
    from arcade_helper.games.arcade_songs import ARCADE_SONGS_SITES_URL
    from arcade_helper.catalog import service as catalog_module

    store, _ = catalog
    entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    waiting_release = asyncio.Event()
    calls = []
    clock = [100.0]
    broken = [True]
    monkeypatch.setattr(catalog_module, "monotonic", lambda: clock[0])
    titles = {"gitadora": "concon〜INAReMIX〜", "popn": "Waiting Song", "sdvx": "Recovered Song"}
    async def get_json(url, **kwargs):
        calls.append(url)
        if url == ARCADE_SONGS_SITES_URL:
            return [{"gameCode": game, "dataSourceUrl": f"https://fixture.test/{game}"} for game in titles] + [
                {"gameCode": "maimai", "dataSourceUrl": "https://fixture.test/maimai"},
                {"gameCode": "any", "dataSourceUrl": "https://fixture.test/any", "isHidden": True}]
        game = url.split("/")[-2]
        if game == "gitadora":
            entered.set()
            await release.wait()
        if game == "popn":
            waiting.set()
            await waiting_release.wait()
        if game == "sdvx" and broken[0]:
            raise ConnectionError("fixture transport failed")
        return {"songs": [{"songId": game, "title": titles[game], "artist": None, "sheets": []}]}

    client = ArcadeHelperClient(http_get_json=get_json, local_data_dir=tmp_path, catalog_store=store)
    await client.lifecycle.start()
    first = asyncio.create_task(client.catalog.search_song("gitadora", titles["gitadora"]))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(client.catalog.search_song("gitadora", titles["gitadora"]))
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 3)
        assert all(r[0].title == titles["gitadora"] for r in results)
        assert results[0][0].song_id == results[1][0].song_id
        assert calls.count("https://fixture.test/gitadora/data.json") == 1
        result = await client.catalog.search_all(titles["gitadora"], text_only=True)
        assert result.candidates and result.inferred_game is None
        assert set(result.unavailable_games) == {"popn", "sdvx"}
        await asyncio.wait_for(waiting.wait(), 2)
        with pytest.raises(CatalogSyncError, match="initializing or unavailable"):
            await client.catalog.search_all(titles["popn"])
        with pytest.raises(CatalogSyncError):
            await client.catalog.search_song("sdvx", titles["sdvx"])
        attempts = calls.count("https://fixture.test/sdvx/data.json")
        with pytest.raises(CatalogSyncError):
            await client.catalog.search_song("sdvx", titles["sdvx"])
        assert attempts == calls.count("https://fixture.test/sdvx/data.json") == 1
        clock[0] += 31
        broken[0] = False
        assert (await client.catalog.search_song("sdvx", titles["sdvx"]))[0].title == titles["sdvx"]
        assert calls.count("https://fixture.test/sdvx/data.json") == 2
        task = client.catalog._catalog_tasks["popn"]
        await asyncio.wait_for(client.lifecycle.stop(), 2)
        assert task.cancelled() and client.state == "stopped"
    finally:
        release.set()
        await client.lifecycle.stop()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    waiting_release.set()
    warmed = ArcadeHelperClient(http_get_json=get_json, local_data_dir=tmp_path, catalog_store=store)
    await warmed.lifecycle.start()
    try:
        prepared = await warmed.catalog.prepare_catalogs()
        assert set(prepared) == set(titles)
        assert (await warmed.catalog.search_all(titles["popn"])).inferred_game == "popn"
        count = len(calls)
        await warmed.catalog.search_song("popn", titles["popn"])
        await warmed.catalog.search_all(titles["gitadora"])
        assert len(calls) == count  # Warm searches never fetch all catalogs again.
        assert not any("/any/" in url or "/maimai/" in url for url in calls)
    finally:
        await warmed.lifecycle.stop()


async def test_first_import_race_between_independent_catalog_connections(catalog):
    from arcade_helper import ArcadeHelperClient
    from arcade_helper.games.arcade_songs import ARCADE_SONGS_SITES_URL

    store, _ = catalog
    peer = TortoiseCatalogStore(connection_name="peer", enable_aliases=True)
    entered, release = asyncio.Event(), asyncio.Event()
    fetches = 0
    async def get_json(url, **kwargs):
        nonlocal fetches
        if url == ARCADE_SONGS_SITES_URL:
            return [{"gameCode": "race", "dataSourceUrl": "https://fixture.test/race"}]
        fetches += 1
        if fetches == 2:
            entered.set()
        await release.wait()
        return {"songs": [{"songId": "one", "title": "Race Song", "artist": None, "sheets": []}]}
    clients = [ArcadeHelperClient(http_get_json=get_json, catalog_store=backend) for backend in (store, peer)]
    for client in clients:
        await client.lifecycle.start()
    tasks = [asyncio.create_task(client.catalog.search_song("race", "Race Song")) for client in clients]
    try:
        await asyncio.wait_for(entered.wait(), 3)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 3)
        assert results[0][0].song_id == results[1][0].song_id
        assert await GameSong.filter(game_code="race").count() == 1
        assert await store.get_sync_version("race") == 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for client in clients:
            await client.lifecycle.stop()


async def test_pending_work_deduplication_reuses_legacy_keys_and_keeps_distinct_entries(catalog):
    from arcade_helper.catalog.importer import content_key
    store, service = catalog
    first = await import_song(store, 'maimai')
    linked = await import_song(store, 'chunithm')
    other = await import_song(store, 'maimai', title='Other Work', artist='Other', native='2')
    payload = {'game_song_id': first, 'text': 'LIGHT', 'scope': 'work', 'origin_game': None}
    request = await service.submit(actor_id=1, operation='ADD_ALIAS', payload=payload, idempotency_key='first')
    # Upgrade compatibility: an old pending key was tied to the first game's entry.
    await AliasRequest.filter(id=request).update(pending_key=content_key(['ADD_ALIAS', {**payload, 'text': 'light'}]))
    assert await service.submit(actor_id=11, operation='ADD_ALIAS', payload={**payload, 'game_song_id': linked, 'text': 'Ｌｉｇｈｔ'}, idempotency_key='linked') == request
    assert await AliasRequest.all().count() == 1
    with pytest.raises(AliasPermissionError):
        await service.review(request, actor_id=11, approve=True, expected_version=1, reason='co-submitter')
    for song in (first, other):
        await service.submit(actor_id=1, operation='ADD_ALIAS', payload={**payload, 'scope': 'game_song', 'origin_game': 'maimai', 'game_song_id': song}, idempotency_key=f'entry-{song}')
    assert await AliasRequest.all().count() == 3
    await service.review(request, actor_id=10, approve=True, expected_version=1, reason='checked')
    assert {c.game_song_id for c in (await service.resolve_alias('light')).candidates} == {first, linked}


async def test_exact_queries_read_only_relevant_evidence_and_no_catalog_payload(catalog, monkeypatch):
    store, service = catalog
    await import_song(store, 'maimai', title='Unrelated', artist='Other', native='2', source='noise:maimai', aliases=[f'unrelated-{i}' for i in range(60)])
    first = await import_song(store, 'maimai', aliases=['Shared Nick'])
    linked = await import_song(store, 'chunithm')
    db = store._db
    original = db.execute_query
    original_dict = db.execute_query_dict
    evidence_rows = []
    async def measured(sql, *args, **kwargs):
        assert 'catalog_payload' not in sql
        result = await original(sql, *args, **kwargs)
        if 'FROM "alias_evidence"' in sql:
            evidence_rows.append(len(result[1]))
        return result
    async def measured_dict(sql, *args, **kwargs):
        assert 'catalog_payload' not in sql and 'last_seen_at' not in sql
        result = await original_dict(sql, *args, **kwargs)
        if 'FROM "alias_evidence"' in sql:
            evidence_rows.append(len(result))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(db, 'execute_query', measured)
        patch.setattr(db, 'execute_query_dict', measured_dict)
        result = await service.search_all('Shared Nick')
        assert {c.game_song_id for c in result.candidates} == {first, linked}
        assert max(evidence_rows) == 1
        evidence_rows.clear()
        aliases = await service.get_effective_aliases(linked)
        assert any(e['text'] == 'Shared Nick' for e in aliases)
        assert max(evidence_rows) <= 3  # Two titles and the one shared alias, excluding unrelated songs.


async def test_read_cache_avoids_catalog_reads_and_refreshes_after_decisions(catalog, monkeypatch):
    store, service = catalog
    song = await import_song(store, 'maimai', aliases=['Old Nick'])
    assert (await service.resolve_alias('Old Nick')).candidates
    aliases = await service.get_effective_aliases(song)
    sql_calls = []
    original = store._db.execute_query
    async def measured(sql, *args, **kwargs):
        sql_calls.append(sql)
        return await original(sql, *args, **kwargs)
    monkeypatch.setattr(store._db, 'execute_query', measured)
    assert (await service.resolve_alias('Old Nick')).candidates
    copy = await service.get_effective_aliases(song)
    assert len(sql_calls) == 2 and all('alias_revision' in sql for sql in sql_calls)
    copy.clear()
    assert await service.get_effective_aliases(song) == aliases

    request = await service.submit(actor_id=1, operation='ADD_ALIAS', idempotency_key='cache',
                                  payload={'game_song_id': song, 'text': 'New Nick'})
    sql_calls.clear()
    assert (await service.resolve_alias('Old Nick')).candidates
    assert len(sql_calls) == 1  # Pending requests do not invalidate effective data.
    await service.review(request, actor_id=10, approve=True, expected_version=1, reason='checked')
    assert (await service.resolve_alias('New Nick')).candidates[0].game_song_id == song
    assert any(e['text'] == 'New Nick' for e in await service.get_effective_aliases(song))
    await service.configure_source('lxns:maimai', actor_id=10, origin_game='maimai', enabled=False)
    assert not (await service.resolve_alias('Old Nick')).candidates
    assert all(e['text'] != 'Old Nick' for e in await service.get_effective_aliases(song))
    await service.get_effective_aliases(song, include_inactive=True, actor_id=10)
    with pytest.raises(AliasPermissionError):
        await service.get_effective_aliases(song, include_inactive=True, actor_id=1)
    await import_song(store, 'maimai', title='New Song', native='2')
    assert (await service.search_all('New Song')).candidates[0].title == 'New Song'


async def test_read_cache_isolated_when_database_connection_is_replaced(catalog, tmp_path):
    store, service = catalog
    song = await import_song(store, 'maimai', title='Before', aliases=['old nick'])
    revision = await store.get_revision()
    assert (await service.search_all('Before')).candidates
    assert await service.get_effective_aliases(song)
    await Tortoise.close_connections()
    await Tortoise.init(db_url=f"sqlite://{tmp_path / 'replacement.sqlite3'}", modules={
        'models': [MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE]})
    await initialize_catalog_storage()
    replacement = await import_song(store, 'maimai', title='After', aliases=['new nick'])
    assert await store.get_revision() == revision
    assert (await service.search_all('After')).candidates[0].title == 'After'
    assert not (await service.resolve_alias('old nick')).candidates
    assert all(e['text'] != 'old nick' for e in await service.get_effective_aliases(replacement))


async def test_postgres_import_does_not_block_requests_or_invalidate_catalog_on_submission(catalog, monkeypatch):
    from arcade_helper.storage.tortoise.alias_store import TortoiseAliasStore
    if catalog[0]._db.capabilities.dialect != 'postgres':
        pytest.skip('Requires PostgreSQL row locks; run with ARCADE_HELPER_TEST_DB_CONFIG in an isolated schema')
    store, _ = catalog
    song = await import_song(store, 'maimai')
    peer = TortoiseCatalogStore(connection_name='peer', enable_aliases=True)
    service = AliasService(peer, policy=policy, daily_limit=1, pending_limit=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original = TortoiseAliasStore.ingest_catalog
    async def paused(self, *args, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(TortoiseAliasStore, 'ingest_catalog', paused)
    before = await peer.get_revision()
    importing = asyncio.create_task(import_song(store, 'maimai'))
    approving = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        payload = {'game_song_id': song, 'text': 'During Import'}
        concurrent = await asyncio.wait_for(asyncio.gather(
            service.submit(actor_id=1, operation='ADD_ALIAS', payload=payload, idempotency_key='during'),
            service.submit(actor_id=1, operation='ADD_ALIAS', payload=payload, idempotency_key='during-retry'),
        ), 3)
        assert concurrent[0] == concurrent[1]
        request = concurrent[0]
        assert await asyncio.wait_for(service.submit(actor_id=2, operation='ADD_ALIAS', payload=payload, idempotency_key='joint'), 3) == request
        with pytest.raises(AliasConflictError, match='DAILY_LIMIT'):
            await asyncio.wait_for(service.submit(actor_id=1, operation='ADD_ALIAS', payload={**payload, 'text': 'Too Many'}, idempotency_key='limit'), 3)
        approving = asyncio.create_task(service.review(request, actor_id=10, approve=True, expected_version=1, reason='checked'))
        await asyncio.sleep(0.05)
        assert not approving.done()
        await asyncio.wait_for(service.withdraw(request, actor_id=1, expected_version=1), 3)
    finally:
        release.set()
        await asyncio.wait_for(importing, 5)
        if approving is not None:
            with pytest.raises(AliasConflictError, match='ALREADY_DECIDED'):
                await asyncio.wait_for(approving, 5)
    assert await peer.get_revision() == before
    assert (await AliasRequest.get(id=request).using_db(peer._db)).status == 'withdrawn'


async def test_summary_pagination_filters_private_requests_and_redacts_co_submitter_metadata(catalog, monkeypatch):
    store, service = catalog
    song = await import_song(store, 'maimai')
    await import_song(store, 'chunithm')
    payload = {'game_song_id': song, 'origin_game': None, 'text': 'private', 'evidence_url': 'https://example.test/private'}
    await service.submit(actor_id=2, operation='ADD_ALIAS', payload=payload, idempotency_key='hidden')
    first = await service.submit(actor_id=1, operation='ADD_ALIAS', payload={**payload, 'text': 'first'}, idempotency_key='first')
    second = await service.submit(actor_id=1, operation='ADD_ALIAS', payload={**payload, 'text': 'second'}, idempotency_key='second')
    assert await service.submit(actor_id=3, operation='ADD_ALIAS', payload={**payload, 'text': 'second'}, idempotency_key='joint') == second
    async def no_details(*args, **kwargs):
        pytest.fail('summary pagination must not load full request details')
    monkeypatch.setattr(service._store, 'get_request', no_details)
    page = await service.list_requests(actor_id=1, limit=1, game_code='chunithm', include_details=False)
    assert [r['id'] for r in page['items']] == [first]
    assert page['has_more'] and page['next_cursor'] == first
    last = await service.list_requests(actor_id=1, limit=1, after_id=first, include_details=False)
    assert [r['id'] for r in last['items']] == [second] and not last['has_more']
    joint = await service.list_requests(actor_id=3, limit=1, include_details=False)
    assert [r['id'] for r in joint['items']] == [second] and not joint['has_more']
    assert 'evidence_url' not in joint['items'][0]['payload']


async def test_shared_alias_inference_variants_and_reversible_association(catalog):
    store, service = catalog
    mai = await import_song(store, "maimai", aliases=["Shared Nick"])
    chuni = await import_song(store, "chunithm")
    assert (await GameSong.get(id=mai)).work_id == (await GameSong.get(id=chuni)).work_id
    await assert_text_projection_matches_evidence(store)
    result = await service.resolve_alias("shared nick")
    assert result.inferred_game == "maimai" and result.candidates[0].game_code == "maimai"
    assert {c.game_song_id for c in result.candidates} == {mai, chuni}
    revision = await store.get_revision()
    await import_song(store, "maimai", aliases=["Shared Nick"])
    assert await store.get_revision() == revision
    assert (await service.resolve_alias("Shared Nick", game_code="chunithm")).candidates[0].shared
    await import_song(store, "chunithm", aliases=["Shared Nick"])
    assert (await service.resolve_alias("Shared Nick")).inferred_game is None
    fuzzy = await service.search_all("shared nic")
    assert fuzzy.candidates and fuzzy.inferred_game is None

    special = await import_song(store, "maimai", title="[新标记] Shared Song", native="2")
    assert (await GameSong.get(id=special)).work_id != (await GameSong.get(id=mai)).work_id
    candidates = await service.list_candidates(actor_id=10)
    candidate = next(c for c in candidates if special in {c["left_id"], c["right_id"]})
    await service.decide_candidate(candidate["id"], actor_id=10, accept=True, expected_version=candidate["version"], reason="same audio confirmed")
    await approve(service, "ADD_ALIAS", {"game_song_id": special, "text": "Special Nick", "scope": "game_song"})
    assert {c.game_song_id for c in (await service.resolve_alias("Special Nick")).candidates} == {special}
    await assert_text_projection_matches_evidence(store)
    assert {c.game_song_id for c in (await service.search_all("[新标记] Shared Song")).candidates} == {special}
    await approve(service, "BLOCK_ALIAS", {"game_song_id": mai, "text": "Shared Nick"})
    await approve(service, "RELINK_GAME_SONGS", {"song_ids": [chuni], "mode": "split"})
    assert not (await service.resolve_alias("Shared Nick")).candidates
    await assert_text_projection_matches_evidence(store)
    await service.associate(actor_id=10)
    assert (await GameSong.get(id=mai)).work_id != (await GameSong.get(id=chuni)).work_id
    await approve(service, "UNBLOCK_ALIAS", {"game_song_id": chuni, "text": "Shared Nick"})
    assert {c.game_song_id for c in (await service.resolve_alias("Shared Nick")).candidates} == {chuni}
    await assert_text_projection_matches_evidence(store)


async def test_cross_game_search_rejects_artist_only_hit_in_unrelated_query(catalog):
    store, service = catalog
    for game in ("maimai", "chunithm"):
        records = []
        for index in range(25):
            song = SongData(id=index + 1, title=f"Example Track {index}", artist="Teto" if index < 2 else "Other",
                            alias_observations=[AliasObservation(source_key=f"lxns:{game}", text="做眼保健操")] if index == 2 else [])
            records.append(CatalogRecord(song, song.model_dump(mode="json"), str(song.id)))
        await store.import_records(game, records, provider="catalog", namespace="native", complete=False)

    assert not (await service.search_all("teto做眼保健操")).candidates
    supported = await service.search_all("teto example")
    assert {candidate.game_code for candidate in supported.candidates} == {"maimai", "chunithm"}
    assert {candidate.title for candidate in supported.candidates} == {"Example Track 0", "Example Track 1"}
    assert {candidate.title for candidate in (await service.search_all("做眼保健操")).candidates} == {"Example Track 2"}


async def test_cross_game_short_queries_and_incoherent_aliases(catalog):
    store, service = catalog
    for game in ("maimai", "chunithm"):
        await import_song(store, game, title="测试", aliases=["红苹果", "蓝香蕉", "teto做眼保健操", "🍥"])
    for query in ("shi", "测", "测式", "红苹果蓝香蕉"):
        assert not (await service.search_all(query)).candidates
    for query in ("cs", "测试", "teto做眼保健操", "🍥"):
        result = await service.search_all(query)
        assert {candidate.game_code for candidate in result.candidates} == {"maimai", "chunithm"}


async def test_review_authorization_idempotency_concurrency_and_rollback(catalog, monkeypatch):
    store, service = catalog
    song = await import_song(store, "maimai")
    request = await service.submit(actor_id=10, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Pending Nick"}, idempotency_key="retry")
    assert not (await service.resolve_alias("Pending Nick")).candidates
    assert await service.submit(actor_id=10, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Pending Nick"}, idempotency_key="retry") == request
    with pytest.raises(AliasConflictError, match="IDEMPOTENCY"):
        await service.submit(actor_id=10, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Changed"}, idempotency_key="retry")
    for actor in (10, 2):
        with pytest.raises(AliasPermissionError):
            await service.review(request, actor_id=actor, approve=True, expected_version=1, reason="no")
    with pytest.raises(AliasPermissionError):
        await AliasService(store).review(request, actor_id=11, approve=True, expected_version=1, reason="no policy")
    other = AliasService(TortoiseCatalogStore(connection_name="peer", enable_aliases=True), policy=policy)
    decisions = await asyncio.gather(
        service.review(request, actor_id=11, approve=True, expected_version=1, reason="yes"),
        other.review(request, actor_id=12, approve=True, expected_version=1, reason="yes"),
        return_exceptions=True,
    )
    assert decisions.count("approved") == 1
    assert sum(isinstance(item, AliasConflictError) for item in decisions) == 1
    assert len((await other.resolve_alias("Pending Nick")).candidates) == 1

    rollback = await service.submit(actor_id=1, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Rollback Nick"}, idempotency_key="rollback")
    version = await store.get_revision()
    events = await AliasEvent.all().count()
    original_event = service._store.event
    async def fail(db, action, **kwargs):
        if action == "approved":
            raise RuntimeError("commit failure")
        return await original_event(db, action, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(service._store, "event", fail)
        with pytest.raises(RuntimeError):
            await service.review(rollback, actor_id=10, approve=True, expected_version=1, reason="yes")
    assert (await AliasRequest.get(id=rollback)).status == "pending"
    assert not (await other.resolve_alias("Rollback Nick")).candidates
    assert await AliasEvent.all().count() == events and await store.get_revision() == version
    await service.withdraw(rollback, actor_id=1, expected_version=1)
    with pytest.raises(AliasPermissionError):
        await service.get_request(rollback, actor_id=2)
    assert (await service.events(actor_id=1))["items"]
    assert not (await service.events(actor_id=2))["items"]
    shared = await service.submit(actor_id=1, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Joint", "evidence_url": "https://example.test/private"}, idempotency_key="joint1")
    assert await service.submit(actor_id=11, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Joint"}, idempotency_key="joint2") == shared
    with pytest.raises(AliasPermissionError):
        await service.review(shared, actor_id=11, approve=True, expected_version=1, reason="co-submitter")
    assert "evidence_url" not in (await service.get_request(shared, actor_id=11))["payload"]
    await service.supersede(shared, actor_id=10, expected_version=1, reason="duplicate proposal withdrawn from consideration")


async def test_sources_retraction_blocking_and_stale_sync(catalog):
    store, service = catalog
    song = await import_song(store, "maimai", aliases=["Many Sources"])
    await service.configure_source("second:maimai", actor_id=10, origin_game="maimai", trusted_import=True)
    observations = [{"anchor_id": song, "text": "Many Sources", "record_key": "one"}]
    await service.sync_source("second:maimai", observations, actor_id=10, expected_sync_version=0, complete=True)
    evidence = await service.get_effective_aliases(song)
    assert len([e for e in evidence if e["text"] == "Many Sources"]) == 2
    first = next(e for e in evidence if e["source_key"] == "lxns:maimai")
    await approve(service, "RETRACT_EVIDENCE", {"evidence_id": first["id"]})
    await import_song(store, "maimai", aliases=["Many Sources"])
    assert len([e for e in await service.get_effective_aliases(song) if e["text"] == "Many Sources"]) == 1
    await assert_text_projection_matches_evidence(store)
    with pytest.raises(AliasConflictError, match="SOURCE_CHANGED"):
        await service.sync_source("second:maimai", [], actor_id=10, expected_sync_version=0, complete=True, allow_empty=True)
    await approve(service, "BLOCK_ALIAS", {"game_song_id": song, "text": "Many Sources"})
    await service.sync_source("second:maimai", observations, actor_id=10, expected_sync_version=1, complete=True)
    assert not (await service.resolve_alias("Many Sources")).candidates
    await assert_text_projection_matches_evidence(store)
    await approve(service, "UNBLOCK_ALIAS", {"game_song_id": song, "text": "Many Sources"})
    await service.sync_source("second:maimai", [], actor_id=10, expected_sync_version=2, complete=True, allow_empty=True)
    assert not (await service.resolve_alias("Many Sources")).candidates
    assert await AliasEvidence.filter(alias_key="many sources").count() == 2
    await assert_text_projection_matches_evidence(store)


async def test_target_versions_limits_and_mapping(catalog):
    store, service = catalog
    song = await import_song(store, "maimai")
    pending = await service.submit(actor_id=1, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Versioned"}, idempotency_key="v")
    await import_song(store, "chunithm")  # Work membership changes after submission.
    with pytest.raises(AliasConflictError, match="TARGET_CHANGED"):
        await service.review(pending, actor_id=10, approve=True, expected_version=1, reason="stale")
    current = (await service.get_request(pending, actor_id=10))["current_target_versions"]
    await service.review(pending, actor_id=10, approve=True, expected_version=1, expected_target_versions=current, reason="confirmed new membership")
    for index in range(5):
        await service.submit(actor_id=2, operation="ADD_ALIAS", payload={"game_song_id": song, "text": f"Queue {index}"}, idempotency_key=str(index))
    with pytest.raises(AliasConflictError, match="PENDING_LIMIT"):
        await service.submit(actor_id=2, operation="ADD_ALIAS", payload={"game_song_id": song, "text": "Overflow"}, idempotency_key="overflow")
    original = {"songs": [{"title": "Original", "artist": "Composer"}]}
    imported = await store.import_arcade_catalog("sdvx", original)
    original_id = next(iter(imported.song_ids.values()))
    renamed = await store.import_arcade_catalog("sdvx", {"songs": [{"title": "Renamed", "artist": "Composer"}]})
    key = renamed.pending[0]["external_key"]
    await approve(service, "MAP_SOURCE_RECORD", {"game_code": "sdvx", "provider": "arcade_songs", "external_key": key, "decision": "bind", "target_id": original_id})
    assert (await store.get_song_by_id("sdvx", original_id)).title == "Renamed"
    again = await store.import_arcade_catalog("sdvx", {"songs": [{"title": "Renamed", "artist": "Composer"}]})
    assert not again.pending and again.song_ids[key] == original_id
    await approve(service, "MAP_SOURCE_RECORD", {"game_code": "sdvx", "provider": "arcade_songs", "external_key": key, "decision": "reject"})
    assert await store.get_song_by_id("sdvx", original_id) is None
    rejected = await store.import_arcade_catalog("sdvx", {"songs": [{"title": "Renamed", "artist": "Composer"}]})
    assert not rejected.created and not rejected.pending


async def test_batched_source_replay_preserves_moderation_events_timestamps_and_rollback(catalog, monkeypatch):
    from datetime import datetime, timezone
    from arcade_helper.storage.tortoise.alias_store import TortoiseAliasStore
    store, service = catalog
    song = await import_song(store, "maimai")
    await service.configure_source("batch:maimai", actor_id=10, origin_game="maimai", trusted_import=True)
    observations = [{"anchor_id": song, "text": f"Batch alias {i}", "record_key": str(i)} for i in range(257)]
    await service.sync_source("batch:maimai", observations, actor_id=10, expected_sync_version=0, complete=True)
    source = await AliasSource.get(source_key="batch:maimai")
    before_events = await AliasEvent.all().count()
    before_rows = await AliasEvidence.filter(source=source).order_by("id").values("id", "version", "raw_text")
    old_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await AliasEvidence.filter(source=source).update(last_seen_at=old_time)
    # A remote reobservation cannot undo a human retraction.
    await AliasEvidence.filter(id=before_rows[0]["id"]).update(moderation_state="revoked")
    sql_reads = []
    aliases = TortoiseAliasStore(store, policy=policy)
    async with aliases.write() as db:
        query, query_dict = db.execute_query, db.execute_query_dict
        async def measured(sql, *args, **kwargs):
            if sql.lstrip().upper().startswith("SELECT"):
                sql_reads.append(sql)
            return await query(sql, *args, **kwargs)
        async def measured_dict(sql, *args, **kwargs):
            if sql.lstrip().upper().startswith("SELECT"):
                sql_reads.append(sql)
            return await query_dict(sql, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(db, "execute_query", measured)
            patch.setattr(db, "execute_query_dict", measured_dict)
            await aliases._sync_source(db, source, observations + observations[:1], expected_version=1, complete=True)
    assert len(sql_reads) < 20  # Replaying 258 observations uses batch reads, not per-alias SELECTs.
    assert all("catalog_payload" not in sql and "first_seen_at" not in sql for sql in sql_reads)
    assert await AliasEvent.all().count() == before_events
    assert await AliasEvidence.filter(source=source).order_by("id").values("id", "version", "raw_text") == before_rows
    assert await AliasEvidence.filter(source=source, last_seen_at__lte=old_time).count() == 0
    assert (await AliasEvidence.get(id=before_rows[0]["id"])).moderation_state == "revoked"
    await assert_text_projection_matches_evidence(store)
    assert len((await aliases._alias_texts())[song]) == 256
    revision = await store.get_revision()
    # The first batch changes a row, then a bad anchor in the second batch aborts everything.
    bad = [{**observations[0], "text": "Changed alias"}, *observations[1:], {"anchor_id": 999999, "text": "Bad", "record_key": "bad"}]
    with pytest.raises(DoesNotExist):
        await service.sync_source("batch:maimai", bad, actor_id=10, expected_sync_version=2, complete=True)
    assert await store.get_revision() == revision and await AliasEvent.all().count() == before_events
    assert (await AliasSource.get(id=source.id)).sync_version == 2
    assert not await AliasEvidence.filter(raw_text="Changed alias").exists()
    await service.sync_source("batch:maimai", observations[:1], actor_id=10, expected_sync_version=2, complete=True)
    assert await AliasEvidence.filter(source=source, remote_state="withdrawn").count() == 256
    assert await AliasEvent.filter(action="withdraw_source_evidence").count() == 256
    assert not (await aliases._alias_texts())[song]


async def test_additive_upgrade_is_explicit_and_repeatable(catalog):
    store, _ = catalog
    db = store._db
    await db.execute_query('ALTER TABLE game_songs DROP COLUMN edition_key')
    planned = await upgrade_catalog_schema()
    assert len(planned) == 2
    assert any(statement.startswith("ALTER TABLE") and "edition_key" in statement for statement in planned)
    assert any(statement.startswith("CREATE INDEX") and "idx_alias_evidence_key_kind" in statement for statement in planned)
    assert await upgrade_catalog_schema(dry_run=False) == planned
    assert await upgrade_catalog_schema(dry_run=False) == []


@pytest.mark.parametrize("include_songs", [True, False])
async def test_native_client_preserves_sources_and_freezes_legacy_aliases(catalog, tmp_path, monkeypatch, include_songs):
    from arcade_helper import ArcadeHelperClient
    from arcade_helper.storage.tortoise.models import MaiSong, MaiSongAlias
    from arcade_helper.games.maimai.schemas import MaiSongData
    from arcade_helper.games.maimai.data_fetcher import _attach_lxns_yuzuchan_aliases
    from arcade_helper.core.context import CatalogContext
    import logging

    store, _ = catalog
    async def get_json(url, **kwargs):
        if "yuzu" in url:
            return {"content": [{"SongID": 42, "Alias": ["Source Nick"]}]}
        return {"aliases": [{"song_id": 42, "aliases": ["Source Nick"]}]}
    client = ArcadeHelperClient(http_get_json=get_json, local_data_dir=tmp_path, catalog_store=store, alias_policy=policy)
    songs = {42: MaiSongData(id=42, title="Native Song", artist="Artist")}
    await _attach_lxns_yuzuchan_aliases(CatalogContext(http_get_json=get_json, logger=logging.getLogger(__name__)), songs)
    assert {o.source_key for o in songs[42].alias_observations} == {"lxns:maimai", "yuzuchan:maimai"}
    await MaiSong.create(id=42, title="Native Song", artist="Artist")
    await MaiSongAlias.create(song_id=42, alias="Frozen Legacy")
    from arcade_helper.storage.tortoise import migrate_catalog_identities
    await migrate_catalog_identities(store=store, dry_run=False)
    await client.catalog.sync_songs("maimai", songs)
    internal = await store.resolve_reference("maimai", "42", provider="catalog", namespace="native")
    evidence = await client.aliases.get_effective_aliases(internal)
    assert {e["source_key"] for e in evidence if e["text"] == "Source Nick"} == {"lxns:maimai", "yuzuchan:maimai"}
    assert any(e["text"] == "Frozen Legacy" and e["source_key"] == "legacy:maimai" for e in evidence)
    assert (await client.catalog.search_song("maimai", "Source Nick"))[0].song_id == 42
    assert await MaiSongAlias.all().values_list("alias", flat=True) == ["Frozen Legacy"]
    await approve(client.aliases, "BLOCK_ALIAS", {"game_song_id": internal, "text": "Source Nick"})
    assert "Source Nick" not in (await client.catalog.get_song_by_id("maimai", 42)).aliases
    from types import SimpleNamespace
    async def fetched(game, *, current_data_version):
        assert game == "maimai" and current_data_version == 7
        return SimpleNamespace(songs=songs, provider="lxns", data_version=17)
    monkeypatch.setattr(client.catalog, "fetch_game_raw_data", fetched)
    if not include_songs:
        async def forbidden(*args, **kwargs):
            pytest.fail("alias-enabled native background sync must not reload full songs")
        monkeypatch.setattr(client.catalog, "load_all_songs", forbidden)
    result = await client.catalog.sync_game_from_remote("maimai", current_data_version=7, **({"include_songs": False} if not include_songs else {}))
    assert result.ok and result.data_version == 17
    if include_songs:
        assert set(result.songs) == {42} and "Source Nick" not in result.songs[42].aliases
    else:
        assert result.songs is None
    assert await client.catalog.load_song_index("maimai") == {42: "Native Song"}


async def test_sql_song_search_and_light_metadata_never_load_full_catalog(catalog, monkeypatch):
    from arcade_helper.search import SongSearchService
    store, aliases = catalog
    song = await import_song(store, 'sdvx', title='FLOWER', aliases=['flower nick'], native='5459')
    await import_song(store, 'chunithm', title='FLOWER')
    await import_song(store, 'sdvx', title='Unrelated', artist='Other', native='99')
    async def forbidden(*args, **kwargs):
        raise AssertionError('full catalog must not be loaded')
    monkeypatch.setattr(store, '_cached_songs', forbidden)
    monkeypatch.setattr(store, 'load_all_songs', forbidden)
    statements = []
    original = store._db.execute_query
    async def measured(sql, *args, **kwargs):
        statements.append(sql)
        return await original(sql, *args, **kwargs)
    monkeypatch.setattr(store._db, 'execute_query', measured)
    metadata = await store.load_search_songs('sdvx')
    assert metadata[song].title == 'FLOWER' and metadata[song].aliases == ['flower nick']
    assert all('catalog_payload' not in sql for sql in statements)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'load_search_songs', forbidden)
        assert (await store.native_aliases('sdvx'))[5459] == ['flower nick']
    search = SongSearchService(store)
    assert [r.song_id for r in await search.search_song('ＦＬＯＷＥＲ', game_code='sdvx')] == [song]
    assert [r.song_id for r in await search.search_song('flower nick', game_code='sdvx')] == [song]
    assert await store.query_alias_exact('chunithm', 'flower nick')
    await aliases.configure_source('lxns:sdvx', actor_id=10, enabled=False, origin_game='sdvx')
    assert not await store.query_alias_exact('sdvx', 'flower nick')
    await aliases.configure_source('lxns:sdvx', actor_id=10, enabled=True, origin_game='sdvx')
    await approve(aliases, 'BLOCK_ALIAS', {'game_song_id': song, 'text': 'flower nick'})
    assert not await store.query_alias_exact('sdvx', 'flower nick')
    assert not await store.query_alias_exact('chunithm', 'flower nick')
