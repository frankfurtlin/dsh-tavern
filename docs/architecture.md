# dsh-tavern 架构

## 一句话

插件由**两个半边**组成：`lib/index.js`（host 半）在 DSH Web 里注册
`prefix /tavern` 反向代理（旧 `/story` 作为兼容别名），把本地写作应用挂到 DSH
登录态后面，并另外注册 `prefix /tavern-agent` 模型网关给应用 worker 调 DSH 模型；
`client.js`（client 半）在浏览器里注册侧栏入口与主面板，用同源 iframe 展示应用，
并用 `postMessage`（P2 桥）把当前故事上下文交给 DSH Agent 续写 / 角色扮演。
应用本体按单用户精简（顶部导航只有「探索作品」「角色卡库」两个 tab，无左侧栏，
界面固定浅色），提供角色卡空间与长期记忆，生成任务在应用 worker 里跑；
群聊已从 UI 与服务端一并删除。

```
┌────────────────────────── DSH Web (127.0.0.1:3080) ──────────────────────────┐
│  client.js（浏览器）                                                          │
│   ├─ slots.register('sidebar.panellist', id=tavern, order=24)                 │
│   └─ slots.register('main', key=tavern)                                       │
│        └─ 工具条 + <iframe src="/tavern/">  ←── P2 postMessage ──┐             │
│                                                                │             │
│  lib/index.js（host）                                          │             │
│   ├─ ctx.get('connection').requestRejection(req)  ← 登录闸门   │             │
│   ├─ ctx.webServer.register({kind:'prefix', path:'/tavern'})    │             │
│   └─ ctx.webServer.register({kind:'prefix', path:'/tavern-agent'})            │
└───────────────┬────────────────────────────────────────────────┘             │
                │ 剥掉 /tavern 前缀                                             │
     ┌──────────┴───────────┐                                                   │
     ▼                      ▼                                                   │
 /api/** /health/**      其余 /assets、HTML、sw.js                              │
     │                      │                                                   │
     ▼                      ▼                                                   │
 127.0.0.1:8001        127.0.0.1:8810                                           │
 uvicorn(FastAPI)      app/dist 静态站                                          │
     ▲                                                                          │
     └── 附加 x-dsh-trusted: <secret>（应用侧校验可信登录身份）──────────────────┘

应用 worker（AI_MODE=dsh，服务端进程、无浏览器会话）
  └─ DshAgentAdapter ── Authorization: Bearer <DSH_AGENT_SECRET> ──▶
       /tavern-agent/v1/**（host 半）──▶ ctx.llm.stream() ──▶ DSH 模型通道
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

这样内嵌面板天然继承 DSH 的登录态，插件不另做一套认证；`/tavern`
在公网上也只能经 Caddy → DSH 网关访问，端口本身只监听回环地址。

### 分流（先剥前缀，再转发）

| 浏览器请求 | 上游 | 超时 | 额外头 |
| --- | --- | --- | --- |
| `/tavern/api/**` | `127.0.0.1:8001` | **不设**（生成/续写可能数分钟） | `x-dsh-trusted: <secret>` |
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
| `GET/HEAD /story/...` | `302` 到 `/tavern/...`，保留 path 后缀与 query | 书签、旧 iframe 缓存、外链都能平滑换到新地址；用 302 而非 301，别名将来下线时浏览器不会永久缓存 |
| `POST/PUT/PATCH/DELETE /story/...` | **不重定向**，把请求前缀改写成 `/tavern` 后照常反代 | 302 会把 POST 降级成 GET 并丢 body；308 又要求客户端重放 body（浏览器做不到无感）。原地改写 body 一个字节都不碰 |

两种路径都先过登录闸门；命中分流规则与 `/tavern` 完全一致。

### 头处理

- 逐跳头（`connection`、`keep-alive`、`transfer-encoding`、`upgrade`、
  `proxy-*`、`te`、`trailer`）双向都不透传；
- 入站的 `x-dsh-trusted` 一律删除，只允许插件按自己的 secret 重新签发，
  避免客户端自己伪造可信身份；
- 入站的 `x-forwarded-*` 删除后重写，防止伪造来源；
- `Host` 重写为上游地址，`X-Forwarded-Host` 保留浏览器原始 Host；
- 响应头同样剥逐跳头，由 node 重新计算 `Transfer-Encoding`。

### 流式与错误

- 用 `req.pipe(upstream)` / `upstreamRes.pipe(res)` 转发，保留 chunked、
  multipart 上传与 SSE；`text/event-stream` 响应立即 `flushHeaders()`，
  生成第一帧不会憋在代理层。
- API 不设响应超时；静态上游 10s 不响应则销毁连接。
- 错误只回中文友好文案，细节（错误码、端口、路径）只进宿主日志：
  - 上游连不上 → `502 酒馆应用暂时不可用，请稍后重试。`
  - 静态上游超时 → `504 酒馆应用响应超时，请稍后重试。`
- 客户端断开（`req` aborted / `res` close）时销毁上游请求，避免泄漏。

## DSH 模型网关 `/tavern-agent/v1/**`（host 半）

应用 worker 是**服务端进程**：它没有浏览器会话，也不该让 API 进程为了调模型
去拿 DSH 登录 cookie。所以插件另开一个平级前缀 `/tavern-agent`，给应用
worker 做服务端到服务端调用，复用 DSH 已经在跑的模型通道。

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/tavern-agent/v1/health` | `GET` | 探活 + 返回 DSH 当前默认模型 |
| `/tavern-agent/v1/complete` | `POST` | 一次性返回整段文本 |
| `/tavern-agent/v1/stream` | `POST` | SSE 增量返回（聊天面板用） |

### 鉴权

- 只认 `Authorization: Bearer <secret>`，用 `timingSafeEqual` 做常量时间比较；
- secret 读取顺序：环境变量 `DSH_TAVERN_AGENT_SECRET` → `app/.env` 的
  `DSH_TAVERN_AGENT_SECRET` → `DSH_TRUSTED_SECRET`（应用侧一般配成同一个值）；
- **没配 secret 时一律 401**（`AGENT_UNAUTHORIZED`），不静默放行；
- 网关**不过** `requestRejection`：它服务的是 worker 而不是浏览器；
  `/tavern`、`/story` 的闸门行为不受影响。

### 请求 / 响应

```http
POST /tavern-agent/v1/complete
Authorization: Bearer <secret>
Content-Type: application/json

{
  "system": "你在写一部仙侠小说……",
  "messages": [{ "role": "user", "content": "请续写下一段" }],
  "maxTokens": 1024,
  "temperature": 0.8,
  "sessionId": "a7f1c0d2e3b4",
  "reasoningEffort": "low",
  "model": { "provider": "opencode-go", "model": "deepseek-v4.1-flash" }
}
```

- `messages` 的 `content` 可以是字符串，也可以是 `[{ "type": "text", "text": "..." }]`
  （插件统一归一成 DSH 的 `ContentBlock[]`；裸字符串直接丢给 `ctx.llm.stream` 不合法）；
  目前只接受 `role: "user"`，多轮上下文由应用自己拼进 content；
- `model` 可选；不传时用 `ctx.agentDefaultModel.currentSelection()`，
  两者都没有则 502；
- `sessionId` 可选：应用按会话传稳定值（≤200 字符）能提高上游缓存命中；不传时
  网关每次生成一个 UUID——opencode-go 要求 `x-opencode-session`，缺了会
  `400 MissingSessionID`；
- `reasoningEffort` 可选，白名单 `off|minimal|low|medium|high|xhigh|max`，
  **默认 `low`**：profile 默认是 `high`，省略会继承 high 把 maxTokens 烧在思考里；
  当前聊天模型（deepseek-v4.1-flash）实测只支持 `low`/`high`，传
  `off`/`minimal`/`medium` 会被上游拒（`UNSUPPORTED_REASONING_EFFORT`）；
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
| 上游报错 / 没有可用默认模型 / 无完成信号 | 502 | `AGENT_UPSTREAM_FAILED` |
| 方法不对 / 未知路径 | 405 / 404 | `AGENT_METHOD_NOT_ALLOWED` / `AGENT_NOT_FOUND` |
| 超过 120s（`AGENT_TIMEOUT_MS`） | 502 | `AGENT_UPSTREAM_FAILED`（文案含「超时」） |

超时会 `AbortController.abort()` 中断上游；客户端断开同样中断，避免孤儿请求。
**日志里只有模型名、字数、耗时和错误码**，不含 prompt、消息内容或 secret；
provider 的原始报错文本只取 `code`/`name` 进日志，不会回显给调用方。

应用侧对应配置见 `docs/step2-dsh-generation.md` §4：`AI_MODE=dsh` 时
`DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1`，`DSH_AGENT_SECRET` 与
`DSH_TAVERN_AGENT_SECRET` 配同一个值；回滚时把 `AI_MODE` 改回 `mock`。

## 应用内界面与功能：角色卡空间、长期记忆（群聊已删除）

host 半只做反向代理，以下链路都在应用侧。记在这里是为了让「面板里看到什么、
模型收到什么」可追溯；表结构与验收点以 `docs/step3*.md`、`docs/step4*.md` 为准。

### 界面结构（单用户精简后）

- **顶部 sticky header**（`presentation/AppShell.vue` 的 `.shell-top`，min-height 66px，
  scoped 样式在 `AppShell.vue` 里，不含左侧栏）：左侧「酒馆」品牌（`BookOpen` 图标，
  链 `/explore`）+ 两个 tab「探索作品」`/explore`、「角色卡库」`/characters`
  （`workspace/navigation.ts` 的 `workspaceGroups` 拍平去重）；右侧
  `.shell-top-right` 放 `#sidebar-footer` 槽位（仅 LocalApplication 兼容，移动端隐藏）
  与 `#header-actions`（当前为空；顶部右侧只保留布局容器）。
- **无左侧栏、无面包屑**：旧 sidebar 与 breadcrumb 已删；页面内的「返回上一级」
  由 `AppShell` 的 `.shell-page-return` 按 `pageLocation` 计算（有父级且非自有返回时显示）。
- **移动端**（≤767px）：header 只留品牌 + actions，`shell-tabs` 隐藏，底部
  `.shell-mobile-nav` 固定显示两项导航。
- **创作工作台**：路由 `work-entry`（`/stories/:storyId`）时内容区高度按
  `calc(100dvh - 66px)` 铺满，导航仍从探索页「创作作品」按钮进入。
- **固定浅色主题**：`App.vue` 启动即 `document.documentElement.dataset.theme='light'`，
  明暗切换按钮已删除；`settings.theme` 字段保留兼容但不参与 UI。
  `LocalApplication.vue`/`LocalReaderPage.vue` 旧壳仍留在仓库（当前无路由/无引用，
  `/local/**` 只做 legacy 跳转），它们内部的外观逻辑不影响主界面。
- 旧路由 redirect 清单：`/works`、`/recent`、`/my-prompts`、`/product/knowledge`、
  `/published/*`、`/authors/*`、`/creators`、`/community`、`/characters/groups`、
  `/characters/groups/:id`、`/tasks` 等全部 redirect `/explore`；`/product/wallet`、
  `/product/admin`、`/worlds/:id/author` 等运维/作者页保留。探索页去掉了
  「最近游玩 → /recent」链接与「公开互动作品」整节，改为对有 `lineId` 的精选卡
  显示「最近游玩」徽标。

### 服务端瘦身（task-29）

单用户精简同时删掉了没有 UI 的服务端能力，当前应用路由总数 318：

- `experience`：feedback 7 条路由、notices、tasks、`usage.py` 全部删除，
  `experience/api.py` 只剩一个空 router 占位（保留前缀兼容）；
- `authoring`：公开目录 `GET /authoring/catalog`、`GET /authoring/authors/{id}`
  删除，保留 curated publish/beta、审核、发布等运维接口；
- `cards`：`cards/groups.py` 整文件、5 条 `/card-group-chats` 路由、
  `CardGroupChat` / `CardGroupMessage` 模型、worker `card_group_chat` 分支、
  `prompts/card_group_chat.txt` 与相关测试删除；
- 保留：支付/邀请/analytics 等运维集成；`product_notices` 保留（moderation
  notify 仍写），`product_feedback` 不再有 API，已由 host 侧归档并 DROP；
  DB 中 `world.card_group_chats` / `world.card_group_messages` 已无代码引用，
  已由 host 侧 DROP；
- 客户端同期删除 24 个零引用页面组件（PublicCatalog/PublicCover/AgentPage/
  McpPage/DiscoverPage/OriginalStoryPage/companion 页面/CharacterPreferences/
  RecoverySettings/media 四页/PlayCreatePage/PluginsPage/SkillsPage/tavern 的
  ChatImport/Lorebooks/Personas/Presets/QuickReplies/StagePage/SceneModelSettings），
  保留 `AuthorPage`/`WalletPage`/`AdminPage`/`PublicPage` 与 `Local*` 旧壳
  （部分未挂路由）。

### 卡片 → world 建库链路

导入一张卡（`POST /api/reader/v1/character-cards/imports`，multipart，PNG 或 JSON）
时，应用不只存卡：

1. `cards/parse.py` 解析 SillyTavern v1/v2/v3 卡（PNG 内嵌 JSON 或 JSON 文件），
   得到 description/personality/scenario/first_message/system_prompt/
   post_history_instructions 与可选 `character_book`；
2. `cards/service.py:import_card` 新建一个 `World`
   （`source={"kind":"character-card",...}`），把卡设定转成 world 的 definition/state；
3. `start_line(...)` 建一条 line + 根 checkpoint + 一个 character，
   `card.world_id/line_id/checkpoint_id/character_id` 全部回填；
4. `space.import_lore` 把 `character_book` 条目写进 `world.card_lore`（source=import）；
5. 开聊 `POST /character-cards/{id}/chats` 复用同一 world/line/checkpoint/character，
   建 `WorldChatSession`（「<名字> · 单独聊聊」）。

因此卡库列表、聊天会话、世界书、记忆都以 world 为归属；删卡是软删除，world 保留。
（群聊表 `card_group_chats` / `card_group_messages` 已随群聊删除，见下文。）

### 角色卡空间与 system prompt 注入（B1）

表（`cards/models.py`）：`world.card_lore`、`world.chat_presets`、`world.regex_scripts`。

聊天每轮生成前，`worker.py` 对 `world_character_chat` 调
`cards.space.chat_injection`，把以下段落追加在冻结后的 system prompt 之后，
段间空行：

```
以下内容仅作扮演补充，与 source 冲突时以 source 为准。

【角色卡设定】  description/personality/scenario/system_prompt/post_history_instructions（≤6000 字）
【世界书】      最多 6 条命中（关键词大小写不敏感，匹配本条 + 最近 4 条用户消息）
【预设】        system_suffix（≤4000 字）
【长期记忆】    最多 6 条 / 合计 ≤3000 字 —— 见下一节
```

- 世界书按 `order` 升序、累计 ≤6000 字；
- 正则 `world.regex_scripts` 在 `worker.py` 保存回复前按 `order` 逐条 `re.sub`；
  流式 delta 不改，刷新后看到的是改写后的文本；
- 卡详情「预览」`POST /character-cards/{id}/context-preview` 返回同一套拼接结果
  （`loreMatched/preset/systemSuffix/memorySection`），UI 用它解释「下一轮会注入什么」。

### 群聊（B2，已彻底删除）

单用户精简（task-25）先删掉界面：角色卡库多选与「发起群聊」入口、群聊列表页与
群聊页、`/characters/groups` 入口；随后 task-29 把服务端链路也删了：

- 删除 `cards/groups.py` 与 5 条 `/card-group-chats` 路由；
- 删除 `CardGroupChat` / `CardGroupMessage` 模型（`cards/models.py`）；
- 删除 worker 的 `card_group_chat` 任务分支与 `prompts/card_group_chat.txt`；
- 删除对应服务端测试。

DB 里 `world.card_group_chats`、`world.card_group_messages` 已无代码引用，由
host 侧 DROP；`/characters/groups`、`/characters/groups/:id` 仍 redirect `/explore`。
原始设计（表结构、轮转发言人、Idempotency-Key、409 CHAT_PENDING、202 任务响应等）
保留在 `docs/step3b-group-chat.md` 作为历史参考，不再有运行时代码。

### 长期记忆与长期伙伴（C）

表：`world.card_memories`（`kind` fact|preference|relationship|event、`title`≤120、
`content`≤2000、`salience` 1–100、`enabled`、`source auto|manual`）；
`character_cards.companion` 控制「长期伙伴」置顶分组。

- 手工：`GET/POST /character-cards/{id}/memories`、`PATCH/DELETE .../{memoryId}`；
  每卡上限 40 条；
- 抽取：`POST /character-cards/{id}/memories/extract` 同步调用模型
  （prompt `card_memory_extract.txt`，最近 60 条 ≤12000 字，严格 JSON），
  失败重试一次仍失败 → `502 MEMORY_EXTRACT_FAILED`，不写半成品；
- 自动：每 8 条助手回复（`AUTO_EVERY`）自动入队 `card_memory_extract`，
  `card.memory_task_id` 防重入，去重键 `memory:{cardId}:{saved}`；
- 注入：只取 `enabled` 记忆，按 `salience` 降序 + `updated_at` 降序，最多 6 条且
  累计 ≤3000 字，拼成【长期记忆】段插在【预设】之后；
- 长期伙伴：`POST /character-cards/{id}/companion` 切换；卡库列表把伙伴卡置顶分组。

### 失败降级

| 场景 | 表现 |
| --- | --- |
| 模型网关不可用（401/502/超时） | 应用生成失败，UI 显示失败原因，不伪造回复；`AI_MODE=mock` 可临时回滚 |
| 记忆抽取失败 | `502 MEMORY_EXTRACT_FAILED`，不落半成品；自动整理失败不阻断聊天 |
| 世界书/记忆超预算 | 超出条目静默截断（不进 prompt），可在 context-preview 里核对 |
| 角色卡/记忆接口不可用 | 页面局部错误文案（如「长期记忆暂时不可用。」），不影响其它区块 |

注意：以上都是应用侧行为，host 半只负责把 `/tavern/api/**` 原样转给 8001；
网关错误码（`AGENT_*`）与 `/tavern` 反代的 502/504 文案是两套，不要混淆。

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
│ 酒馆  当前：/worlds/w1/play    [续写要求 / @角色名] [让 DSH 续写] [角色扮演] [刷新] [新窗口打开] │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         <iframe src="/tavern/"> 应用本体                      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

iframe 地址是 `new URL('tavern/', document.baseURI)`：与宿主同源，cookie /
登录态自然共享，也方便 P2 用 `window.location.origin` 做目标校验。
若上次在应用里打开过正文页（应用上报过有效 `appPath`），重新挂载面板时
iframe 会恢复到那里，见下文「位置记忆」。

工具条上的动作：

| 动作 | `data` 锚点 | 行为 |
| --- | --- | --- |
| 让 DSH 续写 | `data-dsh-tavern-action="continue"` | 读上下文 → 组续写 prompt → 新开 DSH 会话 |
| 角色扮演 | `data-dsh-tavern-action="roleplay"` | 读上下文 → 选角色 → 组角色扮演 prompt |
| 刷新 | `data-dsh-tavern-action="reload"` | 清掉位置记忆并回 `/tavern/` 应用首页重新加载（恢复失败时的兜底） |
| 新窗口打开 | `data-dsh-tavern-action="open"` | 新标签页打开 `/tavern/` |
| 额外要求 | `data-dsh-tavern-extra` | 输入框；回车等同「让 DSH 续写」 |

给自动化验收的稳定锚点：

| 属性 | 值 |
| --- | --- |
| `data-dsh-tavern-view` | 面板根节点（存在即面板已挂载） |
| `data-dsh-tavern-connected` | `"1"` / `"0"`：是否收到 pong/state |
| `data-dsh-tavern-restored` | `"1"` / `"0"`：本次挂载是否从位置记忆恢复了 iframe 地址 |
| `data-dsh-tavern-hint` | `idle` / `loading` / `waiting` / `ready` / `busy` / `sent` / `error` |
| `data-dsh-tavern-status` | 工具条状态文本（人话，不保证稳定，断言优先用 hint） |
| `data-dsh-tavern-frame` | iframe |

状态提示优先级：动作反馈（notice）> 加载中 > 等待应用 > 未打开正文 >
已连接并显示当前页面。`hint` 是按同一状态压缩出的稳定 token。

### 位置记忆（切面板不丢上下文）

DSH 切到别的面板会卸载 main 座位，iframe 一并销毁；应用重新加载后默认停在
`/explore`，故事上下文就丢了。插件侧做一层「最后一次有效应用路由」记忆：

- 应用通过 `dsh-tavern:state` 上报 `appPath` 且 `hasContext=true`（或 path 落在
  `/worlds/` 下，`pong` 也认）时，把 `appPath` 记进模块作用域，并镜像到
  `sessionStorage`（key `dsh-tavern:last-app-path`）；
- 面板挂载时 iframe `src = 'tavern/' + appPath`（去掉前导 `/`），没有记忆才用
  `tavern/`；`/` 首页不记；
- 恢复期间（新文档 `onLoad` 之前）不覆盖记忆，避免 iframe 导航与 postMessage
  路由互相打架；`onLoad` 之后应用上报的路由才是权威值；
- 恢复的地址失效（应用回落到 `/explore` 或报错页）不会卡死面板：点「刷新」
  清空记忆并回应用首页即可；插件本身不因恢复失败抛错；
- 新增锚点 `data-dsh-tavern-restored` 便于验收区分「恢复」与「回首页」。

实现注意：`appPath` 是应用路由路径（如 `/worlds/w1/play`），**不含** `/tavern`
base —— 应用用 `createWebHistory('/tavern')`，`router.currentRoute.value.path`
天然去掉 base；拼 URL 时只加一次 `/tavern/`。

### 主题

应用**固定浅色**：没有明暗切换按钮，`App.vue` 启动即写
`document.documentElement.dataset.theme='light'`；`settings.theme` 字段保留兼容但
不参与界面。宿主仍会观察 DSH 的 `color-scheme` 变化，给 iframe 发附加消息
`{type:'dsh-tavern:theme', theme}`（契约之外）并在 iframe onLoad 时补发一次；
应用当前不跟随，保持浅色，收到也无害。

## P2 postMessage 协议（契约 §3）

同源通信，双方向都按 `event.origin === window.location.origin` 校验；
父页面还会校验 `event.source === iframe.contentWindow`。

父 → iframe：

| 消息 | 字段 | 说明 |
| --- | --- | --- |
| `dsh-tavern:ping` | — | 握手；应用回 pong |
| `dsh-tavern:context-request` | `id`、`kind: 'continue' \| 'roleplay'`、可空 `target` | 请求当前故事上下文；`id` 用于关联响应 |

iframe → 父：

| 消息 | 字段 | 说明 |
| --- | --- | --- |
| `dsh-tavern:pong` | `appPath` | 回应 ping，父页面标记已连接 |
| `dsh-tavern:state` | `appPath`、`hasContext` | 应用主动上报状态（建议页面就绪时发一次） |
| `dsh-tavern:context` | `id`、`ok: true`、`context{...}` 或 `ok:false`、`error` | 上下文响应 |

`context` 字段：`appPath`、`worldTitle`、`lineTitle`、`mode`、
`characters[{name, identity, speech}]`、`activeCharacter`（最近活跃角色名，
可空）、`recentText`（最近正文纯文本，截断到 ~4000 字）。

父页面侧实现要点：

- 请求带自增 id，`Map<id, {resolve, timer}>` 关联；**8 秒超时**，超时/断连
  都 resolve 成 `{context:null, error}`，界面不会卡死；
- 失败时把应用给的原因拼进提示（`读取故事内容失败：<error>…`），
  且**不**调用 `startSession`（不产生空会话）；
- 应用不实现 P2 时表现为「等待应用就绪…」，工具条仍可点，只是读不到内容。

## 交给 DSH Agent 的流程

`client.js` 的 `ensureWorkspaceThenAsk`：

1. 用 `ctx.get('workspaces')` 的 `list.getSnapshot().items` 看有没有工作区；
2. 没有则 `service.initializeDefault()` 建一个默认工作区并取 `view.id`；
3. 调 `ctx.get('uiWorkspace').startSession(workspaceId, { prompt, whenDraftExists: 'append' })`
   —— **注意它是同步 `void`**，不要 await 它「等会话建好」；
4. `ctx.get('layout').selectPanel(null)` 切回会话面板（让用户看到新会话）。

编辑器预填（宿主没有公开的草稿写入 API，只能模拟用户粘贴）：

- `COMPOSER_SELECTOR` 依次匹配 `[data-lexical-editor="true"]`、
  `[contenteditable="true"][role="textbox"]`、`[contenteditable="true"]`、`textarea`；
- 切会话前先快照已有编辑器，填充时**避开这些旧节点**——新会话的编辑器是重新
  挂载的，命中的旧节点会把 prompt 写进上一个会话；
- 找到新编辑器后聚焦、放光标到末尾，`document.execCommand('insertText')`，
  失败退 `insertHTML`，`textarea` 则直接赋值 + 触发 `input` 事件；
- 轮询 250ms、最多约 15 秒、写入后读回校验（React 重置内容会重写），
  最多重试 6 次；写不进去就提示「内容已复制到剪贴板，粘贴后发送」；
- 每次新动作会自增填充令牌（`fillSeqRef`），取消上一次还在等编辑器的填充任务，
  避免两个动作抢同一个输入框。

剪贴板兜底在 `startSession` 之前就写好（`navigator.clipboard.writeText`），
即使后续 DOM 填充失败，内容也已经可用。

## prompt 组装规则

### 续写

```
请帮我接着下面的故事往下续写。

【作品】…
【章节】…
【模式】…            （应用给了才写）
【页面】…            （应用给了才写）

【角色】
- 名字（身份），说话口吻：…

【最近正文】
…

【要求】
请基于以上内容续写下一段（400–600 字，保持文风与人物口吻）。

【我的额外要求】      （工具条输入框非空才写）
…
```

### 角色扮演

```
请以角色扮演的方式，接着下面的故事即兴演绎。

【作品】…
【模式】…（默认「角色扮演」）

【角色】
- 名字（身份），说话口吻：…

【你要扮演】名字（身份），说话口吻：…

【最近情境】
…

【要求】
以 <名字> 的第一人称即兴演绎，保持其身份、认知与说话口吻。
承接最近情境往下写 300–500 字，不要复述原文，也不要写成旁白。

【我的额外要求】      （可空）
…
```

### 扮演对象的选择优先级

1. 额外要求里写了 `@角色名` → 按名字（或包含匹配）选；这个 `@token` 会从
   额外要求里剥掉，不会原样进 prompt；
2. 否则用 `context.activeCharacter`（契约 §3，最近活跃角色）；
3. 都为空 → 退化取 `characters` 末位；
4. 一个角色都没有 → 提示「当前故事没有可扮演的角色」，不新开会话。

## 配置

| 配置 | 位置 | 作用 |
| --- | --- | --- |
| `AI_MODE` | `app/.env` | `dsh` = 应用 worker 走插件网关真生成；`mock` = 内置确定性回复（离线自测/回滚）；默认 `real`（应用自己的 provider 通道） |
| `DSH_AGENT_URL` | `app/.env` | 应用侧调网关的地址，生产 `http://127.0.0.1:3080/tavern-agent/v1` |
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
- 改 `lib/index.js`：必须重启 DSH Web 进程（host 半在启动时注册路由、读 secret）；
- 改应用/改 `.env`：按 `app/` 的部署方式重启对应 `dsh-tavern-*` 服务，
  插件不用动（secret 变了才需要重启 DSH Web）。

应用以 host 侧 systemd 单元运行（单元文件在 `/etc/systemd/system/`，不随本插件仓库分发）；入口如下：

| 单元 | 入口 |
| --- | --- |
| `dsh-tavern-api.service` | `app/venv/bin/python -m uvicorn app.main:app --port 8001` |
| `dsh-tavern-web.service` | `/usr/bin/python3.11 app/deploy/static_server.py`（`:8810`） |
| `dsh-tavern-worker.service` | `app/venv/bin/python -m app.worker` |
| `dsh-tavern-world-worker.service` | `app/venv/bin/python -m app.world.worker` |

## 风险 / 待验证

1. **应用侧可信登录**：`x-dsh-trusted` 由应用 `app/server/app/security.py` 用常量时间
   比较校验；应用进程必须带 `DSH_TRUSTED_AUTH=1` + `DSH_TRUSTED_SECRET` 运行，
   否则 API 请求会被应用当未登录（401）。可信用户不存在时应用返回 503（不是白屏）。
2. **编辑器 DOM 依赖**：预填靠 Lexical 的 `contenteditable` + `execCommand`，
   宿主升级编辑器实现时可能失效。失效表现是「提示已复制到剪贴板」，
   不会丢内容；发现异常先查 `COMPOSER_SELECTOR` 与实际 DOM。
3. **`dsh-tavern:theme` 是契约外的附加消息**：应用已固定浅色、不再跟随，
   收到只当无操作；宿主侧发送逻辑保留，删除该消息也不影响功能。
4. **8 秒上下文超时**：应用读到上千字正文通常远快于此；若真实环境常超时，
   需要应用侧优化读取或调整 `CONTEXT_TIMEOUT_MS`（改之前先确认契约是否接受）。
5. **父页面只认自己的 iframe**：`event.source` 校验 + origin 校验；
   应用若在 iframe 里再嵌第三方页面并转发消息，需要显式处理，当前不支持。
6. **模型网关依赖应用侧 adapter**：网关只负责「把一次模型调用转发给
   `ctx.llm.stream`」；`AI_MODE=dsh` 下应用 worker 需要 `DshAgentAdapter`
   按契约 URL 调本插件，否则应用仍走 mock 生成。网关 secret 在 `apply` 时
   读入，轮换 secret 后需要重启 DSH Web 才生效；没配 secret 时健康检查也会 401，
   这是有意的「配置错就明确失败」，不是故障。
7. **真生成依赖 DSH 侧条件**：DSH 需已登录、`agentDefaultModel` 有默认模型
   （本机 `opencode-go / deepseek-v4.1-flash`）；provider 侧异常/超时表现为
   生成失败，应用不会伪造回复，也不该被前端吞掉。
8. **卡片空间/记忆的注入预算**：世界书 ≤6 条/≤6000 字、记忆 ≤6 条/≤3000 字、
   卡设定 ≤6000 字、预设 ≤4000 字；超出静默截断。群聊链路（含其服务端任务）
   已整体删除，不再存在「无入口但可调用」的群聊注入路径。
9. **记忆整理是模型调用**：`/memories/extract` 同步等模型，失败 502
   `MEMORY_EXTRACT_FAILED` 且回滚；每 8 条回复的自动整理是后台任务，
   失败不打断聊天，但需要用户稍后手动重试。

## 验收锚点一览（给测试用）

- 侧栏：`[data-dsh-panel-entry="tavern"]`、label「酒馆」、order 24；
- 面板：`[data-dsh-tavern-view]`、`[data-dsh-tavern-connected="1"]`、
  `[data-dsh-tavern-frame]`（`src` 以 `/tavern/` 结尾）、
  `[data-dsh-tavern-action=continue|roleplay|reload|open]`、`[data-dsh-tavern-extra]`；
- P2：假 iframe 可断言收到 `context-request{kind,id}`、父页面发出 `ping`/`theme`；
- 交 Agent：替身 `uiWorkspace.startSession` 能拿到含 `【作品】/【章节】/【最近正文】`
  的 prompt，并观察到 `layout.selectPanel(null)`。
