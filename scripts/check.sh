#!/usr/bin/env bash
# dsh-tavern 验收入口：静态检查 + 隔离 DSH 实例 + 真实浏览器 E2E。
#
# 用法：
#   scripts/check.sh                 # 静态 + 隔离实例 + verify.mjs（默认）
#   scripts/check.sh --static        # 只跑静态检查（快，不启实例）
#   scripts/check.sh --instance      # 只跑隔离实例 + E2E
#   scripts/check.sh --with-app-tests # 额外跑应用前端 typecheck + 单测（慢）
#   scripts/check.sh --no-bridge     # E2E 跳过「交给 Agent」的端到端点击
#   scripts/check.sh --open-story   # E2E 先在应用内打开一部作品（会写阅读进度，全链路验证用）
#   scripts/check.sh --cards         # E2E 额外跑角色卡导入 + 单卡聊天（会写入/删除测试卡）
#   scripts/check.sh --real          # 真生成验收：隐含 --cards，断言回复不是 mock、刷新后仍在（需应用 AI_MODE=dsh + DSH 网关）
#   scripts/check.sh --real-negative # 负向演练：假定网关已被弄坏，断言 UI 显示失败而不是假回复（侵入环境，手动用）
#   scripts/check.sh --keep-home     # 保留 /tmp/dsh-tavern-check（排查用）
#   scripts/check.sh --port 3199     # 换端口
#
# 产物：
#   $OUT/report.json                   —— verify.mjs 的逐项结果（PASS/FAIL/SKIP）
#   $OUT/logs/*.log                    —— 每个静态命令的输出
#   $OUT/logs/dsh.log                  —— 隔离实例的 DSH 日志（启动失败时先看这个）
#   截图 01-sidebar.png … 08-refresh-home.png（含 03b-tavern-open / 06-restore-panel / 07-reload-restore）
#        --cards 时另有 09-cards-page.png … 14-cards-deleted.png
#   角色卡 fixture：/tmp/tavern-verify/cards/{v2-png.png,v2-json.json,v1-json.json,manifest.json}
#   --real 时 report.real 记录首句前 60 字/字数/耗时/模型名（不写 prompt 全文）。
#
# 设计（参考同级插件 dsh-ziwei/scripts/check.sh）：
#   - 在 /tmp/dsh-tavern-check 起一个完全隔离的 DSH_HOME，profile=check，端口默认 3099，
#     绝不动生产实例（3080）。node_modules / package.json 复用生产 profile（符号链接），
#     但 credentials / storages / 会话全部在临时目录里，退出时清掉。
#   - 生产 profile 已安装 dsh-tavern 时直接镜像它的 package.json + node_modules；
#     未安装（plugin-core 还没跑 install）时，生成一份 dev profile 把插件源目录
#     link 进 node_modules，这样 checkout 也能验收。
#   - 插件未就绪（/tavern 404、client.js 缺失、侧栏无入口）时脚本不崩，verify.mjs
#     会输出「未就绪」报告并 exit 1。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_HOME="${DSH_HOME:-$HOME/.dsh}"
REAL_PROFILE="$REAL_HOME/profiles/web"
PROFILE="check"
PORT="${DSH_TAVERN_CHECK_PORT:-3099}"
CHECK_HOME="${DSH_TAVERN_CHECK_HOME:-/tmp/dsh-tavern-check}"
OUT="${DSH_TAVERN_VERIFY_OUT:-/tmp/dsh-tavern-verify/check}"
LOGS="$OUT/logs"
DSH_BIN="$(command -v dsh || true)"

RUN_STATIC=1
RUN_INSTANCE=1
WITH_APP_TESTS=0
NO_BRIDGE=0
OPEN_STORY=0
CARDS=0
REAL=0
REAL_NEGATIVE=0
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
    --no-bridge) NO_BRIDGE=1 ;;
    --open-story) OPEN_STORY=1 ;;
    --cards) CARDS=1 ;;
    --real) REAL=1; CARDS=1 ;;
    --real-negative) REAL_NEGATIVE=1; REAL=1; CARDS=1 ;;
    --keep-home) KEEP_HOME=1 ;;
    --port) PORT="${2:?--port 需要端口号}"; shift ;;
    --help|-h) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1（--help 查看用法）" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$LOGS"
say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$*"; }
bad() { FAIL=$((FAIL + 1)); FAILED+=("$*"); printf 'FAIL  %s\n' "$*"; }
skip() { SKIP=$((SKIP + 1)); printf 'SKIP  %s\n' "$*"; }

# run "<人话名称>" <命令…>：输出进日志，失败时打印尾部。
run() {
  local name="$1"
  shift
  local log
  log="$LOGS/$(printf '%s' "$name" | tr ' /():' '______').log"
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
    # mirror：生产 profile 已经装了插件 → 直接复用它的依赖图（不改生产文件）
    for item in package.json node_modules; do
      ln -s "$REAL_PROFILE/$item" "$target/$item"
    done
    for item in cordis.yml cordis.patch.yml; do
      [[ -f "$REAL_PROFILE/$item" ]] && cp "$REAL_PROFILE/$item" "$target/$item"
    done
    echo "profile 模式：mirror（生产 profile 已安装 dsh-tavern）"
  else
    # dev：生产 profile 还没装插件 → 生成一份包含插件源目录的依赖图
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

  # 登录状态/工作区：复制到隔离 HOME，避免首次启动弹 "Get started"。
  if [[ -f "$REAL_HOME/.credentials.yaml" ]]; then
    cp "$REAL_HOME/.credentials.yaml" "$CHECK_HOME/.credentials.yaml"
    chmod 600 "$CHECK_HOME/.credentials.yaml"
  fi
  if [[ -f "$REAL_HOME/storages/workspace.json" ]]; then
    cp "$REAL_HOME/storages/workspace.json" "$CHECK_HOME/storages/workspace.json"
  fi
}

