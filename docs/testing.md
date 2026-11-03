# 测试与验收（dsh-tavern / 酒馆）

本插件是「DSH Web 里的酒馆」：宿主半 `lib/index.js` 把本地写作应用挂到 `/tavern`
前缀并复用 DSH 登录闸门（旧前缀 `/story` 仍注册：GET/HEAD 302 到 `/tavern` 同后缀
同 query，POST 等原地改写前缀后反代）；浏览器半 `client.js` 加侧栏入口「酒馆」、
主面板 iframe 和「让 DSH 续写 / 角色扮演」工具条。验收因此必须同时覆盖 **HTTP 闸门**、
**DSH Web UI** 和 **iframe 内应用/P2 桥**，只看单测或构建通过不算数。

契约见 [contract.md](./contract.md)；本文只讲怎么验、怎么读结果、有哪些坑。

## 验收入口

### `scripts/check.sh` —— 隔离验收（日常用，绝不动生产 3080）

```bash
cd /root/workspace/plugins/dsh-tavern

scripts/check.sh                  # 静态检查 + /tmp 隔离实例 + 真实浏览器 E2E（默认）
scripts/check.sh --static         # 只跑静态检查（秒级）
scripts/check.sh --instance       # 只跑隔离实例 + E2E
scripts/check.sh --with-app-tests # 额外跑应用前端 typecheck + 单测（慢）
scripts/check.sh --no-bridge      # 跳过「交给 Agent」的端到端点击
scripts/check.sh --open-story     # 先在应用内打开一部作品再点续写（会写阅读进度，验证提示词模板全链路）
scripts/check.sh --keep-home      # 保留 /tmp/dsh-tavern-check 供排查
scripts/check.sh --port 3199      # 3099 被占用时换端口
```

隔离实例细节（照搬 `dsh-ziwei/scripts/check.sh` 的做法）：

| 项 | 值 |
| --- | --- |
| 临时 HOME | `/tmp/dsh-tavern-check`（退出时删除，`--keep-home` 保留；`DSH_TAVERN_CHECK_HOME` 可覆盖） |
| profile | `check`（`$CHECK_HOME/profiles/check`） |
| 端口 | 默认 `3099`，与生产 `3080` 不冲突（`DSH_TAVERN_CHECK_PORT` 可覆盖） |
| 依赖 | 生产 profile 已安装 `dsh-tavern` 时 **mirror**：符号链接其 `package.json` / `node_modules`，复制 `cordis*.yml`；未安装时 **dev**：生成 profile `package.json` 把插件源目录 link 进 `node_modules` |
| 登录态 | 复制 `~/.dsh/.credentials.yaml`（chmod 600）和 `storages/workspace.json`，避免首次启动弹 Get started / Choose a workspace |
| 启动 | `DSH_HOME=$CHECK_HOME dsh --profile check --no-open --host 127.0.0.1 --port 3099`，从日志里取 `?token=...` |
| 清理 | `trap cleanup EXIT INT TERM`：先杀 PID 再 `pkill -f` 兜底，复制 `dsh.log` 到 `$OUT/logs/dsh.log` |

产物默认写在 `/tmp/dsh-tavern-verify/check/`（`report.json`、截图、`logs/`），可用
`DSH_TAVERN_VERIFY_OUT` 覆盖。`/tavern/` 还没挂载（404）时脚本不会崩：E2E 会输出
「未就绪」报告并 exit 1。

## `verify.mjs` 断言清单

产物：`report.json` + 截图 `01-sidebar.png`、`02-panel.png`、`03-app-iframe.png`、
`03b-tavern-open.png`、`04-continue-draft.png`、`05-roleplay-draft.png`、
`06-restore-panel.png`、`07-reload-restore.png`、`08-refresh-home.png`。

