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

## 持久曲库与稳定身份

安装 `storage-tortoise` 后，可显式注入 `catalog_store`。新表独立放在 `CATALOG_MODEL_MODULE`，仅注册原有 `MODEL_MODULE` 的宿主即使调用 `generate_schemas()` 也不会创建它们。要启用时，在隔离库验证后把两个模块注册到 `models` 应用；注册新模块后，宿主的 `generate_schemas()` 就会创建这些表。以下代码在宿主的异步初始化流程中执行：

```python
from tortoise import Tortoise
from arcade_helper.storage.tortoise import (
    MODEL_MODULE, CATALOG_MODEL_MODULE, TortoiseCatalogStore,
    initialize_catalog_storage, migrate_catalog_identities,
)

await Tortoise.init(db_url="sqlite://catalog.sqlite3", modules={"models": [MODEL_MODULE, CATALOG_MODEL_MODULE]})
await initialize_catalog_storage()  # 创建缺失表，不修改旧曲库表的结构
store = TortoiseCatalogStore()
client = ArcadeHelperClient(
    http_get_json=http_client.get_json,
    local_data_dir="./data/arcade-helper",
    catalog_store=store,
)
await client.lifecycle.start()

preview = await client.catalog.preview_import("sdvx")  # 不写数据库
report = await client.catalog.import_catalog("sdvx")  # 获取新快照并发布确定的记录
results = await client.catalog.search_song("sdvx", "FLOWER")
latest_report = await store.get_import("sdvx")

# 已有舞萌、中二曲库的身份回填；默认 dry_run=True，可重复执行。
native_preview = await migrate_catalog_identities(store=store)
native_report = await migrate_catalog_identities(store=store, dry_run=False)
internal_id = await store.resolve_reference(
    "maimai", "123", provider="catalog", namespace="native",
)

await client.lifecycle.stop()
await Tortoise.close_connections()  # 实际宿主应在自己的 finally/关闭流程中调用
```

- 持久模式的通用查询只读已导入曲库；用 `import_catalog()` 或 `sync_game_from_remote()` 更新。`catalog.arcade_songs` 同样读取持久曲库，`force_refresh=True` 会导入新快照。未注入存储时保持只读模式，实例复用解析后曲库和搜索索引，曲库缓存 24 小时。
- 通用持久曲库返回稳定的内部 `GameSong.id`，另有 `song_data.game_song_id`、`song_data.work_id`。舞萌、中二原有 catalog 查询继续接受/返回原生 ID；通过 `resolve_reference()` 找内部 ID。只读 arcade-songs 的枚举 ID 不能作为长期绑定依据。
- 来源键包含 provider、namespace、game、key_kind；无可信原生 ID 时仅用 NFC/首尾空白规范化后的完整标题、作者、谱面标记、音乐版本生成来源内派生键，不以模糊匹配判断身份。`identity_material` 和原始记录均保存。重排/重复导入不换 ID，谱面、封面等元数据更新不影响身份。
- `report.pending` / `report.invalid` 给出待处理或坏记录。新派生键只有与同一数据集本次缺失记录的完整标题相同，或非空作者相同，才判为疑似改名；标题/作者比较做 NFC 和首尾去空白、保留大小写。无关新增可以正常导入。缺作者、重复身份冲突等仍单独报告；本批有不确定项就暂缓缺失撤回。空曲库默认不撤回。各数据集保存最新报告及最后可用的完整快照，坏行、待定项、部分或异常空抓取不覆盖该快照；当前没有人工映射界面或历史回放。
- 自有可信来源可用 `CatalogRecord` 和 `store.import_records(..., provider=..., namespace=...)`；仅当上游保证稳定时设置 `native_key`。网络同步应先调用 `store.get_sync_version(game_code, provider=..., namespace=...)`，抓取后将其作为 `expected_sync_version` 传入。同一数据集版本冲突抛出 `CatalogConflictError`，需重新获取；不同数据集不会因此互相冲突。每次非预览导入递增数据集版本，即使内容相同；全局 `get_revision()` 仅用于查询事实的缓存失效。部分数据用 `complete=False`，避免撤回未提供的记录。
- 标题开头任意非空 `[内容]` 会解析为有序 `chart_variant`，原始标题不变；显式设置 `SongData(chart_variant=())` 可声明字面标题例外。不同特殊谱面保持不同收录，本阶段每条收录各有一个 `MusicWork`。
- 原生曲库回填不改旧表，排除旧实现保留的临时 ID。启用持久模式后，原生 `sync_songs()` 会接着回填身份；旧表同步与身份回填是两步，后者失败可重试回填。

