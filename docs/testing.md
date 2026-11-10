# 测试与验收（dsh-tavern / 酒馆 2.0 原生流程）

本插件是「DSH Web 里的酒馆」：宿主半 `lib/index.js` 把本地应用挂到 `/tavern`
前缀并复用 DSH 登录闸门；浏览器半 `client.js` 加侧栏入口「酒馆」和主面板 iframe。
酒馆 2.0 改成**原生酒馆**流程（角色卡 → 全屏聊天 → 多版本回复），旧的探索 / 世界 /
长期记忆流程已删除；群聊在 P3 以「角色卡库多选 2–5 张卡发起群聊」的形式回归（G 组）。验收因此必须同时覆盖 **HTTP 闸门**、**DSH Web UI** 和
**iframe 内的原生应用**，只看单测或构建通过不算数。

设计见 [step6-native-tavern.md](./step6-native-tavern.md)，集成契约见
[contract.md](./contract.md)；本文只讲怎么验、怎么读结果、有哪些坑。

## 验收入口

### `scripts/check.sh` —— 隔离验收（日常用，绝不动生产 3080）

```bash
cd /root/workspace/plugins/dsh-tavern

scripts/check.sh                  # 静态检查 + /tmp 隔离实例 + 真实浏览器 E2E（默认）
scripts/check.sh --static         # 只跑静态检查（秒级）
scripts/check.sh --instance       # 只跑隔离实例 + E2E
scripts/check.sh --with-app-tests # 额外跑应用前端 typecheck + 单测（慢）
scripts/check.sh --keep-home      # 保留 /tmp/dsh-tavern-native 供排查
scripts/check.sh --port 3199      # 3099 被占用时换端口
```

隔离实例细节：

| 项 | 值 |
| --- | --- |
| 临时 HOME | `/tmp/dsh-tavern-native/instance`（退出时删除，`--keep-home` 保留；`DSH_TAVERN_CHECK_HOME` 可覆盖） |
| profile | `check`（`$CHECK_HOME/profiles/check`） |
| 端口 | 默认 `3099`，与生产 `3080` 不冲突（`DSH_TAVERN_CHECK_PORT` 可覆盖） |
| 依赖 | 生产 profile 已安装 `dsh-tavern` 时 **mirror**：符号链接其 `package.json` / `node_modules`，复制 `cordis*.yml`；未安装时 **dev**：生成 profile `package.json` 把插件源目录 link 进 `node_modules` |
| 登录态 | 复制 `~/.dsh/.credentials.yaml`（chmod 600）和 `storages/workspace.json`，避免首次启动弹 Get started / Choose a workspace |
| 应用数据 | 生产 app 用 `APP_BASE=/tavern`、API `8001`、web `8810`，`AI_MODE=dsh`；隔离实例只换 DSH 宿主，应用与生产网关仍是同一份（生成会消耗真实模型额度，测试数据跑完必须清理） |
| 产物 | `/tmp/dsh-tavern-native/check/report.json`（逐项 PASS/FAIL/SKIP）、`check/logs/*.log`（静态命令与隔离实例日志）、`check/90-*.png` … `107-*.png` 截图、`cards/` fixture（角色卡 + chat-sample.jsonl） |

浏览器任务建议放在独立 cgroup 里跑，避免被 DSH 自身的内存护栏牵连：

```bash
systemd-run --scope --unit=tavern-verify-$(date +%H%M) --property=MemoryMax=2G \
  --setenv=HOME=/root --setenv=XDG_CACHE_HOME=/root/.cache \
  bash -c 'cd /root/workspace/plugins/dsh-tavern && bash scripts/check.sh'
```

### 直接跑浏览器部分（调试用）

```bash
node verify.mjs --url "http://127.0.0.1:3080/?token=..." \
  --out /tmp/dsh-tavern-native/check --fixtures /tmp/dsh-tavern-native/cards
DSH_TAVERN_ONLY=a,n,c node verify.mjs ...   # 只跑某几组（a/n/c/r/h/s/m）
```

`verify.mjs` 的分组：`a` 基础、`n` 角色列表、`c` 聊天/流式 + 停止生成、`r` 重新生成、
`p` swipe 版本切换、`t` 标题改名、`e` 编辑/删除消息、`h` 聊天历史、`s` 世界书 + 注入预览、
`i` 导入导出（JSONL/JSON）、`g` 群聊（多选建群/轮转/刷新/列表/删除）、`m` 旧路由下线；
不传 `--only` 时全跑。

## 断言分组

| 组 | 断言 | 证据字段 |
| --- | --- | --- |
| A1 | 未登录 `GET /tavern/` 必须 401 | `checks[]` |
| A2 | 侧栏出现「酒馆」入口；点击打开主面板；`iframe[data-dsh-tavern-frame]` 可见且加载出应用（不是应用自己的登录页） | `screenshots` 90/91 |
| A3 | 原生酒馆 UI 已交付：极简布局 + 角色卡网格，无旧探索入口（`.shell-tabs` / `.discovery-page` / 「探索作品」） | `native.ready` / `native.oldUi` |
| N1 | 首页显示角色卡网格，有导入入口 | `native.home` |
| N2 | 导入 fixture 卡（PNG v2）后列表出现卡名与头像 | `native.home.imported/avatar` |
| N3 | 角色列表搜索过滤 | `native.home.search` |
| C1 | 点卡进入 `/chats/<id>`（或角色页 → 开始聊天） | `native.chat.chatUrl` |
| C2 | 发送「你好」后出现用户气泡 | `native.chat.userShown` |
| C3 | assistant 文本流式增长：250ms 采样至少 2 个不同长度，最终保存 | `native.chat.samples/distinct/elapsedMs` |
| C4 | 刷新后消息仍在（落库） | `native.chat.persisted` |
| C5 | 流式中出现「停止生成」按钮，点击后停止且不报错 | `native.stop` |
| R1 | 最后一条 assistant「重新生成」→ 文本变化、消息数不重复 | `native.regenerate` |
| P1 | 版本切换：`1/1 → 2/2` 后点 ‹ 切旧版本（文本变化）→ 刷新保持 → 点 › 切回最新 | `native.swipe` |
| E1 | 编辑 user 消息保存后文本更新 | `native.edit.edited` |
| E2 | 删除一条消息后列表减少、无 alert | `native.edit.deleted` |
| T1 | 点击聊天标题改名保存，刷新后保持 | `native.title` |
| H1 | 返回角色页看到聊天列表 | `native.history.listShown` |
| H2 | 从历史重新进入聊天，消息完整 | `native.history.reentered/messagesIntact` |
| S1 | 卡详情世界书区块可编辑 | `native.worldInfo.sectionShown` |
| S2 | 新增条目（key「茶」，position after_char）后列表可见 | `native.worldInfo.added` |
| S3 | 注入预览：输入示例消息 → 出现命中条目/injection 文本（无预览入口时 SKIP + owner） | `native.worldInfo.preview` |
| I1 | 导出聊天 JSONL：触发下载、逐行 JSON 可解析、含 `mes` 字段 | `native.exportImport.exportJsonl` |
| I2 | 导出聊天 JSON：下载并可解析出 `messages` 数组 | `native.exportImport.exportJson` |
| I3 | 卡详情导入 fixture `chat-sample.jsonl`（2 条消息）→ 历史新增、自动进入会话、条数正确 | `native.exportImport.import` |
| M1 | 旧路由 `/explore`、`/works`、`/worlds/scripts`、`/conversations/world/x` 全部下线（落 `/` 或 404，无死链） | `native.oldRoutes.routes` |
| M2 | 页面无「探索作品」等旧入口 | `native.oldRoutes.deadLinks` |
| G | 浏览器控制台无 JS 报错（过滤 `Failed to load resource`） | `checks[]` |
| W1 | 世界书条目 position=after_char / depth=2 / constant / probability / order 保存后列表回显 | `native.worldInfoDeep.entry` |
| W2 | 注入预览命中条目并显示原因（关键词/常驻） | `native.worldInfoDeep.preview` |
| W3 | context-preview API 命中条目按 order 返回，position/depth 生效，injection 包含条目文本 | `native.worldInfoDeep.api` |
| S2-1 | 聊天页菜单可打开「采样参数」对话框 | `native.sampling.dialog` |
| S2-2 | 采样参数 temperature/topP/maxTokens 保存后刷新回显 | `native.sampling.persisted` |
| S2-3 | 带采样参数发送一条消息不报错（真实生成 +1） | `native.sampling.send` |
| Q1 | 设置页可打开预设管理 | `native.presets.steps` |
| Q2 | 新建预设（system/temperature/maxTokens）并出现在列表 | `native.presets.created` |
| Q3 | 预设设为默认且刷新保持 | `native.presets.defaulted` |
| Q4 | 新聊天使用默认预设（`GET prompt?chatId=` 包含预设文本） | `native.presets.prompt` |
| Q5 | 非法采样值被拒绝（前端拦截或后端 422） | `native.presets.invalid` |
| P3-1 | 首页（非聊天页）工具条：续写/角色扮演按钮 disabled，状态提示「先打开一个聊天」 | `native.bridge.home` |
| P3-2 | 聊天页工具条检测到上下文：两按钮 enabled，状态显示「当前：/chats/<id>」 | `native.bridge.chat` |
| P3-3 | 点「让 DSH 续写」→ DSH 会话输入框预填，含角色名与「继续写下一段回复」 | `native.bridge.continuePrompt` |
| P3-4 | 点「角色扮演」→ 输入框预填，含「身份扮演」与角色名 | `native.bridge.roleplayPrompt` |
| P3-5 | 位置记忆：聊天页刷新仍在该聊天；回首页刷新应用 iframe 仍停在首页；console 干净 | `native.bridge.memory/console` |
| G1 | 角色卡库多选两张卡 → 点「发起群聊（2）」→ 进入 `/chats/<id>`，页头出现两个参与者（名字/头像） | `native.group.members/headerText` |
| G2 | 群聊发送「你好」→ assistant 气泡出现，发言人 = members[0]（UI 气泡发言人与 API `speaker` 一致） | `native.group.replies[0].speaker` |
| G3 | 再发一条 → 发言人轮转到 members[1]（API assistant `speaker` 顺序 = members 顺序） | `native.group.rotation/speakers` |
| G4 | 刷新后两条消息仍在、发言人名仍正确、标题含两位成员名 | `native.group.persisted/titleAfterReload` |
| G5 | `/chats` 聊天历史出现该群聊：成员名 + 「群聊」标记 + 最后一条消息 | `native.group.listShown/listModeShown` |
| G6 | 删除群聊（聊天页无删除入口时走 API `DELETE /tavern/chats/{id}`）→ 列表条数回落、行消失 | `native.group.deleted/listAfterDelete` |
| G7 | 本组 console 无报错 | `native.group.console` |

