# dsh-tavern · 酒馆（DSH 插件）

把一个本地写作应用「酒馆」装进 DSH：侧栏「插件」组里多一行 **酒馆**，
点开不用等跳转，就是写作页本身；看中的正文可以一键交给 DSH 的 Agent
**续写下一段**，或者让 Agent 以某个角色做**第一人称角色扮演**。

- 侧栏顺序：紫微斗数(20) → **酒馆(24)** → Skill Center(30)
- 插件目录：`/root/workspace/plugins/dsh-tavern`，已在
  `/root/.dsh/profiles/web/package.json` 里以 `link:` 依赖注册进 `dsh.profile.bundles`
- 应用目录：本仓库 `app/`，由四个 systemd 单元跑在回环地址上：
  - `dsh-tavern-api.service` → `127.0.0.1:8001`（FastAPI / uvicorn）
  - `dsh-tavern-web.service` → `127.0.0.1:8810`（`app/client/dist` 静态站）
  - `dsh-tavern-worker.service` → 生成任务 worker
  - `dsh-tavern-world-worker.service` → 世界观任务 worker
- 公开入口只有 `https://dsh.frankfurtlin.top/tavern/`（受 DSH 登录保护）；
  旧的外站入口 `hermes.frankfurtlin.top/story` 已退休。插件内部只连 `127.0.0.1`，不额外开端口

## 它由哪几块组成

| 文件 | 作用 |
| --- | --- |
| `package.json` | 声明 `dsh.bundle.patch`（cordis patch）、`dsh.client.platform=web`、`exports["./client"]`；`npm run check` 做语法检查 |
| `cordis.patch.yml` | `- insert: [{id: dsh-tavern, name: dsh-tavern}]`，`id` 必须等于包名 |
| `lib/index.js` | host 半：注册 `prefix /tavern` 反向代理（旧 `/story` 为 302 兼容别名）；先过 DSH 登录闸门；`/api/**`、`/health/**` → 8001 并附加可信头，其余 → 8810 静态站。另外注册 `prefix /tavern-agent` 模型网关，给应用 worker 调 DSH 真生成 |
| `client.js` | client 半：注册侧栏行（`sidebar.panellist` 座位）+ 主面板（`main` 座位，`key=tavern`）；工具条 + 同源 iframe + P2 `postMessage` 桥 |
| `app/` | 写作应用本体（Vue 前端 + FastAPI 后端）。应用侧为插件做的改动（可信登录、P2 桥）也在这个目录里，随本仓库 git 管理 |
| `docs/contract.md` | 与应用侧的集成契约（路由分流、可信头、P2 协议、UI 座位）——**先读它再改代码** |
| `docs/architecture.md` | 本插件的实现架构：请求链路、鉴权、面板结构、P2 协议、prompt 规则、风险 |
| `docs/testing.md` | 验收方法：静态检查 + 隔离实例真实浏览器 E2E |
| `scripts/check.sh`、`verify.mjs` | 验收工具：静态检查 + 隔离实例端到端 |

> 应用本体（`app/`）需要先在 host 上跑起来，插件才有内容可代理。四个 systemd
> 单元已安装在 `/etc/systemd/system/dsh-tavern-*.service`，属于 host 运维，
> **不随本插件仓库分发**；本仓库只含插件两半 + 应用源码。

## 安装 / 启用

插件以 `link:` 方式注册在 Web profile（不在本仓库内执行这一步）：

```json
"dependencies": { "dsh-tavern": "link:/root/workspace/plugins/dsh-tavern" },
"dsh": { "profile": { "bundles": [ "...", "dsh-tavern" ] } }
```

