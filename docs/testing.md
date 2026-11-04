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
scripts/check.sh --open-story --cards   # 全链路：打开作品 + 角色卡导入 + 单卡聊天（会写测试库）
scripts/check.sh --open-story --cards --real  # 真生成验收（--real 隐含 --cards）：回复必须是真模型生成、刷新后仍在（需应用 AI_MODE=dsh）
scripts/check.sh --instance --open-story --cards --space --real  # B 步角色卡空间 + 真实生成（--space/--real 都隐含 --cards）：S1–S6
scripts/check.sh --instance --open-story --cards --space --real --groups  # 上面全部 + B2 群聊（--groups 隐含 --cards）：T1–T6
scripts/check.sh --instance --open-story --cards --space --real --groups --memory  # 上面全部 + C 步长期伙伴与记忆（--memory 隐含 --cards）：C1–C6
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
`DSH_TAVERN_VERIFY_OUT` 覆盖。`--cards` 会先跑 `scripts/fixtures/make-cards.py` 生成
`/tmp/tavern-verify/cards/`（v2 PNG / v2 JSON / v1 JSON + `manifest.json`）；另生成
`v2-lore.json`（陆三更，带 `character_book.entries`，manifest 的 `space.lorebook` 标注
keys「茶」「雨」与可辨认 marker），仅供 `--space` 使用，不进 H 组基线。
`/tavern/` 还没挂载（404）时脚本不会崩：E2E 会输出
「未就绪」报告并 exit 1。

## `verify.mjs` 断言清单

产物：`report.json` + 截图 `01-sidebar.png`、`02-panel.png`、`03-app-iframe.png`、
`03b-tavern-open.png`、`04-continue-draft.png`、`05-roleplay-draft.png`、
`06-restore-panel.png`、`07-reload-restore.png`、`08-refresh-home.png`；
`--cards` 时另有 `09-cards-page.png`、`10-cards-imported.png`、`11-chat-open.png`、
`12-chat-reply.png`、`13-chat-after-reload.png`、`14-cards-deleted.png`。

