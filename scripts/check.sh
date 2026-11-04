#!/usr/bin/env bash
# dsh-tavern 验收入口：静态检查 + 隔离 DSH 实例 + 真实浏览器 E2E（酒馆 2.0 原生流程）。
#
# 用法：
#   scripts/check.sh                 # 静态 + 隔离实例 + verify.mjs（默认）
#   scripts/check.sh --static        # 只跑静态检查（快，不启实例）
#   scripts/check.sh --instance      # 只跑隔离实例 + E2E
#   scripts/check.sh --with-app-tests # 额外跑应用前端 typecheck + 单测（慢）
#   scripts/check.sh --keep-home     # 保留 /tmp/dsh-tavern-native/instance（排查用）
#   scripts/check.sh --port 3199     # 换端口
#
# 产物：
#   /tmp/dsh-tavern-native/check/report.json      —— verify.mjs 的逐项结果（PASS/FAIL/SKIP）
#   /tmp/dsh-tavern-native/check/logs/*.log       —— 每个静态命令的输出
#   /tmp/dsh-tavern-native/check/logs/dsh.log     —— 隔离实例的 DSH 日志（启动失败时先看这个）
#   截图 90-sidebar.png、91-panel.png、92-characters-home.png、93-card-imported.png、
#        94-chat-stream.png、95-chat-done.png、96-regenerate.png、97-history.png、
#        98-world-info.png、99-old-routes.png
#   角色卡 fixture：/tmp/dsh-tavern-native/cards/{v2-png.png,v2-json.json,manifest.json}
#
# 设计：
#   - 在 /tmp/dsh-tavern-native/instance 起一个完全隔离的 DSH_HOME，profile=check，端口默认 3099，
#     绝不动生产实例（3080）。node_modules / package.json 复用生产 profile（符号链接），
#     但 credentials / storages / 会话全部在临时目录里，退出时清掉。
#   - 生产 profile 已安装 dsh-tavern 时直接镜像它的 package.json + node_modules；
#     未安装时生成 dev profile 把插件源目录 link 进 node_modules。
#   - 插件未就绪（/tavern 404、client.js 缺失、侧栏无入口）或原生 UI 未交付时脚本不崩，
#     verify.mjs 会输出「未就绪」报告，依赖项记 SKIP 并写明 owner。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_HOME="${DSH_HOME:-$HOME/.dsh}"
REAL_PROFILE="$REAL_HOME/profiles/web"
PROFILE="check"
PORT="${DSH_TAVERN_CHECK_PORT:-3099}"
CHECK_HOME="${DSH_TAVERN_CHECK_HOME:-/tmp/dsh-tavern-native/instance}"
OUT="${DSH_TAVERN_VERIFY_OUT:-/tmp/dsh-tavern-native/check}"
FIXTURES="/tmp/dsh-tavern-native/cards"
LOGS="$OUT/logs"
DSH_BIN="$(command -v dsh || true)"

RUN_STATIC=1
RUN_INSTANCE=1
WITH_APP_TESTS=0
KEEP_HOME=0
PASS=0
FAIL=0
SKIP=0
FAILED=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --static) RUN_INSTANCE=0 ;;
    --instance) RUN_STATIC=0 ;;
    --with-app-tests) WITH_APP_TESTS=1 ;;
    --keep-home) KEEP_HOME=1 ;;
    --port) PORT="${2:?--port 需要端口号}"; shift ;;
    --help|-h) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1（--help 查看用法）" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$LOGS"
say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$*"; }
bad() { FAIL=$((FAIL + 1)); FAILED+=("$*"); printf 'FAIL  %s\n' "$*"; }
skip() { SKIP=$((SKIP + 1)); printf 'SKIP  %s\n' "$*"; }

run() {
  local name="$1"
  shift
  local log
  mkdir -p "$LOGS"
  log="$LOGS/$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-80).log"
  if "$@" >"$log" 2>&1; then
    ok "$name"
  else
    bad "$name（日志：${log#"$ROOT"/}）"
    tail -n 20 "$log" | sed 's/^/      /'
  fi
}