# ── 真生成前置检查（--real）─────────────────────────────────────────────────
# 只做「只读」探测，不改任何环境：读 app/.env 的 AI_MODE / DSH_AGENT_URL / DSH_AGENT_SECRET，
# 再带 secret 调一次网关 health。不可用时记 SKIP（由 Lead 决定是否重启/切换），
# 真正失败会由 verify.mjs 的 H9/R 组以「没有回复」体现，不在这里误判。
real_preflight() {
  say "真生成前置检查（--real）"
  local env_file="$ROOT/app/.env"
  local ai_mode="" agent_url="" agent_secret=""
  if [[ -f "$env_file" ]]; then
    ai_mode="$(sed -n 's/^AI_MODE=//p' "$env_file" | tail -1 | tr -d '\r' | tr -d '"')"
    agent_url="$(sed -n 's/^DSH_AGENT_URL=//p' "$env_file" | tail -1 | tr -d '\r' | tr -d '"')"
    agent_secret="$(sed -n 's/^DSH_AGENT_SECRET=//p' "$env_file" | tail -1 | tr -d '\r' | tr -d '"')"
  fi
  if [[ "$ai_mode" == "dsh" ]]; then
    ok "app/.env AI_MODE=dsh（真生成）"
  else
    skip "app/.env AI_MODE=${ai_mode:-未设置}：--real 需要 dsh；当前会得到 mock 回复（等 Lead/app-auth 切换后重跑）"
  fi
  [[ -n "$agent_url" ]] || agent_url="http://127.0.0.1:3080/tavern-agent/v1"
  agent_url="${agent_url%/}"
  if [[ -z "$agent_secret" ]]; then
    skip "DSH 模型网关 health（app/.env 里没有 DSH_AGENT_SECRET，无法鉴权探测）"
    return
  fi
  local health
  health="$(curl -s -m 5 -H "Authorization: Bearer $agent_secret" "$agent_url/health" 2>/dev/null || true)"
  if grep -q '"ok"[[:space:]]*:[[:space:]]*true' <<<"$health"; then
    local model
    model="$(node -e 'try{const j=JSON.parse(process.argv[1]);const m=j.model||{};process.stdout.write([m.provider,m.model].filter(Boolean).join("/")||"unknown")}catch{process.stdout.write("unknown")}' "$health" 2>/dev/null || echo unknown)"
    ok "DSH 模型网关 health ok（模型 ${model}）"
  else
    skip "DSH 模型网关 health 不可用（${agent_url}；3080 是否已重启加载 /tavern-agent？owner: plugin-core/Lead）—— --real 会体现为回复超时/UI 失败"
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

  # 旧 /story 别名：GET/HEAD 应 302 到 /tavern（匿名可能先被闸门拦成 401，都算已注册）。
  local legacy_headers legacy_code legacy_location
  legacy_headers="$(curl -s -o /dev/null -D - -m 5 "http://127.0.0.1:$PORT/story/?probe=1" 2>/dev/null || true)"
  legacy_code="$(printf '%s\n' "$legacy_headers" | awk 'NR==1{print $2}' | tr -d '\r')"
  legacy_location="$(printf '%s\n' "$legacy_headers" | grep -i '^location:' | head -1 | tr -d '\r' | sed 's/^[Ll]ocation: *//')"
  case "$legacy_code" in
    302)
      if [[ "$legacy_location" == *"/tavern/"* ]]; then
        ok "旧 /story/ 302 → $legacy_location"
      else
        bad "旧 /story/ 302 但 Location 不是 /tavern/：${legacy_location:-无}（owner: plugin-core）"
      fi
      ;;
    401) ok "旧 /story/ 被登录闸门挡住（401；登录态 302 由 verify.mjs 断言）" ;;
    404) bad "旧 /story/ 404：宿主半没注册兼容别名（owner: plugin-core）" ;;
    *) bad "旧 /story/ 期望 302/401，实际 HTTP ${legacy_code:-无响应}" ;;
  esac

  if [[ "$REAL" -eq 1 ]]; then
    real_preflight
  fi

  if [[ "$CARDS" -eq 1 ]]; then
    say "角色卡 fixture（make-cards.py）"
    local fixture_args=(--out /tmp/tavern-verify/cards)
    run "生成角色卡 fixture（v2 PNG / v2 JSON / v1 JSON）" python3 "$ROOT/scripts/fixtures/make-cards.py" "${fixture_args[@]}"
  fi

  say "真实浏览器 E2E（verify.mjs）"
  local verify_args=(--url "$url" --out "$OUT")
  [[ "$NO_BRIDGE" -eq 1 ]] && verify_args+=(--no-bridge)
  [[ "$OPEN_STORY" -eq 1 ]] && verify_args+=(--open-story)
  [[ "$CARDS" -eq 1 ]] && verify_args+=(--cards --cards-dir /tmp/tavern-verify/cards)
  [[ "$REAL" -eq 1 ]] && verify_args+=(--real)
  [[ "$REAL_NEGATIVE" -eq 1 ]] && verify_args+=(--real-negative)
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