`--real` 不新增截图，但 `report.real` 会记录：首句前 60 字、字数、耗时（ms）、模型名
（聊天消息 `<small>` 里的 `模型名 · 已保存`）、是否含角色名（仅信息，不作硬断言）、刷新后是否同一条、
console 是否干净；不记录 prompt / 回复全文。

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
| H1 | `--cards`：导航「角色卡库」→ `/characters` 页面可进入 | 出现「角色卡库」导航且路由存在 | cards-client |
| H1 | 角色卡库有导入入口 | `input[type=file]`（`accept=.png,.json`）存在 | cards-client |
| H2 | 逐张导入 PNG / v2 JSON / v1 JSON 卡 | 每次列表出现对应卡名（25s 内） | cards-client + app-auth |
| H2 | 卡头像渲染 | 页面 `img.naturalWidth > 0` ≥ 1（PNG 卡带 `blob:` 头像） | cards-client |
| H3 | 点「开聊」 | 进入 `/conversations/world/<sessionId>`，页面显示角色名 | cards-client + app-auth |
| H4 | 发「你好」 | 出现 `.world-chat-message.user` 消息，120s 内出现 `.world-chat-message.assistant` mock 回复 | app-auth（AI_MODE=mock）/ cards-client |
| H5 | 刷新聊天页（同源 iframe 内 reload） | 用户消息与回复仍在（`report.cards.reload`） | app-auth 持久化 + cards-client |
| H6 | 回角色卡库点「删除」 | 卡名数量回落到导入前基线（应用无确认弹层，点击即删；断言 `DELETE /character-cards/<id>` 返回 204 且列表刷新） | cards-client |
| H6 | 删除只删一张 | 其余 fixture 卡仍在列表 | cards-client |
| H7 | 页面无控制台 JS 报错 | 0 条（资源加载失败不算） | plugin-core |
| R1 | `--real`：回复不含 mock 特征句「我们可以聊聊此刻的想法」 | 不出现（出现 = 应用仍在 `AI_MODE=mock`） | app-auth（环境/worker） |
| R2 | `--real`：回复长度 ≥ 20 字且不是用户消息本身 | 真模型回复（`字数`、`首句` 写进 `report.real.reply`） | app-auth / plugin-core |
| R3 | `--real`：完整刷新 iframe 后同一条回复仍在 | `report.cards.reload.replyMatched === true`（按文本前 20 字比对，不只看条数） | app-auth 持久化 + cards-client |
| R4 | `--real`：console 无 JS 报错 | 0 条 | plugin-core |
| R5 | `--real-negative`：网关不可用时 UI 显示失败而不是假回复 | 默认 SKIP（需手动把网关弄坏）；显式演练时要求 `[role=alert]/[role=status]` 有失败文案、且没有 assistant 假回复 | app-auth / plugin-core |
| S1 | `--space`：卡详情把卡名改成「<名>改」并保存 | 刷新详情页后显示新名（输入框回显旧名才算可编辑） | cards-client（详情页/PATCH） |
| S2 | 导入 `v2-lore.json`（陆三更）→ 世界书列表 | 详情页「世界书」区块出现 2 条 `li.lore-item`，`【E2E-LORE-茶】`/`【E2E-LORE-雨】` 两条 marker 均命中，且带「导入」badge | app-auth（解析/落库）+ cards-client（展示） |
| S3 | 手动新增世界书条目（key「雨」，marker `【E2E-MANUAL-雨】`）→ 删除 | 新增后可见，删除后消失 | cards-client + app-auth（lore API） |
| S4 | 新增正则 `。`→`！` → 开聊发「你好，来一盏茶。今天茶馆生意好吗？」 | 落库回复刷新后 `!includes('。') && includes('！')`（流式气泡是替换前文本，以刷新后为准） | app-auth（regex 应用）+ cards-client（规则页） |
| S5 | 详情页「新建预设」→「创建并绑定」→ 注入预览填「试写一句对话」点「预览」 | 绑定后 select 选中「E2E 预设」，`.preview-result` 含「预设：E2E 预设」（即 context-preview 命中） | app-auth（presets/context-preview）+ cards-client |
| S6 | S 组操作后页面 console | 0 条 JS 报错（资源加载失败不算） | plugin-core / cards-client |
| T1 | `--groups`：角色卡库勾选两张卡 →「发起群聊」→ `/characters/groups/<id>` | 进群聊页，`.group-participant small` 含所选两张卡名，`.selection-count` 显示「已选 2 张」 | cards-client（选择/路由）+ app-auth（POST） |
| T2 | 发「你们好」 | 202 响应带 `speakerName`，UI `.group-message-speaker strong` 与之一致，回复为非 mock 文本（`report.groups.messages[0].reply`） | app-auth（群聊任务/轮转）+ cards-client |
| T3 | 再发「那你们谁先说说？」 | 新回复发言人 ≠ 第一条（按已保存 assistant 条数轮转），UI 与 API 一致 | app-auth（轮转逻辑） |
| T4 | iframe 内整页刷新群聊页 | 两条用户消息与两条回复文本仍在（`report.groups.persisted.reply1Matched/reply2Matched`） | app-auth 持久化 + cards-client |
| T5 | 返回群聊列表 →「删除」→「确认删除」 | 本群聊从列表消失，列表数量回落（`report.groups.deleted.before/after`） | cards-client + app-auth（DELETE） |
| T6 | T 组操作后 console | 0 条 JS 报错（资源加载失败不算） | plugin-core / cards-client |
| C1 | `--memory`：卡详情「长期记忆」→「新增记忆」（标题「读者养猫」/内容「读者养了一只叫团子的猫」/权重 70）→「保存记忆」→「编辑记忆」改标题 →「删除记忆」→「确认删除」 | 保存后列表出现「读者养猫」（手记 badge），编辑后标题变「读者养猫（改）」且列表更新，删除后条目消失；随后重新建回「读者养猫」供 C3 | cards-client（记忆表单/列表）+ app-auth（memories CRUD） |
| C2 | 先开聊发两条含自述的消息（「我叫林舟，最近养了一只叫团子的猫…」「我每天早上都要喝一杯手冲咖啡…」），等回复 saved 后回卡详情点「立即整理」 | `[role=status]` 出现「新增 N 条记忆」（N≥1），`li.memory-item` 数量增加（`report.memory.extract`） | app-auth（同步抽取/去重）+ cards-client |
| C3 | 注入预览「试写一句对话」→「预览」 | `.preview-result` 的「注入的长期记忆：」行包含「读者养猫」（`report.memory.preview.containsTitle`） | app-auth（context-preview memories/memorySection）+ cards-client |
| C4 | 记忆「读者养猫」→「停用」→ 再预览；再「删除记忆」→「确认删除」→ 再预览 | 停用后 preview 的注入记忆不含该标题；删除后再预览仍不含（`report.memory.disabled/deleted`） | app-auth（enabled 过滤）+ cards-client |
| C5 | 卡详情勾选「设为长期伙伴」→ 回角色卡库 | 卡库顶部出现「长期伙伴」分组，该卡是分组第一张（`data-card-id` 一致）且带「长期伙伴」badge（`report.memory.companion`） | cards-client（分组/置顶）+ app-auth（companion 字段） |
| C6 | C 组操作后 console | 0 条 JS 报错（资源加载失败不算） | plugin-core / cards-client |

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
| 导航「角色卡库」/ `a[href="/characters"]` | 应用 shell | `--cards` 入口（文本与 href 双候选） |
| `input[type="file"]` | 角色卡库 | 导入 PNG/JSON（`setInputFiles`） |
| `article.character-card[data-card-id]` | 角色卡列表 | 首选卡片容器；`filter({ has: getByText(name,{exact}) })` 找卡，再点其中「开聊」「删除」；找不到再退 xpath ancestor |
| `#world-chat-message` | 聊天页 | 输入框（`textarea`） |
| `form.world-chat-compose button[type="submit"]` | 聊天页 | 发送按钮 |
| `.world-chat-message.user p` / `.world-chat-message.assistant p` | 聊天页 | 用户消息 / 回复 |
| `[role="alert"]` | 聊天页 | 发送失败/超时提示，原样写进失败详情 |

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
- **`--cards` 的 mock 回复**：应用 `AI_MODE=mock`，发送「你好」后由应用 mock worker
  生成确定性回复，不走真实模型；但仍要轮询任务完成（最长 120s），不能只等 DOM 变化。
  刷新验证用 iframe 内 `location.reload()`（同源），不要整页 reload DSH——那会走 G2 的记忆恢复路径。
