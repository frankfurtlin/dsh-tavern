# 运维手册（dsh-story-between）

本文件面向在本机（`/root/workspace/plugins/dsh-story-between`）维护这个插件的人。
应用本身的业务文档在 `app/` 里；这里只讲「怎么跑、怎么改、怎么回滚」。

## 1. 组件与端口

| 组件 | 单元 / 进程 | 监听 | 说明 |
| --- | --- | --- | --- |
| DSH Web（宿主） | `deepseek-harness.service` | `127.0.0.1:3080` | 加载 `lib/index.js` + `client.js`，提供 `/story` 前缀与登录闸门 |
| DSH 登录网关 | `dsh-passwords` 的 `serve-gateway` | `127.0.0.1:3081` | 公网 `dsh.frankfurtlin.top` → 3080；插件的公网入口只在这里 |
| 应用 API | `dsh-story-between-api.service` | `127.0.0.1:8001` | FastAPI；插件代理 `/story/api/**`、`/story/health/**` |
| 应用静态站 | `dsh-story-between-web.service` | `127.0.0.1:8810` | `app/client/dist` 的 SPA；插件代理其余 `/story/**` |
| 文本 worker | `dsh-story-between-worker.service` | 无端口 | 生成任务队列 |
| 世界观 worker | `dsh-story-between-world-worker.service` | 无端口 | 世界观任务队列 |

四个应用单元安装在 `/etc/systemd/system/`，模板在本仓库 `deploy/`；
它们的 `WorkingDirectory`/`ExecStart`/`EnvironmentFile` 全部指向本插件的 `app/`。

公网 `https://hermes.frankfurtlin.top/story/` 已在 2026-11-03 退休（Caddy 配置见
`deploy/Caddyfile.story-retired`）；唯一入口是 `https://dsh.frankfurtlin.top/story/`，
受 DSH 登录保护。

## 2. 改了代码之后

- **只改 `lib/index.js` / `client.js`**：重启 DSH Web（见 §3），浏览器强制刷新。
- **改 `app/server/**`**：`systemctl restart dsh-story-between-api`（改到 worker 逻辑则连
  worker 一起重启）。
- **改 `app/client/src/**`**：在 `app/client` 下用项目自带命令重建 dist，然后不用重启
  静态站（直接读 dist）。构建必须带 `/story` 基址：

  ```bash
  cd app/client && APP_BASE=/story npm run build
  ```

  构建产物 `app/client/dist` 不进 git。
- **改 `app/.env`**（例如轮换 `DSH_TRUSTED_SECRET`）：API 与 DSH 都要重启——
  secret 是插件启动时读进内存的，只重启一侧会出现「API 期望新值 / 插件还发旧值」。

## 3. 重启 DSH（重要：会杀掉当前 agent 回合）

DSH 的 agent 与 Web 服务同进程，直接 `systemctl restart deepseek-harness.service`
会把正在执行的对话一起杀掉。**在 DSH 会话里必须用 systemd-run 分离执行**：

```bash
systemd-run --on-active=10s --unit=dsh-story-verify \
  /root/workspace/plugins/dsh-story-between/restart-and-verify.sh
```

生产收尾（重启 + 浏览器验收 + 退休旧公网路由）用：

```bash
systemd-run --on-active=30s --unit=dsh-story-finalize \
  /root/workspace/plugins/dsh-story-between/finalize-prod.sh
```

两个脚本都把日志和报告写到 `verification/prod/`（`finalize-*.log`、`restart-*.log`、
`report.json`、截图）。`--no-restart` 可在不重启的情况下只复跑验收。

## 4. 回滚

1. **插件导致 DSH 起不来**（服务 120s 内不监听、journal 有 bundle/patch 报错）：
   恢复 profile 后重启：

   ```bash
   cp /root/.dsh/profiles/web/package.json.bak-pre-story \
      /root/.dsh/profiles/web/package.json
   systemctl restart deepseek-harness.service
   ```

   `finalize-prod.sh` 在「重启后 120s 内服务未监听」时会自动做这一步。
2. **想临时下线插件但保留应用**：从 profile `package.json` 的
   `dsh.profile.bundles` 和 `dependencies` 里去掉 `dsh-story-between`，重启 DSH。
3. **恢复公开 `/story` 路由**（一般不需要）：`cp /etc/caddy/Caddyfile.bak-pre-story-retire
   /etc/caddy/Caddyfile && systemctl reload caddy`。
4. **应用自身回滚**：`app/` 已在 git 里初始化（基线提交 `00991c6`），按文件回滚后重启
   对应服务。`.env` 备份：`app/.env.bak-pre-dsh`。

## 5. 可信登录（trusted auth）怎么工作

- 插件给所有转发到 API 的请求加 `x-dsh-trusted: <secret>`，并**删除**浏览器自带的同名头。
- 应用侧（`app/server/app/security.py`）用常量时间比较校验；通过后按
  `DSH_TRUSTED_USERNAME` 解析 active 用户，复用/创建稳定的 web DeviceSession，
  设 `request.state.session`。
- `POST /api/reader/v1/auth/refresh` 在可信模式下直接返回 token（前端 boot 无感），
  `logout` 在可信模式下 204 no-op（会话由宿主登录态管理）。
- 浏览器端**没有**应用自己的登录页：`/story/login` 不再会被使用；直接进 `/story/explore`。
- secret 缺失或不匹配时行为与普通部署完全一致（401 未登录），不会 500。

安全边界：可信头只在 DSH 进程内部、对本机回环上游有效；`/story` 也不能从 hermes
域名直连到 8001（Caddy 路由已删除），所以外部无法伪造这个头。

## 6. 排障

```bash
# 宿主是否加载了插件 / 闸门是否生效：未登录应 401，404 表示路由没挂上
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3080/story/

# 应用健康
curl -s http://127.0.0.1:8001/health/ready
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8810/

# 插件日志（host 半）
journalctl -u deepseek-harness.service --since '-10 min' | grep dsh-story-between

# 浏览器验收（隔离实例，不碰生产）
cd /root/workspace/plugins/dsh-story-between && bash scripts/check.sh --open-story
```

常见现象：

| 现象 | 多半是 |
| --- | --- |
| 侧栏没有「故事之间」 | profile 里没注册 / DSH 没重启 / client bundle 没被加载 |
| 点开面板 iframe 里是应用登录页 | API 还是旧进程（没加载 trusted auth），或 secret 不匹配 |
| 面板提示「应用没有响应（8 秒超时）」 | iframe 已加载但 P2 桥没装（dist 旧），或应用路由状态异常 |
| `/story/assets/*` 200 但页面白屏 | dist 构建没带 `APP_BASE=/story`，资源 404 被 SPA fallback 成 HTML |

## 7. 验收产物

- `verification/check/`：隔离实例（`/tmp/dsh-story-check`，profile `check`，端口 3099）
  的 E2E 报告与截图，可随时重跑，不影响生产。
- `verification/prod/`：生产重启后的真实验收报告与截图。
