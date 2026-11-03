# dsh-story-between · 故事之间（DSH 插件）

把一个本地写作应用「故事之间」装进 DSH：侧栏「插件」组里多一行 **故事之间**，
点开不用等跳转，就是写作页本身；看中的正文可以一键交给 DSH 的 Agent
**续写下一段**，或者让 Agent 以某个角色做**第一人称角色扮演**。

- 侧栏顺序：紫微斗数(20) → **故事之间(24)** → Skill Center(30)
- 插件目录：`/root/workspace/plugins/dsh-story-between`，已在
  `/root/.dsh/profiles/web/package.json` 里以 `link:` 依赖注册进 `dsh.profile.bundles`
- 应用目录：本仓库 `app/`，由四个 systemd 单元跑在回环地址上：
  - `dsh-story-between-api.service` → `127.0.0.1:8001`（FastAPI / uvicorn）
  - `dsh-story-between-web.service` → `127.0.0.1:8810`（`app/client/dist` 静态站）
  - `dsh-story-between-worker.service` → 生成任务 worker
  - `dsh-story-between-world-worker.service` → 世界观任务 worker
- 公开入口只有 `https://dsh.frankfurtlin.top/story/`（受 DSH 登录保护）；
  `hermes.frankfurtlin.top/story` 的旧路由已退休。插件内部只连 `127.0.0.1`，不额外开端口

## 它由哪几块组成

| 文件 | 作用 |
| --- | --- |
| `package.json` | 声明 `dsh.bundle.patch`（cordis patch）、`dsh.client.platform=web`、`exports["./client"]`；`npm run check` 做语法检查 |
| `cordis.patch.yml` | `- insert: [{id: dsh-story-between, name: dsh-story-between}]`，`id` 必须等于包名 |
| `lib/index.js` | host 半：注册 `prefix /story` 反向代理；先过 DSH 登录闸门；`/api/**`、`/health/**` → 8001 并附加可信头，其余 → 8810 静态站 |
| `client.js` | client 半：注册侧栏行（`sidebar.panellist` 座位）+ 主面板（`main` 座位，`key=story`）；工具条 + 同源 iframe + P2 `postMessage` 桥 |
| `app/` | 写作应用本体（Vue 前端 + FastAPI 后端）。应用侧为插件做的改动（可信登录、P2 桥）也在这个目录里，随本仓库 git 管理 |
| `docs/contract.md` | 与应用侧的集成契约（路由分流、可信头、P2 协议、UI 座位）——**先读它再改代码** |
| `docs/architecture.md` | 本插件的实现架构：请求链路、鉴权、面板结构、P2 协议、prompt 规则、风险 |
| `docs/testing.md` | 验收方法：静态检查 + 隔离实例真实浏览器 E2E |
| `scripts/check.sh`、`verify.mjs` | 验收工具：静态检查 + 隔离实例端到端 |

> 应用本体（`app/`）需要先在 host 上跑起来，插件才有内容可代理。四个 systemd
> 单元已安装在 `/etc/systemd/system/dsh-story-between-*.service`，属于 host 运维，
> **不随本插件仓库分发**；本仓库只含插件两半 + 应用源码。

## 安装 / 启用

插件以 `link:` 方式注册在 Web profile（不在本仓库内执行这一步）：

```json
"dependencies": { "dsh-story-between": "link:/root/workspace/plugins/dsh-story-between" },
"dsh": { "profile": { "bundles": [ "...", "dsh-story-between" ] } }
```

