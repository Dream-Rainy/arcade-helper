# arcade-helper

`arcade-helper` is a Python helper library for arcade rhythm game data. It currently covers maimai DX, CHUNITHM, LXNS providers, and local JSON / Tortoise ORM storage.

Chinese documentation: [README.md](README.md).

## Installation

```powershell
uv pip install -e .\src\submodule\arcade-helper[storage-tortoise,sse]
```

Or configure it in the consuming project's `pyproject.toml`:

```toml
dependencies = [
    "arcade-helper[storage-tortoise,sse]",
]

[tool.uv.sources]
arcade-helper = { path = "src/submodule/arcade-helper", editable = true }
```

Optional extras:

- `storage-tortoise`: Tortoise ORM storage.
- `sse`: `aiohttp` for the LXNS OAuth SSE relay.
- `dev`: test tooling and optional test dependencies.

## Quick Start

```python
from arcade_helper import ArcadeHelperClient

client = ArcadeHelperClient(
    http_get_json=http_client.get_json,
    http_post_json=http_client.post_json,
    local_data_dir="./data/arcade-helper",
)

await client.lifecycle.start()
try:
    songs = await client.catalog.arcade_songs.search("sdvx", "FLOWER")
finally:
    await client.lifecycle.stop()
```

HTTP access is injected by the caller. `catalog` is the main entrypoint; with `local_data_dir`, generic arcade-songs catalogs use a local JSON cache. Pass `account_store` or `song_store` when account bindings, OAuth tokens, or persisted song catalogs need custom storage. Account binding migration is available through `arcade_helper.storage.migrate_account_store`.

## Main Entrypoints

Common imports:

```python
from arcade_helper import ArcadeHelperClient, PlatformIdentity
```

- `ArcadeHelperClient`: top-level client for HTTP, providers, storage, catalogs, users, OAuth, and player services.
- `client.catalog`: main entrypoint for song catalog sync, reads, search, aliases, and `client.catalog.arcade_songs` for on-demand generic arcade-songs catalogs with local JSON caching when `local_data_dir` is configured.
- `client.users`: platform identities, internal users, account binding, default accounts, and unbinding.
- `client.auth`: LXNS OAuth state, authorization URLs, callback validation, token binding, and refresh.
- `client.players`: maimai / CHUNITHM player handles resolved from platform identities.
- `client.maimai` / `client.chunithm`: lower-level game endpoint facades.
- `client.providers` / `client.lxns.user`: provider registry and LXNS user API lower-level entrypoints.

Entrypoint boundaries:

- `client.maimai` / `client.chunithm`: game-specific clients for LXNS, OAuth, developer APIs, and player-facing endpoints.
- `client.catalog`: unified song catalog entrypoint; prefer it for song lookup, sync, and search. Use `client.catalog.arcade_songs.sites()`, `.catalog(game_code)`, and `.search(game_code, query)` for generic arcade-songs data.
- `arcade_songs`: the generic catalog provider behind `client.catalog`, intended for games without a dedicated client. It does not handle player data.

Use source docstrings as the method-level reference.

## Persistent catalogs and stable identities

With `storage-tortoise`, explicitly inject `catalog_store`. New tables live in `CATALOG_MODEL_MODULE`. Hosts registering only the original `MODEL_MODULE` will not create them even when calling `generate_schemas()`. After verification in an isolated database, register both modules under the `models` app to opt in; subsequent host schema generation will create the new tables. Run this within the host's async initialization:

```python
from tortoise import Tortoise
from arcade_helper.storage.tortoise import (
    MODEL_MODULE, CATALOG_MODEL_MODULE, TortoiseCatalogStore,
    initialize_catalog_storage, migrate_catalog_identities,
)

await Tortoise.init(db_url="sqlite://catalog.sqlite3", modules={"models": [MODEL_MODULE, CATALOG_MODEL_MODULE]})
await initialize_catalog_storage()  # Create missing tables; no legacy schema alteration.
store = TortoiseCatalogStore()
client = ArcadeHelperClient(
    http_get_json=http_client.get_json,
    local_data_dir="./data/arcade-helper",
    catalog_store=store,
)
await client.lifecycle.start()
preview = await client.catalog.preview_import("sdvx")  # No database writes.
report = await client.catalog.import_catalog("sdvx")
results = await client.catalog.search_song("sdvx", "FLOWER")
latest_report = await store.get_import("sdvx")

native_preview = await migrate_catalog_identities(store=store)  # dry_run=True by default
native_report = await migrate_catalog_identities(store=store, dry_run=False)
internal_id = await store.resolve_reference(
    "maimai", "123", provider="catalog", namespace="native",
)
await client.lifecycle.stop()
await Tortoise.close_connections()  # Put cleanup in the host's finally/shutdown handler.
```

