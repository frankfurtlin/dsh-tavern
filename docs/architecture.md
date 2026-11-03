# dsh-story-between 架构

## 一句话

插件由**两个半边**组成：`lib/index.js`（host 半）在 DSH Web 里注册
`prefix /story` 反向代理，把本地写作应用挂到 DSH 登录态后面；`client.js`
（client 半）在浏览器里注册侧栏入口与主面板，用同源 iframe 展示应用，
并用 `postMessage`（P2 桥）把当前故事上下文交给 DSH Agent 续写 / 角色扮演。

```
┌────────────────────────── DSH Web (127.0.0.1:3080) ──────────────────────────┐
│  client.js（浏览器）                                                          │
│   ├─ slots.register('sidebar.panellist', id=story, order=24)                 │
│   └─ slots.register('main', key=story)                                       │
│        └─ 工具条 + <iframe src="/story/">  ←── P2 postMessage ──┐             │
│                                                                │             │
│  lib/index.js（host）                                          │             │
│   ├─ ctx.get('connection').requestRejection(req)  ← 登录闸门   │             │
│   └─ ctx.webServer.register({kind:'prefix', path:'/story'})    │             │
└───────────────┬────────────────────────────────────────────────┘             │
                │ 剥掉 /story 前缀                                             │
     ┌──────────┴───────────┐                                                   │
     ▼                      ▼                                                   │
 /api/** /health/**      其余 /assets、HTML、sw.js                              │
     │                      │                                                   │
     ▼                      ▼                                                   │
 127.0.0.1:8001        127.0.0.1:8810                                           │
 uvicorn(FastAPI)      app/dist 静态站                                          │
     ▲                                                                          │
     └── 附加 x-dsh-trusted: <secret>（应用侧校验可信登录身份）──────────────────┘
```

## 请求链路与鉴权

### 闸门

每个进入 `/story` 的请求先调用宿主的
`ctx.get('connection', false)?.requestRejection(req)`：

| 返回值 | 含义 | 插件行为 |
| --- | --- | --- |
| `undefined` | Host/Origin 在信任围栏内且已登录 | 继续转发 |
| `401` | 未通过浏览器登录（无 token/cookie） | 返回 401 纯文本，沿用 DSH 措辞 |
| `403` | Host/Origin 不在信任列表 | 返回 403 纯文本，沿用 DSH 措辞 |

这样内嵌面板天然继承 DSH 的登录态，插件不另做一套认证；`/story`
在公网上也只能经 Caddy → DSH 网关访问，端口本身只监听回环地址。

### 分流（先剥前缀，再转发）

| 浏览器请求 | 上游 | 超时 | 额外头 |
| --- | --- | --- | --- |
| `/story/api/**` | `127.0.0.1:8001` | **不设**（生成/续写可能数分钟） | `x-dsh-trusted: <secret>` |
| `/story/health/**` | `127.0.0.1:8001` | **不设** | 同上 |
| `/story/`、`/story/assets/**`、`/story/sw.js`… | `127.0.0.1:8810` | 10s | 无 |
| `/story`（无尾斜杠） | `127.0.0.1:8810` 的 `/` | 10s | 无 |

上游路径是「剥掉 `/story` 之后」的路径：应用 dist 已按 `base=/story` 构建，
页面里引用 `/story/assets/...`，静态服务器本身不认识 `story` 段，所以必须在
插件里剥掉。`/story/api/...` 到 uvicorn 时同样只剩 `/api/...`。

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
  - 上游连不上 → `502 故事之间应用暂时不可用，请稍后重试。`
  - 静态上游超时 → `504 故事之间应用响应超时，请稍后重试。`
- 客户端断开（`req` aborted / `res` close）时销毁上游请求，避免泄漏。

## 前端面板结构（client 半）

### 座位注册

| 座位 | 注册项 | 说明 |
| --- | --- | --- |
| `sidebar.panellist` | `{ id: 'story', order: 24, label: () => '故事之间' }` | 侧栏一行；图标是手写 SVG（`data-dsh-panel-entry="story"`） |
| `main` | `{ key: 'story', inject: () => ({ askDsh }) }` | 主面板；点击侧栏行后由宿主按 key 找到组件 |

`order=24` 落在紫微斗数（20）与 Skill Center（30）之间。