# ── 静态检查 ────────────────────────────────────────────────────────────────
static_checks() {
  say "静态检查"
  if [[ -f "$ROOT/verify.mjs" ]]; then
    run "node --check verify.mjs" node --check "$ROOT/verify.mjs"
  else
    bad "verify.mjs 缺失"
  fi

  local scripts
  for scripts in "$ROOT"/scripts/*.sh; do
    [[ -f "$scripts" ]] || continue
    run "bash -n ${scripts#"$ROOT"/}" bash -n "$scripts"
  done

  if [[ -f "$ROOT/lib/index.js" ]]; then
    run "node --check lib/index.js" node --check "$ROOT/lib/index.js"
  else
    bad "lib/index.js 缺失（owner: plugin-core）"
  fi
  if [[ -f "$ROOT/client.js" ]]; then
    run "node --check client.js" node --check "$ROOT/client.js"
  else
    skip "node --check client.js（client.js 尚未产出，owner: plugin-core）"
  fi
  if [[ -f "$ROOT/package.json" ]]; then
    if node -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))' "$ROOT/package.json" >/dev/null 2>&1; then
      ok "package.json 是合法 JSON"
    else
      bad "package.json 不是合法 JSON（owner: plugin-core）"
    fi
    if [[ -f "$ROOT/cordis.patch.yml" ]]; then
      ok "存在 cordis.patch.yml"
    else
      bad "cordis.patch.yml 缺失（owner: plugin-core）"
    fi
  else
    bad "package.json 缺失（owner: plugin-core）"
  fi

  if [[ -f "$ROOT/scripts/fixtures/make-cards.py" ]]; then
    run "python 语法 check scripts/fixtures/make-cards.py" env PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$ROOT/scripts/fixtures/make-cards.py"
  else
    skip "python 语法 check（scripts/fixtures/make-cards.py 不存在）"
  fi

  if [[ "$WITH_APP_TESTS" -eq 1 ]]; then
    if [[ -f "$ROOT/app/client/package.json" ]]; then
      run "应用前端 typecheck" npm --prefix "$ROOT/app/client" run typecheck
      run "应用前端单测" npm --prefix "$ROOT/app/client" run test:unit
    else
      skip "应用前端检查（app/client/package.json 不存在）"
    fi
  else
    skip "应用前端 typecheck/单测（加 --with-app-tests 启用）"
  fi
}

# ── 隔离实例 ────────────────────────────────────────────────────────────────
TAVERN_INSTALLED=0
if [[ -f "$REAL_PROFILE/package.json" ]] && grep -q '"dsh-tavern"' "$REAL_PROFILE/package.json" 2>/dev/null; then
  TAVERN_INSTALLED=1
fi

cleanup() {
  trap - EXIT INT TERM
  if [[ -f "$CHECK_HOME/dsh.pid" ]]; then
    local pid
    pid="$(cat "$CHECK_HOME/dsh.pid" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]]; then
      kill -TERM "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  pkill -f "dsh --profile $PROFILE --no-open --host 127.0.0.1 --port $PORT" 2>/dev/null || true
  if [[ -f "$CHECK_HOME/dsh.log" ]]; then
    cp "$CHECK_HOME/dsh.log" "$LOGS/dsh.log" 2>/dev/null || true
  fi
  if [[ "$KEEP_HOME" -eq 0 ]]; then
    rm -rf "$CHECK_HOME"
  else
    echo "保留隔离实例目录：$CHECK_HOME"
  fi
}

port_free() {
  local code
  code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)"
  [[ -z "$code" || "$code" == "000" ]]
}

wait_http() {
  local deadline=$((SECONDS + 60))
  while [[ $SECONDS -lt $deadline ]]; do
    local code
    code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)"
    if [[ -n "$code" && "$code" != "000" ]]; then
      echo "$code"
      return 0
    fi
    sleep 1
  done
  return 1
}

setup_profile() {
  rm -rf "$CHECK_HOME"
  mkdir -p "$CHECK_HOME/profiles/$PROFILE" "$CHECK_HOME/storages"
  local target="$CHECK_HOME/profiles/$PROFILE"

  if [[ "$TAVERN_INSTALLED" -eq 1 ]]; then
    for item in package.json node_modules; do
      ln -s "$REAL_PROFILE/$item" "$target/$item"
    done
    for item in cordis.yml cordis.patch.yml; do
      [[ -f "$REAL_PROFILE/$item" ]] && cp "$REAL_PROFILE/$item" "$target/$item"
    done
    echo "profile 模式：mirror（生产 profile 已安装 dsh-tavern）"
  else
    local missing=0
    mkdir -p "$target/node_modules"
    if [[ -d "$REAL_PROFILE/node_modules" ]]; then
      for entry in "$REAL_PROFILE"/node_modules/* "$REAL_PROFILE"/node_modules/.bin "$REAL_PROFILE"/node_modules/.pnpm; do
        [[ -e "$entry" ]] || continue
        ln -s "$entry" "$target/node_modules/$(basename "$entry")"
      done
    else
      missing=1
    fi
    ln -sfn "$ROOT" "$target/node_modules/dsh-tavern"
    node - "$REAL_PROFILE/package.json" "$target/package.json" "$ROOT" <<'NODE'
const fs = require('node:fs')
const [realPath, outPath, root] = process.argv.slice(2)
let base = { name: 'dsh-profile-check', private: true, dependencies: {}, dsh: { profile: { bundles: [] } } }
try { base = JSON.parse(fs.readFileSync(realPath, 'utf8')) } catch {}
base.dependencies = base.dependencies || {}
base.dependencies['dsh-tavern'] = `link:${root}`
base.dsh = base.dsh || {}
base.dsh.profile = base.dsh.profile || {}
const bundles = Array.isArray(base.dsh.profile.bundles) ? base.dsh.profile.bundles : []
if (!bundles.includes('dsh-tavern')) bundles.push('dsh-tavern')
base.dsh.profile.bundles = bundles
fs.writeFileSync(outPath, JSON.stringify(base, null, 2) + '\n')
NODE
    [[ -f "$REAL_PROFILE/cordis.patch.yml" ]] && cp "$REAL_PROFILE/cordis.patch.yml" "$target/cordis.patch.yml"
    if [[ "$missing" -eq 1 ]]; then
      echo "profile 模式：dev（生产 profile 无 node_modules，依赖可能不全）"
    else
      echo "profile 模式：dev（已把插件源目录 link 进 profile node_modules）"
    fi
  fi

  if [[ -f "$REAL_HOME/.credentials.yaml" ]]; then
    cp "$REAL_HOME/.credentials.yaml" "$CHECK_HOME/.credentials.yaml"
    chmod 600 "$CHECK_HOME/.credentials.yaml"
  fi
  if [[ -f "$REAL_HOME/storages/workspace.json" ]]; then
    cp "$REAL_HOME/storages/workspace.json" "$CHECK_HOME/storages/workspace.json"
  fi
}

instance_checks() {
  say "隔离 DSH 实例（$CHECK_HOME，profile=$PROFILE，端口 $PORT）"
  if [[ -z "$DSH_BIN" ]]; then
    bad "找不到 dsh 可执行文件（PATH 里没有）"
    return
  fi
  if ! port_free; then
    bad "端口 $PORT 已被占用；先停掉占用进程，或用 --port 换端口"
    return
  fi

  trap cleanup EXIT INT TERM
  setup_profile

  local target="$CHECK_HOME/profiles/$PROFILE"
  if [[ ! -e "$target/node_modules" ]]; then
    bad "隔离 profile 的 node_modules 不可用（生产 profile 依赖缺失？）"
    return
  fi

  (
    cd "$CHECK_HOME" || exit 1
    DSH_HOME="$CHECK_HOME" setsid nohup "$DSH_BIN" --profile "$PROFILE" --no-open --host 127.0.0.1 --port "$PORT" \
      >"$CHECK_HOME/dsh.log" 2>&1 &
    echo $! >"$CHECK_HOME/dsh.pid"
  )

  local token_url="" deadline=$((SECONDS + 120))
  while [[ $SECONDS -lt $deadline ]]; do
    token_url="$(grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" "$CHECK_HOME/dsh.log" 2>/dev/null | head -1 || true)"
    [[ -n "$token_url" ]] && break
    if ! kill -0 "$(cat "$CHECK_HOME/dsh.pid" 2>/dev/null || echo 0)" 2>/dev/null; then
      break
    fi
    sleep 2
  done
  if [[ -z "$token_url" ]]; then
    bad "隔离实例没有启动（120s 内没等到 token URL）"
    echo "      —— dsh.log 尾部 ——"
    tail -n 40 "$CHECK_HOME/dsh.log" 2>/dev/null | sed 's/^/      /'
    return
  fi
  local token
  token="$(grep -o 'token=[A-Za-z0-9_-]*' <<<"$token_url" | head -1)"
  local url="http://127.0.0.1:$PORT/?${token}"
  echo "实例地址：:${PORT}（token 已脱敏）"

  local code
  code="$(wait_http || true)"
  if [[ -z "$code" ]]; then
    bad "隔离实例端口 $PORT 没有响应 HTTP"
    return
  fi
  ok "隔离实例 HTTP 可访问（/ → $code，401 = 需要登录，符合预期）"

  # 插件是否已挂载：匿名 /tavern/ 期望 401；404 = 路由没注册（插件未就绪）。
  local tavern_code
  tavern_code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/tavern/" 2>/dev/null || true)"
  case "$tavern_code" in
    401) ok "匿名 /tavern/ 被登录闸门挡住（HTTP 401）" ;;
    404) skip "匿名 /tavern/ 返回 404：宿主半还没挂载 /tavern（plugin-core 未完成，E2E 会输出未就绪报告）" ;;
    *) bad "匿名 /tavern/ 期望 401，实际 HTTP ${tavern_code:-无响应}" ;;
  esac

  say "角色卡 fixture（make-cards.py）"
  run "生成角色卡 fixture（v2 PNG / v2 JSON）" python3 "$ROOT/scripts/fixtures/make-cards.py" --out "$FIXTURES"

  say "真实浏览器 E2E（verify.mjs）"
  local verify_args=(--url "$url" --out "$OUT" --fixtures "$FIXTURES")
  [[ -n "${DSH_TAVERN_DEBUG:-}" ]] && echo "verify args: ${verify_args[*]}"
  if node "$ROOT/verify.mjs" "${verify_args[@]}"; then
    ok "verify.mjs 全部检查通过"
  else
    bad "verify.mjs 存在失败项（见 ${OUT#"$ROOT"/}/report.json；未就绪时属预期）"
  fi
}

# ── 主流程 ──────────────────────────────────────────────────────────────────
[[ "$RUN_STATIC" -eq 1 ]] && static_checks
[[ "$RUN_INSTANCE" -eq 1 ]] && instance_checks

say "汇总"
printf 'PASS %d · FAIL %d · SKIP %d\n' "$PASS" "$FAIL" "$SKIP"
if [[ "$FAIL" -gt 0 ]]; then
  printf '失败项：\n'
  printf '  - %s\n' "${FAILED[@]}"
fi
[[ -f "$OUT/report.json" ]] && echo "浏览器报告：${OUT#"$ROOT"/}/report.json"
node - "$OUT/check-summary.json" "$PASS" "$FAIL" "$SKIP" <<'NODE' 2>/dev/null || true
const fs = require('node:fs')
const [file, pass, fail, skip] = process.argv.slice(2)
fs.writeFileSync(file, JSON.stringify({ pass: Number(pass), fail: Number(fail), skip: Number(skip), at: new Date().toISOString() }, null, 2) + '\n')
NODE
exit "$([[ "$FAIL" -gt 0 ]] && echo 1 || echo 0)"
