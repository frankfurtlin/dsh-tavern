# 酒馆 2.0 P3-d：群聊（多角色同场）

状态：设计稿（P3 第一阶段之后执行）
依赖：P3-a 服务端瘦身完成（app/server 只剩酒馆域）、P3-b/c 原生上下文桥完成

## 1. 产品行为

- 角色卡网格多选 2–5 张 →「发起群聊」→ 进入全屏聊天页（与单聊同一页面）。
- 群聊里每条 assistant 消息有明确发言人（头像 + 名字 + 颜色标识），用户消息无发言人。
- 服务端按参与者顺序轮转发言：`speaker = members[assistant_count % member_count]`（用户每次发言后生成一条；重生成沿用原发言人）。
- 提示词：所有参与者的人设都进入 system prompt（各自 `name/description/personality/scenario`，每人限量），并显式声明「本轮只扮演 <speaker>，其他人作为场景中的角色存在，不要替他们说话，也不要替读者说话」。
- 群聊同样支持：SSE 流式、停止、重新生成（同一发言人追加 swipe）、编辑用户消息、删除消息、分页（沿用 P1 逻辑）。
- 世界书/预设/人设：沿用 chat 级设置（单聊逻辑复用）；世界书关键词命中对所有成员通用。

## 2. 数据模型（复用现有表，避免第二套）

表 `world.tavern_chats` 增列：

| 列 | 类型 | 说明 |
|---|---|---|
| `mode` | varchar(8) not null default 'single' | `single` / `group` |
| `members` | jsonb not null default '[]' | `[{cardId, name, avatarPath, order}]`，群聊 2–5，单聊为 `[]` |

表 `world.tavern_messages` 增列：

| 列 | 类型 | 说明 |
|---|---|---|
| `speaker_card_id` | varchar(32) null | 群聊 assistant 行必填；单聊 null |

`tavern_chats.card_id` 对群聊置 null（或取第一个成员），以 `members[0].cardId` 为准；`GET /chats/{id}` 返回 `mode/members`。

迁移：`app/server/app/world/migrate.py` 增加 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，幂等。

## 3. API（/api/reader/v1/tavern）

- `POST /chats` 扩展 body：`{cardId}`（单聊，兼容现网）或 `{cardIds:[2..5], title?}`（群聊）。
  - 群聊返回 `{chat:{mode:'group', members:[...]}}`；成员必须存在、未删除、同 owner；重复 id 去重后仍须 2–5，否则 422 `GROUP_MEMBERS_INVALID`。
- `GET /chats/{id}`：返回 `mode`、`members`、messages（含 `speakerCardId/speakerName`）。
- `POST /chats/{id}/messages`（SSE）：行为不变，服务端决定本轮 speaker 并写入 `speaker_card_id`；首帧 `message` 增加 `speaker:{cardId,name}`。
- `POST /chats/{id}/regenerate`：沿用原消息的 `speaker_card_id`。
- 其余（PATCH/DELETE/swipe/export/import）不变；导出 JSONL 时 group 消息带 `speaker_name`。

## 4. 服务端实现要点

- `tavern/prompt.py`：单聊分支不变；群聊新增 `build_group()`：全部成员卡字段（name/description ≤500/personality ≤300/scenario ≤500）+「本轮由 X 发言」+ 历史 + 后置指令；复用现有世界书/预设/人设注入与采样优先级。
- `tavern/stream.py`：message 与 regenerate 的 speaker 解析集中在 `_speaker_for(chat, db)`；写入 `speaker_card_id`。
- 轮转计数用 `SELECT count(*) FROM tavern_messages WHERE chat_id=:id AND role='assistant'`；并发下同一 chat 已有 streaming 保护（409），无需额外锁。

## 5. 客户端

- `TavernCharactersPage.vue`：卡片多选（checkbox，2–5 上限提示）+ 底部操作条「发起群聊（N）」；群聊入口不单独做列表页，历史聊天从卡详情/群聊页进入（P3 范围）。
- `TavernChatPage.vue`：assistant 气泡显示头像（`/tavern/characters/{id}/avatar`）+ 名字；页头显示参与者头像堆叠；发送/流式/swipe/编辑/删除复用。
- `TavernCharacterPage.vue`：单卡「新聊天」不变。
- 需要「我的聊天历史」入口（P3 顺手做：`/chats` 列表页，按 last_message_at 排序，显示成员头像与最后一句）。

## 6. 验收（verify.mjs 新增 G 组）

| 断言 | 说明 |
|---|---|
| G1 | 卡库多选 2 张 →「发起群聊」→ 进入 /chats/:id，页头显示 2 个参与者 |
| G2 | 发一条消息：SSE 完成，assistant 气泡带发言人 A 的名字/头像 |
| G3 | 再发一条：发言人为 B（轮转），数据库 `speaker_card_id` 正确 |
| G4 | 刷新后发言人与消息顺序保持；`GET /chats/{id}` 的 members/mode=group |
| G5 | 重新生成：仍是同一发言人，swipe 2 版本可切换 |
| G6 | 单聊回归：`POST /chats {cardId}` 行为不变、无 speaker 字段、既有 41 项无回归 |
| G7 | console 无错误、退出后无残留数据 |

测试数据：导入 2 张 fixture 卡建群，跑完删群聊 + 卡，回基线（cards=0/chats=0/messages=0）。

## 7. 归属

| 范围 | owner |
|---|---|
| `app/server/app/tavern/**`、`app/server/app/world/migrate.py`、`tests/test_tavern_group_chat.py` | app-auth |
| `app/client/src/tavern/**`（卡片多选/群聊气泡/历史列表） | cards-client |
| `verify.mjs` G 组、`scripts/check.sh`、`docs/testing.md` | verifier |
| 迁移执行、部署、生产冒烟、清理、commit | Lead |
