/**
 * dsh-tavern —— host 半：把本机「酒馆」（Tavern）应用挂到 DSH Web 的 `/tavern`
 * 前缀下，复用 DSH 自己的登录闸门，并给 API 请求附加可信登录头。
 *
 * 应用分两个本地进程（见 deploy/*.service）：
 *   - API    uvicorn，127.0.0.1:8001（/api/reader/v1/**、/api/world/v1/**、/health/**）
 *   - 静态站  python -m http.server 版 dist，127.0.0.1:8810（index.html、/assets、sw.js…）
 *
 * 设计要点：
 *  - 只在 127.0.0.1 上反代，不碰公网；公网访问统一走 Caddy → DSH 登录网关。
 *  - 每个请求先过 `ctx.connection.requestRejection()`：它返回 403（Host/Origin
 *    不在信任围栏内）或 401（未通过浏览器登录），undefined 才放行。这样内嵌
 *    面板天然继承 DSH 的 token/cookie 会话，不需要另做一套认证。
 *  - 分流（先剥掉 `/tavern` 前缀再转发）：
 *      `/tavern/api/**`、`/tavern/health/**` → 127.0.0.1:8001
 *      其余 `/tavern/**`（HTML、/assets、sw.js、favicon…）→ 127.0.0.1:8810
 *  - 转发给 API 的请求附加 `x-dsh-trusted: <secret>`；客户端自己传的同名头
 *    一律先删除，绝不能透传（否则等于任何人都能给自己发可信身份）。
 *  - 流式：API 可能是数分钟的 SSE（生成/续写），**不设响应超时**；静态资源
 *    10s 超时即可。用 node:http 管道转发，保留 chunked / multipart 上传 / SSE。
 *  - secret 启动时从 `app/.env` 的 `DSH_TRUSTED_SECRET` 读取（环境变量
 *    `DSH_TAVERN_TRUSTED_SECRET` 优先）；读不到只告警，不崩宿主。
 *  - 兼容别名：旧地址 `/story` 仍然可用（书签、旧 iframe 缓存、旧客户端 dist
 *    都可能还在用）。GET/HEAD 用 302 跳到 `/tavern` 同后缀同 query；其它方法
 *    （POST/PUT…，含上传和生成请求）**不重定向**，直接把前缀改写成 `/tavern`
 *    后照常反代 —— 既避免 302 把 POST 降级成 GET，也避免 308 要求客户端重放 body。
 *
 * 另外提供「DSH 模型网关」宿主路由 `/tavern-agent/v1/**`（见 docs/step2-dsh-generation.md）：
 * 应用的 worker 进程用 `Authorization: Bearer <secret>` 直接调用 DSH 当前配置的模型，
 * 聊天记录仍存在应用侧。这条链路**不经** `requestRejection`、也**不碰** iframe 会话，
 * 只做 Bearer 鉴权 + `ctx.llm.stream()` 转发；失败统一 502/SSE error 帧，
 * 日志和错误里绝不出现 prompt、消息内容或 secret。
 */
import { randomUUID, timingSafeEqual } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { request as httpRequest } from 'node:http'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** DSH Web 里挂载应用的前缀（新地址）。 */
const ROUTE_PATH = '/tavern'
/** 旧前缀：只作为兼容别名保留，不再作为主地址。 */
const LEGACY_ROUTE_PATH = '/story'
const UPSTREAM_HOST = '127.0.0.1'
/** 上游端口：允许用环境变量覆盖（隔离测试实例 / 换端口时不用改代码）。 */
const API_PORT = Number(process.env.DSH_TAVERN_API_PORT || 8001)
const STATIC_PORT = Number(process.env.DSH_TAVERN_STATIC_PORT || 8810)
/** 静态资源超时（毫秒）；API 不设超时，生成请求可能持续数分钟。 */
const STATIC_TIMEOUT_MS = 10_000
/** 插件根目录 → 应用 .env 的默认位置。 */
const DEFAULT_ENV_FILE = join(fileURLToPath(new URL('..', import.meta.url)), 'app', '.env')
/**
 * 实际的 .env 路径：允许 `DSH_TAVERN_ENV_FILE` 覆盖。
 * 隔离测试实例 / 自测脚本用它指到临时文件，生产不设置即走默认路径。
 */
function envFilePath() {
  return String(process.env.DSH_TAVERN_ENV_FILE || '').trim() || DEFAULT_ENV_FILE
}
/** 应用侧识别的可信登录头。 */
const TRUSTED_HEADER = 'x-dsh-trusted'
/** 日志前缀：方便在 DSH journal 里过滤本插件。 */
const LOG_PREFIX = '[dsh-tavern]'

/** RFC 7230 hop-by-hop 头，不该被代理透传。 */
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