报告里 `report.ok = (fail === 0)`，`report.summary` 给出 PASS/FAIL/SKIP 计数；
`report.app.nativeReady` / `report.app.oldUi` 记录原生 UI 判定，便于快速定位是
「产品没交付」还是「harness 断言坏了」。

## 未就绪时的预期行为

插件/应用还在开发时，`check.sh` 与 `verify.mjs` **不允许崩溃**，必须跑出「未就绪」报告：

- `/tavern/` 返回 404 → 宿主半没挂载，记 SKIP 并注明 `owner: plugin-core`；
- 侧栏入口/iframe 缺失 → 记 FAIL，owner `plugin-core`；
- iframe 出现应用自己的登录页（`input[type=password]`）→ 记 FAIL，owner `app-auth`；
- 原生 UI 未交付（仍是旧探索布局）→ A3 记 FAIL 且 N/C/R/H/S/M 组统一记 SKIP，
  注明 `owner: cards-client`，不产生虚假 PASS 或误导性 FAIL。

## 清理测试数据（跑完必须回基线）

酒馆 2.0 的测试数据在 `world` schema（`world.tavern_chats` / `world.tavern_messages` /
`world.tavern_world_info`）与 `world.character_cards`；隔离实例只换宿主，写的是同一套库：

```bash
PGPASSWORD=storybetween2026 psql -h 127.0.0.1 -U reader -d story_between -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DELETE FROM world.tavern_messages;
DELETE FROM world.tavern_chats;
DELETE FROM world.tavern_world_info;
DELETE FROM world.character_cards;
COMMIT;
SQL
rm -f /root/workspace/plugins/dsh-tavern/app/data/private/*/card-avatar-*.png
```

核对基线：`cards=0`、`chats=0`、`messages=0`、`world_info=0`；确认 `3099` 无监听、
`/tmp/dsh-tavern-native` 已删除。

## 已知坑

- **应用 base 是 `/tavern/`**：iframe 内所有相对 fetch 都带前缀；直接访问 `3080/tavern/`
  未登录会 401，必须用带 `?token=` 的地址进 DSH。
- **DSH 桌宠气泡**可能浮在 iframe 右下角、盖住发送按钮；harness 点击已用
  `force: true` + DOM `click()` 兜底，若手动验证时点不动，先关掉桌宠气泡。
- **真实生成会消耗模型额度**（R 组重新生成 = 两次生成），联调时确认
  `app/.env` 的 `DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1`，不要指向临时端口。
- **iframe 导航竞态**：`frame` 对象跨导航会 stale；`verify.mjs` 统一用
  `waitFrameUrl()` / `appGoto()` 重新取 frame，断言失败时优先看 `report.app.frameUrl`。
- **旧截图已删除**（20–79 属于已下线的探索/群聊/记忆流程）；当前保留 90–124（P0 90–99、P1 100–107、P2 110–115、P3 120–124）。

## P1 断言（task-48，客户端/服务端交付后跑）

P1 在 P0 的 22 项之外新增 8 项（C5、P1、E1、E2、T1、I1–I3），并让 S3 从 SKIP 转真断言。
fixture 由 `check.sh` 生成：`cards/v2-png.png`、`cards/chat-sample.jsonl`（ST JSONL，2 条消息：
assistant「雨下得正好，进来坐坐？」+ user「好，来一盏茶。」）。

预期（P1 全部交付时）：**30 PASS / 0 FAIL / 0 SKIP**；若注入预览 UI 未交付，S3 记 SKIP
（`owner: cards-client`），即 29 PASS / 0 FAIL / 1 SKIP。真实生成次数 ≤4（C 发送 1 + R 重新生成 1 +
停止生成演练 1，其余组不触发生成）。

截图新增：`100-c-stop.png`、`101-p-swipe.png`、`102-e-edit.png`、`103-e-delete.png`、
`104-t-title.png`、`105-i-export.png`、`106-i-import.png`、`107-s-preview.png`。

失败归因：
- 停止生成/版本切换/标题/编辑删除/导出菜单/导入按钮缺失或行为不符 → `owner: cards-client`；
- 导出响应 4xx/5xx、导入 422、swipe select 失败 → `owner: app-auth`（服务端 P1 接口）；
- 下载事件与 fixture 路径问题 → `owner: verifier`（harness）。

## P2 断言（task-51，客户端/服务端交付后跑）

P2 在 P1 的 30 项之外新增 11 项（W1–W3、S2-1–S2-3、Q1–Q5）：

- **W 组（世界书深度语义）**：卡详情新增条目 `position=after_char`、`depth=2`、`constant=true`、
  `probability=100`、`order=5`；列表 meta 回显这些字段；注入预览命中并显示原因；
  `POST /world-info/preview` 返回的 `matched` 按 `order` 升序、`position/depth` 生效、`injection`
  包含条目文本。现有 P1 世界书编辑器已支持这些字段，W 组在 P1 UI 上即可全绿。
- **S2 组（采样参数）**：聊天页菜单 →「采样参数」对话框 → 设 `temperature=1.2 / topP=0.9 /
  maxTokens=200` → 保存 → 刷新后重开对话框回显一致 → 带参数发送一条消息（真实生成 +1）不报错。
