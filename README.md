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

- 持久模式下，指定游戏的首次搜索/读取会按需导入尚未准备的通用曲库；并发请求共享准备任务。已有可用数据的查询不重复联网；用 `import_catalog()` 或 `sync_game_from_remote()` 更新，`catalog.arcade_songs` 的 `force_refresh=True` 也会导入新快照。未注入存储时保持已有只读缓存路径，曲库缓存 24 小时。
- 通用持久曲库返回稳定的内部 `GameSong.id`，另有 `song_data.game_song_id`、`song_data.work_id`。舞萌、中二原有 catalog 查询继续接受/返回原生 ID；通过 `resolve_reference()` 找内部 ID。只读 arcade-songs 的枚举 ID 不能作为长期绑定依据。
- 启用别名存储后，`sync_game_from_remote()` 也保存中二 arcade-songs、舞萌 DXRating 的完整来源快照，并补入落雪独有的舞萌曲目。相同游戏中唯一且身份兼容的来源记录共用收录；已有原生数据优先。仅补充源有记录的曲目使用稳定内部 ID，不伪造落雪 ID；`search_song()` 返回 `song_data.catalog_only=True`，可用曲名、别名或 `catalog:<game_song_id>` 再次搜索。普通数字仍按原生 ID 解析，`get_song_by_id/get_songs_by_ids` 仍只接受原生 ID。这类曲目可展示上游资料与谱面，不能用内部 ID 请求原生成绩或收藏。之后补到唯一匹配的原生记录时保留原收录 ID、别名及审核状态。
- 来源键包含 provider、namespace、game、key_kind；无可信原生 ID 时用完整标题、作者、谱面标记、音乐版本生成来源内派生键。arcade-songs 的合法 `songId` 作为原样保存的 `source_record_key` 区分同标题多收录，不直接证明跨游戏同曲，也不升级为可信 `native_key`。旧引用在唯一、兼容的匹配下原地迁移，保留歌曲和证据 ID、人工状态与屏蔽；真实冲突仍待定。`identity_material` 和原始记录均保存，重排、重复导入、新增难度不换 ID。
- 作者缺失的合法记录可建立独立收录，原文缺失值保留，不凭缺作者自动跨游戏合并。新派生键与同数据集本次缺失记录的完整标题或非空作者相同时可能是改名；真实冲突/疑似来源键复用通过 `report.pending` 报告。待定项只暂缓相关旧引用的撤回；不可归属坏行、部分或异常空响应仍保守，不清空旧曲库。最新观察/报告与最后完整快照分别保存，部分发布不覆盖最后完整快照。
- `source_record_count` 是源行数，`processed_record_count`、`rejected_record_count`、`pending_record_count` 分别统计已处理、已拒绝和待定源行；`len(pending)` 是待定组数。`duplicate_record_count` 记录原样重复行，`pending_reasons` 按待定行数统计；`available_song_count` 是当前游戏可用收录总数。原有 `created/updated/unchanged/withdrawn` 保持增量语义，不能当曲库总量；`outcome=complete|partial|unusable` 描述发布结果。`CatalogSyncResult.import_report` 返回通用曲库报告；`status=success` 表示事务成功，不保证所有源记录可用。
- 可在启动后 `await client.catalog.prepare_catalogs(["gitadora", "popn"])`，或省略列表准备所有可见通用游戏；跳过两原生游戏与隐藏来源，抓取并发上限 2。无游戏限定的 `search_all/resolve_alias` 会后台准备未初始化来源，返回已有候选和 `unavailable_games`，范围不完整时不推断唯一游戏。部分曲库已有可用数据时，零命中正常返回空候选及不完整标记，不因少量坏行阻断搜索。无候选且仍有完全缺失、初始化中或失败而不可用的来源时抛 `CatalogSyncError`，避免把未准备误报为歌曲不存在；可等待 `prepare_catalogs()` 或之后重试。失败冷却 30 秒，显式 `prepare_catalogs(..., force_refresh=True)` 可立即重试。`client.lifecycle.stop()` 会取消并等待库自身后台任务；底层 `AliasService` 和直接存储读取仍只读已发布事实。
- 自有可信来源可用 `CatalogRecord` 和 `store.import_records(..., provider=..., namespace=...)`；仅当上游保证稳定时设置 `native_key`。网络同步应先调用 `store.get_sync_version(game_code, provider=..., namespace=...)`，抓取后将其作为 `expected_sync_version` 传入。同一数据集版本冲突抛出 `CatalogConflictError`，需重新获取；不同数据集不会因此互相冲突。每次非预览导入递增数据集版本，即使内容相同；全局 `get_revision()` 仅用于查询事实的缓存失效。部分数据用 `complete=False`，避免撤回未提供的记录。
- 标题开头任意非空 `[内容]` 会解析为有序 `chart_variant`，原始标题不变；显式设置 `SongData(chart_variant=())` 可声明字面标题例外。不同特殊谱面保持不同收录，本阶段每条收录各有一个 `MusicWork`。
- 原生曲库回填不改旧表，排除旧实现保留的临时 ID。启用别名存储后，原生 `sync_songs()` 将旧曲库、稳定身份及传入的补充来源放在同一事务；任一写入失败全部回滚。

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
- 自动关联仅接受规范化标题和非空作者全等、没有特殊谱面/音乐版本标记且各游戏没有未确认同名多版本的跨游戏收录。变体与相似标题进入 `list_candidates(actor_id=...)`；同游戏、同基础曲名和非空作者、音乐版本一致且标记无歧义的普通/特殊谱面合为一组人工确认。候选的 `song_ids` 和 `target_versions` 包含整组成员，`left_id/right_id` 仅为兼容字段，调用方须展示和校验全部成员。管理员用 `decide_candidate(id, actor_id=..., accept=..., expected_version=..., reason=...)` 原子采纳或否决整组，任一成员版本变化都会阻止旧确认。`associate(actor_id=...)` 可重算已有曲库并将旧冗余待定候选标为 `superseded`，历史保留；普通同步也会重算。拆分会阻止重新自动合并，证据随原锚点移动，屏蔽规则保守保留。
- `work` 别名跨同作品共享；`game_song` 仅属于所选收录。特殊收录的有效别名自动添加完整谱面标记（如 `[奏] 公交车`），已有相同标记不会重复添加，原始证据保留为 `source_text`；旧证据立即遵守此投影，无需重新同步。不带标记的文本只搜索普通收录，带标记只搜索对应谱面，数字 ID 仍可直接查询特殊收录。特殊收录新增别名默认 `game_song`；明确指定 `work` 仍可共享。完整标题属于收录且不参与来源游戏偏好。舞萌、中二采集时分别保留 dxrating、LXNS、柚子查证据；arcade-songs 标题也有来源。`get_effective_aliases(internal_id)` 可查看各来源及人工审核/外部导入区别。未知外部来源默认禁用，须管理员 `configure_source()` 启用；`sync_source()` 仅允许管理员向可信来源提交快照，要求 `expected_sync_version`。
- `submit` 支持 `ADD_ALIAS`、`RETRACT_EVIDENCE`、`REPLACE_ALIAS`、`BLOCK_ALIAS`、`UNBLOCK_ALIAS`、`RELINK_GAME_SONGS`、`MAP_SOURCE_RECORD`。撤回证据使用 `evidence_id`；替换另需新收录、文字和 scope；屏蔽使用 `game_song_id/text`；关联使用 `song_ids` 与 `mode=merge|split`；来源映射使用 `game_code/provider/namespace/external_key` 与 `decision=bind|new|reject`，绑定时提供 `target_id`。同键多观察可传 `pending[*].records[*].record_selector` 选择拒绝一条，省略选择器则拒绝整组；该指纹只定位快照，不能充当永久身份。尚无持续区分规则的同键冲突不能强行创建/绑定。映射原文从最新已保存抓取读取，已批准且仍兼容的映射可持续复用，过期快照仍要求重审。
- `list_requests()` 支持状态、操作、游戏和 ID 游标；返回 `has_more`，仅为真时使用 `next_cursor` 翻页，权限和游戏过滤后再判断分页。默认详情包含当前目标版本、影响游戏、同名候选、可审核状态；`include_details=False` 只取编号、版本、操作、状态、可见 payload、影响游戏与 `songs`（游戏/曲名）摘要，不执行冲突搜索。目标变化返回 `TARGET_CHANGED`；审核员重新查看后可传 `expected_target_versions=detail["current_target_versions"]` 明确确认。驳回、过期终结 `supersede()` 均需理由；本人可用 `withdraw(..., expected_version=...)` 撤回待审请求。误审通过新请求补偿，不删除旧事件。
- 幂等键按提交者隔离，同键不同内容报冲突；同一语义的待审请求合并。相同 scope、origin_game 与标准化别名的 `work` 请求按作品去重，`game_song` 按收录去重；升级前的待审键也能复用。重复请求不占新额度。待审内容不进入搜索，批准时事实、状态、事件和修订号同事务提交。通知由宿主读取 `events()` 并自行去重/发送，库不会抓取证据 URL 或主动发消息。
- `migrate_catalog_identities(store=store, dry_run=False)` 可重复回填旧别名为 `legacy:<game>`，不伪造真实来源。启用后 Tortoise 原生同步停止写旧别名表，原生查歌仍返回原 ID、别名改读证据投影。仅真实来源确认覆盖的 legacy 证据会被替代；单源缺失或同步不会复活人工撤销、解除屏蔽。回退前应导出新增社区证据和审核历史，旧表没有这些数据。

