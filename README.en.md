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

## Development

```powershell
cd .\src\submodule\arcade-helper
uv run --extra dev pytest
uv build --out-dir dist
```

Do not commit `.venv`, `dist`, `build`, `*.egg-info`, or cache directories.