### 主面板

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 故事之间  当前：/worlds/w1/play    [续写要求 / @角色名] [让 DSH 续写] [角色扮演] [刷新] [新窗口打开] │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         <iframe src="/story/"> 应用本体                      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

iframe 地址是 `new URL('story/', document.baseURI)`：与宿主同源，cookie /
登录态自然共享，也方便 P2 用 `window.location.origin` 做目标校验。
若上次在应用里打开过正文页（应用上报过有效 `appPath`），重新挂载面板时
iframe 会恢复到那里，见下文「位置记忆」。

工具条上的动作：

| 动作 | `data` 锚点 | 行为 |
| --- | --- | --- |
| 让 DSH 续写 | `data-dsh-story-action="continue"` | 读上下文 → 组续写 prompt → 新开 DSH 会话 |
| 角色扮演 | `data-dsh-story-action="roleplay"` | 读上下文 → 选角色 → 组角色扮演 prompt |
| 刷新 | `data-dsh-story-action="reload"` | 清掉位置记忆并回 `/story/` 应用首页重新加载（恢复失败时的兜底） |
| 新窗口打开 | `data-dsh-story-action="open"` | 新标签页打开 `/story/` |
| 额外要求 | `data-dsh-story-extra` | 输入框；回车等同「让 DSH 续写」 |

给自动化验收的稳定锚点：

| 属性 | 值 |
| --- | --- |
| `data-dsh-story-view` | 面板根节点（存在即面板已挂载） |
| `data-dsh-story-connected` | `"1"` / `"0"`：是否收到 pong/state |
| `data-dsh-story-restored` | `"1"` / `"0"`：本次挂载是否从位置记忆恢复了 iframe 地址 |
| `data-dsh-story-hint` | `idle` / `loading` / `waiting` / `ready` / `busy` / `sent` / `error` |
| `data-dsh-story-status` | 工具条状态文本（人话，不保证稳定，断言优先用 hint） |
| `data-dsh-story-frame` | iframe |

状态提示优先级：动作反馈（notice）> 加载中 > 等待应用 > 未打开正文 >
已连接并显示当前页面。`hint` 是按同一状态压缩出的稳定 token。

### 位置记忆（切面板不丢上下文）

DSH 切到别的面板会卸载 main 座位，iframe 一并销毁；应用重新加载后默认停在
`/explore`，故事上下文就丢了。插件侧做一层「最后一次有效应用路由」记忆：

- 应用通过 `dsh-story:state` 上报 `appPath` 且 `hasContext=true`（或 path 落在
  `/worlds/` 下，`pong` 也认）时，把 `appPath` 记进模块作用域，并镜像到
  `sessionStorage`（key `dsh-story:last-app-path`）；
- 面板挂载时 iframe `src = 'story/' + appPath`（去掉前导 `/`），没有记忆才用
  `story/`；`/` 首页不记；
- 恢复期间（新文档 `onLoad` 之前）不覆盖记忆，避免 iframe 导航与 postMessage
  路由互相打架；`onLoad` 之后应用上报的路由才是权威值；
- 恢复的地址失效（应用回落到 `/explore` 或报错页）不会卡死面板：点「刷新」
  清空记忆并回应用首页即可；插件本身不因恢复失败抛错；
- 新增锚点 `data-dsh-story-restored` 便于验收区分「恢复」与「回首页」。

实现注意：`appPath` 是应用路由路径（如 `/worlds/w1/play`），**不含** `/story`
base —— 应用用 `createWebHistory('/story')`，`router.currentRoute.value.path`
天然去掉 base；拼 URL 时只加一次 `/story/`。

### 主题

宿主切浅色/深色时，`client.js` 观察 `document.documentElement` 的
`color-scheme` 变化，给 iframe 发附加消息 `{type:'dsh-story:theme', theme}`
（契约之外，应用可选实现）；iframe onLoad 时也会补发一次当前主题。

## P2 postMessage 协议（契约 §3）

同源通信，双方向都按 `event.origin === window.location.origin` 校验；
父页面还会校验 `event.source === iframe.contentWindow`。

父 → iframe：