已知谱面标记查询可省略方括号，例如 `奏公交车`、`奏 公交车` 与 `[奏] 公交车`；普通完整曲名/别名与标记解析发生精确冲突时保留候选。网页同曲关联审核可给每个特殊收录新增搜索标记/简称；对应参数为 `decide_candidate(..., marker_aliases={internal_song_id: ["宴", "派对"]})`，仅采纳候选时可用，每个特殊收录最多新增 16 个、每个 1–32 字符且不含空白的标记。简称仅作用于当前收录，例如 `宴公交车`、`宴 公交车`、`[宴] 公交车`，不修改上游标记、标题或稳定身份，也不单独充当歌曲昵称。标记与整组确认同事务生效，记录审核人，普通上游同步不覆盖它们。`aliases.get_marker_aliases(song_ids=None)` 批读已生效标记；`aliases.expand_query(query, game_code=None)` 返回原查询及已知标记的规范查询写法。

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

## 独立别名网页与中继

管理扩展可复用同一传输：`EditorSession.start(page_path="/manage/")`指定同源页面，通道地址和加密协议仍沿用原值，默认入口保持不变。宿主可在`create_app()`返回的ASGI应用上挂载自己的静态页面。`relay.js`同时提供`window.editorRelay`与兼容的`window.aliasRelay`；`mountAliasPortal(root, relay, onLogout)`可把原审批面板挂入其他页面，业务权限仍由机器人核验。`alias-view.html`及面板资源属于受信任的同源部署资源。