/** 从 app/.env 读一行 KEY=VALUE；读不到返回空串，绝不抛错。 */
function readEnvValue(file, wantedKey) {
  let text
  try {
    text = readFileSync(file, 'utf8')
  } catch (error) {
    console.warn(`${LOG_PREFIX} 读取 ${file} 失败：`, (error && error.message) || error)
    return ''
  }
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq <= 0) continue
    if (line.slice(0, eq).trim() !== wantedKey) continue
    // 去掉可能存在的成对引号（.env 也允许 "value" 写法）。
    return line.slice(eq + 1).trim().replace(/^(['"])(.*)\1$/, '$2').trim()
  }
  return ''
}

/**
 * 可信登录 secret：环境变量 `DSH_TAVERN_TRUSTED_SECRET` 优先，
 * 其次 app/.env 的 `DSH_TRUSTED_SECRET`（名字沿用应用侧，UI 无关）。
 * 返回空串表示没有配置 —— 此时照常转发但不加头，应用侧按未登录处理。
 */
function readTrustedSecret() {
  const fromEnv = String(process.env.DSH_TAVERN_TRUSTED_SECRET || '').trim()
  if (fromEnv) return fromEnv
  return readEnvValue(envFilePath(), 'DSH_TRUSTED_SECRET')
}

/**
 * 模型网关的 Bearer secret：`DSH_TAVERN_AGENT_SECRET` 优先（环境变量或 app/.env），
 * 其次回退可信登录 secret（应用侧两个值通常配成同一个）。空串表示没配置，
 * 此时 `apply` 之后的网关请求**一律 401**，绝不静默放行。
 */
function readAgentSecret() {
  const fromEnv = String(process.env.DSH_TAVERN_AGENT_SECRET || '').trim()
  if (fromEnv) return fromEnv
  const fromFile = readEnvValue(envFilePath(), 'DSH_TAVERN_AGENT_SECRET')
  if (fromFile) return fromFile
  return readTrustedSecret()
}

/** 判断 rawUrl 是否落在某个前缀下（前缀本身，或前缀 + '/'）。 */
function underPrefix(rawUrl, prefix) {
  return rawUrl === prefix || rawUrl.startsWith(prefix + '/')
}

/** 把旧前缀 `/story` 换成新前缀 `/tavern`，保留 path 后缀与 query。 */
function rewriteLegacyPath(rawUrl) {
  const rest = (rawUrl || '').slice(LEGACY_ROUTE_PATH.length)
  return ROUTE_PATH + rest
}

/** 剥掉 `/tavern` 前缀；`/tavern` 本身 → `/`（静态站首页）。 */
function stripRoutePrefix(rawUrl) {
  const url = rawUrl || '/'
  if (url === ROUTE_PATH) return '/'
  if (url.startsWith(ROUTE_PATH + '/')) {
    const rest = url.slice(ROUTE_PATH.length)
    return rest || '/'
  }
  // register({kind:'prefix'}) 保证进入这里的都以 /tavern 开头；兜底原样转发。
  return url
}

/** `/api/**`、`/health/**` 归 API，其余归静态站。 */
function isApiPath(pathname) {
  return (
    pathname === '/api' ||
    pathname.startsWith('/api/') ||
    pathname === '/health' ||
    pathname.startsWith('/health/')
  )
}

function routeTarget(upstreamPath) {
  const pathname = upstreamPath.split('?')[0]
  if (isApiPath(pathname)) {
    // API：不设响应超时（SSE / 长生成）。
    return { kind: 'api', port: API_PORT, timeoutMs: 0 }
  }
  return { kind: 'static', port: STATIC_PORT, timeoutMs: STATIC_TIMEOUT_MS }
}

/**
 * 转发给上游的请求头：剥掉逐跳头，重写 Host / X-Forwarded-*，
 * 删除客户端传入的可信头，API 请求再按本插件的 secret 重新签发。
 */
function upstreamHeaders(req, target, secret) {
  const out = {}
  for (const [key, value] of Object.entries(req.headers)) {
    if (value === undefined) continue
    const lower = key.toLowerCase()
    if (HOP_BY_HOP.has(lower)) continue
    // 客户端自带的可信头一律丢弃，只允许本插件按自己的 secret 重新签发。
    if (lower === TRUSTED_HEADER) continue
    // x-forwarded-* 由本层重写，避免客户端伪造来源。
    if (lower.startsWith('x-forwarded-')) continue
    out[lower] = value
  }
  out.host = `${UPSTREAM_HOST}:${target.port}`
  out['x-forwarded-host'] = req.headers.host || `${UPSTREAM_HOST}:${target.port}`
  out['x-forwarded-proto'] = 'http'
  if (req.socket && req.socket.remoteAddress) {
    out['x-forwarded-for'] = req.socket.remoteAddress
  }
  // 只有 API 需要可信身份；静态资源不需要，也不该带。
  if (target.kind === 'api' && secret) {
    out[TRUSTED_HEADER] = secret
  }
  return out
}

/** 上游响应头：同样剥掉逐跳头，交给 node 自己算 Transfer-Encoding。 */
function downstreamHeaders(upstreamRes) {
  const out = {}
  for (const [key, value] of Object.entries(upstreamRes.headers)) {
    if (value === undefined) continue
    if (HOP_BY_HOP.has(key.toLowerCase())) continue
    out[key] = value
  }
  return out
}

function sendPlain(res, status, text) {
  if (res.headersSent) {
    res.end()
    return
  }
  res.writeHead(status, {
    'content-type': 'text/plain; charset=utf-8',
    'cache-control': 'no-store',
  })
  res.end(text)
}

/** 鉴权闸门：没登录/主机不可信就直接挡掉，绝不把本地应用暴露出去。 */
function rejectUnprivileged(ctx, req, res) {
  let rejection
  try {
    const connection = ctx.get('connection', false)
    rejection = connection && typeof connection.requestRejection === 'function'
      ? connection.requestRejection(req)
      : 401
  } catch {
    rejection = 401
  }
  if (!rejection) return false
  const text = rejection === 403
    ? 'dsh web: 该请求的主机/来源不在信任列表内。\n'
    : 'dsh web authentication required; reopen the URL printed by dsh web.\n'
  sendPlain(res, rejection, text)
  return true
}

/** `/tavern` 前缀 → 上游（API 或静态站）的流式反代。 */
function forward(req, res, upstreamPath, target, secret) {
  const label = target.kind === 'api' ? 'API' : '静态站'
  const upstream = httpRequest(
    {
      host: UPSTREAM_HOST,
      port: target.port,
      method: req.method,
      path: upstreamPath,
      headers: upstreamHeaders(req, target, secret),
    },
    (upstreamRes) => {
      if (res.writableEnded || res.destroyed) {
        upstreamRes.destroy()
        return
      }
      res.writeHead(upstreamRes.statusCode || 502, downstreamHeaders(upstreamRes))
      // SSE 必须立刻把响应头推给浏览器，否则面板要等到第一帧数据才显示「正在生成」。
      const contentType = String(upstreamRes.headers['content-type'] || '')
      if (contentType.startsWith('text/event-stream')) {
        try {
          res.flushHeaders()
        } catch {}
      }
      upstreamRes.pipe(res)
      upstreamRes.on('error', () => {
        try {
          res.destroy()
        } catch {}
      })
    },
  )

  if (target.timeoutMs > 0) {
    // 上游卡住（不响应）时不能把 handler 永久挂起：静态资源超时直接 504。
    upstream.setTimeout(target.timeoutMs, () => {
      upstream.destroy(Object.assign(new Error('上游响应超时'), { code: 'ETIMEDOUT' }))
    })
  }

  upstream.on('error', (error) => {
    if (error && error.code === 'ETIMEDOUT') {
      console.warn(`${LOG_PREFIX} ${label}响应超时（${target.timeoutMs}ms）: ${req.method} ${req.url}`)
      sendPlain(res, 504, '酒馆应用响应超时，请稍后重试。\n')
      return
    }
    const reason = error && error.code ? error.code : 'ECONNREFUSED'
    // 诊断信息只写宿主日志：回给浏览器的文案不带错误码、端口、路径。
    console.warn(
      `${LOG_PREFIX} 反代失败（${reason}）: ${req.method} ${req.url} → 127.0.0.1:${target.port}`,
    )
    sendPlain(res, 502, '酒馆应用暂时不可用，请稍后重试。\n')
  })

  req.on('aborted', () => upstream.destroy())
  res.on('close', () => upstream.destroy())
  req.pipe(upstream)
}

function createHandler(ctx, secret) {
  return function tavernProxy(req, res) {
    if (rejectUnprivileged(ctx, req, res)) return
    let rawUrl = req.url || '/'
    if (underPrefix(rawUrl, LEGACY_ROUTE_PATH)) {
      if (req.method === 'GET' || req.method === 'HEAD') {
        // 旧地址 302 到新地址：临时跳转，path 后缀与 query 原样保留。
        // 为什么不是 301：别名是兼容手段，将来可能下线，别让浏览器永久缓存。
        res.writeHead(302, {
          location: rewriteLegacyPath(rawUrl),
          'cache-control': 'no-store',
        })
        res.end()
        return
      }
      // 非 GET/HEAD 不做重定向：302 会把 POST 降级成 GET（丢 body），
      // 308 则要求客户端自己重放 body（浏览器做不到无感）。直接改写成
      // /tavern 同路径后照常反代，body 一个字节都不碰。
      req.url = rewriteLegacyPath(rawUrl)
      rawUrl = req.url
    }
    const upstreamPath = stripRoutePrefix(rawUrl)
    const target = routeTarget(upstreamPath)
    forward(req, res, upstreamPath, target, secret)
  }
}

// ---------------------------------------------------------------------------
// DSH 模型网关：/tavern-agent/v1/**（见 docs/step2-dsh-generation.md §2）
//
// 应用 worker 是服务端进程，浏览器同源/登录闸门对它没有意义；这里用一条独立
// 的共享 secret（`Authorization: Bearer`）鉴权，只把「一次模型调用」暴露出去：
//   POST /tavern-agent/v1/complete → 一次性文本
//   POST /tavern-agent/v1/stream   → SSE 增量
//   GET  /tavern-agent/v1/health   → 是否可用 + 当前默认模型
// 这条链路不写 session、不碰 iframe，也不经过 requestRejection。
// ---------------------------------------------------------------------------

/** 模型网关的宿主前缀（与 /tavern 平级，互不影响）。 */
const AGENT_ROUTE_PATH = '/tavern-agent'
/** 单次模型调用上限：聊天回复通常几十秒，120s 足够且能兜住挂死的上游。 */
const AGENT_TIMEOUT_MS = 120_000
/** 请求体上限：对话历史以 JSON 传入，4 MiB 足够且不会打满宿主内存。 */
const AGENT_MAX_BODY_BYTES = 4 * 1024 * 1024
const AGENT_ERROR_UNAUTHORIZED = 'AGENT_UNAUTHORIZED'
const AGENT_ERROR_UPSTREAM = 'AGENT_UPSTREAM_FAILED'
const AGENT_ERROR_BAD_REQUEST = 'AGENT_BAD_REQUEST'
/** 采样参数校验失败：请求体结构没问题，但某个字段越界/类型不对。 */
const AGENT_ERROR_INVALID_ARGUMENT = 'INVALID_ARGUMENT'
/** stop 序列上限：与 OpenAI 系 provider 的常见限制对齐。 */
const AGENT_MAX_STOP_SEQUENCES = 4
const AGENT_MAX_STOP_CHARS = 200

/** 网关超时：默认 120s；自测可用环境变量调小，生产不要设置。 */
function agentTimeoutMs() {
  const raw = Number(process.env.DSH_TAVERN_AGENT_TIMEOUT_MS)
  return Number.isFinite(raw) && raw > 0 ? raw : AGENT_TIMEOUT_MS
}

/** 常量时间比较两个 secret，避免逐字节比较泄漏长度/前缀信息。 */
function secretEquals(a, b) {
  const left = Buffer.from(String(a), 'utf8')
  const right = Buffer.from(String(b), 'utf8')
  return left.length === right.length && timingSafeEqual(left, right)
}

/** `Authorization: Bearer <secret>` 是否匹配；secret 为空（未配置）时永远不匹配。 */
function agentAuthorized(req, secret) {
  if (!secret) return false
  const header = String(req.headers.authorization || '').trim()
  const match = /^Bearer\s+(.+)$/i.exec(header)
  return Boolean(match) && secretEquals(match[1].trim(), secret)
}

function sendJson(res, status, payload) {
  if (res.headersSent) {
    res.end()
    return
  }
  const body = JSON.stringify(payload)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(body),
  })
  res.end(body)
}

