# dsh-tavern 集成契约（v3：酒馆 2.0 原生酒馆）

目标：把本地「酒馆」（Tavern）应用装成 DSH 插件，放在
`/root/workspace/plugins/dsh-tavern`，在 DSH Web 里以内嵌面板访问，登录只走 DSH
闸门。**2.0 起产品改为单用户原生酒馆**：角色卡网格 → 点卡进全屏聊天（单聊）或
在卡库多选 2–5 张发起**群聊**，SSE 流式，重新生成追加版本（swipe），世界书按关键词
注入 prompt。**P3 已把旧 story 域整体删除**：故事模式 / 探索 / 世界 / 分支 / 记忆 /
伙伴 / 卡↔世界 / AI 写卡的 UI 与服务端代码都已移除（`/worlds`、`/world-chats`、
`/books`、`/play-controls` 等旧 API 一律 404），旧表已 DROP（备份
`/root/workspace/.backups/dsh-tavern/story_between-pre-p3-20261105-081109.dump`）。
服务端现状：`from app.main import app` 共 **86 条路由**（`/api/reader/v1/tavern/**`
36 条），见 §7。

应用源码在 `plugins/dsh-tavern/app`，host 侧 systemd 单元：

- `dsh-tavern-api`  → uvicorn，127.0.0.1:8001（聊天 SSE 在请求线程直连网关）
- `dsh-tavern-web`  → 静态 dist，127.0.0.1:8810
- `dsh-tavern-worker` → 旧任务队列 worker；2.0 生成在请求线程跑 SSE，host 已停用并 disable（新 worker.py 无 main 循环，勿重启）
- `dsh-tavern-world-worker` → 旧 story worker；**P3 删掉 `app.world.worker` 后该单元已停用、disable（host 已处理，勿重启）**
  重启会失败**，属 host 待清理项（停用或改指向由 Lead 处理）

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

> **2.0 状态（P3 起）**：桥协议保留 `ping/pong/state` 与 `context-request/context`，
> 但上下文语义已改为原生酒馆：聊天页 `/chats/:id` 回当前角色卡与最近对话；
> 其它页面（角色卡库 `/`、卡详情、设置）回 `ok:false`，错误文案
> 「当前页面没有可用的聊天上下文」。工具条的「让 DSH 续写 / 角色扮演」据此
> 在聊天页可用、其它页面禁用（见 §4）。旧故事字段（`worldTitle`/`lineTitle`/
> `characters`/`recentText`）不再由应用提供；父页面只做兼容兜底，不依赖它们。

父 → iframe：
- `{type:'dsh-tavern:ping'}` → iframe 回 `{type:'dsh-tavern:pong', appPath}`
- `{type:'dsh-tavern:context-request', id, kind:'continue'|'roleplay', target?}`
  → iframe 回
  `{type:'dsh-tavern:context', id, ok:true, context:{
      appPath,      // 当前应用路由，如 /chats/42
      chatId,       // 当前聊天 id（字符串）
      cardId,       // 当前聊天角色卡 id；群聊的 chat.cardId 为 null（前端据此显示成员名）
      characterName,        // 角色名（必填；父页面用它组 prompt 与按钮可用性）
      characterDescription, // 角色简介，可空
      personality,          // 性格，可空
      scenario,             // 场景，可空
      firstMessage,         // 开场白，可空
      recentMessages:[{role:'user'|'assistant', text}], // 最近若干条，建议 ≤20 条
   }}`
  失败时 `{type:'dsh-tavern:context', id, ok:false, error:'当前页面没有可用的聊天上下文'}`
  （父页面对缺字段一律容错：缺角色名视为「无上下文」，缺设定/对话就少写一段，
  不报错、不崩面板。）

iframe → 父（主动，节流）：
- `{type:'dsh-tavern:state', appPath, hasContext:boolean}`

附加消息（契约之外、可选）：
- `{type:'dsh-tavern:theme', theme:'light'|'dark'}`：宿主切主题时推给 iframe。
  应用已固定浅色，当前忽略；宿主保留发送逻辑，删掉也不影响功能。

> 命名空间是 `dsh-tavern:*`（1.x 曾用 `dsh-story:*`），没有第三方消费者。

## 4. UI（client 半，client.js）

- 侧栏 `sidebar.panellist`：`PANEL_ID="tavern"`、label「酒馆」、order 24（落在
  ziwei 20 与 Skill Center 30 之间）；图标带 `data-dsh-panel-entry="tavern"`。
