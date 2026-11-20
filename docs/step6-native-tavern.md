# 酒馆 2.0：原生酒馆（SillyTavern 式）重设计

决策日期：2026-11-05。用户明确：**现有「故事模式」全部下线**，插件按原生酒馆重做。

## 1. 产品定义

- 一个装在 DSH 里的私人角色扮演酒馆：**角色卡 → 全屏聊天 → 多版本回复**。
- 单用户；无账号/社区/付费/多人。
- 生成由 DSH 当前模型提供（`/tavern-agent/v1` 网关），不配置外部 key。
- 数据全部自持（PostgreSQL + 本地存储）。

## 2. 保留 / 删除

**保留并复用**
- 角色卡解析与导入导出：`app/server/app/cards/parse.py`（PNG v1/v2/v3、character_book）
- 角色卡表与 CRUD、头像：`app/server/app/cards/{models,service,api}.py`（改造）
- 人设：`app/server/app/cards/personas.py`、客户端 PersonaSettings（选择存 `tavern:persona`，发消息时带 `personaId`）
- 正则：`regex_scripts` 与客户端 RegexPage
- 导出：卡 PNG / 聊天 JSONL（改为 ST 格式）
- 可信登录、DSH 桥（`app/client/src/dsh-bridge.ts`）、api 客户端、DSH 模型网关
- 流式生成基础设施（worker/stream），但聊天改为**同步 SSE 直出**，不再走异步任务+轮询

**删除（UI 先行，服务端在 P1 清理）**
- 探索作品/curated 世界、WorldCreatePage、WorldChatPage、WorldScene、360° 探索
- 创作工作台 `/worlds/scripts`、书/阅读器、play-controls 的 world-chat 会话模型
- 长期记忆/长期伙伴（原生酒馆无此概念；数据表保留不删，后续可作扩展）
- 群聊（P3 再重建）、Experience/Authoring/公开目录等无入口域

## 3. 数据模型（新，schema 沿用 world 以降低迁移成本）

- `tavern_chats`：id, owner_id, card_id, title, created_at, updated_at, last_message_at, model_name
- `tavern_messages`：id, chat_id, role(user|assistant), sequence, swipes jsonb（[{content, createdAt, model}]）, active_swipe int, created_at, updated_at
- `tavern_world_info`：id, owner_id, card_id, keys jsonb, content, position enum(before_char|after_char|before_example|at_depth), depth int, constant bool, probability int, order int, enabled bool, source(import|manual), created_at
- `tavern_presets`（P2）：id, owner_id, name, system_prompt, main_prompt, jailbreak_prompt, post_history_prompt, temperature, top_p, max_tokens, stop jsonb, is_default
- 复用：`character_cards`、`tavern_personas`、`regex_scripts`
- 不新增 alembic revision；用 `app.world.migrate` create_all + `ALTER TABLE ... IF NOT EXISTS`

## 4. API（前缀 `/api/reader/v1/tavern`，可信头 auth）

- `GET /characters?q=&sort=` → 卡列表（含 lastChatAt、chatCount）
- `POST /characters/imports`（multipart）、`GET/PATCH/DELETE /characters/{id}`、`GET /characters/{id}/avatar`、`GET /characters/{id}/export`
- `GET /characters/{id}/chats`；`POST /characters/{id}/chats` {greetingId?} → chat
- `GET /chats/{id}` → {chat, messages[含 swipes/activeSwipe]}
- `PATCH /chats/{id}`（title）、`DELETE /chats/{id}`
- `POST /chats/{id}/messages` **SSE**：body {text} → 先建 user 消息，再流式生成 assistant（事件：`message`(user)、`delta`、`done`、`error`）
- `POST /chats/{id}/regenerate` **SSE**：最后一条 assistant 新建 swipe 并流式返回
- `POST /chats/{id}/messages/{mid}/swipes/{index}/select`
- `PATCH /chats/{id}/messages/{mid}`（编辑 user 文本；截断其后消息）、`DELETE .../{mid}`
- 世界书：`GET/POST/PATCH/DELETE /characters/{id}/world-info`
- 系统提示词/采样：`GET/PATCH /characters/{id}/prompt`（P0 存 character_cards 扩展列或 presets 单条）
- 人设：沿用 `/tavern-personas`
- 正则：沿用 `/character-cards/{id}/regex-scripts`（暂留旧前缀）

## 5. 生成与提示词

- 提示词按 ST 顺序拼：主提示词 → 角色（名称/描述/性格/场景/示例）→ 用户人设 → 世界书（按 position/depth）→ 聊天历史（token 预算裁剪）→ 后置指令
- 采样参数透传：temperature / top_p / max_tokens / stop → `/tavern-agent/v1/stream`（插件侧扩展）
- 单条消息：assistant 文本只存最终完成内容；流式文本仅在浏览器渲染，不落库（防止半截数据）
- 上下文窗口默认 128k，按字符近似裁剪（保留最近 N 轮 + 世界书固定条目）

## 6. 客户端（重写路由与页面）

- 首页 `/`：角色卡网格（头像/名字/简介/最近聊天时间），导入、搜索；顶部「导入卡」
- `/characters/:id`：角色详情（编辑设定、世界书、示例对话、导出、开新聊天、聊天历史）
- `/chats/:id`：全屏聊天页（沉浸；顶部角色名+菜单；消息气泡；每轮操作：重生成/左右滑/编辑；底部输入）
- `/settings`：人设、预设、正则、导入导出、模型参数
- 布局：去 AppShell 的写作工作区概念，改为极简顶部栏；桌面居中 720–900px，移动端全屏
- 事件流：fetch + ReadableStream 解析 SSE；停止生成用 AbortController

## 7. 阶段

- **P0（3–4 天）**：数据模型 + 角色列表/导入/详情 + 全屏聊天 + SSE 流式 + 重新生成 + 聊天历史 + 世界书基础注入 + 系统提示/温度基础设置 + 旧模式路由全部下线
- **P1（2–3 天）**：swipe 多版本与左右切换、编辑/删除消息、ST 聊天 JSONL 导入导出、卡导出、删除故事模式服务端域与文档
- **P2（2–3 天）**：预设（主提示词/越狱/后置/采样参数）、世界书 position/depth/递归/概率、人设注入完善
- **P3（2–3 天）**：群聊、移动端打磨、全量 E2E、生产验收、文档与提交

## 8. 验收（P0）

- 导入 ST 卡 → 角色列表出现 → 点开 → 自动建聊天 → 发消息 → 流式增长 → 完成落库
- 重新生成 → 最后一条 assistant 被新版本替换（版本数 +1 预留 swipe 字段）
- 历史：返回角色页看到聊天列表，重进消息完整
- 世界书：导入 character_book → 关键词命中时注入（可用注入预览验证）
- 旧路由（/explore、/worlds/*、/conversations/world/*）全部 302 到 `/` 或 404 且无残留入口
- 匿名访问 `/tavern/` 401；DSH 内嵌面板正常