| 组 | 检查项 | 期望 | 失败归属 |
| --- | --- | --- | --- |
| A | 未登录 `GET /tavern/` | 401（404 = 路由没挂上；200 = 闸门失效） | plugin-core |
| A2 | 登录态 `GET /story/?probe=1&x=2`（旧别名） | 302，`Location` 指向 `/tavern/` 且保留 query | plugin-core |
| B | 侧栏有「酒馆」入口（`[data-dsh-panel-entry="tavern"]` / `button[aria-label="酒馆"]`） | 可见可点 | plugin-core |
| B | 点击后 main 面板出现 `iframe[data-dsh-tavern-frame]` | 可见、尺寸 > 500×300 | plugin-core |
| B | iframe 指向 `/tavern/`；登录态 `/tavern/` = 200 | 与匿名 401 成对 | plugin-core |
| B | 首次挂载（sessionStorage 无记忆） | iframe `src` 是 `/tavern/`，`data-dsh-tavern-restored=0` | plugin-core |
| C | 内嵌应用是 `AppShell` 而不是应用登录页 | `.app-shell` 存在、`.entry-page` 不存在 | app-auth（trusted 模式） |
| C | 品牌「酒馆」+ 侧栏导航 ≥ 3 项；正文非空白 | 稳定内容渲染 | app-auth |
| C2 | `--open-story`：在 explore 页点「继续游玩/开始游玩」进入 `/worlds/…` 故事页 | 进入故事页后 context 才有当前故事 | app-auth（数据/选择器） |
| C2 | 打开作品后位置记忆落盘 | `sessionStorage["dsh-tavern:last-app-path"]` 含 `/worlds/` 路径（`report.app.memoryAfterOpen`） | plugin-core |
| D | 工具条「让 DSH 续写」「角色扮演」按钮可见 | 可见 | plugin-core |
| E | P2 `ping` → `pong`（带 `appPath`） | 8s 内响应 | app-auth |
| E | P2 应用主动上报 `state` | 收到 `{appPath,hasContext}`（`report.bridge.state`） | app-auth |
| E | P2 `context-request` → `context` | 10s 内响应，`ok:true` | app-auth |
| E | context 字段齐全 | `appPath/worldTitle/lineTitle/mode/characters/recentText` 类型正确 | app-auth |
| F | 点「让 DSH 续写」 | 新会话打开 + 编辑器草稿匹配 `/续写/`（或剪贴板兜底），并含模板句「请基于以上内容续写下一段」 | plugin-core + app-auth |
| F | 点「角色扮演」 | 新会话 + 草稿/剪贴板匹配 `/【你要扮演】/` 且 `/第一人称即兴演绎/`（点前会清空剪贴板，避免复用续写提示词假阳性）；点前应先等到恢复后的 context | plugin-core + app-auth |
| G1 | 切到其它面板（紫微斗数）再点回「酒馆」 | iframe 深链回 `/tavern/worlds/…/play`、`data-dsh-tavern-restored=1`、P2 `state.hasContext=true`、context 仍 `ok:true` | plugin-core |
| G2 | 整页 reload DSH 后重新进面板 | 仍从 `sessionStorage` 恢复（`restored=1`，路径仍是故事页） | plugin-core |
| G3 | 点「刷新」 | iframe 回 `/tavern/`、`restored=0`、`sessionStorage` 键被删、应用自路由到 `/explore` | plugin-core |
| H | 页面无控制台 JS 报错 | 0 条（资源加载失败不算） | plugin-core |

### 自动化锚点（契约约定）

验收脚本优先用这些稳定锚点，不靠中文文案猜测：

| 锚点 | 位置 | 说明 |
| --- | --- | --- |
| `data-dsh-panel-entry="tavern"` | 侧栏行 | 脚本会点击它所属的 `button/a`（锚点可能在行内图标上） |
| `data-dsh-tavern-view` | 主面板根 | 面板是否存在 |
| `data-dsh-tavern-hint` | 主面板根 | `idle / loading / waiting / ready / busy / sent / error`；`error` = 插件走了降级分支 |
| `data-dsh-tavern-connected` | 主面板根 | `1/0`，是否收到应用 P2 state |
| `data-dsh-tavern-restored` | 主面板根 | `1/0`，本次挂载是否从位置记忆深链恢复 |
| `sessionStorage["dsh-tavern:last-app-path"]` | 浏览器存储 | 最后一次有效应用路由（`/worlds/…` 或 `hasContext=true` 的页面）；「刷新」会删除 |
| `data-dsh-tavern-status` | 状态文字 | 人话提示，原样写进报告 |
| `data-dsh-tavern-action="continue\|roleplay\|reload\|open"` | 工具条按钮/链接 | 点击目标 |
| `data-dsh-tavern-frame` | iframe | 内嵌应用 |
| `data-dsh-tavern-extra` | 续写要求输入框 | 可选输入 |

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
- **位置记忆**：DSH 关闭面板会卸载 iframe，应用重挂载默认回 `/explore`。插件把最后
  有效路由写进内存 + `sessionStorage`，重挂载时深链回去（`data-dsh-tavern-restored=1`）；
  「刷新」按钮 = 清记忆回 `/tavern/`（`restored=0`）；整页 reload DSH 后仍能恢复。
  E2E 的 G1/G2/G3 覆盖这三条路径，`report.restore` 里存证据。注意恢复的是 `pathname`，
  `?line=` 查询串不保留；世界页靠应用自身 store 选当前章，实测恢复后 context 仍 `ok:true`。
- **F2 优先靠记忆恢复**：重开面板后先等 iframe 回到 `/worlds/` 再等 context；记忆失效
  才退回「去 explore 重新打开作品」的兜底（`report.bridge.roleplayReopenedStory`）。
