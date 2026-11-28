# 设计 A3：设计债清理五则

A1 定下「暖纸本」、A2 修了聊天/详情/设置的主结构。A3 处理评审里剩下的
**局部设计债**：结构上重复的「我是谁」、移动端每条消息的操作行、助手气泡的
内容面、整段斜体的动作文字、以及抢戏的「管理世界书」。

范围只动前端（`app/client/src/**`）与验收脚本/文档，不改接口语义；
唯一的行为变化是「我是谁」终于真的随消息发送（`personaId` 本来就在契约里）。

## 1. 设置页「我是谁」：一个选择器，一层卡片

现状：设置页「默认对话」组里先渲染旧组件 `PersonaControl`（自带描边+`--paper` 底，
里面又有「当前人设」下拉），紧接着又渲染一个「默认人设」下拉。两个下拉一个只改
组件内部状态、一个只写 `localStorage`（且没有任何代码读它），说明互相矛盾；
容器是「组 → settings-card → persona-control」三层。

改法：

- 删除 `PersonaControl.vue`（含只服务旧世界线聊天的 `sessionId` 绑定分支）。
- 新增 `characters/PersonaSettings.vue`：无描边、无内层背景，直接落在
  `settings-card` 里；只有一个下拉「人设」+ 就地新建/编辑/两步删除。
- 新增 `characters/persona-store.ts`：`tavern:persona` 读写，设置页写入，
  聊天页在**每条消息**里带 `personaId`（服务端按此注入 `[你的角色]`）。
- 设置组标题从「默认对话」改为「我是谁」；删除页脚重复的「默认人设：X」。
- 文案只留一句真话：「会作为「你的角色」写进每一轮对话；不选时按默认的「用户」称呼你。」

为什么不再有「当前 / 默认」：酒馆 2.0 的 persona 不落聊天表，只在发消息时随
payload 传（`MessageInput.personaId`），也没有聊天级切换入口。两个下拉描述的
「当前」与「新聊天默认」在数据上不存在，合并成一个全局选择是唯一不撒谎的形态。

## 2. 移动端操作行：收进「⋯」

现状：`@media(max-width:900px){.bubble-actions{opacity:1}}`，每条消息下面常显
引用 / 复制 / 编辑（或重新生成/版本）/ 删除，3–4 个 44px 图标，视觉噪声大。

改法：

- 每条消息右侧（用户消息在左侧）出现一个 40×40 的「⋯」触发器，与气泡同排，
  **不额外占竖向空间**；桌面 `display:none`。
- 移动端 `.bubble-actions` 默认 `display:none`；`⋯` 打开时变成固定在底部的
  两列菜单（`.bubble-actions.open`），带「消息操作」标题、图标+文字标签、
  每项 ≥44px，遮罩点击关闭，Esc 关闭，点任一操作后关闭。
- 桌面端保持 hover/focus 才显示的横向图标行，DOM 只有一套操作按钮：同一份
  按钮在两套布局里复用，不复制逻辑。
- 删除仍是两段式：菜单里点「删除」→ 原位变「确认删除 / 取消」。

## 3. 助手气泡：1px `--edge` + 极轻阴影

现状：`--canvas`(#FFFDF9) 与 `--paper`(#F7F2EA) 只差一档，1px 描边太淡，
内容面浮不起来。

改法：`.bubble.assistant` 在 `1px solid var(--edge)` 之外加
`box-shadow:0 1px 2px color-mix(in srgb,var(--ink) 8%,transparent)`
——一像素级的贴合阴影，不改变排版、深色主题下同样成立。

## 4. 动作文字：去掉整段斜体

现状：`.act{font-style:italic;color:var(--quiet)}`，长回复整段动作+对白混排时
斜体中文可读性差。

改法：`.act{color:var(--quiet)}`。动作与正文靠颜色与上下文区分，不再靠字形倾斜；
`strong` 仍用 600 字重 + `--ink`，层级没有丢。

## 5.「管理世界书」：降为次要入口

现状：白底 + 描边的默认按钮，与「开新聊天」同级，在一个只读区块里显得很重。

改法：`.section-action`：透明底、无描边、`--quiet` 色、带下划线的文字按钮，
hover 变 `--accent-ink`；移动端仍保留 44px 触达。文案不变（无条目时「管理世界书」，
有条目时「查看全部世界书」）。

## 6. 验收

单测（`app/client`）：

```bash
npm run typecheck
npm run test:unit
```

覆盖：`characters/PersonaSettings.test.ts`（唯一选择器、持久化、创建/两步删除、
失效 id 清理、空态不出现「默认人设」）、`tavern/TavernSettingsPage.test.ts`
（组标题、单人设容器、无旧文案）、`tavern/TavernChatPage.test.ts`
（「⋯」菜单开关/Esc/引用、绑定人设时消息 body 带 `personaId`）。

实机（复用已登录浏览器，桌面 1440 + 移动 390，含断言与截图）：

```bash
node scripts/design-shots.mjs --out docs/screenshots/design-a3
```

`scripts/design-shots.mjs` 会导入 `chat-a2.jsonl` 造一条含动作/跨天的聊天，
拦截消息请求验证 `personaId`（不消耗模型），并收集两端的 console 报错；结束后删除验收人设与该聊天。
截图：`docs/screenshots/design-a3/a3-{chat,detail,settings}-{1440,390}.png`、
`a3-chat-390-menu.webp`。

`verify.mjs` 的 A2 组断言同步更新：

| 检查 | 新断言 |
| --- | --- |
| V1 | `.act` 的 `font-style` 为 `normal` 且颜色等于 `--quiet` |
| V2 | 助手气泡仍有 `1px` 描边且 `box-shadow !== none` |
| V4 | `.section-action` 透明底、`border-width:0` |
| V5 | 四组标题含「我是谁」；人设组只有 1 个 `select` / 1 层 `settings-card`；页面不含「当前人设/默认人设」 |
| V6 | 移动端 `.bubble-actions` 默认 `display:none`、每条消息 1 个「⋯」；点开菜单 ≥3 项、每项 ≥44px、不越出视口；点遮罩关闭 |

## 7. 这轮不做

- 不改聊天页顶部图标来源（A2 遗留的 `?` 文本图标）与帮助页目录。
- 不引入 per-chat persona（需要服务端把 persona 落到 `tavern_chats`，超出设计债范围）。
- 不重做卡库网格密度、详情页双栏（属于下一轮信息架构）。