应用以 host 侧 systemd 单元运行（单元文件在 `/etc/systemd/system/`，不在本仓库内）；
插件本身由 `dsh web` 进程加载，改完 `lib/` 或 `client.js` 后需要**重启 DSH Web 服务**
并刷新页面（详见 [docs/architecture.md](docs/architecture.md#配置)）。本仓库的插件代码
不存在独立构建步骤：`cordis.patch.yml` + 手写 `client.js` 由宿主直接加载。

## 请求链路一句话

浏览器 → DSH Web `:3080` → 登录闸门（`requestRejection`）→ `prefix /tavern`（旧 `/story` 302 兼容）
→ 剥前缀分两路：API 请求带 `x-dsh-trusted` 去 `:8001`，静态资源去 `:8810`。
详细分流表、头处理、超时策略见 [docs/architecture.md](docs/architecture.md)。

## P2 桥：页面怎么把「当前故事」交给 DSH

面板与 iframe 同源（`/tavern/`），用 `postMessage` 通信，协议见
`docs/contract.md` §3：

| 方向 | 消息 | 用途 |
| --- | --- | --- |
| 父 → iframe | `dsh-tavern:ping` | 握手，应用回 `pong` |
| iframe → 父 | `dsh-tavern:pong` | 带上 `appPath`，面板显示「当前：…」 |
| iframe → 父 | `dsh-tavern:state` | 应用主动上报 `appPath` / `hasContext` |
| 父 → iframe | `dsh-tavern:context-request` | `kind: "continue" | "roleplay"`，带请求 id |
| iframe → 父 | `dsh-tavern:context` | 作品/章节/模式/角色/最近正文/`activeCharacter` |

拿到上下文后插件组装 prompt，通过宿主的 `uiWorkspace.startSession()` 新开一个
DSH 会话，并尝试把 prompt 预填进新会话编辑器（写不进去就退化为复制到剪贴板）。
角色扮演默认对象优先用 `context.activeCharacter`，为空时退化为 `characters`
末位；用户可以在工具条输入框里用 `@角色名` 强制指定。

**位置记忆**：DSH 切走面板会卸载 iframe，应用回来时默认停在 `/explore`。
插件会记住应用上报的最后一次有效路由（`state.hasContext=true` 或 `/worlds/`
路径；模块内存 + `sessionStorage` 镜像，key `dsh-tavern:last-app-path`），
重新挂载面板时把 iframe 恢复到那里；点「刷新」清空记忆并回应用首页，
用于恢复失败时兜底。详见 [docs/architecture.md](docs/architecture.md)。

## DSH 模型网关：应用 worker 怎么用真生成

应用的生成 worker 是服务端进程，没有浏览器会话。插件在 `/tavern` 旁边开了一个
平级前缀 `/tavern-agent`，用共享 secret 做服务端到服务端鉴权，复用 DSH 已经
跑着的模型通道（`ctx.llm.stream`）：

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/tavern-agent/v1/health` | `GET` | 探活 + 返回当前默认模型 |
| `/tavern-agent/v1/complete` | `POST` | 一次性返回整段文本 |
| `/tavern-agent/v1/stream` | `POST` | SSE 增量返回（每段文本一帧，末尾 `data: [DONE]`） |

- 鉴权：`Authorization: Bearer <secret>`；secret 读 `app/.env` 的
  `DSH_TAVERN_AGENT_SECRET`，缺省回退 `DSH_TRUSTED_SECRET`，**没配则一律 401**；
- 模型：请求可用 `model:{provider,model}` 指定，否则用 DSH 当前默认模型
  （`agentDefaultModel`）；可选 `sessionId`（应用按会话传稳定值，缺省每次随机
  UUID，opencode-go 等 provider 靠它做上游会话）；`reasoningEffort` 默认
  `low`（覆盖 profile 的 high，避免把 maxTokens 烧在思考里；当前聊天模型只支持
  `low`/`high`）；
- 失败：401 `AGENT_UNAUTHORIZED`、400 `AGENT_BAD_REQUEST`、
  502 `AGENT_UPSTREAM_FAILED`；SSE 开始后失败走 `{"type":"error"}` 帧；
- 超时 120s，客户端断开/超时会中断上游；日志只记模型名、字数、耗时、错误码，
  **不记 prompt、消息内容和 secret**。

应用侧在 `AI_MODE=dsh` 时使用它（`DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1`，
`DSH_AGENT_SECRET` 与 `DSH_TAVERN_AGENT_SECRET` 同值）；回滚改 `AI_MODE=mock`。
详见 [docs/architecture.md](docs/architecture.md)。

## 配置

| 位置 | 键 | 说明 |
| --- | --- | --- |
| `app/.env` | `DSH_TRUSTED_SECRET` | 应用侧校验可信登录头的 secret，插件转发 API 请求时附加 |
| `app/.env` 或宿主环境 | `DSH_TAVERN_AGENT_SECRET` | 模型网关 Bearer secret；缺省回退 `DSH_TRUSTED_SECRET`，都没有则网关一律 401 |
| 宿主环境 | `DSH_TAVERN_TRUSTED_SECRET` | 覆盖可信登录 secret（隔离测试 / 应急时用） |
| 宿主环境 | `DSH_TAVERN_API_PORT` | 覆盖 API 上游端口（默认 8001） |
| 宿主环境 | `DSH_TAVERN_STATIC_PORT` | 覆盖静态上游端口（默认 8810） |
| 宿主环境 | `DSH_TAVERN_ENV_FILE` | 覆盖读取的 `.env` 路径（隔离测试用，生产不设） |
| 宿主环境 | `DSH_TAVERN_AGENT_TIMEOUT_MS` | 覆盖模型网关超时（默认 120000；仅测试用） |

secret 在插件启动（`apply`）时读入；读不到会打印告警，但不会让宿主崩溃，
此时 API 请求不带可信头，应用侧按未登录处理（401）。

## 改完代码先自检

```bash
cd /root/workspace/plugins/dsh-tavern

node --check lib/index.js && node --check client.js   # 同 npm run check
npm run check
```

涉及 `/tavern` 反代或面板行为的改动，请再跑 verifier 的端到端验收
（`scripts/check.sh` / `verify.mjs`，以它们的文档为准）。UI 改动必须用真实
浏览器验证过工具条与 iframe 交互，不能只看构建通过。

## 已知限制 / 待验证

- **应用侧可信登录**：`x-dsh-trusted` 由应用 `app/server/app/security.py` 做常量时间
  校验；应用进程需带 `DSH_TRUSTED_AUTH=1` + `DSH_TRUSTED_SECRET` 重启后才生效，
  否则面板里的 API 请求会被应用按未登录处理（401）。
- **编辑器预填依赖 DOM**：宿主没有「往会话草稿写文本」的公开 API，插件用
  Lexical 编辑器的原生插入命令模拟粘贴；宿主改编辑器实现时需要复核
  `client.js` 里的 `COMPOSER_SELECTOR`。
- **主题同步是附加消息**：`dsh-tavern:theme` 不在契约里，应用侧可选实现；
  不实现时 iframe 维持自己的主题，不影响功能。
- **P2 超时 8 秒**：应用侧读 store 较慢时会提示「应用没有响应」，可重试；
  不会卡死界面。
- **位置记忆只记「页面路由」**：恢复的是应用路由（如 `/worlds/w1/play`），
  应用内部未写进路由的临时状态（滚动位置、未保存草稿等）仍以应用自身为准；
  恢复的地址若失效，应用会回落 `/explore`，点「刷新」即可回首页。
- **模型网关依赖应用侧 adapter**：网关只负责转发一次模型调用；`AI_MODE=dsh` 下
  应用 worker 的 `DshAgentAdapter` 必须按契约调它，否则应用仍走 mock 生成。
  网关 secret 在插件启动时读入，轮换后需要重启 DSH Web；没配 secret 时连
  `/v1/health` 都 401，这是「配置错就明确失败」的预期行为。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/contract.md](docs/contract.md) | 与应用侧的集成契约（Lead 定稿）：分流、可信头、P2 协议、UI 座位 |
| [docs/architecture.md](docs/architecture.md) | 本插件实现：两层结构、请求链路、面板结构、P2 细节、prompt 规则、风险 |
