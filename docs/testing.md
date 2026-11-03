# 测试与验收（dsh-story-between）

本插件是「DSH Web 里的故事之间」：宿主半 `lib/index.js` 把本地应用挂到 `/story`
前缀并复用 DSH 登录闸门；浏览器半 `client.js` 加侧栏入口、主面板 iframe 和
「让 DSH 续写 / 角色扮演」工具条。验收因此必须同时覆盖 **HTTP 闸门**、**DSH Web UI**
和 **iframe 内应用/P2 桥**，只看单测或构建通过不算数。

契约见 [contract.md](./contract.md)；本文只讲怎么验、怎么读结果、有哪些坑。

## 两套入口

### 1. `scripts/check.sh` —— 隔离验收（日常用，绝不动生产 3080）

```bash
cd /root/workspace/plugins/dsh-story-between

scripts/check.sh                  # 静态检查 + /tmp 隔离实例 + 真实浏览器 E2E（默认）
scripts/check.sh --static         # 只跑静态检查（秒级）
scripts/check.sh --instance       # 只跑隔离实例 + E2E
scripts/check.sh --with-app-tests # 额外跑应用前端 typecheck + 单测（慢）
scripts/check.sh --no-bridge      # 跳过「交给 Agent」的端到端点击
scripts/check.sh --open-story     # 先在应用内打开一部作品再点续写（会写阅读进度，验证提示词模板全链路）
scripts/check.sh --keep-home      # 保留 /tmp/dsh-story-check 供排查
scripts/check.sh --port 3199      # 3099 被占用时换端口
```

隔离实例细节（照搬 `dsh-ziwei/scripts/check.sh` 的做法）：

| 项 | 值 |
| --- | --- |
| 临时 HOME | `/tmp/dsh-story-check`（退出时删除，`--keep-home` 保留） |
| profile | `check`（`$CHECK_HOME/profiles/check`） |
| 端口 | 默认 `3099`，与生产 `3080` 不冲突 |
| 依赖 | 生产 profile 已安装 `dsh-story-between` 时 **mirror**：符号链接其 `package.json` / `node_modules`，复制 `cordis*.yml`；未安装时 **dev**：生成 profile `package.json` 把插件源目录 link 进 `node_modules` |
| 登录态 | 复制 `~/.dsh/.credentials.yaml`（chmod 600）和 `storages/workspace.json`，避免首次启动弹 Get started / Choose a workspace |
| 启动 | `DSH_HOME=$CHECK_HOME dsh --profile check --no-open --host 127.0.0.1 --port 3099`，从日志里取 `?token=...` |
| 清理 | `trap cleanup EXIT INT TERM`：先杀 PID 再 `pkill -f` 兜底，复制 `dsh.log` 到 `verification/check/logs/dsh.log` |

`/story/` 还没挂载（404）时脚本不会崩：E2E 会输出「未就绪」报告并 exit 1。

### 2. `restart-and-verify.sh` —— 生产重启后冒烟（Lead 执行）

**不能在 DSH 会话里直接跑**（`systemctl restart` 会把自己杀掉），用 systemd-run：

```bash
systemd-run --on-active=10s --unit=dsh-story-verify \
  /root/workspace/plugins/dsh-story-between/restart-and-verify.sh

# 已重启过、只想复跑验收：
scripts/restart-and-verify.sh --no-restart
```

它做四件事：重启 `deepseek-harness.service` → 等 3080 → 从 journalctl 取 token →
本地/公网 `/story/` 状态码检查（本地期望 401；公网期望 302/401，**404 即未接通**）→
跑 `verify.mjs --out verification/prod`。报告和截图写在 `verification/prod/`。
默认**不**打开作品（不写生产阅读进度），F 组只验「优雅降级」；要跑提示词填充全链路加
`DSH_OPEN_STORY=1`（会写一条阅读进度，确认可接受后再开）。

## `verify.mjs` 断言清单