| 消息 | 字段 | 说明 |
| --- | --- | --- |
| `dsh-story:ping` | — | 握手；应用回 pong |
| `dsh-story:context-request` | `id`、`kind: 'continue' \| 'roleplay'`、可空 `target` | 请求当前故事上下文；`id` 用于关联响应 |

iframe → 父：

| 消息 | 字段 | 说明 |
| --- | --- | --- |
| `dsh-story:pong` | `appPath` | 回应 ping，父页面标记已连接 |
| `dsh-story:state` | `appPath`、`hasContext` | 应用主动上报状态（建议页面就绪时发一次） |
| `dsh-story:context` | `id`、`ok: true`、`context{...}` 或 `ok:false`、`error` | 上下文响应 |

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

## 配置与运维

| 配置 | 位置 | 作用 |
| --- | --- | --- |
| `DSH_TRUSTED_SECRET` | `app/.env` | 应用侧校验可信头的 secret；插件启动时读入 |
| `DSH_STORY_TRUSTED_SECRET` | 宿主环境 | 覆盖上面的 secret（隔离测试/应急） |
| `DSH_STORY_API_PORT` | 宿主环境 | 覆盖 API 上游端口（默认 8001） |
| `DSH_STORY_STATIC_PORT` | 宿主环境 | 覆盖静态上游端口（默认 8810） |

secret 读不到时：打印告警、继续转发但**不带**可信头，应用侧按未登录处理；
不要让宿主崩溃。日志前缀统一 `[dsh-story-between]`。

改完代码：

- 只改 `client.js`：重启 DSH Web（或按宿主机制热载）后刷新页面；
- 改 `lib/index.js`：必须重启 DSH Web 进程（host 半在启动时注册路由、读 secret）；
- 改应用/改 `.env`：按 `app/` 的部署方式重启对应 `dsh-story-between-*` 服务，
  插件不用动（secret 变了才需要重启 DSH Web）。

服务单元模板在 `deploy/`，正式单元已安装到 `/etc/systemd/system/`：

| 单元 | 入口 |
| --- | --- |
| `dsh-story-between-api.service` | `app/venv/bin/python -m uvicorn app.main:app --port 8001` |
| `dsh-story-between-web.service` | `/usr/bin/python3.11 app/deploy/static_server.py`（`:8810`） |
| `dsh-story-between-worker.service` | `app/venv/bin/python -m app.worker` |
| `dsh-story-between-world-worker.service` | `app/venv/bin/python -m app.world.worker` |

## 风险 / 待验证

1. **应用侧可信登录未落地**：`x-dsh-trusted` 的后端校验由 app-auth 并行实现；
   落地前，面板里 API 调用会被应用当未登录（401/跳登录）。插件侧按契约发头，
   不需要跟着改；验收时需确认应用侧确实校验并通过。
2. **编辑器 DOM 依赖**：预填靠 Lexical 的 `contenteditable` + `execCommand`，
   宿主升级编辑器实现时可能失效。失效表现是「提示已复制到剪贴板」，
   不会丢内容；发现异常先查 `COMPOSER_SELECTOR` 与实际 DOM。
3. **`dsh-story:theme` 是契约外的附加消息**：应用未实现时无影响；
   应用实现时注意只切主题、不重载页面。
4. **8 秒上下文超时**：应用读到上千字正文通常远快于此；若真实环境常超时，
   需要应用侧优化读取或调整 `CONTEXT_TIMEOUT_MS`（改之前先确认契约是否接受）。
5. **父页面只认自己的 iframe**：`event.source` 校验 + origin 校验；
   应用若在 iframe 里再嵌第三方页面并转发消息，需要显式处理，当前不支持。

## 验收锚点一览（给测试用）

- 侧栏：`[data-dsh-panel-entry="story"]`、label「故事之间」、order 24；
- 面板：`[data-dsh-story-view]`、`[data-dsh-story-connected="1"]`、
  `[data-dsh-story-frame]`（`src` 以 `/story/` 结尾）、
  `[data-dsh-story-action=continue|roleplay|reload|open]`、`[data-dsh-story-extra]`；
- P2：假 iframe 可断言收到 `context-request{kind,id}`、父页面发出 `ping`/`theme`；
- 交 Agent：替身 `uiWorkspace.startSession` 能拿到含 `【作品】/【章节】/【最近正文】`
  的 prompt，并观察到 `layout.selectPanel(null)`。