- **发送按钮是异步 disabled 的**：`textarea#world-chat-message` fill 之后 Vue 还没把 Draft 落进
  DOM，此时提交按钮 `disabled`；直接 `click({force:true})` 会命中 disabled 按钮、浏览器静默不派发
  click，表现为「没有报错但消息没发出去」。脚本先轮询 `isDisabled()===false`（8s）再普通 click，
  仍不可用则 `press('Control+Enter')` 兜底（app 的 textarea 有 Ctrl/Cmd+Enter 发送）。
- **角色卡删除没有确认弹层**：`CharactersPage.remove()` 直接调 `DELETE`，成功后 toast
  「已删除…」并 `load()` 刷列表；失败时 `p.error[role=alert]` 显示「删除失败：…」。脚本用
  `page.on('response')` 记录 DELETE 的 `{pathname,status}`，「消失」以基线计数回落判断；
  删除是软删（卡从列表消失、chatTarget 的 world/聊天保留）。
- **角色卡 E2E 会写应用数据**：导入会建 World/WorldLine/root Checkpoint，删除是**软删**
  （卡从列表消失、world 保留）。仅用于本地单用户验收库；生产冒烟不要加 `--cards`
  除非确认允许写入。
- **P2 命名空间是 `dsh-tavern:*`**（不是 `dsh-story:*`）；脚本注入的 `message` 监听和
  `report.bridge.protocol` 都按这个断言，用错命名空间会表现为「应用没有响应」超时。

## `--real` 真生成验收（task-12 / step2）

```bash
scripts/check.sh --instance --cards --real
# 完整全链路：scripts/check.sh --instance --open-story --cards --real
```

前置（缺一项就会在 H9/R 组暴露，不会静默通过）：

1. 应用 `app/.env`：`AI_MODE=dsh`、`DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1`、
   `DSH_AGENT_SECRET=<secret>`（插件侧 `DSH_TAVERN_AGENT_SECRET` 同一值）。
2. 生产 DSH（3080）已加载新插件，`/tavern-agent/v1/health` 带 Bearer 返回 `{"ok":true}`。
   注意：隔离 E2E 的 UI 在 3099，但应用 worker 调的是 **3080** 的网关（`DSH_AGENT_URL`），
   所以 3080 没重启时 `--real` 会表现为回复超时/UI 失败，而不是 verify 脚本自身崩。