产物：`verification/{check,prod}/report.json` + 截图
`01-sidebar.png`、`02-panel.png`、`03-app-iframe.png`、`03b-story-open.png`、
`04-continue-draft.png`、`05-roleplay-draft.png`、`06-restore-panel.png`、
`07-reload-restore.png`、`08-refresh-home.png`。

| 组 | 检查项 | 期望 | 失败归属 |
| --- | --- | --- | --- |
| A | 未登录 `GET /story/` | 401（404 = 路由没挂上；200 = 闸门失效） | plugin-core |
| B | 侧栏有「故事之间」入口（`button[aria-label="故事之间"]` 等） | 可见可点 | plugin-core |
| B | 点击后 main 面板出现 `iframe[data-dsh-story-frame]` | 可见、尺寸 > 500×300 | plugin-core |
| B | iframe 指向 `/story/`；登录态 `/story/` = 200 | 与匿名 401 成对 | plugin-core |
| B | 首次挂载（sessionStorage 无记忆） | iframe `src` 是 `/story/`，`data-dsh-story-restored=0` | plugin-core |
| C | 内嵌应用是 `AppShell` 而不是应用登录页 | `.app-shell` 存在、`.entry-page` 不存在 | app-auth（trusted 模式） |
| C | 品牌「故事之间」+ 侧栏导航 ≥ 3 项；正文非空白 | 稳定内容渲染 | app-auth |
| D | 工具条「让 DSH 续写」「角色扮演」按钮可见 | 可见 | plugin-core |
| E | P2 `ping` → `pong`（带 `appPath`） | 8s 内响应 | app-auth |
| E | P2 应用主动上报 `state` | 收到 `{appPath,hasContext}`（`report.bridge.state`） | app-auth |
| E | P2 `context-request` → `context` | 10s 内响应，`ok:true` | app-auth |
| E | context 字段齐全 | `appPath/worldTitle/lineTitle/mode/characters/recentText` 类型正确 | app-auth |
| C2 | `--open-story`：在 explore 页点「继续游玩/开始游玩」进入 `/worlds/…` 故事页 | 进入故事页后 context 才有当前故事 | app-auth（数据/选择器） |
| C2 | 打开作品后位置记忆落盘 | `sessionStorage["dsh-story:last-app-path"]` 含 `/worlds/` 路径（`report.app.memoryAfterOpen`） | plugin-core |
| F | 点「让 DSH 续写」 | 新会话打开 + 编辑器草稿匹配 `/续写/`（或剪贴板兜底），并含模板句「请基于以上内容续写下一段」 | plugin-core + app-auth |
| F | 点「角色扮演」 | 新会话 + 草稿/剪贴板匹配 `/【你要扮演】/` 且 `/第一人称即兴演绎/`（点前会清空剪贴板，避免复用续写提示词假阳性）；点前应先等到恢复后的 context | plugin-core + app-auth |
| G1 | 切到其它面板（紫微斗数）再点回「故事之间」 | iframe 深链回 `/story/worlds/…/play`、`data-dsh-story-restored=1`、P2 `state.hasContext=true`、context 仍 `ok:true` | plugin-core |
| G2 | 整页 reload DSH 后重新进面板 | 仍从 `sessionStorage` 恢复（`restored=1`，路径仍是故事页） | plugin-core |
| G3 | 点「刷新」 | iframe 回 `/story/`、`restored=0`、`sessionStorage` 键被删、应用自路由到 `/explore` | plugin-core |
| H | 页面无控制台 JS 报错 | 0 条（资源加载失败不算） | plugin-core |

### 自动化锚点（契约约定）

验收脚本优先用这些稳定锚点，不靠中文文案猜测：