- Persistent generic queries read imported data. Refresh with `import_catalog()` or `sync_game_from_remote()`. The `catalog.arcade_songs` facade also reads persisted data; `force_refresh=True` imports a fresh snapshot. Without the store, read-only catalogs and search indexes are reused per instance, with a 24-hour catalog TTL.
- Persistent generic IDs are internal `GameSong.id` values; results also expose `song_data.game_song_id` and `song_data.work_id`. Existing maimai/chunithm catalog methods retain native IDs. Use `resolve_reference()` to translate to internal IDs. Read-only arcade-songs enumeration IDs are unsuitable for durable bindings.
- Source keys include provider, namespace, game and key kind. Without a verified native key, a derived key uses NFC/trimmed full title, artist, chart markers and music variant. Raw records and identity material are retained. Reordering or repeating imports preserves IDs; chart/cover metadata changes do not affect identity.
- Inspect `report.pending` and `report.invalid`. A new derived identity is a possible rename only if its full title or nonempty artist matches a missing entry in the same dataset, using NFC and trimming while preserving case. Unrelated additions proceed. Missing artists and duplicate identity conflicts remain separate pending reasons; uncertain or empty batches defer removals. The latest report and last usable complete snapshot are retained separately; invalid, pending, partial or unexpectedly empty fetches do not replace that snapshot. Manual mapping and historical replay are not implemented.
- Trusted adapters may use `CatalogRecord` and `store.import_records(..., provider=..., namespace=...)`. Set `native_key` only when the upstream guarantees stability. Read `store.get_sync_version(game_code, provider=..., namespace=...)` before fetching and pass `expected_sync_version`; on a same-dataset `CatalogConflictError`, refetch. Different datasets do not invalidate each other's tokens. Each non-preview import advances the dataset version, including unchanged content; global `get_revision()` only tracks query fact changes. Use `complete=False` for partial data to avoid withdrawing omitted records.
- Any leading nonempty `[marker]` becomes an ordered `chart_variant`; the original title is preserved. Explicit `SongData(chart_variant=())` declares a literal-title exception. Special charts remain separate entries; each entry initially has its own `MusicWork`.
- Native backfill leaves legacy tables untouched and excludes reserved temporary IDs. With persistent mode enabled, native `sync_songs()` subsequently backfills identities. Legacy sync and identity backfill are separate steps; retry backfill if the latter fails.

Identity, import and revision-aware caching remain the default. Cross-game association and moderation require explicit opt-in below. Rollback, independent-connection contention and cache invalidation are verified on file-backed SQLite and isolated PostgreSQL 16.11 / MySQL 9.6.0 databases. Check the deployed backend version and configuration separately.

`sync_game_from_remote(game_code, *, current_data_version=None, include_songs=True)` returns the full `result.songs` by default. Background callers can pass `include_songs=False` to skip the full catalog readback after persistence. A successful result still has `result.ok == True`, with `result.songs` set to `None`; data-version updates and cache invalidation still occur. Use `load_song_index(game_code)` for counts or titles, and check `result.ok` for sync success.

`fetch_game_raw_data()` remains an unpersisted compatibility fetch: generic IDs are snapshot-local enumeration IDs. Use queries after import for stable IDs. If an earlier experimental S1 schema was created, write a migration before upgrading: `safe=True` will not add columns to existing tables.

## Shared aliases and human moderation (S2 / S3)

Register `MODEL_MODULE`, `CATALOG_MODEL_MODULE` and `ALIAS_MODEL_MODULE` together under the `models` app. Existing S1 databases require a backup and an explicit additive migration before schema generation:

```python
from arcade_helper.storage.tortoise import (
    MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE,
    TortoiseCatalogStore, upgrade_catalog_schema, initialize_catalog_storage,
)
await Tortoise.init(db_url="sqlite://catalog.sqlite3", modules={
    "models": [MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE],
})
ddl = await upgrade_catalog_schema()  # Preview missing columns only.
await upgrade_catalog_schema(dry_run=False)
await initialize_catalog_storage()

def alias_policy(user_id, action, game_code):
    if user_id == 303:
        return True  # Example administrator.
    if action == "submit":
        return user_id in {101, 202}
    return user_id == 202 and action == "review" and game_code == "maimai"

client = ArcadeHelperClient(
    http_get_json=http_client.get_json, local_data_dir="./data/arcade-helper",
    catalog_store=TortoiseCatalogStore(enable_aliases=True),
    alias_policy=alias_policy, alias_daily_limit=20, alias_pending_limit=5,
)
await client.lifecycle.start()
request_id = await client.aliases.submit(
    actor_id=101, operation="ADD_ALIAS",
    payload={"game_song_id": selected_candidate.game_song_id, "text": "Example nickname",
             "scope": "work", "origin_game": "maimai"},
    idempotency_key="host-request-123", reason="Common nickname",
)
detail = await client.aliases.get_request(request_id, actor_id=202)
await client.aliases.review(request_id, actor_id=202, approve=True,
                           expected_version=detail["version"], reason="Verified")
result = await client.catalog.resolve_alias("Example nickname")
shared = await client.catalog.search_all("Example nickname", game_code="chunithm")
events = await client.aliases.events(actor_id=101, after_id=0)
```

