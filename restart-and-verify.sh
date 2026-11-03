#!/usr/bin/env bash
# 生产重启 + 自动验收（必须脱离 DSH 会话执行：在会话里 systemctl restart 会把自己杀掉）。
#
# 用法：
#   systemd-run --on-active=10s --unit=dsh-story-verify \
#     /root/workspace/plugins/dsh-story-between/restart-and-verify.sh
#
# 已有生产实例、只想复跑验收（不重启）：
#   scripts/restart-and-verify.sh --no-restart
#
# 想把「打开一部作品 → 续写/角色扮演提示词填充」也纳入生产冒烟（会写阅读进度）：
#   DSH_OPEN_STORY=1 ...（默认不开，生产默认只验 A–E + F 的优雅降级）
#
# 前置：生产 DSH 是 systemd 服务 deepseek-harness.service（127.0.0.1:3080），
#       app 的 api/web 两个服务已在 8001/8810 运行，且 lib/index.js 已把 /story
#       挂到生产 profile。
# 产物：verification/prod/report.json + 截图；verification/prod/restart-<时间>.log。
set -uo pipefail
export HOME="${HOME:-/root}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/root/.cache}"

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$PLUGIN_DIR/verification/prod"
LOG="$OUT_DIR/restart-$(date +%Y%m%d-%H%M%S).log"
NODE="${NODE:-$(command -v node || echo /root/.nvm/versions/node/v24.21.0/bin/node)}"
SERVICE="${DSH_SERVICE:-deepseek-harness.service}"
PORT="${DSH_PORT:-3080}"
PUBLIC_HOST="${DSH_PUBLIC_HOST:-dsh.frankfurtlin.top}"
NO_RESTART=0
[[ "${1:-}" == "--no-restart" ]] && NO_RESTART=1

mkdir -p "$OUT_DIR"
exec > >(tee -a "$LOG") 2>&1
echo "[$(date '+%F %T')] 开始：dsh-story-between 生产验收（$( [[ "$NO_RESTART" -eq 1 ]] && echo 不重启 || echo 重启服务 )）"

if [[ "$NO_RESTART" -eq 0 ]]; then
  systemctl restart "$SERVICE" || { echo "重启失败（需要 root / 服务名不是 $SERVICE）"; exit 1; }
fi

# 等端口就绪：DSH 的 / 未登录返回 401，所以不能用 curl -f 判断。
for _ in $(seq 1 60); do
  sleep 2
  CODE="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)"
  if [[ -n "$CODE" && "$CODE" != "000" ]]; then break; fi
done
sleep 2

URL="$(journalctl -u "$SERVICE" --since '-2 min' 2>/dev/null \
  | grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" | tail -1)"
if [[ -z "$URL" ]]; then
  URL="$(journalctl -u "$SERVICE" 2>/dev/null \
    | grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" | tail -1)"
fi
if [[ -z "$URL" ]]; then
  echo "[$(date '+%F %T')] 没拿到带 token 的地址（journalctl 权限？服务没打印？），无法做浏览器验收"
  exit 2
fi
echo "[$(date '+%F %T')] 实例已起：${URL%%\?*}（token 已脱敏）"

# 生产入口健康检查：
#   - 本地匿名 /story/ 必须 401（不能 404 = 路由没挂上，不能 200 = 闸门失效）
#   - 公网 https://<host>/story/ 经 Caddy 后应 302/401（404 = 网关或插件没接通）
LOCAL_EDGE="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/story/" 2>/dev/null || true)"
echo "[$(date '+%F %T')] 本地 /story/ 未登录状态码：${LOCAL_EDGE:-无响应}（期望 401）"
EDGE="$(curl -sk -o /dev/null -m 8 -w '%{http_code}' --resolve "$PUBLIC_HOST:443:127.0.0.1" \
  "https://$PUBLIC_HOST/story/" 2>/dev/null || true)"
echo "[$(date '+%F %T')] 公网 $PUBLIC_HOST/story/ 未登录状态码：${EDGE:-无响应}（期望 302/401，404 表示未接通）"

STATUS=0
if [[ "$LOCAL_EDGE" != "401" ]]; then
  echo "[$(date '+%F %T')] ✗ 本地 /story/ 不是 401：宿主半可能未加载或登录闸门异常"
  STATUS=1
fi
if [[ "$EDGE" == "404" ]]; then
  echo "[$(date '+%F %T')] ✗ 公网入口 404：检查 Caddy → dsh-passwords → DSH 链路与插件注册"
  STATUS=1
fi

cd "$PLUGIN_DIR" || exit 1
VERIFY_ARGS=()
[[ "${DSH_OPEN_STORY:-0}" == "1" ]] && VERIFY_ARGS+=(--open-story)
"$NODE" verify.mjs --url "$URL" --out "$OUT_DIR" ${VERIFY_ARGS[@]+"${VERIFY_ARGS[@]}"}
VERIFY_STATUS=$?
[[ "$VERIFY_STATUS" -ne 0 ]] && STATUS=1

echo "[$(date '+%F %T')] 验收结束，exit=$STATUS，报告：$OUT_DIR/report.json"
exit "$STATUS"
