# 酒馆 B1：角色卡空间（世界书 / 预设 / 正则 / 卡编辑）

状态：进行中（B2 群聊另立文档）。目标：把「导入一张卡、单卡聊天」升级成「卡片可维护、可配置、能带设定」的最小空间。

## 1. 范围（B1）

1. **卡详情与编辑**：查看/修改 name、description、personality、scenario、first_message、system_prompt、post_history_instructions、tags。
2. **世界书（lorebook）**：导入时解析 ST v2/v3 `character_book.entries`；每张卡可增删改条目；聊天时按关键词命中注入。
3. **预设**：用户级预设（名称 + 追加系统提示），每张卡选择其一，聊天时注入。
4. **正则**：用户级输出替换规则（查找/替换/启用/顺序），模型回复保存前应用。
5. **可观测**：`POST /character-cards/{id}/context-preview` 返回本回合将注入的世界书条目与预设，供 UI/验收断言。

不在 B1：多卡群聊（B2）、长期记忆抽取（C）、卡导入时自动生成世界定义（继续沿用现有 world 管道）。

## 2. 数据模型（app/server，app-auth）

- `world.card_lore`：id、owner_id、card_id、name(≤160)、keys JSONB(list[str], ≤20)、content(≤8000)、enabled、order int default 100、source('import'|'manual')、created_at、updated_at。索引 card_id。
- `world.chat_presets`：id、owner_id、name(≤80)、system_suffix(≤4000)、enabled、created_at、updated_at。
- `world.regex_scripts`：id、owner_id、name(≤80)、pattern(≤500)、replacement(≤2000)、enabled、order int default 0、created_at、updated_at。
- `world.character_cards` 新增可空列 `preset_id`（FK chat_presets 不强制，删除预设时置空）。
- 建表沿用 create_all：app/server/app/world/migrate.py 与 app/server/app/main.py 需 import 新模型。

## 3. API（前缀 /api/reader/v1，沿用 x-dsh-trusted 会话）

- `PATCH /character-cards/{id}` 局部更新；返回 `{card}`（card 字段为既有 CardOut + presetId）。
- `GET /character-cards/{id}/lore` → `{items:[{id,name,keys,content,enabled,order,source}]}`
- `POST /character-cards/{id}/lore` `{name?,keys:[...],content,enabled?,order?}` → 201 `{lore}`
- `PATCH /character-cards/{id}/lore/{loreId}` 局部更新；`DELETE` → 204
- `GET /presets` → `{items}`；`POST /presets {name,systemSuffix,enabled?}` → 201；`PATCH|DELETE /presets/{id}`
- `GET /regex-scripts` → `{items}`；`POST {name,pattern,replacement,enabled?,order?}`（非法正则 422 `REGEX_INVALID`）→ 201；`PATCH|DELETE /regex-scripts/{id}`
- `POST /character-cards/{id}/context-preview {message}` → `{loreMatched:[{id,name,keys}], preset:{id,name}|null, systemSuffix}`

## 4. 聊天注入与正则（app/server，app-auth）

- 任务 `world_character_chat` 的 payload 含 `chatId`：由 session.world_id → character_cards 找卡。
- 生成时在 system prompt 末尾追加（顺序）：`【角色卡设定】`（description/personality/scenario/system_prompt/post_history_instructions，取非空，≤6000 字）、`【世界书】`（命中条目 name+content）、`【预设】`（system_suffix）。开头统一加一句：仅作扮演补充，与 source 冲突时以 source 为准。
- 世界书命中：enabled 条目 keys 大小写不敏感地出现在「本条用户消息 + 最近 4 条用户消息」中；最多 6 条、合计 ≤6000 字，按 order 升序。
- 正则：生成结果通过校验后、`save_reply` 前按 order 升序逐条 `re.sub`（仅第一条/全局替换按 pattern 自身语义）。流式 delta 不改（UI 最终以落库文本为准），文档注明。
- 校验不过/失败路径不应用正则。

## 5. 客户端（cards-client）

- `/characters/:id` 卡详情页：显示头像/名/标签，编辑表单（保存 PATCH），世界书列表（增删改条目，展示 source），预设选择下拉 + 「新建预设」弹层。
- `/characters/regex` 输出替换规则页：列表 + 新增/编辑/开关/删除（从卡详情或角色卡库进入）。
- api.ts 增加 getCard/updateCard/lore CRUD/presets CRUD/regex CRUD/contextPreview。
- 测试：vitest 组件/接口测试 ≥10；`APP_BASE=/tavern npm run build` 重建 dist（静态服务读盘即生效，无需重启 web）。

## 6. 验收（verifier）

`--space` 新增 S 组（隔离 3099，复用现有 fixture 机制）：
- S1 卡详情改名保存 → 刷新后生效；
- S2 导入带 character_book 的 v2 卡 → 世界书列表出现导入条目；
- S3 手工新增世界书条目（key 雨）→ 列表可见、可删除；
- S4 新增正则 `。`→`！` → 聊天发消息 → 刷新后回复无「。」且含「！」；
- S5 新增预设并绑定卡 → context-preview 返回该预设；
- S6 console clean。
预期：既有 59 项全绿 + S 组全绿。

## 7. 文件归属

- app/server/**：app-auth
- app/client/src/characters/**、main.ts 路由、navigation/capabilities：cards-client
- scripts/verify.mjs、scripts/check.sh、scripts/fixtures/**、docs/testing.md：verifier
- docs/**、上线切换脚本、生产清理：Lead
