# dsh-tavern 架构

## 一句话

插件由**两个半边**加一个**应用本体**组成：`lib/index.js`（host 半）在 DSH Web 里
注册 `prefix /tavern` 反向代理（旧 `/story` 作为兼容别名），把本地「酒馆」应用挂到
DSH 登录态后面，并另外注册 `prefix /tavern-agent` 模型网关给应用服务端调 DSH 模型；
`client.js`（client 半）注册侧栏入口与主面板，用同源 iframe 展示应用；
`app/` 是酒馆 2.0 本体：**单用户原生酒馆**——角色卡网格 → 点卡进全屏聊天，
SSE 逐字流式，重新生成追加版本（swipe），世界书按关键词注入 prompt。
故事模式 / 探索 / 世界 / 分支 / 记忆 / 伙伴等旧 story 域已整体删除（P3）；
原生酒馆现在支持**单聊 + 群聊**（2–5 个角色轮转发言）。旧表已按备份
**DROP**（备份 `story_between-pre-p3-20261105-081109.dump`）。

```
┌────────────────────────── DSH Web (127.0.0.1:3080) ──────────────────────────┐
│  client.js（浏览器）                                                          │
│   ├─ slots.register('sidebar.panellist', id=tavern, order=24)                 │
│   └─ slots.register('main', key=tavern)                                       │
│        └─ 工具条 + <iframe src="/tavern/">（P2 桥：握手/路由上报 + 聊天上下文） │
│                                                                              │
│  lib/index.js（host）                                                        │
│   ├─ ctx.get('connection').requestRejection(req)  ← 登录闸门                 │
│   ├─ ctx.webServer.register({kind:'prefix', path:'/tavern'})                  │
│   └─ ctx.webServer.register({kind:'prefix', path:'/tavern-agent'})            │
└───────────────┬────────────────────────────────────────────────┬─────────────┘
                │ 剥掉 /tavern 前缀                              │ Bearer secret
     ┌──────────┴───────────┐                                    │
     ▼                      ▼                                    │
 /api/** /health/**      其余 /assets、HTML、sw.js                 │
     │                      │                                    │
     ▼                      ▼                                    │
 127.0.0.1:8001        127.0.0.1:8810                            │
 uvicorn(FastAPI)      app/dist 静态站                            │
     │  附加 x-dsh-trusted: <secret>（应用侧校验可信登录身份）      │
     │                                                           │
     │  POST /api/reader/v1/tavern/chats/<id>/messages           │
     │   ├─ 写 user 消息 + 组装 prompt（请求线程）                 │
     │   ├─ StreamingResponse → text/event-stream ──▶ 浏览器      │
     │   └─ 后台线程 adapter.generate_stream()                    │
     │        └─ DshAgentAdapter ──▶ /tavern-agent/v1/stream ────┘
     │                                   └─▶ ctx.llm.stream()
     │                                        └─▶ opencode-go / deepseek-v4.1-flash
     └─ 生成结束后把**完整文本**落库（partial 不入库），再发 done 帧
```

## 请求链路与鉴权

### 闸门

每个进入 `/tavern` 的请求先调用宿主的
`ctx.get('connection', false)?.requestRejection(req)`：

| 返回值 | 含义 | 插件行为 |
| --- | --- | --- |
| `undefined` | Host/Origin 在信任围栏内且已登录 | 继续转发 |
| `401` | 未通过浏览器登录（无 token/cookie） | 返回 401 纯文本，沿用 DSH 措辞 |
| `403` | Host/Origin 不在信任列表 | 返回 403 纯文本，沿用 DSH 措辞 |

内嵌面板天然继承 DSH 登录态，插件不另做一套认证；`/tavern` 在公网上只能经
Caddy → DSH 网关访问，上游端口只监听回环地址。

### 分流（先剥前缀，再转发）

| 浏览器请求 | 上游 | 超时 | 额外头 |
| --- | --- | --- | --- |
| `/tavern/api/**` | `127.0.0.1:8001` | **不设**（聊天是一次 SSE 长请求） | `x-dsh-trusted: <secret>` |
| `/tavern/health/**` | `127.0.0.1:8001` | **不设** | 同上 |
| `/tavern/`、`/tavern/assets/**`、`/tavern/sw.js`… | `127.0.0.1:8810` | 10s | 无 |
| `/tavern`（无尾斜杠） | `127.0.0.1:8810` 的 `/` | 10s | 无 |

上游路径是「剥掉 `/tavern` 之后」的路径：应用 dist 已按 `base=/tavern` 构建，
页面里引用 `/tavern/assets/...`，静态服务器本身不认识 `tavern` 段，所以必须在
插件里剥掉。`/tavern/api/...` 到 uvicorn 时同样只剩 `/api/...`。

### 旧前缀 `/story` 兼容别名

插件同时注册 `prefix /story`，指向同一个 handler，行为分两种：

| 旧请求 | 行为 | 原因 |
| --- | --- | --- |
| `GET/HEAD /story/...` | `302` 到 `/tavern/...`，保留 path 后缀与 query | 书签、旧 iframe 缓存、外链平滑换地址；用 302 而非 301，别名将来下线时浏览器不会永久缓存 |
| `POST/PUT/PATCH/DELETE /story/...` | **不重定向**，把请求前缀改写成 `/tavern` 后照常反代 | 302 会把 POST 降级成 GET 并丢 body；308 又要求客户端重放 body（浏览器做不到无感）。原地改写 body 一个字节都不碰 |

两种路径都先过登录闸门；命中分流规则与 `/tavern` 完全一致。

### 头处理

- 逐跳头（`connection`、`keep-alive`、`transfer-encoding`、`upgrade`、
  `proxy-*`、`te`、`trailer`）双向都不透传；
- 入站的 `x-dsh-trusted` 一律删除，只允许插件按自己的 secret 重新签发，
  避免客户端伪造可信身份；
- 入站的 `x-forwarded-*` 删除后重写，防止伪造来源；
- `Host` 重写为上游地址，`X-Forwarded-Host` 保留浏览器原始 Host；
- 响应头同样剥逐跳头，由 node 重新计算 `Transfer-Encoding`。

### 流式与错误

- 用 `req.pipe(upstream)` / `upstreamRes.pipe(res)` 转发，保留 chunked、
  multipart 上传与 SSE；`text/event-stream` 响应立即 `flushHeaders()`，
  生成第一帧不会憋在代理层；
- API 不设响应超时；静态上游 10s 不响应则销毁连接；
- 错误只回中文友好文案，细节（错误码、端口、路径）只进宿主日志：
  上游连不上 → `502 酒馆应用暂时不可用，请稍后重试。`；
  静态上游超时 → `504 酒馆应用响应超时，请稍后重试。`；
