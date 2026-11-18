# dsh-tavern · 酒馆（DSH 插件）

把本地「酒馆」装进 DSH：DSH 侧栏多一行 **酒馆**，点开不用跳站，面板里就是
**角色卡网格**；点一张卡进入**全屏聊天**（`/tavern/chats/<id>`），回复按 SSE
**逐字流出**，重新生成会追加一个**版本**（`‹n/m›` 切换），角色卡的世界书按
关键词注入本轮 prompt。角色卡库还能多选 2–5 张卡**发起群聊**（同一场里按顺序
轮转发言），`/tavern/chats` 是聊天历史页。生成走 DSH 当前模型（本机
`opencode-go / deepseek-v4.1-flash`），不依赖浏览器里的模型登录态。

酒馆 2.0 是**单用户原生酒馆**：故事模式、探索作品、世界、分支、长期记忆、
「长期伙伴」、卡↔世界、AI 写卡等旧能力已随 **P3 彻底删除**（UI 与服务端代码
都不在了，旧 API `/worlds`、`/world-chats`、`/books`、`/play-controls` 一律 404）。
服务端现有 **86 条路由**（`/api/reader/v1/tavern/**` 36 条）；旧 story 表已按备份
**DROP**（备份见下）。界面**固定浅色**，没有明暗切换。

- 侧栏顺序：紫微斗数(20) → **酒馆(24)** → Skill Center(30)
- 插件目录：`/root/workspace/plugins/dsh-tavern`，已在
  `/root/.dsh/profiles/web/package.json` 里以 `link:` 依赖注册进 `dsh.profile.bundles`
- 应用目录：本仓库 `app/`，由四个 systemd 单元跑在回环地址上：
  - `dsh-tavern-api.service` → `127.0.0.1:8001`（FastAPI / uvicorn；聊天 SSE 在**请求线程**里直连 DSH 网关）
  - `dsh-tavern-web.service` → `127.0.0.1:8810`（`app/client/dist` 静态站）
  - `dsh-tavern-worker.service` → 旧任务队列 worker；2.0 生成在 API 请求线程里同步完成，host 已 **停用并 disable**（进程会因新 worker.py 无 main 循环而退出，勿重启）
  - `dsh-tavern-world-worker.service` → 旧 story worker；P3 删掉 `app.world.worker` 后已由 host **停用并 disable**（旧单元文件仍可查到，但不再启动）
- 公开入口只有 `https://dsh.frankfurtlin.top/tavern/`（受 DSH 登录保护）。
  插件内部只连 `127.0.0.1`，不额外开端口

## 功能一览

| 能力 | 说明 |
| --- | --- |
| 角色卡 | 角色卡网格 + 搜索；导入 SillyTavern 卡（PNG / JSON，v2/v3 `character_book` 自动进世界书）；卡详情改设定（简介/性格/场景/开场白/系统提示词/后置指令/标签）、导出 PNG/JSON、删除（软删）；头像 |
| 聊天 | 点卡直接进最近一段对话，没有则新建；`/chats/<id>` 全屏聊天：发送、**SSE 逐字流式**、停止生成、重新生成（追加 swipe）、`‹n/m›` 切换版本、编辑/删除自己的消息、改聊天标题、**导出聊天**（JSONL / JSON）、**导入聊天记录**（ST JSONL，仅单聊）；长聊天按 200 条分页，向上翻「加载更早」 |
| 群聊 | 角色卡库勾选 2–5 张卡 → 「发起群聊」；`mode='group'` 聊天里成员头像常驻，assistant 气泡带发言人名字/头像，每轮按成员顺序**轮转发言**；重新生成沿用原发言人；导出 JSONL 带 `speaker_name` |
| 聊天历史 | `/chats` 页：全部聊天按最近消息倒序，群聊显示成员名，点进对应聊天 |
| 世界书 | 卡详情维护条目：关键词（≤20 个）、内容（≤8000 字）、注入位置（角色前/角色后/示例前/指定深度）、注入深度（`at_depth` 用，0–100）、常驻、概率（0–100%，确定性抽样）、顺序（升序注入）、启用；导入卡自带条目；卡详情有**注入预览**：输入一句示例消息，返回命中的条目、命中原因（常驻 / 关键词「X」）与拼好的注入文本 |
| 设置 | `/settings`：人设（多个人设 + 默认人设）、预设管理（新建/编辑/设为默认/删除；system / main / jailbreak / post_history + temperature / top_p / max_tokens / stop）、输出替换规则页 `/settings/regex`（账号级正则 CRUD、启停、两步删除、客户端校验；规则在回复落库前生效，刷新后可见替换结果）、导入导出说明 |
| 采样与提示词 | 聊天页「采样参数」Sheet（temperature / top_p / max_tokens / stop，落库并回显）；卡详情「提示词与采样」（主提示词 / 越狱提示词 / 采样覆盖 + 世界书默认位置）；生成时按 **chat > 预设 > 卡设置** 逐级回退 |
| 生成通道 | `AI_MODE=dsh` 时应用服务端走插件网关 `/tavern-agent/v1/stream` → `ctx.llm.stream`；网关不可用就如实报错，不伪造回复 |