默认仅启用身份、导入与修订缓存。跨游戏关联与别名审核需按下一节显式开启。事务回滚、独立连接竞争及跨实例缓存已在 SQLite 文件库、PostgreSQL 16.11 和 MySQL 9.6.0 隔离库验证；部署时仍需核对实际后端版本和配置。

`sync_game_from_remote(game_code, *, current_data_version=None, include_songs=True)` 默认返回完整 `result.songs`。后台同步可传 `include_songs=False`，省去落库后的完整曲库读回；此时成功结果仍为 `result.ok == True`，`result.songs` 为 `None`，数据版本和缓存失效照常更新。需要曲目数量或标题时调用 `load_song_index(game_code)`；请按 `result.ok` 判断同步是否成功。

`fetch_game_raw_data()` 是兼容的未落库抓取入口，通用结果仍使用快照内枚举 ID；需要稳定 ID 时用导入后的查询入口。若曾用早期实验版创建 S1 表，`safe=True` 不会补齐新列，应先编写迁移再接入，不能直接靠建表函数升级。

## 跨游戏别名与人工审核（S2 / S3）

在宿主初始化中同时注册三个模块；已有 S1 数据库先备份并预览增量迁移。`generate_schemas(safe=True)` 不能替代升级旧表：

```python
from arcade_helper.storage.tortoise import (
    MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE,
    TortoiseCatalogStore, upgrade_catalog_schema, initialize_catalog_storage,
)

await Tortoise.init(db_url="sqlite://catalog.sqlite3", modules={
    "models": [MODEL_MODULE, CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE],
})
ddl = await upgrade_catalog_schema()  # 默认仅返回缺失列的 DDL
await upgrade_catalog_schema(dry_run=False)
await initialize_catalog_storage()

# user_id 必须由宿主认证会话/UserService 提供，不能直接信任消息或请求中的数字。
def alias_policy(user_id, action, game_code):
    if user_id == 303:  # 示例：管理员
        return True
    if action == "submit":
        return user_id in {101, 202}
    return user_id == 202 and action == "review" and game_code == "maimai"

store = TortoiseCatalogStore(enable_aliases=True)  # auto_match=False 可暂停自动关联
client = ArcadeHelperClient(
    http_get_json=http_client.get_json, local_data_dir="./data/arcade-helper",
    catalog_store=store, alias_policy=alias_policy,
    alias_daily_limit=20, alias_pending_limit=5,
)
await client.lifecycle.start()
```

角色策略是同步函数 `(user_id, action, game_code) -> bool`，必须明确返回 `True`。`submit` 管理提交资格，`review` 管理来源游戏审核，`global` 管理全局来源/整词屏蔽，`admin` 管理来源和身份映射。缺少策略默认拒绝写入及私有审核信息访问；公开有效别名仍可查询。管理员同样禁止自审，共同提交同一待审操作的用户也不能审核它。

```python
# 用户明确选择的内部 GameSong ID，不是未经确认的第一条模糊结果或游戏原生 ID。
request_id = await client.aliases.submit(
    actor_id=101, operation="ADD_ALIAS",
    payload={"game_song_id": selected_candidate.game_song_id,
             "text": "测试简称", "scope": "work", "origin_game": "maimai"},
    idempotency_key="host-request-123", reason="常用简称",
)
detail = await client.aliases.get_request(request_id, actor_id=202)
await client.aliases.review(
    request_id, actor_id=202, approve=True,
    expected_version=detail["version"], reason="已核实",
)
result = await client.catalog.resolve_alias("测试简称")
shared = await client.catalog.search_all("测试简称", game_code="chunithm")
events = await client.aliases.events(actor_id=101, after_id=0)
```

