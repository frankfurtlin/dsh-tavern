# dsh-tavern 集成契约（v3：酒馆 2.0 原生酒馆）

目标：把本地「酒馆」（Tavern）应用装成 DSH 插件，放在
`/root/workspace/plugins/dsh-tavern`，在 DSH Web 里以内嵌面板访问，登录只走 DSH
闸门。**2.0 起产品改为单用户原生酒馆**：角色卡网格 → 点卡进全屏聊天，SSE 流式，
重新生成追加版本（swipe），世界书按关键词注入 prompt；故事模式 / 探索 / 群聊 /
长期记忆等旧 UI 已下线（服务端旧代码暂留，P3 清理）。

应用源码在 `plugins/dsh-tavern/app`，host 侧 systemd 单元：

- `dsh-tavern-api`  → uvicorn，127.0.0.1:8001（聊天 SSE 在请求线程直连网关）
- `dsh-tavern-web`  → 静态 dist，127.0.0.1:8810
- `dsh-tavern-worker` / `dsh-tavern-world-worker` → 旧 story 模式任务 worker（2.0 聊天不依赖）

本契约按「谁写哪半边」分节；改代码前先读对应节，接口变更两边同时改并更新本文。

## 1. 宿主反代（plugin host 半，lib/index.js）

- 路由：`ROUTE_PATH = '/tavern'`，用
  `ctx.webServer.register({kind:'prefix', path:'/tavern', handler})`。
- 兼容别名：旧前缀 `/story` 仍注册同一个 handler。GET/HEAD 返回 302 到
  `/tavern` 同后缀同 query；其它方法（POST/PUT…，含上传与生成）**不重定向**，
  直接把前缀改写成 `/tavern` 后照常反代，绝不丢 body。
- 每个请求先过登录闸门：`ctx.get('connection', false).requestRejection(req)`；
  返回 403/401 时回纯文本并结束；`undefined` 才放行（与 dsh-ziwei 完全一致）。
- 分流（`/tavern` 前缀剥掉后转发）：
  - `/tavern/api/**`、`/tavern/health/**` → `127.0.0.1:8001`
  - 其余 `/tavern/**`（HTML、/assets、sw.js、favicon…） → `127.0.0.1:8810`
- 转发到 8001 的每个请求附加请求头 `x-dsh-trusted: <secret>`；客户端传入的
  `x-dsh-trusted` 一律先删除，绝不能透传。
- secret 来源：启动时读 `app/.env` 的 `DSH_TRUSTED_SECRET`（允许 env 覆盖
  `DSH_TAVERN_TRUSTED_SECRET`）。读不到只告警，不崩宿主。
- 端口覆盖：`DSH_TAVERN_API_PORT` / `DSH_TAVERN_STATIC_PORT`（隔离测试实例用）。
- 流式：`/tavern/api/**` 含数分钟的聊天 SSE，**不要设响应超时**；静态资源 10s
  超时即可。转发必须用 pipe 保留 SSE、chunked、multipart 上传。
- 错误文案：401/403 沿用 ziwei 的措辞；502/504 用中文友好提示
  （「酒馆应用暂时不可用 / 响应超时」），诊断细节只进宿主日志；日志前缀 `[dsh-tavern]`。

## 2. 应用可信模式（app 半，app/server）

`.env`（节选）：`DSH_TRUSTED_AUTH=1`、`DSH_TRUSTED_USERNAME=frankslin`、
`DSH_TRUSTED_SECRET=<随机 64hex>`、`COOKIE_PATH_PREFIX=/tavern`、
`PRODUCT_PUBLIC_ORIGIN=https://dsh.frankfurtlin.top/tavern`、
`STORAGE_PATH=plugins/dsh-tavern/app/data/private`；`CORS_ORIGINS` 已包含
`http://127.0.0.1:3080`、`https://dsh.frankfurtlin.top`。

