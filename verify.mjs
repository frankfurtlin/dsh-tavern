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
        skip('停止生成按钮出现、可点且不报错', '聊天页没有「重新生成」按钮，无法制造流式（owner: cards-client ChatPage）')
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