Actor IDs must come from authenticated host sessions/UserService, never untrusted request fields. The synchronous policy must explicitly return `True`: actions are `submit`, per-origin-game `review`, `global` for global evidence/word blocks, and `admin` for sources and identity. Missing policies deny mutations/private review access. Administrators and co-submitters cannot review their own pending operation.

- Automatic association requires equal normalized titles and nonempty artists across different games, without chart/music variant markers. Uncertain pairs enter `list_candidates()` and administrators use `decide_candidate(..., accept=..., expected_version=..., reason=...)`. `associate()` reprocesses existing catalogs; `auto_match=False` pauses automatic association. Splits retain blocks and prevent immediate automatic re-merging.
- `resolve_alias()` performs exact nickname resolution; `search_all()` also considers titles, IDs and fuzzy matches. Results retain all candidates with game/work/entry IDs, source games, evidence IDs and scope. Only an exact, unique work with one eligible preferred game permits inference; fuzzy matches only suggest. Explicit games take precedence, and multiple entries within one game still require selection.
- `work` aliases are shared across a work; `game_song` aliases and titles remain entry-specific. Titles never contribute nickname-origin preference. Native collectors preserve dxrating/LXNS/Yuzuchan separately, and arcade-songs titles have catalog provenance. `get_effective_aliases()` exposes sources and acceptance provenance. Unknown external sources start disabled; administrators can `configure_source()` and `sync_source()` with a pre-fetch `expected_sync_version`.
- Requests support `ADD_ALIAS`, `RETRACT_EVIDENCE`, `REPLACE_ALIAS`, `BLOCK_ALIAS`, `UNBLOCK_ALIAS`, `RELINK_GAME_SONGS` and `MAP_SOURCE_RECORD`. Retraction uses `evidence_id`; replacement also supplies the new entry/text/scope; blocks use `game_song_id/text`; relinking uses `song_ids` and `mode=merge|split`; mapping uses `game_code/provider/namespace/external_key`, `decision=bind|new|reject` and `target_id` for binding. Mapping reads original data from the latest stored fetch, not caller-supplied raw data.
- `list_requests()` supports status, operation, game and ID cursors. Details expose affected games, collisions, permissions and target versions. `TARGET_CHANGED` requires review and explicit `expected_target_versions` from refreshed details. Submitters can `withdraw()` pending requests; reviewers can reject or `supersede()` with reasons. Corrections use new requests, preserving history.
- Idempotency is actor-scoped; equivalent pending operations share a request. Retries do not consume new quota. Pending aliases are invisible. Approval writes facts, request state, events and revision atomically. Hosts poll `events()`, deduplicate and send notifications themselves; the library neither fetches evidence URLs nor sends messages.
- Repeatable `migrate_catalog_identities(store=store, dry_run=False)` imports old aliases as `legacy:<game>`, without fabricating origins. Enabled Tortoise native sync freezes legacy alias tables; legacy APIs keep native IDs but read the evidence projection. Only confirmed real-source replacements supersede legacy evidence. Sync cannot undo moderation or blocks. Export new community evidence and audit history before reverting to legacy tables.

Bot/web handlers and notification delivery remain host integrations. Additive migration is verified on SQLite, PostgreSQL 16.11 and MySQL 9.6.0, including existing data, dry runs, repeated execution and stable IDs after upgrading. MySQL DDL is not transactionally reversible, so migration steps are independently repeatable. Production-copy migration and recovery rehearsals remain separate. When MySQL authentication uses `sha256_password` / `caching_sha2_password` and requires RSA password exchange, asyncmy also needs `cryptography` (for example, `pip install cryptography`); the storage extra does not install it by default.

## Development

```powershell
cd .\src\submodule\arcade-helper
uv run --extra dev pytest
uv build --out-dir dist
```

### Optional search-store optimizations

A custom search repository may implement `query_title_exact(game_code, title)`, returning `(song_id, title)` pairs, to filter exact title matches in the database. It may also implement `load_search_songs(game_code)`, returning a dictionary of text-only `SongData` objects with IDs, titles, artists, aliases and chart variants for fuzzy ranking and vector preparation. Public `SongQueryResult.song_data` still contains full details. An explicitly populated `aliases` field, including an empty list, is used directly; an unset field falls back to separate alias records.

`catalog.get_songs_by_ids(game_code, song_ids)` returns full `dict[int, SongData]` values, including difficulties, jackets and effective aliases. Keys follow the first occurrence of each requested ID, with duplicates, missing and inactive songs omitted. Maimai/CHUNITHM use public native IDs; other persisted catalogs use internal song IDs. Custom search repositories and stores may implement this optional method with the same signature: search hydrates its final candidates in one batch before returning. Older implementations fall back to individual full-song reads. Repositories without the other optional methods retain the existing index and full-song fallback.

Alias-enabled Tortoise catalogs query normalized evidence keys while preserving work scope, source status, moderation and blocking rules. Generic legacy catalogs without alias storage still read aliases from their original JSON payloads.
