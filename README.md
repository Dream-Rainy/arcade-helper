# arcade-helper

`arcade-helper` 是一个面向街机音游数据的 Python 辅助库，当前覆盖 maimai DX、CHUNITHM、LXNS 数据源，以及本地 JSON / Tortoise ORM 存储。

英文说明见 [README.en.md](README.en.md)。

## 安装

```powershell
uv pip install -e .\src\submodule\arcade-helper[storage-tortoise,sse]
```

或在主项目的 `pyproject.toml` 中配置：

```toml
dependencies = [
    "arcade-helper[storage-tortoise,sse]",
]

[tool.uv.sources]
arcade-helper = { path = "src/submodule/arcade-helper", editable = true }
```

可选 extras：

- `storage-tortoise`：Tortoise ORM 存储。
- `sse`：LXNS OAuth SSE relay 所需的 `aiohttp`。
- `dev`：测试工具和测试所需可选依赖。

## 快速开始

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

HTTP 访问由调用方注入。`catalog` 是主要出口；配置 `local_data_dir` 后，arcade-songs 通用曲库会使用本地 JSON 缓存。需要保存账号绑定或 OAuth token 时，也可传入 `account_store` 或 `song_store`。账号绑定迁移使用 `arcade_helper.storage.migrate_account_store`。

## 主要入口

常用导入：

```python
from arcade_helper import ArcadeHelperClient, PlatformIdentity
```

- `ArcadeHelperClient`：顶层客户端，组合 HTTP、provider、存储、目录、用户、OAuth 和玩家服务。
- `client.catalog`：主要出口，负责乐曲目录同步、读取、搜索和别名查询；`client.catalog.arcade_songs` 可按需拉取 arcade-songs 通用曲库，并在配置 `local_data_dir` 时使用本地 JSON 缓存。
- `client.users`：平台身份、内部用户、账号绑定、默认账号和解绑。
- `client.auth`：LXNS OAuth state、授权链接、callback 校验、token 绑定和刷新。
- `client.players`：按平台身份解析 maimai / CHUNITHM 玩家对象。
- `client.maimai` / `client.chunithm`：低层游戏端点 facade。
- `client.providers` / `client.lxns.user`：provider registry 和 LXNS user API 底层入口。

入口边界：

- `client.maimai` / `client.chunithm`：特化游戏客户端，负责 LXNS / OAuth / 开发者 API 这类游戏和玩家接口。
- `client.catalog`：统一曲库入口；查歌、同步、搜索优先从这里进。通用 arcade-songs 数据使用 `client.catalog.arcade_songs.sites()`、`.catalog(game_code)`、`.search(game_code, query)`。
- `arcade_songs`：`client.catalog` 背后的通用曲库 provider，适合没有专用客户端的游戏，不处理玩家数据。

各入口的具体方法和行为说明以源码 docstring 为准。

## 开发

```powershell
cd .\src\submodule\arcade-helper
uv run --extra dev pytest
uv build --out-dir dist
```

不要提交 `.venv`、`dist`、`build`、`*.egg-info` 或缓存目录。