3. `dsh-tavern-api` + `dsh-tavern-worker` 重启过（`AI_MODE=dsh` 才会走 `DshAgentAdapter`）。

`check.sh --real` 的前置检查只做只读探测（读 `app/.env` + 带 secret 调 health），
不可用时记 SKIP 并说明 owner；真正断言的还是浏览器里的 H9 + R1–R4。

通过标准：

- 原有 A–H 43/55 项仍全绿；
- R1 不出现 mock 特征句；R2 回复 ≥20 字；R3 刷新后同一条回复仍在；R4 console 干净；
- R5 默认 SKIP（原因：需要临时把 `DSH_AGENT_URL` 指向坏端口并重启 api/worker，属侵入式环境改动）。

`--real` 的等待时间放宽：发送后最多等 **180s** 拿到「已保存」标记（真生成 10–60s 属常态，
含网关排队）；「刷新后仍在」轮询也放宽到 45s。mock 模式仍是 120s/30s，不受影响。

实测（2026-11-04，隔离实例 3099 + 生产 app 8001，app/.env 指向 3099 网关）：
`--open-story --cards --real` → 59 PASS / 0 FAIL / 1 SKIP，回复 130 字、耗时约 30.7s、
模型名 `DSH 模型`（网关侧为 `opencode-go/deepseek-v4.1-flash`），刷新后同一条回复仍在。

### `--real-negative`（可选，负向演练）

用于验收「诚实失败」：网关不可用时 UI 必须显示失败/未收到回复，**不能**伪造一条回复。
脚本不会自己去改环境，需要手动制造故障后显式加参数：

```bash
# 1) 临时把 app/.env 的 DSH_AGENT_URL 指向坏端口（例如 http://127.0.0.1:9/tavern-agent/v1）
# 2) systemctl restart dsh-tavern-api dsh-tavern-worker
scripts/check.sh --instance --cards --real-negative
# 3) 恢复 app/.env 的 URL 并重启 api/worker，清理测试卡
```

期望：R5 PASS（`[role=alert]/[role=status]` 有失败文案、没有 assistant 假回复），
H9 记为「网关不可用场景：UI 已显示失败」。演练结束后务必恢复环境。

## `--space` 角色卡空间验收（task-15 / step3）

验收 step3（`docs/step3-card-space.md` §6）的卡空间能力，全部在测试卡上操作：

```bash
# 完整验收（S1–S6 + 真实生成；--space/--real 都隐含 --cards）
scripts/check.sh --instance --open-story --cards --space --real
```

前置：task-13（app-auth：`/character-cards/{id}/lore|presets|regex-scripts|context-preview`）
和 task-14（cards-client：卡详情 `/characters/:id`、世界书/预设/正则页面）已就绪并重启，
且 `app/.env` 是 `AI_MODE=dsh` + 可用 `DSH_AGENT_URL`（生产网关 3080）。
**未就绪时** S1–S5 逐项 SKIP 并写明 owner（cards-client / app-auth），脚本不会 FAIL，
也不会影响既有 59 项；`--space` 隐含 `--cards`（需要 H 组的导入/开聊/聊天基础）。

通过标准：

- 原有 59 项（含 R1–R4 真实生成）仍全绿；
- S1–S6 共 6 项全 PASS，不允许 SKIP（唯一允许的 SKIP 是 R5 负向演练）；
- `report.space` 记录：fixture、cardName/cardId/detailUrl、renamed、lore（导入条目/marker 命中/
  手工条目增删）、regex（规则与回复首句/替换结果）、preset（绑定 + 注入预览命中）、console；
  聊天只写回复前 60 字与字数，不写 prompt 全文。

**2026-11-04 实测（B1 后端 + cards-client 就绪后）**：

```
bash scripts/check.sh --instance --open-story --cards --space --real
→ 浏览器 65 PASS / 0 FAIL / 1 SKIP；静态 7 PASS / 0 FAIL / 0 SKIP
report: /tmp/dsh-tavern-verify/check/report.json（ok=true）
截图：15-space-detail.png、16-space-lore.png、17-space-lore-manual.png、
      18a-space-regex.png、18-space-regex-chat.png、19-space-preset.png（+ 既有 01–14）
```