/** 统一错误响应：`{"error":{"code"[, "message"]}}`。 */
function sendAgentError(res, status, code, message) {
  sendJson(res, status, message === undefined ? { error: { code } } : { error: { code, message } })
}

/**
 * 只取机器可读错误码用于日志。
 * provider 的原始错误消息可能回显请求内容，绝不允许进日志/响应。
 */
function safeErrorCode(error) {
  if (!error) return 'UNKNOWN'
  if (typeof error.code === 'string' && error.code) return error.code
  if (typeof error.name === 'string' && error.name) return error.name
  return 'UNKNOWN'
}

function agentBadRequest(publicMessage) {
  const error = new Error('bad request')
  error.code = AGENT_ERROR_BAD_REQUEST
  error.publicMessage = publicMessage
  return error
}

function agentUpstreamError(publicMessage) {
  const error = new Error('upstream failed')
  error.code = AGENT_ERROR_UPSTREAM
  error.publicMessage = publicMessage
  return error
}

/** 采样参数不合法（400 INVALID_ARGUMENT）：与「请求体结构不对」的 AGENT_BAD_REQUEST 区分。 */
function agentInvalidArgument(publicMessage) {
  const error = new Error('invalid argument')
  error.code = AGENT_ERROR_INVALID_ARGUMENT
  error.publicMessage = publicMessage
  return error
}