- `config.py`：`trusted_auth` / `trusted_username` / `trusted_secret` 三个字段。
- `security.current_user`：trusted 模式开启且 `x-dsh-trusted` 与 secret 常量时间
  比较通过时，解析固定用户（按 username，active）并复用/创建稳定的
  DeviceSession；头部缺失/不匹配时行为与未开启 trusted 完全一致。
- `POST /api/reader/v1/auth/refresh`：trusted 路径直接返回 `token_result(...)`
  给前端 accessToken，不要求 cookie；`POST .../auth/logout` 在 trusted 模式下
  no-op（204）。前端启动逻辑不需要感知插件。
- 固定用户不存在：结构化错误（401/503）+ 明确日志，不 500、不静默建号。
- 所有应用接口（包括 `tavern/**`）都按 `owner_id` 做归属校验，跨用户返回 404。

## 3. 面板 ↔ 内嵌页桥（P2）

同源 iframe：`new URL('tavern/', document.baseURI)`；父页面与 iframe 都用
`window.location.origin` 校验 origin、父页面额外校验 `event.source`。

> **2.0 状态**：应用只保留 `ping/pong/state`（握手与路由上报，位置记忆依赖
> `state.appPath`）。`context-request` 仍会被应答，但没有故事上下文数据源，
> 一律回 `ok:false`（「当前页面没有可用的故事上下文」）——工具条上依赖它的
> 「让 DSH 续写/角色扮演」是 1.x 遗留动作，点击走读取失败分支（不新建空会话）。
> 定义保留用于兼容，P3 再决定是否删除。

父 → iframe：
- `{type:'dsh-tavern:ping'}` → iframe 回 `{type:'dsh-tavern:pong', appPath}`
- `{type:'dsh-tavern:context-request', id, kind:'continue'|'roleplay', target?}`
  → iframe 回
  `{type:'dsh-tavern:context', id, ok:true, context:{
      appPath, worldTitle, lineTitle, mode,
      characters:[{name, identity, speech}],
      activeCharacter, // 最近活跃角色名，可为空（父页面缺省时退化取 characters 末位）
      recentText,   // 最近正文纯文本，截断到 ~4000 字
   }}`
  失败时 `{type:'dsh-tavern:context', id, ok:false, error:'...'}`

iframe → 父（主动，节流）：
- `{type:'dsh-tavern:state', appPath, hasContext:boolean}`

附加消息（契约之外、可选）：
- `{type:'dsh-tavern:theme', theme:'light'|'dark'}`：宿主切主题时推给 iframe。
  应用已固定浅色，当前忽略；宿主保留发送逻辑，删掉也不影响功能。

> 命名空间是 `dsh-tavern:*`（1.x 曾用 `dsh-story:*`），没有第三方消费者。

## 4. UI（client 半，client.js）

- 侧栏 `sidebar.panellist`：`PANEL_ID="tavern"`、label「酒馆」、order 24（落在
  ziwei 20 与 Skill Center 30 之间）；图标带 `data-dsh-panel-entry="tavern"`。
- `main` 座位 keyed 面板 `tavern`：工具条（标题、状态、续写要求输入、刷新、
  新窗口打开，另有 1.x 遗留的「让 DSH 续写 / 角色扮演」）+ iframe
  （`data-dsh-tavern-frame`，`src` 指向 `tavern/`）。
- 位置记忆（宿主侧行为，应用无需配合）：记住最后一次 `state`/`pong` 上报的
  有效站内路径（`hasContext=true` 或 `/worlds/…`），重新挂载面板时把 iframe
  深链到该路径；「刷新」= 清空记忆并回 `/tavern/` 首页（恢复失败时的脱困路径）。
  记忆存模块内存 + sessionStorage（key `dsh-tavern:last-app-path`），不上报服务端。
  验收锚点 `data-dsh-tavern-restored="1|0"`。
- 其它验收锚点：`data-dsh-tavern-view`、`data-dsh-tavern-connected`、
  `data-dsh-tavern-hint`（idle/loading/waiting/ready/busy/sent/error）、
  `data-dsh-tavern-status`、`data-dsh-tavern-extra`；
  `data-dsh-tavern-action="continue|roleplay|reload|open"`。