应用以 host 侧 systemd 单元运行（单元文件在 `/etc/systemd/system/`，不在本仓库内）；
插件本身由 `dsh web` 进程加载，改完 `lib/` 或 `client.js` 后需要**重启 DSH Web 服务**
并刷新页面（详见 [docs/architecture.md](docs/architecture.md#配置)）。本仓库的插件代码
不存在独立构建步骤：`cordis.patch.yml` + 手写 `client.js` 由宿主直接加载。

## 请求链路一句话

浏览器 → DSH Web `:3080` → 登录闸门（`requestRejection`）→ `prefix /story`
→ 剥前缀分两路：API 请求带 `x-dsh-trusted` 去 `:8001`，静态资源去 `:8810`。
详细分流表、头处理、超时策略见 [docs/architecture.md](docs/architecture.md)。

## P2 桥：页面怎么把「当前故事」交给 DSH

面板与 iframe 同源（`/story/`），用 `postMessage` 通信，协议见
`docs/contract.md` §3：

| 方向 | 消息 | 用途 |
| --- | --- | --- |
| 父 → iframe | `dsh-story:ping` | 握手，应用回 `pong` |
| iframe → 父 | `dsh-story:pong` | 带上 `appPath`，面板显示「当前：…」 |
| iframe → 父 | `dsh-story:state` | 应用主动上报 `appPath` / `hasContext` |
| 父 → iframe | `dsh-story:context-request` | `kind: "continue" | "roleplay"`，带请求 id |
| iframe → 父 | `dsh-story:context` | 作品/章节/模式/角色/最近正文/`activeCharacter` |

拿到上下文后插件组装 prompt，通过宿主的 `uiWorkspace.startSession()` 新开一个
DSH 会话，并尝试把 prompt 预填进新会话编辑器（写不进去就退化为复制到剪贴板）。
角色扮演默认对象优先用 `context.activeCharacter`，为空时退化为 `characters`
末位；用户可以在工具条输入框里用 `@角色名` 强制指定。

**位置记忆**：DSH 切走面板会卸载 iframe，应用回来时默认停在 `/explore`。
插件会记住应用上报的最后一次有效路由（`state.hasContext=true` 或 `/worlds/`
路径；模块内存 + `sessionStorage` 镜像，key `dsh-story:last-app-path`），
重新挂载面板时把 iframe 恢复到那里；点「刷新」清空记忆并回应用首页，
用于恢复失败时兜底。详见 [docs/architecture.md](docs/architecture.md)。

## 配置

| 位置 | 键 | 说明 |
| --- | --- | --- |
| `app/.env` | `DSH_TRUSTED_SECRET` | 应用侧校验可信登录头的 secret，插件转发 API 请求时附加 |
| 宿主环境 | `DSH_STORY_TRUSTED_SECRET` | 覆盖上面的 secret（隔离测试 / 应急时用） |
| 宿主环境 | `DSH_STORY_API_PORT` | 覆盖 API 上游端口（默认 8001） |
| 宿主环境 | `DSH_STORY_STATIC_PORT` | 覆盖静态上游端口（默认 8810） |

secret 在插件启动（`apply`）时读入；读不到会打印告警，但不会让宿主崩溃，
此时 API 请求不带可信头，应用侧按未登录处理（401）。

## 改完代码先自检

```bash
cd /root/workspace/plugins/dsh-story-between

node --check lib/index.js && node --check client.js   # 同 npm run check
npm run check
```

涉及 `/story` 反代或面板行为的改动，请再跑 verifier 的端到端验收
（`scripts/check.sh` / `verify.mjs`，以它们的文档为准）。UI 改动必须用真实
浏览器验证过工具条与 iframe 交互，不能只看构建通过。

## 已知限制 / 待验证

- **应用侧可信登录**：`x-dsh-trusted` 由应用 `app/server/app/security.py` 做常量时间
  校验；应用进程需带 `DSH_TRUSTED_AUTH=1` + `DSH_TRUSTED_SECRET` 重启后才生效，
  否则面板里的 API 请求会被应用按未登录处理（401）。
- **编辑器预填依赖 DOM**：宿主没有「往会话草稿写文本」的公开 API，插件用
  Lexical 编辑器的原生插入命令模拟粘贴；宿主改编辑器实现时需要复核
  `client.js` 里的 `COMPOSER_SELECTOR`。
- **主题同步是附加消息**：`dsh-story:theme` 不在契约里，应用侧可选实现；
  不实现时 iframe 维持自己的主题，不影响功能。
- **P2 超时 8 秒**：应用侧读 store 较慢时会提示「应用没有响应」，可重试；
  不会卡死界面。
- **位置记忆只记「页面路由」**：恢复的是应用路由（如 `/worlds/w1/play`），
  应用内部未写进路由的临时状态（滚动位置、未保存草稿等）仍以应用自身为准；
  恢复的地址若失效，应用会回落 `/explore`，点「刷新」即可回首页。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/contract.md](docs/contract.md) | 与应用侧的集成契约（Lead 定稿）：分流、可信头、P2 协议、UI 座位 |
| [docs/architecture.md](docs/architecture.md) | 本插件实现：两层结构、请求链路、面板结构、P2 细节、prompt 规则、风险 |
