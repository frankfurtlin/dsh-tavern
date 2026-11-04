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
 *   R. 重新生成：最后一条 assistant「重新生成」→ 文本变化、版本数增加、消息数不重复。
 *   H. 历史：角色页聊天历史列表 → 再进入消息完整。
 *   S. 世界书：卡详情新增条目（key「茶」，position after_char）→ 列表可见 → 删除；
 *      P0 无注入预览 UI 时记 SKIP 并注明 owner。
 *   M. 旧路由：/tavern/explore、/tavern/works、/tavern/worlds/scripts、
 *      /tavern/conversations/world/x → 落 / 或无 404 死链；页面无「探索作品」入口。
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
  native: { home: null, characters: null, chat: null, regenerate: null, history: null, worldInfo: null, oldRoutes: null },
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
    return landed || fnow()
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
    const card = await findCard(cardName)
    if (!card) return null
    await card.click({ force: true }).catch(async () => {
      await card.evaluate((el) => el.click()).catch(() => {})
    })
    // 原生交互：点卡直接进最近聊天（没有则自动新建）→ /chats/<id>；若落在详情页再点「开新聊天」。
    let chatFrame = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 25000)
    let detail = null
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
  async function rowText(row) {
    if (!row) return ''
    const text = row.locator('.bubble-text').first()
    if (await text.count().catch(() => 0)) return ((await text.innerText().catch(() => '')) || '').trim()
    return ((await row.innerText().catch(() => '')) || '').trim()
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
      const opened = await openChat()
      chatFrame = opened && opened.chatFrame ? opened.chatFrame : null
      cardUrl = opened && opened.detail ? opened.detail.url() : null
      chatEvidence.chatUrl = chatFrame ? chatFrame.url() : null
      chatEvidence.cardUrl = cardUrl
      check('点卡进入角色页并可开新聊天', Boolean(chatFrame), chatFrame ? `detail=${cardUrl} chat=${chatFrame.url()}` : `点卡后没有进入 /chats/<id>（detail=${cardUrl}；owner: cards-client 角色页/聊天路由）`)
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

  // ── H. 聊天历史 ──────────────────────────────────────────────────────────
  const historyEvidence = { listShown: false, reentered: false, messagesIntact: false }
  report.native.history = historyEvidence
  if (groupOn('h')) {
    if (nativeBlocked) {
      skip('返回角色页看到聊天历史列表', blockedReason)
      skip('从历史重新进入聊天，消息完整', blockedReason)
    } else {
      const detail = await openDetail()
      const listShown = detail
        ? await waitFor(async () => (await detail.locator('section[aria-label="聊天历史"] .chat-history-item, .chat-history-item').count().catch(() => 0)) > 0, 20000)
        : false
      historyEvidence.listShown = listShown
      const itemCount = detail ? await detail.locator('.chat-history-item').count().catch(() => 0) : 0
      check('返回角色页看到聊天历史列表', listShown, listShown ? `角色页聊天历史 ${itemCount} 条` : '角色页没有聊天历史条目（owner: cards-client 卡详情）')
      await shot(page, '97-history.png')

      if (listShown && detail) {
        await detail.locator('.chat-history-item').first().click({ force: true }).catch(() => {})
        const reentered = await waitFrameUrl(/\/chats\/[0-9a-zA-Z_-]+/, 15000)
        const intact = reentered
          ? await waitFor(async () => {
              const text = await bodyText(reentered)
              return text.includes('你好') && (regenEvidence.afterFirst60 ? text.includes(regenEvidence.afterFirst60.slice(0, 12)) : true)
            }, 20000)
          : false
        historyEvidence.reentered = Boolean(reentered)
        historyEvidence.messagesIntact = intact
        check('从历史重新进入聊天，消息完整', Boolean(reentered) && intact, `reentered=${Boolean(reentered)} intact=${intact}`)
      } else {
        skip('从历史重新进入聊天，消息完整', '前置：角色页没有聊天历史条目')
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

            const previewButton = detail.getByRole('button', { name: /预览|注入预览|试写/ }).first()
            if (await previewButton.count().catch(() => 0)) {
              const previewInput = detail.locator('input[placeholder*="说"], textarea[placeholder*="说"]').first()
              if (await previewInput.count().catch(() => 0)) await previewInput.fill('来一盏茶').catch(() => {})
              await previewButton.click({ force: true }).catch(() => {})
              const previewOk = await waitFor(async () => /【E2E-WORLD-INFO】|茶博士/.test(await bodyText(detail)), 20000)
              worldInfoEvidence.preview = { ok: previewOk }
              check('世界书条目注入预览可见', previewOk, previewOk ? '预览结果命中 key「茶」的条目' : '预览结果没有命中条目（owner: cards-client 预览 / app-auth 注入）')
            } else {
              skip('世界书条目注入预览可见', 'P0 卡详情没有注入预览 UI（owner: cards-client；服务端注入接口已由 app-auth 提供，P1 补 UI）')
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