- 客户端断开（`req` aborted / `res` close）时销毁上游请求，避免泄漏
  （聊天生成随之中断，未完成的回复不会落库）。

## DSH 模型网关 `/tavern-agent/v1/**`（host 半）

应用 API 进程是**服务端进程**：它没有浏览器会话，也不该为了调模型去拿 DSH
登录 cookie。插件另开一个平级前缀 `/tavern-agent`，给应用做服务端到服务端调用，
复用 DSH 已经在跑的模型通道。

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/tavern-agent/v1/health` | `GET` | 探活 + 返回 DSH 当前默认模型 + `features` |
| `/tavern-agent/v1/complete` | `POST` | 一次性返回整段文本 |
| `/tavern-agent/v1/stream` | `POST` | SSE 增量返回（原生聊天走这个） |

### 鉴权

- 只认 `Authorization: Bearer <secret>`，用 `timingSafeEqual` 做常量时间比较；
- secret 读取顺序：环境变量 `DSH_TAVERN_AGENT_SECRET` → `app/.env` 的
  `DSH_TAVERN_AGENT_SECRET` → `DSH_TRUSTED_SECRET`（应用侧一般配成同一个值）；
- **没配 secret 时一律 401**（`AGENT_UNAUTHORIZED`），不静默放行；
- 网关**不过** `requestRejection`：它服务的是应用服务端而不是浏览器。

### 请求 / 响应

```http
POST /tavern-agent/v1/complete
Authorization: Bearer <secret>
Content-Type: application/json

{
  "system": "tavern-v1\n\n[主提示词]…\n\n[角色设定]…\n\n[对话历史]…",
  "messages": [{ "role": "user", "content": "……" }],
  "maxTokens": 1024,
  "temperature": 0.8,
  "topP": 0.95,
  "stop": ["\nUser:"],
  "sessionId": "a7f1c0d2e3b4",
  "reasoningEffort": "low",
  "model": { "provider": "opencode-go", "model": "deepseek-v4.1-flash" }
}
```

- `messages` 的 `content` 可以是字符串，也可以是 `[{ "type": "text", "text": "..." }]`
  （插件统一归一成 DSH 的 `ContentBlock[]`；裸字符串直接丢给 `ctx.llm.stream` 不合法）；
  目前只接受 `role: "user"`，多轮上下文由应用自己拼进 `system`；
- `model` 可选；不传时用 `ctx.agentDefaultModel.currentSelection()`，
  两者都没有则 502；
- `sessionId` 可选：应用按聊天传稳定值（≤200 字符）能提高上游缓存命中；不传时
  网关每次生成一个 UUID——opencode-go 要求 `x-opencode-session`，缺了会
  `400 MissingSessionID`；
- `reasoningEffort` 可选，白名单 `off|minimal|low|medium|high|xhigh|max`，
  **默认 `low`**：profile 默认是 `high`，省略会继承 high 把 maxTokens 烧在思考里；
  当前聊天模型（deepseek-v4.1-flash）实测只支持 `low`/`high`；
- 采样参数：`temperature`（0–2）、`topP`（0–1，兼容 `top_p`）、`stop`
  （string 或 string[]，≤4 条、单条 ≤200 字符）只在出现时校验，null 视为不传；
  非法值 400 `INVALID_ARGUMENT`；
- `complete` 响应：

```json
{
  "text": "……",
  "model": { "provider": "opencode-go", "model": "deepseek-v4.1-flash" },
  "usage": { "state": "known", "inputTokens": 812, "outputTokens": 486 },
  "finishReason": "stop"
}
```

### SSE 帧

`/v1/stream` 先回 `200 text/event-stream` 并 `flushHeaders()`，之后每段文本一帧：

```
data: {"type":"delta","text":"……"}

data: {"type":"usage","usage":{"state":"known","inputTokens":812,"outputTokens":486}}

data: {"type":"finish","reason":"stop"}

data: [DONE]
```

- 只转发 `text-delta`；`reasoning-delta` 丢弃（不把思维链塞给应用）；
- `usage` 有精确值用 `state: "known"`，否则按字符数粗估并标 `state: "estimated"`；
- 上游失败时发 `data: {"type":"error","error":{"code":"AGENT_UPSTREAM_FAILED","message":"…"}}`
  然后结束；此时 HTTP 状态已经是 200，错误只能走帧。

### 失败语义

| 场景 | HTTP | code |
| --- | --- | --- |
| 缺/错 secret | 401 | `AGENT_UNAUTHORIZED` |
| 坏 JSON、空 `messages`、超大 body（>4 MiB）、非 user 角色 | 400 | `AGENT_BAD_REQUEST` |
| 采样参数非法 | 400 | `INVALID_ARGUMENT` |
| 上游报错 / 没有可用默认模型 / 无完成信号 | 502 | `AGENT_UPSTREAM_FAILED` |
| 方法不对 / 未知路径 | 405 / 404 | `AGENT_METHOD_NOT_ALLOWED` / `AGENT_NOT_FOUND` |
| 超过 120s（`AGENT_TIMEOUT_MS`） | 502 | `AGENT_UPSTREAM_FAILED`（文案含「超时」） |

超时会 `AbortController.abort()` 中断上游；客户端断开同样中断。
**日志里只有模型名、字数、耗时和错误码**，不含 prompt、消息内容或 secret。

**生效边界**：网关只把采样参数放进 `ctx.llm.stream` 的选项；本机 `opencode-go`
的 pi-ai 适配器只消费 `temperature/maxTokens/sessionId`，**`topP`、`stop` 会被忽略**；
且这些字段的网关代码要等 DSH Web 重启后才生效。

## 原生酒馆应用（app/）

host 半只做反向代理，以下链路都在应用侧。设计文档见
`docs/step6-native-tavern.md`；表结构与验收点以它为准。

### 页面与路由

应用是一个独立的 Vue SPA（`app/client`，`createWebHistory('/tavern')`），
路由表只有：

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/` | `tavern/TavernCharactersPage.vue` | 角色卡网格：搜索、导入（PNG/JSON）、点卡开最近/新聊天、右上「设置」进卡详情；卡右上角勾选 2–5 张后底部出现「发起群聊（N）」 |
| `/characters/:id` | `tavern/TavernCharacterPage.vue` | 卡详情：设定编辑/保存/删除、导出卡、世界书 CRUD、聊天历史、开新聊天 |
| `/chats` | `tavern/TavernChatsPage.vue` | 聊天历史页：全部聊天按 `lastMessageAt` 倒序（群聊显示成员名），点进对应聊天 |
| `/chats/:id` | `tavern/TavernChatPage.vue` | 全屏聊天（`immersive`）：消息列表、流式气泡、swipe 版本、编辑/删除、重新生成、标题改名、导出聊天、「采样参数」Sheet（temperature/top_p/max_tokens/stop，落库并回显）；群聊顶部显示成员头像、assistant 气泡带发言人名字与头像 |
| `/settings` | `tavern/TavernSettingsPage.vue` | 设置：人设（`PersonaControl` + 默认人设）、预设管理（`TavernPresetsPanel`）、输出替换规则入口、导入导出说明 |
| `/settings/regex` | `tavern/TavernRegexPage.vue` | 输出替换规则：账号级正则列表 CRUD、启停、两步删除、客户端校验（名称 ≤80 / 正则 ≤500 / 替换 ≤2000 / 顺序）；规则按账号级存储（`world.regex_scripts`，owner 级），**回复落库前会调用 `apply_regex`**（流式 delta 为原文，刷新后见替换结果） |

