# 酒馆 B2：群聊（多卡同聊）

前置：B1 已上线（卡空间/世界书/预设/正则）。本步只新增群聊，不改单卡聊天与世界聊天路径。

## 1. 数据模型（app/server）

- `world.card_group_chats`：id、owner_id、title(≤80，默认「A、B 的群聊」)、card_ids JSONB（有序，2–5 个）、preset_id 可空、status('active')、version、created_at、updated_at。
- `world.card_group_messages`：id、owner_id、chat_id、sequence、role('user'|'assistant')、speaker_card_id 可空、speaker_name、text(≤8000)、task_id、status('saved'|'pending'|'failed')、created_at。
- 索引 (chat_id, sequence)。删除群聊：硬删 messages + chat（最小实现）。

## 2. API（/api/reader/v1，沿用 trusted 会话）

- `GET /card-group-chats` → `{items:[{id,title,cards:[{id,name}],lastText,updatedAt}]}`
- `POST /card-group-chats {cardIds:[...],title?}` → 201 `{chat}`（2–5 张、去重、校验本人卡；默认 title=「A、B 的群聊」）
- `GET /card-group-chats/{id}` → `{chat, cards:[{id,name,description,avatarUrl?}], messages:[{id,role,speakerCardId,speakerName,text,status,createdAt}]}`
- `POST /card-group-chats/{id}/messages {message, turnId}` → 202 `{taskId,chatId,speakerCardId,speakerName}`；Idempotency-Key + turnId 幂等；同一会话 pending 时 409 `CHAT_PENDING`。
- `DELETE /card-group-chats/{id}` → 204
- 发言人轮转：speaker = card_ids[（已保存的 assistant 条数）% n]，创建任务时写入 payload 并返回，客户端显示「下一位：X」。

## 3. 生成（app/server，新任务类型 card_group_chat）

- 提示词 `app/server/app/prompts/card_group_chat.txt`（v1）：多角色聊天室，本轮只演 speaker；输出纯文本、无 JSON；可动作描写；不得替读者说话；100–500 字。
- 输入 data：`{cards:[{name,description,personality,scenario,systemPrompt}], speaker:{id,name}, conversation:[{role,speakerName,text}], message, presetSuffix?, rule}`；卡字段各截断 1200 字，历史取最近 30 条。
- 复用 worker 的 adapter_for()（dsh/mock/real），generation 单阶段，无 attribution/validation。
- 落库前应用 B1 的 regex（cards.space.apply_regex），失败则消息 status=failed 并保留错误给前端轮询。
- 任务完成写 assistant 消息；worker 崩溃恢复沿用现有 task 机制即可（最小实现允许 failed 状态手动重发）。

## 4. 客户端（app/client/src/characters/**）

- 角色卡库：卡片多选（勾选 2–5 张）→ 底部/头部「发起群聊」按钮；另加「群聊」列表入口（可与卡库页并列的 tab），点击进入群聊页。
- `/characters/groups`：群聊列表（标题、参与卡、最后一句、时间，可删除）。
- `/characters/groups/:id`：群聊页（参与者头像/名字、消息气泡带发言人名、输入发送、发送后 1s 轮询直到新 assistant 消息或失败；显示「下一位：X」；空状态）。
- 测试：api 形态 + 创建/发送/轮询/删除交互 ≥8；dist 重建。

## 5. 验收（verifier，T 组，隔离 3099 + 真实网关）

- T1 卡库多选两张卡 → 发起群聊 → 进入群聊页，参与者正确；
- T2 发送消息 → 出现真实回复且发言人 = 返回的 speakerName；
- T3 再发一条 → 发言人轮转（与上一条不同）；
- T4 整页刷新后消息仍在；
- T5 删除群聊 → 列表数量回落；
- T6 console clean。
预期：既有 65 项（含 S 组）全绿 + T 组全绿。跑完清理群聊数据与测试卡（基线 cards=0、worlds=6、group_chats=0）。

## 6. 归属

app/server/** → app-auth；app/client/src/characters/**、main.ts、nav、page-location.ts → cards-client；scripts/**、verify.mjs、docs/testing.md → verifier；docs/**、部署/清理 → Lead。
