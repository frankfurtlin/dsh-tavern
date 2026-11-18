# 酒馆 P0/P1/P2 体验升级（单用户）

来源：m03213「p0 p1 p2 都做一下，打不开的没出现了，无需关注」。
定位：私人酒馆——角色与故事的长期记忆库，DSH Agent 是常驻作者兼演员。

## P0（体验摩擦与对话控制，先行）

### P0-1 一键开聊
- 现状：探索页「开始游玩」→ `/worlds/new?world=<id>` 身份选择页 → 选人 →「进入故事」。
- 目标：探索页/卡片上的开始入口带 `auto=1`；`WorldCreatePage` 在身份页加载完成后自动选择第一个可用角色（自定义人物需手填，保持手动），自动调用 begin 并跳转 `/worlds/<id>/play?line=<lineId>`。
- 边界：auto 只触发一次；失败回退到手动身份页并提示；`?book=` 创建流程不受影响。
- owner：cards-client（client）；无需服务端改动。

### P0-2 继续上次
- 探索页顶部新增「继续上次」：优先 `GET /api/reader/v1/play-controls/world-chats?status=active&limit=1`，显示角色名 + 最近消息摘要，点击进 `/tavern/conversations/world/<sessionId>`；无会话不渲染。
- owner：cards-client。

### P0-3 流式回复（打字机）
- 服务端：`POST /world-chats/{identity}/send` 在用户消息后预先插入 assistant 占位消息（`status='pending'`、`text=''`、同 `turn_key`）。world worker 生成时每 ~600ms/≥40 字把当前 buffer（经 regex + public_stream 脱敏）写回该行，状态保持 pending；完成后 `save_reply` 写最终文本并置 saved。
- 客户端：聊天页轮询从 1s 收紧为 ~700ms；pending 且 text 非空时按“已收到字数”渐进渲染（打字效果可选，直接显示增长文本即可）；pending 无文本显示「正在写…」。
- 边界：失败保持 interrupted/failed；断线不重复插入；流式文本只用于展示，最终以 saved 为准。
- owner：app-auth（server）+ cards-client（client）。

### P0-4 重生成 / 编辑重发
- `POST /world-chats/{identity}/regenerate` body 可空：删除最后一条 assistant（saved/interrupted/failed），用该轮 user 消息重建任务（复用同一 `turn_key`、更新 user 行 task_id），返回 202 `{taskId}`。无 assistant 时 409 `NOTHING_TO_REGENERATE`。
- `PATCH /world-chats/{identity}/messages/{sequence}` body `{text}`：仅 role=user；更新文本，删除 sequence 更大的消息（含 assistant），以新文本重建任务（复用 turn_key），返回 202 `{taskId,sequence}`；空文本 422 `MESSAGE_REQUIRED`；非 user 行 422 `MESSAGE_NOT_EDITABLE`。
- 客户端：最后一条 assistant 显示「重新生成」；用户消息显示「编辑」（内联输入，保存后原样重发）；任务进行中禁用按钮（CHAT_PENDING）。
- owner：app-auth + cards-client。

## P1（角色资产与长期记忆）

### P1-1 用户人设（persona）
- 表 `world.tavern_personas`（id, owner_id, name≤80, description≤800, created_at, updated_at, deleted_at）；API `/api/reader/v1/tavern-personas` GET/POST、PATCH/DELETE `{id}`。
- `world_chat_sessions.persona_id`（nullable），PATCH `/world-chats/{id}` 支持 `personaId`（null=不注入）。
- 注入：`cards/space.py` 的 sysSuffix 追加【你的对话对象】姓名+简介（≤600 字），位置在长期记忆之后；context-preview 返回 persona 段落。
- 客户端：聊天页/卡详情可「我是谁」选择人设、管理（新建/编辑/删除），聊天顶部展示当前人设。
- owner：app-auth + cards-client。

### P1-2 导出
- 角色卡导出：`GET /api/reader/v1/character-cards/{id}/export` → PNG（v2 `chara` tEXt base64 JSON，Pillow 11.1.0 可用）或 `?format=json`；文件名 `<name>.png`。
- 聊天导出：`GET /play-controls/world-chats/{id}/export?format=jsonl` → 每行 `{"role","text","sequence","modelName","createdAt"}`。
- 客户端：卡详情「导出角色卡」、聊天页「导出聊天」。
- owner：app-auth + cards-client。

### P1-3 记忆透明
- `GET /world-chats/{id}/injection?before=<sequence>` → `{lore:[{title}], memories:[{kind,title}], persona:{name}|null}`（复用 chat_injection 的匹配逻辑，只读）。
- 客户端：聊天页「本轮记忆」抽屉/按钮，展示本轮命中条目与当前人设。
- owner：app-auth + cards-client。

### P1-4 卡库增强 + 示例卡 + 阅读感
- 卡库：搜索框（已有 q）、排序（最近更新/名称）、最近使用时间；批量导入多选文件。
- 示例卡包：客户端内置 6 张 JSON（悬疑/武侠/言情/奇幻/日常/科幻），「导入示例卡包」按钮逐个调用导入 API，已存在同名跳过。
- 阅读感：聊天正文字号/行高/字体（衬线/无衬线）设置（localStorage），阅读页与聊天页应用；固定浅色，不做暗色。
- owner：cards-client。

## P2（结构能力）

### P2-1 会话分支
- `POST /world-chats/{identity}/fork` body `{sequence}`：创建新 WorldChatSession（同 world/line/checkpoint/character/snapshot），复制 sequence ≤ 指定值的消息，标题加「（分支）」；返回 `{sessionId}`。
- 客户端：任意消息「从这里分支」，跳转新会话。
- owner：app-auth + cards-client。

### P2-2 角色卡 ↔ 世界
- 卡→世界：卡已有 world/line，卡详情「进入互动世界」→ `/tavern/worlds/<worldId>/play?line=<lineId>`。
- 世界→卡：`POST /worlds/{worldId}/character-cards` body `{characterId}`：把世界 definition 中该 character 转成 CharacterCard（新表行 + source='world-export'，复用世界 line 的 checkpoint），返回 `{cardId}`；客户端世界页/人物页「导出为角色卡」。
- owner：app-auth + cards-client。

### P2-3 Agent 创作助手
- `POST /character-cards/draft` body `{brief≤2000}` → 用 DSH 模型生成角色卡草案 JSON（name/description/personality/scenario/first_message/tags），返回可预览字段（不自动入库）。
- `POST /character-cards/{id}/greetings` body `{count?=3}` → 生成 3 条开场白候选，返回数组（不自动保存）。
- 客户端：卡库「AI 写卡」入口 + 卡详情「生成开场白」。
- owner：app-auth + cards-client。

### P2-4 语音 TTS
- 暂不做：本机无 TTS 后端，DSH 网关只提供文本模型；留作后续（可用外部 TTS provider 时再接）。文档标注。

## 验收
- P0：`scripts/check.sh --instance --open-story --cards --space --real --memory` 基线 + 新增 P 组（自动进入、继续上次、流式文本增长、重生成文本变化、编辑重发）。
- P1：新增 Q 组（persona CRUD+注入、导出 PNG/JSONL 可下载、记忆抽屉命中、卡库搜索/示例卡/字号设置）。
- P2：新增 R2 组（分支会话独立、世界导出卡可见、AI 草稿返回字段齐全）。
- 每组跑完清库回基线；生产冒烟用 systemd-run 独立 cgroup。

## 归属
- app/server/** → app-auth；app/client/src/** → cards-client；scripts/verify.mjs/docs/testing.md → verifier；本文档与 README/architecture/contract → plugin-core（P2 完成后统一更新）。