`/characters/groups`、`/characters/groups/:id`、`/characters/regex` 以及所有未知
路径都 redirect `/`（旧链接不落死页）。壳层 `tavern/TavernShell.vue`：
sticky 顶栏（「酒馆」品牌、返回角色卡、标题、设置入口），固定浅色。

### 服务端模块

| 文件 | 作用 |
| --- | --- |
| `app/server/app/tavern/api.py` | `APIRouter(prefix="/tavern")`，挂到 `/api/reader/v1` 下：角色卡、聊天（单聊/群聊）、聊天历史、消息、世界书、prompt 设置（36 条路由，见下） |
| `app/server/app/tavern/stream.py` | SSE 生成流水线：请求线程组装 prompt，后台线程跑适配器，队列把 delta 交给响应生成器；结束后**只把完整文本**落库 |
| `app/server/app/tavern/prompt.py` | SillyTavern 顺序的 prompt 组装、世界书匹配（含命中原因）、历史裁剪；`effective_prompts` 做 preset > 卡设置的提示词合并 |
| `app/server/app/tavern/service.py` | 序列化、归属校验（`owner_id`）、导入卡、swipe 内容读取、消息/聊天 CRUD 辅助、预设读取/删除（内置默认不可删） |
| `app/server/app/tavern/models.py` | `world.tavern_chats`、`world.tavern_messages`、`world.tavern_world_info`、`world.tavern_presets`（`WorldBase` + `WorldOwned`，由 `app.world.migrate` 的 `create_all` 建表，不需要 reader alembic revision） |
| `app/server/app/cards/parse.py`、`exporting.py` | SillyTavern 卡解析（PNG 内嵌 JSON / JSON，v1/v2/v3，`character_book`）与导出（PNG + JSON） |
| `app/server/app/cards/presets.py`、`presets/` | 内置预设角色卡种子：`MANIFEST.json` + 5 张 ST v2 JSON/PNG（CC BY 4.0，署名见 `CREDITS.md`）；`ensure_preset_cards(db, owner)` 按 `builtin_slug` 补种 |

**预设角色卡种子**：新账号第一次列卡时，`cards/service.py:list_cards` 先调用
`cards/presets.py:ensure_preset_cards(db, owner)`——在 advisory lock 下读
`presets/MANIFEST.json`，按 `builtin_slug` 只补**缺失**的 slug（已存在或已软删的
都跳过，因此删除后不会被重灌）；每张卡用 `parse.parse_card` + `service.import_card`
入库（头像写 private 存储、`lore` 自动导入），并写
`world.character_cards.builtin_slug`（`varchar(40)`，与 `owner_id` 组成 partial
unique index）。`card_out` 输出 `builtin`/`builtinSlug`，前端据此显示「预设」徽标。
资产缺失或单卡解析失败只记结构化日志、不阻断列表，返回已成功的部分。

### 数据模型

**`world.tavern_chats`**

| 列 | 说明 |
| --- | --- |
| `id`、`owner_id`、`created_at` | `WorldOwned` 公共列 |
| `card_id` | 归属角色卡（`character_cards.id`），单聊用；**群聊为 NULL** |
| `mode` | `single`（默认）/ `group` |
| `members` | JSONB 有序数组 `[{cardId,name,order}]`；群聊 2–5 个，单聊为 `[]` |
| `title` | 默认「<卡名> 的对话」/群聊「A、B 的群聊」，≤160 |
| `last_message_at` | 最近消息时间（卡库排序、聊天历史排序用） |
| `model_name` | 最近一次生成用的模型名，≤150 |
| `preset_id` | 绑定的提示词预设（`world.tavern_presets.id`，可空，有索引）；空 = 用默认预设 |
| `sampling` | JSONB 聊天级采样覆盖：`temperature` / `topP` / `maxTokens` / `stop`，空字段回退到预设/卡设置 |
| `updated_at` | 更新时间 |

**`world.tavern_messages`**

| 列 | 说明 |
| --- | --- |
| `chat_id` | 归属聊天，有索引 |
| `role` | `user` / `assistant` |
| `speaker_card_id` | 群聊 assistant 的发言人卡 id（单聊为 NULL） |
| `sequence` | 聊天内自增序号；`UNIQUE(chat_id, sequence)` |
| `swipes` | JSONB 数组，每个 swipe 是 `{content, model, createdAt}`；**重生成往这里追加** |
| `active_swipe` | 当前展示的版本下标（0 起） |
| `updated_at` | 更新时间 |

> 消息正文不单独存列：`swipes[active_swipe].content` 就是当前文本
> （`service.swipe_content`），接口输出时同时给 `swipes` 与 `text`。

**`world.tavern_world_info`**：`card_id`、`keys`（JSONB string[]，≤20）、
`content`（≤8000）、`position`（`before_char` / `after_char` / `before_example` /
`at_depth`）、`depth`（0–100，`at_depth` 用）、`constant`（常驻）、
`probability`（0–100，按 `sha256(id|匹配文本)` 确定性抽样；卡详情 UI 用 0–1
输入、提交时乘 100 存百分比）、`order`
（0–10000，升序注入）、`enabled`、`source`（`manual` / `import`）、`updated_at`。