| 锚点 | 位置 | 说明 |
| --- | --- | --- |
| `data-dsh-panel-entry="story"` | 侧栏行 | 脚本会点击它所属的 `button/a`（锚点可能在行内图标上） |
| `data-dsh-story-view` | 主面板根 | 面板是否存在 |
| `data-dsh-story-hint` | 主面板根 | `idle / loading / waiting / ready / busy / sent / error`；`error` = 插件走了降级分支 |
| `data-dsh-story-connected` | 主面板根 | `1/0`，是否收到应用 P2 state |
| `data-dsh-story-restored` | 主面板根 | `1/0`，本次挂载是否从位置记忆深链恢复 |
| `sessionStorage["dsh-story:last-app-path"]` | 浏览器存储 | 最后一次有效应用路由（`/worlds/…` 或 `hasContext=true` 的页面）；「刷新」会删除 |
| `data-dsh-story-status` | 状态文字 | 人话提示，原样写进报告 |
| `data-dsh-story-action="continue\|roleplay\|reload\|open"` | 工具条按钮/链接 | 点击目标 |
| `data-dsh-story-frame` | iframe | 内嵌应用 |
| `data-dsh-story-extra` | 续写要求输入框 | 可选输入 |

### 关键判定细节（踩过的坑）

- **「新会话打开」不能看 `body.innerText` 里有没有 `New Session`**：DSH 侧栏的
  New Session 按钮一直都在，会假阳性。可靠信号是 **面板关闭 + Lexical 输入框出现**
  （`[contenteditable="true"]` 数量从 0 变 1），验收脚本按这个判定。
- **面板打开时主区被面板占据**，输入框数量为 0、hero 文案消失；成功交给 Agent 后
  `layout.selectPanel(null)` 关闭面板，会话输入框才回来。失败时面板保留并显示状态提示。
- 应用侧尚未就绪时（登录页/没有打开故事），插件应 **优雅降级**：面板内出现
  「读取故事内容失败…」「没有可交给…」等提示，且 **不误开空会话**。
  `report.bridge.continue.panelStatus` 会如实记录这句话，这类检查记为 SKIP 并写明根因。
- SKIP 不是通过：它表示「依赖方未就绪，无法断言」。最终 `report.ok` 只在
  `fail === 0 && pass > 0` 时为 true。
- `PASS/FAIL/SKIP` 三态在 `report.json` 的 `checks[].{name,ok,skipped,detail}` 里，
  端到端分支（`draft` / `clipboard` / `draft+clipboard`）和 P2 payload 在
  `report.bridge` 里，方便贴到交接报告。
- **点按钮前先轮询 context 直到 `ok:true`**（`waitContext()`）：应用刚启动/面板重挂载时
  世界 store 还没加载，此刻 `context-request` 会立刻回 `ok:false`
  （「当前页面没有可用的故事上下文」），是瞬时状态不是缺陷。没等就点会误判插件降级。
- **位置记忆（plugin-core 新增）**：DSH 关闭面板会卸载 iframe，应用重挂载默认回
  `/explore`。插件现在把最后有效路由写进内存 + `sessionStorage`，重挂载时深链回去
  （`data-dsh-story-restored=1`）；「刷新」按钮 = 清记忆回 `/story/`（`restored=0`）；
  整页 reload DSH 后仍能恢复。E2E 的 G1/G2/G3 覆盖这三条路径，`report.restore` 里存证据。
  注意恢复的是 `pathname`，`?line=` 查询串不保留；世界页靠应用自身 store 选当前章，
  实测恢复后 context 仍 `ok:true`。
- **F2 现在优先靠记忆恢复**：重开面板后先等 iframe 回到 `/worlds/` 再等 context；
  记忆失效才退回「去 explore 重新打开作品」的兜底（`report.bridge.roleplayReopenedStory`）。
- 角色扮演成功时 DSH DOM 里可能同时留着上一会话的编辑器节点，`draft` 会包含续写+扮演两段
  提示词；`clipboard` 是干净的单条扮演提示词，报告里两个都记。

## 手工探针（不启浏览器）