- S1 旧名陆三更 → 陆三更改，刷新后回显新名（notice「角色设定已保存」）；
- S2 世界书 2 条、`【E2E-LORE-茶】`/`【E2E-LORE-雨】` 均命中、导入 badge=2；
- S3 key「雨」手工条目新增可见、删除后消失（aria-label「删除世界书条目」→「确认删除」）；
- S4 规则「E2E 句号替换」（`。`→`！`）落库；真实回复 124 字，刷新后无「。」且含「！」
  （流式气泡显示替换前文本，刷新后为落库文本——与服务端 `apply_regex` 行为一致）；
- S5 绑定「E2E 预设」后注入预览显示「预设：E2E 预设」；
- S6 console clean；唯一 SKIP 是 R5（`--real-negative` 手动演练）。

S4 依赖模型回复里真的出现「。」，所以保留了 `report.space.chat.replyFirst60` 与
`afterRefreshFirst60` 供归因；若真实模型恰好不用句号，换一条用户消息重跑即可，不是产品 bug。

**跑完必须清理测试数据**（生产库是真实用户库）。UI 删除只覆盖 H 组删掉的那张卡，
S 组导入/改名/世界书/会话/预设/正则都要清，基线应回到
`cards=0 / instances=6(character-card 世界=0) / messages=0 / sessions=0 / lore=0 /
presets=0 / regex_scripts=0`：

```bash
export PGPASSWORD=storybetween2026
psql -h 127.0.0.1 -U reader -d story_between -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
CREATE TEMP TABLE tw AS SELECT id FROM world.instances WHERE source->>'kind'='character-card';
CREATE TEMP TABLE tc AS SELECT id FROM world.character_cards;
DELETE FROM public.world_chat_messages WHERE session_id IN
  (SELECT id FROM public.world_chat_sessions WHERE world_id IN (SELECT id FROM tw));
DELETE FROM public.world_chat_sessions WHERE world_id IN (SELECT id FROM tw);
DELETE FROM world.card_lore WHERE card_id IN (SELECT id FROM tc);
DELETE FROM world.character_cards WHERE id IN (SELECT id FROM tc);
DELETE FROM world.checkpoints WHERE world_id IN (SELECT id FROM tw);
DELETE FROM world.lines WHERE world_id IN (SELECT id FROM tw);
DELETE FROM world.instances WHERE id IN (SELECT id FROM tw);
DELETE FROM world.chat_presets WHERE name = 'E2E 预设';
DELETE FROM world.regex_scripts WHERE name = 'E2E 句号替换';
COMMIT;
SQL
rm -f app/data/private/*/card-avatar-*.png
```

清理后再核对一遍计数，并确认隔离实例已退出（`curl 127.0.0.1:3099` 连接失败、`/tmp/dsh-tavern-check` 不存在）。

## `--groups` 群聊验收（B2 / step3b）

验收 step3b（`docs/step3b-group-chat.md` §5）的角色卡群聊能力。全部用 fixture 测试卡，
T5 会通过 UI 删掉本轮创建的群聊；结束后再按上面的清理 SQL 清残留：

```bash
scripts/check.sh --instance --open-story --cards --space --real --groups
```

前置：B1（卡空间）与 B2 后端已部署（`/api/reader/v1/card-group-chats` 系列），
客户端 dist 含 `/characters/groups` 与 `/characters/groups/:id`，`AI_MODE=dsh` 且网关可用。
`--groups` 隐含 `--cards`（需要 H 组先导入两张以上测试卡）；未就绪时 T1–T5 SKIP 并写 owner，
S/T 都不会让既有检查项变 FAIL。

通过标准：

- 既有 65 项（含 S1–S6、R1–R4）不回归；
- T1–T6 全 PASS，不允许 SKIP（唯一允许的 SKIP 是 R5 负向演练），即 **71 PASS / 0 FAIL / 1 SKIP**；
- `report.groups` 记录：selected/selectionCount、group{id,url,participants}、messages[]（用户消息、
  202 响应 speakerName、会话 id、回复前 100 字、耗时）、rotation{first,second,shown,nextSpeakerText}、
  persisted{userMessages,assistantMessages,reply1Matched,reply2Matched}、deleted{before,after}、console。