- `main` 座位 keyed 面板 `tavern`：工具条（标题、状态、额外要求输入、刷新、
  新窗口打开、「让 DSH 续写」「角色扮演」）+ iframe
  （`data-dsh-tavern-frame`，`src` 指向 `tavern/`）。
- 工具条语义（原生酒馆）：`state.hasContext` 为真时两个动作可用，否则禁用并
  提示「先打开一个聊天」（回车等入口也有同样的兜底）；两者都走
  `context-request` → 组 prompt → 新开 DSH 会话（`startSession`）→
  `selectPanel(null)` → 编辑器填充 + 剪贴板兜底。
- 位置记忆（宿主侧行为，应用无需配合）：**只记聊天页** `/chats/:id`
  （卡库 `/`、卡详情、设置页不记），重新挂载面板时把 iframe 深链回该聊天；
  「刷新」= 清空记忆并回 `/tavern/` 首页（恢复失败时的脱困路径）。
  记忆存模块内存 + sessionStorage（key `dsh-tavern:last-app-path`），不上报服务端。
  验收锚点 `data-dsh-tavern-restored="1|0"`。
- 其它验收锚点：`data-dsh-tavern-view`、`data-dsh-tavern-connected`、
  `data-dsh-tavern-hint`（idle/loading/waiting/ready/busy/sent/error）、
  `data-dsh-tavern-status`、`data-dsh-tavern-extra`；
  `data-dsh-tavern-action="continue|roleplay|reload|open"`。
- 交给 Agent 的链路：`workspaces.initializeDefault` → `uiWorkspace.startSession` →
  `layout.selectPanel(null)` → 编辑器填充 + 剪贴板兜底。
- 提示词模板（`<name>` = `context.characterName`；`@角色名` 只对角色扮演生效，
  且会从额外要求里剥掉）：
  - 续写：首句固定「请以<name>的口吻继续写下一段回复（第一人称，符合人设与场景；
    不要替读者说话）。」+【角色】设定（简介/性格/场景/开场白，有则写）+
    【最近对话】（`recentMessages` 逐条，`user` 记作「读者」、`assistant` 记作角色名）
    + 可选【我的额外要求】。
  - 角色扮演：首句固定「请以<name>的身份扮演，针对读者最后一条消息即兴演绎一段
    回复。」+ 同样的角色设定与最近对话 + 可选【我的额外要求】。

## 5. 文件所有权（避免并发写冲突）