- **Q 组（预设与提示词）**：设置页 →「新建预设」（名称「E2E 预设 P2」、system 提示词含
  `【E2E-PRESET】`、temperature=1.2、maxTokens=200）→ 列表出现 → 设为默认（API `is_default=true`，
  刷新保持）→ 新聊天 `GET /characters/{id}/prompt?chatId=` 返回文本包含 `【E2E-PRESET】` →
  非法 temperature（5）后端返回 422（或前端拦截）。

预期（P2 全部交付时）：**41 PASS / 0 FAIL / 0 SKIP**（30 + 11）。真实生成次数 ≤4：
C 发送 1 + C2 停止演练 1（取消）+ R 重新生成 1 + S2 发送 1。P2 未交付时 W 组仍可跑（P1 UI），
S2/Q 组按缺入口记 SKIP 并注明 `owner: cards-client`；Q4 的 `prompt?chatId=` 未上线记 SKIP
（`owner: app-auth`），Q5 预设接口 404 记 SKIP（`owner: app-auth`）。

截图新增：`110-w-worldinfo.png`、`111-w-preview.png`、`112-s2-sampling.png`、
`113-q-presets.png`、`114-q-prompt.png`、`115-q-invalid.png`。

失败归因：预设/采样/世界书编辑器 UI 缺失或行为不符 → `owner: cards-client`；
预设 CRUD、prompt 组装、preview 顺序、422 校验 → `owner: app-auth`；harness 时序 → `owner: verifier`。

## P3 断言（task-58，原生上下文桥）

P3 在 P2 的 41 项之外新增 5 项（P3-1–P3-5），验证插件工具条与酒馆原生桥接：

- **P3-1 首页（无上下文）**：工具条 `[data-dsh-tavern-view]` 内「让 DSH 续写」「角色扮演」均
  `disabled`，`[data-dsh-tavern-status]` 文案含「先打开一个聊天」。
- **P3-2 聊天页（有上下文）**：进入 `/chats/<id>` 后按钮变为 enabled，状态栏显示
  `当前：/chats/<id>`（桥只在聊天页上报 `hasContext`）。
- **P3-3 续写预填**：点「让 DSH 续写」→ DSH 新会话输入框（Lexical/contenteditable）出现
  `请以<角色名>的口吻继续写下一段回复…` + `【角色】【简介】【性格】【场景】【开场白】【最近对话】`。
  插件 `fillComposer` 偶发退化到剪贴板兜底（notice「内容已复制到剪贴板」）时，harness 重开面板
  再试一次；仍退化则记录 `source=clipboard` 并 FAIL（`owner: plugin-core`）。
- **P3-4 角色扮演预填**：点「角色扮演」→ 输入框出现 `请以<角色名>的身份扮演…`，含角色名。
- **P3-5 位置记忆**：聊天页刷新仍停在 `/chats/<id>`；回到首页后刷新应用 iframe 仍停在
  `/tavern/`（插件只记忆 `/chats/:id`，不会把首页拉回聊天页）；本组 console 无报错。

预期（P3 全部交付时）：**46 PASS / 0 FAIL / 0 SKIP**（41 + 5）。真实生成次数仍 ≤4（P3 只点击
工具条、不发送，不产生额外生成）。桥未交付/未连接时 P3 组记 SKIP 并注明 `owner: plugin-core`
（插件工具条）或 `owner: cards-client`（应用侧桥上下文）。

截图新增：`120-p3-home-disabled.png`、`121-p3-chat-enabled.png`、`122-p3-continue-prompt.png`、
`123-p3-roleplay-prompt.png`、`124-p3-memory.png`。

失败归因：工具条按钮状态/预填文案/位置记忆 → `owner: plugin-core`；聊天页上下文缺失
（status 无「当前：/chats/」）→ `owner: cards-client`（dsh-bridge）；harness 时序 → `owner: verifier`。

## 群聊断言（G 组 / task-62）

P3 群聊服务端 + 客户端上线后新增 7 项（G1–G7），验证「多选角色卡 → 群聊 → 发言人轮转」：

- **G1 建群**：首页卡片左上角选择按钮 `button.tavern-select[aria-label="选择 <角色名> 加入群聊"]`
  勾选两张 fixture 卡（林小满 / 顾清和；缺卡时 G 组自己导入 fixture）→ 选择栏 `.tavern-select-bar`
  显示「已选 2 张」→ 点 `.tavern-select-group`「发起群聊（2）」→ 进入 `/chats/<id>`。
  页头 `.chat-members[aria-label="群聊参与者"]` 出现两个参与者；API `GET /tavern/chats/{id}`
  返回 `mode=group`、`members` 顺序与选择顺序一致。
- **G2 第一发言人**：发送「你好」→ 出现 assistant 气泡，`.bubble-speaker-name` = `members[0]`，
  且 API 消息 `speakerName` 与之一致。
- **G3 轮转**：再发一条 → 发言人 = `members[1]`；API 两条 assistant 的 `speakerName` 顺序为
  `[members[0], members[1]]`（服务端 `assistant_count % members.length`）。
- **G4 刷新**：聊天页 reload 后两条消息仍在、两条 assistant 的发言人名都在、标题含两位成员名。
- **G5 历史列表**：`/chats` 页 `.chat-history-row` 出现该群聊，行内 `.chat-history-mode` 显示
  「群聊」、`.chat-history-name` 含两位成员名、`.chat-history-last` 有最后一条消息。
- **G6 删除**：聊天页菜单当前没有「删除聊天」，G6 走 API `DELETE /tavern/api/reader/v1/tavern/chats/{id}`
  （期望 204），刷新 `/chats` 后该行消失、API 列表条数回落。
- **G7 console**：本组 console 无 JS 报错。

预期（群聊全部交付时）：**53 PASS / 0 FAIL / 0 SKIP**（46 + 7）。真实生成次数：G 组 2 次
（G2/G3 各一条），全量总计 6 次（C 1 + C2 停止 1 + R 1 + S2 1 + G 2）。群聊未交付时 G 组记
SKIP 并注明 `owner: cards-client`（多选/建群 UI）或 `owner: app-auth`（`POST /chats` cardIds、
`GET /chats`、轮转/删除）。

截图新增：`130-g-select.png`、`131-g-chat-members.png`、`132-g-reply-first.png`、
`133-g-reply-rotate.png`、`134-g-reload.png`、`135-g-list.png`、`136-g-deleted.png`。

失败归因：多选/建群按钮/群聊页参与者 → `owner: cards-client`；`POST /chats` cardIds 校验、
`GET /chats` 列表、`speakerName` 轮转、`DELETE /chats/{id}` → `owner: app-auth`；harness 时序 →
`owner: verifier`。

## 实测记录（P0，2026-11-05）

命令（隔离 3099 + 生产 app 8001/8810 + 生产 DSH 网关，systemd-run 独立 cgroup）：

```bash
systemd-run --scope --unit=tavern-native-p0d --property=MemoryMax=2G \
  --setenv=HOME=/root --setenv=XDG_CACHE_HOME=/root/.cache \
  bash -c 'cd /root/workspace/plugins/dsh-tavern && bash scripts/check.sh --instance'
```

结果：浏览器 **21 PASS / 0 FAIL / 1 SKIP**，静态 7 PASS / 0 FAIL / 1 SKIP（SKIP=应用前端测试，
需 `--with-app-tests`），`report.ok=true`；报告
`/tmp/dsh-tavern-native/check/report.json`，截图 90–99。

关键证据（`report.native.*`）：

- N：首页导入 fixture（`林小满`）后 `cards=1`、头像 `avatar=true`，导入提示「已导入角色卡「林小满」」；
  搜索「林小」过滤命中。
- C：点卡直接进 `/tavern/chats/<id>`；发送「你好」后用户气泡 55ms 上屏（流式期间可见）；
  assistant 采样 `distinct=2`（4→123 字）、`elapsedMs≈5.5s`；刷新后用户消息与回复都在。