- 截图：`20-groups-select.png`、`21-group-reply.png`、`22-group-rotate.png`、
  `23-group-reload.png`、`24-group-deleted.png`。

失败归因：speaker 不轮转/任务不落库/刷新丢失 → app-auth；多选、发起群聊路由、列表删除、
发言人/气泡 DOM → cards-client；只有 harness 自身（选择器/等待）才算 verifier。

**2026-11-04 实测（B2 后端 + cards-client 就绪后）**：

```
bash scripts/check.sh --instance --open-story --cards --space --real --groups
→ 浏览器 71 PASS / 0 FAIL / 1 SKIP（唯一 SKIP = R5 负向演练）；静态 7 PASS / 0 FAIL / 0 SKIP
report: /tmp/dsh-tavern-verify/check/report.json（ok=true）
截图：20-groups-select.png、21-group-reply.png、22-group-rotate.png、
      23-group-reload.png、24-group-deleted.png（+ 既有 01–19）
```

- T1 选中=顾清和、沈夜舟（选择栏「已选 2 张」），进 `/tavern/characters/groups/<id>`，
  参与者 `.group-participant small` 与所选一致；
- T2 发「你们好」→ HTTP 202 `speakerName=顾清和`，UI 发言人一致，真生成 115 字、约 30.9s；
- T3 再发「那你们谁先说说？」→ 202 `speakerName=沈夜舟`，UI 发言人一致，
  `.group-next` 显示「下一位：沈夜舟」，轮转正确（已保存 assistant 条数取模）；
- T4 iframe 内整页刷新后 `user=2 assistant=2`，两条回复文本均匹配（`reply1Matched/reply2Matched=true`）；
- T5 返回群聊列表 → 删除 → 列表 1 → 0；
- T6 console clean。
- 证据细节在 `report.groups`：messages[].{clickPath,httpStatus,response.speakerName,reply.text,elapsedMs}、
  rotation、persisted、deleted、console。

**踩坑（已在 harness 处理）**：DSH 桌宠气泡偶尔浮在 iframe 右下角，正好盖住群聊「发送」按钮，
`click({force:true})` 会点在遮罩上、表单不提交（表现为没有 POST、UI 无反应）。T 组发送逻辑先正常点，
6s 内没有 POST 就退化为 `button.evaluate(el => el.click())`（仍走应用 send() 逻辑），
并把实际路径记在 `messages[].clickPath`（`click` / `js-click`）。这是测试环境浮层问题，不是群聊产品 bug。

**跑完必须清理**（见上一节 SQL，另需删群聊表；基线 `group_chats=0 / group_messages=0`）：

```sql
DELETE FROM world.card_group_messages;
DELETE FROM world.card_group_chats;
```

## `--memory` 长期伙伴与记忆验收（C / step4）

验收 step4（`docs/step4-companion-memory.md` §7）的长期记忆抽取/注入与「长期伙伴」置顶：

```bash
scripts/check.sh --instance --open-story --cards --space --real --groups --memory
```

前置：task-19（app-auth：`/character-cards/{id}/memories` CRUD、`/memories/extract`、`/companion`、
context-preview 的 `memories/memorySection`）与 task-20（cards-client：卡详情「长期记忆」区、
角色卡库「长期伙伴」分组）已部署；`AI_MODE=dsh` 且网关可用（extract 是同步模型调用，需要真生成链路）。
`--memory` 隐含 `--cards`。注意：`POST /memories` 请求体只能带 `kind/title/content/salience/enabled`，
带 `source` 会 422（服务端字段只读）；`extract` 依赖卡最近会话消息，因此 C2 先发两条含自述的消息。

通过标准：

- 既有 71 项（含 S/T 组、R1–R4）不回归；
- C1–C6 全 PASS，不允许 SKIP（唯一允许的 SKIP 是 R5 负向演练），即 **77 PASS / 0 FAIL / 1 SKIP**；
- `report.memory` 记录：fixture/cardName/cardId/detailUrl、chat.messages（自述消息 + 回复前 60 字/耗时）、
  manual{created,edited,deleted,recreated,error}、extract{before,after,notice,added}、
  preview{containsTitle,memoryLine}、disabled/deleted（preview 是否仍含标题）、
  companion{checked,heading,inTopGroup}、console。
- 截图：`25-memory-manual.png`、`26-memory-extract.png`、`27-memory-preview.png`、
  `28-memory-disabled.png`、`29-companion-library.png`。