| 范围 | owner |
| --- | --- |
| `app/server/**`（服务端源码与测试） | app-auth |
| `app/client/**` 源码与 dist 构建 | cards-client / app-auth（按任务指派） |
| `package.json`、`cordis.patch.yml`、`lib/**`、`client.js`、`README.md`、`docs/architecture.md`、`docs/contract.md` | plugin-core |
| `scripts/**`、`verify.mjs`、`docs/testing.md` | verifier |
| `/etc/systemd/system`、Caddy、`/root/.dsh/profiles/web`（host 级，不在本仓库） | Lead |
| `app/.env`（含 `DSH_AGENT_*`、`AI_MODE`）、host 重启与上线 | Lead |
| `docs/step1–step5*.md`（历史设计，UI 与服务端多已删除） | Lead |

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
| `GET` | `/characters` | 卡列表；`q` 名字搜索、`sort=recent\|name\|created`；返回 `chatCount`/`lastChatAt`；首次读取时按 owner 自动种子 5 张内置预设卡 |
| `POST` | `/characters/imports` | multipart `file`（PNG/JSON）→ `{card}`（201） |
| `GET` | `/characters/{id}` | `{card, prompt, worldInfoCount}` |
| `PATCH` | `/characters/{id}` | 改 name/description/personality/scenario/firstMessage/systemPrompt/postHistoryInstructions/tags/alternateGreetings |
| `DELETE` | `/characters/{id}` | 软删（204） |
| `GET` | `/characters/{id}/avatar` | `image/png` |
| `GET` | `/characters/{id}/export?format=png\|json` | 导出卡（附件文件名在 `Content-Disposition`） |
| `GET` | `/characters/{id}/chats` | 该卡聊天列表（`lastText`/`messageCount`）+ `card` |
| `POST` | `/characters/{id}/chats` | 新建**单聊**（201）；`greetingId` 选备用开场白；开场白作为第一条 assistant 消息 |
| `POST` | `/chats` | 新建**群聊**（201）；body `{cardIds:[2..5], title?}`，也接受 `{cardId}` 走单聊；群聊 `mode='group'`、`members=[{cardId,name,order}]` |
| `GET` | `/chats` | 全部聊天历史列表（`limit` 1–100 默认 20、`offset`），按 `lastMessageAt` 倒序；`q` 可选（trim 后 1–100 字符）按聊天标题或**当前版本消息正文**搜索，`%`/`_`/`\` 按字面匹配；超长 422 `CHAT_SEARCH_INVALID`；每项带 `mode`/`members`/`lastMessage`/`messageCount`（群聊标题/成员名由 `members` 拼） |
| `GET` | `/chats/{id}` | `{chat, messages, hasMore, latestSequence}`；`before=<sequence>`、`limit=1..500`（默认 200）向后翻页 |
| `PATCH` | `/chats/{id}` | 改 `title`（1–160）/ `presetId`（null 清空）/ `sampling`（见 §10）/ 群聊 `cardIds`（2–5，去重保序，替换成员）；返回与 `GET /chats/{id}` 相同的 `chat`；空 body 422 |
| `DELETE` | `/chats/{id}` | 连消息一起删（204） |
| `GET` | `/chats/{id}/export?format=jsonl\|json` | 导出聊天（附件）；群聊 JSONL 每行带 `speaker_name`；其它 format → 422 |
| `POST` | `/chats/{id}/messages` | 发消息 → **SSE**；body `{message}`（兼容 `{text}`，≤4000），可选 `personaId`、可选引用回复 `quote:{text}`（trim 后 1–2000，空/超长 422 `QUOTE_INVALID`）；群聊首帧带本轮 `speaker`；首帧 `userMessage.quotedText` 回显引用 |
| `POST` | `/chats/{id}/regenerate` | 对最后一条 assistant 重新生成 → **SSE**（追加 swipe；群聊沿用该条 `speaker_card_id`） |
| `POST` | `/chats/{id}/messages/{mid}/swipes/{index}/select` | 切版本（下标 0 起），返回 `{message}` |
| `PATCH` | `/chats/{id}/messages/{mid}` | 只能改 user 消息；截断其后消息，返回 `{message, deleted}` |
| `DELETE` | `/chats/{id}/messages/{mid}` | 删除该消息及其后消息（204） |
| `GET/POST` | `/characters/{id}/world-info` | 列表 / 新建（201） |
| `PATCH/DELETE` | `/characters/{id}/world-info/{entryId}` | 改 / 删 |
| `POST` | `/characters/{id}/world-info/preview` | body `{message}` → `{matched, injection}`；卡详情已有预览 UI |
| `GET/PATCH` | `/characters/{id}/prompt` | 读写 `systemPrompt` + `prompt_settings` |
| `POST` | `/characters/{id}/chats/imports` | multipart `file`（ST JSONL）→ 201 `{chat, imported, skipped}` |

群聊语义：`mode='single'|'group'`；`members` 是有序的 `[{cardId,name,avatarPath,order}]`
（2–5 张，创建时按选择顺序），assistant 消息记 `speaker_card_id`；本轮发言人是
`alive_members[已保存 assistant 条数 % alive 成员数]`（轮转，只从存活成员中选），
重新生成沿用原消息的发言人（原发言人已删则回落到存活列表轮转并更新归属）；
`PATCH /chats/{id}` 传 `cardIds` 可中途替换成员（refresh `members` 快照，不动历史
消息；单聊传 `cardIds` → 422 `CHAT_MODE_INVALID`，数量不在 2–5 → 422
`GROUP_MEMBERS_INVALID`，卡不存在/已软删/跨 owner → 404）；成员变更后下一次轮转
按新列表继续（`assistant_count % alive_len`）。成员卡被软删不再让群聊 404：详情/
列表/导出仍可用，`chat.members[*].deleted=true`、`aliveMemberCount` 给出存活数，
`cards` 只含存活卡；0 存活时发消息/重新生成 → 409 `CHAT_NO_MEMBERS`。
群聊 JSONL 导入未支持（导入接口只建单聊）。

内置预设卡（4.0 打磨）：服务端随包携带 5 张 CC BY 4.0 角色卡（玄净/裴聿/奈芙尔/
沈砚/唐团团，资产在 `app/server/app/cards/presets/`，来源与修改见其 `MANIFEST.json`）。
每个 owner 首次读取卡库（`GET /characters` 或旧 `/character-cards`）时惰性种子：
按 `character_cards.builtin_slug` 幂等补齐（advisory lock + 部分唯一索引
`uq_character_cards_builtin_slug`），头像/开场白/世界书与手动导入一致；已存在
（**含已软删**）的 slug 跳过，因此用户删除预设卡后不会被重灌；资产缺失或单张解析
失败只记 `preset_card_seed_failed` 日志并跳过，不阻断列表。卡片对象新增
`builtin: bool` 与 `builtinSlug: string|null`（用户导入卡为 `false`/`null`；
`GET /characters` 列表项与卡片详情均返回该字段）。

旧卡片空间 API 仍挂载但 2.0 UI 不再使用：`/character-cards/**`（15 条，含
`lore`/`context-preview`/`companion`）、`/presets`、`/regex-scripts`（设置页仍用）、
`/tavern-personas`（设置页人设仍用）、`/api/world/v1/config`。数据表
`world.character_cards`/`card_lore`/`chat_presets`/`regex_scripts` 保留。

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
| `message` | 首帧（发消息） | `userMessage`（新 user 消息对象）、`assistantSequence`；群聊另有 `speaker:{cardId,name}`（本轮发言人） |
| `message` | 首帧（重新生成） | `messageId`、`activeSwipe`（目标消息当前版本）；群聊另有 `speaker` |
| `delta` | 每个增量 | `text` |
| `done` | 生成成功并落库 | `message`（完整 assistant 消息，含 `swipes`/`activeSwipe`；群聊含 `speakerCardId`/`speakerName`） |
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
  `{user_name, character_name, create_date, mes, is_user, is_name, swipes?, swipe_id?}`，
  群聊 assistant 行另有 `speaker_name`（`character_name` 也取该发言人）；JSON 形状
  `{chat, card, cards, messages}`；其它 format → 422。
- `POST /characters/{id}/chats/imports`（multipart `file`）：≤20000 行、≤32 MiB；
  坏行跳过计数；空/全坏 → `422 CHAT_IMPORT_EMPTY`，超限 → `422 CHAT_IMPORT_TOO_LARGE`；
  成功 201 `{chat, imported, skipped}`；导入后聊天自动进入会话。
- `GET /chats/{id}?before=<sequence>&limit=<1..500>`（默认 200）：返回
  `{chat, messages, hasMore, latestSequence}`；`before` 为空即取最新一页，
  向上翻页把当前最早 `sequence` 传给 `before`。
- **引用回复（P0-B，已实现）**：`POST /chats/{id}/messages` 可带
  `{"quote": {"text": "..."}}`（也接受扁平 `{"quotedText": "..."}`，`quote` 优先）；
  文本 trim 后 1–2000 字符，否则 `422 QUOTE_INVALID`。引用片段复制保存到用户消息的
  `quoted_text`（源消息删除/编辑不影响），assistant 消息恒为 null；`GET /chats/{id}`
  的每条 message 与 SSE 首帧用户回执都带 `quotedText`（可为 null）。重新生成不会改动
  上一条用户消息的引用。prompt 中该用户回合前会插入
  `[引用] <quoted_text 截断到 500 字>` 块（群聊同样生效）。
- **导入只建单聊**：接口挂在 `/characters/{id}/chats/imports` 下，按 ST JSONL 逐行
  解析成 user/assistant 消息；群聊导出文件可以下载，但暂不支持重新导入成群聊
  （导入后会是一条单聊，`speaker_name` 被忽略）。
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
  预设）、`sampling`（`temperature`/`topP`/`maxTokens`/`stop`，兼容
  `top_p`/`max_tokens`；非法值 422；`null` 清空）与群聊 `cardIds`
  （`string[]`，去重保序，2–5 张，替换 `members` 快照并刷新 name/avatarPath/order；
  单聊 → 422 `CHAT_MODE_INVALID`，数量非法 → 422 `GROUP_MEMBERS_INVALID`，
  已删/跨 owner 卡 → 404）。`GET /tavern/chats/{id}` 与所有 chat 响应都带
  `sampling`（无值时为 `{}`）。
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
| `docs/step3b-group-chat.md` | 历史：旧群聊设计；2.0 已用原生实现取代（见 §7 群聊语义） |
| `docs/step4-companion-memory.md` | 历史：长期记忆/伙伴（P3 已随 story 域删除） |
| `docs/step5-p0-p1-p2.md` | 历史：故事模式 P0–P2 体验设计 |

旧文档只作参考，**有冲突以本文与 `docs/step6-native-tavern.md` 为准**。
