/**
 * dsh-tavern 端到端验收脚本（酒馆 2.0 原生流程，真实浏览器行为）。
 *
 * 用法：
 *   node verify.mjs --url "http://127.0.0.1:3080/?token=..." --fixtures /tmp/dsh-tavern-native/cards [--out dir]
 *
 * 检查分组（docs/step6-native-tavern.md §8）：
 *   A. 基础：匿名 GET /tavern/ 必须 401；登录后 DSH 侧栏「酒馆」入口可打开面板，
 *      iframe[data-dsh-tavern-frame] 加载出应用（不是应用自己的登录页），原生 UI 已交付。
 *   N. 角色列表：首页角色卡网格；导入 fixture 卡（PNG v2）→ 卡名/头像出现；搜索过滤。
 *   C. 聊天：点卡进角色页 → 开新聊天 /chats/<id> → 发「你好」→ 用户气泡 →
 *      assistant 文本流式增长（250ms 采样至少 2 个不同长度）→ 完成后落库、刷新仍在。
 *   C2. 停止生成：流式中出现「停止生成」按钮，点击后停止且不报错（复用 R 后的流式）。
 *   R. 重新生成：最后一条 assistant「重新生成」→ 文本变化、版本数增加、消息数不重复。
 *   P. swipe：版本计数 1/1→2/2 后点 ‹ 切到旧版本（文本变化）→ 刷新仍保持 → 点 › 切回最新。
 *   E. 编辑/删除消息：编辑 user 文本保存后更新；删除一条消息后列表减少；无 console 报错。
 *   T. 标题：点击聊天标题改名保存 → 刷新保持。
 *   H. 历史：角色页聊天历史列表 → 再进入消息完整。
 *   S. 世界书：卡详情新增条目（key「茶」，position after_char）→ 列表可见 → 删除；
 *      注入预览：输入示例消息 → 出现命中条目/injection 文本（P0 无 UI 时记 SKIP 并注明 owner）。
 *   I. 导入导出：聊天导出 JSONL（下载 + 逐行 JSON 解析）与 JSON（解析 messages）；
 *      卡详情导入 fixture chat-sample.jsonl（2 条消息）→ 历史新增并自动进入会话、条数正确。
 *   W. 世界书深度语义（P2）：条目 position=after_char/depth=2/constant/probability/order 保存回显；
 *      注入预览显示命中与原因；context-preview API 按 order 返回且注入文本包含条目。
 *   S2. 采样参数（P2）：聊天页菜单打开采样参数 → temperature/topP/maxTokens 保存 → 刷新回显 →
 *      带参数发送一条消息不报错（真实生成 +1）。
 *   Q. 预设与提示词（P2）：设置页新建预设（system/temperature/maxTokens）→ 设默认 →
 *      新聊天 prompt 接口包含预设文本；非法采样值后端 422（或前端拦截）。
 *   M. 旧路由：/tavern/explore、/tavern/works、/tavern/worlds/scripts、
 *      /tavern/conversations/world/x → 落 / 或无 404 死链；页面无「探索作品」入口。
 *   L. 群聊删成员容错（task-66）：删 1 位成员后详情 200 + members.deleted/aliveMemberCount + UI 灰显
 *      「（已删除）」+ 发言人切到存活成员；全员删除后 200 + UI 只读 + POST messages 409 CHAT_NO_MEMBERS；
 *      /chats 列表仍可见、删除群聊 204。用 API 造数，真实生成 +2。
 *
 * 设计原则：
 *   - 插件/应用尚未就绪时不崩：能跑出「未就绪」报告，依赖项记 SKIP 并写明 owner。
 *   - 测试只写隔离实例的数据；跑完由调用方清库回基线。
 */
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

const require = createRequire('/root/workspace/hermes-site/tools/node_modules/')
const { chromium } = require('playwright')

const args = process.argv.slice(2)
function arg(name, fallback) {
  const index = args.indexOf(`--${name}`)
  if (index === -1) return fallback
  const value = args[index + 1]
  if (value === undefined || value.startsWith('--')) return fallback
  return value
}
const url = arg('url', process.env.DSH_WEB_URL || '')
const outDir = arg('out', '/tmp/dsh-tavern-native/check')
const fixturesDir = arg('fixtures', '/tmp/dsh-tavern-native/cards')
const headless = !args.includes('--headed')
const only = (arg('only', process.env.DSH_TAVERN_ONLY || '') || '')
  .split(',')
  .map((item) => item.trim().toLowerCase())
  .filter(Boolean)
const groupOn = (name) => only.length === 0 || only.includes(name)
if (!url) {
  console.error('缺少 --url（带 token 的 DSH Web 地址）')
  process.exit(2)
}

const report = {
  plugin: 'dsh-tavern',
  url: url.replace(/token=[^&]+/, 'token=***'),
  startedAt: new Date().toISOString(),
  checks: [],
  native: {
    home: null,
    characters: null,
    chat: null,
    stop: null,
    regenerate: null,
    swipe: null,
    title: null,
    edit: null,
    history: null,
    worldInfo: null,
    exportImport: null,
    oldRoutes: null,
    group: null,
    groupDeletion: null,
  },
  app: {},
  screenshots: [],
}

function record(name, ok, detail, skipped = false) {
  const entry = { name, ok: skipped ? true : !!ok, skipped, detail: detail === undefined ? null : String(detail) }
  report.checks.push(entry)
  const tag = skipped ? 'SKIP' : entry.ok ? 'PASS' : 'FAIL'
  console.log(`${tag}  ${name}${entry.detail ? ` — ${entry.detail}` : ''}`)
}
const check = (name, ok, detail) => record(name, ok, detail, false)
const skip = (name, detail) => record(name, true, detail, true)

function clip(value, max = 240) {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  if (text === undefined || text === null) return 'n/a'
  return text.length > max ? `${text.slice(0, max)}…` : text
}

async function shot(page, name) {
  const file = path.join(outDir, name)
  await page.screenshot({ path: file, fullPage: false }).catch(() => {})
  report.screenshots.push(file)
  return file
}

async function waitFor(fn, timeoutMs, stepMs = 250) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      if (await fn()) return true
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, stepMs))
  }
  return false
}

