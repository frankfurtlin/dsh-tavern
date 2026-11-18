# P0 改动汇总与代码整理（2026-11-18）

本文件记录本轮「让新用户会用」的 P0 改动，以及随后的代码整理。代码基线：`475e924`（P0 功能）、`c8d2f42`（侧栏顺序）、`ef5209d`（本轮代码整理）。

## 1. 功能

### 1.1 首次引导 / 帮助中心
- `OnboardingGuide.vue`：三步引导（选一张卡 → 回一句话 → 让 DSH 帮忙），圆点/跳过/上一步/下一步/开始体验，Esc 关闭、打开时聚焦；≤767px 全屏 sheet。
- 记忆：`guide.ts` 用 `localStorage` 的 `tavern:guide-seen` / `tavern:hint-dismissed`；无 localStorage 环境视为已看，不阻塞。
- 入口：卡库首页常驻提示条（可关闭）+ `?` 帮助按钮；聊天页页头 `?`；设置页「使用帮助」。
- `TavernHelpPage.vue`（`/settings/help`）：7 节说明 + 「重新查看引导」（清 `guide-seen` 后跳 `/?guide=1`）。
- 聊天页开场白示例 chips「你好 / 继续 / 我来了」：只填入输入框、不自动发送。

### 1.2 引用回复
- 消息操作「引用」按钮（移动端长按气泡 500ms 也可）；输入框上方引用条，120 字截断，`×`/Esc 取消。
- 发送时 `POST /tavern/chats/{id}/messages` 带 `{"quote":{"text":"…"}}`；用户气泡显示引用块（左侧竖线 + 2 行截断），点击按文本前缀定位原消息并高亮。
- 服务端存 `tavern_messages.quoted_text`（纯文本副本，源消息编辑/删除不影响），prompt 中以 `[引用] …`（≤500 字）置于该回合用户消息前。

### 1.3 聊天全局搜索
- `GET /tavern/chats?q=`：标题 ILIKE 或当前可见 swipe 内容 ILIKE（转义 `\ % _`，仅 active swipe，硬删无软删）。
- 历史列表搜索框 300ms debounce、「找到 N 个聊天」、无结果「没有匹配的聊天」+ 清空 CTA、标题 `<mark>` 高亮、滚动回顶。

### 1.4 侧栏顺序
- 插件组内 `order` 升序：自动任务(10) → 酒馆(12) → 知识库(14) → 紫微斗数(16) → 华尔街之狼(18) → Skill Center(30)。
- 只改 `client.js` 的 `PANEL_ORDER`；实测新页面加载即生效，无需重启 DSH。

## 2. 接口与数据

| 变更 | 说明 |
| --- | --- |
| `GET /tavern/chats` | 新增 `q`（trim 1..100，超长 `422 CHAT_SEARCH_INVALID`），与 `limit`/`offset` 组合 |
| `POST /tavern/chats/{id}/messages` | 新增 `quote: {text}`（规范）或 `quotedText`（兼容）；trim 1..2000，空/超长 `422 QUOTE_INVALID` |
| 消息响应 | `message_out` 回显 `quotedText`（assistant 恒 null）；SSE 首帧 `userMessage.quotedText`；regenerate 保留用户引用 |
| `world.tavern_messages` | 新增 `quoted_text text`（幂等 `ALTER TABLE … ADD COLUMN IF NOT EXISTS`） |

契约细节见 [contract.md](contract.md)。

## 3. 代码整理

### 3.1 删除旧故事模式遗留前端
P3（`06d637f`）只删了服务端 story 域与部分页面，客户端仍保留整棵旧前端源码。本轮按「从 `src/main.ts` 可达 + 被保留测试引用」重建依赖图，删除不可达文件：

- 本轮整理共删除 **634 个文件 / 约 34,515 行**：
  - `src/agent`、`src/authoring`、`src/catalog`、`src/writing`、`src/world` 旧实现、`src/play-controls`、`src/reader`、`src/novel-characters` 等不可达模块，及其 81 个旧测试、23 个未被引用的 CSS、旧 fixtures；
  - `app/tests/{acceptance,e2e,next,reader-plus,fixtures}` 旧 Playwright 套件（故事模式页面早已删除，套件不可运行）、4 个对应 `playwright*.config.ts` 与 `scripts/test-e2e.mjs`；
  - `insights.html` 第二构建入口及 `insights.vite.config.ts`（引用已删除的 `src/insights/main.ts`），`vite.config.ts` 收窄为单入口。
- 结果：`src` 从 6.0MB 降到 1.2MB；`.ts/.vue` 从 618 → 85（59 个应用模块 + 28 个测试），无孤立模块。
- 保留测试 28 文件 / 1189 用例（覆盖原生酒馆全部页面、api、sse、mobile、guide、help、引用/搜索等）。

### 3.2 文档归档
- `docs/step1–step5*.md`（故事模式/旧卡片空间/旧群聊/长期记忆/P0–P2 体验）移入 `docs/history/`，链接同步更新。
- 新增 [docs/README.md](README.md) 文档索引；保留 `architecture`、`contract`、`testing`、`step6`（2.0 设计）、`step7`（群聊设计）为主文档。

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 客户端类型检查（`npm run typecheck`） | 0 error |
| 客户端单测（整理后） | 28 文件 / 1189 用例通过 |
| 服务端 pytest | 103 passed / 1 skipped |
| 隔离全量 E2E（P0 功能，整理前） | 92 PASS / 0 FAIL / 0 SKIP |
| 生产冒烟 prod-p0（整理前） | 92 PASS / 0 FAIL / 0 SKIP |
| 整理后全量 E2E + 生产冒烟 | 见 `testing.md` 实测记录 |

上游偶发超时（opencode-go）表现为「DSH 模型网关不可用」；E2E 会记录 `gatewayProbe`/`sseTail` 并做一次带证据的重试，不静默刷绿。

## 5. 回滚点

- 整体回滚：`git reset --hard 475e924`（P0 功能提交）。
- 更早基线：tag `polish-base-06d637f`。