- 交给 Agent（1.x 遗留链路）：`workspaces.initializeDefault` →
  `uiWorkspace.startSession` → `layout.selectPanel(null)` → 编辑器填充 + 剪贴板兜底。
- 提示词模板（仅遗留动作使用）：
  - 续写：作品/章节/模式/角色/最近正文 + 「请基于以上内容续写下一段（400–600 字，
    保持文风与人物口吻）」+ 用户额外要求。
  - 角色扮演：以 `target`（未指定时用 `activeCharacter`，为空再退化 `characters`
    末位；`@角色名` 可覆盖）第一人称即兴演绎 + 最近情境 + 用户额外要求。

## 5. 文件所有权（避免并发写冲突）

| 范围 | owner |
| --- | --- |
| `app/server/**`（服务端源码与测试） | app-auth |
| `app/client/**` 源码与 dist 构建 | cards-client / app-auth（按任务指派） |
| `package.json`、`cordis.patch.yml`、`lib/**`、`client.js`、`README.md`、`docs/architecture.md`、`docs/contract.md` | plugin-core |
| `scripts/**`、`verify.mjs`、`docs/testing.md` | verifier |
| `/etc/systemd/system`、Caddy、`/root/.dsh/profiles/web`（host 级，不在本仓库） | Lead |
| `app/.env`（含 `DSH_AGENT_*`、`AI_MODE`）、host 重启与上线 | Lead |
| `docs/step1–step5*.md`（历史设计，UI 多已下线） | Lead |

任何人不改别人范围的文件；发现 bug 用 send_message 报给 owner 或 Lead。

## 6. DSH 模型网关 `/tavern-agent/v1/**`

- 链路：应用服务端（`ai_mode='dsh'` 的 `DshAgentAdapter`）→
  `POST /tavern-agent/v1/{complete,stream}`（`Authorization: Bearer <secret>`）→
  插件调 `ctx.llm.stream()`，默认模型取 `ctx.agentDefaultModel.currentSelection()`
  （本机 `opencode-go / deepseek-v4.1-flash`）。
- 端点：`GET /v1/health`、`POST /v1/complete`、`POST /v1/stream`（SSE）。
- 鉴权 secret：`DSH_TAVERN_AGENT_SECRET`（插件侧，回退 `DSH_TRUSTED_SECRET`）
  必须与 `DSH_AGENT_SECRET`（应用侧）一致；只出现在 host 环境文件，不入库、不入日志。
- 失败语义：网关不可用 → `PROVIDER_UNAVAILABLE / 502`「DSH 模型网关不可用，未收到回复」；
  secret 错 → `AGENT_UNAUTHORIZED`；界面显示失败，不落假回复。
- `complete` 响应：`{text, model:{provider,model}, usage:{state,inputTokens,outputTokens}, finishReason}`。
- `/v1/stream` 帧：`data: {"type":"delta","text"}` → `usage` → `finish` → `data: [DONE]`；
  SSE 已开始后失败发 `data: {"type":"error","error":{"code","message"}}` 收尾。

### 6.1 请求字段（`/complete`、`/stream` 共用；除 `messages` 外均可选）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `messages` | `{role:'user', content: string \| ContentBlock[]}[]` | 1–64 条，只支持 `user`；多轮上下文由应用自己拼进 `system` |
| `system` | `string` | 系统提示词（酒馆把整段 prompt 顺序拼在这里） |
| `maxTokens` | 正整数 | 输出上限 |
| `temperature` | `number`，0–2 | 透传 `ctx.llm.stream` |
| `topP` | `number`，0–1 | 透传 `ctx.llm.stream`（`GenerateOptions` 未声明该字段，原样透传，适配器支持后生效）；兼容 snake_case 别名 `top_p` |
| `stop` | `string` 或 `string[]` | 单条字符串归一为数组；最多 4 条、单条 ≤200 字符，映射到 `GenerateOptions.stop` |
| `reasoningEffort` | `string` | 白名单 `off/minimal/low/medium/high/xhigh/max`，默认 `low`；非法值回落 `low` |
| `sessionId` | `string`（≤200） | 透传 provider 会话标识，缺省每次随机 UUID |
| `model` | `{provider, model}` | 覆盖默认模型，两者都非空才生效 |

