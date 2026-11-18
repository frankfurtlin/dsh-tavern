# 酒馆 C：长期伙伴与记忆抽取

状态：设计（B1 之后实施）。目标：让酒馆跨会话记住读者与角色之间的事，并把常用角色变成「长期伙伴」。

## 1. 范围

1. **记忆条目**：每张卡一份记忆库（事实/偏好/关系/约定），支持自动整理与手工增删改、开关。
2. **记忆注入**：聊天生成时把高权重记忆带进 system prompt，让角色自然「记得」之前聊过的事。
3. **记忆整理**：「立即整理」按钮 + 每 8 条助手回复自动触发（后台任务）两种；用 DSH 模型抽取，去重、限 40 条。
4. **长期伙伴**：卡可标记为伙伴（置顶分组 + 入口），聊天记录沿用「我的对话」。
5. 不做：跨卡共享记忆、情感数值、主动消息推送。

## 2. 数据模型（app/server，app-auth）

- `world.card_memories`：id、owner_id、card_id、kind('fact'|'preference'|'relationship'|'event')、title(≤120)、content(≤2000)、salience int 1–100 default 50、enabled、source('auto'|'manual')、created_at、updated_at。索引 (card_id, enabled)。
- `world.character_cards` 新增 `companion bool default false`、`memory_task_id`（进行中的整理任务）。
- 记忆上限 40 条/卡，超出按 salience 低→高淘汰自动来源条目。

## 3. API（/api/reader/v1）

- `GET /character-cards/{id}/memories` → `{items:[{id,kind,title,content,salience,enabled,source,updatedAt}]}`
- `POST /character-cards/{id}/memories` `{kind,title,content,salience?}` → 201
- `PATCH /character-cards/{id}/memories/{mid}`（含 enabled/salience）；`DELETE` → 204
- `POST /character-cards/{id}/memories/extract` → 同步调用模型抽取，返回 `{added:[...], skipped:int}`；无新记忆返回 added=[]（200）；模型/解析失败 502 `MEMORY_EXTRACT_FAILED`（不写半成品）。
- `POST /character-cards/{id}/companion` `{companion:true|false}` 或并入 PATCH。
- 自动整理：每次助手回复保存后计数，满 8 条入队 `card_memory_extract` 任务（worker 任务类型，幂等 taskId=`memory:{cardId}:{seq}`，失败静默重试 1 次）。

## 4. 抽取提示词（app/server/app/prompts/card_memory_extract.txt）

输入（user 消息 JSON）：`{card:{name,description,personality,scenario}, existingTitles:[...], messages:[{role,text}]}`（最近 60 条，截断 12000 字）。
输出严格 JSON：`{"memories":[{"kind":"...","title":"...","content":"...","salience":50}]}`；要求：只记读者自述/双方约定/关系变化/反复出现的偏好，不记世界事件与剧情；每条 ≤2 句；与 existingTitles 语义重复不要输出；无内容输出 `{"memories":[]}`。解析失败重试一次后 502。

## 5. 注入（app/server）

生成时在既有【角色卡设定】【世界书】【预设】之后追加【长期记忆】：取 enabled 记忆按 salience 降序 + updated_at 降序，最多 6 条、合计 ≤3000 字；条目标题+内容用「- 」列表。冲突规则同前：仅作扮演补充，与 source 冲突以 source 为准；记忆里读者自述的事实可用，但不能当作世界正史。

## 6. 客户端（cards-client）

- 卡详情新增「记忆」tab：列表（kind 徽标/权重/来源/开关）、新建/编辑弹层、删除、「立即整理」按钮（loading ≥ 抽取时间，成功提示「新增 N 条」）。
- 角色卡库顶部「长期伙伴」分组/吸顶；卡详情可切换「设为长期伙伴」。
- 测试 ≥8；dist 重建。

## 7. 验收（verifier，C 组）

- C1 手工新增记忆（title「读者养猫」，content「读者养了一只叫团子的猫」）→ 列表可见、可编辑、可删除；
- C2 调「立即整理」（E2E 前先发 2–3 条含自述消息）→ 返回 added 数组、列表条数增加（内容不强制精确，但至少 1 条 title/content 非空）；
- C3 新开会话 → context-preview 含该记忆标题；
- C4 关闭/删除记忆后 preview 不再包含；
- C5 伙伴切换后角色卡库顶部出现该卡；
- C6 console clean。
报告与截图落 /tmp/dsh-tavern-verify，失败归属清楚。

## 8. 文件归属

同 B1：app/server/** → app-auth；app/client/src/characters/** → cards-client；scripts/**、docs/testing.md → verifier；文档/部署/生产清理 → Lead。
