# 第一步：角色卡导入 +「和 TA 聊」

用户已批准（m00969 "要"）。目标：在酒馆应用里导入 SillyTavern 角色卡，形成「角色卡库」，
点一张卡即可进入聊天页，复用现有 world chat 会话、流式回复、记忆与导出。

约定：应用内部数据与 UI 用中文；技术标识用英文。只读代码先读再改。
应用当前 `AI_MODE=mock`（生成走确定性 mock），真实生成后续再接，不在本步范围内。
账号体系不扩展（单用户 frankslin，DSH 可信登录已就位）。

## 1. 架构决策（已定，不要再摇摆）

- **不用 RoleSession / character-chats 链路**：那条路要求 `KnowledgeSnapshot` 完整
  （由书籍内容分析产生，含 scope_hash/analyzer_version），对任意角色卡很难合成。
- **复用 world chat**（`app/server/app/play_controls/`）：角色卡 → 独立 World + WorldLine +
  root WorldCheckpoint → `POST /world-chats` 创建 `WorldChatSession` → 发消息走
  `world_character_chat` 任务 → 现有 `WorldChatPage` 直接渲染。
- 角色卡导入时**同步**建好 world/line/checkpoint（不创建开局 turn、不起异步任务），
  这样「开聊」就是纯本地读 + 建会话，秒开。
- 不在本步做：多卡房间、群聊、世界书、预设、正则、swipe/重生成、长期记忆抽取。
  这些是第二步/第三步。

## 2. 服务端（owner: app-auth）

新增包 `app/server/app/cards/`：

### 2.1 模型 `app/server/app/cards/models.py`

用 world 的独立 schema（`app/world/models.py` 的 `WorldBase`），表
`world.character_cards`，字段：

```
id: str PK (uid())
owner_id: FK users.id ON DELETE CASCADE index
name, description, personality, scenario, first_message: Text
example_messages, alternate_greetings, tags: JSONB (list)
creator, character_version, system_prompt, post_history_instructions: Text/str
source: 'png' | 'json'
raw: JSONB           # 原始解析出的卡 JSON（v1/v2/v3 归一化前）
avatar_path: str|None # 相对 STORAGE_PATH 的私有文件路径
world_id, line_id, checkpoint_id, character_id: str|None
deleted_at, created_at, updated_at
```

把 `CharacterCard` import 进 `app/world/migrate.py`（或 cards 里写 `migrate()` 调
`WorldBase.metadata.create_all`），部署后跑一次
`app/venv/bin/python -m app.world.migrate`。**不要**新增 alembic revision
（`/health/ready` 硬编码 version=0022）。

### 2.2 解析 `app/server/app/cards/parse.py`

`parse_card(data: bytes, filename: str) -> dict`：

- PNG（magic `\x89PNG\r\n\x1a\n`）：逐 chunk 找 `tEXt` keyword=`chara`（base64 JSON）；
  兼容 `iTXt`/`zTXt` 同名 keyword；找不到 → `Problem("CARD_INVALID", "PNG 里没有角色卡数据", 422)`。
- JSON：直接解析；支持 `{"spec":"chara_card_v2","data":{...}}`、v3 同理、以及 v1 扁平结构。
- 归一化：`name` 必填非空（否则 422 `CARD_INVALID`）；`description` ← description 或
  personality 兜底；`first_message` ← first_mes/first_message；`example_messages` ←
  mes_example 按 `<START>` 分段；`alternate_greetings` ← alternate_greetings；
  `tags` ← tags；`system_prompt`/`post_history_instructions` 透传；保留 raw。
- 上限：文件 ≤ 8 MiB，JSON ≤ 2 MiB，字段长度做合理截断（name≤160，其余≤20000）。

### 2.3 建 world `app/server/app/cards/service.py`

`create_card(db, owner, payload, avatar_bytes) -> CharacterCard`：

1. 建 `World`（schema：读 `app/world/models.py` + `app/world/service.py` 的 create_world
   模板分支；`status='active'`，`source={'schemaVersion':2,'kind':'character-card',
   'templateId':None,'blocks':[]}`，`definition` 用下述定义，不要走 `world_prepare` 任务）。
2. 由角色卡构造 definition（形状必须与 `app/world/schemas.py:PrepareOutput` 一致，
   参考 `app/world/templates.py` 的 `_setting`）：
   - `title` = 卡名；`description` = scenario 或 description 前 300 字；
   - `timeLabel` = '' 或「现在」；`location` = 一个 place 实体；
   - `entities`: `place`（type='place'）+ `character`（type='character'，id 固定
     `card-<cardId>`，name=卡名，description=description，traits=[personality 拆分]，
     source='card'）；
   - `initial`: character 初始 state，缺省用 world 默认 initial_state 规则补齐；
   - `lore`: []；`unresolved`: []；`narrativeStyle` = personality 或 ''。
3. 复用 `begin_world` 的内部步骤建 `WorldLine` + root `WorldCheckpoint`
   （`kind='start'`，`sequence=0`），**不要**创建 opening turn。建议把
   `app/server/app/world/service.py:begin_world` 的「建线+根存档」抽成一个
   `_start_line(...)` 供两边调用；`begin_world` 行为保持不变（有测试）。
4. 头像：把原始 PNG 用 `app/storage.py:write_private(owner_id, f'cards/{card_id}.png', data)`
   落盘（参考 `private_path`），`avatar_path` 存相对路径；非 PNG 卡可无头像。
5. 记 `world_id/line_id/checkpoint_id/character_id` 到卡记录。

