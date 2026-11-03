#!/usr/bin/env bash
# dsh-story-between 生产收尾（必须脱离 DSH 会话执行：重启 deepseek-harness 会杀掉当前 agent 回合）。
#
# 用法：
#   systemd-run --on-active=30s --unit=dsh-story-finalize \
#     /root/workspace/plugins/dsh-story-between/finalize-prod.sh
#
# 流程：
#   1. 重启 deepseek-harness.service，等 3080 就绪；
#   2. 本地匿名 /story/ 必须 401（插件已加载 + 闸门生效），否则回滚 profile 并退出；
#   3. 用 journalctl 里的 token URL 跑 verify.mjs（浏览器真实验收）→ verification/prod/；
#   4. 插件本地可达后，换上已预演过的 Caddyfile（删除 hermes 域名的公开 /story 路由）
#      并 reload caddy；再验公网：/story/ 需 302/401，旧 /story/api/health 需 404；
#   5. 全程写 verification/prod/finalize-<ts>.log。
#
# 回滚：profile 恢复到 /root/.dsh/profiles/web/package.json.bak-pre-story；
#       Caddy 恢复到 /etc/caddy/Caddyfile.bak-pre-story-retire（脚本首次换配置前创建）。
set -uo pipefail
export HOME="${HOME:-/root}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/root/.cache}"

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$PLUGIN_DIR/verification/prod"
LOG="$OUT_DIR/finalize-$(date +%Y%m%d-%H%M%S).log"
NODE="${NODE:-$(command -v node || echo /root/.nvm/versions/node/v24.21.0/bin/node)}"
SERVICE="${DSH_SERVICE:-deepseek-harness.service}"
PORT="${DSH_PORT:-3080}"
PUBLIC_HOST="${DSH_PUBLIC_HOST:-dsh.frankfurtlin.top}"
PROFILE="/root/.dsh/profiles/web/package.json"
PROFILE_BACKUP="$PROFILE.bak-pre-story"
CADDY=/etc/caddy/Caddyfile
CADDY_BACKUP=/etc/caddy/Caddyfile.bak-pre-story-retire
STAGED_CADDY="$PLUGIN_DIR/deploy/Caddyfile.story-retired"

mkdir -p "$OUT_DIR"
exec > >(tee -a "$LOG") 2>&1
ts(){ date '+%F %T'; }
say(){ echo "[$(ts)] $*"; }

say "开始 dsh-story-between 生产收尾"

# ---------- 1. 重启 DSH ----------
if ! systemctl restart "$SERVICE"; then
  say "✗ systemctl restart 失败，未做任何其它改动"
  exit 1
fi
UP=0
for _ in $(seq 1 60); do
  sleep 2
  CODE="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)"
  if [[ -n "$CODE" && "$CODE" != "000" ]]; then UP=1; break; fi
done
if [[ "$UP" -ne 1 ]]; then
  say "✗ DSH 重启后 120s 内没有起来 → 回滚 profile 后重启"
  [[ -f "$PROFILE_BACKUP" ]] && cp -f "$PROFILE_BACKUP" "$PROFILE"
  systemctl restart "$SERVICE"
  sleep 10
  systemctl is-active "$SERVICE"
  exit 1
fi
say "DSH 已就绪（$PORT）"

# ---------- 2. 本地 /story 闸门 ----------
LOCAL_EDGE="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/story/" 2>/dev/null || true)"
say "本地匿名 /story/ → ${LOCAL_EDGE:-无响应}（期望 401）"
STATUS=0
if [[ "$LOCAL_EDGE" != "401" ]]; then
  say "✗ 本地 /story/ 不是 401：插件可能未加载（404）或闸门失效（200）"
  say "  不自动回滚（profile 生效但插件有问题时回滚也救不了），请查看 journalctl -u $SERVICE 后手工决定"
  STATUS=1
fi

# ---------- 3. 浏览器验收 ----------
URL="$(journalctl -u "$SERVICE" --since '-3 min' 2>/dev/null \
  | grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" | tail -1)"
if [[ -z "$URL" ]]; then
  URL="$(journalctl -u "$SERVICE" 2>/dev/null \
    | grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" | tail -1)"
fi
if [[ -z "$URL" ]]; then
  say "✗ 没拿到 token URL，跳过浏览器验收"
  STATUS=1
else
  say "实例已起：${URL%%\?*}（token 已脱敏）"
  ( cd "$PLUGIN_DIR" && "$NODE" verify.mjs --url "$URL" --out "$OUT_DIR" ) || STATUS=1
fi

# ---------- 4. 退休公网 /story（仅当插件本地可达） ----------
if [[ "$LOCAL_EDGE" == "401" && -f "$STAGED_CADDY" ]]; then
  if ! caddy adapt --config "$STAGED_CADDY" --adapter caddyfile >/dev/null 2>&1; then
    say "✗ 预置 Caddyfile 无法解析，跳过公网路由退休"
    STATUS=1
  else
    if [[ ! -f "$CADDY_BACKUP" ]]; then cp -f "$CADDY" "$CADDY_BACKUP"; fi
    cp -f "$STAGED_CADDY" "$CADDY"
    if caddy validate --config "$CADDY" --adapter caddyfile >/dev/null 2>&1; then
      systemctl reload caddy
      sleep 2
      say "已退休 hermes 域名的公开 /story 路由（备份 $CADDY_BACKUP）"
    else
      say "✗ 新 Caddyfile 校验失败，恢复原配置"
      cp -f "$CADDY_BACKUP" "$CADDY"
      STATUS=1
    fi
  fi
  # 公网核验（Caddy 已 reload 或未改，都测一次）
  PUB_EDGE="$(curl -sk -o /dev/null -m 8 -w '%{http_code}' --resolve "$PUBLIC_HOST:443:127.0.0.1" \
    "https://$PUBLIC_HOST/story/" 2>/dev/null || true)"
  PUB_OLD_API="$(curl -sk -o /dev/null -m 8 -w '%{http_code}' --resolve "$PUBLIC_HOST:443:127.0.0.1" \
    "https://$PUBLIC_HOST/story/api/health" 2>/dev/null || true)"
  say "公网 $PUBLIC_HOST/story/ → ${PUB_EDGE:-无响应}（期望 302/401，即由 DSH 登录网关接手）"
  say "公网旧接口 $PUBLIC_HOST/story/api/health → ${PUB_OLD_API:-无响应}（期望 404，已从 hermes 站点摘除）"
  [[ "$PUB_EDGE" == "404" ]] && { say "✗ 公网 /story/ 仍 404：Caddy/网关链路没接通"; STATUS=1; }
  [[ "$PUB_OLD_API" == "200" ]] && { say "✗ 旧公网 /story/api/ 还能直连 8001：Caddy 路由未摘干净"; STATUS=1; }
fi

say "收尾结束，exit=$STATUS；报告目录 $OUT_DIR"
exit "$STATUS"