- 采样参数只在字段出现时校验，`null` 视为不传；未知字段忽略。
- 错误语义：结构/`messages` 问题 → `400 AGENT_BAD_REQUEST`；采样参数类型/越界 →
  `400 INVALID_ARGUMENT`；鉴权缺失/错误 → `401 AGENT_UNAUTHORIZED`；未知路径/方法 →
  `404 AGENT_NOT_FOUND` / `405 AGENT_METHOD_NOT_ALLOWED`；上游失败/超时 →
  `502 AGENT_UPSTREAM_FAILED`（SSE 已开始则用 `error` 帧收尾）。
- `GET /v1/health` 返回 `{ok, model, features:['temperature','topP','stop']}`。
- 透传边界：网关只把参数放进 `ctx.llm.stream` 选项；实际生效取决于 provider 适配器
  （当前 `opencode-go` 的 pi-ai 适配器只消费 `temperature`/`maxTokens`/`sessionId`，
  `stop`/`topP` 会被忽略）。**且生产 DSH Web 尚未重启，采样参数新代码未生效**；
  应用不应假设 `stop` 一定截断输出。

## 7. 应用 API（`/api/reader/v1/tavern/**`，2.0）

鉴权：DSH 可信登录（`x-dsh-trusted`）或应用内会话；所有资源按 `owner_id` 鉴权，
不存在/非本人一律 `404 RESOURCE_NOT_FOUND`。实现见 `app/server/app/tavern/api.py`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/characters` | 卡列表；`q` 名字搜索、`sort=recent\|name\|created`；返回 `chatCount`/`lastChatAt` |
| `POST` | `/characters/imports` | multipart `file`（PNG/JSON）→ `{card}`（201） |
| `GET` | `/characters/{id}` | `{card, prompt, worldInfoCount}` |
| `PATCH` | `/characters/{id}` | 改 name/description/personality/scenario/firstMessage/systemPrompt/postHistoryInstructions/tags/alternateGreetings |
| `DELETE` | `/characters/{id}` | 软删（204） |
| `GET` | `/characters/{id}/avatar` | `image/png` |
| `GET` | `/characters/{id}/export?format=png\|json` | 导出卡（附件文件名在 `Content-Disposition`） |
| `GET` | `/characters/{id}/chats` | 该卡聊天列表（`lastText`/`messageCount`）+ `card` |
| `POST` | `/characters/{id}/chats` | 新建聊天（201）；`greetingId` 选备用开场白；开场白作为第一条 assistant 消息 |
| `GET` | `/chats/{id}` | `{chat, messages, hasMore, latestSequence}`；`before=<sequence>`、`limit=1..500`（默认 200）向后翻页 |
| `PATCH` | `/chats/{id}` | 改标题（1–160） |
| `DELETE` | `/chats/{id}` | 连消息一起删（204） |
| `GET` | `/chats/{id}/export?format=jsonl\|json` | 导出聊天（附件）；其它 format → 422 |
| `POST` | `/chats/{id}/messages` | 发消息 → **SSE**；body `{message}`（兼容 `{text}`，≤4000），可选 `personaId` |
| `POST` | `/chats/{id}/regenerate` | 对最后一条 assistant 重新生成 → **SSE**（追加 swipe） |
| `POST` | `/chats/{id}/messages/{mid}/swipes/{index}/select` | 切版本（下标 0 起），返回 `{message}` |
| `PATCH` | `/chats/{id}/messages/{mid}` | 只能改 user 消息；截断其后消息，返回 `{message, deleted}` |
| `DELETE` | `/chats/{id}/messages/{mid}` | 删除该消息及其后消息（204） |
| `GET/POST` | `/characters/{id}/world-info` | 列表 / 新建（201） |
| `PATCH/DELETE` | `/characters/{id}/world-info/{entryId}` | 改 / 删 |
| `POST` | `/characters/{id}/world-info/preview` | body `{message}` → `{matched, injection}`；卡详情已有预览 UI |
| `GET/PATCH` | `/characters/{id}/prompt` | 读写 `systemPrompt` + `prompt_settings` |
| `POST` | `/characters/{id}/chats/imports` | multipart `file`（ST JSONL）→ 201 `{chat, imported, skipped}` |

字段约束（服务端校验）：卡文本字段 ≤20000、tags ≤20、备用开场白 ≤10；
世界书 `keys` ≤20、`content` ≤8000、`position ∈ before_char|after_char|before_example|at_depth`、
`depth 0–100`、`probability 1–100`、`order 0–10000`；prompt `temperature 0–2`、
`topP 0–1`、`maxTokens 16–8192`、`stop ≤8 条`。

错误码：`GREETING_INVALID`/`SWIPE_INVALID`/`MESSAGE_NOT_EDITABLE`/`MESSAGE_REQUIRED`/
`INVALID_REQUEST`/`CHAT_IMPORT_EMPTY`/`CHAT_IMPORT_TOO_LARGE` → 422；
`NOTHING_TO_REGENERATE` → 409；`RESOURCE_NOT_FOUND` → 404。

## 8. 聊天 SSE 帧与 swipe 语义

### 8.1 帧格式（应用 → 浏览器，`text/event-stream`）

响应头：`Cache-Control: no-store`、`X-Accel-Buffering: no`。每帧形如
`event: {type}\ndata: {json}\n\n`：`event:` 名恒等于 payload 的 `type`
（`message` / `delta` / `done` / `error`），客户端按命名事件派发：

| `type` | 时机 | 字段 |
| --- | --- | --- |
| `message` | 首帧（发消息） | `userMessage`（新 user 消息对象）、`assistantSequence` |
| `message` | 首帧（重新生成） | `messageId`、`activeSwipe`（目标消息当前版本） |
| `delta` | 每个增量 | `text` |
| `done` | 生成成功并落库 | `message`（完整 assistant 消息，含 `swipes`/`activeSwipe`） |
| `error` | 失败 | `code`、`message` |

`error` 码：`GENERATION_CANCELLED`、`PROVIDER_UNAVAILABLE`、`REPLY_EMPTY`、
`PERSISTENCE_UNAVAILABLE`、`RESOURCE_NOT_FOUND`。HTTP 状态此时已是 200，
错误只能走帧；客户端断开时服务端取消生成且**不落库**。

> **已对齐（2026-11-05）**：服务端 `stream.sse()` 现在同时输出 `event: {type}` 与
> `data: {json}`，与客户端 `sse.ts` 的命名事件派发一致；逐字流式已可用。修复后
> `verify.mjs` C 组（250ms 采样断言文本增长）通过。

### 8.2 swipe（多版本）

- 每条 assistant 消息：`swipes: [{content, model, createdAt}]`、`activeSwipe` 下标；
  接口输出的 `text` 恒等于 `swipes[activeSwipe].content`；
- 落库前对最终文本应用账号级 `world.regex_scripts` 输出替换（仅 `enabled`，按
  `order` 逐条 `re.sub`；非法正则记 warning 跳过，不影响回复）；SSE `delta` 仍为
  模型原文，刷新/读库看到替换结果。
- 「重新生成」只作用于最后一条 assistant：以目标之前的历史重新生成，**追加**一个
  swipe 并把 `activeSwipe` 指到新版本，旧版本保留；
- 切换版本 `POST .../swipes/{index}/select` 只改 `activeSwipe`，不生成、不删版本；
- 编辑 user 消息会删除其后所有消息（返回 `deleted` 计数）；删除消息同样截断其后
  消息——语义是「从这里重来」；
- 停止/失败不写消息；编辑/删除以 `sequence` 为界，`UNIQUE(chat_id, sequence)` 保证顺序。

## 9. 导出 / 导入 / 分页（P1，已实现）

- `GET /chats/{id}/export?format=jsonl|json`：JSONL 每行 ST 风格
  `{user_name, character_name, create_date, mes, is_user, is_name, swipes?, swipe_id?}`；
  JSON 形状 `{chat, card, messages}`；其它 format → 422。
- `POST /characters/{id}/chats/imports`（multipart `file`）：≤20000 行、≤32 MiB；
  坏行跳过计数；空/全坏 → `422 CHAT_IMPORT_EMPTY`，超限 → `422 CHAT_IMPORT_TOO_LARGE`；
  成功 201 `{chat, imported, skipped}`；导入后聊天自动进入会话。
- `GET /chats/{id}?before=<sequence>&limit=<1..500>`（默认 200）：返回
  `{chat, messages, hasMore, latestSequence}`；`before` 为空即取最新一页，
  向上翻页把当前最早 `sequence` 传给 `before`。
- 客户端：聊天页「导出聊天 (JSONL/JSON)」菜单 + 顶部「加载更早」；角色页
  「导入聊天记录」上传 JSONL。

## 10. 预设 / 提示词组装 / 采样（P2，已实现）

预设是用户级的生成配置，聊天可绑定一条预设；未绑定时使用内置「默认」预设
（懒创建，`isDefault=true`，不可删除）。非空预设字段覆盖卡片/聊天设置。

表 `world.tavern_presets`：`name`、`is_default`、`system`、`main`、
`jailbreak`、`post_history`、`temperature`、`top_p`、`max_tokens`、
`stop`（JSON 数组，≤4 条、每条 ≤200 字）；`world.tavern_chats.preset_id`
可空、`world.tavern_chats.sampling`（JSONB 可空，形如
`{"temperature":0.8,"topP":0.95,"maxTokens":1024,"stop":[]}`）。
P2 新表/列由 `app.world.migrate` 幂等创建（`create_all` +
`ADD COLUMN IF NOT EXISTS`，可重复执行），生产部署需先跑迁移再重启
`dsh-tavern-api`。

API：

- `GET /api/reader/v1/tavern/presets` → `{items: PresetOut[]}`（默认预设排最前）。
  `PresetOut`：`{id,name,isDefault,system,main,jailbreak,postHistory,temperature,
  topP,maxTokens,stop,createdAt,updatedAt}`。
- `POST /tavern/presets` → 201 `{preset}`；`name` 1–80 必填；`temperature` 0–2、
  `topP` 0–1、`maxTokens` 16–8192；`stop` 超限 → 422 `PRESET_INVALID`。
- `PATCH /tavern/presets/{id}` → `{preset}`；空 body 422；删除默认预设 →
  409 `PRESET_IMMUTABLE`；`DELETE /tavern/presets/{id}` → 204（解绑所有引用会话）。
- `PATCH /tavern/chats/{id}` 支持 `title`、`presetId`（`null` 清空 → 回退默认
  预设）与 `sampling`（`temperature`/`topP`/`maxTokens`/`stop`，兼容
  `top_p`/`max_tokens`；非法值 422；`null` 清空）。`GET /tavern/chats/{id}`
  与所有 chat 响应都带 `sampling`（无值时为 `{}`）。
- `PATCH /tavern/presets/{id}` 支持 `isDefault: true`：切换唯一默认预设（原
  默认自动取消）；内置「默认」预设不可删除（409 `PRESET_IMMUTABLE`）。

提示词按 SillyTavern 顺序组装，`GET /tavern/characters/{id}/prompt?chatId=`
返回全文与分段（`chatId` 可选，不属于该卡 → 404）：

1. `[系统提示词]` → `[主提示词]` → `[世界书 · 角色设定之前]` →
2. `[角色设定]`（姓名/描述/性格/场景）→ `[你的角色]`（人设）→
3. `[示例对话]` → `[世界书 · 示例对话之前]` → `[世界书 · 角色设定之后]` →
4. `[对话历史]`（`at_depth` 条目按 `depth` 注入在倒数第 N 条之前，历史按
   字符预算从旧到新裁剪，超长单条保留最新尾部）→
5. `[后置指令]` → `[越狱提示]` → `[本轮要求]`。

响应：`{prompt, presetId, presetName, system, main, jailbreakPrompt, postHistory,
worldInfoPosition, systemPrompt（组装后全文）, sections/segments:[{title,chars}],
matched:[{id,keys,position,depth,order,reason,content}], worldInjection}`。
`PATCH /tavern/characters/{id}/prompt` 接受 `{system?, main?, jailbreakPrompt?,
postHistory?, worldInfoPosition?}`（同时兼容 `systemPrompt`/`mainPrompt` 与
`system_prompt`/`main_prompt` 等 snake_case），落 `character_cards.prompt_settings`。

世界书语义：`position` 取 `before_char` / `after_char` / `before_example` /
`at_depth`；`order` 决定同段内顺序；`constant` 恒命中；否则 `enabled` 且
`keys`（大小写不敏感子串）命中「本条消息 + 最近 4 条用户消息」；`probability`
<100 时按 `sha256(entryId + "|" + scanText) % 100 < probability` 决定当轮是否
命中（可复算，预览与生成结果一致）。`at_depth` 用 `depth` 指定注入深度。
`POST /tavern/characters/{id}/world-info/preview`（body 可带 `message`/`chatId`）
与生成共用同一实现，返回 `{matched（每条带 reason：`constant` 或
`keyword:<词>`）, reasons, injection, worldInfoPosition, sections, systemPrompt}`。

采样参数：卡片 prompt 设置（`temperature`/`topP`/`maxTokens`/`stop`）与绑定预设
预设的并发/幂等保证（2026-11-05 修复）：每个 owner 至多一条内置预设
（`builtin=true`，由 `(owner_id) WHERE builtin` 唯一索引保证）且至多一条
`is_default=true`（`(owner_id) WHERE is_default` 唯一索引）。内置「默认」由
`default_preset` 在 advisory lock 内懒创建，不会重复；`GET /tavern/presets`
的每项包含 `builtin` 标记。`app.world.migrate` 幂等清理历史重复「默认」行
（保留最早一条，若其中有 default 则保留该条），并把指向重复行的 chat
`preset_id` 重指到保留行；删除内置预设返回 409 `PRESET_IMMUTABLE`。

`chat.sampling` > 预设 > 卡片设置合并后传给适配器（`temperature`、`top_p`、
`stop` 随 `/complete` 与 `/stream` 请求体发送；`maxTokens` 取
`chat.sampling.maxTokens`/预设 `max_tokens`/卡片设置与模型策略上限的较小值）。注意：生产
DSH 网关未重启到支持采样参数的版本前，网关侧会忽略部分参数，属于预期。

## 11. 文档索引

| 文档 | 状态 |
| --- | --- |
| `docs/step6-native-tavern.md` | **当前设计**：酒馆 2.0 原生聊天/SSE/swipe/世界书/验收分组 |
| `docs/architecture.md` | 当前实现架构（host 反代、网关、应用数据模型、prompt 组装、风险） |
| `docs/testing.md` | 验收方法（静态 + 隔离实例 E2E） |
| `docs/step1-character-card-chat.md` | 历史：角色卡聊天第一步 |
| `docs/step2-dsh-generation.md` | 历史：DSH 真生成网关（字段仍在用，见 §6） |
| `docs/step3-card-space.md` | 历史：卡片空间（世界书/预设/正则），2.0 世界书改为 `tavern_world_info` |
| `docs/step3b-group-chat.md` | 历史：群聊（UI 与服务端均已删除） |
| `docs/step4-companion-memory.md` | 历史：长期记忆/伙伴（UI 已下线，代码待 P3 清） |
| `docs/step5-p0-p1-p2.md` | 历史：故事模式 P0–P2 体验设计 |

旧文档只作参考，**有冲突以本文与 `docs/step6-native-tavern.md` 为准**。
