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
| 产物 | `/tmp/dsh-tavern-native/check/report.json`（逐项 PASS/FAIL/SKIP）、`check/logs/*.log`（静态命令与隔离实例日志）、`check/90-*.png` … `99-*.png` 截图、`cards/` fixture |

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

`verify.mjs` 的分组：`a` 基础、`n` 角色列表、`c` 聊天/流式、`r` 重新生成、
`h` 聊天历史、`s` 世界书、`m` 旧路由下线；不传 `--only` 时全跑。

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
| R1 | 最后一条 assistant「重新生成」→ 文本变化、消息数不重复 | `native.regenerate` |
| H1 | 返回角色页看到聊天列表 | `native.history.listShown` |
| H2 | 从历史重新进入聊天，消息完整 | `native.history.reentered/messagesIntact` |
| S1 | 卡详情世界书区块可编辑 | `native.worldInfo.sectionShown` |
| S2 | 新增条目（key「茶」，position after_char）后列表可见 | `native.worldInfo.added` |
| S3 | 世界书条目注入预览可见（无预览入口时 SKIP + owner） | `native.worldInfo.preview` |
| M1 | 旧路由 `/explore`、`/works`、`/worlds/scripts`、`/conversations/world/x` 全部下线（落 `/` 或 404，无死链） | `native.oldRoutes.routes` |
| M2 | 页面无「探索作品」等旧入口 | `native.oldRoutes.deadLinks` |
| G | 浏览器控制台无 JS 报错（过滤 `Failed to load resource`） | `checks[]` |

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
- **旧截图已删除**（20–79 属于已下线的探索/群聊/记忆流程）；当前只保留 90–99。

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