/** 读 JSON 请求体；超限、坏 JSON、中断都归一成 AGENT_BAD_REQUEST。 */
function readAgentBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    let settled = false
    const fail = (error) => {
      if (settled) return
      settled = true
      reject(error)
    }
    req.on('data', (chunk) => {
      if (settled) return
      size += chunk.length
      if (size > AGENT_MAX_BODY_BYTES) {
        fail(agentBadRequest('请求体过大。'))
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => {
      if (settled) return
      settled = true
      const text = Buffer.concat(chunks).toString('utf8').trim()
      if (!text) {
        resolve({})
        return
      }
      try {
        resolve(JSON.parse(text))
      } catch {
        reject(agentBadRequest('请求体不是合法 JSON。'))
      }
    })
    req.on('error', (error) => fail(error))
    req.on('aborted', () => fail(agentBadRequest('请求被中断。')))
    // 连接在 body 没读完时就断了：'aborted' 在新版本 node 里语义弱化，
    // 这里再用 close 兜一次，避免 handler 永远等不到 resolve/reject。
    req.on('close', () => {
      if (!settled && !req.complete) fail(agentBadRequest('请求被中断。'))
    })
  })
}

/** 内容既接受字符串（应用当前写法），也接受 ContentBlock 数组（只取 text 块）。 */
function textOfContent(content) {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    const parts = []
    for (const block of content) {
      if (block && block.type === 'text' && typeof block.text === 'string') parts.push(block.text)
    }
    return parts.join('')
  }
  return ''
}