- R：「重新生成」`1/1 → 2/2`，文本变化，消息数 `3 → 3`（无重复）。
- H：角色页「聊天历史」1 条，重进后消息完整。
- S：世界书新增 key「茶」条目 → 列表可见 → 删除后回落；注入预览 SKIP（P0 无预览 UI，
  `owner: cards-client`，服务端注入接口已由 app-auth 提供，P1 补 UI）。
- M：`/explore`、`/works`、`/worlds/scripts`、`/conversations/world/x` 全部落 `/tavern/`；
  首页无「探索作品」旧文案、死链 0；console clean。

本轮修复的真实缺陷（均为客户端，已由 cards-client 修掉后复跑）：

1. **卡详情崩溃 / 导入提示 undefined**：客户端 unwrap key 与服务端不一致
   （`{card:...}` 取了 `'character'`，`{item:...}` 取了 `'entry'`），导致
   `card.name.slice` TypeError、世界书/聊天历史不渲染、点卡失败。
2. **SSE 首帧用户消息不渲染**：服务端首帧 payload 是 `userMessage`，客户端
   `extractUserMessage()` 取 `payload.message` → 流式期间看不到自己发的消息，
   只能等 `done` 后 reload 补出（`userAtMs≈5.2s`、`userDuringStreaming=false`）；
   客户端已改为优先读 `payload.userMessage`（兼容 `message`/裸消息），复跑 `userAtMs=55ms`。

harness 侧同时修了两处时序：导入控件要等 Vue 挂载后再取（`input[type=file]` 隐藏控件）；
重新生成前等聊天记录异步加载完成再取最后一条 assistant。

## 实测记录（P1，2026-11-05）

命令同上（隔离 3099 + 生产 app 8001/8810 + 生产 DSH 网关，systemd-run 独立 cgroup）：

```bash
systemd-run --scope --unit=tavern-native-p1 --property=MemoryMax=2G \
  --setenv=HOME=/root --setenv=XDG_CACHE_HOME=/root/.cache \
  bash -c 'cd /root/workspace/plugins/dsh-tavern && bash scripts/check.sh --instance'
```

结果：浏览器 **30 PASS / 0 FAIL / 0 SKIP**，`report.ok=true`；报告
`/tmp/dsh-tavern-native/check/report.json`，截图 90–107（P1 新增 100–107）。

关键证据（`report.native.*`）：

- C：SSE 修复后真实流式 —— 用户气泡 51ms 上屏且流式期间可见；assistant 采样
  `distinct=5`（4→3→40→38→84 字）、`elapsedMs≈4.4s`；刷新后消息仍在。
- C2（停止生成）：`visible=true clicked=true ended=true alert=无`。
- R：`1/1 → 2/2`，文本变化、消息数 `3 → 3`。
- P（swipe）：`2/2 → 1/2` 切换后文本变化、刷新保持、再点 › 切回最新。
- T（标题）：`林小满 · 1` → `E2E 改标题 8353`，刷新保持。
- H（历史）：角色页 1 条历史；重进后消息数与 `GET /chats/{id}` 返回条数一致（本轮 3 条）。
- E：删除最后一条 assistant 消息 `3 → 2`；编辑 user 消息后文本更新为「你好，改一下 0351」。
- S：世界书 key「茶」新增可见、注入预览命中（`.preview-result`「命中 1 条世界书」）、删除后回落。
- I：导出 JSONL（`attachment; filename=…jsonl`、2 行逐行解析、`bad=0`）、导出 JSON（2007B、messages=2）；
  卡详情导入 `chat-sample.jsonl` 后历史 `1 → 2`、自动进入会话、消息数 2、文本命中「雨下得正好」「来一盏茶」。
- M：4 条旧路由全落 `/tavern/`，无旧文案、死链 0；console clean。

本轮 harness 修复（都是时序/选择器，不是产品缺陷）：

1. 每次 `location.reload()` 后先 `waitChatRendered()`（等 `.bubble-row` 与 `.chat-name`/菜单渲染完），
   再取「重新生成 / 标题 / 版本切换 / 删除」控件；否则 reload 后异步加载期间会误判「没有按钮」。
2. 停止生成演练放在 R 之前（取消的流式不产生成功回复，避免污染后续 swipe 版本状态）。
3. E 组先删后编：编辑 user 消息会截断其后消息，先编辑会导致最后一条 assistant 消失、删除按钮找不到。
4. H 组不再比对旧文本快照：重进后用 `GET /tavern/api/reader/v1/tavern/chats/{id}` 的
   `messages.length` 与最后一条 assistant 的 active swipe 文本做断言。
5. 导出菜单等待 `.chat-menu-pop [role=menuitem]` 出现（最多 6s，必要时重开一次菜单）；
   导入文件输入改为轮询查找（历史区块可能尚未挂载），找不到时重进一次详情页。

## 实测记录（P2，2026-11-05）

命令同 P1（隔离 3099 + 生产 app 8001/8810 + 生产 DSH 网关，systemd-run 独立 cgroup）：

```bash
systemd-run --scope --unit=tavern-p2-run --property=MemoryMax=2G \
  --setenv=HOME=/root --setenv=XDG_CACHE_HOME=/root/.cache \
  bash -c 'cd /root/workspace/plugins/dsh-tavern && bash scripts/check.sh --instance'
```