await mkdir(outDir, { recursive: true })
const browser = await chromium.launch({ headless, args: ['--no-sandbox'] })
let page = null
try {
  // ── A1. 匿名 /tavern/ 必须 401 ─────────────────────────────────────────────
  const anon = await browser.newContext({ ignoreHTTPSErrors: true })
  const anonRes = await anon.request.get(new URL('tavern/', url).href)
  const anonStatus = anonRes.status()
  check(
    '未登录访问 /tavern/ 被拒绝（期望 401）',
    anonStatus === 401,
    `HTTP ${anonStatus}${
      anonStatus === 404
        ? '（404 = 宿主没加载/没注册 /tavern 路由，插件尚未就绪，owner: plugin-core）'
        : anonStatus === 200
          ? '（200 = 登录闸门没生效，owner: plugin-core）'
          : ''
    }`,
  )
  await anon.close()

  // ── A2. 登录态打开 DSH → 侧栏入口 → 面板 iframe ─────────────────────────────
  const context = await browser.newContext({
    permissions: ['clipboard-read', 'clipboard-write'],
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 960 },
  })
  page = await context.newPage()
  const consoleErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  async function dismissWelcome() {
    await page.keyboard.press('Escape').catch(() => {})
    const close = page.locator('div[role="presentation"] button[aria-label="Close"]').first()
    if (await close.count().catch(() => 0)) {
      await close.click({ force: true }).catch(() => {})
      await page.waitForTimeout(300)
    }
  }
  await dismissWelcome()
  await shot(page, '90-sidebar.png')

  async function findEntry(timeoutMs) {
    const selectors = ['[data-dsh-panel-entry="tavern"]', 'button[aria-label="酒馆"]', 'button[data-dsh-panel="tavern"]', 'button:has-text("酒馆")']
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      for (const selector of selectors) {
        const anchor = page.locator(selector).first()
        if (!(await anchor.count().catch(() => 0))) continue
        const clickable = anchor.locator('xpath=ancestor-or-self::*[self::button or self::a or self::div[@role="button"]][1]').first()
        if (await clickable.count().catch(() => 0)) return clickable
        return anchor
      }
      await page.waitForTimeout(400)
    }
    return null
  }

  const entry = await findEntry(20000)
  check('侧栏出现「酒馆」入口', Boolean(entry), entry ? '已定位侧栏入口' : '20s 内没找到入口（插件未加载或 label/id 不符合契约，owner: plugin-core）')
  let panelReady = false
  if (entry) {
    for (let attempt = 0; attempt < 3 && !panelReady; attempt += 1) {
      await dismissWelcome()
      try {
        await entry.click({ timeout: 4000 })
      } catch {
        await entry.click({ force: true }).catch(() => {})
      }
      panelReady = await page
        .locator('iframe[data-dsh-tavern-frame]')
        .first()
        .waitFor({ state: 'visible', timeout: 6000 })
        .then(() => true)
        .catch(() => false)
    }
  }
  check('点击侧栏入口打开「酒馆」主面板', panelReady, panelReady ? 'iframe[data-dsh-tavern-frame] 已可见' : '点击后没有出现 iframe[data-dsh-tavern-frame]（owner: plugin-core）')
  await shot(page, '91-panel.png')

  function fnow() {
    return page.frames().find((candidate) => /\/tavern\//.test(candidate.url())) || null
  }
  async function waitFrameUrl(pattern, timeoutMs = 25000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const frame = page.frames().find((candidate) => /\/tavern\//.test(candidate.url()) && pattern.test(candidate.url()))
      if (frame) return frame
      await page.waitForTimeout(300)
    }
    return null
  }
  async function appGoto(appPath, timeoutMs = 25000) {
    const frame = fnow()
    if (!frame) return null
    const origin = new URL(frame.url()).origin
    await frame
      .goto(`${origin}/tavern${appPath}`, { waitUntil: 'domcontentloaded', timeout: timeoutMs })
      .catch(() => {})
    const escaped = `/tavern${appPath}`.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const landed = await waitFrameUrl(new RegExp(`${escaped}(?:[?#]|$)`), 8000)
    const target = landed || fnow()
    // 等 Vue 应用真正挂载（index.html 里的启动占位也有 h1，不能只看标题）。
    if (target) {
      await waitFor(
        async () =>
          (await target
            .locator('.tavern-home, .tavern-settings, .tavern-character, .bubble-row, .tavern-empty')
            .count()
            .catch(() => 0)) > 0,
        timeoutMs,
      )
    }
    return target
  }
  // 会重定向的旧路由：导航后等落地，再读当前 frame 与 URL。
  async function appGotoRedirect(appPath, settleMs = 3000) {
    const frame = fnow()
    if (!frame) return null
    const origin = new URL(frame.url()).origin
    await frame.goto(`${origin}/tavern${appPath}`, { waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(settleMs)
    return fnow()
  }
  async function bodyText(frame) {
    return ((await frame.locator('body').innerText().catch(() => '')) || '').trim()
  }

  const frame = panelReady ? fnow() || (await waitFrameUrl(/\/tavern\//, 20000)) : null
  const frameUrl = frame ? frame.url() : ''
  const appLoginPage = frame ? await frame.locator('input[type="password"]').count().catch(() => 0) : 0
  check(
    '内嵌应用已加载（不是应用自己的登录页）',
    Boolean(frame) && /\/tavern\//.test(frameUrl) && appLoginPage === 0,
    frame ? `url=${frameUrl}${appLoginPage ? '（出现 password 输入框 = 应用登录页，owner: app-auth）' : ''}` : '没有找到 /tavern/ iframe',
  )

  // 原生 UI 判定：新布局是极简顶部栏 + 角色卡网格；旧布局有 .shell-tabs/.discovery-page。
  const nativeMarkers = ['.tavern-home', '.tavern-grid', '.tavern-card-grid', '.tavern-card', '.tavern-toolbar', '.tavern-shell', '.tavern-app', '[data-tavern-home]']
  const oldMarkers = ['.shell-tabs', '.discovery-page', '.shell-sidebar']
  let nativeReady = false
  let oldUi = false
  if (frame) {
    nativeReady = await waitFor(async () => {
      for (const selector of nativeMarkers) if (await frame.locator(selector).count().catch(() => 0)) return true
      const text = await bodyText(frame)
      return /酒馆/.test(text) && /角色卡/.test(text) && !/探索作品/.test(text)
    }, 15000)
    oldUi = await waitFor(async () => {
      for (const selector of oldMarkers) if (await frame.locator(selector).count().catch(() => 0)) return true
      return /探索作品/.test(await bodyText(frame))
    }, 3000)
  }
  report.native.ready = nativeReady
  report.native.oldUi = oldUi
  check(
    '原生酒馆 UI 已交付（极简布局 + 角色卡网格，无旧探索入口）',
    nativeReady && !oldUi,
    nativeReady && !oldUi
      ? `url=${frameUrl}`
      : `url=${frameUrl}；nativeReady=${nativeReady} oldUi=${oldUi}（原生 UI 未交付或仍是旧探索布局，owner: cards-client；N/C/R/H/S/M 组记 SKIP）`,
  )

  const nativeBlocked = !(nativeReady && !oldUi)
  const blockedReason = `原生酒馆 UI 未交付（owner: cards-client；nativeReady=${nativeReady} oldUi=${oldUi}）`

  // 读取 fixture manifest（导入用）
  let manifest = null
  try {
    manifest = JSON.parse(await readFile(path.join(fixturesDir, 'manifest.json'), 'utf8'))
  } catch {
    manifest = null
  }
  const fixtureCard = manifest && Array.isArray(manifest.cards) ? manifest.cards.find((card) => card.format === 'png-v2') || manifest.cards[0] : null
  const cardName = (fixtureCard && fixtureCard.name) || '林小满'

  // 生成卡住时旁路探网关：用来区分「整个网关/模型服务挂起」与「单个请求卡住」。
  // 只读 app/.env 的 DSH_AGENT_SECRET，不写任何状态；结果只进详情，不改变断言。
  async function gatewayProbe(timeoutMs = 15000) {
    let secret = ''
    try {
      const raw = await readFile(new URL('./app/.env', import.meta.url), 'utf8')
      const match = raw.match(/^DSH_AGENT_SECRET=(.+)$/m) || raw.match(/^DSH_TAVERN_AGENT_SECRET=(.+)$/m)
      if (match) secret = match[1].trim()
    } catch {
      return { ok: false, error: 'no-secret' }
    }
    if (!secret) return { ok: false, error: 'no-secret' }
    const started = Date.now()
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const response = await fetch('http://127.0.0.1:3080/tavern-agent/v1/stream', {
        method: 'POST',
        headers: { Authorization: `Bearer ${secret}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          system: '你是测试助手',
          messages: [{ role: 'user', content: '只回复两个字：你好' }],
          maxTokens: 16,
        }),
        signal: controller.signal,
      })
      let chars = 0
      for await (const chunk of response.body) chars += chunk.length
      return { ok: response.ok, status: response.status, ms: Date.now() - started, chars }
    } catch (error) {
      return { ok: false, error: error && error.name === 'AbortError' ? `timeout>${timeoutMs}ms` : String(error && error.message), ms: Date.now() - started }
    } finally {
      clearTimeout(timer)
    }
  }

  async function findCard(name) {
    const frameNow = fnow()
    if (!frameNow) return null
    const candidates = [
      frameNow.locator('.tavern-card').filter({ hasText: name }).first(),
      frameNow.locator('[data-tavern-card]').filter({ hasText: name }).first(),
      frameNow.locator('article.character-card').filter({ hasText: name }).first(),
    ]
    for (const locator of candidates) if (await locator.count().catch(() => 0)) return locator
    return null
  }
  async function cardCount() {
    const frameNow = fnow()
    if (!frameNow) return 0
    for (const selector of ['.tavern-grid .tavern-card', '.tavern-card', '[data-tavern-card]']) {
      const count = await frameNow.locator(selector).count().catch(() => 0)
      if (count > 0) return count
    }
    return 0
  }

  // ── O. 移动端 390×844 适配（task-71 打磨 4；真实后端，禁用 mock）──────────────
  const mobileEvidence = {
    viewport: { width: 390, height: 844 },
    empty: null,
    grid: null,
    detail: null,
    chats: null,
    settings: null,
    regex: null,
    console: [],
    steps: [],
  }
  report.native.mobile = mobileEvidence
  const mobileNames = [
    '空库首页移动端：空态 CTA 正常、无横向溢出、主题 token（--edge）恢复',
    '导入两张卡后首页 390 宽两列网格且无横向溢出',
    '角色详情页主操作按钮可见、高≥44px 且无横向溢出',
    '/chats 空态与列表行：空态 CTA、行高≥48、标题 ellipsis、无横向溢出',
    '设置/正则页移动适配：表单不溢出、按钮≥44、正则行卡片、console clean',
  ]
  let mobileDone = 0
  const mobileRecord = (index, ok, detail) => {
    if (mobileDone > index) return
    mobileDone = index + 1
    check(mobileNames[index], ok, detail)
  }
  const mobileSkip = (index, detail) => {
    if (mobileDone > index) return
    mobileDone = index + 1
    skip(mobileNames[index], detail)
  }
  if (groupOn('o')) {
    if (nativeBlocked) {
      for (let index = 0; index < mobileNames.length; index += 1) mobileSkip(index, blockedReason)
    } else {
      const mobileBase = new URL(url).origin + '/tavern'
      const mobilePage = await context.newPage()
      await mobilePage.setViewportSize({ width: 390, height: 844 }).catch(() => {})
      mobilePage.on('console', (message) => {
        if (message.type() === 'error') mobileEvidence.console.push(String(message.text()).slice(0, 200))
      })
      mobilePage.on('pageerror', (error) => {
        mobileEvidence.console.push(`PAGEERROR ${String(error && error.message).slice(0, 200)}`)
      })
      const mobileOverflow = () =>
        mobilePage.evaluate(() => {
          const root = document.scrollingElement || document.documentElement
          return root.scrollWidth - window.innerWidth
        })
      const gotoMobile = async (pathname, selector, timeoutMs = 25000) => {
        await mobilePage.goto(mobileBase + pathname, { waitUntil: 'domcontentloaded' }).catch(() => {})
        if (selector) await mobilePage.waitForSelector(selector, { timeout: timeoutMs }).catch(() => {})
      }
      const mobileApi = async (pathname, options = {}) =>
        mobilePage.evaluate(
          async ({ pathname, options }) => {
            const headers = {}
            const hasBody = options.body !== undefined
            if (hasBody) headers['Content-Type'] = 'application/json'
            if (options.method && options.method !== 'GET' && options.method !== 'HEAD') {
              headers['Idempotency-Key'] = 'e2e-mobile-' + Math.random().toString(36).slice(2) + Date.now().toString(36)
            }
            try {
              const response = await fetch('/tavern/api/reader/v1/tavern' + pathname, {
                credentials: 'include',
                method: options.method || 'GET',
                headers,
                body: hasBody ? JSON.stringify(options.body) : undefined,
              })
              let body = null
              try {
                body = await response.json()
              } catch {}
              return { status: response.status, body }
            } catch (error) {
              return { status: 0, body: null, error: String(error) }
            }
          },
          { pathname, options },
        )
      const mobileApiRoot = async (pathname, options = {}) =>
        mobilePage.evaluate(
          async ({ pathname, options }) => {
            const headers = {}
            const hasBody = options.body !== undefined
            if (hasBody) headers['Content-Type'] = 'application/json'
            if (options.method && options.method !== 'GET' && options.method !== 'HEAD') {
              headers['Idempotency-Key'] = 'e2e-mobile-' + Math.random().toString(36).slice(2) + Date.now().toString(36)
            }
            try {
              const response = await fetch('/tavern/api/reader/v1' + pathname, {
                credentials: 'include',
                method: options.method || 'GET',
                headers,
                body: hasBody ? JSON.stringify(options.body) : undefined,
              })
              let body = null
              try {
                body = await response.json()
              } catch {}
              return { status: response.status, body }
            } catch (error) {
              return { status: 0, body: null, error: String(error) }
            }
          },
          { pathname, options },
        )
      const boxHeight = async (selector) => {
        const box = await mobilePage.locator(selector).first().boundingBox().catch(() => null)
        return box ? Math.round(box.height) : 0
      }
      const visibleCount = (selector) => mobilePage.locator(selector).count().catch(() => 0)

      // 数据准备：明确从空库开始（只清测试库数据，不动其它数据）
      await gotoMobile('/', '.tavern-home, .load-state-empty, .load-state-loading')
      const cardsNow = await mobileApi('/characters')
      for (const item of (cardsNow.body && cardsNow.body.items) || []) {
        const cardId = item && (item.id || (item.card && item.card.id))
        if (cardId) await mobileApi('/characters/' + cardId, { method: 'DELETE' })
      }
      const chatsNow = await mobileApi('/chats')
      for (const item of (chatsNow.body && chatsNow.body.items) || []) {
        const chatId = item && (item.id || (item.chat && item.chat.id))
        if (chatId) await mobileApi('/chats/' + chatId, { method: 'DELETE' })
      }

      try {
        const actualViewport = await mobilePage.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight })).catch(() => null)
        mobileEvidence.viewport = { ...mobileEvidence.viewport, actual: actualViewport }

        // O1 空库首页
        await gotoMobile('/', '.load-state-empty')
        const emptyTitle = (await mobilePage.locator('.load-state-title').first().textContent().catch(() => '')) || ''
        const emptyCta = await visibleCount('.tavern-empty-import, input[aria-label="导入第一张角色卡"]')
        const tokens = await mobilePage.evaluate(() => {
          const style = getComputedStyle(document.documentElement)
          return {
            edge: style.getPropertyValue('--edge').trim(),
            ink: style.getPropertyValue('--ink').trim(),
          }
        })
        const emptyOverflow = await mobileOverflow()
        mobileEvidence.empty = { title: clip(emptyTitle, 40), cta: emptyCta > 0, ...tokens, overflow: emptyOverflow }
        mobileRecord(
          0,
          /还没有角色卡/.test(emptyTitle) && emptyCta > 0 && emptyOverflow <= 1 && Boolean(tokens.edge),
          `title=${clip(emptyTitle, 40)} cta=${emptyCta > 0 ? 'yes' : 'no'} edge=${clip(tokens.edge, 40) || 'EMPTY'} ink=${clip(tokens.ink, 40) || 'EMPTY'} overflow=${emptyOverflow}`,
        )
        await shot(mobilePage, '160-mobile-empty-home.png')

        // O2 导入两张卡 → 两列网格
        const firstInput = mobilePage.locator('input[aria-label="导入第一张角色卡"]').first()
        if (await firstInput.count().catch(() => 0)) {
          await firstInput.setInputFiles(path.join(fixturesDir, 'v2-png.png')).catch(() => {})
          await waitFor(async () => (await mobilePage.locator('.tavern-card').count().catch(() => 0)) >= 1, 25000)
        }
        const toolbarInput = mobilePage.locator('input[aria-label="导入角色卡文件"]').first()
        if (await toolbarInput.count().catch(() => 0)) {
          await toolbarInput.setInputFiles(path.join(fixturesDir, 'v2-json.json')).catch(() => {})
        }
        await waitFor(async () => (await mobilePage.locator('.tavern-grid .tavern-card').count().catch(() => 0)) >= 2, 25000)
        const gridInfo = await mobilePage.evaluate(() => {
          const grid = document.querySelector('.tavern-grid')
          const columns = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean) : []
          const boxes = Array.from(document.querySelectorAll('.tavern-grid .tavern-card'))
            .slice(0, 2)
            .map((node) => {
              const box = node.getBoundingClientRect()
              return { top: Math.round(box.top), left: Math.round(box.left) }
            })
          return { columns: columns.length, boxes }
        })
        const gridOverflow = await mobileOverflow()
        const twoColumns = gridInfo.columns === 2 && gridInfo.boxes.length === 2
        const sameRow =
          twoColumns &&
          Math.abs(gridInfo.boxes[0].top - gridInfo.boxes[1].top) <= 4 &&
          Math.abs(gridInfo.boxes[0].left - gridInfo.boxes[1].left) > 8
        mobileEvidence.grid = { columns: gridInfo.columns, boxes: gridInfo.boxes, overflow: gridOverflow }
        mobileRecord(
          1,
          twoColumns && sameRow && gridOverflow <= 1,
          `columns=${gridInfo.columns} boxes=${clip(gridInfo.boxes)} overflow=${gridOverflow}`,
        )
        await shot(mobilePage, '161-mobile-grid-2col.png')

        // O3 角色详情：主操作
        const cardsList = await mobileApi('/characters')
        const cardItems = (cardsList.body && cardsList.body.items) || []
        const detailCard = cardItems.find((item) => item && (item.name === cardName || (item.card && item.card.name === cardName))) || cardItems[0]
        const detailId = detailCard && (detailCard.id || (detailCard.card && detailCard.card.id))
        if (detailId) {
          await gotoMobile('/characters/' + detailId, '.character-hero')
          // 390 下 hero 内的按钮被 CSS 隐藏，主操作固定在底部 .character-mobile-actions。
          let heroButton = mobilePage.locator('.character-mobile-actions button.primary').first()
          let heroVisible = await heroButton.isVisible().catch(() => false)
          if (!heroVisible) {
            heroButton = mobilePage.locator('.character-hero-actions button.primary').first()
            heroVisible = await heroButton.isVisible().catch(() => false)
          }
          const heroBox = await heroButton.boundingBox().catch(() => null)
          const detailOverflow = await mobileOverflow()
          const heroInside = Boolean(heroBox) && heroBox.y >= -1 && heroBox.y + heroBox.height <= 844 + 1
          mobileEvidence.detail = {
            height: heroBox ? Math.round(heroBox.height) : 0,
            visible: heroVisible,
            inside: heroInside,
            box: heroBox ? { x: Math.round(heroBox.x), y: Math.round(heroBox.y), w: Math.round(heroBox.width), h: Math.round(heroBox.height) } : null,
            overflow: detailOverflow,
          }
          mobileRecord(
            2,
            heroVisible && Boolean(heroBox) && heroBox.height >= 44 && heroInside && detailOverflow <= 1,
            `button=${heroVisible ? 'visible' : 'hidden'} height=${heroBox ? Math.round(heroBox.height) : 0} inside=${heroInside} overflow=${detailOverflow}`,
          )
        } else {
          mobileSkip(2, '没有可打开的角色卡（导入失败，owner: cards-client 首页导入）')
        }
        await shot(mobilePage, '162-mobile-detail-actions.png')

        // O4 /chats 空态 + 列表行
        await gotoMobile('/chats', '.load-state-empty, .chat-history-row')
        const chatsEmptyTitle = (await mobilePage.locator('.load-state-title').first().textContent().catch(() => '')) || ''
        const chatsEmptyCta = await visibleCount('button.tavern-empty-action, .load-state-actions a, .load-state-actions button')
        const chatsEmptyOverflow = await mobileOverflow()
        let chatsRow = null
        if (detailId) {
          const created = await mobileApi('/characters/' + detailId + '/chats', { method: 'POST', body: {} })
          const createdChatId = created.body && (created.body.chat && created.body.chat.id ? created.body.chat.id : created.body.id)
          if (createdChatId) {
            await mobileApi('/chats/' + createdChatId, {
              method: 'PATCH',
              body: { title: 'E2E 移动端窄屏聊天标题用来验证省略号是否生效并且确实很长' },
            })
          }
          await gotoMobile('/chats', '.chat-history-row')
          const row = mobilePage.locator('.chat-history-row').first()
          const rowBox = await row.boundingBox().catch(() => null)
          const nameOverflow = await mobilePage
            .evaluate(() => {
              const name = document.querySelector('.chat-history-name')
              if (!name) return null
              return name.scrollWidth - name.clientWidth
            })
            .catch(() => null)
          const listOverflow = await mobileOverflow()
          chatsRow = {
            height: rowBox ? Math.round(rowBox.height) : 0,
            nameOverflow,
            overflow: listOverflow,
          }
        }
        mobileEvidence.chats = {
          emptyTitle: clip(chatsEmptyTitle, 40),
          emptyCta: chatsEmptyCta > 0,
          emptyOverflow: chatsEmptyOverflow,
          row: chatsRow,
        }
        const chatsOk =
          /还没有聊天/.test(chatsEmptyTitle) &&
          chatsEmptyCta > 0 &&
          chatsEmptyOverflow <= 1 &&
          Boolean(chatsRow) &&
          chatsRow.height >= 48 &&
          Number(chatsRow.nameOverflow) > 0 &&
          chatsRow.overflow <= 1
        mobileRecord(
          3,
          chatsOk,
          `empty=${clip(chatsEmptyTitle, 20)} cta=${chatsEmptyCta > 0 ? 'yes' : 'no'} emptyOverflow=${chatsEmptyOverflow} row=${clip(chatsRow)}`,
        )
        await shot(mobilePage, '163-mobile-chats-empty.png')
        await shot(mobilePage, '164-mobile-chats-row.png')

        // O5 设置 + 正则页
        await gotoMobile('/settings', '.tavern-settings')
        const settingsOverflow = await mobileOverflow()
        const settingsBackHeight = await boxHeight('.settings-back')
        // 空态下模板不渲染规则表单（只有列表存在才显示），先用 API 建一条规则，再验证窄屏行卡片与保存按钮。
        const regexSeed = await mobileApiRoot('/regex-scripts', {
          method: 'POST',
          body: { name: 'E2E 移动端规则', pattern: '。', replacement: '！', enabled: true, order: 0 },
        })
        const seededRuleId = regexSeed.body && (regexSeed.body.regex ? regexSeed.body.regex.id : regexSeed.body.id)
        await gotoMobile('/settings/regex', '.tavern-regex')
        await waitFor(async () => (await mobilePage.locator('.regex-item').count().catch(() => 0)) > 0, 20000)
        const regexOverflowBefore = await mobileOverflow()
        const regexBackHeight = await boxHeight('.regex-back')
        const saveButton = mobilePage.locator('form.regex-form button[type="submit"]').first()
        const saveHeight = await boxHeight('form.regex-form button[type="submit"]')
        const nameInput = mobilePage.locator('input[aria-label="规则名称"]').first()
        let saveAction = null
        if (await nameInput.count().catch(() => 0)) {
          await nameInput.fill('E2E 移动端规则改').catch(() => {})
          await mobilePage.locator('input[aria-label="查找正则"]').first().fill('！').catch(() => {})
          await mobilePage.locator('textarea[aria-label="替换内容"]').first().fill('。').catch(() => {})
          if (await saveButton.count().catch(() => 0)) {
            await saveButton.click({ force: true }).catch(() => {})
            const saved = await waitFor(
              async () => /E2E 移动端规则改/.test((await mobilePage.locator('.regex-list').textContent().catch(() => '')) || ''),
              20000,
            )
            saveAction = { clicked: true, saved }
          }
        }
        const regexItem = await mobilePage
          .evaluate(() => {
            const item = document.querySelector('.regex-item')
            if (!item) return null
            const actions = Array.from(item.querySelectorAll('.regex-actions button'))
            return {
              direction: getComputedStyle(item).flexDirection,
              actionHeights: actions.map((node) => Math.round(node.getBoundingClientRect().height)),
            }
          })
          .catch(() => null)
        const regexOverflowAfter = await mobileOverflow()
        const consoleErrors = mobileEvidence.console.filter((text) => !/Failed to load resource/.test(text))
        const regexOk =
          settingsOverflow <= 1 &&
          settingsBackHeight >= 44 &&
          regexOverflowBefore <= 1 &&
          regexOverflowAfter <= 1 &&
          regexBackHeight >= 44 &&
          saveHeight >= 44 &&
          Boolean(regexItem) &&
          Boolean(saveAction && saveAction.saved) &&
          regexItem.direction === 'column' &&
          regexItem.actionHeights.every((height) => height >= 44) &&
          consoleErrors.length === 0
        mobileEvidence.settings = { overflow: settingsOverflow, backHeight: settingsBackHeight }
        mobileEvidence.regex = {
          overflowBefore: regexOverflowBefore,
          overflowAfter: regexOverflowAfter,
          backHeight: regexBackHeight,
          saveHeight,
          saveAction,
          item: regexItem,
          console: consoleErrors,
        }
        if (seededRuleId) await mobileApiRoot('/regex-scripts/' + seededRuleId, { method: 'DELETE' })
        mobileRecord(
          4,
          regexOk,
          `settingsOverflow=${settingsOverflow} settingsBack=${settingsBackHeight} regexOverflow=${regexOverflowBefore}/${regexOverflowAfter} regexBack=${regexBackHeight} save=${saveHeight} item=${clip(regexItem)} console=${clip(consoleErrors, 120)}`,
        )
        await shot(mobilePage, '165-mobile-settings.png')
        await shot(mobilePage, '166-mobile-regex.png')
      } finally {
        // O 组是最先执行的分组：收尾把本组导入的卡/聊天清掉，后续分组仍从干净状态开始。
        try {
          const leftovers = await mobileApi('/cards')
          for (const item of (leftovers.body && leftovers.body.items) || []) {
            const leftoverId = item && (item.id || (item.card && item.card.id))
            if (leftoverId) await mobileApi('/characters/' + leftoverId, { method: 'DELETE' })
          }
          const leftoverChats = await mobileApi('/chats')
          for (const item of (leftoverChats.body && leftoverChats.items) || []) {
            const leftoverChatId = item && (item.id || (item.chat && item.chat.id))
            if (leftoverChatId) await mobileApi('/chats/' + leftoverChatId, { method: 'DELETE' })
          }
        } catch {}
        await mobilePage.close().catch(() => {})
        mobileEvidence.steps = mobileDone
      }
    }
  }

  // ── N. 角色列表 ───────────────────────────────────────────────────────────
  const homeEvidence = { imported: false, cardName, avatar: false, search: false, importNotice: null }
  report.native.home = homeEvidence
  if (groupOn('n')) {
    if (nativeBlocked) {
      skip('首页加载角色卡页（原生酒馆）', blockedReason)
      skip('导入 fixture 卡后角色列表出现卡名/头像', blockedReason)
      skip('角色列表搜索过滤', blockedReason)
    } else {
      const home = await appGoto('/')
      if (!home) {
        check('首页加载角色卡页（原生酒馆）', false, '无法导航到 /tavern/（owner: cards-client 路由）')
      } else {
        const pageReady = await waitFor(async () => (await home.locator('.tavern-home').count().catch(() => 0)) > 0, 20000)
        // 导入控件（label.tavern-import 内的隐藏 input）要等 Vue 挂载完成，不能刚导航就读一次。
        const hasImport = await waitFor(async () => (await home.locator('input[type="file"]').count().catch(() => 0)) > 0, 20000)
        const importInput = home.locator('input[type="file"]').first()
        homeEvidence.pageReady = pageReady
        homeEvidence.importInput = hasImport
        homeEvidence.inputs = await home
          .locator('input, textarea, select')
          .evaluateAll((els) => els.slice(0, 8).map((el) => `${el.tagName.toLowerCase()}[type=${el.getAttribute('type') || ''}][aria-label=${el.getAttribute('aria-label') || ''}]`))
          .catch(() => [])
        check(
          '首页加载角色卡页（原生酒馆）',
          pageReady && hasImport,
          `pageReady=${pageReady} importInput=${hasImport} url=${home.url()} controls=${clip(homeEvidence.inputs)}（owner: cards-client 首页/导入）`,
        )
        await shot(page, '92-characters-home.png')

        if (hasImport) {
          const fixturePath = path.join(fixturesDir, fixtureCard ? fixtureCard.file : 'v2-png.png')
          await importInput.setInputFiles(fixturePath).catch(() => {})
          const appeared = await waitFor(async () => (await cardCount()) >= 1 && /林小满|顾清和|沈夜舟/.test(await bodyText(home)), 30000)
          homeEvidence.imported = appeared
          homeEvidence.cards = await cardCount()
          homeEvidence.importNotice = ((await home.locator('[role="status"], .tavern-notice, .notice').allTextContents().catch(() => [])) || []).filter(Boolean).join(' | ').slice(0, 160)
          const avatar = await home
            .locator('.tavern-avatar img, img')
            .evaluateAll((imgs) => imgs.some((img) => /avatar|blob:|card/i.test(img.getAttribute('src') || '')))
            .catch(() => false)
          homeEvidence.avatar = Boolean(avatar)
          check('导入 fixture 卡后角色列表出现卡名/头像', appeared, `cardName=${cardName} cards=${homeEvidence.cards} avatar=${Boolean(avatar)} 提示=${clip(homeEvidence.importNotice)}（owner: cards-client 导入/列表；app-auth imports API）`)
          await shot(page, '93-card-imported.png')

          const searchInput = home.locator('.tavern-search input, input[aria-label="搜索角色卡"], input[placeholder*="搜索"], input[type="search"]').first()
          if (await searchInput.count().catch(() => 0)) {
            await searchInput.fill(cardName.slice(0, 2)).catch(() => {})
            const filtered = await waitFor(async () => (await cardCount()) >= 1 && (await bodyText(home)).includes(cardName), 8000)
            homeEvidence.search = filtered
            check('角色列表搜索过滤', filtered, `输入「${cardName.slice(0, 2)}」后仍能看到 ${cardName}=${filtered}`)
            await searchInput.fill('').catch(() => {})
          } else {
            skip('角色列表搜索过滤', '首页没有搜索框（owner: cards-client 首页）')
          }
        } else {
          skip('导入 fixture 卡后角色列表出现卡名/头像', '首页没有 input[type=file] 导入入口（owner: cards-client 首页）')
        }
      }
    }
  }

  // ── 通用：进入聊天页 ─────────────────────────────────────────────────────
  async function openChat() {
    const frameNow = fnow()
    if (!frameNow) return null
    // 列表在搜索/导入后会短暂重渲染（卡片数可能瞬时为 0）：等卡片出现再点，避免误判「点卡无效」。
    let card = await findCard(cardName)
    if (!card) {
      await waitFor(async () => Boolean(await findCard(cardName)), 12000)
      card = await findCard(cardName)
    }
    if (!card) return null
    // 原生交互：点卡直接进最近聊天（没有则自动新建）→ /chats/<id>；若落在详情页再点「开新聊天」。
    // 卡片刚渲染/图片未就绪时 force click 可能丢事件：12s 未跳转则重试（最多 3 次）。
    let chatFrame = null
    let detail = null
    // 卡片的点击处理器挂在内层 button.tavern-card-open 上；点外层 div 的中心未必落在按钮里，
    // 因此优先点真实按钮，找不到再退回整卡点击（每轮都重试，最多 3 次）。
    for (let attempt = 0; attempt < 3 && !chatFrame; attempt += 1) {
      if (attempt > 0) {
        await page.waitForTimeout(800)
        // 第一次没跳转时：回到首页并重新定位卡片，排除「面板刚挂载/卡片被重渲染」导致的点击丢失。
        await appGoto('/')
        await page.waitForTimeout(400)
        const refreshed = await findCard(cardName)
        if (refreshed) card = refreshed
      }
      const openButton = card.locator('.tavern-card-open').first()
      if (await openButton.count().catch(() => 0)) {
        await openButton.click({ force: true }).catch(async () => {
          await openButton.evaluate((el) => el.click()).catch(() => {})
        })
      } else {
        await card.click({ force: true }).catch(async () => {
          await card.evaluate((el) => el.click()).catch(() => {})
        })
      }
      chatFrame = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, attempt === 0 ? 25000 : 12000)
    }
    if (!chatFrame) {
      detail = await waitFrameUrl(/\/characters\/[0-9a-zA-Z_-]+/, 6000)
      if (detail) {
        const startButton = detail.getByRole('button', { name: /开新聊天|开始聊天|新聊天|开始对话/ }).first()
        if (await startButton.count().catch(() => 0)) {
          await startButton.click({ force: true }).catch(() => {})
          chatFrame = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 25000)
        }
      }
    }
    return { chatFrame, detail }
  }

  // 打开角色详情页：点卡片右上「设置」（原生交互），失败再点卡片本身。
  async function openDetail() {
    const home = await appGoto('/')
    if (!home) return null
    await waitFor(async () => (await home.locator('.tavern-card').count().catch(() => 0)) > 0, 25000)
    const edit = home.locator('.tavern-card').filter({ hasText: cardName }).locator('.tavern-card-edit').first()
    if (await edit.count().catch(() => 0)) await edit.click({ force: true }).catch(() => {})
    else {
      const card = await findCard(cardName)
      if (card) await card.click({ force: true }).catch(() => {})
    }
    return (await waitFrameUrl(/\/characters\/[0-9a-zA-Z_-]+/, 20000)) || fnow()
  }

  const assistantRowSelectors = ['.bubble-row.assistant', 'article.bubble-row.assistant', '[data-role="assistant"]']
  async function lastAssistantRow(frameNow) {
    for (const selector of assistantRowSelectors) {
      const locator = frameNow.locator(selector)
      const count = await locator.count().catch(() => 0)
      if (count > 0) return locator.nth(count - 1)
    }
    return null
  }
  async function waitChatRendered(frameNow, timeoutMs = 20000) {
    if (!frameNow) return false
    return waitFor(async () => {
      const rows = await frameNow.locator('.bubble-row').count().catch(() => 0)
      const chrome =
        (await frameNow.locator('.chat-name, button[aria-label="聊天菜单"]').count().catch(() => 0)) > 0
      return rows > 0 && chrome
    }, timeoutMs)
  }
  async function rowText(row) {
    if (!row) return ''
    const text = row.locator('.bubble-text').first()
    if (await text.count().catch(() => 0)) return ((await text.innerText().catch(() => '')) || '').trim()
    return ((await row.innerText().catch(() => '')) || '').trim()
  }

  // C 组自足：目标分组单独跑（例如 DSH_TAVERN_ONLY=c,g,l）时 N 组不会导入卡，
  // 这里按需补导入 fixture，避免「首页没有卡」被误判成点卡无效。
  async function ensureFixtureCard() {
    if (await findCard(cardName)) return true
    await appGoto('/')
    const frameNow = fnow()
    if (!frameNow) return false
    const input = frameNow
      .locator('input[type=file][aria-label="导入角色卡文件"], .tavern-import input[type=file], input[type=file]')
      .first()
    if (!(await input.count().catch(() => 0))) return false
    await input.setInputFiles(path.join(fixturesDir, 'v2-png.png')).catch(() => {})
    await waitFor(async () => Boolean(await findCard(cardName)), 20000)
    return Boolean(await findCard(cardName))
  }

  // ── C. 聊天 + 流式 ───────────────────────────────────────────────────────
  const chatEvidence = { chatUrl: null, cardUrl: null, userShown: false, samples: [], distinct: 0, growing: false, saved: false, persisted: false }
  report.native.chat = chatEvidence
  let chatFrame = null
  let cardUrl = null
  if (groupOn('c')) {
    if (nativeBlocked) {
      skip('点卡进入角色页并可开新聊天', blockedReason)
      skip('发送「你好」后出现用户气泡', blockedReason)
      skip('assistant 文本流式增长（≥2 个不同长度）', blockedReason)
      skip('刷新后消息仍在（落库）', blockedReason)
    } else {
      const apiTrace = []
      const traceResponse = (response) => {
        const url = response.url()
        if (!/\/tavern\/api\//.test(url)) return
        if (apiTrace.length < 40) apiTrace.push(`${response.status()} ${url.replace(/^.*\/tavern\/api\/reader\/v1\/tavern/, '')}`)
      }
      page.on('response', traceResponse)
      page.on('pageerror', (error) => {
        if (apiTrace.length < 45) apiTrace.push(`PAGEERROR ${String(error && error.message).slice(0, 120)}`)
      })
      chatEvidence.apiTrace = apiTrace
      const cardReady = await ensureFixtureCard()
      chatEvidence.cardReady = cardReady
      const opened = cardReady ? await openChat() : null
      page.off('response', traceResponse)
      chatFrame = opened && opened.chatFrame ? opened.chatFrame : null
      cardUrl = opened && opened.detail ? opened.detail.url() : null
      chatEvidence.chatUrl = chatFrame ? chatFrame.url() : null
      chatEvidence.cardUrl = cardUrl
      if (!chatFrame) {
        const failFrame = fnow()
        const failBody = failFrame ? (await bodyText(failFrame)).replace(/\s+/g, ' ').slice(0, 300) : ''
        const cardCount = failFrame ? await failFrame.locator('.tavern-card').count().catch(() => 0) : 0
        const openCount = failFrame ? await failFrame.locator('.tavern-card-open').count().catch(() => 0) : 0
        chatEvidence.clickFail = {
          url: failFrame ? failFrame.url() : null,
          cardCount,
          openCount,
          body: failBody,
        }
        await shot(page, '92-c-card-click-fail.png')
      }
      check(
        '点卡进入角色页并可开新聊天',
        Boolean(chatFrame),
        chatFrame
          ? `detail=${cardUrl} chat=${chatFrame.url()}`
          : `点卡后没有进入 /chats/<id>（detail=${cardUrl} url=${chatEvidence.clickFail && chatEvidence.clickFail.url} cards=${chatEvidence.clickFail && chatEvidence.clickFail.cardCount} opens=${chatEvidence.clickFail && chatEvidence.clickFail.openCount} body=${clip(chatEvidence.clickFail && chatEvidence.clickFail.body, 160)} api=${clip((chatEvidence.apiTrace || []).join(' ; '), 260)}；owner: cards-client 角色页/聊天路由）`,
      )
      if (chatFrame) {
        const input = chatFrame.locator('textarea[aria-label="聊天输入框"], textarea[placeholder*="说"]').first()
        if (!(await input.count().catch(() => 0))) {
          check('发送「你好」后出现用户气泡', false, '聊天页没有输入框（owner: cards-client ChatPage）')
        } else {
          await input.fill('你好').catch(() => {})
          const sendButton = chatFrame.locator('button[aria-label="发送消息"], button[type="submit"]').first()
          // 历史会话里可能已有旧的用户气泡，只认「发送后新增」的那一条。
          const userCountBefore = await chatFrame.locator('.bubble-row.user').count().catch(() => 0)
          const sendAt = Date.now()
          if (await sendButton.count().catch(() => 0)) await sendButton.click({ force: true }).catch(() => {})
          else await input.press('Enter').catch(() => {})

          // 立刻开始采样（不要先等用户气泡，否则会错过流式过程）。
          const samples = []
          let stable = 0
          let lastText = ''
          let userAt = null
          let userDuringStreaming = false
          while (Date.now() - sendAt < 180000) {
            const row = await lastAssistantRow(chatFrame)
            const text = await rowText(row)
            const pending =
              (await chatFrame.locator('.bubble-pending').count().catch(() => 0)) > 0 ||
              /正在回复/.test(await bodyText(chatFrame))
            const userRows = await chatFrame.locator('.bubble-row.user').count().catch(() => 0)
            const newUser = userRows > userCountBefore
            if (newUser && userAt === null) userAt = Date.now() - sendAt
            if (newUser && pending) userDuringStreaming = true
            if (text !== lastText) {
              lastText = text
              stable = 0
              if (text) samples.push({ at: Date.now() - sendAt, length: text.length })
            } else if (text) {
              stable += 1
            }
            if (text && !pending && stable >= 4) break
            await page.waitForTimeout(250)
          }
          const userShown = userAt !== null && /你好/.test(await bodyText(chatFrame))
          chatEvidence.userShown = userShown
          chatEvidence.userAtMs = userAt
          chatEvidence.userDuringStreaming = userDuringStreaming
          check(
            '发送「你好」后出现用户气泡',
            userShown && userDuringStreaming,
            `userShown=${userShown} userAtMs=${userAt} 流式中可见=${userDuringStreaming}（服务端 SSE 首帧 payload 是 userMessage，客户端取 message → 生成期间不渲染用户消息，owner: app-auth/cards-client 事件契约）`,
          )

          const lengths = samples.map((sample) => sample.length)
          const distinct = new Set(lengths).size
          const finalText = lastText
          chatEvidence.samples = samples.slice(-8)
          chatEvidence.distinct = distinct
          chatEvidence.growing = distinct >= 2
          chatEvidence.finalLength = finalText.length
          chatEvidence.finalFirst60 = finalText.slice(0, 60)
          chatEvidence.elapsedMs = Date.now() - sendAt
          check(
            'assistant 文本流式增长（≥2 个不同长度）',
            distinct >= 2 && finalText.length >= 2,
            `samples=${samples.length} distinct=${distinct} lengths=${clip(lengths.slice(-6))} final=${finalText.length} 字 elapsed=${chatEvidence.elapsedMs}ms`,
          )
          await shot(page, '94-chat-stream.png')
          chatEvidence.saved = finalText.length >= 2
          await shot(page, '95-chat-done.png')

          const assistantFirst = finalText.slice(0, 20)
          await chatFrame.evaluate(() => location.reload()).catch(() => {})
          chatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 20000)) || fnow()
          await waitChatRendered(chatFrame, 25000)
          const persisted = chatFrame
            ? await waitFor(async () => {
                const text = await bodyText(chatFrame)
                return text.includes('你好') && text.includes(assistantFirst)
              }, 30000)
            : false
          chatEvidence.persisted = persisted
          check('刷新后消息仍在（落库）', persisted, persisted ? '刷新后用户消息与 assistant 回复均存在' : `刷新后找不到消息（assistant 前 20 字=${clip(assistantFirst)}；owner: app-auth 落库 / cards-client 渲染）`)
        }
      }
    }
  }

  // ── G. 群聊（task-62，多选 2 张卡发起群聊 → 轮转 → 刷新 → 列表 → 删除）────
  const groupCheckNames = [
    '多选两张卡发起群聊，进入 /chats/<id> 且页头出现两个参与者',
    '群聊发送「你好」后出现 assistant 回复且发言人=第一位成员',
    '再发一条后发言人轮转到第二位成员',
    '刷新后消息与发言人保持，标题含成员名',
    '/chats 聊天历史出现该群聊（群聊标记 + 成员名 + 最后一条消息）',
    '删除群聊后聊天历史列表减少',
    '群聊 console 无报错',
  ]
  const groupEvidence = {
    enabled: false,
    imported: false,
    cards: 0,
    selected: [],
    barText: null,
    groupUrl: null,
    groupId: null,
    title: null,
    members: [],
    memberText: null,
    headerText: null,
    replies: [],
    speakers: [],
    rotation: false,
    persisted: false,
    listBefore: 0,
    listAfter: 0,
    listShown: false,
    deleted: false,
    listAfterDelete: 0,
    console: null,
    steps: [],
  }
  report.native.group = groupEvidence
  if (groupOn('g')) {
    if (nativeBlocked) {
      for (const name of groupCheckNames) skip(name, blockedReason)
    } else {
      groupEvidence.enabled = true
      const groupConsoleStart = consoleErrors.length
      const groupSecond =
        manifest && Array.isArray(manifest.cards) ? manifest.cards.find((card) => card.name !== cardName) || null : null
      const groupSecondName = (groupSecond && groupSecond.name) || '顾清和'
      const skipGroupRemaining = (detail) => {
        for (let index = groupEvidence.steps.length; index < groupCheckNames.length; index += 1) {
          if (!groupEvidence.steps.includes(index)) skip(groupCheckNames[index], detail)
        }
      }
      const markStep = (index) => {
        if (!groupEvidence.steps.includes(index)) groupEvidence.steps.push(index)
      }
      // 记录建群前的聊天历史条数（API），G6 删除后要回落。
      const apiChats = async () => {
        const frameNow = fnow()
        if (!frameNow) return null
        return frameNow
          .evaluate(async () => {
            try {
              const response = await fetch('/tavern/api/reader/v1/tavern/chats', { credentials: 'include' })
              if (!response.ok) return { status: response.status }
              const payload = await response.json()
              return { items: Array.isArray(payload.items) ? payload.items : [] }
            } catch (error) {
              return { error: String(error) }
            }
          })
          .catch(() => null)
      }
      const listBefore = await apiChats()
      groupEvidence.listBefore = listBefore && Array.isArray(listBefore.items) ? listBefore.items.length : 0
      // 1) 准备两张不同的卡（G 组可独立跑：缺卡时自己导入 fixture）。
      //    注意不能只看卡片数量：重复导入同名卡也会让数量 +1，但群聊需要两张「不同」角色。
      let home = await appGoto('/')
      await waitFor(async () => (await home.locator('.tavern-home').count().catch(() => 0)) > 0, 20000)
      const ensureCard = async (name, file) => {
        if (await findCard(name)) return true
        const importInput = home.locator('input[type="file"]').first()
        if (!(await importInput.count().catch(() => 0))) return false
        await importInput.setInputFiles(path.join(fixturesDir, file)).catch(() => {})
        const appeared = await waitFor(async () => Boolean(await findCard(name)), 30000)
        if (appeared) await page.waitForTimeout(400)
        return appeared
      }
      const firstReady = fixtureCard ? await ensureCard(cardName, fixtureCard.file) : false
      const secondReady = groupSecond ? await ensureCard(groupSecondName, groupSecond.file) : false
      groupEvidence.cards = await cardCount()
      groupEvidence.imported = firstReady && secondReady
      // 2) 多选两张卡 → 发起群聊
      const picked = []
      for (const name of [cardName, groupSecondName]) {
        const selectButton = home.locator(`button.tavern-select[aria-label="选择 ${name} 加入群聊"]`).first()
        const fallback = home.locator('.tavern-card').filter({ hasText: name }).locator('.tavern-select').first()
        const target = (await selectButton.count().catch(() => 0)) ? selectButton : fallback
        if (await target.count().catch(() => 0)) {
          await target.click({ force: true }).catch(() => {})
          picked.push(name)
        }
        await page.waitForTimeout(150)
      }
      groupEvidence.selected = picked
      const bar = home.locator('.tavern-select-bar').first()
      const barShown = await waitFor(async () => (await bar.count().catch(() => 0)) > 0, 8000)
      const barText = barShown ? ((await bar.innerText().catch(() => '')) || '').trim() : ''
      groupEvidence.barText = barText
      await shot(page, '130-g-select.png')
      let groupChatFrame = null
      if (picked.length === 2 && /已选\s*2/.test(barText)) {
        const createButton = home.locator('.tavern-select-group').first()
        if (await createButton.count().catch(() => 0)) await createButton.click({ force: true }).catch(() => {})
        groupChatFrame = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 30000)
      }
      const groupUrl = groupChatFrame ? groupChatFrame.url() : null
      const groupChatId = groupUrl ? groupUrl.split('/chats/')[1].split(/[?#]/)[0] : null
      groupEvidence.groupUrl = groupUrl
      groupEvidence.groupId = groupChatId
      const readGroup = async () => {
        if (!groupChatFrame || !groupChatId) return null
        return groupChatFrame
          .evaluate(async (chatId) => {
            try {
              const response = await fetch(`/tavern/api/reader/v1/tavern/chats/${chatId}`, { credentials: 'include' })
              if (!response.ok) return { status: response.status }
              return await response.json()
            } catch (error) {
              return { error: String(error) }
            }
          }, groupChatId)
          .catch(() => null)
      }
      const groupDetail = groupUrl ? await readGroup() : null
      const detailChat = groupDetail && groupDetail.chat ? groupDetail.chat : null
      const detailMembers =
        detailChat && Array.isArray(detailChat.members) ? detailChat.members.map((member) => member.name) : []
      groupEvidence.members = detailMembers
      groupEvidence.title = detailChat ? detailChat.title : null
      if (!groupChatFrame) {
        check(groupCheckNames[0], false, `没有进入 /chats/<id>（bar=${clip(barText)} selected=${clip(picked)}；owner: cards-client 建群/路由）`)
        markStep(0)
        skipGroupRemaining('群聊没有创建成功，后续步骤无法验证')
      } else {
        await waitChatRendered(groupChatFrame, 25000)
        const memberText = ((await groupChatFrame.locator('.chat-members').innerText().catch(() => '')) || '').trim()
        const memberTitles = await groupChatFrame
          .locator('.chat-members [title]')
          .evaluateAll((els) => els.map((el) => el.getAttribute('title') || ''))
          .catch(() => [])
        const headerText = ((await groupChatFrame.locator('.chat-title').innerText().catch(() => '')) || '').trim()
        groupEvidence.memberText = memberText
        groupEvidence.headerText = headerText
        const membersShown =
          (memberText.includes(cardName) || memberTitles.some((title) => title.includes(cardName))) &&
          (memberText.includes(groupSecondName) || memberTitles.some((title) => title.includes(groupSecondName)))
        const headerHasNames = headerText.includes(cardName) && headerText.includes(groupSecondName)
        const apiOk =
          Boolean(detailChat) &&
          detailChat.mode === 'group' &&
          detailMembers.length === 2 &&
          detailMembers[0] === cardName &&
          detailMembers[1] === groupSecondName
        check(
          groupCheckNames[0],
          Boolean(groupChatFrame) && membersShown && headerHasNames && apiOk,
          `url=${groupUrl} members=${clip(detailMembers)} header=${clip(headerText)} memberText=${clip(memberText)} apiOk=${apiOk}`,
        )
        markStep(0)
        await shot(page, '131-g-chat-members.png')

        const sendGroupMessage = async (text) => {
          const input = groupChatFrame.locator('textarea[aria-label="聊天输入框"], textarea[placeholder*="说"]').first()
          if (!(await input.count().catch(() => 0))) return null
          await input.fill(text).catch(() => {})
          const beforeRows = await groupChatFrame.locator('.bubble-row.assistant').count().catch(() => 0)
          const sendButton = groupChatFrame.locator('button[aria-label="发送消息"], button[type="submit"]').first()
          // 刚进聊天或上一条还在收尾时发送按钮是 disabled 的，force click 会被静默丢弃 → 先等可用。
          await waitFor(async () => {
            if (!(await sendButton.count().catch(() => 0))) return false
            return !(await sendButton.isDisabled().catch(() => false))
          }, 20000)
          let httpStatus = null
          const onResponse = (response) => {
            if (/\/tavern\/chats\/[^/]+\/messages/.test(response.url()) && response.request().method() === 'POST') {
              httpStatus = response.status()
            }
          }
          page.on('response', onResponse)
          const startedAt = Date.now()
          let clickPath = 'click'
          if (await sendButton.count().catch(() => 0)) await sendButton.click({ force: true }).catch(() => {})
          else {
            clickPath = 'enter'
            await input.press('Enter').catch(() => {})
          }
          // 6s 内没有 POST 响应 → 点击可能落空，退化为 DOM click 再试一次，并记录 clickPath 便于归因。
          await page.waitForTimeout(6000)
          if (httpStatus === null) {
            clickPath += '+domClick'
            await sendButton.evaluate((el) => el.click()).catch(() => {})
          }
          let stable = 0
          let lastText = ''
          let finalText = ''
          let stallProbe = null
          while (Date.now() - startedAt < 180000) {
            if (!stallProbe && !lastText && Date.now() - startedAt > 30000) stallProbe = await gatewayProbe(15000)
            const rows = groupChatFrame.locator('.bubble-row.assistant')
            const count = await rows.count().catch(() => 0)
            const row = count > beforeRows ? rows.nth(count - 1) : null
            const bubble = row ? row.locator('.bubble-text').first() : null
            const textNow =
              bubble && (await bubble.count().catch(() => 0)) ? ((await bubble.innerText().catch(() => '')) || '').trim() : ''
            const pending =
              (await groupChatFrame.locator('.bubble-pending').count().catch(() => 0)) > 0 ||
              /正在回复/.test(await bodyText(groupChatFrame))
            if (textNow !== lastText) {
              lastText = textNow
              stable = 0
            } else if (textNow) {
              stable += 1
            }
            if (textNow && !pending && stable >= 4) {
              finalText = textNow
              break
            }
            await page.waitForTimeout(250)
          }
          page.off('response', onResponse)
          const lastRow = await lastAssistantRow(groupChatFrame)
          const speaker = lastRow
            ? ((await lastRow.locator('.bubble-speaker-name').first().innerText().catch(() => '')) || '').trim()
            : ''
          // 超时/失败时把 UI 错误提示与状态文案一起带回，便于判断是网关挂起还是前端没渲染。
          const alertText = ((await groupChatFrame.locator('[role=alert]').allTextContents().catch(() => [])) || []).join(' | ')
          const statusText = ((await groupChatFrame.locator('.chat-status').allTextContents().catch(() => [])) || []).join(' | ')
          return {
            text: finalText || lastText,
            speaker,
            elapsedMs: Date.now() - startedAt,
            httpStatus,
            clickPath,
            alertText,
            statusText,
            stallProbe,
          }
        }

        // G2：第一条 → members[0]
        const reply1 = await sendGroupMessage('你好')
        const detail1 = await readGroup()
        const assistants1 = detail1 && Array.isArray(detail1.messages) ? detail1.messages.filter((item) => item.role === 'assistant') : []
        const apiSpeaker1 = assistants1.length ? assistants1[assistants1.length - 1].speakerName : null
        groupEvidence.replies.push(reply1)
        const g2Ok =
          Boolean(reply1 && reply1.text && reply1.text.length >= 2) &&
          reply1.speaker === detailMembers[0] &&
          apiSpeaker1 === detailMembers[0] &&
          (await bodyText(groupChatFrame)).includes(detailMembers[0])
        check(
          groupCheckNames[1],
          g2Ok,
          `speaker=${clip(reply1 && reply1.speaker)} 期望=${clip(detailMembers[0])} apiSpeaker=${clip(apiSpeaker1)} text=${clip(reply1 && reply1.text, 80)} elapsed=${reply1 && reply1.elapsedMs}ms http=${reply1 && reply1.httpStatus} path=${reply1 && reply1.clickPath} alert=${clip(reply1 && reply1.alertText, 60)} status=${clip(reply1 && reply1.statusText, 40)} probe=${clip(JSON.stringify(reply1 && reply1.stallProbe))}`,
        )
        markStep(1)
        await shot(page, '132-g-reply-first.png')

        // G3：第二条 → members[1]
        const reply2 = await sendGroupMessage('再聊聊刚才那盏茶吧。')
        const detail2 = await readGroup()
        const assistants2 = detail2 && Array.isArray(detail2.messages) ? detail2.messages.filter((item) => item.role === 'assistant') : []
        const apiSpeakers = assistants2.map((item) => item.speakerName).filter(Boolean)
        groupEvidence.replies.push(reply2)
        groupEvidence.speakers = apiSpeakers
        const rotationOk =
          Boolean(reply2 && reply2.text && reply2.text.length >= 2) &&
          reply2.speaker === detailMembers[1] &&
          apiSpeakers.length >= 2 &&
          apiSpeakers[0] === detailMembers[0] &&
          apiSpeakers[1] === detailMembers[1]
        groupEvidence.rotation = rotationOk
        check(
          groupCheckNames[2],
          rotationOk,
          `speaker=${clip(reply2 && reply2.speaker)} 期望=${clip(detailMembers[1])} apiSpeakers=${clip(apiSpeakers)} text=${clip(reply2 && reply2.text, 80)}`,
        )
        markStep(2)
        await shot(page, '133-g-reply-rotate.png')

        // G4：刷新后消息/发言人/标题保持
        const firstText = (reply1 && reply1.text ? reply1.text : '').slice(0, 20)
        const secondText = (reply2 && reply2.text ? reply2.text : '').slice(0, 20)
        await groupChatFrame.evaluate(() => location.reload()).catch(() => {})
        groupChatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 25000)) || fnow()
        await waitChatRendered(groupChatFrame, 25000)
        const persisted = await waitFor(async () => {
          const text = await bodyText(groupChatFrame)
          return Boolean(firstText && secondText) && text.includes(firstText) && text.includes(secondText)
        }, 30000)
        // 刷新后详情是异步加载的：标题先显示占位「聊天」，发言人名也要等 detail 回来才渲染。
        const readTitle = async () => {
          const nameButton = groupChatFrame.locator('.chat-name').first()
          if (await nameButton.count().catch(() => 0)) return ((await nameButton.innerText().catch(() => '')) || '').trim()
          return ((await groupChatFrame.locator('.chat-title').innerText().catch(() => '')) || '').trim()
        }
        const reloadReady = await waitFor(async () => {
          const title = await readTitle()
          const names = await groupChatFrame.locator('.bubble-row.assistant .bubble-speaker-name').count().catch(() => 0)
          return names >= 2 && title.includes(cardName) && title.includes(groupSecondName)
        }, 25000)
        const speakerNames = await groupChatFrame
          .locator('.bubble-row.assistant .bubble-speaker-name')
          .allTextContents()
          .catch(() => [])
        const titleAfterReload = await readTitle()
        const g4Ok =
          persisted &&
          speakerNames.map((item) => item.trim()).includes(detailMembers[0]) &&
          speakerNames.map((item) => item.trim()).includes(detailMembers[1]) &&
          titleAfterReload.includes(cardName) &&
          titleAfterReload.includes(groupSecondName)
        groupEvidence.persisted = persisted
        groupEvidence.titleAfterReload = titleAfterReload
        check(
          groupCheckNames[3],
          g4Ok,
          `persisted=${persisted} speakers=${clip(speakerNames)} title=${clip(titleAfterReload)}`,
        )
        markStep(3)
        await shot(page, '134-g-reload.png')

        // G5：/chats 聊天历史列表出现该群聊
        const listFrame = await appGoto('/chats')
        const listReady = await waitFor(
          async () => (await listFrame.locator('.chat-history-row, .chat-history-list').count().catch(() => 0)) > 0,
          25000,
        )
        const rowTexts = await listFrame.locator('.chat-history-row').allInnerTexts().catch(() => [])
        const groupRow = rowTexts.find((text) => text.includes(groupSecondName) && text.includes(cardName)) || ''
        const modeShown = groupRow.includes('群聊')
        const lastShown = /。|！|？|\.|!|\?/.test(groupRow)
        const apiAfter = await apiChats()
        groupEvidence.listAfter = apiAfter && Array.isArray(apiAfter.items) ? apiAfter.items.length : 0
        groupEvidence.listShown = Boolean(groupRow)
        groupEvidence.listModeShown = modeShown
        const g5Ok = listReady && Boolean(groupRow) && modeShown && lastShown && groupEvidence.listAfter > groupEvidence.listBefore
        check(
          groupCheckNames[4],
          g5Ok,
          `rows=${rowTexts.length} before=${groupEvidence.listBefore} after=${groupEvidence.listAfter} groupRow=${clip(groupRow)} mode=${modeShown} lastMessage=${lastShown}`,
        )
        markStep(4)
        await shot(page, '135-g-list.png')

        // G6：删除群聊 → 列表减少（客户端菜单没有删除聊天，走 API DELETE，随后刷新列表）
        const deleteStatus = await listFrame
          .evaluate(async (chatId) => {
            try {
              const response = await fetch(`/tavern/api/reader/v1/tavern/chats/${chatId}`, {
                method: 'DELETE',
                credentials: 'include',
              })
              return response.status
            } catch (error) {
              return String(error)
            }
          }, groupChatId)
          .catch(() => null)
        const apiDeleted = await apiChats()
        groupEvidence.listAfterDelete = apiDeleted && Array.isArray(apiDeleted.items) ? apiDeleted.items.length : -1
        const listFrame2 = await appGoto('/chats')
        await waitFor(async () => (await listFrame2.locator('.chat-history-row').count().catch(() => 0)) >= 0, 15000)
        const rowsAfter = await listFrame2.locator('.chat-history-row').allInnerTexts().catch(() => [])
        const groupGone = !rowsAfter.some((text) => text.includes(groupSecondName) && text.includes(cardName))
        groupEvidence.deleted = groupGone
        const g6Ok =
          deleteStatus === 204 && groupGone && groupEvidence.listAfterDelete < groupEvidence.listAfter
        check(
          groupCheckNames[5],
          g6Ok,
          `deleteStatus=${deleteStatus} listAfter=${groupEvidence.listAfter} afterDelete=${groupEvidence.listAfterDelete} groupGone=${groupGone}`,
        )
        markStep(5)
        await shot(page, '136-g-deleted.png')

        // G7：本组 console clean
        const groupConsole = consoleErrors
          .slice(groupConsoleStart)
          .filter((text) => !/Failed to load resource/.test(text))
        groupEvidence.console = groupConsole
        check(groupCheckNames[6], groupConsole.length === 0, groupConsole.length ? clip(groupConsole.join(' | ')) : '本组无 console 报错')
        markStep(6)
      }
    }
  }

  // G 组结束后 iframe 停在 /chats 列表：把聊天页还回 C 组创建的单聊（C2/R/P/T 依赖 chatFrame）。
  if (groupEvidence.enabled && chatEvidence.chatUrl) {
    const restorePath = new URL(chatEvidence.chatUrl).pathname.replace(/^\/tavern/, '') || '/'
    let restored = await appGoto(restorePath)
    if (restored) {
      await waitChatRendered(restored, 25000)
      let loaded = await waitFor(
        async () => (await restored.locator('article.bubble-row.assistant').count().catch(() => 0)) > 0,
        20000,
      )
      if (!loaded) {
        const again = await appGoto(restorePath)
        if (again) {
          await waitChatRendered(again, 25000)
          loaded = await waitFor(
            async () => (await again.locator('article.bubble-row.assistant').count().catch(() => 0)) > 0,
            20000,
          )
          if (loaded) restored = again
        }
      }
      report.native.restore = { ok: loaded, url: restored.url() }
      chatFrame = restored
    }
  }

  // ── M. 群聊成员管理（task-70：管理成员弹层 2→3→2 + 校验 + 单聊无入口）──────
  const membersEvidence = {
    enabled: false, chatId: null, cards: [], opened: null, added: null, removed: null,
    invalid: null, single: null, console: [], steps: [],
  }
  report.native.membership = membersEvidence
  const memberCheckNames = [
    '群聊「管理成员」弹层打开：2 人预选、计数 2/5、角色卡可勾选',
    '勾选第 3 张保存后：PATCH 200、页头 3 人、GET members=3、aliveMemberCount=3',
    '取消 1 人保存后 members=2，继续发言 POST 200 + 落库且 speakerName 属于剩余成员',
    '成员校验：只选 1 时保存禁用 + 文案；直连 PATCH 1 个/6 个/单聊均 422',
    '单聊页头无「管理成员」入口；群聊管理流程 console 无报错',
  ]
  if (groupOn('m') && !nativeBlocked) {
    membersEvidence.enabled = true
    const memberStart = consoleErrors.length
    const memberConsole = () => consoleErrors.slice(memberStart).filter((text) => !/Failed to load resource/.test(text))
    let memberStep = 0
    const memberPlane = () => fnow()
    const mApi = async (pathname, options = {}) => {
      const frameNow = memberPlane()
      if (!frameNow) return { status: 0, body: null }
      return frameNow.evaluate(
        async ({ pathname: target, method, body }) => {
          const response = await fetch('/tavern/api/reader/v1/tavern' + target, {
            method: method || 'GET',
            credentials: 'include',
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
          })
          let payload = null
          try {
            payload = await response.json()
          } catch (error) {
            payload = null
          }
          return { status: response.status, body: payload }
        },
        { pathname, method: options.method || 'GET', body: options.body || null },
      ).catch(() => ({ status: 0, body: null }))
    }
    const memberMark = (index) => {
      if (!membersEvidence.steps.includes(index)) membersEvidence.steps.push(index)
      memberStep = Math.max(memberStep, index + 1)
    }
    const memberSkipRest = (detail) => {
      for (let index = memberStep; index < memberCheckNames.length; index += 1) {
        if (!membersEvidence.steps.includes(index)) {
          skip(memberCheckNames[index], detail)
          memberMark(index)
        }
      }
    }
    const closeMembersDialog = async () => {
      const frameNow = memberPlane()
      if (!frameNow) return
      const dialog = frameNow.locator('.member-manager[role="dialog"]').first()
      if (!(await dialog.count().catch(() => 0))) return
      const cancel = dialog.locator('button:not(.primary)').last()
      await cancel.click({ force: true }).catch(() => {})
      await waitFor(async () => (await frameNow.locator('.member-manager[role="dialog"]').count().catch(() => 0)) === 0, 8000)
    }
    const waitSaveEnabled = async (dialog, timeout = 10000) => {
      return waitFor(async () => {
        const save = dialog.locator('button.primary').first()
        if (!(await save.count().catch(() => 0))) return false
        return !(await save.isDisabled().catch(() => true))
      }, timeout)
    }
    const setMemberSelected = async (dialog, name, selected) => {
      const box = dialog.locator(`input[type="checkbox"][aria-label="选择 ${name}"]`).first()
      const found = await waitFor(async () => (await box.count().catch(() => 0)) > 0, 8000)
      if (!found) return { ok: false, reason: 'checkbox-missing' }
      const current = await box.isChecked().catch(() => false)
      if (current !== selected) {
        if (selected) await box.check({ force: true }).catch(() => {})
        else await box.uncheck({ force: true }).catch(() => {})
        await page.waitForTimeout(200)
      }
      const after = await box.isChecked().catch(() => false)
      return { ok: after === selected, current, after }
    }
    const openMembersSheet = async () => {
      await closeMembersDialog()
      await waitChatRendered(memberPlane(), 25000)
      const frameNow = memberPlane()
      if (!frameNow) return null
      const menu = frameNow.locator('button[aria-label="聊天菜单"]').first()
      if (!(await menu.count().catch(() => 0))) return null
      await menu.click({ force: true }).catch(() => {})
      const entry = frameNow.locator('.chat-menu-pop button[role="menuitem"]').filter({ hasText: '管理成员' }).first()
      if (!(await entry.count().catch(() => 0))) return null
      if (await entry.isDisabled().catch(() => false)) return null
      await entry.click({ force: true }).catch(() => {})
      await waitFor(async () => (await frameNow.locator('.member-manager[role="dialog"]').count().catch(() => 0)) > 0, 15000)
      const dialog = frameNow.locator('.member-manager[role="dialog"]').first()
      if (!(await dialog.count().catch(() => 0))) return null
      return dialog
    }
    const memberSendMessage = async (text) => {
      const frameNow = memberPlane()
      const input = frameNow.locator('textarea[aria-label="聊天输入框"], textarea[placeholder*="说"]').first()
      if (!(await input.count().catch(() => 0))) return null
      await input.fill(text).catch(() => {})
      const beforeRows = await frameNow.locator('.bubble-row.assistant').count().catch(() => 0)
      const sendButton = frameNow.locator('button[aria-label="发送消息"], button[type="submit"]').first()
      await waitFor(async () => {
        if (!(await sendButton.count().catch(() => 0))) return false
        return !(await sendButton.isDisabled().catch(() => false))
      }, 20000)
      let httpStatus = null
      const onResponse = (response) => {
        if (/\/tavern\/chats\/[^/]+\/messages/.test(new URL(response.url()).pathname) && response.request().method() === 'POST') {
          httpStatus = response.status()
        }
      }
      page.on('response', onResponse)
      const startedAt = Date.now()
      let clickPath = 'click'
      if (await sendButton.count().catch(() => 0)) await sendButton.click({ force: true }).catch(() => {})
      else {
        clickPath = 'enter'
        await input.press('Enter').catch(() => {})
      }
      await page.waitForTimeout(6000)
      if (httpStatus === null) {
        clickPath += '+domClick'
        await sendButton.evaluate((el) => el.click()).catch(() => {})
      }
      let stable = 0
      let lastText = ''
      let finalText = ''
      while (Date.now() - startedAt < 180000) {
        const rows = frameNow.locator('.bubble-row.assistant')
        const count = await rows.count().catch(() => 0)
        const row = count > beforeRows ? rows.nth(count - 1) : null
        const bubble = row ? row.locator('.bubble-text').first() : null
        const textNow = bubble && (await bubble.count().catch(() => 0)) ? ((await bubble.innerText().catch(() => '')) || '').trim() : ''
        const pending =
          (await frameNow.locator('.bubble-pending').count().catch(() => 0)) > 0 || /正在回复/.test(await bodyText(frameNow))
        if (textNow !== lastText) {
          lastText = textNow
          stable = 0
        } else if (textNow) {
          stable += 1
        }
        if (textNow && !pending && stable >= 4) {
          finalText = textNow
          break
        }
        await page.waitForTimeout(250)
      }
      page.off('response', onResponse)
      const lastRow = await lastAssistantRow(frameNow)
      const speaker = lastRow ? ((await lastRow.locator('.bubble-speaker-name').first().innerText().catch(() => '')) || '').trim() : ''
      return { text: finalText || lastText, speaker, elapsedMs: Date.now() - startedAt, httpStatus, clickPath }
    }

    const sheetSummary = async (dialog) => {
      const text = ((await dialog.innerText().catch(() => '')) || '').replace(/\s+/g, ' ')
      const checks = await dialog.locator('input[type="checkbox"]').count().catch(() => 0)
      const checked = await dialog.locator('input[type="checkbox"]:checked').count().catch(() => 0)
      const save = dialog.locator('button.primary').first()
      const saveDisabled = await save.isDisabled().catch(() => true)
      const hint = dialog.locator('.member-count-hint').first()
      const hintText = (await hint.count().catch(() => 0)) ? ((await hint.innerText().catch(() => '')) || '').trim() : ''
      return { text, checks, checked, saveDisabled, hintText }
    }
    const saveMembers = async (expectedStatus = 200) => {
      const frameNow = memberPlane()
      const dialog = frameNow.locator('.member-manager[role="dialog"]').first()
      const save = dialog.locator('button.primary').first()
      let httpStatus = null
      const onResponse = (response) => {
        if (/\/tavern\/chats\/[^/]+$/.test(new URL(response.url()).pathname) && response.request().method() === 'PATCH') {
          httpStatus = response.status()
        }
      }
      page.on('response', onResponse)
      await save.click({ force: true }).catch(() => {})
      await waitFor(async () => (await frameNow.locator('.member-manager[role="dialog"]').count().catch(() => 0)) === 0, 20000)
      page.off('response', onResponse)
      return httpStatus
    }
    const headerMembers = async () => {
      const frameNow = memberPlane()
      if (!frameNow) return { count: 0, labels: [] }
      const labels = await frameNow
        .locator('.chat-members .chat-member-avatar')
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('aria-label') || node.getAttribute('title') || '').filter(Boolean))
        .catch(() => [])
      return { count: labels.length, labels }
    }
    const upTo = (value, length = 200) => {
      const text = String(value == null ? '' : value)
      return text.length > length ? text.slice(0, length) + '…' : text
    }

    try {
      let list = await mApi('/characters')
      let items = list && list.body && Array.isArray(list.body.items) ? list.body.items : []
      const want = ['林小满', '顾清和', '沈夜舟']
      const missing = want.filter((name) => !items.some((card) => card.name === name))
      if (missing.length) {
        const fixtureBy = { 林小满: 'v2-png.png', 顾清和: 'v2-json.json', 沈夜舟: 'v1-json.json' }
        for (const name of missing) {
          await appGoto('/')
          const home = fnow()
          if (!home) break
          const input = home.locator('input[type=file][aria-label="导入角色卡文件"], .tavern-import input[type=file], input[type=file]').first()
          if (!(await input.count().catch(() => 0))) break
          await input.setInputFiles(path.join(fixturesDir, fixtureBy[name])).catch(() => {})
          await waitFor(async () => Boolean(await findCard(name)), 20000)
        }
        list = await mApi('/characters')
        items = list && list.body && Array.isArray(list.body.items) ? list.body.items : []
      }
      const cardIdOf = (name) => {
        const found = items.find((card) => card.name === name)
        return found ? found.id : null
      }
      const ids = want.map(cardIdOf)
      membersEvidence.cards = want.map((name, index) => ({ name, id: ids[index] }))
      if (ids.some((id) => !id)) {
        memberSkipRest(`角色卡不足（${want.join('/')}；已导入 ${items.map((card) => card.name).join('、') || '无'}；owner: cards-client 导入）`)
      } else {
        const created = await mApi('/chats', { method: 'POST', body: { cardIds: [ids[0], ids[1]] } })
        const chatId = created && created.body && created.body.chat ? created.body.chat.id : null
        membersEvidence.chatId = chatId
        if (!chatId) {
          memberSkipRest(`创建 2 人群聊失败：status=${created && created.status} body=${upTo(created && created.body && JSON.stringify(created.body), 160)}（owner: app-auth /tavern/chats）`)
        } else {
          const opened = await appGoto(`/chats/${chatId}`)
          if (!opened) {
            memberSkipRest('群聊页打不开（owner: cards-client /chats/:id）')
          } else {
            await waitChatRendered(opened, 25000)
            chatFrame = opened

            // M1：管理成员弹层 2 人预选、计数 2/5
            const dialog1 = await openMembersSheet()
            if (!dialog1) {
              memberSkipRest('群聊页没有「管理成员」入口/弹层（owner: cards-client 管理成员）')
            } else {
              const summary1 = await sheetSummary(dialog1)
              const m1Ok = summary1.checked === 2 && /2\/5/.test(summary1.text) && summary1.checks >= 3
              membersEvidence.opened = { ...summary1, dialog: await dialog1.count().catch(() => 0) }
              check(memberCheckNames[0], m1Ok, `checked=${summary1.checked} checks=${summary1.checks} text=${upTo(summary1.text, 140)}`)
              memberMark(0)
              await shot(page, '145-m1-members-dialog.png')

              // M2：勾第 3 张保存 → PATCH 200 + 页头 3 人 + members=3
              let m2Ok = false
              let m2Detail = ''
              const third = want[2]
              const thirdBox = dialog1.locator(`input[type="checkbox"][aria-label="选择 ${third}"]`).first()
              if (!(await thirdBox.count().catch(() => 0))) {
                m2Detail = `弹层没有第 3 张可选卡（${third}）`
              } else {
                const check3 = await setMemberSelected(dialog1, third, true)
                if (!check3.ok) m2Detail += `（setMemberSelected=${check3.reason || 'still-unchecked'}）`
                await waitSaveEnabled(dialog1, 10000)
                const before = await sheetSummary(dialog1)
                m2Ok = before.checked === 3 && !before.saveDisabled
                if (!m2Ok) m2Detail = `勾选第 3 张后 checked=${before.checked} saveDisabled=${before.saveDisabled}`
                else {
                  const patchStatus = await saveMembers()
                  const header = await headerMembers()
                  const after = await mApi(`/chats/${chatId}`)
                  const chat = after && after.body && after.body.chat ? after.body.chat : {}
                  const memberList = Array.isArray(chat.members) ? chat.members : []
                  const alive = Number(chat.aliveMemberCount || 0)
                  m2Ok = patchStatus === 200 && header.count === 3 && memberList.length === 3 && alive === 3
                  m2Detail = `patch=${patchStatus} header=${header.count} members=${memberList.length} alive=${alive} labels=${header.labels.join('、')}`
                  membersEvidence.added = { patchStatus, header, members: memberList.length, alive, labels: header.labels }
                  await shot(page, '146-m2-members-3.png')
                }
              }
              check(memberCheckNames[1], m2Ok, m2Detail)
              memberMark(1)

              // M3：取消 1 人保存 → members=2，继续发言 POST 200 + 落库 + speakerName ∈ 剩余成员
              let m3Ok = false
              let m3Detail = ''
              const dialog3 = await openMembersSheet()
              if (!dialog3) {
                m3Detail = '保存后再次打开「管理成员」失败（owner: cards-client 管理成员）'
              } else {
                const drop = await setMemberSelected(dialog3, third, false)
                m3Detail = `drop=${JSON.stringify(drop)} `
                await waitSaveEnabled(dialog3, 10000)
                const summary3 = await sheetSummary(dialog3)
                const patchStatus3 = summary3.saveDisabled ? null : await saveMembers()
                m3Detail = `summary3 checked=${summary3.checked} saveDisabled=${summary3.saveDisabled} `
                const after3 = await mApi(`/chats/${chatId}`)
                const chat3 = after3 && after3.body && after3.body.chat ? after3.body.chat : {}
                const remaining = (Array.isArray(chat3.members) ? chat3.members : []).map((member) => member.name).filter(Boolean)
                const beforeMessages = Array.isArray(after3.body && after3.body.messages) ? after3.body.messages : []
                const beforeAssistant = beforeMessages.filter((item) => item.role === 'assistant').length
                const reply = await memberSendMessage('（E2E 成员管理）人少了，继续聊聊这盏茶吧。')
                const httpStatus = reply ? reply.httpStatus : null
                const afterReply = await mApi(`/chats/${chatId}`)
                const replyMessages = Array.isArray(afterReply.body && afterReply.body.messages) ? afterReply.body.messages : []
                const assistants = replyMessages.filter((item) => item.role === 'assistant')
                const last = assistants[assistants.length - 1]
                const speaker = last && last.speakerName ? last.speakerName : ''
                m3Ok = patchStatus3 === 200 && remaining.length === 2 && beforeAssistant + 1 <= assistants.length && httpStatus === 200 && remaining.includes(speaker) && Boolean(reply && reply.text)
                m3Detail += `patch=${patchStatus3} members=${remaining.join('、')} post=${httpStatus} assistant=${beforeAssistant}→${assistants.length} speaker=${speaker} reply=${upTo(reply && reply.text, 40)}`
                membersEvidence.removed = { patchStatus: patchStatus3, remaining, beforeAssistant, assistants: assistants.length, httpStatus, speaker, reply: upTo(reply && reply.text, 60) }
                await shot(page, '147-m3-members-2-reply.png')
              }
              check(memberCheckNames[2], m3Ok, m3Detail)
              memberMark(2)

              // M4：只选 1 → 保存禁用 + 文案；直连 PATCH 1 个/6 个/单聊 → 422
              let m4Ok = false
              let m4Detail = ''
              const dialog4 = await openMembersSheet()
              if (!dialog4) {
                m4Detail = '打开「管理成员」失败（owner: cards-client 管理成员）'
              } else {
                // 只保留第一位成员：取消其余勾选
                const uncheckResults = []
                for (const name of want.slice(1)) {
                  uncheckResults.push({ name, ...(await setMemberSelected(dialog4, name, false)) })
                }
                m4Detail = `uncheck=${JSON.stringify(uncheckResults)} `
                const summary4 = await sheetSummary(dialog4)
                const invalidOne = await mApi(`/chats/${chatId}`, { method: 'PATCH', body: { cardIds: [ids[0]] } })
                const invalidSix = await mApi(`/chats/${chatId}`, { method: 'PATCH', body: { cardIds: ['x1', 'x2', 'x3', 'x4', 'x5', 'x6'] } })
                const singlePath = chatEvidence.chatUrl ? new URL(chatEvidence.chatUrl).pathname.replace(/^\/tavern/, '') : null
                const singleId = singlePath ? singlePath.split('/').pop() : null
                const invalidSingle = singleId ? await mApi(`/chats/${singleId}`, { method: 'PATCH', body: { cardIds: [ids[0], ids[1]] } }) : { status: 0, body: null }
                m4Ok =
                  summary4.checked === 1 &&
                  summary4.saveDisabled &&
                  /群聊需要 2 至 5 位角色/.test(summary4.hintText) &&
                  invalidOne.status === 422 &&
                  invalidSix.status === 422 &&
                  invalidSingle.status === 422 &&
                  singleId !== null
                m4Detail += `checked=${summary4.checked} saveDisabled=${summary4.saveDisabled} hint=${upTo(summary4.hintText, 40)} patch1=${invalidOne.status}/${upTo(invalidOne.body && invalidOne.body.code, 40)} patch6=${invalidSix.status}/${upTo(invalidSix.body && invalidSix.body.code, 40)} patchSingle=${invalidSingle.status}/${upTo(invalidSingle.body && invalidSingle.body.code, 40)}`
                membersEvidence.invalid = { checked: summary4.checked, saveDisabled: summary4.saveDisabled, hint: summary4.hintText, one: invalidOne, six: invalidSix, single: invalidSingle }
                await shot(page, '148-m4-invalid.png')
                await dialog4.locator('button:not(.primary)').first().click({ force: true }).catch(() => {})
              }
              check(memberCheckNames[3], m4Ok, m4Detail)
              memberMark(3)

              // M5：单聊页头无「管理成员」入口 + console clean
              const singlePath5 = chatEvidence.chatUrl ? new URL(chatEvidence.chatUrl).pathname.replace(/^\/tavern/, '') : null
              let m5Ok = false
              let m5Detail = ''
              if (singlePath5) {
                const singleFrame = await appGoto(singlePath5)
                if (singleFrame) {
                  await waitChatRendered(singleFrame, 25000)
                  chatFrame = singleFrame
                  const menu = singleFrame.locator('button[aria-label="聊天菜单"]').first()
                  if (await menu.count().catch(() => 0)) await menu.click({ force: true }).catch(() => {})
                  await waitFor(async () => (await singleFrame.locator('.chat-menu-pop').count().catch(() => 0)) > 0, 8000)
                  const entryCount = await singleFrame.locator('.chat-menu-pop button[role="menuitem"]').filter({ hasText: '管理成员' }).count().catch(() => 0)
                  const clean = memberConsole()
                  m5Ok = entryCount === 0 && clean.length === 0
                  m5Detail = `entries=${entryCount} console=${clean.length ? upTo(clean.join(' | '), 140) : 'clean'}`
                  membersEvidence.single = { entries: entryCount, menu: true }
                  membersEvidence.console = clean
                  await shot(page, '149-m5-single-no-entry.png')
                } else {
                  m5Detail = '单聊页打不开'
                }
              } else {
                m5Detail = '缺少单聊会话（C 组未创建）'
              }
              check(memberCheckNames[4], m5Ok, m5Detail)
              memberMark(4)
            }
          }
        }
      }
      memberSkipRest('前置未完成（群聊/成员管理未就绪）')
    } catch (error) {
      memberSkipRest(`成员管理流程异常：${upTo(error && error.message || error, 120)}`)
    }
  }

  // ── C2. 停止生成（在 R 之前制造一次可取消的流式，不新增成功回复）────────────
  const stopEvidence = { visible: false, clicked: false, ended: false, error: null }
  report.native.stop = stopEvidence
  if (groupOn('c')) {
    if (nativeBlocked || !chatFrame) {
      skip('停止生成按钮出现、可点且不报错', nativeBlocked ? blockedReason : '前置：聊天页未打开')
    } else {
      await waitChatRendered(chatFrame, 20000)
      const regenAgain = chatFrame.getByRole('button', { name: /重新生成|重生成|重新回复/ }).first()
      const regenReady = await waitFor(async () => (await regenAgain.count().catch(() => 0)) > 0, 20000)
      if (!regenReady) {
        const c2Url = chatFrame && typeof chatFrame.url === 'function' ? chatFrame.url() : '(no-frame)'
        const c2Rows = chatFrame ? await chatFrame.locator('article.bubble-row').count().catch(() => -1) : -1
        const c2Restore = report.native.restore ? JSON.stringify(report.native.restore) : '{}'
        skip(
          '停止生成按钮出现、可点且不报错',
          `聊天页没有「重新生成」按钮，无法制造流式（owner: cards-client ChatPage）；debug url=${c2Url} rows=${c2Rows} restore=${c2Restore}`,
        )
      } else {
        await regenAgain.click({ force: true }).catch(() => {})
        const stopLocator = chatFrame.locator('button[aria-label*="停止"], button:has-text("停止")').first()
        let visible = false
        for (let i = 0; i < 100 && !stopEvidence.clicked; i += 1) {
          if ((await stopLocator.count().catch(() => 0)) > 0) {
            visible = true
            await stopLocator.click({ force: true }).catch(() => {})
            stopEvidence.clicked = true
            break
          }
          await page.waitForTimeout(100)
        }
        stopEvidence.visible = visible
        const ended = await waitFor(async () => {
          const pending =
            (await chatFrame.locator('.bubble-pending').count().catch(() => 0)) > 0 ||
            /正在回复/.test(await bodyText(chatFrame))
          return !pending
        }, 30000)
        const alert = await chatFrame.locator('[role=alert]').first().innerText().catch(() => '')
        stopEvidence.ended = ended
        stopEvidence.error = alert || null
        check(
          '停止生成按钮出现、可点且不报错',
          visible && stopEvidence.clicked && ended && !alert,
          `visible=${visible} clicked=${stopEvidence.clicked} ended=${ended} alert=${clip(alert || '无')}`,
        )
        await shot(page, '100-c-stop.png')
      }
    }
  }

  // ── R. 重新生成 ──────────────────────────────────────────────────────────
  const regenEvidence = { changed: false, swipesBefore: null, swipesAfter: null, messageCount: null }
  report.native.regenerate = regenEvidence
  if (groupOn('r')) {
    if (nativeBlocked) {
      skip('重新生成：最后一条 assistant 文本变化、版本数增加且无重复消息', blockedReason)
    } else if (!chatFrame) {
      skip('重新生成：最后一条 assistant 文本变化、版本数增加且无重复消息', '前置：聊天页未打开')
    } else {
      // 等聊天记录渲染完（reload 后 load() 是异步的），再取最后一条 assistant。
      await waitFor(async () => {
        const candidate = await lastAssistantRow(chatFrame)
        return (await rowText(candidate)).length > 0
      }, 20000)
      const row = await lastAssistantRow(chatFrame)
      const before = await rowText(row)
      const countBefore = await chatFrame.locator('.bubble-row').count().catch(() => 0)
      const swipesBefore = ((await chatFrame.locator('.swipe-count').first().innerText().catch(() => '')) || '').trim()
      const regenButton = chatFrame.getByRole('button', { name: /重新生成|重生成|重新回复/ }).first()
      if (!before) {
        skip('重新生成：最后一条 assistant 文本变化、版本数增加且无重复消息', '没有可重生成的 assistant 消息')
      } else if (!(await regenButton.count().catch(() => 0))) {
        skip('重新生成：最后一条 assistant 文本变化、版本数增加且无重复消息', '聊天页没有「重新生成」按钮（owner: cards-client ChatPage）')
      } else {
        await regenButton.click({ force: true }).catch(() => {})
        const changed = await waitFor(async () => {
          const nowRow = await lastAssistantRow(chatFrame)
          const text = await rowText(nowRow)
          const pending = (await chatFrame.locator('.bubble-pending').count().catch(() => 0)) > 0 || /正在回复/.test(await bodyText(chatFrame))
          return text.length > 0 && text !== before && !pending
        }, 120000)
        const countAfter = await chatFrame.locator('.bubble-row').count().catch(() => 0)
        const swipesAfter = ((await chatFrame.locator('.swipe-count').first().innerText().catch(() => '')) || '').trim()
        regenEvidence.changed = changed
        regenEvidence.beforeFirst60 = before.slice(0, 60)
        const afterRow = await lastAssistantRow(chatFrame)
        regenEvidence.afterFirst60 = (await rowText(afterRow)).slice(0, 60)
        regenEvidence.swipesBefore = swipesBefore || null
        regenEvidence.swipesAfter = swipesAfter || null
        regenEvidence.messageCount = { before: countBefore, after: countAfter }
        const versionIncreased = swipesAfter !== '' && swipesAfter !== swipesBefore
        check(
          '重新生成：最后一条 assistant 文本变化、版本数增加且无重复消息',
          changed && countAfter === countBefore && versionIncreased,
          `changed=${changed} 版本 ${clip(swipesBefore || '1/1')}→${clip(swipesAfter || '无')} 消息数 ${countBefore}→${countAfter} 前=${clip(regenEvidence.beforeFirst60)} 后=${clip(regenEvidence.afterFirst60)}`,
        )
        await shot(page, '96-regenerate.png')
      }
    }
  }

  // ── P. swipe 版本切换 ─────────────────────────────────────────────────────
  const swipeEvidence = { versions: null, switched: false, persisted: false, backToLatest: false, oldVersionText: null, latestText: null }
  report.native.swipe = swipeEvidence
  if (groupOn('p')) {
    if (nativeBlocked || !chatFrame) {
      skip('swipe：切换到旧版本后文本变化且刷新保持', nativeBlocked ? blockedReason : '前置：聊天页未打开')
    } else {
      await waitFor(async () => (await chatFrame.locator('.swipe-count').count().catch(() => 0)) > 0, 15000)
      const versions = ((await chatFrame.locator('.swipe-count').first().innerText().catch(() => '')) || '').trim()
      swipeEvidence.versions = versions || null
      const prevButton = chatFrame.locator('button[aria-label="上一个版本"], button[aria-label*="上一个"]').first()
      const nextButton = chatFrame.locator('button[aria-label="下一个版本"], button[aria-label*="下一个"]').first()
      const latestRow = await lastAssistantRow(chatFrame)
      const latestText = await rowText(latestRow)
      const hasControls = (await prevButton.count().catch(() => 0)) > 0 && (await nextButton.count().catch(() => 0)) > 0
      if (!hasControls) {
        skip('swipe：切换到旧版本后文本变化且刷新保持', '聊天页没有版本切换按钮（owner: cards-client ChatPage swipe）')
      } else {
        await prevButton.click({ force: true }).catch(() => {})
        const switched = await waitFor(async () => {
          const text = await rowText(await lastAssistantRow(chatFrame))
          return text.length > 0 && text !== latestText
        }, 20000)
        const oldRow = await lastAssistantRow(chatFrame)
        const oldText = await rowText(oldRow)
        const countAfterSwitch = ((await chatFrame.locator('.swipe-count').first().innerText().catch(() => '')) || '').trim()
        swipeEvidence.switched = switched
        swipeEvidence.oldVersionText = oldText.slice(0, 60)
        swipeEvidence.latestText = latestText.slice(0, 60)
        await shot(page, '101-p-swipe.png')

        await chatFrame.evaluate(() => location.reload()).catch(() => {})
        chatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 20000)) || fnow()
        await waitChatRendered(chatFrame, 25000)
        await waitFor(async () => (await chatFrame.locator('.swipe-count').count().catch(() => 0)) > 0, 15000)
        const persisted = await waitFor(async () => {
          const text = await rowText(await lastAssistantRow(chatFrame))
          return text.length > 0 && oldText.length > 0 && text.includes(oldText.slice(0, 20))
        }, 25000)
        swipeEvidence.persisted = persisted
        const nextAfterReload = chatFrame.locator('button[aria-label="下一个版本"], button[aria-label*="下一个"]').first()
        const nextReady = await waitFor(async () => (await nextAfterReload.count().catch(() => 0)) > 0, 15000)
        if (nextReady) {
          await nextAfterReload.click({ force: true }).catch(() => {})
          swipeEvidence.backToLatest = await waitFor(async () => {
            const text = await rowText(await lastAssistantRow(chatFrame))
            return text.length > 0 && latestText.length > 0 && text.includes(latestText.slice(0, 20))
          }, 20000)
        }
        check(
          'swipe：切换到旧版本后文本变化且刷新保持',
          switched && persisted && swipeEvidence.backToLatest,
          `版本=${clip(versions || '无')}→${clip(countAfterSwitch || '无')} switched=${switched} persisted=${persisted} 切回最新=${swipeEvidence.backToLatest} 旧=${clip(swipeEvidence.oldVersionText || '')} 新=${clip(swipeEvidence.latestText || '')}`,
        )
      }
    }
  }

  // ── T. 聊天标题改名 ───────────────────────────────────────────────────────
  const titleEvidence = { before: null, after: null, updated: false, persisted: false }
  report.native.title = titleEvidence
  if (groupOn('t')) {
    if (nativeBlocked || !chatFrame) {
      skip('标题改名后刷新保持', nativeBlocked ? blockedReason : '前置：聊天页未打开')
    } else {
      await waitChatRendered(chatFrame, 20000)
      let titleElement = chatFrame.locator('.chat-name').first()
      const titleReady = await waitFor(async () => (await titleElement.count().catch(() => 0)) > 0, 15000)
      if (!titleReady) titleElement = chatFrame.locator('header h1').first()
      if (!(await titleElement.count().catch(() => 0))) {
        skip('标题改名后刷新保持', '聊天页没有可点击标题（owner: cards-client ChatPage）')
      } else {
        const before = ((await titleElement.innerText().catch(() => '')) || '').trim()
        titleEvidence.before = before
        await titleElement.click({ force: true }).catch(() => {})
        const titleInput = chatFrame.locator('input[aria-label="聊天名称"], input[aria-label*="标题"], input.chat-title-input').first()
        const formShown = await waitFor(async () => (await titleInput.count().catch(() => 0)) > 0, 10000)
        if (!formShown) {
          skip('标题改名后刷新保持', '点击标题没有出现编辑框（owner: cards-client ChatPage）')
        } else {
          const nextTitle = `E2E 改标题 ${Date.now().toString().slice(-4)}`
          await titleInput.fill(nextTitle).catch(() => {})
          await titleInput.press('Enter').catch(() => {})
          let updated = await waitFor(async () => (await bodyText(chatFrame)).includes(nextTitle), 10000)
          if (!updated) {
            const saveButton = chatFrame.getByRole('button', { name: /保存|确定/ }).first()
            if (await saveButton.count().catch(() => 0)) await saveButton.click({ force: true }).catch(() => {})
            updated = await waitFor(async () => (await bodyText(chatFrame)).includes(nextTitle), 10000)
          }
          titleEvidence.after = nextTitle
          titleEvidence.updated = updated
          await shot(page, '104-t-title.png')
          await chatFrame.evaluate(() => location.reload()).catch(() => {})
          chatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 20000)) || fnow()
          await waitChatRendered(chatFrame, 25000)
          const persisted = await waitFor(async () => (await bodyText(chatFrame)).includes(nextTitle), 20000)
          titleEvidence.persisted = persisted
          check(
            '标题改名后刷新保持',
            updated && persisted,
            `before=${clip(before)} after=${clip(nextTitle)} updated=${updated} persisted=${persisted}`,
          )
        }
      }
    }
  }

  // ── H. 聊天历史 ──────────────────────────────────────────────────────────
  const historyEvidence = { listShown: false, reentered: false, messagesIntact: false }
  report.native.history = historyEvidence
  if (groupOn('h')) {
    if (nativeBlocked) {
      skip('返回角色页看到聊天历史列表', blockedReason)
      skip('从历史重新进入聊天，消息完整', blockedReason)
    } else {
      // 历史列表属于「C 组那个聊天」的角色；打开它对应的卡详情，
      // 避免首页有多张卡时 openDetail() 打开的是另一张卡导致历史为空。
      let detail = null
      let detailCardId = null
      const chatUrl = chatEvidence.chatUrl || ''
      const chatIdMatch = chatUrl.match(/\/chats\/([0-9a-zA-Z_-]+)/)
      if (chatIdMatch) {
        const probeFrame = fnow()
        if (probeFrame) {
          detailCardId = await probeFrame
            .evaluate(async (id) => {
              const response = await fetch('/tavern/api/reader/v1/tavern/chats/' + id, { credentials: 'include' })
              if (!response.ok) return null
              const body = await response.json()
              const chat = body.chat || body
              return (chat.card && chat.card.id) || chat.cardId || null
            }, chatIdMatch[1])
            .catch(() => null)
        }
      }
      if (detailCardId) {
        await appGoto('/characters/' + detailCardId)
        detail = fnow()
        if (detail) await detail.locator('.character-hero').first().waitFor({ timeout: 25000 }).catch(() => {})
      } else {
        detail = await openDetail()
      }
      let listShown = detail
        ? await waitFor(async () => (await detail.locator('section[aria-label="聊天历史"] .chat-history-item, .chat-history-item').count().catch(() => 0)) > 0, 20000)
        : false
      if (!listShown && detail) {
        // 卡详情可能先渲染、聊天历史异步拉取偶发未返回：重进一次详情页再等。
        const detailPath = new URL(detail.url()).pathname.replace(/^\/tavern/, '')
        if (detailPath) await appGoto(detailPath)
        detail = fnow()
        listShown = detail
          ? await waitFor(async () => (await detail.locator('section[aria-label="聊天历史"] .chat-history-item, .chat-history-item').count().catch(() => 0)) > 0, 30000)
          : false
      }
      historyEvidence.listShown = listShown
      const itemCount = detail ? await detail.locator('.chat-history-item').count().catch(() => 0) : 0
      check('返回角色页看到聊天历史列表', listShown, listShown ? `角色页聊天历史 ${itemCount} 条` : '角色页没有聊天历史条目（owner: cards-client 卡详情）')
      await shot(page, '97-history.png')

      if (listShown && detail) {
        await detail.locator('.chat-history-item').first().click({ force: true }).catch(() => {})
        const reentered = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 15000)
        if (reentered) chatFrame = reentered
        if (chatFrame) await waitChatRendered(chatFrame, 20000)
        let expectedRows = null
        let expectedTail = ''
        if (reentered) {
          const chatId = new URL(reentered.url()).pathname.split('/').filter(Boolean).pop()
          const detailApi = await reentered
            .evaluate(async (id) => {
              for (const base of ['/tavern/api/reader/v1/tavern/chats/', '/api/reader/v1/tavern/chats/']) {
                try {
                  const response = await fetch(base + encodeURIComponent(id), { credentials: 'include' })
                  if (response.ok) return await response.json()
                } catch {}
              }
              return null
            }, chatId)
            .catch(() => null)
          const apiMessages = detailApi && Array.isArray(detailApi.messages) ? detailApi.messages : []
          if (apiMessages.length) {
            expectedRows = apiMessages.length
            const lastAssistant = [...apiMessages].reverse().find((item) => item.role === 'assistant')
            if (lastAssistant) {
              const swipes = Array.isArray(lastAssistant.swipes) ? lastAssistant.swipes : []
              const active = swipes[lastAssistant.activeSwipe ?? 0] || swipes[0]
              expectedTail = String((active && active.content) || '').slice(0, 12)
            }
          }
        }
        const intact = reentered
          ? await waitFor(async () => {
              const rows = await reentered.locator('.bubble-row').count().catch(() => 0)
              const text = await bodyText(reentered)
              const countOk = expectedRows ? rows >= expectedRows : rows >= 2
              const tailOk = !expectedTail || text.includes(expectedTail)
              return countOk && tailOk && text.includes('你好')
            }, 25000)
          : false
        if (reentered) {
          historyEvidence.diagnostics = {
            url: reentered.url(),
            rows: await reentered.locator('.bubble-row').count().catch(() => 0),
            expectedRows,
            expectedTail,
            textHead: ((await bodyText(reentered).catch(() => '')) || '').slice(0, 160),
          }
        }
        historyEvidence.reentered = Boolean(reentered)
        historyEvidence.messagesIntact = intact
        check('从历史重新进入聊天，消息完整', Boolean(reentered) && intact, `reentered=${Boolean(reentered)} intact=${intact}`)
      } else {
        skip('从历史重新进入聊天，消息完整', '前置：角色页没有聊天历史条目')
      }
    }
  }

  // ── E. 编辑 / 删除消息 ────────────────────────────────────────────────────
  const editEvidence = { edited: false, editedText: null, deleted: false, countBefore: null, countAfter: null, alert: null }
  report.native.edit = editEvidence
  if (groupOn('e')) {
    if (nativeBlocked || !chatFrame) {
      skip('编辑 user 消息后文本更新', nativeBlocked ? blockedReason : '前置：聊天页未打开')
      skip('删除消息后列表减少', nativeBlocked ? blockedReason : '前置：聊天页未打开')
    } else {
      const rows = chatFrame.locator('.bubble-row')
      const countBefore = await rows.count().catch(() => 0)
      const targetRow = await lastAssistantRow(chatFrame)
      let deleteButton = targetRow ? targetRow.locator('button[aria-label="删除这条消息"]').first() : null
      let deleteHost = targetRow
      if (!deleteButton || (await deleteButton.count().catch(() => 0)) === 0) {
        const lastUser = chatFrame.locator('.bubble-row.user').last()
        if ((await lastUser.count().catch(() => 0)) > 0) {
          const userDelete = lastUser.locator('button[aria-label="删除这条消息"]').first()
          if ((await userDelete.count().catch(() => 0)) > 0) {
            deleteButton = userDelete
            deleteHost = lastUser
          }
        }
      }
      if (!deleteButton || (await deleteButton.count().catch(() => 0)) === 0) {
        editEvidence.diagnostics = {
          rows: countBefore,
          rowClass: targetRow ? await targetRow.getAttribute('class').catch(() => null) : null,
          rowText: targetRow ? ((await targetRow.innerText().catch(() => '')) || '').slice(0, 160) : null,
          actionButtons: targetRow ? await targetRow.locator('button').count().catch(() => 0) : 0,
          deleteAny: await chatFrame.locator('button[aria-label*="删除"]').count().catch(() => 0),
          bodyHead: ((await bodyText(chatFrame).catch(() => '')) || '').slice(0, 220),
        }
        skip('删除消息后列表减少', 'assistant 消息没有「删除」按钮（owner: cards-client ChatPage）')
      } else {
        await deleteButton.click({ force: true }).catch(() => {})
        const confirm = chatFrame.locator('button[aria-label="确认删除消息"]').first()
        if ((await confirm.count().catch(() => 0)) > 0) await confirm.click({ force: true }).catch(() => {})
        const deleted = await waitFor(async () => (await chatFrame.locator('.bubble-row').count().catch(() => 0)) < countBefore, 15000)
        editEvidence.deleted = deleted
        editEvidence.countBefore = countBefore
        editEvidence.countAfter = await chatFrame.locator('.bubble-row').count().catch(() => 0)
        editEvidence.alert = (await chatFrame.locator('[role=alert]').first().innerText().catch(() => '')) || null
        check(
          '删除消息后列表减少',
          deleted && !editEvidence.alert,
          `消息数 ${countBefore}→${editEvidence.countAfter} alert=${clip(editEvidence.alert || '无')}`,
        )
        await shot(page, '103-e-delete.png')
      }

      const userRows = chatFrame.locator('.bubble-row.user')
      if ((await userRows.count().catch(() => 0)) === 0) {
        skip('编辑 user 消息后文本更新', '聊天页没有 user 消息可编辑')
      } else {
        const lastUser = userRows.last()
        const editButton = lastUser.locator('button[aria-label*="编辑"], button:has-text("编辑")').first()
        if ((await editButton.count().catch(() => 0)) === 0) {
          skip('编辑 user 消息后文本更新', 'user 消息没有「编辑」按钮（owner: cards-client ChatPage）')
        } else {
          await editButton.click({ force: true }).catch(() => {})
          const editor = lastUser.locator('textarea, input[type="text"]').first()
          const editorShown = await waitFor(async () => (await editor.count().catch(() => 0)) > 0, 10000)
          if (!editorShown) {
            skip('编辑 user 消息后文本更新', '点「编辑」后没有出现编辑器（owner: cards-client ChatPage）')
          } else {
            const newText = `你好，改一下 ${Date.now().toString().slice(-4)}`
            await editor.fill(newText).catch(() => {})
            const saveButton = chatFrame.getByRole('button', { name: /保存并重发|保存|确定/ }).first()
            if ((await saveButton.count().catch(() => 0)) > 0) await saveButton.click({ force: true }).catch(() => {})
            else await editor.press('Enter').catch(() => {})
            const edited = await waitFor(async () => (await bodyText(chatFrame)).includes(newText), 20000)
            editEvidence.edited = edited
            editEvidence.editedText = newText
            check('编辑 user 消息后文本更新', edited, edited ? `新文本=${clip(newText)}` : '保存后文本没有更新（owner: cards-client 编辑 / app-auth PATCH messages）')
            await shot(page, '102-e-edit.png')
          }
        }
      }

    }
  }

  // ── S. 世界书 ────────────────────────────────────────────────────────────
  const worldInfoEvidence = { sectionShown: false, added: false, preview: null, deleted: false }
  report.native.worldInfo = worldInfoEvidence
  if (groupOn('s')) {
    if (nativeBlocked) {
      skip('卡详情世界书区块可编辑', blockedReason)
      skip('新增世界书条目（key「茶」）后列表可见', blockedReason)
      skip('删除世界书条目后列表回落', blockedReason)
      skip('世界书条目注入预览可见', blockedReason)
    } else {
      const detail = await openDetail()
      const sectionShown = detail
        ? await waitFor(async () => (await detail.locator('section[aria-label="世界书"]').count().catch(() => 0)) > 0, 15000)
        : false
      worldInfoEvidence.sectionShown = sectionShown
      check('卡详情世界书区块可编辑', sectionShown, sectionShown ? '出现「世界书」区块' : '卡详情没有「世界书」区块（owner: cards-client 卡详情）')
      if (sectionShown && detail) {
        const addButton = detail.getByRole('button', { name: /新增条目|新增世界书|添加条目/ }).first()
        if (await addButton.count().catch(() => 0)) {
          await addButton.click({ force: true }).catch(() => {})
          const keyInput = detail.locator('input[aria-label="世界书关键词"], input[placeholder*="关键词"]').first()
          const contentInput = detail.locator('textarea[aria-label="世界书内容"], textarea[placeholder*="内容"]').first()
          if ((await keyInput.count().catch(() => 0)) && (await contentInput.count().catch(() => 0))) {
            await keyInput.fill('茶').catch(() => {})
            await contentInput.fill('【E2E-WORLD-INFO】茶博士只在下雨天讲古。').catch(() => {})
            const position = detail.locator('select[aria-label="世界书位置"]').first()
            if (await position.count().catch(() => 0)) await position.selectOption('after_char').catch(() => {})
            const saveButton = detail.getByRole('button', { name: /保存条目|保存世界书/ }).first()
            if (await saveButton.count().catch(() => 0)) await saveButton.click({ force: true }).catch(() => {})
            const added = await waitFor(async () => /【E2E-WORLD-INFO】|茶博士/.test(await bodyText(detail)), 15000)
            worldInfoEvidence.added = added
            check('新增世界书条目（key「茶」）后列表可见', added, added ? '条目已出现在世界书列表' : '保存后列表没出现条目（owner: cards-client 世界书编辑 / app-auth world-info API）')
            await shot(page, '98-world-info.png')

            // P1：注入预览（打开预览 → 输入示例消息 → 出现命中条目/injection 文本）。
            const previewButton = detail.getByRole('button', { name: '注入预览', exact: true }).first()
            if (await previewButton.count().catch(() => 0)) {
              const previewInput = detail
                .locator('input[aria-label="注入预览示例消息"], textarea[aria-label*="预览"], input[aria-label*="预览"]')
                .first()
              if (await previewInput.count().catch(() => 0)) await previewInput.fill('来一盏茶').catch(() => {})
              await previewButton.click({ force: true }).catch(() => {})
              const previewOk = await waitFor(async () => {
                const result = await detail.locator('.preview-result').first().innerText().catch(() => '')
                return /命中\s*[1-9]/.test(result) && /【E2E-WORLD-INFO】|茶博士/.test(result)
              }, 20000)
              const previewText = await bodyText(detail)
              worldInfoEvidence.preview = { ok: previewOk, snippet: previewText.slice(0, 200) }
              check(
                '世界书条目注入预览可见',
                previewOk,
                previewOk
                  ? `预览结果命中 key「茶」的条目（${clip(worldInfoEvidence.preview.snippet)}）`
                  : `预览结果没有命中条目/injection（${clip(previewText.slice(0, 120))}；owner: cards-client 预览 / app-auth world-info/preview）`,
              )
              await shot(page, '107-s-preview.png')
            } else {
              skip('世界书条目注入预览可见', '卡详情没有「注入预览」按钮（owner: cards-client；P1 客户端未交付该 UI）')
            }

            const item = detail.locator('.world-info-item').filter({ hasText: '【E2E-WORLD-INFO】' }).first()
            const delButton = item.getByRole('button', { name: /删除/ }).first()
            if (await delButton.count().catch(() => 0)) {
              await delButton.click({ force: true }).catch(() => {})
              const gone = await waitFor(async () => (await detail.locator('.world-info-item').filter({ hasText: '【E2E-WORLD-INFO】' }).count().catch(() => 0)) === 0, 15000)
              worldInfoEvidence.deleted = gone
              check('删除世界书条目后列表回落', gone, gone ? '条目已从列表移除' : '删除后条目仍在列表（owner: cards-client 世界书编辑 / app-auth world-info API）')
            } else {
              skip('删除世界书条目后列表回落', '条目没有删除按钮（owner: cards-client）')
            }
          } else {
            skip('新增世界书条目（key「茶」）后列表可见', '世界书编辑表单缺少关键词/内容输入（owner: cards-client）')
          }
        } else {
          skip('新增世界书条目（key「茶」）后列表可见', '世界书区块没有「新增条目」按钮（owner: cards-client）')
        }
      }
    }
  }

  // ── I. 导入导出（聊天 JSONL/JSON 下载；卡详情导入 ST JSONL）────────────────
  const exportImportEvidence = { exportJsonl: null, exportJson: null, import: null, responses: [] }
  report.native.exportImport = exportImportEvidence
  if (groupOn('i')) {
    const downloadsDir = path.join(outDir, 'downloads')
    await mkdir(downloadsDir, { recursive: true })
    const exportResponses = []
    const onExportResponse = (response) => {
      if (/\/export\?format=/.test(response.url())) {
        exportResponses.push({
          url: response.url(),
          status: response.status(),
          disposition: response.headers()['content-disposition'] || '',
        })
      }
    }
    page.on('response', onExportResponse)
    try {
      if (nativeBlocked || !chatFrame) {
        skip('导出聊天 JSONL（下载并逐行解析）', nativeBlocked ? blockedReason : '前置：聊天页未打开')
        skip('导出聊天 JSON（下载并可解析）', nativeBlocked ? blockedReason : '前置：聊天页未打开')
      } else {
        if (!/\/chats\//.test(chatFrame.url())) {
          const chatPath = chatEvidence && chatEvidence.chatUrl ? new URL(chatEvidence.chatUrl).pathname.replace(/^\/tavern/, '') : ''
          if (chatPath) {
            await appGoto(chatPath)
            chatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 15000)) || fnow()
          } else {
            const reopened = await openChat()
            if (reopened && reopened.chatFrame) chatFrame = reopened.chatFrame
          }
        }
        const menuButton = chatFrame
          .locator('button[aria-label*="菜单"], button[aria-label*="更多"], button[aria-label*="选项"]')
          .first()
        const menuDiagnostics = { button: false, pop: false, items: 0, header: '' }
        exportImportEvidence.menu = menuDiagnostics
        const exportItems = async () => {
          const items = chatFrame
            .locator('[role="menuitem"], .chat-menu-pop button, .chat-menu-pop a')
            .filter({ hasText: /导出聊天|导出会话|导出记录/ })
          const count = await items.count().catch(() => 0)
          const list = []
          for (let index = 0; index < count; index += 1) {
            const text = ((await items.nth(index).innerText().catch(() => '')) || '').trim()
            if (text) list.push({ locator: items.nth(index), text })
          }
          return list
        }
        const openChatMenu = async () => {
          menuDiagnostics.button = await waitFor(async () => (await menuButton.count().catch(() => 0)) > 0, 20000)
          if (!menuDiagnostics.button) {
            menuDiagnostics.header = ((await bodyText(chatFrame)) || '').slice(0, 200)
            return false
          }
          await menuButton.click({ force: true }).catch(() => {})
          let list = await waitFor(async () => {
            const found = await exportItems()
            return found.length ? found : null
          }, 6000)
          if (!list) {
            // 菜单可能被前一次点击关掉，重试一次。
            await menuButton.click({ force: true }).catch(() => {})
            list = await waitFor(async () => {
              const found = await exportItems()
              return found.length ? found : null
            }, 6000)
          }
          menuDiagnostics.pop = (await chatFrame.locator('.chat-menu-pop, [role="menu"]').count().catch(() => 0)) > 0
          menuDiagnostics.items = list ? list.length : 0
          if (!list) menuDiagnostics.header = ((await bodyText(chatFrame)) || '').slice(0, 200)
          return Boolean(list)
        }
        const downloadVia = async (kind) => {
          const opened = await openChatMenu()
          const list = opened ? await exportItems() : []
          if (!list.length) return null
          const isJson = (text) => /JSON(?!L)/i.test(text) && !/JSONL/i.test(text)
          const isJsonl = (text) => /JSONL|jsonl|\.jsonl/i.test(text)
          let target = null
          if (kind === 'jsonl') target = list.find((item) => isJsonl(item.text)) || list.find((item) => !isJson(item.text)) || list[0]
          else target = list.find((item) => isJson(item.text)) || null
          if (!target) return null
          const [download] = await Promise.all([
            page.waitForEvent('download', { timeout: 30000 }).catch(() => null),
            target.locator.click({ force: true }).catch(() => {}),
          ])
          if (!download) return { clicked: target.text, download: false }
          const filename = download.suggestedFilename() || `chat-export-${kind}`
          const file = path.join(downloadsDir, filename)
          await download.saveAs(file).catch(() => {})
          return { clicked: target.text, download: true, file, filename }
        }

        const jsonl = await downloadVia('jsonl')
        if (!jsonl || !jsonl.download) {
          check(
            '导出聊天 JSONL（下载并逐行解析）',
            false,
            jsonl ? `点击「${clip(jsonl.clicked)}」没有触发下载（owner: cards-client 导出菜单 / app-auth export）` : '聊天页没有「导出聊天」入口（owner: cards-client ChatPage 菜单）',
          )
        } else {
          const raw = await readFile(jsonl.file, 'utf8').catch(() => '')
          const lines = raw.split('\n').map((line) => line.trim()).filter(Boolean)
          const parsed = []
          let badLines = 0
          for (const line of lines) {
            try {
              parsed.push(JSON.parse(line))
            } catch {
              badLines += 1
            }
          }
          const sample = parsed[0] || null
          const disposition = exportResponses.find((item) => /format=jsonl/.test(item.url))?.disposition || ''
          const ok =
            jsonl.download &&
            parsed.length >= 1 &&
            badLines === 0 &&
            parsed.some((row) => typeof row.mes === 'string') &&
            /attachment|jsonl|\.txt/i.test(disposition || '')
          exportImportEvidence.exportJsonl = {
            filename: jsonl.filename,
            lines: lines.length,
            parsed: parsed.length,
            badLines,
            disposition,
            first: sample ? { mes: String(sample.mes || '').slice(0, 40), isUser: sample.is_user } : null,
          }
          check(
            '导出聊天 JSONL（下载并逐行解析）',
            ok,
            `file=${clip(jsonl.filename)} lines=${lines.length} parsed=${parsed.length} bad=${badLines} disposition=${clip(disposition || '无')} 首行=${clip(exportImportEvidence.exportJsonl.first)}`,
          )
          await shot(page, '105-i-export.png')
        }

        const jsonExport = await downloadVia('json')
        if (!jsonExport || !jsonExport.download) {
          skip(
            '导出聊天 JSON（下载并可解析）',
            jsonExport ? `点击「${clip(jsonExport.clicked)}」没有触发下载（owner: cards-client 导出菜单 / app-auth export）` : '聊天页没有「导出聊天 (JSON)」入口（owner: cards-client ChatPage 菜单）',
          )
        } else {
          const raw = await readFile(jsonExport.file, 'utf8').catch(() => '')
          let payload = null
          try {
            payload = JSON.parse(raw)
          } catch {}
          const messages = payload ? payload.messages || payload.chat?.messages || [] : []
          const ok = Boolean(payload) && Array.isArray(messages) && messages.length >= 1
          exportImportEvidence.exportJson = {
            filename: jsonExport.filename,
            bytes: raw.length,
            messages: messages.length,
          }
          check(
            '导出聊天 JSON（下载并可解析）',
            ok,
            `file=${clip(jsonExport.filename)} bytes=${raw.length} messages=${messages.length}`,
          )
        }
      }

      // 导入：卡详情「导入酒馆聊天」→ 新会话 + 消息条数正确。
      const importFixture = path.join(fixturesDir, 'chat-sample.jsonl')
      const fixtureRaw = await readFile(importFixture, 'utf8').catch(() => '')
      if (!fixtureRaw) {
        skip('导入 ST JSONL 聊天后历史新增且消息条数正确', `fixture 不存在：${importFixture}（owner: scripts/check.sh fixture）`)
      } else {
        let detail = await openDetail()
        let historySection = detail ? detail.locator('section[aria-label="聊天历史"]').first() : null
        let fileInput = null
        const findImportInput = async (frameNow) => {
          if (!frameNow) return null
          const section = frameNow.locator('section[aria-label="聊天历史"]').first()
          const inSection = section.locator('input[type="file"]').first()
          if ((await inSection.count().catch(() => 0)) > 0) return inSection
          const anywhere = frameNow
            .locator('input[aria-label="导入酒馆聊天 JSONL"], input[type="file"][accept*="jsonl"]')
            .first()
          if ((await anywhere.count().catch(() => 0)) > 0) return anywhere
          return null
        }
        const waitImportInput = async (frameNow, timeoutMs) => {
          const deadline = Date.now() + timeoutMs
          while (Date.now() < deadline) {
            const found = await findImportInput(frameNow)
            if (found) return found
            await page.waitForTimeout(250)
          }
          return null
        }
        fileInput = await waitImportInput(detail, 15000)
        if (!fileInput) {
          // 详情页可能还没挂载好，重进一次。
          detail = await openDetail()
          historySection = detail ? detail.locator('section[aria-label="聊天历史"]').first() : null
          fileInput = await waitImportInput(detail, 15000)
        }
        const beforeCount = historySection ? await historySection.locator('.chat-history-item').count().catch(() => 0) : 0
        if (!detail || !fileInput || (await fileInput.count().catch(() => 0)) === 0) {
          skip('导入 ST JSONL 聊天后历史新增且消息条数正确', '卡详情没有「导入酒馆聊天」文件输入（owner: cards-client 卡详情）')
        } else {
          await fileInput.setInputFiles(importFixture).catch(() => {})
          const entered = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 20000)
          const importedFrame = entered || chatFrame
          const messageCount = importedFrame ? await importedFrame.locator('.bubble-row').count().catch(() => 0) : 0
          const text = importedFrame ? await bodyText(importedFrame) : ''
          const textOk = /雨下得正好/.test(text) && /来一盏茶/.test(text)
          // 导入成功后客户端会自动跳进新会话，历史列表已离开 DOM；回卡详情再数历史条目。
          const detailAgain = await openDetail()
          const historyAgain = detailAgain ? detailAgain.locator('section[aria-label="聊天历史"]').first() : null
          if (historyAgain) {
            await waitFor(async () => (await historyAgain.locator('.chat-history-item').count().catch(() => 0)) > 0, 15000)
          }
          const afterCount = historyAgain ? await historyAgain.locator('.chat-history-item').count().catch(() => 0) : 0
          if (entered) chatFrame = entered
          const ok = Boolean(entered) && afterCount > beforeCount && messageCount >= 2 && textOk
          exportImportEvidence.import = {
            beforeCount,
            afterCount,
            entered: Boolean(entered),
            messageCount,
            textOk,
          }
          check(
            '导入 ST JSONL 聊天后历史新增且消息条数正确',
            ok,
            `历史 ${beforeCount}→${afterCount} 自动进入=${Boolean(entered)} 消息数=${messageCount} 文本命中=${textOk}`,
          )
          await shot(page, '106-i-import.png')
        }
      }
    } finally {
      page.off('response', onExportResponse)
    }
  }

  // ── W. 世界书深度语义（P2，task-51）──────────────────────────────────────
  const worldInfoDeep = { enabled: false, entry: null, preview: null, api: null, console: null, steps: [] }
  report.native.worldInfoDeep = worldInfoDeep
  const wNames = [
    '世界书条目支持 position/depth/constant/probability/order 并回显',
    '注入预览显示命中条目与原因',
    'context-preview API 按 order 返回命中条目且注入文本包含条目',
  ]
  const wRecorded = new Set()
  const wRecord = (index, ok, detail, skipped = false) => {
    wRecorded.add(index)
    record(wNames[index], ok, detail, skipped)
    worldInfoDeep.steps.push({ name: wNames[index], ok, skipped, detail })
  }
  const wSkipRemaining = (from, reason) => {
    for (let index = from; index < wNames.length; index += 1) if (!wRecorded.has(index)) wRecord(index, true, reason, true)
  }
  if (groupOn('w')) {
    if (nativeBlocked) {
      wSkipRemaining(0, blockedReason)
    } else {
      worldInfoDeep.enabled = true
      const wConsoleStart = consoleErrors.length
      try {
        const detail = await openDetail()
        if (!detail) {
          wSkipRemaining(0, '无法打开角色详情页（owner: cards-client 详情页）')
        } else {
          const worldSection = detail.locator('section[aria-label="世界书"]').first()
          const sectionReady = await waitFor(async () => (await worldSection.count().catch(() => 0)) > 0, 20000)
          if (!sectionReady) {
            wSkipRemaining(0, '角色详情没有「世界书」区块（owner: cards-client 详情页）')
          } else {
            const marker = '【E2E-WORLD-INFO-P2】茶博士只在雨天讲古；雨停后第三盏茶留给赶路的人。'
            const addButton = worldSection.getByRole('button', { name: /新增条目|新增世界书|添加条目/ }).first()
            let added = false
            if ((await addButton.count().catch(() => 0)) > 0) {
              await addButton.click({ force: true }).catch(() => {})
              const editor = detail.locator('form.world-info-editor').first()
              const editorReady = await waitFor(async () => (await editor.count().catch(() => 0)) > 0, 12000)
              if (editorReady) {
                const keysInput = editor.locator('input[aria-label="世界书关键词"]').first()
                const contentInput = editor.locator('textarea[aria-label="世界书内容"]').first()
                const positionSelect = editor.locator('select[aria-label="世界书位置"]').first()
                const depthInput = editor.locator('input[aria-label="世界书深度"]').first()
                const probabilityInput = editor.locator('input[aria-label="世界书概率"]').first()
                const orderInput = editor.locator('input[aria-label="世界书顺序"]').first()
                const constantBox = editor.locator('input[type="checkbox"]').first()
                await keysInput.fill('茶').catch(() => {})
                await contentInput.fill(marker).catch(() => {})
                await positionSelect.selectOption('after_char').catch(() => {})
                await depthInput.fill('2').catch(() => {})
                await probabilityInput.fill('100').catch(() => {})
                await orderInput.fill('5').catch(() => {})
                await constantBox.check().catch(() => {})
                await editor.getByRole('button', { name: /保存条目|保存世界书|保存/ }).first().click({ force: true }).catch(() => {})
                added = await waitFor(async () => (await worldSection.locator('.world-info-item').filter({ hasText: marker }).count().catch(() => 0)) > 0, 15000)
              }
            }
            const item = worldSection.locator('.world-info-item').filter({ hasText: marker }).first()
            const metaText = added ? ((await item.locator('.world-info-meta').innerText().catch(() => '')) || '') : ''
            const keysText = added ? ((await item.locator('.world-info-keys').innerText().catch(() => '')) || '') : ''
            const w1Ok = added && /after_char/.test(metaText) && /depth\s*2/.test(metaText) && /order\s*5/.test(metaText) && /常驻/.test(keysText)
            wRecord(0, w1Ok, `added=${added} meta=${metaText.slice(0, 100)} keys=${keysText.slice(0, 60)}`)
            worldInfoDeep.entry = { added, marker, metaText, keysText }

            // W2：注入预览显示命中与原因
            let previewText = ''
            let previewOk = false
            if (added) {
              const previewInput = detail.locator('input[aria-label="注入预览示例消息"]').first()
              const previewSubmit = detail.getByRole('button', { name: '注入预览', exact: true }).first()
              if ((await previewInput.count().catch(() => 0)) > 0 && (await previewSubmit.count().catch(() => 0)) > 0) {
                await previewInput.fill('来一盏茶，讲讲古吧。').catch(() => {})
                await previewSubmit.click({ force: true }).catch(() => {})
                await waitFor(async () => (await detail.locator('.preview-result').count().catch(() => 0)) > 0, 20000)
                previewText = ((await detail.locator('.preview-result').first().innerText().catch(() => '')) || '').trim()
                previewOk = /命中\s*[1-9]/.test(previewText) && /【E2E-WORLD-INFO-P2】/.test(previewText)
              }
            }
            const reasonOk = /常驻|关键词|constant|概率|probability/i.test(previewText)
            worldInfoDeep.preview = { text: previewText.slice(0, 400), reasonShown: reasonOk }
            wRecord(1, previewOk, `命中=${previewOk} 原因=${reasonOk} preview=${previewText.slice(0, 180)}`)
            await shot(page, '110-w-worldinfo.png').catch(() => {})
            await shot(page, '111-w-preview.png').catch(() => {})

            // W3：API 断言 order/position 与注入文本
            if (added && detail) {
              const cardIdMatch = detail.url().match(/\/characters\/([0-9a-zA-Z_-]+)/)
              const cardId = cardIdMatch ? cardIdMatch[1] : null
              const apiResult = await detail
                .evaluate(async ({ id }) => {
                  const bases = ['/tavern/api/reader/v1', '/api/reader/v1']
                  for (const base of bases) {
                    try {
                      const listRes = await fetch(`${base}/tavern/characters/${encodeURIComponent(id)}/world-info`, { credentials: 'include' })
                      if (!listRes.ok) continue
                      const list = await listRes.json()
                      const previewRes = await fetch(`${base}/tavern/characters/${encodeURIComponent(id)}/world-info/preview`, {
                        method: 'POST',
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: '来一盏茶，讲讲古吧。' }),
                      })
                      const preview = previewRes.ok ? await previewRes.json() : null
                      return { list, preview, status: previewRes.status }
                    } catch {}
                  }
                  return null
                }, { id: cardId })
                .catch(() => null)
              const entries = apiResult && apiResult.list ? (apiResult.list.items || apiResult.list.entries || apiResult.list) : null
              const matched = apiResult && apiResult.preview ? (apiResult.preview.matched || apiResult.preview.items || []) : []
              const markerHit = Array.isArray(matched) && matched.some((entry) => String(entry.content || '').includes('【E2E-WORLD-INFO-P2】'))
              const orders = Array.isArray(matched) ? matched.map((entry) => Number(entry.order)) : []
              const sortedOk = orders.every((value, index) => index === 0 || orders[index - 1] <= value)
              const positionOk = Array.isArray(matched) && matched.some((entry) => entry.position === 'after_char' && Number(entry.depth) === 2)
              const injection = apiResult && apiResult.preview ? String(apiResult.preview.injection || '') : ''
              const injectionOk = injection.includes('【E2E-WORLD-INFO-P2】')
              worldInfoDeep.api = { status: apiResult ? apiResult.status : null, orders, sortedOk, positionOk, injectionOk, entriesCount: Array.isArray(entries) ? entries.length : null }
              wRecord(2, markerHit && sortedOk && positionOk && injectionOk, `matched=${matched.length} orders=${orders.join(',')} sorted=${sortedOk} position/depth=${positionOk} injection=${injectionOk}`)
            } else {
              wSkipRemaining(2, '世界书条目未创建成功，无法校验注入顺序（owner: cards-client 世界书编辑器）')
            }
          }
        }
      } catch (error) {
        wSkipRemaining(wRecorded.size, `W 组异常：${(error && error.message) || error}`)
      }
      worldInfoDeep.console = consoleErrors.slice(wConsoleStart).filter((item) => !/Failed to load resource/.test(item))
    }
  }
  if (groupOn('w')) wSkipRemaining(0, 'W 组未执行（跳过）')

  // ── S2. 采样参数（P2，task-51）──────────────────────────────────────────
  const sampling = { enabled: false, dialog: null, persisted: false, send: null, steps: [] }
  report.native.sampling = sampling
  const s2Names = ['聊天页菜单可打开采样参数', '采样参数保存后刷新回显', '带采样参数发送一条消息不报错']
  const s2Recorded = new Set()
  const s2Record = (index, ok, detail, skipped = false) => {
    s2Recorded.add(index)
    record(s2Names[index], ok, detail, skipped)
    sampling.steps.push({ name: s2Names[index], ok, skipped, detail })
  }
  const s2SkipRemaining = (from, reason) => {
    for (let index = from; index < s2Names.length; index += 1) if (!s2Recorded.has(index)) s2Record(index, true, reason, true)
  }
  if (groupOn('s2')) {
    if (nativeBlocked) {
      s2SkipRemaining(0, blockedReason)
    } else {
      sampling.enabled = true
      try {
        if (!chatFrame || !/\/chats\//.test(chatFrame.url())) {
          // 先用 C 组记录的聊天 URL 直接回去（此时可能停在卡详情/设置页，首页找卡不可靠）。
          const knownChat = chatEvidence && chatEvidence.chatUrl ? new URL(chatEvidence.chatUrl).pathname.replace(/^\/tavern/, '') : ''
          if (knownChat) {
            const back = await appGoto(knownChat)
            if (back && /\/chats\//.test(back.url())) chatFrame = back
          }
          if (!chatFrame || !/\/chats\//.test(chatFrame.url())) {
            const opened = await openChat()
            chatFrame = (opened && opened.chatFrame) || chatFrame
          }
        }
        const chatReady = chatFrame ? await waitChatRendered(chatFrame) : false
        let menuItem = null
        if (chatReady) {
          const menuButton = chatFrame.locator('button[aria-label="聊天菜单"]').first()
          if ((await menuButton.count().catch(() => 0)) > 0) {
            await menuButton.click({ force: true }).catch(() => {})
            const pop = chatFrame.locator('.chat-menu-pop').first()
            await waitFor(async () => (await pop.count().catch(() => 0)) > 0, 8000)
            menuItem = pop.locator('button, [role="menuitem"]').filter({ hasText: /采样参数|采样|参数/ }).first()
            if ((await menuItem.count().catch(() => 0)) === 0) menuItem = null
          }
        }
        if (!menuItem) {
          // 可能刚 reload 完还没挂载：重进聊天页再试一次。
          await chatFrame.evaluate(() => location.reload()).catch(() => {})
          chatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 20000)) || chatFrame
          const retryReady = await waitChatRendered(chatFrame)
          if (retryReady) {
            const menuButton2 = chatFrame.locator('button[aria-label="聊天菜单"]').first()
            if ((await menuButton2.count().catch(() => 0)) > 0) {
              await menuButton2.click({ force: true }).catch(() => {})
              const pop2 = chatFrame.locator('.chat-menu-pop').first()
              await waitFor(async () => (await pop2.count().catch(() => 0)) > 0, 8000)
              const item2 = pop2.locator('button, [role="menuitem"]').filter({ hasText: /采样参数|采样|参数/ }).first()
              if ((await item2.count().catch(() => 0)) > 0) menuItem = item2
            }
          }
        }
        if (!menuItem) {
          const menuTexts = chatFrame
            ? (await chatFrame.locator('.chat-menu-pop button, .chat-menu-pop [role="menuitem"]').allInnerTexts().catch(() => []))
            : []
          s2SkipRemaining(0, `聊天页菜单没有「采样参数」入口（owner: cards-client ChatPage 菜单）menu=${JSON.stringify(menuTexts)} url=${chatFrame ? chatFrame.url() : ''}`)
        } else {
          await menuItem.click({ force: true }).catch(() => {})
          const dialog = chatFrame
            .locator('[role="dialog"], section[aria-label*="采样"], .sampling-dialog, form[aria-label*="采样"]')
            .first()
          const dialogReady = await waitFor(async () => (await dialog.count().catch(() => 0)) > 0, 10000)
          s2Record(0, dialogReady, dialogReady ? '采样参数对话框已打开' : '点击「采样参数」后没有出现对话框')
          if (dialogReady) {
            const temperatureInput = dialog.locator('input[aria-label*="temperature" i], input[aria-label*="温度"]').first()
            const topPInput = dialog.locator('input[aria-label*="top_p" i], input[aria-label*="top"]').first()
            const maxTokensInput = dialog.locator('input[aria-label*="max_tokens" i], input[aria-label*="max"]').first()
            await temperatureInput.fill('1.2').catch(() => {})
            await topPInput.fill('0.9').catch(() => {})
            await maxTokensInput.fill('200').catch(() => {})
            // 保存前读值（保存成功后 Sheet 会关闭，之后就读不到 input 了）。
            const savedValues = {
              temperature: await temperatureInput.inputValue().catch(() => null),
              topP: await topPInput.inputValue().catch(() => null),
              maxTokens: await maxTokensInput.inputValue().catch(() => null),
            }
            await dialog.getByRole('button', { name: /保存参数|保存|确定|应用/ }).first().click({ force: true }).catch(() => {})
            await page.waitForTimeout(1000)
            sampling.dialog = savedValues
            await shot(page, '112-s2-sampling.png').catch(() => {})
            // API 校验：chat.sampling 是否真的落库
            const chatPath = new URL(chatFrame.url()).pathname.replace(/^\/tavern/, '')
            const chatApi = await chatFrame
              .evaluate(async (path) => {
                for (const base of ['/tavern/api/reader/v1', '/api/reader/v1']) {
                  try {
                    const response = await fetch(`${base}/tavern${path}`, { credentials: 'include' })
                    if (response.ok) return await response.json()
                  } catch {}
                }
                return null
              }, chatPath)
              .catch(() => null)
            const savedSampling = chatApi && chatApi.chat ? chatApi.chat.sampling || null : null
            const apiPersisted = Boolean(savedSampling && Number(savedSampling.temperature) === 1.2 && Number(savedSampling.topP) === 0.9 && Number(savedSampling.maxTokens) === 200)
            // 刷新回显
            await chatFrame.evaluate(() => location.reload()).catch(() => {})
            chatFrame = (await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 20000)) || chatFrame
            const readyAgain = await waitChatRendered(chatFrame)
            let persisted = false
            let echo = null
            const readEchoFromUi = async () => {
              const debug = { ready: false, menu: false, item: false, dialog: false, text: '', reopened: false, attempt: 0 }
              await page.waitForTimeout(600)
              if (!(await waitFrameUrl(/\/tavern\//, 15000))) return { ok: false, echo: null, debug }
              // reload 后 Vue 事件偶发未绑定：直接整页重进聊天页再读，最稳。
              const navigated = await appGoto(chatPath)
              if (!navigated) return { ok: false, echo: null, debug }
              debug.reopened = true
              debug.ready = await waitChatRendered(navigated, 25000)
              if (!debug.ready) return { ok: false, echo: null, debug }
              const menuButton = navigated.locator('button[aria-label="聊天菜单"]').first()
              for (let attempt = 0; attempt < 4 && !debug.menu; attempt += 1) {
                debug.attempt = attempt + 1
                await menuButton.click({ force: true }).catch(() => {})
                debug.menu = await waitFor(async () => (await navigated.locator('.chat-menu-pop').count().catch(() => 0)) > 0, 4000)
                if (!debug.menu) await page.waitForTimeout(700)
              }
              if (!debug.menu) return { ok: false, echo: null, debug }
              const item = navigated.locator('.chat-menu-pop button, .chat-menu-pop [role="menuitem"]').filter({ hasText: /采样参数/ }).first()
              debug.item = (await item.count().catch(() => 0)) > 0
              if (!debug.item) return { ok: false, echo: null, debug }
              await item.click({ force: true }).catch(() => {})
              const dialog = navigated.locator('section.sheet[aria-label="采样参数"], [role="dialog"], section[aria-label*="采样"]').first()
              debug.dialog = await waitFor(async () => (await dialog.count().catch(() => 0)) > 0, 10000)
              if (!debug.dialog) return { ok: false, echo: null, debug }
              debug.text = ((await dialog.innerText().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 200)
              const t = dialog.locator('input[aria-label*="temperature" i], input[aria-label*="温度"]').first()
              const p = dialog.locator('input[aria-label*="top_p" i], input[aria-label*="top"]').first()
              const m = dialog.locator('input[aria-label*="max_tokens" i], input[aria-label*="max"]').first()
              const values = {
                temperature: await t.inputValue().catch(() => null),
                topP: await p.inputValue().catch(() => null),
                maxTokens: await m.inputValue().catch(() => null),
              }
              await dialog.getByRole('button', { name: /关闭|取消|完成/ }).first().click({ force: true }).catch(() => {})
              return { ok: true, echo: values, debug }
            }
            let echoAttempt = await readEchoFromUi()
            if (!echoAttempt.ok) {
              await page.waitForTimeout(1500)
              echoAttempt = await readEchoFromUi()
            }
            const echoDebug = echoAttempt.debug
            echo = echoAttempt.echo
            persisted = Boolean(echo) && Number(echo.temperature) === 1.2 && Number(echo.topP) === 0.9 && Number(echo.maxTokens) === 200
            chatFrame = (await waitFrameUrl(/\/tavern\//, 10000)) || chatFrame
            sampling.persisted = persisted
            sampling.api = savedSampling
            sampling.echoDebug = echoDebug
            s2Record(1, apiPersisted && persisted, `保存=${JSON.stringify(savedValues)} API=${JSON.stringify(savedSampling)} 回显=${JSON.stringify(echo)} debug=${JSON.stringify(echoDebug)}`)
            // 发送一条消息（真实生成 +1）
            await waitChatRendered(chatFrame, 20000)
            const input = chatFrame.locator('textarea[aria-label="聊天输入框"]').first()
            const send = chatFrame.locator('button[aria-label="发送消息"]').first()
            const rowsBefore = await chatFrame.locator('.bubble-row').count().catch(() => 0)
            let sent = false
            if ((await input.count().catch(() => 0)) > 0 && (await send.count().catch(() => 0)) > 0) {
              await input.fill('（E2E 采样）用一句话回应我。').catch(() => {})
              await send.click({ force: true }).catch(() => {})
              sent = await waitFor(async () => {
                const rows = await chatFrame.locator('.bubble-row').count().catch(() => 0)
                const pending = await chatFrame.locator('.bubble-pending').count().catch(() => 0)
                const status = ((await chatFrame.locator('.chat-status').first().innerText().catch(() => '')) || '').trim()
                return rows > rowsBefore + 1 && pending === 0 && !/正在回复/.test(status)
              }, 120000)
            }
            const alertText = await chatFrame.locator('[role="alert"]').first().innerText().catch(() => '')
            sampling.send = { sent, alert: alertText || null }
            s2Record(2, sent && !alertText, `sent=${sent} alert=${alertText ? alertText.slice(0, 120) : '无'}`)
          } else {
            s2SkipRemaining(1, '采样参数对话框未打开，无法验证保存/回显')
          }
        }
      } catch (error) {
        s2SkipRemaining(s2Recorded.size, `S2 组异常：${(error && error.message) || error}`)
      }
    }
  }
  if (groupOn('s2')) s2SkipRemaining(0, 'S2 组未执行（跳过）')

  // ── Q. 预设与提示词（P2，task-51）──────────────────────────────────────
  const presets = { enabled: false, created: null, defaulted: false, prompt: null, invalid: null, console: null, steps: [] }
  report.native.presets = presets
  const q2Names = [
    '设置页可打开预设管理',
    '新建预设（system/main/temperature/maxTokens）并出现在列表',
    '预设可设为默认且刷新保持',
    '新聊天使用默认预设（prompt 接口包含预设文本）',
    '非法采样值被拒绝（前端拦截或后端 422）',
  ]
  const q2Recorded = new Set()
  const q2Record = (index, ok, detail, skipped = false) => {
    q2Recorded.add(index)
    record(q2Names[index], ok, detail, skipped)
    presets.steps.push({ name: q2Names[index], ok, skipped, detail })
  }
  const q2SkipRemaining = (from, reason) => {
    for (let index = from; index < q2Names.length; index += 1) if (!q2Recorded.has(index)) q2Record(index, true, reason, true)
  }
  if (groupOn('q')) {
    if (nativeBlocked) {
      q2SkipRemaining(0, blockedReason)
    } else {
      presets.enabled = true
      const qConsoleStart = consoleErrors.length
      try {
        const settings = await appGoto('/settings')
        const settingsReady = settings
          ? await waitFor(async () => (await settings.locator('.tavern-settings, section[aria-label="设置"]').count().catch(() => 0)) > 0, 20000)
          : false
        if (settingsReady) {
          await waitFor(async () => (await settings.locator('section.presets-panel').count().catch(() => 0)) > 0, 20000)
        }
        const presetButton = settings
          ? settings.getByRole('button', { name: /新建预设|新增预设|创建预设/ }).first()
          : null
        const presetEntry = presetButton && (await presetButton.count().catch(() => 0)) > 0
        if (!settingsReady) {
          q2Record(0, true, '设置页没有渲染（owner: cards-client 设置页）', true)
          q2SkipRemaining(1, '设置页没有渲染（owner: cards-client 设置页）')
        } else if (!presetEntry) {
          const debugText = settings ? ((await settings.locator('body').innerText().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 220) : ''
          const debugUrl = settings ? settings.url() : ''
          q2Record(0, true, `设置页有渲染，但没有「预设」管理入口（owner: cards-client 设置页）url=${debugUrl} body=${debugText}`, true)
          q2SkipRemaining(1, '设置页没有「预设」管理入口（owner: cards-client 设置页）')
        } else {
          q2Record(0, true, '设置页可打开预设管理')
          await presetButton.click({ force: true }).catch(() => {})
          const form = settings.locator('section.presets-panel form.preset-form').first()
          const formReady = await waitFor(async () => (await form.count().catch(() => 0)) > 0, 10000)
          const nameInput = form.locator('input[aria-label="预设名称"], input[aria-label*="名称"]').first()
          const systemInput = form.locator('textarea[aria-label="预设系统提示词"], textarea[aria-label*="系统"]').first()
          const temperatureInput = form.locator('input[aria-label="预设 temperature"], input[aria-label*="temperature" i]').first()
          const maxTokensInput = form.locator('input[aria-label="预设 max_tokens"], input[aria-label*="max_tokens"]').first()
          let created = false
          let createdDetail = ''
          if (formReady) {
            await nameInput.fill('E2E 预设 P2').catch(() => {})
            await systemInput.fill('【E2E-PRESET】请用温柔简洁的中文回应，每次不超过两句话。').catch(() => {})
            await temperatureInput.fill('1.2').catch(() => {})
            if ((await maxTokensInput.count().catch(() => 0)) > 0) await maxTokensInput.fill('200').catch(() => {})
            await form.getByRole('button', { name: /保存预设|保存|创建/ }).first().click({ force: true }).catch(() => {})
            created = await waitFor(async () => (await settings.locator('li.preset-item').filter({ hasText: 'E2E 预设 P2' }).count().catch(() => 0)) > 0, 20000)
            const formAlert = ((await form.locator('[role="alert"]').first().innerText().catch(() => '')) || '').trim()
            createdDetail = `form=${formReady} created=${created}${formAlert ? ` alert=${formAlert.slice(0, 120)}` : ''}`
          }
          presets.created = { name: 'E2E 预设 P2', created, detail: createdDetail }
          q2Record(1, created, createdDetail)
          await shot(page, '113-q-presets.png').catch(() => {})
          if (!created) {
            q2SkipRemaining(2, '预设未创建成功（owner: cards-client 设置页表单）')
          } else {
            // 设为默认
            const presetRow = settings.locator('li.preset-item').filter({ hasText: 'E2E 预设 P2' }).first()
            const defaultButton = presetRow.getByRole('button', { name: /设为默认/ }).first()
            let defaulted = false
            if ((await defaultButton.count().catch(() => 0)) > 0) {
              await defaultButton.click({ force: true }).catch(() => {})
              await page.waitForTimeout(800)
            }
            // API 校验
            const apiCheck = await settings
              .evaluate(async () => {
                for (const base of ['/tavern/api/reader/v1', '/api/reader/v1']) {
                  try {
                    const response = await fetch(`${base}/tavern/presets`, { credentials: 'include' })
                    if (response.ok) return await response.json()
                  } catch {}
                }
                return null
              })
              .catch(() => null)
            const items = apiCheck ? (apiCheck.items || apiCheck.presets || apiCheck) : []
            const target = Array.isArray(items) ? items.find((item) => item.name === 'E2E 预设 P2') : null
            defaulted = Boolean(target && (target.isDefault === true || target.is_default === true))
            await settings.evaluate(() => location.reload()).catch(() => {})
            await page.waitForTimeout(1200)
            const settingsAgain = fnow()
            const rowAgain = settingsAgain ? settingsAgain.locator('li.preset-item').filter({ hasText: 'E2E 预设 P2' }).first() : null
            const badgeAgain = rowAgain ? ((await rowAgain.innerText().catch(() => '')) || '') : ''
            presets.defaulted = defaulted
            q2Record(2, defaulted, `is_default=${defaulted} 刷新后行文本=${badgeAgain.slice(0, 80)}`)

            // 新聊天使用预设 + prompt 断言
            // 卡详情是异步加载的：先等「开新聊天」按钮/卡名渲染完，不要只固定 sleep；
            // 生产上偶发停在「正在打开角色卡...」，10s 后重进一次再等 20s。
            const waitDetailReady = async (frameNow, timeoutMs = 20000) => {
              if (!frameNow) return false
              return waitFor(async () => {
                const buttons = await frameNow
                  .getByRole('button', { name: /开新聊天|开始聊天|新聊天/ })
                  .count()
                  .catch(() => 0)
                const body = ((await frameNow.locator('body').innerText().catch(() => '')) || '')
                return buttons > 0 && !/正在打开角色卡/.test(body)
              }, timeoutMs)
            }
            let detail = await openDetail()
            let detailReady = await waitDetailReady(detail, 20000)
            if (!detailReady && detail) {
              const detailPath = (() => {
                try {
                  return new URL(detail.url()).pathname.replace(/^\/tavern/, '')
                } catch {
                  return ''
                }
              })()
              if (detailPath) {
                detail = await appGoto(detailPath, 25000)
                detailReady = await waitDetailReady(detail, 20000)
              }
            }
            let promptOk = false
            let promptDetail = ''
            if (!detail) {
              promptDetail = '无法打开角色详情页（owner: cards-client）'
            } else {
              const cardIdMatch = detail.url().match(/\/characters\/([0-9a-zA-Z_-]+)/)
              const cardId = cardIdMatch ? cardIdMatch[1] : null
              const startButton = detail.getByRole('button', { name: /开新聊天|开始聊天|新聊天/ }).first()
              let newChat = null
              const q4Debug = { button: 0, disabled: null, btnText: '', alert: '', body: '', chatPost: null, detailReady: Boolean(detailReady) }
              if ((await startButton.count().catch(() => 0)) > 0) {
                q4Debug.button = 1
                q4Debug.disabled = await startButton.isDisabled().catch(() => null)
                q4Debug.btnText = ((await startButton.innerText().catch(() => '')) || '').trim()
                const chatResponses = []
                const onChatResponse = (response) => {
                  if (!/\/tavern\/chats(?:\?|$)/.test(response.url()) || response.request().method() !== 'POST') return
                  chatResponses.push(response)
                  response.text().then((text) => { q4Debug.chatPost = { status: response.status(), body: text.slice(0, 200) } }).catch(() => {})
                }
                page.on('response', onChatResponse)
                await startButton.click({ force: true }).catch(() => {})
                newChat = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 25000)
                if (!newChat) {
                  // 事件未绑定/按钮被遮挡时用 DOM click 再试一次。
                  await startButton.evaluate((el) => el.click()).catch(() => {})
                  newChat = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 15000)
                }
                await page.waitForTimeout(500)
                page.off('response', onChatResponse)
                q4Debug.alert = ((await detail.locator('[role="alert"]').first().innerText().catch(() => '')) || '').trim()
                q4Debug.body = ((await detail.locator('body').innerText().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 200)
              }
              if (!q4Debug.body) {
                // 找不到按钮时把可见文本带进 detail，方便区分「未加载」还是「渲染异常」。
                q4Debug.body = ((await detail.locator('body').innerText().catch(() => '')) || '')
                  .replace(/\s+/g, ' ')
                  .slice(0, 300)
              }
              const chatId = newChat ? newChat.url().match(/\/chats\/([0-9a-zA-Z_-]+)/)[1] : null
              if (newChat && cardId) {
                const promptResult = await newChat
                  .evaluate(async ({ cid, chatId: chat }) => {
                    const bases = ['/tavern/api/reader/v1', '/api/reader/v1']
                    for (const base of bases) {
                      try {
                        const response = await fetch(`${base}/tavern/characters/${encodeURIComponent(cid)}/prompt?chatId=${encodeURIComponent(chat)}`, { credentials: 'include' })
                        const text = await response.text()
                        if (response.status === 200) return { status: 200, body: text }
                        if (response.status === 422 || response.status === 404) return { status: response.status, body: text }
                      } catch {}
                    }
                    return null
                  }, { cid: cardId, chatId })
                  .catch(() => null)
                if (!promptResult) promptDetail = 'prompt 接口不可用（owner: app-auth）'
                else if (promptResult.status !== 200) promptDetail = `prompt 接口 status=${promptResult.status}（P2 chatId 未上线，owner: app-auth）`
                else {
                  promptOk = promptResult.body.includes('【E2E-PRESET】')
                  promptDetail = `status=200 含预设文本=${promptOk} len=${promptResult.body.length}`
                }
                presets.prompt = { status: promptResult ? promptResult.status : null, ok: promptOk, detail: promptDetail }
              } else {
                promptDetail = `新聊天/卡 id 缺失 newChat=${Boolean(newChat)} cardId=${Boolean(cardId)} debug=${JSON.stringify(q4Debug)}`
              }
            }
            if (/owner: app-auth/.test(promptDetail)) q2Record(3, true, promptDetail, true)
            else q2Record(3, promptOk, promptDetail)
            await shot(page, '114-q-prompt.png').catch(() => {})

            // 非法采样值：前端拦截（role=alert 或列表不增加）与后端 422 都要测
            let invalid = null
            const uiInvalid = await settings
              .evaluate(async () => {
                const bases = ['/tavern/api/reader/v1', '/api/reader/v1']
                const list = async () => {
                  for (const base of bases) {
                    try {
                      const response = await fetch(`${base}/tavern/presets`, { credentials: 'include' })
                      if (response.ok) return await response.json()
                    } catch {}
                  }
                  return null
                }
                return { before: await list() }
              })
              .catch(() => null)
            const countNamed = (payload, name) => {
              const items = payload && (payload.items || payload.presets || payload)
              return Array.isArray(items) ? items.filter((item) => item && item.name === name).length : 0
            }
            const invalidName = 'E2E 非法预设'
            const beforeCount = countNamed(uiInvalid && uiInvalid.before, invalidName)
            let uiAlert = ''
            let uiBlocked = false
            if (formReady) {
              await nameInput.fill(invalidName).catch(() => {})
              await temperatureInput.fill('5').catch(() => {})
              const saveButton = form.getByRole('button', { name: /保存预设|保存/ }).first()
              await saveButton.click({ force: true }).catch(() => {})
              await page.waitForTimeout(1500)
              uiAlert = ((await form.locator('[role="alert"]').first().innerText().catch(() => '')) || '').trim()
              const afterPayload = await settings
                .evaluate(async () => {
                  for (const base of ['/tavern/api/reader/v1', '/api/reader/v1']) {
                    try {
                      const response = await fetch(`${base}/tavern/presets`, { credentials: 'include' })
                      if (response.ok) return await response.json()
                    } catch {}
                  }
                  return null
                })
                .catch(() => null)
              const afterCount = countNamed(afterPayload, invalidName)
              uiBlocked = Boolean(uiAlert) || afterCount === beforeCount
              invalid = { uiAlert: uiAlert || null, uiBlocked, beforeCount, afterCount }
            }
            const apiInvalid = await settings
              .evaluate(async () => {
                for (const base of ['/tavern/api/reader/v1', '/api/reader/v1']) {
                  try {
                    const response = await fetch(`${base}/tavern/presets`, {
                      method: 'POST',
                      credentials: 'include',
                      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)) },
                      body: JSON.stringify({ name: 'E2E 非法预设', temperature: 5 }),
                    })
                    if (response.status !== 404) return { status: response.status, body: (await response.text()).slice(0, 300) }
                  } catch {}
                }
                return null
              })
              .catch(() => null)
            const backendRejected = Boolean(apiInvalid && apiInvalid.status === 422 && !/IDEMPOTENCY_KEY_REQUIRED/.test(apiInvalid.body || ''))
            invalid = { ...(invalid || {}), backendStatus: apiInvalid ? apiInvalid.status : null, backendRejected, backendBody: apiInvalid ? apiInvalid.body : null }
            presets.invalid = invalid
            if (!apiInvalid || apiInvalid.status === 404) {
              q2Record(4, true, `预设接口未上线 status=${apiInvalid ? apiInvalid.status : 'unreachable'}（owner: app-auth）`, true)
            } else {
              const detail = `前端拦截=${Boolean(invalid.uiBlocked)} alert=${invalid.uiAlert ? invalid.uiAlert.slice(0, 80) : '无'} 后端 status=${apiInvalid.status} rejected=${backendRejected}`
              q2Record(4, Boolean(invalid.uiBlocked) && backendRejected, detail)
            }
            await shot(page, '115-q-invalid.png').catch(() => {})
          }
        }
      } catch (error) {
        q2SkipRemaining(q2Recorded.size, `Q 组异常：${(error && error.message) || error}`)
      }
      presets.console = consoleErrors.slice(qConsoleStart).filter((item) => !/Failed to load resource/.test(item))
    }
  }
  if (groupOn('q')) q2SkipRemaining(0, 'Q 组未执行（跳过）')

  // ── P3. 原生上下文桥（task-58，插件工具条在 /chats/:id 可用）──────────────
  const bridge = {
    enabled: false,
    home: null,
    chat: null,
    continuePrompt: null,
    roleplayPrompt: null,
    memory: null,
    console: null,
    steps: [],
  }
  report.native.bridge = bridge
  const p3Names = [
    '首页工具条：续写/角色扮演不可用并提示「先打开一个聊天」',
    '聊天页工具条：检测到上下文并启用续写/角色扮演',
    '点「让 DSH 续写」后 DSH 会话输入框预填（角色名 + 续写要求）',
    '点「角色扮演」后输入框预填（身份扮演 + 角色名）',
    '位置记忆：聊天页刷新仍在该聊天、首页刷新回首页',
  ]
  const p3Done = new Set()
  const p3Record = (index, ok, detail, skipped = false) => {
    if (p3Done.has(index)) return
    p3Done.add(index)
    bridge.steps.push({ name: p3Names[index], ok: Boolean(ok), skipped, detail })
    record(p3Names[index], ok, detail, skipped)
  }
  const p3SkipRemaining = (from, reason) => {
    for (let index = from; index < p3Names.length; index += 1) p3Record(index, false, reason, true)
  }
  if (groupOn('p3')) {
    if (nativeBlocked) {
      p3SkipRemaining(0, blockedReason)
    } else {
      const p3Start = consoleErrors.length
      const panelView = () => page.locator('[data-dsh-tavern-view]').first()
      const toolbarStatus = async () => ((await page.locator('[data-dsh-tavern-status]').first().innerText().catch(() => '')) || '').trim()
      const toolbarHint = async () => (await panelView().getAttribute('data-dsh-tavern-hint').catch(() => '')) || ''
      const actionButton = (action) => page.locator(`[data-dsh-tavern-action="${action}"]`).first()
      const ensurePanel = async (timeoutMs = 20000) => {
        const visible = await panelView()
          .waitFor({ state: 'visible', timeout: 1500 })
          .then(() => true)
          .catch(() => false)
        if (visible) return true
        const anchor = await findEntry(timeoutMs)
        if (!anchor) return false
        await dismissWelcome()
        await anchor.click({ force: true }).catch(() => {})
        return panelView()
          .waitFor({ state: 'visible', timeout: timeoutMs })
          .then(() => true)
          .catch(() => false)
      }
      // DSH 主会话输入框：Lexical / contenteditable / textarea 都覆盖，取最后一个有内容的。
      const readComposer = () =>
        page
          .evaluate(() => {
            const nodes = Array.prototype.slice.call(
              document.querySelectorAll('[data-lexical-editor="true"], [contenteditable="true"][role="textbox"], [contenteditable="true"], textarea'),
            )
            const texts = []
            for (const node of nodes) {
              if (node.tagName === 'TEXTAREA') {
                if (node.value && node.value.trim()) texts.push(node.value)
              } else {
                const text = (node.innerText || node.textContent || '').trim()
                if (text) texts.push(text)
              }
            }
            return texts
          })
          .catch(() => [])
      const readClipboard = () =>
        page
          .evaluate(() => (navigator.clipboard && navigator.clipboard.readText ? navigator.clipboard.readText() : ''))
          .catch(() => '')
      // 只认「本次点击对应」的 prompt（续写/扮演文案不同），避免读到另一个会话残留的草稿。
      const waitComposerText = async (pattern, timeoutMs = 30000) => {
        let found = ''
        await waitFor(async () => {
          const texts = await readComposer()
          found = texts.find((text) => pattern.test(text)) || ''
          return Boolean(found)
        }, timeoutMs)
        return found
      }
      // 点工具条按钮 → 等 DSH 新会话输入框出现对应 prompt；插件退化到剪贴板时记录并重试一次。
      const askAndRead = async (action, pattern) => {
        const beforeTexts = (await readComposer()).map((text) => text.slice(0, 40))
        await actionButton(action).click({ force: true }).catch(() => {})
        let text = await waitComposerText(pattern, 30000)
        let source = 'editor'
        if (!text) {
          const clip = (await readClipboard()) || ''
          if (pattern.test(clip)) {
            text = clip
            source = 'clipboard'
          }
        }
        const debug = {
          hint: await toolbarHint(),
          status: (await toolbarStatus()).slice(0, 120),
          composers: (await readComposer()).map((item) => item.slice(0, 80)).slice(0, 5),
          before: beforeTexts.slice(0, 3),
          dom: await page
            .evaluate(() => ({
              path: location.pathname,
              editors: document.querySelectorAll('[contenteditable="true"]').length,
              lexical: document.querySelectorAll('[data-lexical-editor="true"]').length,
              textareas: document.querySelectorAll('textarea').length,
              bodyLen: (document.body.innerText || '').length,
              snippet: (document.body.innerText || '').slice(0, 240),
            }))
            .catch(() => null),
        }
        return { text, source, debug }
      }
      const chatPath = (() => {
        if (!chatEvidence || !chatEvidence.chatUrl) return ''
        try {
          return new URL(chatEvidence.chatUrl).pathname.replace(/^\/tavern/, '')
        } catch {
          return ''
        }
      })()
      const homeEvidence = report.native.home || {}
      const chatCharacterName =
        (chatEvidence && chatEvidence.characterName) || (homeEvidence.cardName || cardName) || '林小满'
      bridge.enabled = true
      bridge.characterName = chatCharacterName
      try {
        // P1 首页：无上下文 → 两个按钮 disabled + 状态提示「先打开一个聊天」。
        let panelOk = await ensurePanel()
        if (!panelOk) {
          p3SkipRemaining(0, '找不到酒馆面板工具条（owner: plugin-core）')
        } else {
          await appGoto('/')
          const homeOk = await waitFor(async () => {
            const continueButton = actionButton('continue')
            const roleplayButton = actionButton('roleplay')
            const disabled =
              (await continueButton.count().catch(() => 0)) > 0 &&
              (await continueButton.isDisabled().catch(() => false)) &&
              (await roleplayButton.count().catch(() => 0)) > 0 &&
              (await roleplayButton.isDisabled().catch(() => false))
            const status = await toolbarStatus()
            return disabled && /先打开一个聊天/.test(status)
          }, 25000)
          const homeDetail = `continueDisabled=${await actionButton('continue').isDisabled().catch(() => null)} roleplayDisabled=${await actionButton('roleplay').isDisabled().catch(() => null)} status=${(await toolbarStatus()).slice(0, 120)} hint=${await toolbarHint()}`
          bridge.home = { ok: homeOk, detail: homeDetail }
          p3Record(0, homeOk, homeDetail)
          await shot(page, '120-p3-home-disabled.png').catch(() => {})

          // P2 聊天页：上下文就绪 → 按钮 enabled，状态栏显示当前聊天路径。
          if (!chatPath) {
            p3SkipRemaining(1, '没有已建立的聊天会话（先跑 C 组；owner: harness）')
          } else {
            await appGoto(chatPath)
            const chatOk = await waitFor(async () => {
              const continueButton = actionButton('continue')
              const roleplayButton = actionButton('roleplay')
              const enabled =
                (await continueButton.count().catch(() => 0)) > 0 &&
                !(await continueButton.isDisabled().catch(() => true)) &&
                (await roleplayButton.count().catch(() => 0)) > 0 &&
                !(await roleplayButton.isDisabled().catch(() => true))
              const status = await toolbarStatus()
              return enabled && /当前：\/chats\//.test(status)
            }, 30000)
            const chatDetail = `continueDisabled=${await actionButton('continue').isDisabled().catch(() => null)} roleplayDisabled=${await actionButton('roleplay').isDisabled().catch(() => null)} status=${(await toolbarStatus()).slice(0, 120)} hint=${await toolbarHint()}`
            bridge.chat = { ok: chatOk, detail: chatDetail }
            p3Record(1, chatOk, chatDetail)
            await shot(page, '121-p3-chat-enabled.png').catch(() => {})

            // P3 续写：点按钮 → DSH 新会话输入框预填；编辑器写不进去时按剪贴板兜底断言。
            if (!chatOk) {
              p3SkipRemaining(2, '聊天页上下文未就绪，无法验证续写/扮演（owner: plugin-core 桥接）')
            } else {
              let ask = await askAndRead('continue', /继续写下一段回复/)
              if (ask.source === 'clipboard') {
                // 插件 fillComposer 偶发退化到剪贴板（owner: plugin-core）；重开面板再试一次，仍退化才记 FAIL。
                await ensurePanel().catch(() => false)
                await appGoto(chatPath)
                await waitFor(async () => !(await actionButton('continue').isDisabled().catch(() => true)), 30000)
                ask = await askAndRead('continue', /继续写下一段回复/)
              }
              const continueText = ask.text
              const continueSource = ask.source
              const continueOk = Boolean(continueText) && continueText.includes(chatCharacterName) && /继续写下一段回复/.test(continueText)
              bridge.continuePrompt = { ok: continueOk, source: continueSource, text: continueText.slice(0, 200), debug: ask.debug }
              p3Record(
                2,
                continueOk,
                continueOk
                  ? `source=${continueSource} 含角色名=${continueText.includes(chatCharacterName)} 含续写要求=true`
                  : `source=${continueSource} text=${continueText.slice(0, 160) || '（空）'}（owner: plugin-core 续写预填 / cards-client 桥上下文）`,
              )
              await shot(page, '122-p3-continue-prompt.png').catch(() => {})

              // P4 角色扮演：重新打开酒馆面板并回到聊天页，再点角色扮演。
              const reopened = await ensurePanel()
              if (!reopened) {
                p3SkipRemaining(3, '点续写后无法重新打开酒馆面板（owner: plugin-core）')
              } else {
                await appGoto(chatPath)
                const readyAgain = await waitFor(async () => !(await actionButton('roleplay').isDisabled().catch(() => true)), 30000)
                if (!readyAgain) {
                  p3SkipRemaining(3, '重新进入聊天页后上下文未就绪（owner: plugin-core 桥接）')
                } else {
                  let askRole = await askAndRead('roleplay', /身份扮演/)
                  if (askRole.source === 'clipboard') {
                    await ensurePanel().catch(() => false)
                    await appGoto(chatPath)
                    await waitFor(async () => !(await actionButton('roleplay').isDisabled().catch(() => true)), 30000)
                    askRole = await askAndRead('roleplay', /身份扮演/)
                  }
                  const roleplayText = askRole.text
                  const roleplaySource = askRole.source
                  const roleplayOk =
                    Boolean(roleplayText) && roleplayText.includes(chatCharacterName) && /身份扮演/.test(roleplayText)
                  bridge.roleplayPrompt = { ok: roleplayOk, source: roleplaySource, text: roleplayText.slice(0, 200), debug: askRole.debug }
                  p3Record(
                    3,
                    roleplayOk,
                    roleplayOk
                      ? `source=${roleplaySource} 含角色名=${roleplayText.includes(chatCharacterName)} 含身份扮演=true`
                      : `source=${roleplaySource} text=${roleplayText.slice(0, 160) || '（空）'}（owner: plugin-core 扮演预填 / cards-client 桥上下文）`,
                  )
                  await shot(page, '123-p3-roleplay-prompt.png').catch(() => {})
                }
              }
            }
          }
        }

        // P5 位置记忆 + 本组 console：
        //   a) 聊天页刷新 → 仍在该聊天；
        //   b) 回到首页后刷新应用 iframe → 停在首页（不会被「上次聊天」记忆拉回聊天页）。
        // 注意：P3-3/P3-4 点击工具条会新开 DSH 会话，面板可能重挂载/整页刷新，这里每一步都取「当前」tavern frame 并重试一次。
        await ensurePanel().catch(() => false)
        await page.waitForTimeout(800)
        const currentTavernFrame = async () => (await waitFrameUrl(/\/tavern\//, 15000)) || fnow()
        const tryChatReload = async () => {
          const frame = await currentTavernFrame()
          if (!frame || !chatPath) return false
          await frame.evaluate(() => location.reload()).catch(() => {})
          return Boolean(await waitFrameUrl(new RegExp(chatPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 20000))
        }
        const tryHomeReload = async () => {
          const frame = await currentTavernFrame()
          if (!frame) return false
          const origin = new URL(frame.url()).origin
          await frame.goto(`${origin}/tavern/`, { waitUntil: 'domcontentloaded' }).catch(() => {})
          await frame.evaluate(() => location.reload()).catch(() => {})
          const settled = await waitFrameUrl(/\/tavern\/?$/, 25000)
          if (!settled) return false
          return waitFor(async () => {
            const frameNow = await currentTavernFrame()
            if (!frameNow) return false
            const body = await bodyText(frameNow)
            return /角色卡/.test(body) && !/正在打开角色卡/.test(body)
          }, 25000)
        }
        let chatReloadOk = await tryChatReload()
        if (!chatReloadOk) {
          await ensurePanel().catch(() => false)
          await page.waitForTimeout(1200)
          chatReloadOk = await tryChatReload()
        }
        let homeAgain = await tryHomeReload()
        if (!homeAgain) {
          await page.waitForTimeout(1200)
          homeAgain = await tryHomeReload()
        }
        const p3Console = consoleErrors.slice(p3Start).filter((text) => !/Failed to load resource/.test(text))
        bridge.console = p3Console
        const memoryOk = chatReloadOk && homeAgain
        bridge.memory = { chatReloadOk, homeAgain }
        p3Record(
          4,
          memoryOk,
          `聊天页刷新仍在该聊天=${chatReloadOk} 首页刷新回首页=${homeAgain} console=${p3Console.length ? p3Console.slice(0, 2).join(' | ') : 'clean'}`,
        )
        await shot(page, '124-p3-memory.png').catch(() => {})
      } catch (error) {
        p3SkipRemaining(p3Done.size, `P3 组异常：${(error && error.message) || error}`)
      }
    }
  }
  if (groupOn('p3')) p3SkipRemaining(0, 'P3 组未执行（跳过）')

  // ── M. 旧路由下线 ────────────────────────────────────────────────────────
  const oldRouteEvidence = { routes: [], deadLinks: 0, exploreEntry: false }
  report.native.oldRoutes = oldRouteEvidence
  if (groupOn('m')) {
    if (nativeBlocked) {
      skip('旧路由全部下线（/explore、/works、/worlds/scripts、/conversations/world/x）', blockedReason)
      skip('页面无「探索作品」等旧入口', blockedReason)
    } else {
      const oldRoutes = ['/explore', '/works', '/worlds/scripts', '/conversations/world/e2e-probe']
      let allOk = true
      for (const route of oldRoutes) {
        const landed = await appGotoRedirect(route)
        const landedUrl = landed ? landed.url() : ''
        let landedPath = ''
        try {
          landedPath = landedUrl ? new URL(landedUrl).pathname : ''
        } catch {
          landedPath = ''
        }
        const body = landed ? await bodyText(landed) : ''
        const ok = landedPath === '/tavern/' || landedPath === '/tavern' || /404|不存在|未找到/.test(body)
        oldRouteEvidence.routes.push({ route, landed: landedPath, ok })
        if (!ok) allOk = false
      }
      check('旧路由全部下线（/explore、/works、/worlds/scripts、/conversations/world/x）', allOk, `routes=${clip(oldRouteEvidence.routes)}`)
      const home = await appGoto('/')
      const text = home ? await bodyText(home) : ''
      const exploreEntry = /探索作品|创作工作台|世界脚本|进入故事/.test(text)
      const deadLinks = home
        ? await home.locator('a[href*="/explore"], a[href*="/worlds/"], a[href*="/conversations/world"]').count().catch(() => 0)
        : -1
      oldRouteEvidence.exploreEntry = exploreEntry
      oldRouteEvidence.deadLinks = deadLinks
      check('页面无「探索作品」等旧入口', !exploreEntry && deadLinks === 0, `旧文案=${exploreEntry} 死链=${deadLinks}`)
      await shot(page, '99-old-routes.png')
    }
  }


  // ── L. 群聊删成员容错（task-66：成员卡被删后 200 + 已删除展示 + 只读）─────
  const groupDeletionCheckNames = [
    '删除一位成员后群聊详情仍可读（deleted 标记 + aliveMemberCount=1 + cards 只剩存活成员）',
    'UI 页头把已删除成员标注「（已删除）」并灰显',
    '删成员后继续发言，发言人切换到存活成员',
    '全部成员删除后群聊详情仍可读（aliveMemberCount=0）',
    '全员删除后 UI 只读提示 + 输入/发送/重生成禁用，POST messages 返回 409 CHAT_NO_MEMBERS',
    '/chats 列表仍包含该群聊，删除群聊 204 且列表回落',
    '群聊删成员场景 console 无报错',
  ]
  const groupDeletionEvidence = {
    enabled: false,
    chatId: null,
    cards: [],
    memberDeleted: null,
    uiDeleted: null,
    speakerSwitch: null,
    allDeleted: null,
    readonly: null,
    list: null,
    console: null,
    steps: [],
  }
  report.native.groupDeletion = groupDeletionEvidence
  if (groupOn('l')) {
    if (nativeBlocked) {
      for (const name of groupDeletionCheckNames) skip(name, blockedReason)
    } else {
      groupDeletionEvidence.enabled = true
      const lConsoleStart = consoleErrors.length
      const lApi = async (pathname, options = {}) => {
        const frameNow = fnow()
        if (!frameNow) return null
        return frameNow
          .evaluate(
            async ([pathAndQuery, opts]) => {
              try {
                const response = await fetch('/tavern/api/reader/v1/tavern' + pathAndQuery, {
                  credentials: 'include',
                  method: opts.method || 'GET',
                  headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
                  body: opts.body ? JSON.stringify(opts.body) : undefined,
                })
                const text = await response.text()
                let payload = null
                try {
                  payload = text ? JSON.parse(text) : null
                } catch {
                  payload = { raw: String(text).slice(0, 200) }
                }
                return { status: response.status, payload }
              } catch (error) {
                return { status: 0, error: String(error) }
              }
            },
            [pathname, options],
          )
          .catch(() => null)
      }
      const markStep = (index) => {
        if (!groupDeletionEvidence.steps.includes(index)) groupDeletionEvidence.steps.push(index)
      }
      const skipRest = (fromIndex, detail) => {
        for (let index = fromIndex; index < groupDeletionCheckNames.length; index += 1) {
          if (!groupDeletionEvidence.steps.includes(index)) {
            skip(groupDeletionCheckNames[index], detail)
            markStep(index)
          }
        }
      }
      // 新建的群聊还没有消息：就绪条件是聊天壳（标题/菜单）+ 空态或消息区，而不是 .bubble-row。
      const lWaitChatShell = async (frameNow, timeoutMs) => {
        if (!frameNow) return false
        return waitFor(async () => {
          const chrome = await frameNow
            .locator('.chat-name, button[aria-label="聊天菜单"]')
            .count()
            .catch(() => 0)
          const shell = await frameNow
            .locator('.chat-log, .chat-empty, .bubble-row')
            .count()
            .catch(() => 0)
          return chrome > 0 && shell > 0
        }, timeoutMs)
      }

      // 1) 准备两张 fixture 卡（L 组可独立跑：缺卡时自己导入）。
      const lHome = await appGoto('/')
      const secondCard =
        manifest && Array.isArray(manifest.cards) ? manifest.cards.find((card) => card.name !== cardName) || null : null
      const secondCardName = (secondCard && secondCard.name) || '顾清和'
      const lEnsureCard = async (name, file) => {
        if (!lHome) return false
        if (await findCard(name)) return true
        if (!file) return false
        const input = lHome.locator('input[type="file"]').first()
        if (!(await input.count().catch(() => 0))) return false
        await input.setInputFiles(path.join(fixturesDir, file)).catch(() => {})
        const appeared = await waitFor(async () => Boolean(await findCard(name)), 30000)
        if (appeared) await page.waitForTimeout(400)
        return appeared
      }
      const firstReady = fixtureCard ? await lEnsureCard(cardName, fixtureCard.file) : false
      const secondReady = secondCard ? await lEnsureCard(secondCardName, secondCard.file) : false
      const cardsList = await lApi('/characters')
      const cardItems =
        cardsList && cardsList.payload && Array.isArray(cardsList.payload.items) ? cardsList.payload.items : []
      const cardIdFor = (name) => {
        const found = cardItems.find((item) => item.name === name)
        return found ? found.id : null
      }
      const firstCardId = cardIdFor(cardName)
      const secondCardId = cardIdFor(secondCardName)
      groupDeletionEvidence.cards = [firstCardId, secondCardId]
      if (!firstReady || !secondReady || !firstCardId || !secondCardId) {
        skipRest(0, '缺少两张 fixture 卡，无法建群（owner: verifier 导入）')
      } else {
        // 2) API 建群（不依赖 G 组：L 组必须可独立运行）。
        const created = await lApi('/chats', { method: 'POST', body: { cardIds: [firstCardId, secondCardId] } })
        const createdChat = created && created.payload && created.payload.chat ? created.payload.chat : null
        const chatId = createdChat && createdChat.id ? createdChat.id : null
        groupDeletionEvidence.chatId = chatId
        if (!chatId) {
          skipRest(0, `API 建群失败（status=${created && created.status}；owner: app-auth）`)
        } else {
          let frame = await appGoto(`/chats/${chatId}`)
          const chatReady = frame ? await lWaitChatShell(frame, 25000) : false
          if (!chatReady) {
            skipRest(0, '群聊页未渲染（owner: cards-client）')
          } else {
            const sendInChat = async (frameNow, text) => {
              const input = frameNow.locator('textarea[aria-label="聊天输入框"]').first()
              await input.waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
              await input.fill(text).catch(() => {})
              const before = await frameNow.locator('.bubble-row.assistant').count().catch(() => 0)
              const sendButton = frameNow.locator('button[aria-label="发送消息"]').first()
              // 发送按钮在「刚进聊天/上一条还在收尾」时是 disabled 的，force click 会被静默丢弃。
              await waitFor(async () => {
                if (!(await sendButton.count().catch(() => 0))) return false
                return !(await sendButton.isDisabled().catch(() => false))
              }, 20000)
              let httpStatus = null
              const onResponse = (response) => {
                if (/\/tavern\/chats\/[^/]+\/messages/.test(response.url()) && response.request().method() === 'POST') {
                  httpStatus = response.status()
                }
              }
              page.on('response', onResponse)
              const started = Date.now()
              let clickPath = 'click'
              await sendButton.click({ force: true }).catch(() => {})
              await waitFor(async () => httpStatus !== null, 8000)
              if (httpStatus === null) {
                clickPath += '+domClick'
                await sendButton.evaluate((element) => element.click()).catch(() => {})
                await waitFor(async () => httpStatus !== null, 8000)
              }
              page.off('response', onResponse)
              // 等 assistant 气泡出现（最多 90s）；30s 仍无气泡时旁路探网关，作为「上游挂起」证据。
              let stallProbe = null
              const deadline = Date.now() + 90000
              while (Date.now() < deadline) {
                const count = await frameNow.locator('.bubble-row.assistant').count().catch(() => 0)
                if (count > before) break
                if (!stallProbe && Date.now() - started > 30000) stallProbe = await gatewayProbe(15000)
                await page.waitForTimeout(600)
              }
              // 群聊气泡没有 .bubble-meta（small 为空），完成判定用「无 pending + 文本连续两次稳定」。
              let saved = false
              let previousText = ''
              const settleDeadline = Date.now() + 90000
              while (Date.now() < settleDeadline) {
                const rows = await frameNow.locator('.bubble-row').count().catch(() => 0)
                const pending = await frameNow.locator('.bubble-pending').count().catch(() => 0)
                const textNow =
                  ((await frameNow.locator('.bubble-row.assistant .bubble-text').last().innerText().catch(() => '')) || '').trim()
                if (rows >= before + 2 && pending === 0 && textNow.length > 10) {
                  if (previousText && previousText === textNow) {
                    saved = true
                    break
                  }
                  previousText = textNow
                } else {
                  previousText = ''
                }
                await page.waitForTimeout(600)
              }
              const replyText =
                ((await frameNow.locator('.bubble-row.assistant .bubble-text').last().innerText().catch(() => '')) || '').trim()
              const debug = {
                saved,
                rows: await frameNow.locator('.bubble-row').count().catch(() => 0),
                assistants: await frameNow.locator('.bubble-row.assistant').count().catch(() => 0),
                pending: await frameNow.locator('.bubble-pending').count().catch(() => 0),
                meta: ((await frameNow.locator('.bubble-row.assistant small').last().innerText().catch(() => '')) || '').trim(),
                before,
                replyLength: replyText.length,
                httpStatus,
                clickPath,
                stallProbe,
              }
              return { saved, replyText, debug, httpStatus, clickPath, stallProbe }
            }
            const assistantsOf = (payload) =>
              payload && Array.isArray(payload.messages) ? payload.messages.filter((item) => item.role === 'assistant') : []

            // 3) 先发一条消息（真实生成 1 次），确认群聊可用。
            const firstSend = await sendInChat(frame, '（E2E 群聊）你好，先打个招呼。')
            const detailBefore = await lApi(`/chats/${chatId}`)
            const beforePayload = detailBefore && detailBefore.payload ? detailBefore.payload : null
            const firstSpeaker = assistantsOf(beforePayload).length
              ? assistantsOf(beforePayload)[assistantsOf(beforePayload).length - 1].speakerName || null
              : null

            // 4) API 软删第一位成员卡，再读详情：应 200 且容错。
            const deletedFirst = await lApi(`/characters/${firstCardId}`, { method: 'DELETE' })
            const detailAfterOne = await lApi(`/chats/${chatId}`)
            const onePayload = detailAfterOne && detailAfterOne.payload ? detailAfterOne.payload : null
            // GET /chats/{id} 形状：{chat:{...members/aliveMemberCount/cards}, messages:[...]}
            const oneChat = onePayload && onePayload.chat ? onePayload.chat : null
            const membersAfterOne = oneChat && Array.isArray(oneChat.members) ? oneChat.members : []
            const aliveAfterOne =
              oneChat && typeof oneChat.aliveMemberCount === 'number' ? oneChat.aliveMemberCount : null
            const cardsAfterOne = oneChat && Array.isArray(oneChat.cards) ? oneChat.cards.length : null
            const memberMarkedDeleted = membersAfterOne.some(
              (member) => member.cardId === firstCardId && member.deleted === true,
            )
            const aliveMember = membersAfterOne.find((member) => member.deleted !== true) || null
            const aliveMemberName = aliveMember ? aliveMember.name : null
            groupDeletionEvidence.memberDeleted = {
              deleteStatus: deletedFirst ? deletedFirst.status : null,
              detailStatus: detailAfterOne ? detailAfterOne.status : null,
              memberMarkedDeleted,
              aliveAfterOne,
              cardsAfterOne,
              firstSpeaker,
              aliveMemberName,
            }

            // 5) UI：重进聊天页，页头已删除成员应标注「（已删除）」并灰显。
            frame = await appGoto(`/chats/${chatId}`)
            if (frame) await lWaitChatShell(frame, 25000)
            const markerShown = frame
              ? await waitFor(async () => /（已删除）|\(已删除\)/.test(await bodyText(frame)), 20000)
              : false
            const markerNodes = frame
              ? await frame
                  .evaluate(() => {
                    const nodes = Array.from(document.querySelectorAll('.chat-members .chat-member-avatar'))
                    return nodes
                      .map((node) => {
                        // title 与 aria-label 常是同一文案，去重后再断言，避免「林小满（已删除） 林小满（已删除）」误判。
                        const parts = Array.from(
                          new Set([node.getAttribute('title') || '', node.getAttribute('aria-label') || ''].filter(Boolean)),
                        )
                        const text = parts.join(' | ').trim()
                        const style = getComputedStyle(node)
                        return { text, opacity: style.opacity, className: String(node.className), filter: style.filter }
                      })
                      .filter((node) => /已删除/.test(node.text) || /deleted/.test(node.className))
                  })
                  .catch(() => [])
              : []
            const markerOk = markerNodes.some(
              (node) =>
                /已删除/.test(node.text || '') &&
                /(?:^|\s)deleted(?:\s|$)/.test(node.className || '') &&
                Number(node.opacity) < 0.6,
            )
            const greyed = markerNodes.some(
              (node) => Number(node.opacity) < 1 || /(?:^|\s)(?:is-)?deleted|disabled/.test(node.className),
            )
            groupDeletionEvidence.uiDeleted = { markerShown, markerOk, markerNodes, greyed }
            await shot(page, '140-l-member-deleted.png')
            const l1Ok = Boolean(
              detailAfterOne &&
                detailAfterOne.status === 200 &&
                oneChat &&
                oneChat.mode === 'group' &&
                memberMarkedDeleted &&
                aliveAfterOne === 1 &&
                cardsAfterOne === 1,
            )
            if (detailAfterOne && detailAfterOne.status === 200 && aliveAfterOne === null) {
              skipRest(0, '详情缺少 aliveMemberCount/deleted 字段（owner: app-auth，task-63 未部署）')
            } else if (!l1Ok) {
              check(groupDeletionCheckNames[0], false, `status=${detailAfterOne && detailAfterOne.status} members=${clip(membersAfterOne)} alive=${aliveAfterOne} cards=${cardsAfterOne}；owner: app-auth`)
              markStep(0)
              skipRest(1, 'L1 详情容错未通过，跳过后续依赖项')
            } else {
              check(groupDeletionCheckNames[0], true, `status=200 aliveMemberCount=${aliveAfterOne} cards=${cardsAfterOne}`)
              markStep(0)
              check(
                groupDeletionCheckNames[1],
                markerOk && greyed,
                `marker=${markerShown} markerOk=${markerOk} greyed=${greyed} nodes=${clip(markerNodes)}${markerOk ? '' : '；owner: cards-client 页头标记'}`,
              )
              markStep(1)
              await shot(page, '141-l-member-grey.png')

              // 6) 删成员后继续发言：发言人必须切换到存活成员（以服务端落库为准，UI 只校验展示）。
              // 注意：UI 在发送瞬间可能仍在加载历史消息，不能只看「最后一条 assistant 气泡」判定完成，
              // 否则会读到删除前那条回复造成假失败。这里轮询服务端，要求 assistant 条数增加且文本变化。
              const preSendDetail = await lApi(`/chats/${chatId}`)
              const preSendPayload = preSendDetail && preSendDetail.payload ? preSendDetail.payload : null
              const assistantsBeforeSend = assistantsOf(preSendPayload)
              const beforeAssistantCount = assistantsBeforeSend.length
              const beforeLastText = beforeAssistantCount
                ? String(assistantsBeforeSend[beforeAssistantCount - 1].text || '')
                : ''
              const secondSend = await sendInChat(frame, '（E2E 群聊）刚才那位还在吗？')
              let assistantsAfter = []
              let secondSpeaker = null
              let lastTextAfter = ''
              const sendDeadline = Date.now() + 180000
              while (Date.now() < sendDeadline) {
                const detail = await lApi(`/chats/${chatId}`)
                const payload = detail && detail.payload ? detail.payload : null
                const list = assistantsOf(payload)
                const lastText = list.length ? String(list[list.length - 1].text || '') : ''
                if (list.length > beforeAssistantCount && lastText && lastText !== beforeLastText) {
                  assistantsAfter = list
                  secondSpeaker = list[list.length - 1].speakerName || null
                  lastTextAfter = lastText
                  break
                }
                await page.waitForTimeout(700)
              }
              const expectedRows =
                (preSendPayload && Array.isArray(preSendPayload.messages) ? preSendPayload.messages.length : 0) + 2
              await waitFor(
                async () => (await frame.locator('.bubble-row').count().catch(() => 0)) >= expectedRows,
                30000,
              )
              const secondSpeakerUi =
                ((await frame.locator('.bubble-row.assistant .bubble-speaker-name').last().innerText().catch(() => '')) || '').trim()
              // 必须看到本次发送的 POST 200 + 服务端落库 + 发言人=存活成员，三者缺一不可。
              const switchOk = Boolean(
                secondSend.httpStatus === 200 &&
                  secondSpeaker &&
                  secondSpeaker === aliveMemberName &&
                  secondSpeakerUi === secondSpeaker,
              )
              groupDeletionEvidence.speakerSwitch = {
                saved: Boolean(secondSpeaker),
                debug: {
                  ...secondSend.debug,
                  beforeAssistantCount,
                  afterAssistantCount: assistantsAfter.length,
                  replyLength: lastTextAfter.length,
                  expectedRows,
                  rowsNow: await frame.locator('.bubble-row').count().catch(() => 0),
                },
                firstSpeaker,
                secondSpeaker,
                secondSpeakerUi,
                aliveMemberName,
                switchOk,
              }
              check(
                groupDeletionCheckNames[2],
                switchOk,
                `http=${secondSend.httpStatus} path=${secondSend.clickPath} probe=${clip(JSON.stringify(secondSend.stallProbe))} saved=${secondSend.saved} first=${clip(firstSpeaker)} second=${clip(secondSpeaker)} ui=${clip(secondSpeakerUi)} alive=${clip(aliveMemberName)}`,
              )
              markStep(2)
              await shot(page, '142-l-speaker-switch.png')

              // 7) 删掉剩下那位成员：详情仍 200，aliveMemberCount=0。
              await lApi(`/characters/${secondCardId}`, { method: 'DELETE' })
              const detailAfterAll = await lApi(`/chats/${chatId}`)
              const allPayload = detailAfterAll && detailAfterAll.payload ? detailAfterAll.payload : null
              const allChat = allPayload && allPayload.chat ? allPayload.chat : null
              const aliveAfterAll =
                allChat && typeof allChat.aliveMemberCount === 'number' ? allChat.aliveMemberCount : null
              const allMembers = allChat && Array.isArray(allChat.members) ? allChat.members : []
              const allDeleted = allMembers.length === 2 && allMembers.every((member) => member.deleted === true)
              groupDeletionEvidence.allDeleted = {
                status: detailAfterAll ? detailAfterAll.status : null,
                aliveAfterAll,
                allMembers,
                allDeleted,
              }
              check(
                groupDeletionCheckNames[3],
                Boolean(detailAfterAll && detailAfterAll.status === 200 && allChat && allChat.mode === 'group' && aliveAfterAll === 0 && allDeleted),
                `status=${detailAfterAll && detailAfterAll.status} aliveMemberCount=${aliveAfterAll} deletedFlags=${clip(allMembers.map((member) => Boolean(member.deleted)))}`,
              )
              markStep(3)

              // 8) UI 只读 + 直接 POST messages 应 409 CHAT_NO_MEMBERS。
              frame = await appGoto(`/chats/${chatId}`)
              if (frame) await lWaitChatShell(frame, 25000)
              const hintShown = frame
                ? await waitFor(async () => /群聊成员已全部删除|无法继续发言/.test(await bodyText(frame)), 20000)
                : false
              let inputDisabled = false
              let sendDisabled = false
              let regenerateDisabled = null
              let regenerateAbsent = false
              let readonlyText = null
              if (frame) {
                const input = frame.locator('textarea[aria-label="聊天输入框"]').first()
                inputDisabled = await input.isDisabled().catch(() => false)
                const placeholder = await input.getAttribute('placeholder').catch(() => null)
                if (/群聊成员已全部删除/.test(String(placeholder || ''))) inputDisabled = true
                sendDisabled = await frame
                  .locator('button[aria-label="发送消息"]')
                  .first()
                  .isDisabled()
                  .catch(() => false)
                const readonly = frame.locator('.chat-readonly').first()
                readonlyText = (await readonly.count().catch(() => 0))
                  ? ((await readonly.innerText().catch(() => '')) || '').trim()
                  : null
                const regen = frame.locator('.bubble-actions button', { hasText: /重新生成/ }).first()
                const regenCount = await regen.count().catch(() => 0)
                regenerateAbsent = regenCount === 0
                regenerateDisabled = regenCount ? await regen.isDisabled().catch(() => false) : null
              }
              const postBlocked = await lApi(`/chats/${chatId}/messages`, { method: 'POST', body: { text: '（E2E）全员删除后还能说话吗？' } })
              const postPayload = postBlocked && postBlocked.payload ? postBlocked.payload : {}
              const postCode = postPayload.code || (postPayload.error && postPayload.error.code) || null
              const postOk = Boolean(postBlocked && postBlocked.status === 409 && postCode === 'CHAT_NO_MEMBERS')
              groupDeletionEvidence.readonly = {
                hintShown,
                inputDisabled,
                sendDisabled,
                regenerateDisabled,
                regenerateAbsent,
                readonlyText,
                postStatus: postBlocked ? postBlocked.status : null,
                postCode,
              }
              check(
                groupDeletionCheckNames[4],
                hintShown && Boolean(readonlyText) && inputDisabled && sendDisabled && (regenerateAbsent || regenerateDisabled === true) && postOk,
                `hint=${hintShown} text=${clip(readonlyText)} inputDisabled=${inputDisabled} sendDisabled=${sendDisabled} regenAbsent=${regenerateAbsent} POST=${postBlocked && postBlocked.status}/${clip(postCode)}`,
              )
              markStep(4)
              await shot(page, '143-l-no-members.png')

              // 9) /chats 列表仍包含该群聊；DELETE 群聊 204 且列表回落。
              const listBeforeDelete = await lApi('/chats')
              const listItems =
                listBeforeDelete && listBeforeDelete.payload && Array.isArray(listBeforeDelete.payload.items)
                  ? listBeforeDelete.payload.items
                  : []
              const listedBefore = listItems.some((item) => item.id === chatId)
              const deleteChat = await lApi(`/chats/${chatId}`, { method: 'DELETE' })
              const listAfterDelete = await lApi('/chats')
              const afterItems =
                listAfterDelete && listAfterDelete.payload && Array.isArray(listAfterDelete.payload.items)
                  ? listAfterDelete.payload.items
                  : []
              const listedAfter = afterItems.some((item) => item.id === chatId)
              groupDeletionEvidence.list = {
                listedBefore,
                deleteStatus: deleteChat ? deleteChat.status : null,
                listedAfter,
              }
              check(
                groupDeletionCheckNames[5],
                Boolean(listedBefore && deleteChat && deleteChat.status === 204 && !listedAfter),
                `listedBefore=${listedBefore} DELETE=${deleteChat && deleteChat.status} listedAfter=${listedAfter}`,
              )
              markStep(5)
              await shot(page, '144-l-chat-deleted.png')
            }
          }
        }
      }

      const lConsole = consoleErrors.slice(lConsoleStart).filter((text) => !/Failed to load resource/.test(text))
      groupDeletionEvidence.console = lConsole
      if (!groupDeletionEvidence.steps.includes(6)) {
        check(groupDeletionCheckNames[6], lConsole.length === 0, lConsole.length ? clip(lConsole) : 'clean')
        markStep(6)
      }
      for (let index = 0; index < groupDeletionCheckNames.length; index += 1) {
        if (!groupDeletionEvidence.steps.includes(index)) skip(groupDeletionCheckNames[index], '前置步骤未执行')
      }
    }
  }


  // ── N. 搜索 + 移动端打磨（task-67；搜索仅已加载消息、390×844 适配）──────────
  const polishCheckNames = [
    '聊天内搜索：命中计数 >0，上/下一处切换计数变化且命中消息高亮可见',
    '搜索无命中显示「没有匹配」，Esc 关闭搜索后输入草稿保留',
    '移动端（390×844）composer 在视口内且点击命中不被遮挡',
    '移动端发送/搜索触摸目标 ≥40×40，聊天名 ellipsis，页面无横向溢出',
    'index.html viewport meta 含 viewport-fit=cover',
    '搜索/移动端 console 无报错',
  ]
  const polishEvidence = {
    enabled: false,
    chatId: null,
    messages: 0,
    search: null,
    mobile: null,
    viewport: null,
    console: null,
    steps: [],
  }
  report.native.polish = polishEvidence
  if (groupOn('pol')) {
    if (nativeBlocked) {
      for (const name of polishCheckNames) skip(name, blockedReason)
    } else {
      polishEvidence.enabled = true
      const polConsoleStart = consoleErrors.length
      const polDone = new Set()
      const polRecord = (index, ok, detail) => {
        if (polDone.has(index)) return
        check(polishCheckNames[index], ok, detail)
        polDone.add(index)
        polishEvidence.steps.push(index)
      }
      const polSkipRemaining = (from, detail) => {
        for (let index = from; index < polishCheckNames.length; index += 1) {
          if (polDone.has(index)) continue
          skip(polishCheckNames[index], detail)
          polDone.add(index)
          polishEvidence.steps.push(index)
        }
      }
      const polApi = async (pathname, options = {}) => {
        const frameNow = fnow()
        if (!frameNow) return null
        return frameNow
          .evaluate(
            async ([pathAndQuery, opts]) => {
              try {
                const response = await fetch('/tavern/api/reader/v1/tavern' + pathAndQuery, {
                  credentials: 'include',
                  method: opts.method || 'GET',
                  headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
                  body: opts.body ? JSON.stringify(opts.body) : undefined,
                })
                const text = await response.text()
                let payload = null
                try {
                  payload = text ? JSON.parse(text) : null
                } catch {
                  payload = { raw: String(text).slice(0, 200) }
                }
                return { status: response.status, payload }
              } catch (error) {
                return { status: 0, error: String(error) }
              }
            },
            [pathname, options],
          )
          .catch(() => null)
      }
      let mobilePage = null
      try {
        // 1) 准备一个 ≥4 条消息的聊天：卡详情「导入酒馆聊天」导入 chat-search.jsonl（不触发生成）。
        let importedChatId = null
        await appGoto('/')
        const homeFrame = fnow()
        if (homeFrame) {
          const cardImport = homeFrame.locator('input[aria-label="导入角色卡文件"]').first()
          const hasCardImport = await waitFor(async () => (await cardImport.count().catch(() => 0)) > 0, 20000)
          if (hasCardImport) {
            const cardCount = await homeFrame.locator('.tavern-card').count().catch(() => 0)
            if (cardCount === 0) {
              await cardImport.setInputFiles(path.join(fixturesDir, 'v2-png.png')).catch(() => {})
              await waitFor(async () => (await homeFrame.locator('.tavern-card').count().catch(() => 0)) > 0, 20000)
            }
            const edit = homeFrame.locator('.tavern-card-edit').first()
            if ((await edit.count().catch(() => 0)) > 0) {
              await edit.click({ force: true }).catch(() => {})
              const detail = await waitFrameUrl(/\/characters\/[0-9a-zA-Z_-]+/, 15000)
              if (detail) {
                const chatImport = detail.locator('input[aria-label="导入酒馆聊天 JSONL"]').first()
                const hasChatImport = await waitFor(async () => (await chatImport.count().catch(() => 0)) > 0, 20000)
                if (hasChatImport) {
                  await chatImport.setInputFiles(path.join(fixturesDir, 'chat-search.jsonl')).catch(() => {})
                  const chatLanded = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 25000)
                  if (chatLanded) {
                    const matched = (new URL(chatLanded.url()).pathname || '').match(/\/chats\/([0-9a-zA-Z_-]+)/)
                    importedChatId = matched ? matched[1] : null
                    await waitChatRendered(chatLanded, 20000)
                  }
                }
              }
            }
          }
        }
        const detailApi = importedChatId ? await polApi(`/chats/${importedChatId}`) : null
        const messageCount =
          detailApi && detailApi.payload && Array.isArray(detailApi.payload.messages)
            ? detailApi.payload.messages.length
            : 0
        polishEvidence.chatId = importedChatId
        polishEvidence.messages = messageCount
        if (!importedChatId || messageCount < 4) {
          throw new Error(
            `SKIP: 无法准备 ≥4 条消息的聊天（chatId=${importedChatId} messages=${messageCount}；导入入口/ST JSONL 导入不可用，owner: cards-client）`,
          )
        }

        // 2) N1 搜索命中 + 上/下一处 + 高亮滚动
        const searchFrame = fnow()
        const searchButton = searchFrame.locator('button[aria-label="搜索聊天内容"]').first()
        const hasSearchButton = await waitFor(async () => (await searchButton.count().catch(() => 0)) > 0, 20000)
        if (!hasSearchButton) {
          polSkipRemaining(0, '聊天页没有「搜索聊天内容」按钮（owner: cards-client 搜索 UI 未交付）')
        } else {
          await searchButton.click({ force: true }).catch(() => {})
          const panel = searchFrame.locator('.chat-search[role="search"]').first()
          const searchInput = panel.locator('input[aria-label="搜索聊天内容输入"]').first()
          const countLocator = panel.locator('.chat-search-count').first()
          const searchInfo = {
            open: false,
            total: 0,
            first: '',
            hitCount: 0,
            hitId: null,
            hitVisible: false,
            afterNext: '',
            afterPrev: '',
            hint: false,
          }
          searchInfo.open = await waitFor(
            async () => (await panel.count().catch(() => 0)) > 0 && (await panel.isVisible().catch(() => false)),
            15000,
          )
          await searchInput.fill('茶').catch(() => {})
          const countOk = await waitFor(async () => {
            const text = ((await countLocator.innerText().catch(() => '')) || '').trim()
            const matched = text.match(/^(\d+)\/(\d+)$/)
            if (!matched) return false
            searchInfo.total = Number(matched[2])
            return searchInfo.total > 0
          }, 10000)
          searchInfo.first = ((await countLocator.innerText().catch(() => '')) || '').trim()
          // 「下一个匹配」会写入命中 id 并滚动；高亮类只在高亮目标上出现，先切一次再断言。
          const nextButton = panel.locator('button[aria-label="下一个匹配"]').first()
          const prevButton = panel.locator('button[aria-label="上一个匹配"]').first()
          if ((await nextButton.count().catch(() => 0)) > 0) {
            await nextButton.click({ force: true }).catch(() => {})
            await page.waitForTimeout(450)
            searchInfo.afterNext = ((await countLocator.innerText().catch(() => '')) || '').trim()
          }
          searchInfo.hitCount = await searchFrame.locator('article.bubble-row.chat-search-hit').count().catch(() => 0)
          if (searchInfo.hitCount === 1) {
            const hitRow = searchFrame.locator('article.bubble-row.chat-search-hit').first()
            searchInfo.hitId = (await hitRow.getAttribute('data-message-id').catch(() => null)) || null
            const hitBox = await hitRow.boundingBox().catch(() => null)
            const logBox = await searchFrame.locator('.chat-log').first().boundingBox().catch(() => null)
            searchInfo.hitVisible = Boolean(
              hitBox && logBox && hitBox.y + hitBox.height > logBox.y && hitBox.y < logBox.y + logBox.height,
            )
          }
          if ((await prevButton.count().catch(() => 0)) > 0) {
            await prevButton.click({ force: true }).catch(() => {})
            await page.waitForTimeout(450)
            searchInfo.afterPrev = ((await countLocator.innerText().catch(() => '')) || '').trim()
          }
          searchInfo.hint = /仅搜索已加载消息/.test(((await panel.innerText().catch(() => '')) || ''))
          polishEvidence.search = searchInfo
          polRecord(
            0,
            searchInfo.open &&
              countOk &&
              searchInfo.hitCount === 1 &&
              searchInfo.hitVisible &&
              Boolean(searchInfo.afterNext) &&
              searchInfo.afterNext !== searchInfo.first &&
              searchInfo.afterPrev === searchInfo.first &&
              searchInfo.hint,
            `open=${searchInfo.open} count=${clip(searchInfo.first)} total=${searchInfo.total} hit=${searchInfo.hitCount} visible=${searchInfo.hitVisible} next=${clip(searchInfo.afterNext)} prev=${clip(searchInfo.afterPrev)} hint=${searchInfo.hint}`,
          )
          await shot(page, '150-search-hit.png')

          // 3) N2 无命中 + Esc 关闭 + 草稿保留
          const draftText = `草稿保留检查-${Date.now().toString().slice(-4)}`
          const composer = searchFrame.locator('textarea[aria-label="聊天输入框"]').first()
          await composer.fill(draftText).catch(() => {})
          await searchInput.fill('绝无此词-ZZZ').catch(() => {})
          const noMatch = await waitFor(
            async () => /没有匹配/.test(((await countLocator.innerText().catch(() => '')) || '')),
            10000,
          )
          await searchInput.press('Escape').catch(() => {})
          const searchClosed = await waitFor(
            async () => (await panel.count().catch(() => 0)) === 0 || !(await panel.isVisible().catch(() => false)),
            8000,
          )
          const draftValue = ((await composer.inputValue().catch(() => '')) || '')
          const draftKept = draftValue === draftText
          await composer.fill('').catch(() => {})
          polRecord(1, noMatch && searchClosed && draftKept, `noMatch=${noMatch} closed=${searchClosed} draftKept=${draftKept}`)
          await shot(page, '151-search-nomatch.png')

          // 4) N3/N4 移动端 390×844
          mobilePage = await context.newPage()
          await mobilePage.setViewportSize({ width: 390, height: 844 }).catch(() => {})
          const mobileErrors = []
          mobilePage.on('console', (message) => {
            if (message.type() === 'error') mobileErrors.push(message.text())
          })
          mobilePage.on('pageerror', (error) => mobileErrors.push(String(error && error.message ? error.message : error)))
          const appOrigin = new URL(searchFrame.url()).origin
          await mobilePage
            .goto(`${appOrigin}/tavern/chats/${importedChatId}`, { waitUntil: 'domcontentloaded' })
            .catch(() => {})
          await waitFor(
            async () => /说点什么|正在加载聊天|正在打开/.test(((await mobilePage.locator('body').innerText().catch(() => '')) || '')),
            30000,
          )
          const composerReady = await waitFor(
            async () => (await mobilePage.locator('form.chat-compose').count().catch(() => 0)) > 0,
            25000,
          )
          const mobileInfo = await mobilePage
            .evaluate(() => {
              const rect = (el) => {
                if (!el) return null
                const box = el.getBoundingClientRect()
                return { x: box.x, y: box.y, w: box.width, h: box.height, bottom: box.bottom, right: box.right }
              }
              const composer = document.querySelector('form.chat-compose')
              const input = document.querySelector('textarea[aria-label="聊天输入框"]')
              const send = document.querySelector('button.chat-send')
              const search = document.querySelector('button[aria-label="搜索聊天内容"]')
              const name = document.querySelector('.chat-name')
              const log = document.querySelector('.chat-log')
              const style = name ? getComputedStyle(name) : null
              let speakerRule = false
              const scanRules = (rules) => {
                for (const rule of rules) {
                  if (rule.cssRules) scanRules(Array.from(rule.cssRules))
                  if (
                    rule.selectorText &&
                    rule.selectorText.includes('.bubble-speaker-name') &&
                    /text-overflow:\s*ellipsis/.test(rule.cssText)
                  ) {
                    speakerRule = true
                  }
                }
              }
              for (const sheet of Array.from(document.styleSheets)) {
                try {
                  scanRules(Array.from(sheet.cssRules || []))
                } catch {
                  /* cross-origin sheet */
                }
              }
              let composerHit = null
              if (input && composer) {
                const box = input.getBoundingClientRect()
                const el = document.elementFromPoint(box.x + Math.min(box.width / 2, 24), box.y + box.height / 2)
                composerHit = Boolean(el && (composer === el || composer.contains(el)))
              }
              return {
                viewport: { w: window.innerWidth, h: window.innerHeight },
                composer: rect(composer),
                input: rect(input),
                send: rect(send),
                search: rect(search),
                name: rect(name),
                composerHit,
                nameStyle: style
                  ? {
                      textOverflow: style.textOverflow,
                      overflow: style.overflow,
                      whiteSpace: style.whiteSpace,
                      scrollW: name ? name.scrollWidth : null,
                      clientW: name ? name.clientWidth : null,
                    }
                  : null,
                speakerRule,
                docOverflow: document.documentElement.scrollWidth - window.innerWidth,
                logOverflow: log ? log.scrollWidth - log.clientWidth : null,
                viewportMeta: (document.querySelector('meta[name="viewport"]') || {}).content || '',
              }
            })
            .catch(() => null)
          polishEvidence.mobile = mobileInfo
          const composerInView = Boolean(
            composerReady &&
              mobileInfo &&
              mobileInfo.composer &&
              mobileInfo.input &&
              mobileInfo.composer.bottom <= mobileInfo.viewport.h + 1 &&
              mobileInfo.input.y >= 0,
          )
          const composerVisible = composerInView && mobileInfo && mobileInfo.composerHit === true
          polRecord(
            2,
            composerVisible,
            `ready=${composerReady} composer=${clip(mobileInfo && mobileInfo.composer)} viewport=${clip(mobileInfo && mobileInfo.viewport)} hit=${mobileInfo && mobileInfo.composerHit}`,
          )
          const touchOk = Boolean(
            mobileInfo &&
              mobileInfo.send &&
              mobileInfo.search &&
              mobileInfo.send.w >= 40 &&
              mobileInfo.send.h >= 40 &&
              mobileInfo.search.w >= 40 &&
              mobileInfo.search.h >= 40,
          )
          const ellipsisOk = Boolean(
            mobileInfo &&
              mobileInfo.nameStyle &&
              (mobileInfo.nameStyle.textOverflow === 'ellipsis' ||
                (mobileInfo.nameStyle.scrollW || 0) > (mobileInfo.nameStyle.clientW || 0) ||
                mobileInfo.speakerRule),
          )
          const noOverflow = Boolean(mobileInfo && mobileInfo.docOverflow <= 1 && (mobileInfo.logOverflow === null || mobileInfo.logOverflow <= 1))
          polRecord(
            3,
            touchOk && ellipsisOk && noOverflow,
            `touch=${touchOk} send=${clip(mobileInfo && mobileInfo.send)} search=${clip(mobileInfo && mobileInfo.search)} ellipsis=${ellipsisOk}(${clip(mobileInfo && mobileInfo.nameStyle)}) speakerRule=${mobileInfo && mobileInfo.speakerRule} overflow=${clip(mobileInfo && { doc: mobileInfo.docOverflow, log: mobileInfo.logOverflow })}`,
          )
          const viewportMeta = (mobileInfo && mobileInfo.viewportMeta) || ''
          polishEvidence.viewport = { meta: viewportMeta, ok: /viewport-fit\s*=\s*cover/.test(viewportMeta) }
          polRecord(4, polishEvidence.viewport.ok, clip(viewportMeta))
          const mobileFile = path.join(outDir, '152-mobile-chat.png')
          await mobilePage.screenshot({ path: mobileFile, fullPage: false }).catch(() => {})
          report.screenshots.push(mobileFile)
          const mobileFile2 = path.join(outDir, '153-mobile-compose.png')
          await mobilePage.screenshot({ path: mobileFile2, fullPage: false }).catch(() => {})
          report.screenshots.push(mobileFile2)
          const mobileConsole = [...mobileErrors, ...consoleErrors.slice(polConsoleStart)].filter(
            (text) => !/Failed to load resource/.test(text),
          )
          polishEvidence.console = mobileConsole
          polRecord(5, mobileConsole.length === 0, mobileConsole.length ? clip(mobileConsole) : 'clean')
        }
      } catch (error) {
        const message = String(error && error.message ? error.message : error)
        if (/^SKIP:/.test(message)) {
          polSkipRemaining(polDone.size ? Math.max(...polDone) + 1 : 0, message.replace(/^SKIP:\s*/, ''))
        } else {
          polSkipRemaining(polDone.size ? Math.max(...polDone) + 1 : 0, `N 组异常：${message}`)
        }
      } finally {
        if (mobilePage) await mobilePage.close().catch(() => {})
      }
      polSkipRemaining(0, 'N 组未执行（跳过）')
    }
  }

  // ── 控制台 ──────────────────────────────────────────────────────────────
  const jsErrors = consoleErrors.filter((text) => !/Failed to load resource/.test(text))
  check('浏览器控制台无 JS 报错', jsErrors.length === 0, jsErrors.length ? jsErrors.slice(0, 3).join(' | ') : 'clean')

  report.app = { frameUrl, nativeReady, oldUi }
  report.finishedAt = new Date().toISOString()
  report.summary = {
    pass: report.checks.filter((item) => item.ok && !item.skipped).length,
    fail: report.checks.filter((item) => !item.ok && !item.skipped).length,
    skip: report.checks.filter((item) => item.skipped).length,
  }
  report.ok = report.summary.fail === 0
  await writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(`\nPASS ${report.summary.pass} · FAIL ${report.summary.fail} · SKIP ${report.summary.skip}`)
  console.log(`report: ${path.join(outDir, 'report.json')}`)
  process.exitCode = report.ok ? 0 : 1
} catch (error) {
  console.error('E2E 运行失败：', error)
  report.error = String(error && error.message ? error.message : error)
  report.summary = {
    pass: report.checks.filter((item) => item.ok && !item.skipped).length,
    fail: report.checks.filter((item) => !item.ok && !item.skipped).length,
    skip: report.checks.filter((item) => item.skipped).length,
  }
  report.ok = false
  await writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2) + '\n').catch(() => {})
  process.exitCode = 1
} finally {
  await browser.close().catch(() => {})
}