/**
 * 校验采样参数（酒馆 2.0 P0）：temperature 0..2、topP 0..1、
 * stop 单条字符串或数组（≤4 条、单条 ≤200 字符，转成数组交给 llm）。
 * 只有字段出现时才校验；缺省/`null` 视为不传，交给 provider 默认值；
 * 未知字段忽略。类型/越界抛 400 INVALID_ARGUMENT。
 */
function normalizeSamplingParams(payload) {
  let temperature
  if (payload.temperature !== undefined && payload.temperature !== null) {
    if (typeof payload.temperature !== 'number' || !Number.isFinite(payload.temperature)) {
      throw agentInvalidArgument('temperature 必须是数字。')
    }
    if (payload.temperature < 0 || payload.temperature > 2) {
      throw agentInvalidArgument('temperature 必须在 0 到 2 之间。')
    }
    temperature = payload.temperature
  }

  // topP 是契约字段名；酒馆设计文档里写的是 top_p，这里顺手接受 snake_case 别名。
  const rawTopP = payload.topP !== undefined ? payload.topP : payload.top_p
  let topP
  if (rawTopP !== undefined && rawTopP !== null) {
    if (typeof rawTopP !== 'number' || !Number.isFinite(rawTopP)) {
      throw agentInvalidArgument('topP 必须是数字。')
    }
    if (rawTopP < 0 || rawTopP > 1) {
      throw agentInvalidArgument('topP 必须在 0 到 1 之间。')
    }
    topP = rawTopP
  }

  let stop
  if (payload.stop !== undefined && payload.stop !== null) {
    // 单条字符串是常见简写，统一归一成数组再校验，避免下游分叉。
    const raw = typeof payload.stop === 'string' ? [payload.stop] : payload.stop
    if (!Array.isArray(raw)) throw agentInvalidArgument('stop 必须是字符串或字符串数组。')
    if (raw.length > AGENT_MAX_STOP_SEQUENCES) {
      throw agentInvalidArgument(`stop 最多 ${AGENT_MAX_STOP_SEQUENCES} 条。`)
    }
    const sequences = []
    for (const item of raw) {
      if (typeof item !== 'string') throw agentInvalidArgument('stop 必须是字符串或字符串数组。')
      if (!item) throw agentInvalidArgument('stop 不能包含空字符串。')
      if (item.length > AGENT_MAX_STOP_CHARS) {
        throw agentInvalidArgument(`单条 stop 不能超过 ${AGENT_MAX_STOP_CHARS} 字符。`)
      }
      sequences.push(item)
    }
    if (sequences.length > 0) stop = sequences
  }

  return { temperature, topP, stop }
}

/** 校验并归一化应用发来的请求；不合法时抛 AGENT_BAD_REQUEST。 */
function normalizeAgentRequest(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw agentBadRequest('请求体必须是 JSON 对象。')
  }
  const rawMessages = Array.isArray(payload.messages) ? payload.messages : []
  if (rawMessages.length === 0) throw agentBadRequest('messages 不能为空。')
  if (rawMessages.length > 64) throw agentBadRequest('messages 过多。')
  const messages = []
  let inputChars = 0
  for (const raw of rawMessages) {
    if (!raw || typeof raw !== 'object') throw agentBadRequest('messages 元素必须是对象。')
    const role = typeof raw.role === 'string' ? raw.role : 'user'
    // 契约只定义单轮 user 输入；多轮上下文由应用自己拼进 content。
    if (role !== 'user') throw agentBadRequest('messages 只支持 user 角色。')
    const text = textOfContent(raw.content)
    if (!text) throw agentBadRequest('messages 内容不能为空。')
    messages.push({ role: 'user', content: [{ type: 'text', text }] })
    inputChars += text.length
  }
  const system = typeof payload.system === 'string' ? payload.system : undefined
  if (system) inputChars += system.length
  const maxTokens = Number.isInteger(payload.maxTokens) && payload.maxTokens > 0
    ? payload.maxTokens
    : undefined
  // temperature/topP/stop：酒馆原生聊天需要的采样参数（P0 网关扩展）。
  const sampling = normalizeSamplingParams(payload)
  // 某些 provider（如 opencode-go）要求请求带 session id，否则直接 400。
  // 应用可按会话传稳定值（有利于上游缓存/路由）；缺省时每次生成一个。
  let sessionId
  if (typeof payload.sessionId === 'string') {
    const trimmed = payload.sessionId.trim()
    if (trimmed && trimmed.length <= 200) sessionId = trimmed
  }
  // 聊天优先快：默认 low 思考（profile 里默认是 high；该模型不支持 off/minimal/medium）。
  const REASONING_LEVELS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
  let reasoningEffort = 'low'
  if (typeof payload.reasoningEffort === 'string' && REASONING_LEVELS.includes(payload.reasoningEffort)) {
    reasoningEffort = payload.reasoningEffort
  }
  let model
  if (payload.model && typeof payload.model === 'object' && !Array.isArray(payload.model)) {
    const provider = typeof payload.model.provider === 'string' ? payload.model.provider.trim() : ''
    const id = typeof payload.model.model === 'string' ? payload.model.model.trim() : ''
    if (provider && id) model = { provider, model: id }
  }
  return {
    system,
    messages,
    maxTokens,
    temperature: sampling.temperature,
    topP: sampling.topP,
    stop: sampling.stop,
    model,
    sessionId,
    reasoningEffort,
    inputChars,
  }
}