失败归因：extract 不返回/新增 0 条、preview 不含记忆、companion 字段不生效 → app-auth；
记忆表单/列表/删除确认、注入预览记忆区块、卡库分组与 badge DOM → cards-client；
只有 harness 自身（选择器/等待/时序）才算 verifier。

**跑完必须清理**（基线 `memories=0 / cards=0 / chats=0`）：先删 `world.card_memories`
（卡被删后记忆随之清掉也行，但显式清理更直观），再按上一节 SQL 清卡/会话/世界/avatar：

```sql
DELETE FROM world.card_memories;
```

**2026-11-04 实测（C 后端 task-19 + cards-client task-20 就绪后）**：

```
bash scripts/check.sh --instance --open-story --cards --space --real --groups --memory
→ 浏览器 77 PASS / 0 FAIL / 1 SKIP（唯一 SKIP = R5 负向演练）；静态 7 PASS / 0 FAIL / 0 SKIP
report: /tmp/dsh-tavern-verify/check/report.json（ok=true，report.memory 完整证据）
截图：25-memory-manual.png、26-memory-extract.png、27-memory-preview.png、
      28-memory-disabled.png、29-companion-library.png（+ 既有 01–24）
```

- C1 手工记忆「读者养猫」新增可见、`编辑记忆` 改标题生效、`删除记忆`→`确认删除` 后消失、
  再重建成功（`report.memory.manual`）；
- C2 前置聊天两条含自述的消息（真实回复各约 39s）；点「立即整理」→ `新增 3 条记忆。`，
  列表 1 → 4 条（`report.memory.extract`），抽取内容含「读者姓名 / 晨间手冲咖啡 / 偏爱安静环境」；
- C3 注入预览「注入的长期记忆：读者姓名、读者养猫、晨间手冲咖啡、偏爱安静环境」，
  包含手工标题「读者养猫」；
- C4 停用「读者养猫」后预览不再包含（`containsTitle=false`）；删除后再预览仍不含；
- C5 勾选「设为长期伙伴」→ 卡库出现「长期伙伴」标题，该卡 `data-card-id` 置顶且带 badge；
- C6 console clean。
- 证据细节在 `report.memory`：chat.messages[]（自述消息、回复前 60 字、耗时）、
  manual/extract/preview/disabled/deleted/companion、console。

**踩坑（已在 harness 处理）**：
- T 组结束后 iframe 停在 `/characters/groups`，`openCharactersPage()` 的粗匹配会把群聊页当成
  卡库 → C 组用 `memoryLibrary()` 强制落到 `/tavern/characters` 再等卡片渲染；
- 聊天后重新进卡详情时 Vue 还在挂载，直接 `count()` 会误判「没有『立即整理』按钮」→
  先等「长期记忆」区渲染（20s）；
- 注入预览是异步请求，停用/删除前后两次结果文本可能相同，不能只靠「文本变化」判断完成 →
  监听 `context-preview` 响应后再读 `.preview-result`。

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
  隔离实例 E2E **43 PASS / 0 FAIL / 0 SKIP**（`--open-story`，含位置记忆 G1–G3）；
  加 `--open-story --cards` 后 **55 PASS / 0 FAIL / 0 SKIP**（含 H 组角色卡导入 + 单卡聊天）；
  只加 `--cards` 不加 `--open-story` 时，D/E/F 上下文类检查与位置记忆项按预期 SKIP（不 FAIL）。
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
- [x] **角色卡导入 + 单卡聊天（task-9）**：`dsh-tavern-api` 于 02:13 重启后
  `/character-cards` 路由已生效，dist 02:14 重建（含 `/characters`）；`--open-story --cards`
  实测 **55 PASS / 0 FAIL / 0 SKIP**。证据：PNG/v2 JSON/v1 JSON 三张卡都出现在列表、PNG 卡
  头像为 `blob:` 渲染；开聊进 `/tavern/conversations/world/<sessionId>`；发「你好」后 mock
  回复（`我是林小满…这段对话不会替你交接宝石…`）；iframe 内 reload 后
  `user=1 assistant=1`；删除返回 `DELETE 204`、卡名回落到基线、其余卡仍在（`report.cards`
  里 `chat/reload/deleted/deleteDiagnostics` 全部留证）。fixture 的 DB 写入是预期的，结束后由
  Lead 清理测试卡（多轮调试会累积同名卡；脚本已用「基线计数」而非绝对数量断言）。