- `search_all` 汇总标题、ID、昵称和模糊候选；`resolve_alias` 只查规范化后的精确昵称。结果包含 `resolution`、`inferred_game`、`inference_reason` 和全部候选，候选包含内部收录/作品 ID、游戏、来源游戏、证据 ID、scope 与变体。只有精确层唯一作品且来源游戏唯一（或仅收录于一款游戏）才自动推断；模糊命中只给建议，不自动路由。`game_code` 显式指定时优先使用该游戏。同作品同游戏多收录仍需用户选择。
- 自动关联仅接受规范化标题和非空作者全等、没有特殊谱面/音乐版本标记的跨游戏收录。变体与相似标题进入 `list_candidates(actor_id=...)`；管理员用 `decide_candidate(id, actor_id=..., accept=..., expected_version=..., reason=...)` 采纳或否决。`associate(actor_id=...)` 可对已有曲库重跑。拆分会阻止重新自动合并，证据随原锚点移动，屏蔽规则保守保留。
- `work` 别名跨同作品共享；`game_song` 仅属于所选收录。完整标题属于收录且不参与来源游戏偏好。舞萌、中二采集时分别保留 dxrating、LXNS、柚子查证据；arcade-songs 标题也有来源。`get_effective_aliases(internal_id)` 可查看各来源及人工审核/外部导入区别。未知外部来源默认禁用，须管理员 `configure_source()` 启用；`sync_source()` 仅允许管理员向可信来源提交快照，要求 `expected_sync_version`。
- `submit` 支持 `ADD_ALIAS`、`RETRACT_EVIDENCE`、`REPLACE_ALIAS`、`BLOCK_ALIAS`、`UNBLOCK_ALIAS`、`RELINK_GAME_SONGS`、`MAP_SOURCE_RECORD`。撤回证据使用 `evidence_id`；替换另需新收录、文字和 scope；屏蔽使用 `game_song_id/text`；关联使用 `song_ids` 与 `mode=merge|split`；来源映射使用 `game_code/provider/namespace/external_key` 与 `decision=bind|new|reject`，绑定时提供 `target_id`。映射原文从最新已保存抓取读取，不能由提交者伪造。
- `list_requests()` 支持状态、操作、游戏和 ID 游标；返回 `has_more`，仅为真时使用 `next_cursor` 翻页，权限和游戏过滤后再判断分页。默认详情包含当前目标版本、影响游戏、同名候选、可审核状态；`include_details=False` 只取编号、版本、操作、状态、可见 payload、影响游戏与 `songs`（游戏/曲名）摘要，不执行冲突搜索。目标变化返回 `TARGET_CHANGED`；审核员重新查看后可传 `expected_target_versions=detail["current_target_versions"]` 明确确认。驳回、过期终结 `supersede()` 均需理由；本人可用 `withdraw(..., expected_version=...)` 撤回待审请求。误审通过新请求补偿，不删除旧事件。
- 幂等键按提交者隔离，同键不同内容报冲突；同一语义的待审请求合并。相同 scope、origin_game 与标准化别名的 `work` 请求按作品去重，`game_song` 按收录去重；升级前的待审键也能复用。重复请求不占新额度。待审内容不进入搜索，批准时事实、状态、事件和修订号同事务提交。通知由宿主读取 `events()` 并自行去重/发送，库不会抓取证据 URL 或主动发消息。
- `migrate_catalog_identities(store=store, dry_run=False)` 可重复回填旧别名为 `legacy:<game>`，不伪造真实来源。启用后 Tortoise 原生同步停止写旧别名表，原生查歌仍返回原 ID、别名改读证据投影。仅真实来源确认覆盖的 legacy 证据会被替代；单源缺失或同步不会复活人工撤销、解除屏蔽。回退前应导出新增社区证据和审核历史，旧表没有这些数据。

