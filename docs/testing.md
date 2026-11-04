# 测试与验收（dsh-tavern / 酒馆 2.0 原生流程）

本插件是「DSH Web 里的酒馆」：宿主半 `lib/index.js` 把本地应用挂到 `/tavern`
前缀并复用 DSH 登录闸门；浏览器半 `client.js` 加侧栏入口「酒馆」和主面板 iframe。
酒馆 2.0 改成**原生酒馆**流程（角色卡 → 全屏聊天 → 多版本回复），旧的探索 / 世界 /
群聊 / 长期记忆流程已删除。验收因此必须同时覆盖 **HTTP 闸门**、**DSH Web UI** 和
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
`i` 导入导出（JSONL/JSON）、`m` 旧路由下线；不传 `--only` 时全跑。

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
- **旧截图已删除**（20–79 属于已下线的探索/群聊/记忆流程）；当前保留 90–115（P0 90–99、P1 100–107、P2 110–115）。

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
