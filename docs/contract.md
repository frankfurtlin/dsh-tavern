# dsh-story-between 集成契约（v1，Lead 定稿）

目标：把 story-between 变成一个 DSH 插件，放在 `/root/workspace/plugins/dsh-story-between`，
在 DSH Web 里以内嵌面板访问，登录只走 DSH 闸门；用户能把当前故事交给 DSH Agent 续写 / 角色扮演。

应用源码已迁到 `plugins/dsh-story-between/app`（原 `/root/workspace/story-between`），
systemd 服务已改名并以新路径运行：

- `dsh-story-between-api`  → uvicorn，127.0.0.1:8001
- `dsh-story-between-web`  → 静态 dist，127.0.0.1:8810
- `dsh-story-between-worker` / `dsh-story-between-world-worker`

## 1. 宿主反代（plugin host 半，lib/index.js）

- 路由：`ROUTE_PATH = '/story'`，用 `ctx.webServer.register({kind:'prefix', path:'/story', handler})`。
- 每个请求先过登录闸门：`ctx.get('connection', false).requestRejection(req)`；
  返回 403/401 时回纯文本并结束；`undefined` 才放行（与 dsh-ziwei 完全一致）。
- 分流（`/story` 前缀剥掉后转发）：
  - `/story/api/**`、`/story/health/**` → `127.0.0.1:8001`
  - 其余 `/story/**`（HTML、/assets、sw.js、favicon…） → `127.0.0.1:8810`
- 转发到 8001 的每个请求附加请求头 `x-dsh-trusted: <secret>`；客户端传入的
  `x-dsh-trusted` 一律先删除，绝不能透传。
- secret 来源：启动时读 `app/.env` 的 `DSH_TRUSTED_SECRET`（允许 env 覆盖
  `DSH_STORY_TRUSTED_SECRET`）。读不到只告警，不崩宿主。
- 流式：`/story/api/**` 可能是数分钟的 SSE（生成/续写），**不要设响应超时**；
  静态资源 10s 超时即可。转发必须用 pipe 保留 SSE、chunked、multipart 上传。
- 错误文案：401/403 沿用 ziwei 的措辞；502/504 用中文友好提示，诊断细节只进宿主日志。

## 2. 应用可信模式（app 半，app/server）

`.env` 已写入：`DSH_TRUSTED_AUTH=1`、`DSH_TRUSTED_USERNAME=frankslin`、
`DSH_TRUSTED_SECRET=<随机 64hex>`；`CORS_ORIGINS` 已包含 `http://127.0.0.1:3080`、
`https://dsh.frankfurtlin.top`。

- `config.py`：新增 `trusted_auth: bool = False`、`trusted_username: str = ""`、
  `trusted_secret: str = ""`（env 名同上，pydantic BaseSettings 自动映射）。
- `security.current_user`：当 trusted 模式开启且请求头 `x-dsh-trusted` 与 secret
  常量时间比较通过时，解析固定用户（按 username，active）并复用/创建一个稳定的
  DeviceSession，`request.state.session` 照常设置，返回该用户。
- `api_auth` 的 `POST /api/reader/v1/auth/refresh`：trusted 路径下直接返回
  `token_result(...)`（给前端 accessToken），不要求 cookie、不做 cookie 轮换。
  这是让前端 `store.init() → refreshAuth()` 无感启动的关键，前端不需要改。
- `POST /api/reader/v1/auth/logout`：trusted 模式下 no-op（204），不吊销稳定会话。
- 头部缺失/secret 不匹配时行为与现在完全一致（等于没开 trusted）。
- 固定用户不存在：返回结构化错误（401/503 + 明确日志），不要 500，不要静默建号。
- 测试：新增 pytest 覆盖 valid/无效头、refresh、logout、未开启 trusted 的回归；
  跑通 `app/server` 已有测试。

## 3. 面板 ↔ 内嵌页桥（P2）

同源 iframe：`new URL('story/', document.baseURI)`，父页面与 iframe 用
`window.location.origin` 校验、`event.source` 校验。

父 → iframe：
- `{type:'dsh-story:ping'}` → iframe 回 `{type:'dsh-story:pong', appPath}`
- `{type:'dsh-story:context-request', id, kind:'continue'|'roleplay', target?}`
  → iframe 回
  `{type:'dsh-story:context', id, ok:true, context:{
      appPath, worldTitle, lineTitle, mode,
      characters:[{name, identity, speech}],
      activeCharacter, // 最近活跃角色名，可为空（父页面缺省时退化取 characters 末位）
      recentText,   // 最近正文纯文本，截断到 ~4000 字
   }}`
  失败时 `{type:'dsh-story:context', id, ok:false, error:'...'}`

iframe → 父（主动，节流）：
- `{type:'dsh-story:state', appPath, hasContext:boolean}`

## 4. UI

- 侧栏 `sidebar.panellist`：`PANEL_ID="story"`、label「故事之间」、order 24（落在
  ziwei 20 与 Skill Center 30 之间），图标参考 ziwei 的行内 SVG 写法。
- `main` 座位 keyed 面板 `story`：工具条（标题、刷新、新窗口打开、让 DSH 续写、
  角色扮演、续写要求输入、状态提示）+ iframe（`data-dsh-story-frame`）。
- 位置记忆（宿主侧行为，应用无需配合）：插件记住最后一次 `state`/`pong` 上报的
  有效站内路径（`hasContext=true` 或 `/worlds/…`），重新挂载面板时把 iframe 深链到
  该路径；「刷新」= 清空记忆并回 `/story/` 首页（恢复失败时的脱困路径）。验收锚点
  `data-dsh-story-restored="1|0"`。记忆只存 sessionStorage，不上报服务端。
- 交给 Agent：沿用 ziwei 的 `workspaces.initializeDefault` → `uiWorkspace.startSession`
  → `layout.selectPanel(null)` → 编辑器填充 + 剪贴板兜底（该方案已在本机验证过）。
- 提示词模板：
  - 续写：作品/章节/模式/角色/最近正文 + 「请基于以上内容续写下一段（400–600 字，
    保持文风与人物口吻）」+ 用户额外要求。
  - 角色扮演：以 `target`（未指定时用最近活跃角色）第一人称即兴演绎 + 最近情境 +
    用户额外要求。

## 5. 文件所有权（避免并发写冲突）

| 范围 | owner |
| --- | --- |
| `app/**`（server + client 源码、dist 构建） | app-auth |
| `package.json`、`cordis.patch.yml`、`lib/**`、`client.js`、`deploy/**`、`README.md` | plugin-core |
| `scripts/**`、`verify.mjs`、`restart-and-verify.sh`、`verification/**`、`docs/testing.md` | verifier |
| `docs/operations.md`、`finalize-prod.sh`、`deploy/Caddyfile.story-retired` | Lead |
| `/etc/systemd/system`、Caddy、`/root/.dsh/profiles/web`、生产重启 | Lead |

任何人不改别人范围的文件；发现 bug 用 send_message 报给 owner 或 Lead。