`web` extra 提供独立网页、中继 CLI 和机器人出站编辑会话。安装 `pip install '.[web]'` 后可运行 `arcade-alias-relay`，或 `python -m arcade_helper.web.relay`。它不加载 NoneBot、数据库或机器人配置。

本机验证（HTTP 仅限 localhost）：

```powershell
$env:ARCADE_RELAY_TOKEN = '<至少32字符的随机服务密钥>'
$env:ARCADE_RELAY_ORIGIN = 'http://127.0.0.1:8765'
arcade-alias-relay --host 127.0.0.1 --port 8765
```

公网部署推荐独立 VPS/域名 + Docker + Caddy，模板位于 `deploy/alias-web`：

1. 将域名 DNS 指向中继服务器；允许该服务器的 TCP 80/443。
2. 在 `deploy/alias-web` 中将 `.env.example` 复制为 `.env`，设置 `ALIAS_WEB_DOMAIN`。用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成 `ARCADE_RELAY_TOKEN`；不要提交 `.env`。
3. 从此目录执行 `docker compose -f compose.yml up -d --build`。Caddy 自动申请 HTTPS 证书并代理 WebSocket；中继的 8765 端口仅在容器网络内使用。
4. 机器人配置 `alias_web_relay_url=https://你的域名`、`alias_web_relay_token=<同一服务密钥>`；`alias_web_enabled=true`。重启机器人。机器人只发起出站 HTTPS/WSS 连接，不注册别名网页 HTTP 路由；中继不访问数据库。两项中继配置均必填，未配置时编辑器命令会提示补全配置。
5. 私聊机器人发送 `/arcade.editor`，打开临时链接即可提交、匹配和审批，无需再发确认指令。机器人创建链接时绑定真实用户身份，浏览器自动完成链接密钥校验；禁止提交者及共同提交者自审，权限仍由机器人实时检查。

只部署一个中继进程和一个机器人进程。会话最长一小时；退出、断开、刷新网页或任一端重启后需要新建会话。断线时刚提交的操作可能已经生效，请重新连接后检查请求状态。临时链接是创建者身份的授权凭证，拿到链接的人可以使用该身份，请勿转发。链接密钥仅保存在 URL fragment，页面读取后立即清除，浏览器以绑定通道和临时公钥的 HMAC-SHA256 证明自动鉴权；浏览器不会向网页或中继服务器发送明文密钥。中继不保存业务快照；消息以临时 P-256/ECDH、HKDF 和 AES-GCM 端到端加密，递增序列号拒绝篡改/重放。服务密钥仅用于创建中继通道；业务权限、版本校验和人工审核规则继续由机器人执行。

模板没有预设你的真实域名，也不会自动发布。生产证书和公网连通性需在选择域名后验证。长期托管时保留 `caddy_data`/`caddy_config` 卷并维护 HTTPS 服务；页面不依赖 CDN。

升级自动链接鉴权时请同时更新机器人和中继网页，并重新生成编辑链接；旧页面和旧链接不兼容新握手。