- [ ] 默认 `check.sh` 会 SKIP「应用前端 typecheck/单测」（`--with-app-tests` 才跑）；本轮静态
  阶段 SKIP 1 项即此项，浏览器 E2E 0 SKIP；`--cards` 时新增 1 项 fixture 生成 + 12 项 H 组；
  `--space` 时再加 6 项 S 组（预期 65 PASS / 1 SKIP），`--groups` 时再加 6 项 T 组
（预期 71 PASS / 0 FAIL / 1 SKIP），`--memory` 时再加 6 项 C 组（预期 **77 PASS / 0 FAIL / 1 SKIP**，
唯一 SKIP 是 R5）。
- [ ] **生产冒烟**：等生产 3080 重启加载新插件后，用带 token 的 URL 跑
  `node verify.mjs --url ... --open-story`（生产默认不加 `--cards`，避免往生产库写卡）。
- [x] **真生成（step2 / task-12，隔离环境）**：2026-11-04 实测 `scripts/check.sh --instance
  --open-story --cards --real` → **59 PASS / 0 FAIL / 1 SKIP**（R5 负向演练默认 SKIP）。
  `report.real.reply`：`modelName=DSH 模型`、`length=130`、`elapsedMs≈30.7s`、`completed=true`；
  刷新后 `replyMatched=true`；console clean；回复为中文角色化文本，不含 mock 特征句。
  遗留：每轮会向共享库导入 3 张 fixture 卡（只删除本轮 PNG 卡），测试库会累积；生产 3080 尚未重启。
- [ ] **真生成（生产 3080）**：等 Lead 重启生产 DSH / 切换生产 `AI_MODE=dsh` 后，用
  `node verify.mjs --url "http://127.0.0.1:3080/?token=…" --open-story --cards --real` 或
  `scripts/check.sh --real`（指向生产实例）再验一次；当前不能引用任何生产真生成结论。
- [x] **群聊（B2 / step3b）验收（task-18，隔离环境）**：2026-11-04 全量
  `--instance --open-story --cards --space --real --groups` → **71 PASS / 0 FAIL / 1 SKIP**（R5 手动演练），
  既有 65 项不回归；T1–T6 全 PASS（T2 顾清和 115 字 / T3 沈夜舟 227 字、轮转与 speakerName 一致、
  刷新后 2 用户 + 2 回复仍在、删除后列表 1→0、console clean）。测试群聊由 T5 经 UI 删除，
  跑完仍需按清理 SQL 清测试卡/会话/世界书/预设/正则，基线 `cards=0 / groups=0 / chats=0 / instances=6`。
  遗留风险：发送按钮可能被 DSH 桌宠气泡遮挡（harness 已有 js-click 兜底）；真实生成依赖
  `app/.env` 的 `DSH_AGENT_URL` 指向当前隔离实例端口，切生产前要改回 3080。
- [x] **长期伙伴与记忆（C / step4）验收（task-21，隔离环境）**：2026-11-04 全量
  `--instance --open-story --cards --space --real --groups --memory` → **77 PASS / 0 FAIL / 1 SKIP**
  （R5 手动演练），既有 71 项不回归；C1–C6 全 PASS（手工记忆增删改、extract 新增 3 条、
  preview 含记忆标题、停用/删除后不含、长期伙伴置顶、console clean）。
  跑完已清 `world.card_memories` 及测试卡/会话/世界/群聊/avatar，基线
  `cards=0 / memories=0 / groups=0 / chats=0 / lore=0 / presets=0 / regex=0 / instances=6 / worlds=6`。
  遗留风险：C2 的「立即整理」是同步模型调用（依赖 `app/.env` 的 `DSH_AGENT_URL=…:3080/tavern-agent/v1`
  与生产网关），若网关不可用 C2 会 FAIL 并归因 app-auth/网关；C5 的 companion 状态残留在
  fixture 卡上，随清理 SQL 一并删除。
- [ ] **`--with-app-tests`**：应用侧 typecheck/单测归 app-auth/cards-client 自己跑，验收脚本
  默认不跑；需要时可 `scripts/check.sh --static --with-app-tests`。
