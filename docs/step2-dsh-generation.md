# 第二步：真生成（DSH 模型网关）

> 目标（用户 2026-11-04 指令）：角色卡聊天不再走 `AI_MODE=mock` 的确定性假回复，
> 而是由 DSH 当前配置的模型真实生成；聊天记录仍然存在应用侧。

## 1. 为什么是「DSH 模型网关」而不是给应用配 API Key

- 应用自己的 `ChatCompletionsAdapter` 有 SSRF 防护（`app/server/app/providers.py`
  `validate_url`：必须 https + 公网 IP + `PROVIDER_ALLOWED_HOSTS`），不能直连
  `127.0.0.1` 上的 DSH。
- DSH 的模型登录态/额度在 DSH 进程里（`dsh-llm-pi-ai` + 凭据；默认模型
  `agent-default-model` 配置为 `opencode-go / deepseek-v4.1-flash`）。
- 复用同一套模型选择，效果与 DSH Agent 一致，且不需要把 API Key 复制进应用库。

链路：

```
应用 worker ──(HTTP, x-tavern-agent-secret)──> DSH 插件 /tavern-agent/v1/*
                                                └─ ctx.llm.stream({provider,model,system,messages})
```

## 2. 插件侧契约（plugin-core）

新增宿主路由（与 `/tavern` 无关，不经 `requestRejection`，不动 iframe 会话）：

- `POST /tavern-agent/v1/complete`
  - 请求：`{"system": "...", "messages": [{"role":"user","content":"..."}],
    "maxTokens": 1024, "temperature": 0.8, "model": {"provider":"...","model":"..."}?,
    "sessionId": "会话标识（可选，≤200 字符）", "reasoningEffort": "low"}`
    - `sessionId`：opencode-go 等上游要求 `x-opencode-session`，缺失会 400
      `MissingSessionID`；网关缺省随机生成，应用侧建议传聊天会话 id 以稳定路由。
    - `reasoningEffort`：该模型只支持 `low`/`high`（不支持 off/minimal/medium）；
      网关默认 `low`，否则会继承 profile 的 `high`，把 maxTokens 烧在思考上。
  - 响应：`{"text":"...", "model":{"provider","model"},
    "usage":{"state":"known"|"estimated","inputTokens":N,"outputTokens":N},
    "finishReason":"stop"|"length"|...}`
- `POST /tavern-agent/v1/stream`（SSE）
  - `data: {"type":"delta","text":"..."}` … `data: {"type":"usage",...}`
    `data: {"type":"finish","reason":"stop"}` `data: [DONE]`
  - 错误统一 `data: {"type":"error","error":{"code","message"}}` 后结束。
- `GET /tavern-agent/v1/health` → `{"ok":true,"model":{"provider","model"}}`
- 鉴权：`Authorization: Bearer <secret>`，secret = `DSH_TAVERN_AGENT_SECRET`
  （回退 `DSH_TRUSTED_SECRET`，读 `app/.env` 或环境变量）。缺失/错误 → 401
  `{"error":{"code":"AGENT_UNAUTHORIZED"}}`。
- 模型选择：默认 `ctx.agentDefaultModel.currentSelection()`；请求里的 `model`
  只允许显式覆盖。`reasoningEffort` 默认 `low`（profile 配置是 `high`，省略会
  继承 high；deepseek-v4.1-flash 不支持 off/minimal/medium）。
- 文本只取 `text-delta` 块；`reasoning-delta` 丢弃；`usage`/`finish` 用于响应。
- 超时 120s，上游异常 → 502 `{"error":{"code":"AGENT_UPSTREAM_FAILED","message":...}}`
  （不把 provider 原始响应体、请求内容或 secret 写进日志/错误）。
- 服务依赖：`inject = ['webServer','llm','agentDefaultModel']`。

## 3. 应用侧契约（app-auth）

- `config.py`：新增
  - `dsh_agent_url`（`DSH_AGENT_URL`，默认 `http://127.0.0.1:3080/tavern-agent/v1`）
  - `dsh_agent_secret`（`DSH_AGENT_SECRET`，默认空）
  - `dsh_agent_timeout_seconds`（默认 120）
- `providers.py`：新增 `DshAgentAdapter`，接口与 `ChatCompletionsAdapter` 一致。
  - `generate()`：POST `/complete`，`messages=[{"role":"user","content":wire_json(data)}]`，
    `maxTokens=output_limit`；返回 `{'text','finishReason','usage','latencyMs'}`。
  - `generate_stream()`：POST `/stream`，逐 `delta` 调 `on_delta`，返回完整结果。
  - 401 → `Problem('AGENT_UNAUTHORIZED', ..., 502)`；连接失败/超时/5xx →
    `Problem('PROVIDER_UNAVAILABLE', 'DSH 模型网关不可用，未收到回复', 502)`。
- `worker.py`：`adapter = MockAdapter() if ai_mode=='mock' else
  DshAgentAdapter() if ai_mode=='dsh' else ChatCompletionsAdapter()`。
- CLI（`app/server/app/cli.py`）：`configure-dsh-model --username frankslin`
  幂等创建/更新模型档案：
  `name="DSH 模型"`, `base_url="dsh://agent"`, `model_id="dsh-default"`,
  `key_cipher=encrypt_key(<随机>)"`, `context_window=128000`,
  `output_parameter="max_tokens"`, `supports_json=True`，并设为该用户
  `UserSettings.default_profile_id`。打印 profile id。
- 模型档案只用于预算/展示，`DshAgentAdapter` 不读 `base_url`/`key_cipher`。
- 文档记录：validation 阶段（`world_character_chat_review`）同样走网关，
  模型必须输出 JSON；解析失败按现有 `parse_json` 规则报错（不静默放过）。

## 4. 环境变量与上线顺序

1. 生成 secret（`openssl rand -hex 32`），写 `app/.env`：
   `DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1`
   `DSH_AGENT_SECRET=<secret>`
   `DSH_TAVERN_AGENT_SECRET=<同一 secret>`（插件从 app/.env 读）
   `AI_MODE=dsh`
2. 先重启 DSH（detached，脚本内完成），确认
   `curl -H "Authorization: Bearer <secret>" http://127.0.0.1:3080/tavern-agent/v1/health`。
3. 再重启 `dsh-tavern-api` + `dsh-tavern-worker`，跑
   `app/venv/bin/python -m app.cli configure-dsh-model --username frankslin`。
4. 生产冒烟：卡片导入 → 开聊 → 发消息 → 回复不是 mock 文案、刷新后仍在；
   清理测试卡/会话。

回滚：`AI_MODE=mock` + 重启 api/worker 即恢复原状；插件路由留着也无副作用。

## 5. 验收标准

- 隔离实例（3099）`--cards --real`：真实回复（不含 mock 特征句
  「我们可以聊聊此刻的想法」），长度 ≥ 20 字，刷新后仍在，无 console 报错。
- 生产 3080 同链路一次通过；测试数据清理干净。
- 网关未配置/失败时，UI 显示失败状态而不是假回复（诚实失败）。

## 6. 文件归属

| 范围 | owner |
| --- | --- |
| `lib/**`、`README.md`、`docs/architecture.md` | plugin-core |
| `app/server/**`（含 config/providers/worker/cli/测试） | app-auth |
| `scripts/**`、`verify.mjs`、`docs/testing.md` | verifier |
| 本文档、`docs/contract.md`、`app/.env`、host 重启/上线 | Lead |