### 2.4 接口 `app/server/app/cards/api.py`（挂 `/api/reader/v1`）

- `POST /character-cards/imports`：multipart `file`（`UploadFile`）→ 201
  `{card: CardOut}`。重复导入同 `name` 不合并（各自独立卡）。
- `GET /character-cards?q=`：列表（不含 deleted），按 `updated_at desc`。
- `GET /character-cards/{id}`：详情。
- `GET /character-cards/{id}/avatar`：返回 PNG `FileResponse`（无头像 404）。
- `DELETE /character-cards/{id}`：204，软删（保留 world 与会话历史）。
- `POST /character-cards/{id}/chats`：确保 world/line/checkpoint 存在，调用
  `play_controls` 的建会话逻辑（等价 `POST /play-controls/world-chats`
  的 `{worldId,lineId,checkpointId,characterId}`），返回 `{sessionId, cardId}`；
  同一张卡多次调用可复用最近一个 `status='active'` 的会话。

`CardOut` 至少：`{id,name,description,personality,scenario,firstMessage,tags,creator,
source,hasAvatar,chatTarget:{worldId,lineId,checkpointId,characterId}|null,createdAt,updatedAt}`。

在 `app/server/app/main.py` 的 reader 循环里加 `cards` router。
注意 `app/character_cards.py` 已存在名字相近的模块（书内人物卡），新包叫
`app/cards/`，不要改旧模块。

### 2.5 测试 `app/server/tests/test_character_cards.py`

用现有 `conftest.py` 夹具（专用 `_test` 数据库 + alembic + world migrate）。覆盖：

- v2 JSON 导入成功、列表、详情、chatTarget 非空；
- v2 PNG 导入（用 Pillow `PngInfo().add_text('chara', base64(json))` 造卡）；
- v1 扁平 JSON、v3 JSON；
- 坏文件 → 422 CARD_INVALID；未登录 → 401；
- `POST /character-cards/{id}/chats` → 得到 sessionId，再
  `POST /play-controls/world-chats/{sessionId}/send`，mock 模式能拿到回复任务
  （沿用现有 world chat 测试的轮询方式）；
- 删除后列表不可见。

跑：`cd app/server && ../venv/bin/python -m pytest tests/test_character_cards.py -q`，
并回归 `tests/test_world_service.py`（或相关 world 测试）确保 `begin_world` 未被破坏。

## 3. 客户端（owner: cards-client）

- 新页面 `app/client/src/characters/CharactersPage.vue`（路由 `/characters`，标题「角色卡库」）：
  - 顶部：标题 +「导入角色卡」按钮（`input[type=file] accept=".png,.json"`），
    导入中/失败提示（失败显示服务端 message）；
  - 列表：网格卡片，头像（有则 `<img>`，用带 Bearer 的 fetch 取 blob 再
    `URL.createObjectURL`，卸载时 revoke）、名字、description 两行截断、tag 徽标、
    「开聊」「删除」按钮；空状态：「导入一张 SillyTavern 角色卡开始聊天」+ 支持 PNG/JSON 说明。
  - 导入成功后刷新列表并 toast；「开聊」→ `POST /character-cards/{id}/chats` →
    `router.push('/conversations/world/' + sessionId)`。
- 导航：`app/client/src/workspace/navigation.ts` 的「创作」组加
  `{path:'/characters', name:'角色卡库', icon:Users}`（从 lucide-vue-next 选一个已用过的图标也行）。
- 路由：`app/client/src/main.ts` 增加 `/characters` 懒加载路由，`meta:{title:'角色卡库'}`。
- 能力清单：`app/client/src/workspace/capabilities.ts` 里「独立角色卡与写作扩展」条目改为
  partial，说明文字改成「角色卡导入与单卡聊天已可用；预设/正则/插件库尚未接入」。
- vitest：导航/路由的轻量断言即可；页面主要靠 verifier 的浏览器 E2E。

## 4. 验收（owner: verifier）

1. 隔离实例（沿用 3099/`/tmp/dsh-tavern-verify` 模式）跑 `--open-story` 原有 43 项回归全绿。
2. 新增场景：
   - 从「我的→创作→角色卡库」进入，导入 fixture 卡（`/tmp/tavern-verify/cards/*.png|json`），
     列表出现卡名与头像；
   - 点「开聊」进入 `/conversations/world/<id>`，页面标题显示角色名；
   - 发一条「你好」，mock 回复出现（流式或最终气泡均可），刷新后消息仍在；
   - 删除卡后列表消失；
   - 截图 + `report.json`（沿用现有报告格式），失败项标注 owner。
3. 全部通过后再由 Lead 安排生产 DSH 重启与生产冒烟（如需要）。

## 5. 文件所有权（本步）

| 范围 | owner |
| --- | --- |
| `app/server/app/cards/**`、`app/server/app/main.py`、`app/server/tests/test_character_cards.py`、`app/world/migrate.py`、`app/world/service.py`（仅抽函数） | app-auth |
| `app/client/src/characters/**`、`app/client/src/main.ts`、`app/client/src/workspace/navigation.ts`、`app/client/src/workspace/capabilities.ts` | cards-client |
| `scripts/**`、`verify.mjs`、`docs/testing.md` | verifier |
| 本设计文档、`docs/contract.md`、host/systemd/profile/Caddy | Lead |

先读 `docs/contract.md` 与 `docs/architecture.md` 再动手。改完各自跑门禁，
把「改了什么/为什么/怎么验证的」作为完成报告。不要重启 DSH 生产进程，不要改
`app/.env`，不要碰别人的文件。
