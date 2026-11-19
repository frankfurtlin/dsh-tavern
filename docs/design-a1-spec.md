# 方向 A「暖纸本」落地规范（A1：设计系统 + 卡库/列表页）

基线：`6ac7741`。本步只改视觉系统、角色卡库、聊天列表与页面标题层级；聊天正文/详情页/设置重构放 A2。
约束：每步隔离全量 E2E + 生产冒烟；不改接口语义（`lastMessage` 由对象改纯文本除外）；不重启服务（dist 静态读取）。

## 1. Token（已由 Lead 写入 `app/client/src/presentation/aligned.css`）

```
--paper   #F7F2EA    --canvas #FFFDF9
--ink     #2B2622    --quiet  #6B6158
--edge    #E7E0D4    --wash   #F3E8DA
--accent  #8C4A2F    --accent-ink #7A3E27
--on-accent #FFFDF9  --danger #A5372F  --good #3F6B52
--r-control 10px  --r-card 16px  --r-sheet 20px
--reading-size 16px --reading-leading 1.9
```

对比度（实测）：accent on canvas 6.59:1、accent-ink on wash 6.82:1、quiet on paper 5.42:1。
**小字（≤13px）、徽标、链接、图标用 `--accent-ink`；按钮/大面积块才用 `--accent`。**

## 2. 字体与排版

- 标题保留衬线栈 `--font-serif`（Source Han Serif SC / Noto Serif SC / Songti SC / SimSun），字重 600、`letter-spacing:.01em`；若本机无衬线自动回退到无衬线也可接受，不依赖字体表达层级。
- UI 文字 14px/1.5；卡片描述 13px/1.6；正文阅读 16px/1.9（A2 用于聊天与详情）。
- 数字用 `font-variant-numeric: tabular-nums`（已加在 token 层）。

## 3. 角色卡库（`TavernCharactersPage.vue`）

- 桌面网格：`repeat(auto-fill,minmax(300px,1fr))`、`gap:20px`；卡内 padding 14，头像 64×64（圆角 14）；标题 17/600 + 预设徽标 12px（`--accent-ink` on `--wash`）。
- 简介：13px/1.6，`-webkit-line-clamp:2`，去掉尾部的「…」双标点问题（先裁到最后一个完整标点）。
- 元信息行：`还没聊过 · 标签1 · 标签2`，单行 `ellipsis`，标签不拆行（`white-space:nowrap`）。
- 卡内只保留一个动作：「详情」文本按钮（右上角，hover 显现，移动端常显），点击进详情；整卡仍点击开聊。删除「设置」文案与底部 footer 行。
- 多选：工具条加「多选」按钮（`aria-pressed`），进入后卡片左上显示勾选框（36×36、描边 1.5、选中填 `--accent` + 白勾），退出后隐藏；选择条吸顶显示 `已选 N 张 · 清空 · 发起群聊(N)`。
- 移动 ≤600：单列 list 卡（头像 72、两行文字、右侧「详情」），320 宽不溢出；601–900 两列；>900 自动列。

## 4. 页面标题与返回（全部 tavern 页）

- 有顶栏标题的页面不再出现第二个 H1：删除 `TavernChatsPage` / `TavernSettingsPage` / `TavernHelpPage` 的 `<h1>`；保留顶栏 `TavernShell` 标题。
- 删除 `TavernChatsPage` 右上的「返回角色卡」按钮（左上返回已足够）。
- 页面首个内容块与顶栏间距统一 20–24px。

## 5. 聊天列表（`TavernChatsPage.vue` + 服务端）

- `GET /tavern/chats` 的 `lastMessage` 改为**纯文本**：当前 `active_swipe` 内容、折叠空白、截 80 字符（不截断半个 UTF-8 字符）。前端直接插值。
- 行：头像 34、标题 15、预览 13 `--quiet`、时间 12 右对齐；行高 64；分组徽标 11px `--accent-ink`。
- 预览为空的文案保持「还没有消息」。

## 6. 一致性 / 无障碍

- 圆角：控件 `--r-control`、卡片 `--r-card`、弹层 `--r-sheet`；只改 tavern 页面内元素。
- `:focus-visible` 统一 `outline:2px solid var(--accent-ink); outline-offset:2px`。
- 触控目标 ≥44px（桌面可 40）；hover 过渡 140ms ease-out。
- 图标统一 Lucide；`?`、`×`、`⋯` 文本字符替换为图标按钮（`HelpCircle`、`X`、`MoreHorizontal`）。

## 7. 验收（verifier 同步更新断言）

- O1 主题 token 期望值改为：`--edge #E7E0D4`、`--ink #2B2622`。
- O2 卡库列数：390 单列（columns=1）、1440 ≥3 列。
- 新增：
  - 聊天列表预览不含 `{`、`"id"`、`swipes` 等 JSON 片段。
  - `/chats`、`/settings`、`/settings/help` 每页 H1 数量 ≤1（实际为 0）。
  - 卡片动作只有「详情」，无「设置」footer。
  - 预设徽标颜色对比 ≥4.5（计算 style color 与 background）。
- 既有 92 项除上述三处外不允许回归；真实生成相关 flake 仍走重试+证据。