```bash
# 插件/servlet 是否挂载（未登录；404 = 未挂载，200 = 闸门失效）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3080/story/

# 应用服务健康
curl -s -o /dev/null -w 'api live=%{http_code}\n' http://127.0.0.1:8001/health/live
curl -s -o /dev/null -w 'api ready=%{http_code}\n' http://127.0.0.1:8001/health/ready
curl -s -o /dev/null -w 'web=%{http_code}\n' http://127.0.0.1:8810/

# trusted 登录态是否已部署（不要把 secret 打印到终端/报告！）
SECRET=$(grep -E '^DSH_TRUSTED_SECRET=' app/.env | cut -d= -f2-)
curl -s -o /dev/null -w 'trusted refresh=%{http_code}\n' \
  -X POST http://127.0.0.1:8001/api/reader/v1/auth/refresh \
  -H "x-dsh-trusted: $SECRET" -H 'Content-Type: application/json' -d '{}'
# 200 = 已部署；401 AUTH_REQUIRED = 还是旧进程（改完 app/server 必须重启 API 服务）
```

## 常见坑

| 现象 | 原因 / 处理 |
| --- | --- |
| `EADDRINUSE :3099` | 上次实例没清掉：`pkill -f 'dsh --profile check'`，或 `--port 3199` |
| 120s 内没等到 token | 看 `verification/check/logs/dsh.log` 尾部；常见是 profile `package.json`/`node_modules` 不可用、bundle 解析失败 |
| 隔离实例登录/quota 异常 | 检查 `~/.dsh/.credentials.yaml` 是否复制成功（600） |
| 截图中文豆腐块 | 脚本必须 `export HOME=/root XDG_CACHE_HOME=/root/.cache`（systemd 单元里 HOME 为空会跳过 `/root/.fonts`） |
| Welcome/Get started 遮罩挡住侧栏点击 | verify 里 `dismissWelcome()` 关 `div[role="presentation"] button[aria-label="Close"]`；新 HOME 需要先复制 `workspace.json` |
| 改了 `client.js` 要不要重启 | 浏览器半刷新页面即生效；`lib/index.js` 是宿主半，**必须重启 DSH** |
| 改了 `app/client` 没生效 | 需要重新 `npm run build`（`APP_BASE=/story`，产物到 `app/client/dist`），web 服务直接读 dist；不需要重启 DSH |
| `AUTH_REQUIRED`（trusted 头带了也 401） | 应用 API 进程还是旧代码：`systemctl restart dsh-story-between-api`（app-auth 改完 `config.py/security.py/api_auth.py` 后必须重启） |
| Playwright 起不来 | `createRequire('/root/workspace/hermes-site/tools/node_modules/')` 取全局 playwright；`chromium.launch({headless:true,args:['--no-sandbox']})` |
| 无模型凭据能跑吗 | 能。验收只开草稿不点发送，不触发模型调用 |

## 已知缺口（随进度更新）

- [x] **app-auth**：trusted 登录 + P2 桥（ping/context/state）已部署（API 21:46 重启、
  dist 21:29 含桥）；隔离实例 E2E **42 PASS / 0 FAIL / 0 SKIP**（`--open-story`，含位置记忆 G1–G3）。
- [x] **应用内要有「当前故事」**：`--open-story` 会在 explore 点「继续游玩」进入
  `/worlds/:id/play`；context 实测 `mode=world-play world=蓝宝石案 line=福尔摩斯的故事
  chars=12 recent=286字`。应用里当前有 4 部 published/live 作品（DB `product_publications`）。
- [ ] **生产 3080 还是旧进程**：`/story/` 仍 404（profile 已安装插件，21:10 起）。需要
  Lead 用 `restart-and-verify.sh` 重启 `deepseek-harness.service` 后才算生产验收。
- [x] **面板切换丢应用路由**：plugin-core 已实现位置记忆（`sessionStorage` 深链 +
  `data-dsh-story-restored`）；G1 切到紫微斗数再点回、G2 整页 reload、G3「刷新」清记忆
  全部 PASS。恢复只带 pathname，`?line=` 不保留（应用靠 store 选章，context 仍 ok）。
- [ ] **生产冒烟默认不覆盖 F 全链路**：生产默认不写阅读进度，F 只验降级；要覆盖就
  `DSH_OPEN_STORY=1`（写一条阅读进度）。
