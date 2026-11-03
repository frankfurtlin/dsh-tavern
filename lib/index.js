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
 */
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
const ENV_FILE = join(fileURLToPath(new URL('..', import.meta.url)), 'app', '.env')
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
  return readEnvValue(ENV_FILE, 'DSH_TRUSTED_SECRET')
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

export const name = 'dsh-tavern'
export const inject = ['webServer']

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

  const handler = createHandler(ctx, secret)
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
    ]
    return () => {
      for (const dispose of disposers) {
        if (typeof dispose === 'function') dispose()
      }
    }
  }, 'dsh-tavern: /tavern reverse proxy (+ /story alias)')
}