**`world.tavern_presets`**（P2）：`id`、`owner_id`、`name`（≤80）、`builtin`
（内置「默认」= true，不可删）、`is_default`、`system` / `main` / `jailbreak` /
`post_history`（各 ≤20000）、`temperature`（0–2）、`top_p`（0–1）、
`max_tokens`（16–8192）、`stop`（JSONB，≤4 条）、`created_at` / `updated_at`。
两个 partial unique index 保证每个用户只有一条 `builtin` 预设、且只有一条
`is_default` 预设；切换默认时先把同用户其它预设的 `is_default` 置 false，
并发冲突报 409 `PRESET_CONFLICT`。

**`character_cards.prompt_settings`**（JSONB，复用 cards 表）：`mainPrompt`、
`system`、`jailbreakPrompt`、`postHistory`、`worldInfoPosition`（默认
`before_char`，作为新建世界书条目的默认注入位置）、`temperature`（默认 0.8）、
`topP`（默认 0.95）、`maxTokens`（默认 1024）、`stop`（默认 `[]`）；卡自身的
`system_prompt` 仍是独立列。默认值在 `tavern/api.py:PROMPT_DEFAULTS`。

### 应用 API（`/api/reader/v1/tavern/**`）

P3 之后 `from app.main import app` 共 **86 条路由**（含 `/api/reader/v1/tavern/**`
36 条；`/worlds`、`/world-chats`、`/books`、`/play-controls` 等旧 story API 已随
域删除，全部 404）。下表列 `tavern` 模块对前端暴露的接口。

群聊：`POST /chats`（body `{cardIds:[2..5]}`）创建 `mode='group'` 的聊天，
`GET /chats` 是聊天历史列表；发言人是 `members[已保存 assistant 条数 % 成员数]`
（轮转），消息落库时写 `speaker_card_id`，`regenerate` 沿用原消息的发言人。
成员卡被软删后打开该群聊 → 404（无级联）。

