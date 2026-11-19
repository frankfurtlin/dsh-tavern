# 设计 A2：聊天阅读 / 角色详情 / 设置页

方向 A「暖纸本」第二轮。A1 已落地色板、卡库与列表；A2 处理「读起来」的地方：聊天气泡、动作描写、日期分隔、角色详情、设置页信息层级。

## 1. 通用

- 仅用 A1 token（`--paper/--canvas/--ink/--quiet/--edge/--accent/--accent-ink/--on-accent/--wash`、`--r-control/--r-card/--r-sheet`、`--reading-size/--reading-leading`）。
- 卡片边界用 `--edge`，hover 用 `--accent`；焦点环 2px `--accent-ink` offset 2。
- 不用纯黑 `#000`、不用冷灰；小字（≤13px）必须 `--quiet` 或深色，对比 ≥4.5。

## 2. 聊天页（`app/client/src/tavern/TavernChatPage.vue`）

### 2.1 气泡
- assistant：`--canvas` 底 + 1px `--edge`，圆角 16 16 16 4；user：`--accent` 底 + `--on-accent` 字，圆角 16 16 4 16。
- 最大宽度：桌面 `min(72%, 620px)`；≤767 `86%`。
- 内边距 10px 14px；字号 15.5px；行高 1.85（阅读本来就该松）。
- 气泡间距 10px；同日连续同一发言人可缩到 6px（用相邻消息判断）。
- 时间不逐条显示，悬停气泡显示 `HH:mm`（title 即可）。

### 2.2 动作与强调（纯文本渲染，禁 v-html）
- 新增 `app/client/src/tavern/chat-format.ts`：`splitSegments(text): Array<{kind:'text'|'act'|'strong', text:string}>`。
  - `*...*`（单星号、≤200 字、不跨行）→ `act`；`**...**` → `strong`；其余 `text`。
  - 未闭合星号按文本处理。
- 模板用 `v-for` 渲染 `<span :class="seg.kind">`；`.act{font-style:italic;color:var(--quiet)}`、`.strong{font-weight:600;color:var(--ink)}`。
- 只处理首尾对称的标记；不做 Markdown 全量解析。

### 2.3 日期分隔
- 按 `createdAt` 的本地日期分组，在新的一天首条前插 `.chat-day`：今天 / 昨天 / `M月D日`（跨年加年份）。
- 12px `--quiet`，两侧 1px `--edge` 横线，居中，间距 18px。
- `createdAt` 缺失时归入当前分组，不裂开。

### 2.4 发言人
- 群聊：assistant 气泡上方 28px 头像 + 12px 名字（`--accent-ink`, 600）；连续同发言人只在第一条显示头像与名字。
- 单聊：不显示名字，仅头像可在顶部页头展示。
- 已删除成员延续「（已删除）」灰条 + 只读态。

### 2.5 操作
- 统一 Lucide 图标按钮（Quote / RefreshCw / Pencil / Copy / Trash2），aria-label 保留；桌面 hover/focus 显现，≤900 常显。
- 触达 ≥48px（透明区域可扩）；危险操作（删除）hover 时才变 `--danger`。
- 引用：图标替代文字「引用」。

### 2.6 流式与滚动
- 流式中：气泡尾三点呼吸动画；停止按钮 `--danger` 描边、44px；状态文案「正在回复…」。
- 不在底部时来新消息 → 右下浮钮「↓ 新消息」44px，点击滚到底。
- `prefers-reduced-motion` 下取消动画。

## 3. 角色详情（`app/client/src/tavern/TavernCharacterPage.vue`）

- Hero：头像 112 圆角 20 + 名字 serif 28/600 + 标签行（12px, `--quiet`）+ 预设徽标；右侧主按钮「开新聊天」（44），次按钮「编辑」（描边）。
- 只读视图默认：分节「人设 / 场景 / 开场白 / 世界书」，正文 16/1.9 `--ink`；空字段整节省略；世界书只显示条数与首条摘要，点开 Sheet 看全部。
- 编辑：点「编辑」切表单（textarea 自适应高度，最大 40vh，去掉内滚动条），保存/取消吸底 44；有未保存修改时返回需确认。
- 移动 390：hero 纵向排列，按钮整行 44，无横向溢出。

## 4. 设置与帮助

- 设置分四组卡片：「默认对话」「采样参数」「预设」「本地数据」；组标题 13px 600 `--accent-ink`。
- 采样参数用中文标签（温度 / 核采样 / 最大长度 / 重复惩罚），默认值灰字提示；原始 JSON 折叠在「高级」details 内。
- 保存按钮 44 主色；「保存成功」用 toast，不跳页。
- 帮助页：顶部目录（7 节锚点）+ 分节正文 16/1.9；底部「重新查看引导」；返回设置。

## 5. 验收（verifier V 组，追加到既有 92 项）

1. 聊天页：`.chat-day` ≥1；含 `*动作*` 的种子消息渲染出 `span.act` 且无 `*` 外露；user 气泡背景为 `--accent`、assistant 为 `--canvas`；群聊 `.chat-speaker` 头像 28。
2. 详情页：`.character-hero` 存在；只读正文 computed font-size ≥16px、line-height ≥1.8；点「编辑」出现表单，取消恢复。
3. 设置页：四组标题存在；「温度」中文标签可见；`details` 折叠存在。
4. 移动 390：气泡宽度 ≤86%；操作按钮常显；详情 hero 无横向溢出。
5. 既有断言不回退（92 项全 PASS）。

## 6. 分工与顺序

- A2a（聊天阅读，cards-client）：§2 + chat-format.ts + 测试 + dist。
- A2b（详情/设置，cards-client）：§3、§4 + 测试 + dist。
- 验收（verifier）：V 组 + 双轮（隔离/生产）+ 截图。
- Lead：最后 review、清库、commit。