/** 取 cordis 服务：优先 ctx.get（测试/多实例更稳），退化到属性访问。 */
function serviceOf(ctx, name) {
  if (ctx && typeof ctx.get === 'function') {
    const service = ctx.get(name, false)
    if (service) return service
  }
  return ctx ? ctx[name] : undefined
}

/** 当前默认模型（agent-default-model 配置的 provider/model）；不可用时返回 undefined。 */
function currentAgentModel(ctx) {
  try {
    const service = serviceOf(ctx, 'agentDefaultModel')
    const selection = service && typeof service.currentSelection === 'function'
      ? service.currentSelection()
      : undefined
    if (selection && typeof selection.provider === 'string' && selection.provider && typeof selection.model === 'string' && selection.model) {
      return { provider: selection.provider, model: selection.model }
    }
  } catch {
    // 默认模型服务读失败按「没有可用模型」处理，不把宿主异常抛给应用。
  }
  return undefined
}

/** 请求显式指定 model 时用请求的，否则用 DSH 当前默认模型。 */
function resolveAgentModel(ctx, requested) {
  if (requested) return requested
  const fallback = currentAgentModel(ctx)
  if (fallback) return fallback
  throw agentUpstreamError('DSH 没有可用的默认模型，请先选择模型后重试。')
}

/**
 * 组装 ctx.llm.stream() 的选项。
 * reasoningEffort 默认 low：profile 的默认值是 high，直接省略会继承 high，让
 * maxTokens 烧在看不见的思考里；deepseek-v4.1-flash 实测只支持 low/high
 * （off/minimal/medium 会报 UNSUPPORTED_REASONING_EFFORT）。
 * sessionId：opencode-go 等 provider 要求 `x-opencode-session`，缺省随机。
 */
function buildAgentOptions(parsed, model, signal) {
  const options = {
    provider: model.provider,
    model: model.model,
    messages: parsed.messages,
    // opencode-go 需要会话标识（x-opencode-session）；缺省随机一个。
    sessionId: parsed.sessionId || randomUUID(),
    // 明确覆盖 profile 的 high；聊天默认 low 思考，响应更快。
    reasoningEffort: parsed.reasoningEffort,
    signal,
  }
  if (parsed.system) options.system = parsed.system
  if (parsed.maxTokens !== undefined) options.maxTokens = parsed.maxTokens
  if (parsed.temperature !== undefined) options.temperature = parsed.temperature
  // topP：DSH 的 GenerateOptions 当前没有声明该字段，这里按原样透传，等适配器
  // 支持后自然生效；不支持的 provider 会忽略，不会报错。
  if (parsed.topP !== undefined) options.topP = parsed.topP
  // stop：GenerateOptions.stop 是 string[]（适配器映射到 provider 的 stop）。
  if (parsed.stop !== undefined) options.stop = parsed.stop
  return options
}

/** FinishReason.kind → 应用侧习惯的 finishReason 字符串。 */
function mapFinishReason(kind) {
  if (kind === 'stop') return 'stop'
  if (kind === 'max-tokens') return 'length'
  if (kind === 'tool-calls') return 'tool-calls'
  if (kind === 'aborted') return 'aborted'
  return 'error'
}

/** 没有精确 usage 时按字符数粗估（4 字符 ≈ 1 token），并在 state 里标明 estimated。 */
function estimateTokens(chars) {
  return Math.max(0, Math.ceil(chars / 4))
}

function mapUsage(usage, inputChars, outputText) {
  if (usage && Number.isFinite(usage.inputTokens) && Number.isFinite(usage.outputTokens)) {
    return { state: 'known', inputTokens: usage.inputTokens, outputTokens: usage.outputTokens }
  }
  return {
    state: 'estimated',
    inputTokens: estimateTokens(inputChars),
    outputTokens: estimateTokens(outputText.length),
  }
}

/**
 * 跑一次完整生成：只收集 text-delta，reasoning-delta 丢弃，usage/finish 留作响应。
 * @param onDelta 可选；SSE 模式下每个文本增量立即回调。
 */