所有接口都要登录（`current_user`：cookie 会话或插件签发的 `x-dsh-trusted`），
按 `owner_id` 做归属校验，找不到统一 `404 RESOURCE_NOT_FOUND`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/characters` | 卡列表：`q` 搜索（名字子串）、`sort=recent\|name\|created`；带 `chatCount`/`lastChatAt` |
| `POST` | `/characters/imports` | multipart 导入 PNG/JSON → `{card}`（201） |
| `GET` | `/characters/{id}` | 卡详情 + `prompt`（含 prompt_settings）+ `worldInfoCount` |
| `PATCH` | `/characters/{id}` | 改 name/description/personality/scenario/firstMessage/systemPrompt/postHistoryInstructions/tags/alternateGreetings |
| `DELETE` | `/characters/{id}` | 软删卡（204）；聊天记录保留但卡不可见 |
| `GET` | `/characters/{id}/avatar` | PNG 头像 |
| `GET` | `/characters/{id}/export?format=png\|json` | 导出卡（文件名在 `Content-Disposition`） |
| `GET` | `/characters/{id}/chats` | 该卡聊天列表（含 `lastText`/`messageCount`）+ `card` |
| `POST` | `/characters/{id}/chats` | 新建**单聊**（201）；`greetingId` 选备用开场白，默认用 `firstMessage`；非空开场白作为第一条 assistant 消息 |
| `POST` | `/chats` | 新建**群聊**（201）；body `{cardIds:[2..5], title?}`；也接受 `{cardId}` 走单聊 |
| `GET` | `/chats` | 聊天历史列表：`limit`（1–100，默认 20）/ `offset`，按 `lastMessageAt` 倒序；带 `mode`/`members`/`lastMessage`/`messageCount` |
| `GET` | `/chats/{id}` | 聊天 + 消息：`before=<sequence>`、`limit=1..500`（默认 200）分页；返回 `hasMore`/`latestSequence`，向上翻页用 |
| `PATCH` | `/chats/{id}` | 改 `title`（1–160）、`presetId`（校验归属后绑定/解绑预设）、`sampling`（`temperature` / `topP` / `maxTokens` / `stop`，非法值 422） |
| `DELETE` | `/chats/{id}` | 删聊天与其全部消息（204） |
| `GET` | `/chats/{id}/export?format=jsonl\|json` | 导出聊天：JSONL 为 ST 风格逐行（群聊 assistant 行带 `speaker_name`）；JSON 为 `{chat, card, cards, messages}`；文件名在 `Content-Disposition` |
| `POST` | `/characters/{id}/chats/imports` | multipart `file`（ST JSONL）→ `{chat, imported, skipped}`（201）；≤20000 行、≤32 MiB，坏行跳过，空/全坏 422 `CHAT_IMPORT_EMPTY`，超限 422 `CHAT_IMPORT_TOO_LARGE`；**只建单聊**（群聊文件可下载但暂不支持导回成群聊） |
| `POST` | `/chats/{id}/messages` | 发消息 → **SSE**；body `{message}`（兼容 `{text}`），≤4000；可带 `personaId`；群聊首帧带本轮 `speaker:{cardId,name}` |
| `POST` | `/chats/{id}/regenerate` | 对最后一条 assistant 重新生成 → **SSE**；追加 swipe；群聊沿用该条 `speaker_card_id` |
| `POST` | `/chats/{id}/messages/{mid}/swipes/{index}/select` | 切版本（0 起），返回消息 |
| `PATCH` | `/chats/{id}/messages/{mid}` | 只能改 user 消息；改完**截断其后所有消息**，返回 `{message, deleted}` |
| `DELETE` | `/chats/{id}/messages/{mid}` | 删除该消息及其后消息（204） |
| `GET/POST` | `/characters/{id}/world-info` | 世界书列表 / 新建（201） |
| `PATCH/DELETE` | `/characters/{id}/world-info/{entryId}` | 改 / 删条目；可改 `keys` / `content` / `position` / `depth` / `constant` / `probability` / `order` / `enabled` |
| `POST` | `/characters/{id}/world-info/preview` | body `{message, chatId?}`（message 空则取最近 user 文本）→ `{matched, injection, reasons:[{id, reason}], worldInfoPosition, sections, systemPrompt}`；命中原因 `constant` / `keyword:<词>`；与生成共用 `prompt.build`，卡详情已有「注入预览」UI |
| `GET/PATCH` | `/characters/{id}/prompt?chatId=` | 读/写提示词与采样覆盖：`systemPrompt`、`mainPrompt`、`jailbreakPrompt`、`postHistory`、`worldInfoPosition`、`temperature` 0–2、`topP` 0–1、`maxTokens` 16–8192、`stop` ≤8 条；GET 还返回当前生效预设、拼好的 `systemPrompt` / `sections` / `matched` |
| `GET/POST` | `/presets` | 预设列表（默认在首位）/ 新建（201）；默认预设不存在时自动补一条 builtin「默认」 |
| `PATCH/DELETE` | `/presets/{presetId}` | 改字段 / 设默认（`isDefault` 互斥）/ 删除（204）；内置默认与 `isDefault` 预设不可删，返回 409 `PRESET_IMMUTABLE` |

错误码（应用侧 `Problem`）：`RESOURCE_NOT_FOUND` / `PRESET_NOT_FOUND` 404、
`GREETING_INVALID` / `SWIPE_INVALID` / `MESSAGE_NOT_EDITABLE` / `MESSAGE_REQUIRED` /
`INVALID_REQUEST` / `CHAT_IMPORT_EMPTY` / `CHAT_IMPORT_TOO_LARGE` 422、
`NOTHING_TO_REGENERATE` / `PRESET_IMMUTABLE` / `PRESET_CONFLICT` 409。

导出/导入/分页（P1）已在服务端落地：导出走 `cards/exporting.py`（`chat_jsonl` /
卡 `card_png`/`card_document`），导入按行解析 ST JSONL（`mes`/`is_user`/`swipes`/
`swipe_id`），坏行计入 `skipped`，全坏回滚报 422；分页按 `sequence` 游标取
`before` 之前最后 `limit` 条。客户端聊天页已有「导出聊天 (JSONL/JSON)」菜单、
「加载更早」按钮，角色页有「导入聊天记录」。

### 提示词与采样解析（P2）

生成前按固定优先级解析，不需要用户重复配置：

- **提示词**（`prompt.effective_prompts`）：`preset > character_cards.prompt_settings
  > 卡字段/默认值`；预设字段留空则落到卡设置，卡设置留空再落到
  `DEFAULT_MAIN_PROMPT` 等默认值。`system_prompt` 独立列不参与预设覆盖，
  `[越狱提示]` 块只在 preset/卡设置给值时出现。
- **采样**（`stream.sampling_for`）：`chat.sampling > 当前预设 > prompt_settings`；
  `None` 表示交给适配器默认值。`maxTokens` 走 `output_limit_for`，最终
  `min(模型 completion 上限, 请求值)`，下限 16。
- **预设绑定**：`chat.preset_id` 为空时使用 `service.default_preset`（内置
  「默认」）；聊天页可改绑定，`PATCH /chats/{id}` 会校验预设归属。
- **世界书位置**：每条条目自己的 `position` 决定拼块位置；卡级
  `worldInfoPosition` 只作为「新建条目」的默认值/回显，不覆盖已有条目。

### 聊天生成链路

`POST /chats/{id}/messages`（以及 `/regenerate`）不是任务队列，而是在**请求线程**
里完成三件事，然后返回 `StreamingResponse`：

1. 校验并写 user 消息（`add_message`，`sequence` 递增）；群聊先按
   `members[assistant_count % len(members)]` 选本轮 speaker，SSE 首帧把它带给前端；
2. `_reply_context` 组装 prompt：取卡 `prompt_settings`、`chat.preset_id` 绑定的预设
   （未绑定用默认预设）与 `default_profile`，按 `preset > 卡设置 > 默认值` 合并
   提示词，按 `chat > 预设 > 卡设置` 解析采样，算出本次 `output_limit`
   （`completion_limit` 与 `maxTokens` 取小，下限 16）；
3. 返回 `stream.reply_events(...)`，采样参数一起传给适配器调用。

`reply_events` 的运行时形状：

- 先发首帧（`send`：`{"type":"message","userMessage":…,"assistantSequence":…}`；
  `regenerate`：`{"type":"message","messageId":…,"activeSwipe":…}`；群聊两处都多带
  `speaker:{cardId,name}`）；
- 起一个 `daemon` 线程跑 `adapter_for().generate_stream(profile, system_prompt,
  data, output_limit, on_delta=…, cancel_check=…)`：`on_delta` 往
  `queue.Queue` 塞 `("delta", text)`，线程结束塞 `("result", …)` / `("error", …)`、
  最后 `("end", …)`；
- 响应生成器阻塞 `queue.get()`，把 delta 逐帧写 `data: {"type":"delta","text":…}`；
- 客户端断开或响应结束 → `cancel.set()`，`cancel_check` 抛
  `Problem("GENERATION_CANCELLED", …, 499)` 让适配器停下；
- 正常情况下收集完 delta，**只把完整文本**（及模型名、时间）写进 swipe，
  regenerate 时追加到目标消息的 `swipes` 并把 `active_swipe` 指到新版本；
  然后发 `data: {"type":"done","message":…}`。

失败帧（HTTP 仍是 200，错误只能走帧）：

| code | 触发 |
| --- | --- |
| `GENERATION_CANCELLED` | 客户端停止/断开 |
| `PROVIDER_UNAVAILABLE` | 适配器异常（非 `Problem`）或上游失败 |
| `REPLY_EMPTY` | 模型没有返回任何文本 |
| `PERSISTENCE_UNAVAILABLE` | 生成成功但写库失败 |
| `RESOURCE_NOT_FOUND` | 生成期间聊天/消息被删 |

SSE 响应头：`text/event-stream`、`Cache-Control: no-store`、
`X-Accel-Buffering: no`（避免中间层缓冲）。

> **已对齐**：服务端 `stream.sse()` 每帧同时输出 `event: {type}` 与 `data: {json}`，
> 与客户端 `sse.ts` 按 `event: delta|message|done|error` 派发一致；逐字流式正常。
> 契约见 `docs/contract.md` §8。

### Prompt 组装（SillyTavern 顺序）

`tavern/prompt.py:build` 生成的 system prompt 以 `tavern-v1` 开头，然后按顺序拼块：

```
[系统提示词]   预设 system > 卡 prompt_settings.system > 卡 system_prompt（可空）
[主提示词]     预设 main > 卡 prompt_settings.mainPrompt > DEFAULT_MAIN_PROMPT
[世界书 · 角色设定之前]   position=before_char 的命中条目
[角色设定]     姓名/描述/性格/场景
[你的角色]     人设（persona；聊天可带 personaId）
[示例对话]     example_messages（<START> 分隔）
[世界书 · 示例对话之前]   position=before_example 的命中条目
[世界书 · 角色设定之后]   position=after_char 的命中条目
[对话历史]     历史消息按预算裁剪；at_depth 世界书按 depth 插进去
[后置指令]     预设 post_history > 卡 postHistory / post_history_instructions
[越狱提示]     预设 jailbreak > 卡 jailbreakPrompt（都空则不出现）
[本轮要求]     "Only write {char}'s next reply now…"
```

提示词来源优先级为 **预设 > 卡 prompt_settings > 卡字段/默认值**
（`effective_prompts`）；预设由 `chat.preset_id` 绑定，未绑定用当前默认预设。
每块的字符数记录在 `build()` 返回的 `sections` 里，注入预览和
`/characters/{id}/prompt` 都会返回。

宏：`{{char}}`/`<BOT>`/`[char]` → 卡名；`{{user}}`/`<USER>`/`[user]` → 用户名
（当前默认 `User`，带 persona 时用 persona 名）。

**世界书匹配**（`match_world_info_with_reason`）：只看 `enabled` 条目；`constant`
无条件注入（reason=`constant`）；否则把条目的 `keys`（小写）与本轮消息 + 最近 4 条
用户消息拼成的文本做子串匹配（reason=`keyword:<命中的词>`）；`probability < 100`
时用 `sha256(id + "|" + haystack)` 前 8 位取模做**确定性**抽样（同一输入结果稳定，
便于回放）。命中条目按 `order` 升序注入；预览返回的 `reasons` 与生成共用同一套
匹配，因此预览就是「这一轮会注入什么」。

**历史裁剪**：`budget_chars = clamp(1200, 60000, (context_window - output_limit - 1024) * 3)`
（字符≈token×3）；从最新消息往前装，单条过长时保留**尾部**（续写需要承接最近内容），
最后按时间正序输出。`at_depth` 世界书按 `depth` 从末尾插入
（`lines.insert(max(0, len(lines) - depth), section)`）。

### 群聊 Prompt（`prompt.build_group`）

群聊复用单聊的组装顺序，差别是：

- `[群聊角色]` 块列出全部成员（名字 + 描述 ≤500 / 性格 ≤300 / 场景 ≤500），
  替换单聊的 `[角色设定]`；当前 speaker 的 `[系统提示词]`/`[主提示词]`/
  `[后置指令]`/`[越狱提示]` 取 speaker 自己的卡设置（preset 仍按聊天绑定）；
- `[对话历史]` 每行带发言人（`speaker: text`），其余角色只作为场景存在；
- `[本轮要求]` 固定为「本轮只扮演 <speaker>，其他人作为场景中的角色存在；
  不要替其他角色或读者说话，也不要写名字前缀。」+ `DEFAULT_REPLY_INSTRUCTION`；
- 落库时写 `speaker_card_id`，`done` 帧带 `speakerName`。

### swipe（多版本）语义

- 每条 assistant 消息的 `swipes` 是版本数组，`active_swipe` 是当前下标；
- 「重新生成」只对**最后一条** assistant 操作：用目标消息之前的历史重新生成，
  追加一个新 swipe，并把 `active_swipe` 指过去（旧版本保留）；
- `‹n/m›` 切换调用 `POST .../swipes/{index}/select`，只改 `active_swipe`，不改内容；
- 编辑 user 消息会**截断其后所有消息**（`truncate_after`），返回 `deleted` 数量；
  删除消息同样截断其后消息——这是「从这里重来」的语义；
- 聊天只在生成成功时写入新消息，停止/失败不会留下半个回复。

## P3 清理：story 域已删除

P3 删掉了旧 story 域的服务端与客户端代码，`app.main` 只剩 **86 条路由**
（tavern 36 + 认证/设置/角色卡解析/预设/正则/人设等）。具体：

| 已删除 | 说明 |
| --- | --- |
| `app/server/app/world/**` 的 story 路由、`play_controls`、`experience`、`story_flow`、`world_chat*` | `/worlds`、`/world-chats`、`/books`、`/play-controls` 等全部 404；客户端旧路由 redirect `/` |
| `cards/memory.py`（长期记忆/伙伴）、`cards/groups.py`（旧群聊） | 随 story 域一起删除；原生群聊改由 `tavern` 模块实现 |
| `app/world/worker.py`、旧 story 任务类型 | 已删除；`dsh-tavern-world-worker.service` 已由 host **停止并 disable** |
| 旧客户端页面（探索/作品/旧群聊/记忆等） | 路由表已删，不再打包 |

仍在、但只服务原生酒馆：`cards/parse.py`、`exporting.py`（卡解析/导出）、
`cards/space.py` 的 `regex_scripts`（设置页正则）与 `presets`、`tavern-personas`
（设置页人设）、`/character-cards/**` 旧卡片空间 API（UI 不再调用）。

数据层：代码只使用 `public` 的 `users`/`user_settings`/`device_sessions`/
`model_profiles`/`tasks`/`idempotency`/`rate_limits`/`cleanup_receipts`（以及
`foundation/models.py` 仍映射的 `product_*`），`world` 的 `character_cards`/
`card_lore`/`chat_presets`/`regex_scripts`/`tavern_presets`/`tavern_personas`/
`tavern_chats`/`tavern_messages`/`tavern_world_info`。**旧 story 表（`books`、
`world.lines`、`world_chat_sessions`、`world.memories`、`world.card_memories` 等）
目前仍在库里**，代码已无引用；DROP 属 host 清理项（备份
`story_between-pre-p3-20261105-081109.dump`）。

## 前端面板结构（client 半）

### 座位注册

| 座位 | 注册项 | 说明 |
| --- | --- | --- |
| `sidebar.panellist` | `{ id: 'tavern', order: 24, label: () => '酒馆' }` | 侧栏一行；图标是手写 SVG（`data-dsh-panel-entry="tavern"`） |
| `main` | `{ key: 'tavern', inject: () => ({ askDsh }) }` | 主面板；点击侧栏行后由宿主按 key 找到组件 |

`order=24` 落在紫微斗数（20）与 Skill Center（30）之间。

### 主面板

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 酒馆  当前：/chats/42   [额外要求（可 @角色名）] [让 DSH 续写] [角色扮演] [刷新] [新窗口] │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                    <iframe src="/tavern/"> 原生酒馆（角色卡/聊天）              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

iframe 地址是 `new URL('tavern/', document.baseURI)`：与宿主同源，cookie /
登录态自然共享。若上次在应用里打开过页面（应用上报过有效 `appPath`），重新挂载
面板时 iframe 会恢复到那里，见下文「位置记忆」。

工具条动作（`data-dsh-tavern-action`）：

| 动作 | 锚点值 | 当前行为 |
| --- | --- | --- |
| 刷新 | `reload` | 清掉位置记忆并回 `/tavern/` 应用首页重新加载（恢复失败兜底） |
| 新窗口打开 | `open` | 新标签页打开 `/tavern/` |
| 让 DSH 续写 | `continue` | 在聊天页可用：读 P2 聊天上下文（角色卡 + 最近对话）→ 组续写 prompt → 新开 DSH 会话；无聊天时按钮禁用并提示「先打开一个聊天」 |
| 角色扮演 | `roleplay` | 在聊天页可用：同上，以当前角色（或 `@角色名` 覆盖）第一人称即兴演绎 |
| 额外要求 | `data-dsh-tavern-extra` | 输入框；回车等同「让 DSH 续写」；`@角色名` 只对角色扮演生效 |

给自动化验收的稳定锚点：

| 属性 | 值 |
| --- | --- |
| `data-dsh-tavern-view` | 面板根节点（存在即面板已挂载） |
| `data-dsh-tavern-connected` | `"1"` / `"0"`：是否收到 pong/state |
| `data-dsh-tavern-restored` | `"1"` / `"0"`：本次挂载是否从位置记忆恢复了 iframe 地址 |
| `data-dsh-tavern-hint` | `idle` / `loading` / `waiting` / `ready` / `busy` / `sent` / `error` |
| `data-dsh-tavern-status` | 工具条状态文本（人话，不保证稳定，断言优先用 hint） |
| `data-dsh-tavern-frame` | iframe |

### 位置记忆（切面板不丢上下文）

DSH 切到别的面板会卸载 main 座位，iframe 一并销毁。插件侧做一层「最后一次有效
应用路由」记忆：

- **只记聊天页**：应用通过 `dsh-tavern:state` / `pong` 上报的 `appPath` 匹配
  `/^\/chats\/[^/]+$/` 时才记（卡库 `/`、卡详情、设置页不记），记进模块作用域
  并镜像到 `sessionStorage`（key `dsh-tavern:last-app-path`）；
- 面板挂载时 iframe `src = 'tavern/' + appPath`（去掉前导 `/`），没有记忆才用
  `tavern/`；
- 恢复期间（新文档 `onLoad` 之前）不覆盖记忆，避免 iframe 导航与 postMessage
  路由互相打架；`onLoad` 之后应用上报的路由才是权威值；
- 恢复的地址失效（应用 redirect 到 `/` 或报错页）不会卡死面板：点「刷新」
  清空记忆并回应用首页即可；
- 2.0 应用的路由是 `/`、`/characters/:id`、`/chats/:id`、`/settings`、`/settings/regex`，
  重挂面板会直接回到上次的聊天（`/chats/:id`）；这是「切面板不丢聊天」的关键路径。

实现注意：`appPath` 不含 `/tavern` base——应用用 `createWebHistory('/tavern')`，
`router.currentRoute.value.path` 天然去掉 base；拼 URL 时只加一次 `/tavern/`。

### 主题

应用**固定浅色**：`App.vue` 启动即写 `document.documentElement.dataset.theme='light'`，
没有明暗切换。宿主仍会观察 DSH 的 `color-scheme` 变化，给 iframe 发附加消息
`{type:'dsh-tavern:theme', theme}`（契约之外）并在 iframe onLoad 时补发一次；
应用不跟随，保持浅色，收到也无害。

## P2 postMessage 协议（契约 §3）

`ping/pong/state` 用于握手与路由上报（位置记忆依赖 `state.appPath`）；
`context-request/context` 在聊天页返回原生酒馆上下文（角色卡 + 最近对话），
其它页面回 `ok:false`，工具条据此启用/禁用。协议细节见 `docs/contract.md` §3。

| 方向 | 消息 | 字段 |
| --- | --- | --- |
| 父 → iframe | `dsh-tavern:ping` | — |
| iframe → 父 | `dsh-tavern:pong` | `appPath` |
| iframe → 父 | `dsh-tavern:state` | `appPath`、`hasContext` |
| 父 → iframe | `dsh-tavern:context-request` | `id`、`kind: 'continue' \| 'roleplay'`、可空 `target` |
| iframe → 父 | `dsh-tavern:context` | `id`、`ok`、`context{appPath,chatId,cardId,characterName,characterDescription,personality,scenario,firstMessage,recentMessages:[{role,text}]}` 或 `ok:false,error` |

父页面侧实现要点（保留）：请求带自增 id 关联、**8 秒超时**、失败不调用
`startSession`；origin + `event.source` 双校验。

## 交给 DSH Agent 的流程

`client.js` 的 `ensureWorkspaceThenAsk`：用 `ctx.get('workspaces')` 确保有工作区
（没有则 `initializeDefault()`）→ `ctx.get('uiWorkspace').startSession(workspaceId,
{prompt, whenDraftExists:'append'})`（同步 `void`）→ `ctx.get('layout').selectPanel(null)`
切回会话面板。编辑器预填靠 `COMPOSER_SELECTOR` 选中 Lexical/contenteditable/textarea，
用 `execCommand('insertText')` 模拟粘贴，轮询最多 ~15s、失败退化为剪贴板；
`fillSeqRef` 令牌防止多个动作抢同一个输入框。

2.0 的工具条动作（续写/角色扮演）走的就是这条链路：拿到聊天上下文后组 prompt、
新开 DSH 会话并预填；没有聊天时按钮禁用，不会新建空会话。prompt 模板见
`docs/contract.md` §4（续写「请以<角色名>的口吻继续写下一段回复…」、角色扮演
「请以<角色名>的身份扮演…」+ 角色设定 + 最近对话 + 可选额外要求）。

## 配置

| 配置 | 位置 | 作用 |
| --- | --- | --- |
| `AI_MODE` | `app/.env` | `dsh` = 应用走插件网关真生成；`mock` = 内置确定性回复（离线自测/回滚）；默认 `real`（应用自己的 provider 通道） |
| `DSH_AGENT_URL` | `app/.env` | 应用侧调网关的地址，生产 `http://127.0.0.1:3080/tavern-agent/v1` |
| `DSH_AGENT_MODEL` | `app/.env` | 可选，`provider/model`（如 `opencode-go/deepseek-v4.1-flash`）；DSH 默认模型切到未给网关配好凭据的 provider 时由应用侧覆盖，不必重启 DSH |
| `DSH_AGENT_SECRET` | `app/.env` | 应用调网关用的 Bearer secret，与 `DSH_TAVERN_AGENT_SECRET` 同值 |
| `DSH_TRUSTED_SECRET` | `app/.env` | 应用侧校验可信头的 secret；插件启动时读入 |
| `DSH_TAVERN_TRUSTED_SECRET` | 宿主环境 | 覆盖上面的 secret（隔离测试/应急） |
| `DSH_TAVERN_AGENT_SECRET` | `app/.env` 或宿主环境 | 模型网关 Bearer secret；缺省回退 `DSH_TRUSTED_SECRET`，都没有则网关一律 401 |
| `DSH_TAVERN_API_PORT` | 宿主环境 | 覆盖 API 上游端口（默认 8001） |
| `DSH_TAVERN_STATIC_PORT` | 宿主环境 | 覆盖静态上游端口（默认 8810） |
| `DSH_TAVERN_ENV_FILE` | 宿主环境 | 覆盖读取的 `.env` 路径（隔离测试/自测用，生产不设） |
| `DSH_TAVERN_AGENT_TIMEOUT_MS` | 宿主环境 | 覆盖模型网关超时（默认 120000；只用于测试，生产不设） |

secret 读不到时：打印告警、继续转发但**不带**可信头，应用侧按未登录处理；
不要让宿主崩溃。日志前缀统一 `[dsh-tavern]`。

改完代码：

- 只改 `client.js`：重启 DSH Web（或按宿主机制热载）后刷新页面；
- 改 `lib/index.js`：必须重启 DSH Web 进程（host 半启动时注册路由、读 secret）；
- 改应用/改 `.env`：重启对应 `dsh-tavern-*` 服务，插件不用动
  （secret 变了才需要重启 DSH Web）。

应用以 host 侧 systemd 单元运行（单元文件在 `/etc/systemd/system/`，不随本插件仓库分发）：

| 单元 | 入口 |
| --- | --- |
| `dsh-tavern-api.service` | `app/venv/bin/python -m uvicorn app.main:app --port 8001` |
| `dsh-tavern-web.service` | `/usr/bin/python3.11 app/deploy/static_server.py`（`:8810`） |
| `dsh-tavern-worker.service` | 旧任务队列 worker；2.0 生成在请求线程同步完成，host 已停止并 `disable`（新 `app/worker.py` 无 main 循环） |
| `dsh-tavern-world-worker.service` | 已停止并 `disable`（模块随 P3 删除；单元文件仍在 /etc/systemd/system，不再启动） |
| 实际运行 | 只有 `dsh-tavern-api`（uvicorn）+ `dsh-tavern-web`（静态 dist）；两个 worker 单元均已 disable |

## 风险 / 待验证

1. **应用侧可信登录**：`x-dsh-trusted` 由应用 `app/server/app/security.py` 用常量时间
   比较校验；应用进程必须带 `DSH_TRUSTED_AUTH=1` + `DSH_TRUSTED_SECRET` 运行，
   否则 API 请求会被应用当未登录（401）。
2. **SSE 帧格式（已对齐）**：服务端 `stream.sse()` 输出 `event: {type}` + `data: {json}`，
   客户端按命名事件派发；逐字流式已验证（C 组 250ms 采样断言文本增长）。
3. **每请求线程**：一次生成占一个请求线程 + 一个后台线程，无任务队列/限流；
   uvicorn 默认单进程，并发聊天会排队。压测或多人使用前先确认 worker 数。
4. **工具条依赖应用侧上下文（P3-c）**：「让 DSH 续写/角色扮演」在聊天页读
   `context-request` 的原生酒馆上下文（`characterName` 等字段，应用侧 task-56
   交付）；应用没打开聊天时按钮禁用。若应用侧字段改名/缺失，父页面按「缺字段
   容错」降级：缺角色名视为无上下文，缺设定/对话则少写对应段落。
5. **采样参数的生效条件**：应用侧已将 chat > 预设 > 卡设置解析好的
   temperature/topP/stop/maxTokens 透传到网关；网关改写后需 **DSH Web 重启**
   才加载（本轮验收后重启）。即使重启，`opencode-go` 适配器也只消费
   temperature/maxTokens/sessionId，忽略 topP/stop，`stop` 不作为硬截断保证。
6. **模型网关依赖应用侧 adapter**：`AI_MODE=dsh` 下应用走 `DshAgentAdapter`；
   网关 secret 在 `apply` 时读入，轮换后需重启 DSH Web；没配 secret 时连
   `/v1/health` 都 401（有意的「配置错就明确失败」）。
7. **真生成依赖 DSH 侧条件**：DSH 需已登录、`agentDefaultModel` 有默认模型
   （本机 `opencode-go / deepseek-v4.1-flash`）；provider 异常/超时表现为
   `PROVIDER_UNAVAILABLE`/`AGENT_UPSTREAM_FAILED`，应用不伪造回复。
8. **旧表已 DROP**：host 已按备份执行 `DROP TABLE ... CASCADE`（books/world.lines/world_chat_sessions 等 51 张旧表），库内只保留 users/settings/model_profiles/tasks/idempotency/rate_limits/cleanup_receipts + product_* + world 卡片/酒馆表；`dsh-tavern-world-worker` 已停止并 disable；
   清理前要确认没有数据迁移/回滚需求（P3 范围）。

9. **导入卡兼容性**：只解析 SillyTavern v1/v2/v3（PNG 内嵌 JSON 或 JSON）；
   异常卡会 4xx，不会半写入。
10. **正则替换只作用于落库文本**：`/settings/regex` 的启用规则在回复落库前按
    order 应用（`cards/space.apply_regex`，非法正则 warning 跳过）；SSE 流式
    delta 仍是原文，刷新后读到替换后的文本。开场白不经过正则。

## 验收锚点一览（给测试用）

- 侧栏：`[data-dsh-panel-entry="tavern"]`、label「酒馆」、order 24；
- 面板：`[data-dsh-tavern-view]`、`[data-dsh-tavern-connected="1"]`、
  `[data-dsh-tavern-frame]`、`[data-dsh-tavern-action=reload|open|continue|roleplay]`；
- 应用：`TavernShell` 顶栏品牌「酒馆」；角色卡网格、导入、点卡进 `/chats/<id>`；
  聊天 SSE（C 组断言 250ms 内文本增长 ≥2 种长度）；重生成后 `‹n/m›` 变多；
- P2 功能（W/S2/Q 组）：世界书 `position=after_char` / `depth=2` / `constant` /
  `probability` / `order` 保存回显，注入预览出现命中与原因；聊天页「采样参数」
  Sheet 保存后刷新回显且带参生成不报错；设置页新建预设（system/temperature/
  maxTokens）→ 设默认 → 新聊天 prompt 含预设文本，非法采样后端 422；
- 最新完整验收（隔离实例 + 生产）全部 PASS，条数以 `report.json` 的 `summary` 为准；
- 旧路由：`/tavern/explore`、`/tavern/works`、`/tavern/worlds/scripts` 落 `/`；
  页面无「探索作品」入口（M 组）。