## 使用路径

1. DSH 侧栏点 **酒馆**（等价于打开 `/tavern/`），面板里是角色卡网格；
   顶栏「酒馆」品牌 + 右侧「设置」。
2. 首次使用：点「导入卡」上传 SillyTavern 角色卡（PNG 或 JSON），卡出现在网格里；
   搜索框按角色名 / 简介过滤；顶栏「聊天历史」进 `/chats` 看全部会话。
3. 点卡片主体直接进聊天：有历史进最近一段，没有就按开场白新建；
   卡片右上「设置」进卡详情。
3b. 想多个角色同场：在卡库勾选 2–5 张（最多 5 张），底部「发起群聊（N）」→
   进入群聊；发言顺序按勾选顺序轮转，assistant 气泡上会标发言人。
4. 聊天页：输入框发消息 → 回复逐字出现 → 「停止」可中断（已收到的内容不落库）；
   最后一条回复可「重新生成」，每次生成追加一个版本，用 `‹n/m›` 切换；
   点「编辑」可改自己的消息（会截断其后的消息）；标题可点开改名；可导出聊天。
5. 想让模型知道更多设定：卡详情「世界书」加条目（关键词命中本轮消息或最近 4 条
   用户消息时注入；常驻条目每轮都注入；`at_depth` 条目按深度插到历史里），
   改完用「注入预览」核对命中原因与注入文本；卡详情「角色设定」「提示词与采样」
   可覆盖角色设定、主提示词、越狱提示词与采样参数。
6. 需要多套方案时去「设置」：人设（我是谁）建多个人设并选默认；预设保存
   system / main / jailbreak / post_history 与采样参数，设为默认后用于新聊天，
   单个聊天可在聊天页「采样参数」里覆盖；「输出替换规则」页维护账号级正则（回复落库前生效，刷新后可见替换结果；
   旧任务会应用），导入导出说明也在设置页。

## 预设角色卡

新账号第一次打开酒馆（第一次加载角色卡库）时，会自动出现 5 张**预设角色卡**
（不需要导入；种子在首次列卡时懒创建，资产缺失也不影响卡库）：

| 名称 | slug | 说明 |
| --- | --- | --- |
| 玄净 | `xuanjing` | 归墟剑宗剑尊、东峰之主，剑修界的「霜声剑主」 |
| 裴聿 | `peiyu` | 大理寺少卿，长安官场称「阎罗笔」 |
| 奈芙尔 | `naifuer` | 黑沙氏族战争祭司，白鬃战猫族裔，被俘押送途中 |
| 沈砚 | `shenyan` | 砚台资本创始人，前沈氏建材三少爷 |
| 唐团团 | `tangtuantuan` | 「团团面包屋」老板娘，23 岁 |

- 点卡即可直接开聊（开场白、角色设定、世界书都已随卡导入）。
- 卡库/卡详情会带「预设」徽标（`builtin`），说明它来自内置种子。
- 删除预设卡后**不会自动恢复**（种子按 `builtin_slug` 只补缺失的 slug，
  已存在或被软删的都跳过）；想找回只能清库/换新账号，或自行重新导入。