库内服务已提供，机器人命令、网页和通知发送仍由宿主接入。增量迁移已在 SQLite、PostgreSQL 16.11、MySQL 9.6.0 实测，覆盖带数据的缺列升级、dry-run、重复执行和升级后稳定 ID。MySQL DDL 不可整体事务回滚，各步骤可重复执行。生产升级与隔离副本演练应单独进行。MySQL 使用 `sha256_password` / `caching_sha2_password` 且需 RSA 密码交换时，asyncmy 还要求安装 `cryptography`（例如 `pip install cryptography`）；它不属于当前存储 extra 的默认依赖。

## 开发

```powershell
cd .\src\submodule\arcade-helper
uv run --extra dev pytest
uv build --out-dir dist
```

### 自定义搜索存储的可选优化接口

`from arcade_helper.search import is_short_song_query` 提供 `is_short_song_query(query: str | int) -> bool`：按与搜索一致的全半角、简繁和分隔符规范化后，1–4 个字符返回 `True`，空查询返回 `False`。宿主可用它跳过短查询的推理兜底。

`SongSearchService.search_candidates(query, *, game_code="maimai")` 返回同一文本规则下的 `list[SongQueryResult]`，跳过本地 embedding 和完整谱面补全。`song_data` 是文本元数据；仓库应提供 `load_search_songs()`，可选实现 `get_search_songs_by_ids()` 优化冷态精确查询；旧仓库可回退到完整数据读取。接受后可通过仓库的 `get_songs_by_ids()` 批量补全。原有 `search_song()` 默认行为不变；底层函数新增 `allow_embedding=True` 关键字参数。

启用别名存储时，`TortoiseCatalogStore.text_search` 为身份解析和后台预热共享同一份修订感知的索引。`client.catalog.search_all(query, *, game_code=None, text_only=False)` 新增宿主文本模式：`text_only=True` 不进行本地 embedding 或完整谱面读取，并按宿主公开 ID 解析数字（maimai/CHUNITHM 原生 ID，其他游戏内部收录 ID）。结果仍保留作品、来源、证据和 scope。默认 `False` 保留原有 SDK 身份解析及可选本地向量行为；SDK 不调用宿主的推理服务。

`search_song()` 优先返回 ID、曲名和生效别名的精确结果。短查询仅做完整目标/英文词、完整拼音缩写或完整拼音匹配；1–2 字符不扩展错字，3–4 字符允许一次编辑，不进入 BM25 或本地 embedding。长查询保留模糊、罗马音及拼音匹配；BM25 要求完整输入覆盖，且证据来自“一个曲名或一个别名＋曲师”，不能拼接多个别名/拼音字段。无需新配置，默认查询行为保持兼容。匹配分数用于排序，精确性应检查 `MatchType.EXACT_*`，不能以 `match_score == 100` 判断。

搜索存储可实现 `query_title_exact(game_code, title)`，返回 `(song_id, title)` 列表，使精确曲名查询直接由数据库筛选；也可实现 `load_search_songs(game_code)`，返回仅包含 ID、曲名、作者、别名和谱面变体的 `SongData` 字典，供模糊排名和向量构建使用。此结果不是完整歌曲详情；`SongQueryResult.song_data` 对外仍返回完整详情。轻量歌曲明确设置 `aliases`（包括空列表）时直接使用该字段；未设置时继续加载独立的别名记录。

`catalog.get_songs_by_ids(game_code, song_ids)` 批量返回完整的 `dict[int, SongData]`，包含难度、封面和有效别名。结果按请求 ID 的首次出现顺序排列，去除重复并省略不存在或停用的歌曲；maimai/CHUNITHM 使用游戏原生 ID，其他持久曲库使用内部歌曲 ID。自定义搜索仓库或存储可实现同签名的可选方法，搜索会在返回最终候选前批量补全详情；未实现时兼容逐首读取。未实现其他可选方法的自定义存储继续使用原有索引与完整歌曲读取方式。

启用别名存储的 Tortoise 曲库查询使用规范化证据键，并保留作品范围、来源状态、审核和屏蔽规则。未启用别名存储的通用旧曲库仍从原 JSON 读取别名。