async function runAgentCompletion(ctx, parsed, model, signal, onDelta) {
  const llm = serviceOf(ctx, 'llm')
  if (!llm || typeof llm.stream !== 'function') {
    throw agentUpstreamError('DSH 模型服务不可用，请稍后重试。')
  }
  const options = buildAgentOptions(parsed, model, signal)
  let text = ''
  let usage
  let finish
  for await (const chunk of llm.stream(options)) {
    if (!chunk || typeof chunk !== 'object') continue
    if (chunk.type === 'text-delta') {
      if (typeof chunk.text === 'string' && chunk.text) {
        text += chunk.text
        if (onDelta) onDelta(chunk.text)
      }
    } else if (chunk.type === 'usage') {
      usage = chunk.usage
    } else if (chunk.type === 'finish') {
      finish = chunk.reason
    }
    // block-start / block-end / tool-call-delta / reasoning-delta 一律忽略。
  }
  // 超时/客户端断开时 signal 已被 abort；即便上游在收尾阶段还吐了 finish，
  // 这次调用也已经失效，不能把半截结果当成成功返回。
  if (signal && signal.aborted) throw agentUpstreamError('模型生成超时，请稍后重试。')
  if (!finish || !finish.kind) throw agentUpstreamError('模型没有返回完成信号，请稍后重试。')
  if (finish.kind === 'error' || finish.kind === 'aborted') {
    throw agentUpstreamError('模型调用失败，请稍后重试。')
  }
  return {
    text,
    finishReason: mapFinishReason(finish.kind),
    usage: mapUsage(usage, parsed.inputChars, text),
  }
}

async function handleAgentComplete(ctx, req, res) {
  const startedAt = Date.now()
  let parsed
  try {
    parsed = normalizeAgentRequest(await readAgentBody(req))
  } catch (error) {
    // 结构错误是 AGENT_BAD_REQUEST；采样参数越界是 INVALID_ARGUMENT，按 error.code 透出。
    sendAgentError(res, 400, (error && error.code) || AGENT_ERROR_BAD_REQUEST, (error && error.publicMessage) || '请求不合法。')
    return
  }
  let model
  try {
    model = resolveAgentModel(ctx, parsed.model)
  } catch (error) {
    sendAgentError(res, 502, AGENT_ERROR_UPSTREAM, (error && error.publicMessage) || '模型不可用。')
    return
  }

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, agentTimeoutMs())
  if (typeof timer.unref === 'function') timer.unref()
  // 客户端断开（应用侧超时/取消）时尽快停止上游生成，不留下孤儿请求。
  const onClose = () => {
    if (!res.writableEnded) controller.abort()
  }
  res.on('close', onClose)
  try {
    const result = await runAgentCompletion(ctx, parsed, model, controller.signal)
    clearTimeout(timer)
    sendJson(res, 200, {
      text: result.text,
      model: { provider: model.provider, model: model.model },
      usage: result.usage,
      finishReason: result.finishReason,
    })
    console.info(
      `${LOG_PREFIX} agent complete ok: ${model.provider}/${model.model} ${result.text.length} 字 ${Date.now() - startedAt}ms`,
    )
  } catch (error) {
    clearTimeout(timer)
    if (res.writableEnded || res.destroyed) return
    const message = timedOut
      ? '模型生成超时，请稍后重试。'
      : (error && error.publicMessage) || '模型调用失败，请稍后重试。'
    sendAgentError(res, 502, AGENT_ERROR_UPSTREAM, message)
    console.warn(
      `${LOG_PREFIX} agent complete 失败: ${safeErrorCode(error)}${timedOut ? ' (timeout)' : ''} ${Date.now() - startedAt}ms`,
    )
  } finally {
    res.off('close', onClose)
  }
}

async function handleAgentStream(ctx, req, res) {
  const startedAt = Date.now()
  let parsed
  try {
    parsed = normalizeAgentRequest(await readAgentBody(req))
  } catch (error) {
    // 结构错误是 AGENT_BAD_REQUEST；采样参数越界是 INVALID_ARGUMENT，按 error.code 透出。
    sendAgentError(res, 400, (error && error.code) || AGENT_ERROR_BAD_REQUEST, (error && error.publicMessage) || '请求不合法。')
    return
  }
  let model
  try {
    model = resolveAgentModel(ctx, parsed.model)
  } catch (error) {
    sendAgentError(res, 502, AGENT_ERROR_UPSTREAM, (error && error.publicMessage) || '模型不可用。')
    return
  }

  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-store',
    connection: 'keep-alive',
    'x-accel-buffering': 'no',
  })
  try {
    res.flushHeaders()
  } catch {}
  const writeFrame = (payload) => {
    if (res.writableEnded || res.destroyed) return false
    res.write(`data: ${JSON.stringify(payload)}\n\n`)
    return true
  }

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, agentTimeoutMs())
  if (typeof timer.unref === 'function') timer.unref()
  const onClose = () => {
    if (!res.writableEnded) controller.abort()
  }
  res.on('close', onClose)
  try {
    const result = await runAgentCompletion(ctx, parsed, model, controller.signal, (delta) => {
      writeFrame({ type: 'delta', text: delta })
    })
    clearTimeout(timer)
    writeFrame({ type: 'usage', usage: result.usage })
    writeFrame({ type: 'finish', reason: result.finishReason })
    if (!res.writableEnded && !res.destroyed) {
      res.write('data: [DONE]\n\n')
      res.end()
    }
    console.info(
      `${LOG_PREFIX} agent stream ok: ${model.provider}/${model.model} ${result.text.length} 字 ${Date.now() - startedAt}ms`,
    )
  } catch (error) {
    clearTimeout(timer)
    if (res.writableEnded || res.destroyed) return
    const message = timedOut
      ? '模型生成超时，请稍后重试。'
      : (error && error.publicMessage) || '模型调用失败，请稍后重试。'
    // SSE 已经开始就统一用 error 帧收尾，和契约「错误后结束」一致。
    writeFrame({ type: 'error', error: { code: AGENT_ERROR_UPSTREAM, message } })
    res.end()
    console.warn(
      `${LOG_PREFIX} agent stream 失败: ${safeErrorCode(error)}${timedOut ? ' (timeout)' : ''} ${Date.now() - startedAt}ms`,
    )
  } finally {
    res.off('close', onClose)
  }
}