- 角色扮演成功时 DSH DOM 里可能同时留着上一会话的编辑器节点，`draft` 会包含续写+扮演两段
  提示词；`clipboard` 是干净的单条扮演提示词，报告里两个都记。模板断言会优先选**命中
  marker 的那份**（实测 `source=clipboard`：`请以角色扮演的方式，接着下面的故事即兴演绎。`），
  避免拿混合 `draft` 里的旧续写文本蒙混过关。
- **P2 命名空间是 `dsh-tavern:*`**（不是 `dsh-story:*`）；脚本注入的 `message` 监听和
  `report.bridge.protocol` 都按这个断言，用错命名空间会表现为「应用没有响应」超时。

## 手工探针（不启浏览器）

```bash
# 插件是否挂载（未登录；404 = 未挂载，200 = 闸门失效）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3080/tavern/

# 旧 /story 兼容别名（匿名可能先被闸门拦成 401；登录态应 302 到 /tavern/ 且保留 query）
curl -s -o /dev/null -D - 'http://127.0.0.1:3080/story/?probe=1' | head -5

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
| 120s 内没等到 token | 看 `$OUT/logs/dsh.log` 尾部（默认 `/tmp/dsh-tavern-verify/check/logs/`）；常见是 profile `package.json`/`node_modules` 不可用、bundle 解析失败 |
| 隔离实例登录/quota 异常 | 检查 `~/.dsh/.credentials.yaml` 是否复制成功（600） |
| 截图中文豆腐块 | 脚本必须 `export HOME=/root XDG_CACHE_HOME=/root/.cache`（systemd 单元里 HOME 为空会跳过 `/root/.fonts`） |
| Welcome/Get started 遮罩挡住侧栏点击 | verify 里 `dismissWelcome()` 关 `div[role="presentation"] button[aria-label="Close"]`；新 HOME 需要先复制 `workspace.json` |
| 上次的截图/报告串场 | 每次 E2E 前清 `$OUT`（脚本默认清理），或看 `report.startedAt` |
| 改了 `client.js` 要不要重启 | 浏览器半刷新页面即生效；`lib/index.js` 是宿主半，**必须重启 DSH** |
| 改了 `app/client` 没生效 | 需要重新 `npm run build`（`APP_BASE=/tavern`，产物到 `app/client/dist`），web 服务直接读 dist；不需要重启 DSH |
| `AUTH_REQUIRED`（trusted 头带了也 401） | 应用 API 进程还是旧代码：`systemctl restart dsh-tavern-api`（app-auth 改完 `config.py/security.py/api_auth.py` 后必须重启） |
| Playwright 起不来 | `createRequire('/root/workspace/hermes-site/tools/node_modules/')` 取全局 playwright；`chromium.launch({headless:true,args:['--no-sandbox']})` |
| 无模型凭据能跑吗 | 能。验收只开草稿不点发送，不触发模型调用 |

## 已知缺口（随进度更新）

- [x] **app-auth**：trusted 登录 + P2 桥（`dsh-tavern:ping/pong/state/context`）已部署；
  隔离实例 E2E **43 PASS / 0 FAIL / 0 SKIP**（`--open-story`，含位置记忆 G1–G3）。
- [x] **应用内要有「当前故事」**：`--open-story` 会在 explore 点「继续游玩」进入
  `/worlds/:id/play`；context 实测 `mode=world-play world=蓝宝石案 line=福尔摩斯的故事
  chars=12 recent=286字`。应用里当前有 4 部 published/live 作品（DB `product_publications`）。
- [x] **旧 /story 兼容别名**：登录态 GET 实测 `302 Location=/tavern/?probe=1&x=2`（query
  保留）；匿名先被闸门拦 401。POST 等不重定向、原地改写前缀（host 半单测覆盖）。
- [x] **面板切换丢应用路由**：位置记忆已验（G1 切紫微斗数再点回、G2 整页 reload、
  G3「刷新」清记忆全 PASS）。
- [ ] **生产 3080 还是旧插件进程**：生产 DSH 未重启，`/story` 仍是旧逻辑、P2 仍是旧消息名；
  本轮只验隔离 3099。重启生产后用带 token 的 URL 跑 `node verify.mjs --url ... --open-story`
  做生产冒烟（生产默认不写阅读进度，F 只验降级）。
- [x] **app/deploy 里的旧 unit 模板已删除**（连同 Caddyfile.new）；实际运行的 unit 是
  `/etc/systemd/system/dsh-tavern-api/web/worker/world-worker.service`，`app/deploy/` 仅保留
  `static_server.py`（静态站实现，host unit 直接调用）。
- [ ] 默认 `check.sh` 会 SKIP「应用前端 typecheck/单测」（`--with-app-tests` 才跑）；本轮静态
  阶段 SKIP 1 项即此项，浏览器 E2E 0 SKIP。