- 来源：[foreverse-app/character-card-skills](https://github.com/foreverse-app/character-card-skills)，
  作者 **Foreverse Team**（[foreverse.app](https://foreverse.app)），
  内容许可 **[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**。
- 我们做了两处格式修改：**HTML/CSS 文本转纯文本**、**头像裁成 512×512 方形缩略图**；
  卡片内容除此之外未改动。逐卡来源与署名见 [CREDITS.md](CREDITS.md)。

## 它由哪几块组成

| 文件 | 作用 |
| --- | --- |
| `package.json` | 声明 `dsh.bundle.patch`（cordis patch）、`dsh.client.platform=web`、`exports["./client"]`；`npm run check` 做语法检查 |
| `cordis.patch.yml` | `- insert: [{id: dsh-tavern, name: dsh-tavern}]`，`id` 必须等于包名 |
| `lib/index.js` | host 半：注册 `prefix /tavern` 反向代理（旧 `/story` 为 302 兼容别名）；先过 DSH 登录闸门；`/api/**`、`/health/**` → 8001 并附加可信头，其余 → 8810 静态站。另外注册 `prefix /tavern-agent` 模型网关，给应用服务端调 DSH 真生成 |
| `client.js` | client 半：注册侧栏行（`sidebar.panellist` 座位）+ 主面板（`main` 座位，`key=tavern`）；工具条 + 同源 iframe（`tavern/`）；记住应用最后的路由以便切面板后恢复 |
| `app/` | 酒馆 2.0 应用本体（Vue 前端 + FastAPI 后端）：`tavern/` 模块（角色/单聊/群聊/聊天历史/世界书/prompt/SSE）、`cards/` 角色卡解析与导出。旧 story 域代码已在 P3 删除 |
| `CREDITS.md` | 第三方内容署名：5 张预设角色卡的来源 URL、作者、CC BY 4.0 许可与修改说明 |
| `docs/contract.md` | 与应用侧的集成契约（路由分流、可信头、P2、UI 座位、网关字段、应用 API/SSE/swipe）——**先读它再改代码** |
| `docs/architecture.md` | 实现架构：请求链路、网关、原生酒馆数据模型与 prompt 组装、面板结构、配置、风险 |
| `docs/step6-native-tavern.md` | 酒馆 2.0 的设计与验收清单（当前设计） |
| `docs/history/step1–step5*.md` | **历史设计**：故事模式 / 旧卡片空间 / 旧群聊 / 长期记忆 / P0–P2 体验；其中 UI 与服务端多已删除，仅作参考 |
| `docs/testing.md` | 验收方法：静态检查 + 隔离实例真实浏览器 E2E |
| `scripts/check.sh`、`verify.mjs` | 验收工具：静态检查 + 隔离实例端到端 |

> 应用本体（`app/`）需要先在 host 上跑起来，插件才有内容可代理。四个 systemd
> 单元已安装在 `/etc/systemd/system/dsh-tavern-*.service`，属于 host 运维，
> **不随本插件仓库分发**；本仓库只含插件两半 + 应用源码。

## 安装 / 启用

插件以 `link:` 方式注册在 Web profile（不在本仓库内执行这一步）：

```json
"dependencies": { "dsh-tavern": "link:/root/workspace/plugins/dsh-tavern" },
"dsh": { "profile": { "bundles": [ "...", "dsh-tavern" ] } }
```

应用以 host 侧 systemd 单元运行（单元文件在 `/etc/systemd/system/`，不在本仓库内）；
插件本身由 `dsh web` 进程加载，改完 `lib/` 或 `client.js` 后需要**重启 DSH Web 服务**
并刷新页面。本仓库的插件代码没有独立构建步骤：`cordis.patch.yml` + 手写
`client.js` 由宿主直接加载。

## 请求链路一句话

浏览器 → DSH Web `:3080` → 登录闸门（`requestRejection`）→ `prefix /tavern`
→ 剥前缀：`/api/**` 带 `x-dsh-trusted` 去 `:8001`，静态资源去 `:8810`。
chat 的 `POST /api/reader/v1/tavern/chats/<id>/messages` 由 8001 **直接在响应里
回 SSE**；应用服务端同时以 Bearer secret 调 `/tavern-agent/v1/stream` 拿模型增量。
详细分流表、头处理、超时策略见 [docs/architecture.md](docs/architecture.md)。

## DSH 模型网关：应用服务端怎么用真生成

应用的 API 进程是服务端进程，没有浏览器会话。插件在 `/tavern` 旁边开了一个
平级前缀 `/tavern-agent`，用共享 secret 做服务端到服务端鉴权，复用 DSH 已经
跑着的模型通道（`ctx.llm.stream`）：

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/tavern-agent/v1/health` | `GET` | 探活 + 返回当前默认模型 + 支持的采样参数 `features` |
| `/tavern-agent/v1/complete` | `POST` | 一次性返回整段文本 |
| `/tavern-agent/v1/stream` | `POST` | SSE 增量返回（每段文本一帧，末尾 `data: [DONE]`） |

- 鉴权：`Authorization: Bearer <secret>`；secret 读 `app/.env` 的
  `DSH_TAVERN_AGENT_SECRET`，缺省回退 `DSH_TRUSTED_SECRET`，**没配则一律 401**；
- 模型：请求可用 `model:{provider,model}` 指定，否则用 DSH 当前默认模型
  （`agentDefaultModel`）；可选 `sessionId`（opencode-go 等 provider 靠它做上游会话）；
  `reasoningEffort` 默认 `low`（避免把 maxTokens 烧在思考里）；
- 采样参数：`temperature`（0–2）、`topP`（0–1，兼容 `top_p`）、`stop`
  （string 或 ≤4 条、单条 ≤200 字符）已支持透传，非法值 400 `INVALID_ARGUMENT`；
- **生效边界**：网关只把它们放进 `ctx.llm.stream` 的选项；本机 `opencode-go`
  的 pi-ai 适配器只消费 temperature/maxTokens/sessionId，**topP 与 stop 会被忽略**，
  且采样参数要等 DSH Web 重启后新代码才生效；
- 失败：401 `AGENT_UNAUTHORIZED`、400 `AGENT_BAD_REQUEST`/`INVALID_ARGUMENT`、
  502 `AGENT_UPSTREAM_FAILED`；SSE 开始后失败走 `{"type":"error"}` 帧；
- 超时 120s，客户端断开/超时会中断上游；日志只记模型名、字数、耗时、错误码，
  **不记 prompt、消息内容和 secret**。

应用侧在 `AI_MODE=dsh` 时使用它（`DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1`，
`DSH_AGENT_SECRET` 与 `DSH_TAVERN_AGENT_SECRET` 同值）；回滚改 `AI_MODE=mock`。

## 配置

| 位置 | 键 | 说明 |
| --- | --- | --- |
| `app/.env` | `AI_MODE` | `dsh` = 应用走插件网关用 DSH 模型真生成；`mock` = 内置确定性回复（离线自测/回滚）；默认 `real`（应用自己的 provider 通道，与插件无关） |
| `app/.env` | `DSH_AGENT_URL` | 应用侧调网关的地址，生产是 `http://127.0.0.1:3080/tavern-agent/v1` |
| `app/.env` | `DSH_AGENT_SECRET` | 应用调网关的 Bearer secret，与 `DSH_TAVERN_AGENT_SECRET` 配同一个值 |
| `app/.env` | `DSH_TRUSTED_SECRET` | 应用侧校验可信登录头的 secret，插件转发 API 请求时附加 |
| `app/.env` 或宿主环境 | `DSH_TAVERN_AGENT_SECRET` | 模型网关 Bearer secret；缺省回退 `DSH_TRUSTED_SECRET`，都没有则网关一律 401 |
| 宿主环境 | `DSH_TAVERN_TRUSTED_SECRET` | 覆盖可信登录 secret（隔离测试 / 应急时用） |
| 宿主环境 | `DSH_TAVERN_API_PORT` | 覆盖 API 上游端口（默认 8001） |
| 宿主环境 | `DSH_TAVERN_STATIC_PORT` | 覆盖静态上游端口（默认 8810） |
| 宿主环境 | `DSH_TAVERN_ENV_FILE` | 覆盖读取的 `.env` 路径（隔离测试用，生产不设） |
| 宿主环境 | `DSH_TAVERN_AGENT_TIMEOUT_MS` | 覆盖模型网关超时（默认 120000；仅测试用） |

secret 在插件启动（`apply`）时读入；读不到会打印告警，但不会让宿主崩溃，
此时 API 请求不带可信头，应用侧按未登录处理（401）。

## 改完代码先自检

```bash
cd /root/workspace/plugins/dsh-tavern

node --check lib/index.js && node --check client.js   # 同 npm run check
npm run check
```

涉及 `/tavern` 反代、面板或应用 UI 的改动，请再跑端到端验收（真实浏览器）：

```bash
bash scripts/check.sh                 # 静态检查 + 隔离 DSH 实例 + verify.mjs（默认）
bash scripts/check.sh --static        # 只跑静态检查（快）
bash scripts/check.sh --instance      # 只跑隔离实例 + E2E
bash scripts/check.sh --with-app-tests # 额外跑应用前端 typecheck + 单测（慢）
```

也可以直接驱动 `verify.mjs`（需要已有一个跑起来的 DSH Web 和 token）：

```bash
node verify.mjs --url "http://127.0.0.1:3080/?token=..." \
  --fixtures /tmp/dsh-tavern-native/cards --out /tmp/dsh-tavern-native/check
```

- `verify.mjs` 检查分组（设计见 `docs/step6-native-tavern.md` §8）：
  A 基础（匿名 401、侧栏入口、iframe、原生 UI 已交付）、N 角色列表（导入/头像/搜索）、
  C 聊天（开新聊、用户气泡、assistant 文本**流式增长**、刷新后仍在）、
  C2 停止生成、R 重新生成、P swipe 版本切换、E 编辑/删除消息、T 聊天标题、
  H 历史、S 世界书（条目增删 + 注入预览）、I 导入导出（聊天 JSONL/JSON 下载与解析、
  角色页导入聊天记录）、W 世界书深度语义（position/depth/constant/probability/order
  保存回显 + 预览命中原因）、S2 采样参数（聊天页 Sheet 保存 → 刷新回显 → 带参生成）、
  Q 预设与提示词（新建预设 → 设默认 → 新聊天 prompt 含预设文本；非法采样 422）、
  P3 工具条（`/chats` 聊天上下文 → 「让 DSH 续写/角色扮演」预填 DSH 输入框；
  无上下文时按钮禁用）、M 旧路由（`/tavern/explore`、`/works` 等不死链，页面无
  「探索作品」入口）；`--only c,n,p3` 可只跑部分分组；
- 最新一轮完整验收（隔离实例 + 生产各跑一遍）：全部 PASS，条数以
  `report.json` 的 `summary` 为准（P3 后新增群聊/历史页/工具条断言）；
- 产物在 `/tmp/dsh-tavern-native/check`：`report.json`（逐项 PASS/FAIL/SKIP）、
  `logs/*.log`（含隔离实例 `dsh.log`）、截图 `90-sidebar.png`…`99-old-routes.png`；
- 隔离实例用的是临时 `DSH_HOME`（`/tmp/dsh-tavern-native/instance`，端口默认 3099），
  不碰生产 3080；`--keep-home` 可保留现场排查。

应用前端改动另跑（在 `app/client` 下）：`npm run typecheck`、`npm test`。

## 已知限制 / 待验证

- **真生成依赖 DSH 侧条件**：DSH 需已登录、`agentDefaultModel` 有默认模型
  （本机 `opencode-go / deepseek-v4.1-flash`），且应用 `AI_MODE=dsh`。
  网关不可用时应用如实报错（`PROVIDER_UNAVAILABLE` 等），不伪造回复。
- **采样参数的生效条件**：应用侧已把 temperature/topP/stop 透传到网关（聊天级 >
  预设 > 卡设置），但插件 host 半的网关代码要 **DSH Web 重启后**才加载新参数；
  且本机 `opencode-go` 适配器会忽略 topP/stop（只有 temperature 真正传给上游），
  不要依赖 stop 一定截断。
- **旧 story 域已彻底删除**：P3 删除了故事模式/探索/世界/分支/记忆/伙伴的 UI 与服务端
  代码（旧 API `/worlds`、`/world-chats`、`/books`、`/play-controls` 全部 404）；
  `books`、`world.lines`、`world_chat_sessions` 等 51 张旧表已按备份 **DROP**（备份
  `/root/workspace/.backups/dsh-tavern/story_between-pre-p3-20261105-081109.dump`）。
  `dsh-tavern-world-worker` 已停用并 disable（其模块 `app.world.worker` 随 P3 删除）；`dsh-tavern-worker` 也已停用并 disable（新 `app/worker.py` 只剩 adapter_for，无后台循环）。
- **群聊限制**：成员卡被删（软删）后再打开该群聊会 404（无级联清理）；
  群聊 JSONL 可以导出但**不能导回成群聊**（导入接口只建单聊）；群聊轮转顺序
  固定按成员顺序，暂不支持指定/随机/权重。
- **旧路由**：应用不再有探索/作品页，也没有独立的群聊页（群聊从卡库多选发起）；
  `/tavern/explore`、`/tavern/works`、`/tavern/worlds/scripts`、
  `/tavern/characters/groups` 等都会落到角色卡首页（`/`），不会 404 死链。
- **面板工具条（原生酒馆）**：在聊天页 `/chats/<id>` 里「让 DSH 续写 / 角色扮演」
  可用——插件通过 P2 桥向应用要角色卡（名字/简介/性格/场景/开场白）与最近对话，
  组好 prompt 后新开 DSH 会话并预填；角色卡库、设置页等没有聊天上下文的页面
  按钮禁用并提示「先打开一个聊天」。`@角色名` 可覆盖角色扮演对象。
- **世界书注入预览已可用**：卡详情「注入预览」输入一句示例消息，返回命中条目、
  命中原因（常驻 / 关键词「X」）与拼好的注入文本
  （调 `POST /characters/{id}/world-info/preview`）。
- **正则替换只作用于落库文本**：设置页 `/settings/regex` 的启用规则会在每条
  回复落库前按 order 应用（`tavern/stream.py` 调 `cards/space.apply_regex`），
  刷新后看到替换结果；流式过程中的逐字文本仍是模型原文，不回改。开场白来自
  卡片文本，不经过正则。
- **聊天流式依赖每请求线程**：一次生成占一个 API 请求线程 + 一个后台线程
  （`threading.Thread`），服务端没有任务队列削峰；并发聊天数上去时先看 uvicorn
  worker 数。
- **流式帧格式**：服务端 SSE 帧为 `event: {"message"|"delta"|"done"|"error"}` +
  `data: {...}`，与客户端 `sse.ts` 的命名事件解析一致；逐字流式已验证，且整段
  回复会在 `done` 后落库并刷新。
- **位置记忆只记聊天页**：切走面板再回来会恢复到上次的 `/chats/<id>`；卡库、
  卡详情、设置页不记（回到 `/`）。应用内未写进路由的临时状态（滚动位置、
  未发送草稿）不恢复。恢复地址失效时点「刷新」回角色卡首页。
- **P2 上下文只覆盖聊天页**：`ping/pong/state` 负责握手与路由上报；
  `context-request` 只在 `/chats/<id>` 返回角色卡 + 最近对话，其它页面回
  `ok:false`（「当前页面没有可用的聊天上下文」）。字段表见 `docs/contract.md` §3。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/contract.md](docs/contract.md) | 与应用侧的集成契约：分流、可信头、P2、UI 座位、网关字段、应用 API / SSE / swipe |
| [docs/architecture.md](docs/architecture.md) | 实现架构：请求链路、模型网关、原生酒馆数据模型与 prompt 组装、面板结构、配置、风险 |
| [docs/step6-native-tavern.md](docs/step6-native-tavern.md) | 酒馆 2.0 当前设计：原生聊天、SSE、swipe、世界书、验收分组 |
| [docs/history/step1-character-card-chat.md](docs/history/step1-character-card-chat.md) 等 step1–step5 | **历史设计**（故事模式 / 旧卡片空间 / 旧群聊 / 长期记忆 / P0–P2），UI 与服务端多已删除 |
| [docs/testing.md](docs/testing.md) | 验收方法：静态检查 + 隔离实例真实浏览器 E2E |