function handleAgentHealth(ctx, req, res) {
  const model = currentAgentModel(ctx) || { provider: '', model: '' }
  sendJson(res, 200, {
    ok: true,
    model: { provider: model.provider, model: model.model },
    // 网关支持透传的采样参数（酒馆 2.0）；应用可据此决定设置页显示哪些控件。
    features: ['temperature', 'topP', 'stop'],
  })
}

function createAgentHandler(ctx, secret) {
  return function tavernAgentGateway(req, res) {
    const pathname = String(req.url || '').split('?')[0]
    // 网关不走 DSH 登录闸门：应用 worker 没有浏览器会话，只认共享 secret。
    if (!agentAuthorized(req, secret)) {
      sendAgentError(res, 401, AGENT_ERROR_UNAUTHORIZED)
      return
    }
    const prefix = `${AGENT_ROUTE_PATH}/v1`
    if (pathname === `${prefix}/health`) {
      if (req.method !== 'GET') {
        sendAgentError(res, 405, 'AGENT_METHOD_NOT_ALLOWED', '只支持 GET。')
        return
      }
      handleAgentHealth(ctx, req, res)
      return
    }
    if (pathname === `${prefix}/complete`) {
      if (req.method !== 'POST') {
        sendAgentError(res, 405, 'AGENT_METHOD_NOT_ALLOWED', '只支持 POST。')
        return
      }
      void handleAgentComplete(ctx, req, res).catch((error) => {
        console.warn(`${LOG_PREFIX} agent complete 未捕获异常: ${safeErrorCode(error)}`)
        sendAgentError(res, 500, AGENT_ERROR_UPSTREAM, '内部错误，请稍后重试。')
      })
      return
    }
    if (pathname === `${prefix}/stream`) {
      if (req.method !== 'POST') {
        sendAgentError(res, 405, 'AGENT_METHOD_NOT_ALLOWED', '只支持 POST。')
        return
      }
      void handleAgentStream(ctx, req, res).catch((error) => {
        console.warn(`${LOG_PREFIX} agent stream 未捕获异常: ${safeErrorCode(error)}`)
        sendAgentError(res, 500, AGENT_ERROR_UPSTREAM, '内部错误，请稍后重试。')
      })
      return
    }
    sendAgentError(res, 404, 'AGENT_NOT_FOUND', '未知的模型网关路径。')
  }
}

export const name = 'dsh-tavern'
export const inject = ['webServer', 'llm', 'agentDefaultModel']

export function apply(ctx) {
  const secret = readTrustedSecret()
  if (secret) {
    console.info(
      `${LOG_PREFIX} 已加载可信登录 secret（${secret.length} 字符）：API → 127.0.0.1:${API_PORT}，静态 → 127.0.0.1:${STATIC_PORT}`,
    )
  } else {
    console.warn(
      `${LOG_PREFIX} 未读到 DSH_TRUSTED_SECRET（app/.env 或 DSH_TAVERN_TRUSTED_SECRET）：/tavern/api/** 将不带可信头转发，应用侧会按未登录处理。`,
    )
  }

  // 模型网关 secret：没配置时网关所有请求 401（不静默放行），这里只提示一次。
  const agentSecret = readAgentSecret()
  if (agentSecret) {
    console.info(`${LOG_PREFIX} 模型网关已启用：/tavern-agent/v1/**（Bearer 鉴权，secret ${agentSecret.length} 字符）`)
  } else {
    console.warn(
      `${LOG_PREFIX} 未读到 DSH_TAVERN_AGENT_SECRET（app/.env 或环境变量，回退 DSH_TRUSTED_SECRET）：/tavern-agent/v1/** 将一律返回 401。`,
    )
  }

  const handler = createHandler(ctx, secret)
  const agentHandler = createAgentHandler(ctx, agentSecret)
  ctx.effect(() => {
    const disposers = [
      ctx.webServer.register({
        kind: 'prefix',
        path: ROUTE_PATH,
        handler,
      }),
      // 旧 /story 别名：同一 handler 内先做 302 或前缀改写。
      ctx.webServer.register({
        kind: 'prefix',
        path: LEGACY_ROUTE_PATH,
        handler,
      }),
      // DSH 模型网关：给应用 worker 用的服务端到服务端接口，鉴权走 Bearer secret。
      ctx.webServer.register({
        kind: 'prefix',
        path: AGENT_ROUTE_PATH,
        handler: agentHandler,
      }),
    ]
    return () => {
      for (const dispose of disposers) {
        if (typeof dispose === 'function') dispose()
      }
    }
  }, 'dsh-tavern: /tavern reverse proxy (+ /story alias, + /tavern-agent gateway)')
}