结果：浏览器 **41 PASS / 0 FAIL / 0 SKIP**，`report.ok=true`；报告
`/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-04T19:47:05Z），
截图 90–115（P2 新增 110–115）。真实生成 4 次（C 1 + C2 停止 1 + R 1 + S2 1）。

关键证据（`report.native.*`）：

- W（世界书语义）：条目「茶」+ `【E2E-WORLD-INFO-P2】`，回显
  `after_char · depth 2 · order 5 · 100% · 手写`；注入预览「命中 1 条世界书」且显示原因
  「关键词「茶」」；`context-preview` API `matched=1 orders=5 sorted=true position/depth=true injection=true`。
- S2（采样参数）：Sheet 内 temperature/topP/maxTokens 填 `1.2 / 0.9 / 200` → 保存后
  `GET /chats/{id}` 落库 `{temperature:1.2,topP:0.9,maxTokens:200,stop:[]}`，reload 重开回显一致；
  带参数发送一条消息 `sent=true alert=无`。
- Q（预设）：新建「E2E 预设 P2」（system 含 `【E2E-PRESET】`、temperature 1.2、maxTokens 200）成功；
  设为默认后 `isDefault=true` 且刷新保持；新聊天 `GET /characters/{id}/prompt?chatId=` 返回
  `status=200 含预设文本=true len=1835`；非法 temperature=5 前端拦截 + 后端 422。
- 既有 A/N/C/C2/R/P/T/H/E/S/I/M 30 项无回归；console clean。

本轮修复（harness 时序，不是产品缺陷）：

1. `appGoto()` 末尾等 Vue 应用真正挂载（`.tavern-home/.tavern-settings/.tavern-character/.bubble-row/.tavern-empty`）：
   `index.html` 启动占位里也有 `h1`，只等标题会把启动占位误判成已渲染（Q/S2 全 SKIP 的根因）。
2. S2 开始时 iframe 可能停在卡详情页：先用 C 组记录的聊天 URL `appGoto('/chats/<id>')` 回聊天页，
   再等 `.bubble-row` 与菜单；找不到菜单时 reload 重试一次并把菜单文本写进 detail。
3. S2 读输入值放在点「保存参数」之前（保存成功 Sheet 立即关闭，读 input 会拿到 null）；
   落库用 `GET /chats/{id}` 的 `chat.sampling` 断言，回显再 reload 重开 Sheet 读。
4. reload 后 Vue 事件可能尚未绑定：菜单点击最多重试 3 次（每次等 4s）；Q4「开新聊天」点击也加了
   DOM click 兜底（`el.click()`），否则偶发点了没反应。
5. 每轮跑前/跑后清库（保留内置「默认」预设）：`DELETE FROM world.tavern_messages/tavern_chats/
   tavern_world_info; DELETE FROM world.tavern_presets WHERE name LIKE 'E2E%'; DELETE FROM
   world.character_cards;` + `rm -f app/data/private/*/card-avatar-*.png`。

本轮发现的真实客户端缺陷（已由 cards-client 修复，dist 03:27 重建）：`presetToWire()` 把 `isDefault`
塞进 POST `/tavern/presets` body，服务端 `PresetInput` 为 Strict 模型 → 422「请求字段不符合要求」；
修复后 create/update 不再带 `isDefault`，设默认改走 `PATCH {isDefault:true}`。

## 实测记录（P3，2026-11-05）

命令：`bash scripts/check.sh --instance`（systemd-run 独立 cgroup，隔离 3099 + 生产 app 8001/8810 + 生产网关）

结果：**46 PASS / 0 FAIL / 0 SKIP**（P2 的 41 项 + P3 组 5 项），`report.ok=true`；
静态检查 7 PASS / 0 FAIL / 1 SKIP（唯一 SKIP 是应用前端 typecheck/单测，需 `--with-app-tests`）。
报告 `/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-05T00:46:31Z），截图 90–124
（P3 新增 `120-p3-home-disabled`、`121-p3-chat-enabled`、`122-p3-continue-prompt`、
`123-p3-roleplay-prompt`、`124-p3-memory`）。

P3 证据（`report.native.bridge`）：

- P3-1 首页：`continueDisabled=true roleplayDisabled=true`，`status=已连接；先打开一个聊天`，`hint=ready`。
- P3-2 聊天页：两按钮 enabled，`status=当前：/chats/<id>`，`hint=ready`。
- P3-3 续写：`source=editor`，输入框文本含 `请以林小满的口吻继续写下一段回复…` 与【角色】【简介】【性格】【场景】【开场白】【最近对话】。
- P3-4 扮演：`source=editor`，输入框文本含 `请以林小满的身份扮演，针对读者最后一条消息即兴演绎一段回复。`。
- P3-5 位置记忆：`chatReloadOk=true`、`homeAgain=true`、本组 console clean。

既有 41 项无回归（A/N/C/R/H/S/M/W/S2/Q 全 PASS）。真实生成 4 次（C 发送 1 + C2 停止演练 1 +
R 重新生成 1 + S2 采样发送 1）。P3 只点击工具条不发送，不额外消耗生成。

本轮修的两处 harness 时序（非产品缺陷）：

1. **P5 首页刷新误判**：应用 iframe 刷新后先显示「正在打开角色卡...」启动占位，原来 URL 匹配后
   立即读 body 会判 `homeAgain=false`。改为等 body 含「角色卡」且不含「正在打开角色卡」再断言。
2. **P3/P4 预填判定**：按 pattern 精确匹配本次文案（续写 /继续写下一段回复/、扮演 /身份扮演/），
   避免读到另一个会话残留草稿；插件 `fillComposer` 退化到剪贴板兜底时重开面板重试一次，
   仍退化才 FAIL 并记 `source=clipboard`（`owner: plugin-core`）。本轮全量走 `source=editor`。

清理：`cards=0 / chats=0 / messages=0 / world_info=0 / personas=0 / presets=1`（内置默认），
`card-avatar-*.png`、`tavern-card-*.png` 已删；3099 无监听、隔离 HOME 已删。未 commit。

## 群聊删成员容错断言（L 组 / task-66）

打磨第 1 步：群成员角色卡被软删后，群聊不再 404，而是「已删除成员」展示 + 只读容错
（服务端 task-63、客户端 task-64）。L 组用 API 造数，UI 只做展示断言：

- **L1 单删成员详情容错**：API `POST /tavern/api/reader/v1/tavern/chats {cardIds:[a,b]}` 建群 →
  发 1 条消息（真实生成 1 次）→ API `DELETE /tavern/character-cards/{a}`（实际客户端路径
  `DELETE /tavern/api/reader/v1/tavern/characters/{a}`）→ `GET /chats/{id}` 仍 200，
  `members` 中该成员 `deleted=true`、`aliveMemberCount=1`、`cards` 长度 1；
- **L2 UI 已删除标记**：重进聊天页，页头 `.chat-members .chat-member-avatar.deleted` 的
  `title/aria-label` 含「（已删除）」，且 CSS 置灰（`opacity:.45;filter:grayscale(1)`）；
- **L3 发言人切换**：删成员后再发一条（真实生成 1 次）→ assistant 发言人 =
  存活成员（API `speakerName` 与 UI `.bubble-speaker-name` 一致，不再是已删除成员）；
- **L4 全员删除详情**：再 `DELETE` 第二位成员 → `GET /chats/{id}` 仍 200、
  `aliveMemberCount=0`、两个成员都 `deleted=true`；
- **L5 只读容错**：UI 出现 `.chat-readonly[role=status]`「群聊成员已全部删除，无法继续发言」，
  输入框 disabled（placeholder 也变为「群聊成员已全部删除」）、发送 disabled、重新生成按钮隐藏/disabled；
  直接 `POST /chats/{id}/messages` → 409 `CHAT_NO_MEMBERS`；
- **L6 列表与删除**：`GET /tavern/chats` 仍包含该群聊（不报错），`DELETE /chats/{id}` → 204，
  再查列表已回落；L7 本组 console clean。

预期（task-63/64/65 全部部署时）：**66 PASS / 0 FAIL / 0 SKIP**（53 + L 组 7 项；L 组只做一次
「单删 + 全员删除」闭环，真实生成 +2，全量总计 8 次）。未部署时 L 组依赖项记 SKIP 并注明
`owner: app-auth`（详情容错/aliveMemberCount/409）或 `owner: cards-client`（已删除标记/只读 UI）。

截图新增：`140-l-member-deleted.png`、`141-l-member-grey.png`、`142-l-speaker-switch.png`、
`143-l-no-members.png`、`144-l-chat-deleted.png`。

失败归因：`GET /chats/{id}` 404、缺 `deleted/aliveMemberCount`、轮转选中已删除成员、
`POST messages` 未返回 409 `CHAT_NO_MEMBERS` → `owner: app-auth`；页头无「（已删除）」/未灰显、
只读提示缺失、输入/发送未禁用 → `owner: cards-client`；时序/选择器 → `owner: verifier`。

## 实测记录（G，2026-11-05）

命令：`bash scripts/check.sh --instance`（systemd-run 独立 cgroup，隔离 3099 + 生产 app 8001/8810 + 生产网关）

结果：**53 PASS / 0 FAIL / 0 SKIP**（P2 的 41 项 + P3 5 项 + G 组 7 项），`report.ok=true`；
静态检查 7 PASS / 0 FAIL / 1 SKIP（唯一 SKIP 是应用前端 typecheck/单测，需 `--with-app-tests`）。
报告 `/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-05T02:48:48Z），截图 90–136
（G 组新增 `130-g-select` … `136-g-deleted`）。

G 组证据（`report.native.group`）：

- G1 建群：`members=["林小满","顾清和"]`、选择栏「已选 2 张」、页头两参与者，API `mode=group`。
- G2 首发言人：UI `speaker=林小满`，API `speakerName=林小满`，一致。
- G3 轮转：第二条 `speakerName=顾清和`，API `speakers=["林小满","顾清和"]`。
- G4 刷新：`persisted=true`、`speakers=["林小满","顾清和"]`、标题 `林小满、顾清和`。
- G5 历史：`listShown=true`、`listModeShown=true`（「群聊」标记 + 成员名 + 最后一条消息）。
- G6 删除：API `DELETE` 204，`afterDelete=1`（群聊行消失），`/chats` 列表回落。
- G7 console：本组 clean。

真实生成 6 次（C 1 + C2 停止演练 1 + R 1 + S2 1 + G2/G3 各 1）。既有 46 项无回归。

本轮修的 harness 问题（均为时序/选择器，非产品缺陷）：

1. **G1 只选中 1 张**：原逻辑「卡片数 ≥2 就停止导入」被同名重复导入骗过，改为
   `ensureCard(name, file)`（先 `findCard`，缺卡才导入，再选两张）。
2. **G2/G3 API 字段名**：服务端返回的是 `speakerName`，harness 误读 `speaker` → 已改。
3. **G4 刷新后读到占位**：聊天详情异步加载，标题先显示「聊天」；改为等 `.chat-name` 标题含
   两位成员名且 `.bubble-speaker-name` ≥2 再断言。
4. **G 组之后 C2/R/P/T 全 SKIP**：G 把 iframe 导航到 `/chats` 列表，这些组仍复用旧 `chatFrame`；
   在 G 组结束处按 `chatEvidence.chatUrl` 重新导航回单聊并 `waitChatRendered` 后重设 `chatFrame`。
5. **S2 reload 后菜单打不开**（偶发 `menu=false`）：回显读取改为「整页重进聊天页 → 找菜单 → 采样参数」，
   失败重试一次；S2-2 现已稳定 PASS。
6. **H 组聊天历史偶发为空**：卡详情渲染完成但历史接口返回慢，增加「重进详情页再等 30s」重试。
7. **P3-5 位置记忆偶发 false**：点击工具条会新开 DSH 会话、面板可能重挂载，改为每步取当前 tavern frame，
   失败重试一次（`chatReloadOk/homeAgain` 均改为重试后判定）。

清理：`cards=0 / chats=0 / messages=0 / world_info=0 / personas=0 / presets=1`（内置默认），
`card-avatar-*.png`、`tavern-card-*.png` 已删；3099 无监听、隔离 HOME 已删。未 commit。

## 实测记录（L，2026-11-06）

命令：`bash scripts/check.sh --instance`（systemd-run 独立 cgroup，隔离 3099 + 生产 app 8001/8810 + 生产网关）

结果：**60 PASS / 0 FAIL / 0 SKIP**（既有 53 项 + L 组 7 项），`report.ok=true`；
静态检查 7 PASS / 0 FAIL / 1 SKIP（前端测试需 `--with-app-tests`）。
报告 `/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-06T00:55:39Z），截图 140–144。

L 组证据（`report.native.groupDeletion`）：

- L1 单删成员：`DELETE /characters/{a}` → 204；`GET /chats/{id}` → 200，
  `members[0].deleted=true`、`aliveMemberCount=1`、`cards` 长度 1，`firstSpeaker=林小满`。
- L2 UI 标记：`.chat-member-avatar.deleted[title="林小满（已删除）"]`，`opacity=.45`、
  `filter=grayscale(1)`；assistant 气泡发言人显示「林小满（已删除）」。
- L3 发言人切换：删成员后继续发言，`secondSpeaker=顾清和`（=aliveMemberName），
  UI `.bubble-speaker-name` 一致，消息保存成功。
- L4 全员删除：再删第二位 → `GET /chats/{id}` 200、`aliveMemberCount=0`、两个成员
  `deleted=true`。
- L5 只读：`.chat-readonly[role=status]` 文本「群聊成员已全部删除，无法继续发言」，
  输入框 disabled（placeholder 同文案）、发送 disabled、重新生成按钮隐藏；
  `POST /chats/{id}/messages` → 409 `CHAT_NO_MEMBERS`。
- L6 列表/删除：`GET /tavern/chats` 仍包含该群聊；`DELETE /chats/{id}` → 204，
  再查列表已消失；L7 本组 console clean。

真实生成：L1 首条 + L3 删成员后一条 = 2 次；全量总计 8 次。

本轮修的 harness 问题（非产品缺陷）：

1. **新建群聊页就绪判定**：空群聊没有 `.bubble-row`，原先复用 `waitChatRendered` 会误判
   「群聊页未渲染」→ 新增 `lWaitChatShell`（`.chat-name`/菜单 + `.chat-log|.chat-empty`）。
2. **GET /chats/{id} 响应形状**：`members/aliveMemberCount/cards` 在 `payload.chat` 里，
   不是顶层（首版读错层级导致误 SKIP）。
3. **群聊完成判定**：群聊气泡没有 `.bubble-meta`（`small` 为空），不能用「已保存」判定；
   改为「无 `.bubble-pending` + 文本连续两次采样稳定 + 行数增加」。
4. **重复 SKIP**：`skipRest` 未记录 steps 导致末尾补齐又 SKIP 一遍；改为 skip 同时 markStep。

清理：`cards=0 / chats=0 / messages=0 / world_info=0 / personas=0 / presets=1`（内置默认），
`card-avatar-*.png`、`tavern-card-*.png` 已删；3099 无监听、隔离 HOME 已删。未 commit。

## 搜索与移动端断言（N 组 / task-67）

命令：`bash scripts/check.sh --instance`（N 组随 `--cards` 运行，可用 `DSH_TAVERN_ONLY=pol` 单独跑）

| # | 断言 | 关键证据 |
|---|------|---------|
| N1 | 聊天内搜索：命中计数 >0，上/下一处切换计数变化，命中消息 `.chat-search-hit` 高亮且滚动可见 | `report.native.polish.search`（open/total/first/afterNext/afterPrev/hitVisible/hint） |
| N2 | 无命中显示「没有匹配」；Esc 关闭搜索且输入框草稿保留 | `report.native.polish.search`（noMatch/closed/draftKept） |
| N3 | 移动端 390×844：composer 在视口内且点击命中不被遮挡 | `report.native.polish.mobile`（composer/viewport/composerHit） |
| N4 | 移动端发送/搜索触摸目标 ≥40×40、聊天名 ellipsis、页面无横向溢出 | `report.native.polish.mobile`（send/search/nameStyle/docOverflow） |
| N5 | index.html viewport meta 含 `viewport-fit=cover` | `report.native.polish.viewport.meta` |
| N6 | 搜索/移动端 console 无报错 | `report.native.polish.console` |

数据准备：卡详情「导入酒馆聊天」导入 `chat-search.jsonl`（6 条消息，其中 3 条含「茶」），不触发生成；
搜索词「茶」预期 3 条命中，无命中词用「绝无此词-ZZZ」。

预期（task-65 全部部署时）：**66 PASS / 0 FAIL / 0 SKIP**（既有 60 + N 组 6；真实生成次数不变）。
截图：150-search-hit.png、151-search-nomatch.png、152-mobile-chat.png、153-mobile-compose.png。

## 实测记录（N，2026-11-06）

命令：`bash scripts/check.sh --instance`（systemd-run 独立 cgroup，隔离 3099 + 生产 app 8001/8810 + 生产网关）

结果：**66 PASS / 0 FAIL / 0 SKIP**（既有 60 项 + N 组 6 项），`report.ok=true`；静态检查 7 PASS / 0 FAIL / 1 SKIP。
报告 `/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-06T01:40:11Z）；截图 150–153。
真实生成次数与上一轮一致（C 1 + C2 停止 1 + R 1 + S2 1 + L 2）。

N 组证据（`report.native.polish`）：

- N1 搜索：搜索按钮展开 `.chat-search[role=search]`，输入「茶」→ 计数 `1/4`（6 条导入消息中 4 条含茶），
  「下一个匹配」→ `2/4` 且 `.bubble-row.chat-search-hit` 1 条、`data-message-id=…` 且滚动到 `.chat-log` 可视区；
  「上一个匹配」回到 `1/4`；hint「仅搜索已加载消息」存在。
- N2 无命中 + Esc：输入「绝无此词-ZZZ」→ 计数文本「没有匹配」；Esc 后搜索条关闭，composer 草稿原样保留。
- N3 移动端（390×844）：`form.chat-compose` bottom=844（=视口高），textarea 中点 `elementFromPoint` 命中 composer 内元素，
  未被底部导航遮挡。
- N4 触摸目标/溢出：发送按钮 80×44、搜索按钮 44×44（均 ≥40×40）；`.chat-name` computed `text-overflow:ellipsis`、
  `overflow:hidden`、`white-space:nowrap`；`.bubble-speaker-name` 的 ellipsis 规则在 CSSOM 中命中；
  `documentElement.scrollWidth-innerWidth=0`、`.chat-log` 横向溢出 0。
- N5 viewport：`meta[name=viewport]` = `width=device-width,initial-scale=1,viewport-fit=cover`。
- N6 console：新开移动页 + 桌面本组均无报错。

数据准备路径：卡详情「导入酒馆聊天」导入 `chat-search.jsonl`（6 条消息，4 条含「茶」）→ 自动进入
`/chats/<id>`，不触发生成。清理时该导入会话随 `world.tavern_chats/messages` 一并删除。

本轮 harness 修复：搜索高亮类（`.chat-search-hit`）只在点击「下一个/上一个」后写入命中 id 才出现，
首版在输入后立即断言导致 `hitCount=0` → 改为先切一次再断言高亮与滚动；CSSOM 的 ellipsis 规则需递归
（媒体查询内嵌规则）才能命中，已改为递归扫描。

## 实测记录（打磨 2 收尾 / task-67，2026-11-10）

两轮都跑同一份 harness（隔离实例与生产实例各一遍，均先清库）：

| 轮次 | 命令 / 入口 | 结果 | 报告 |
| --- | --- | --- | --- |
| 隔离全量 | `bash scripts/check.sh --instance`（systemd-run 独立 cgroup，3099 + 生产 app 8001/8810 + 生产网关） | **66 PASS / 0 FAIL / 0 SKIP**，`report.ok=true` | `/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-10T02:37:38Z） |
| 生产冒烟 | `node verify.mjs --url "http://127.0.0.1:3080/?token=…" --out /tmp/dsh-tavern-native/prod-pol2b --fixtures /tmp/dsh-tavern-native/cards` | **66 PASS / 0 FAIL / 0 SKIP**，`report.ok=true` | `/tmp/dsh-tavern-native/prod-pol2b/report.json`（startedAt 2026-11-10T02:52:50Z） |

两轮证据一致：G 组两条群聊回复均为 `http=200`、约 7.0s，发言人 林小满 → 顾清和（轮转）；
L 组删第一位成员后 `firstSpeaker=林小满`、`aliveMemberName=顾清和`，删成员后的发言 `http=200` 且落库
`afterAssistantCount=2`、发言人 `顾清和`；C 组流式仍有 ≥2 个不同长度（生产轮 distinct=4）；L2 页头节点级断言
`className` 含 `deleted` + `opacity=0.45` + 文本含「已删除」（去重 title/aria-label 后）通过。

本轮 harness 修复（均为时序/选择器，未放宽任何断言）：

1. **C 组自足**：只用 `DSH_TAVERN_ONLY=c,g,l` 这类目标化跑时 N 组不执行、首页没有卡，导致「点卡无跳转」假失败。
   新增 `ensureFixtureCard()`：卡片缺失时回首页用 `input[aria-label="导入角色卡文件"]` 补导入 `v2-png.png` 并等卡名出现。
2. **点卡入口**：卡片点击处理器在内层 `button.tavern-card-open`（点外层 `div.tavern-card` 中心可能落空），
   改为优先点该按钮；3 次重试间回首页重新定位卡片；卡片列表搜索/导入后短暂重渲染时先等卡片出现再点。
3. **发送可靠性**：G/L 组发送前 `waitFor` 发送按钮 `isDisabled()===false`（刚进聊天或上一条收尾时按钮 disabled，
   force click 会静默丢弃）；`page.on('response')` 捕获 `POST /tavern/chats/<id>/messages` 的 httpStatus；
   6–8s 无 POST 时退化为 DOM click 并记录 `clickPath`。
4. **L3 断言收紧**：必须同时满足「本次发送 POST 200 + 服务端 assistant 条数增加且文本变化 + speakerName=存活成员」
   才 PASS（以 GET `/chats/{id}` 落库为准，UI 只校验展示，避免历史消息未加载完时的假通过/假失败）。
5. **挂起归因**：G/L 组在 30s 无首个 delta 时旁路调用 `gatewayProbe()`（直连 3080 `/tavern-agent/v1/stream`，
   16 tokens），把结果写进 `report.native.group.replies[].stallProbe`。本轮两轮全绿、未触发探针；
   此前（2026-11-06 prod-pol2）曾出现一次「POST 200 但 180s 无 delta」，复跑即恢复，判为上游偶发，
   若再现可用该探针区分「整网关挂起」与「单请求卡住」。
6. **L2 页头断言**：改为节点级三条件（class 含 `deleted` + opacity<0.6 + 文本含「已删除」，title/aria-label 去重），
   不再因「文本拼接重复」误报。

清库核对：`world.character_cards=0`、`world.tavern_chats=0`、`world.tavern_messages=0`、
`world.tavern_world_info=0`、`world.tavern_personas=0`、`world.tavern_presets` 仅内置「默认」1 条；
`app/data/private/*/card-avatar-*.png`、`tavern-card-*.png` 已删；隔离 3099 无监听、`/tmp/dsh-tavern-native/instance` 已删。

## 群聊成员管理断言（M 组 / task-70）

入口：群聊聊天页 → 聊天菜单（`button[aria-label="聊天菜单"]`）→「管理成员」（仅群聊出现）

| # | 断言 | 证据字段 |
| --- | --- | --- |
| M1 | 2 人群聊打开「管理成员」弹层：3 张可选卡、2 人预选、文案「…当前已选 2/5。」 | `report.native.membership.opened`（checks/checked/text/saveDisabled） |
| M2 | 勾选第 3 张 → 保存：PATCH 200、页头 3 个头像、`GET /chats/{id}` members=3、aliveMemberCount=3 | `report.native.membership.added`（patchStatus/header/members/alive） |
| M3 | 取消 1 人 → 保存：members=2；继续发言 POST 200、assistant 落库、`speakerName` ∈ 剩余成员 | `report.native.membership.removed`（patchStatus/remaining/assistants/httpStatus/speaker） |
| M4 | 只选 1 人：保存按钮 disabled + 文案「群聊需要 2 至 5 位角色」；直连 PATCH 1 个/6 个 → 422 `GROUP_MEMBERS_INVALID`，单聊 → 422 `CHAT_MODE_INVALID` | `report.native.membership.invalid`（checked/saveDisabled/hint/one/six/single） |
| M5 | 单聊页头/菜单没有「管理成员」入口；群聊管理流程 console 无报错 | `report.native.membership.single`、`console` |

数据准备：确保三张 fixture 卡（林小满 / 顾清和 / 沈夜舟）→ `POST /tavern/chats {cardIds:[林小满,顾清和]}` 建 2 人群聊；
M 组本身自足（缺卡时按 fixture 导入），因此 `DSH_TAVERN_ONLY=c,m` 也能单独跑。
真实生成次数：M3 一次（继续发言），其余全部走 API/UI，不触发生成。

预期（task-68/69 全部部署时）：**71 PASS / 0 FAIL / 0 SKIP**（既有 66 + M 组 5）。
截图：145-m1-members-dialog.png、146-m2-members-3.png、147-m3-members-2-reply.png、148-m4-invalid.png、149-m5-single-no-entry.png。

## 实测记录（打磨 3 / task-70，2026-11-10）

| 轮次 | 命令 / 入口 | 结果 | 报告 |
| --- | --- | --- | --- |
| 隔离全量 | `bash scripts/check.sh --instance`（systemd-run 独立 cgroup，3099 + 生产 app 8001/8810 + 生产网关） | **71 PASS / 0 FAIL / 0 SKIP**，`report.ok=true` | `/tmp/dsh-tavern-native/check/report.json`（startedAt 2026-11-10T03:32:01Z） |
| 生产冒烟 | `node verify.mjs --url "http://127.0.0.1:3080/?token=…" --out /tmp/dsh-tavern-native/prod-pol3 --fixtures /tmp/dsh-tavern-native/cards` | **71 PASS / 0 FAIL / 0 SKIP**，`report.ok=true` | `/tmp/dsh-tavern-native/prod-pol3/report.json`（startedAt 2026-11-10T03:44:55Z） |

M 组证据（两轮一致）：

- M1：弹层 `checks=3`、`checked=2`、文案「群聊需要 2 至 5 位角色，当前已选 2/5。」、保存因「成员没有变化」禁用。
- M2：勾沈夜舟 → `PATCH 200`；页头头像 `[林小满, 顾清和, 沈夜舟]`；`GET members=3`、`aliveMemberCount=3`。
- M3：取消沈夜舟 → `PATCH 200`；`members=2`；继续发言 `POST 200`、assistant `0→1`、`speakerName=林小满`（属剩余成员）。
- M4：只保 1 人 → 保存 disabled、hint「群聊需要 2 至 5 位角色」；直连 1 个/6 个 → 422 `GROUP_MEMBERS_INVALID`、单聊 → 422 `CHAT_MODE_INVALID`。
- M5：单聊菜单无「管理成员」入口；console 无报错。

M 组调试修复（harness，未放宽断言）：① `headerMembers` 的 `evaluateAll(...)` 在 `await` 之后误接 `.catch` → 改 `await ....catch(()=>[])`；
② 弹层保存按钮状态存在 Vue 渲染竞态 → 每次点选后 `waitSaveEnabled()` 再读 summary；③ 已勾选 checkbox 的 `click({force:true})` 在重开弹层后不生效 →
改用 Playwright `check()/uncheck()` 并读回 `isChecked()` 校验（`setMemberSelected`）；④ 每次打开弹层前先 `closeMembersDialog()`，避免残留弹层挡住入口；
⑤ M3/M4 步骤之间保存失败时也记录 drop/uncheck 诊断，便于归因。

清库核对：`world.character_cards=0`、`world.tavern_chats=0`、`world.tavern_messages=0`、`world.tavern_world_info=0`、
`world.tavern_personas=0`、`world.tavern_presets` 仅内置「默认」1 条；`app/data/private/*/card-avatar-*.png`、`tavern-card-*.png` 已删；
隔离 3099 无监听、`/tmp/dsh-tavern-native/instance` 已删。

## 移动端断言（O 组 / task-72，打磨 4）

viewport 固定 390×844（`context.newPage()` + `setViewportSize`），直连真实后端（隔离 3099，禁用 mock），
O 组在流程最前执行（A 之后、N 之前）：开跑前用 API 清空卡/聊天，跑完把自己导入的卡与聊天删掉，
因此不影响后续分组的数据基线。

| # | 断言 | 证据 |
| --- | --- | --- |
| O1 | 空库首页显示「还没有角色卡」+ 导入 CTA；`scrollWidth<=innerWidth`；`--edge`/`--ink` token 非空（回到 aligned 视觉，非空白主题） | `report.native.mobile.empty`（title/cta/edge/ink/overflow） |
| O2 | 导入两张 fixture 卡后 390 宽为两列网格（两张卡 top 相同、left 不同且 grid 轨道数=2），页面无横向溢出 | `report.native.mobile.grid`（columns/boxes/overflow） |
| O3 | 角色详情主操作可见、高度 ≥44px、完整位于视口内；页面无横向溢出 | `report.native.mobile.detail`（height/visible/inside/box/overflow） |
| O4 | `/chats` 空态「还没有聊天」+「去选角色」CTA；创建聊天（标题拉长）后行高 ≥48、标题 ellipsis（`scrollWidth>clientWidth`）、无横向溢出 | `report.native.mobile.chats`（emptyTitle/emptyCta/row） |
| O5 | `/settings` 与 `/settings/regex` 表单/输入不溢出，保存按钮 ≥44、「返回」≥44，正则行窄屏为 column 卡片且动作按钮 ≥44，console clean | `report.native.mobile.settings` / `regex`（backHeight/saveHeight/item/console） |

预期（task-71 全部部署）：**71 + 5 = 76 PASS / 0 FAIL / 0 SKIP**；真实生成次数与打磨 3 相同。
截图：160-mobile-empty-home.png、161-mobile-grid-2col.png、162-mobile-detail-actions.png、
163-mobile-chats-empty.png、164-mobile-chats-row.png、165-mobile-settings.png、166-mobile-regex.png。

### O 组产品口径（task-72 验收时确认）

- 390 宽卡库必须两列：`TavernCharactersPage.vue` 的 400px 断点已下调到 380px（390 不再是单列）。
- 正则页「返回设置」目标高度 44px（与设置页一致）：`.regex-back{min-height:44px}`。

这两条是打磨 4 期间 O 组发现并由 cards-client 修掉的真实产品偏差（E2E 首轮如实 FAIL，修复后转为 PASS）。

## 实测记录（打磨 4 / task-72，2026-11-10）

两轮验收（先清库）：

- 隔离全量：`bash scripts/check.sh --instance`（systemd-run 独立 cgroup，3099 + 生产 app 8001/8810 + 生产网关）
  → **76 PASS / 0 FAIL / 0 SKIP**，`ok=true`，报告 `/tmp/dsh-tavern-native/check/report.json`
  （startedAt `2026-11-10T05:07:37Z`）。
- 生产冒烟 prod-pol4：`node verify.mjs --url "http://127.0.0.1:3080/?token=…" --out /tmp/dsh-tavern-native/prod-pol4 --fixtures /tmp/dsh-tavern-native/cards`
  → **76 PASS / 0 FAIL / 0 SKIP**，`ok=true`，报告 `/tmp/dsh-tavern-native/prod-pol4/report.json`
  （startedAt `2026-11-10T05:25:11Z`）。

O 组证据（两轮一致）：

- O1 `empty={title:"还没有角色卡", cta:true, edge:"#E3E6EB", ink:"#242932", overflow:0}`——aligned 主题 token 已恢复，不是空白主题。
- O2 `grid.columns=2`，两张卡 `top=310`、`left=14/200`（176+176），`overflow=0`。
- O3 `detail={height:44, visible:true, inside:true, box:{x:14,y:790,w:115,h:44}, overflow:0}`。
- O4 `chats.row={height:69, nameOverflow:127, overflow:0}`（标题 ellipsis 生效），空态「还没有聊天」+「去选角色」。
- O5 `settings={overflow:0, backHeight:44}`；`regex={backHeight:44, saveHeight:46, item:{direction:"column", actionHeights:[44,44,44]}, console:[]}`。

本轮发现并修复的真实产品偏差（首轮 E2E 如实 FAIL，修复后 PASS）：

1. 390 宽卡库为单列：`TavernCharactersPage.vue` 的 400px 断点覆盖了 767px 的两列规则 → 断点下调到 380px（390 两列、360 单列不溢出）。
2. 正则页「返回设置」40px → `.regex-back{min-height:44px}`。

harness 侧修复（未放宽断言，只修时序/定位）：

- O 组必须显式 `setViewportSize(390,844)`（否则量到桌面网格）；`mobileRecord` 守卫改为 `mobileDone > index`（此前只记 O1）。
- O3 取 `boundingBox().y`（原写成 `.top` 恒 undefined → inside 恒 false）。
- O4 建聊天改用 `POST /characters/{id}/chats`（原误用 `POST /chats {cardIds:[1]}` 建群校验失败）。
- O5 空态下规则表单不渲染 → 先用 API 建种子规则再测，finally 删除。
- H 组「卡详情聊天历史」在 3080（卡排序与隔离不同）会打开另一张卡的详情 → 改为按 `chatEvidence.chatUrl` 查 `GET /chats/{id}` 得到 `chat.card.id` 再 `appGoto('/characters/'+cardId)`（回退才用 openDetail）。`DSH_TAVERN_ONLY=c,h,e` 验证 15/0/0。
- O 组 finally 会删除本组导入的卡与聊天（O 组先于 N/C/G/M 执行，保证后续分组从干净数据开始）。

截图精选：`docs/screenshots/native-polish4/`（25 张，2.3MB）：O 组 160–166、M 组 145–149、
L 组 140/142、G 组 130–132、C/R/E 证据（90/91/92/94/95/101/102/152）。

清理核对：`world.tavern_messages=0`、`tavern_chats=0`、`tavern_world_info=0`、`character_cards=0`、
`tavern_personas=0`、`tavern_presets=1`（内置「默认」）、`regex_scripts=0`；`card-avatar-*.png`、
`tavern-card-*.png` 已删；3099 无监听、`/tmp/dsh-tavern-native/instance` 已删除。未 commit。
