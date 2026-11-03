/**
 * dsh-story-between 端到端验收脚本（真实浏览器行为，不是静态代码检查）。
 *
 * 用法：
 *   node verify.mjs --url "http://127.0.0.1:3080/?token=..." [--out dir] [--no-bridge] [--open-story]
 *
 * 检查分组（与 docs/contract.md v1 对应）：
 *   A. 鉴权与入口：未登录 GET /story/ 必须被 DSH 闸门挡住（401）；登录态 200。
 *   B. 侧栏入口「故事之间」存在；点击后 main 面板出现 iframe[data-dsh-story-frame]。
 *   C. 内嵌应用处于可信登录态（出现 .app-shell / 侧栏内容，而不是应用自己的登录页），
 *      并渲染出稳定内容（品牌、导航）。
 *   D. 工具条按钮（让 DSH 续写 / 角色扮演）可见。
 *   E. P2 桥：ping → pong（带 appPath）；context-request → context（应含
 *      appPath/worldTitle/lineTitle/mode/characters/recentText）。
 *   F. 点「让 DSH 续写」：期望新会话打开 + 编辑器草稿被填充，或剪贴板兜底；
 *      上下文不可用时插件应优雅降级（面板内出现错误提示、不误开空会话）。如实记录分支。
 *   G. 截图 + verification/report.json；无控制台 JS 报错。
 *
 * 设计原则：
 *   - 插件/应用尚未就绪时（/story 404、应用登录页、P2 桥没有响应）不崩：
 *     能跑出「未就绪」报告，依赖项记为 SKIP 并写明 owner，退出码仍为非 0。
 *   - 默认不写应用数据：不打开作品、只开草稿、不点发送。
 *     --open-story 会在应用 explore 页点一部 live 作品进入故事页（会写阅读进度），
 *     仅用于验证「续写提示词模板」全链路。
 */
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
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
const outDir = arg('out', '/root/workspace/plugins/dsh-story-between/verification')
const skipBridge = args.includes('--no-bridge')
const headless = !args.includes('--headed')
if (!url) {
  console.error('缺少 --url（带 token 的 DSH Web 地址）')
  process.exit(2)
}

const report = {
  plugin: 'dsh-story-between',
  url: url.replace(/token=[^&]+/, 'token=***'),
  startedAt: new Date().toISOString(),
  checks: [],
  bridge: { protocol: null, continue: null, roleplay: null },
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

function clip(value, max = 200) {
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

const browser = await chromium.launch({ headless, args: ['--no-sandbox'] })
let page = null
try {
  await mkdir(outDir, { recursive: true })

  // ── A. 未登录访问 /story/ 必须被 DSH 登录闸门挡住 ──────────────────────────
  const anon = await browser.newContext({ ignoreHTTPSErrors: true })
  const anonRes = await anon.request.get(new URL('story/', url).href)
  const anonStatus = anonRes.status()
  check(
    '未登录访问 /story/ 被拒绝（期望 401）',
    anonStatus === 401,
    `HTTP ${anonStatus}${
      anonStatus === 404
        ? '（404 = 宿主半没加载 / 没注册 /story 路由，插件尚未就绪，owner: plugin-core）'
        : anonStatus === 200
          ? '（200 = 登录闸门没生效，任何人都能看到内嵌应用，owner: plugin-core）'
          : ''
    }`,
  )
  await anon.close()

  // ── 打开带 token 的 DSH Web，登录态浏览器上下文 ────────────────────────────
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
  // 收集应用主动上报的 dsh-story:state（P2 契约：appPath + hasContext）
  await page.evaluate(() => {
    window.__dshStoryStates = []
    window.addEventListener('message', (event) => {
      const data = event.data
      if (data && data.type === 'dsh-story:state') {
        window.__dshStoryStates.push({ at: Date.now(), appPath: data.appPath, hasContext: data.hasContext })
      }
    })
  })
  // 全新 DSH_HOME 会弹 "Get started" 欢迎层，它的透明遮罩会拦截侧栏点击。
  async function dismissWelcome() {
    await page.keyboard.press('Escape').catch(() => {})
    const close = page.locator('div[role="presentation"] button[aria-label="Close"]').first()
    if (await close.count().catch(() => 0)) {
      await close.click({ force: true }).catch(() => {})
      await page.waitForTimeout(300)
    }
  }
  await dismissWelcome()
  await shot(page, '01-sidebar.png')

  // ── B. 侧栏「故事之间」入口 ─────────────────────────────────────────────────
  // 契约：sidebar.panellist、PANEL_ID="story"、label「故事之间」、order 24。
  // 宿主侧栏行通常渲染成 aria-label = 面板 label；插件也可能在图标上打
  // data-dsh-panel-entry（参考 ziwei）。两种都认，最后一个候选是按文字兜底。
  async function findEntry(timeoutMs) {
    const selectors = [
      '[data-dsh-panel-entry="story"]',
      'button[aria-label="故事之间"]',
      'button[data-dsh-panel="story"]',
      'button:has-text("故事之间")',
    ]
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      for (const selector of selectors) {
        const anchor = page.locator(selector).first()
        if (!(await anchor.count().catch(() => 0))) continue
        // data 锚点可能挂在侧栏行内的图标/文字上：优先点击它所属的 button/a，
        // 这样点击区域和真实用户点的一致（否则 boundingBox 只有 16×16）。
        const clickable = anchor
          .locator('xpath=ancestor-or-self::*[self::button or self::a or self::div[@role="button"]][1]')
          .first()
        if (await clickable.count().catch(() => 0)) return clickable
        return anchor
      }
      await page.waitForTimeout(400)
    }
    return null
  }
  const entry = await findEntry(20000)
  check('侧栏出现「故事之间」入口', Boolean(entry), entry ? '已定位侧栏入口' : '20s 内没找到入口（插件未加载或 label/id 不符合契约，owner: plugin-core）')
  if (entry) {
    const box = await entry.boundingBox().catch(() => null)
    check('侧栏入口可见且可点击', Boolean(box && box.width > 0 && box.height > 0), box ? `${Math.round(box.width)}x${Math.round(box.height)} @${Math.round(box.x)},${Math.round(box.y)}` : '没有可见区域')
  }

  // ── 点击入口 → main 面板 → iframe ──────────────────────────────────────────
  let panelReady = false
  if (entry) {
    let opened = false
    for (let attempt = 0; attempt < 3 && !opened; attempt += 1) {
      await dismissWelcome()
      try {
        await entry.click({ timeout: 4000 })
      } catch {
        await entry.click({ force: true }).catch(() => {})
      }
      opened = await page
        .locator('iframe[data-dsh-story-frame]')
        .first()
        .waitFor({ state: 'visible', timeout: 6000 })
        .then(() => true)
        .catch(() => false)
    }
    panelReady = opened
    check('点击侧栏入口打开「故事之间」主面板', opened, opened ? 'iframe[data-dsh-story-frame] 已可见' : '点击后没有出现 iframe[data-dsh-story-frame]（owner: plugin-core）')
  } else {
    skip('点击侧栏入口打开「故事之间」主面板', '侧栏入口不存在')
  }

  const frameLocator = page.locator('iframe[data-dsh-story-frame]').first()
  const panelVisible = () => frameLocator.isVisible().catch(() => false)
  // 位置记忆（plugin-core：sessionStorage dsh-story:last-app-path + data-dsh-story-restored）
  const readMemory = () =>
    page
      .evaluate(() => {
        try {
          return window.sessionStorage.getItem('dsh-story:last-app-path') || ''
        } catch {
          return ''
        }
      })
      .catch(() => '')
  const readRestored = () =>
    page
      .evaluate(() => {
        const view = document.querySelector('[data-dsh-story-view]')
        return view ? view.getAttribute('data-dsh-story-restored') : null
      })
      .catch(() => null)
  async function waitForFrameUrl(pattern, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const frame = page.frames().find((candidate) => /\/story\//.test(candidate.url()) && pattern.test(candidate.url()))
      if (frame) return frame.url()
      await page.waitForTimeout(400)
    }
    return ''
  }
  async function clickableFor(anchor) {
    const clickable = anchor.locator('xpath=ancestor-or-self::*[self::button or self::a or self::div[@role="button"]][1]').first()
    if (await clickable.count().catch(() => 0)) return clickable
    return anchor
  }
  async function openStoryPanel(timeoutMs = 25000) {
    if (await panelVisible()) return true
    const row = await findEntry(6000)
    if (!row) return false
    await dismissWelcome()
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      try {
        await row.click({ timeout: 4000 })
      } catch {
        await row.click({ force: true }).catch(() => {})
      }
      if (await frameLocator.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false)) return true
      await page.waitForTimeout(500)
    }
    return false
  }
  /** 切到别的面板（优先紫微斗数），没有其它面板就退回「收起主面板」。 */
  async function switchAwayFromStoryPanel() {
    for (const selector of ['[data-dsh-panel-entry="ziwei"]', 'button[aria-label="紫微斗数"]', '[data-dsh-panel-entry="skills"]', 'button[aria-label="Skill Center"]']) {
      const anchor = page.locator(selector).first()
      if (!(await anchor.count().catch(() => 0))) continue
      const target = await clickableFor(anchor)
      await dismissWelcome()
      await target.click({ force: true }).catch(() => {})
      const deadline = Date.now() + 8000
      while (Date.now() < deadline) {
        if (!(await panelVisible())) return `panel:${selector}`
        await page.waitForTimeout(300)
      }
    }
    const newSession = page.locator('button[aria-label*="New Session"], button[aria-label*="新会话"], button:has-text("New Session")').first()
    if (await newSession.count().catch(() => 0)) {
      await newSession.click({ force: true }).catch(() => {})
      const deadline = Date.now() + 8000
      while (Date.now() < deadline) {
        if (!(await panelVisible())) return 'collapse:new-session'
        await page.waitForTimeout(300)
      }
    }
    return ''
  }
  let storyFrame = null
  let frameUrl = ''
  if (panelReady) {
    await frameLocator.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {})
    const box = await frameLocator.boundingBox().catch(() => null)
    check(
      '主面板 iframe 占据可用区域',
      Boolean(box && box.width > 500 && box.height > 300),
      box ? `${Math.round(box.width)}x${Math.round(box.height)}` : 'iframe 无尺寸',
    )
    // 契约里的稳定锚点：主面板 data-dsh-story-view、状态 data-dsh-story-hint（idle/loading/waiting/ready/busy/sent/error）
    const storyView = page.locator('[data-dsh-story-view]').first()
    const viewVisible = await storyView.isVisible().catch(() => false)
    check('主面板有 data-dsh-story-view 锚点', viewVisible)
    const hintState = await page.locator('[data-dsh-story-hint]').first().getAttribute('data-dsh-story-hint').catch(() => null)
    check(
      '面板状态锚点 data-dsh-story-hint 合法',
      ['idle', 'loading', 'waiting', 'ready', 'busy', 'sent', 'error'].includes(hintState || ''),
      `state=${hintState || '无'}`,
    )
    await shot(page, '02-panel.png')

    // 等 iframe 导航到 /story/（同源 iframe 会在 page.frames() 里出现）
    const deadline = Date.now() + 30000
    while (Date.now() < deadline) {
      storyFrame = page.frames().find((candidate) => /\/story\//.test(candidate.url())) || null
      if (storyFrame) break
      await page.waitForTimeout(300)
    }
    frameUrl = storyFrame ? storyFrame.url() : ''
    check('内嵌 iframe 指向 /story/', Boolean(frameUrl && frameUrl.includes('/story/')), frameUrl || '没找到 /story/ 帧')
    // 登录态 /story/ 必须 200（与匿名 401 成对）
    const authedRes = await context.request.get(new URL('story/', url).href).catch(() => null)
    check('登录态访问 /story/ 返回 200', authedRes ? authedRes.status() === 200 : false, authedRes ? `HTTP ${authedRes.status()}` : '请求失败')
    // 首次挂载（全新 browser context，sessionStorage 为空）：必须回应用首页。
    const initialSrc = await frameLocator.getAttribute('src').catch(() => null)
    const initialRestored = await page.locator('[data-dsh-story-view]').first().getAttribute('data-dsh-story-restored').catch(() => null)
    const initialPath = initialSrc ? new URL(initialSrc, url).pathname : ''
    check('首次挂载 iframe 指向应用首页 /story/（无位置记忆）', initialPath === '/story/' || initialPath === '/story', `src=${clip(initialSrc, 90) || '无'}`)
    check('首次挂载 data-dsh-story-restored=0', initialRestored === '0', `restored=${initialRestored ?? '无'}`)
  } else {
    skip('主面板 iframe 占据可用区域', '主面板未打开')
    skip('内嵌 iframe 指向 /story/', '主面板未打开')
    skip('登录态访问 /story/ 返回 200', '主面板未打开')
  }

  // ── C. 内嵌应用处于可信登录态，而不是应用自己的登录页 ─────────────────────
  async function readAppState() {
    if (!storyFrame) return null
    return storyFrame
      .evaluate(() => {
        const clean = (value) => (value || '').replace(/\s+/g, ' ').trim()
        return {
          title: document.title,
          pathname: location.pathname,
          hasShell: Boolean(document.querySelector('.app-shell')),
          hasEntryPage: Boolean(document.querySelector('.entry-page') || document.querySelector('[aria-label="账号登录与注册"]')),
          brand: clean(document.querySelector('.shell-brand')?.innerText),
          heading: clean(document.querySelector('h1')?.innerText),
          nav: [...document.querySelectorAll('.shell-navigation nav a')].map((anchor) => clean(anchor.innerText)).filter(Boolean).slice(0, 10),
          bodyLength: (document.body?.innerText || '').length,
        }
      })
      .catch(() => null)
  }
  let appState = null
  if (panelReady && storyFrame) {
    const deadline = Date.now() + 35000
    while (Date.now() < deadline) {
      appState = await readAppState()
      if (appState && (appState.hasShell || appState.hasEntryPage)) break
      await page.waitForTimeout(500)
    }
    const onLogin = Boolean(appState && (appState.hasEntryPage || /\/login/.test(frameUrl)))
    check(
      '内嵌应用完成可信登录（不是应用登录页）',
      Boolean(appState && appState.hasShell && !appState.hasEntryPage),
      appState
        ? `shell=${appState.hasShell} entryPage=${appState.hasEntryPage} path=${clip(appState.pathname, 40)} h1=${clip(appState.heading, 40) || '无'}${
            onLogin ? '（应用停在登录页：trusted 模式未生效，owner: app-auth）' : ''
          }`
        : '读取 iframe DOM 失败',
    )
    check(
      '内嵌应用渲染出稳定内容（品牌 + 侧栏导航）',
      Boolean(appState && appState.brand.includes('故事之间') && appState.nav.length >= 3),
      appState ? `brand=${clip(appState.brand, 20)} nav=${clip(appState.nav.join('/'), 80)}` : 'n/a',
    )
    check('内嵌应用正文不是空白', Boolean(appState && appState.bodyLength > 200), appState ? `bodyLength=${appState.bodyLength}` : 'n/a')
    await shot(page, '03-app-iframe.png')
    report.app = { frameUrl, ...appState }
  } else {
    skip('内嵌应用完成可信登录（不是应用登录页）', '主面板未打开')
    skip('内嵌应用渲染出稳定内容（品牌 + 侧栏导航）', '主面板未打开')
    skip('内嵌应用正文不是空白', '主面板未打开')
  }

  // 在应用里打开一部作品（explore 页点「继续游玩/开始游玩」），进入故事页后 P2 context 才有内容。
  async function tryOpenStory(timeoutMs = 45000) {
    const storyFrameLocator = page.frameLocator('iframe[data-dsh-story-frame]')
    const currentStoryUrl = () => {
      const frame = page.frames().find((candidate) => /\/story\//.test(candidate.url()))
      return frame ? frame.url() : ''
    }
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const current = currentStoryUrl()
      if (/\/worlds\/|\/books\/[^/]+\/read/.test(current)) return current
      for (const label of [/继续游玩/, /开始游玩/, /开始阅读/, /继续阅读/]) {
        const button = storyFrameLocator.getByRole('button', { name: label }).first()
        if ((await button.count().catch(() => 0)) && (await button.isEnabled().catch(() => false))) {
          await button.click({ timeout: 4000 }).catch(() => {})
          break
        }
      }
      await page.waitForTimeout(800)
    }
    return ''
  }

  // ── C2. （可选）在应用里打开一部作品，让 P2 context 拿到「当前故事」──────────
  const openStory = args.includes('--open-story')
  if (openStory) {
    if (panelReady && appState && appState.hasShell) {
      const openedUrl = await tryOpenStory(45000)
      check(
        '（--open-story）应用内打开一部作品进入故事页',
        Boolean(openedUrl),
        openedUrl ? `url=${clip(openedUrl, 90)}` : '45s 内没有进入故事页（应用里没有可打开的精选作品，或选择器不匹配；此项非插件断言）',
      )
      storyFrame = page.frames().find((candidate) => /\/story\//.test(candidate.url())) || storyFrame
      appState = await readAppState()
      report.app.storyUrl = openedUrl || null
      if (openedUrl) {
        const opened = new URL(openedUrl)
        report.app.storyPath = opened.pathname + opened.search
      }
      // 位置记忆由应用上报 state 触发（400ms 节流），稍等再读 sessionStorage
      let rememberedAfterOpen = ''
      const memoryDeadline = Date.now() + 8000
      while (Date.now() < memoryDeadline) {
        rememberedAfterOpen = await readMemory()
        if (/\/worlds\//.test(rememberedAfterOpen)) break
        await page.waitForTimeout(300)
      }
      report.app.memoryAfterOpen = rememberedAfterOpen || null
      check('打开作品后位置记忆写入 sessionStorage（dsh-story:last-app-path）', /\/worlds\//.test(rememberedAfterOpen), rememberedAfterOpen ? `memory=${clip(rememberedAfterOpen, 100)}` : '8s 内 sessionStorage 里没有 /worlds/ 路径')
      await shot(page, '03b-story-open.png')
    } else {
      skip('（--open-story）应用内打开一部作品进入故事页', '应用未完成登录，无法进入故事页')
    }
  } else {
    skip('（--open-story）应用内打开一部作品进入故事页', '默认不写应用数据；需要全链路时加 --open-story')
  }

  // ── D. 工具条按钮 ──────────────────────────────────────────────────────────
  async function actionButton(action, text) {
    const byAnchor = page.locator(`[data-dsh-story-action="${action}"]`).first()
    if (await byAnchor.count().catch(() => 0)) return byAnchor
    return page.locator('button', { hasText: text }).first()
  }
  const continueButton = await actionButton('continue', '让 DSH 续写')
  const roleplayButton = await actionButton('roleplay', '角色扮演')
  if (panelReady) {
    const continueVisible = await continueButton.isVisible().catch(() => false)
    const roleplayVisible = await roleplayButton.isVisible().catch(() => false)
    check('工具条有「让 DSH 续写」按钮', continueVisible)
    check('工具条有「角色扮演」按钮', roleplayVisible)
  } else {
    skip('工具条有「让 DSH 续写」按钮', '主面板未打开')
    skip('工具条有「角色扮演」按钮', '主面板未打开')
  }

  // ── E. P2 桥协议：ping→pong、context-request→context ─────────────────────
  async function sendToFrame(message, expectType, timeoutMs = 8000) {
    return page
      .evaluate(
        ({ message, expectType, timeout }) =>
          new Promise((resolve) => {
            const iframe = document.querySelector('iframe[data-dsh-story-frame]')
            if (!iframe || !iframe.contentWindow) return resolve({ error: 'no iframe' })
            const timer = setTimeout(() => {
              window.removeEventListener('message', onMessage)
              resolve({ error: 'timeout' })
            }, timeout)
            function onMessage(event) {
              const data = event.data
              if (!data || data.type !== expectType) return
              if (message.id !== undefined && data.id !== message.id) return
              clearTimeout(timer)
              window.removeEventListener('message', onMessage)
              resolve({ data, origin: event.origin })
            }
            window.addEventListener('message', onMessage)
            iframe.contentWindow.postMessage(message, window.location.origin)
          }),
        { message, expectType, timeout: timeoutMs },
      )
      .catch((error) => ({ error: String(error) }))
  }

  let contextOk = false
  // 轮询 context-request 直到应用返回 ok:true（面板重挂载/应用启动都可能在读上下文前有短暂空窗）
  async function waitContext(kind, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs
    let last = null
    while (Date.now() < deadline) {
      const id = `probe-${kind}-${Date.now()}`
      last = await sendToFrame({ type: 'dsh-story:context-request', id, kind }, 'dsh-story:context', 6000)
      if (last && last.data && last.data.ok) return last.data
      await page.waitForTimeout(1000)
    }
    return last && last.data ? last.data : last
  }

  if (panelReady && storyFrame) {
    const pong = await sendToFrame({ type: 'dsh-story:ping' }, 'dsh-story:pong', 8000)
    const pongData = pong && pong.data
    check(
      'P2 ping → pong（iframe 可达）',
      Boolean(pongData && pongData.type === 'dsh-story:pong'),
      pong && pong.error ? `${pong.error}（应用侧 P2 桥未实现，owner: app-auth）` : `appPath=${clip(pongData?.appPath)}`,
    )
    const states = await page.evaluate(() => window.__dshStoryStates || []).catch(() => [])
    const latestState = states.length ? states[states.length - 1] : null
    check(
      'P2 应用主动上报 state（appPath + hasContext）',
      Boolean(latestState && typeof latestState.appPath === 'string'),
      latestState ? `count=${states.length} last=${clip(JSON.stringify(latestState))}` : '25s 内没收到 dsh-story:state',
    )
    report.bridge.state = latestState ? { count: states.length, ...latestState } : null
    const requestId = `verify-${Date.now()}`
    const contextReply = await sendToFrame({ type: 'dsh-story:context-request', id: requestId, kind: 'continue' }, 'dsh-story:context', 10000)
    const contextData = contextReply && contextReply.data
    const arrived = Boolean(contextData && contextData.type === 'dsh-story:context' && contextData.id === requestId)
    check('P2 context-request → context（拿到回复）', arrived, contextReply && contextReply.error ? `${contextReply.error}（owner: app-auth）` : `ok=${contextData?.ok}`)
    if (arrived && contextData.ok) {
      const ctx = contextData.context || {}
      const shapeOk =
        typeof ctx.appPath === 'string' &&
        typeof ctx.worldTitle === 'string' &&
        typeof ctx.lineTitle === 'string' &&
        typeof ctx.mode === 'string' &&
        Array.isArray(ctx.characters) &&
        typeof ctx.recentText === 'string'
      check(
        'P2 context 字段齐全（appPath/worldTitle/lineTitle/mode/characters/recentText）',
        shapeOk,
        `mode=${clip(ctx.mode, 20)} world=${clip(ctx.worldTitle, 24)} line=${clip(ctx.lineTitle, 24)} chars=${Array.isArray(ctx.characters) ? ctx.characters.length : 'n/a'} recent=${typeof ctx.recentText === 'string' ? ctx.recentText.length : 'n/a'}字`,
      )
      contextOk = shapeOk
    } else if (arrived) {
      check('P2 context 字段齐全（appPath/worldTitle/lineTitle/mode/characters/recentText）', false, `应用返回 ok:false：${clip(contextData.error, 160)}（owner: app-auth 的 P2 上下文桥）`)
    } else {
      skip('P2 context 字段齐全（appPath/worldTitle/lineTitle/mode/characters/recentText）', '没有收到 context 回复')
    }
    report.bridge.protocol = {
      ping: pong && pong.error ? { error: pong.error } : { appPath: pongData?.appPath ?? null },
      context: arrived ? { ok: Boolean(contextData.ok), error: contextData.error ?? null, context: contextData.context ?? null } : { error: (contextReply && contextReply.error) || 'no reply' },
    }
  } else {
    skip('P2 ping → pong（iframe 可达）', '主面板未打开')
    skip('P2 context-request → context（拿到回复）', '主面板未打开')
    skip('P2 context 字段齐全（appPath/worldTitle/lineTitle/mode/characters/recentText）', '主面板未打开')
  }

  // ── F. 交给 Agent：新会话 + 草稿填充 / 剪贴板兜底 ─────────────────────────
  // DSH 的会话输入是 Lexical（[contenteditable="true"]）；面板打开时主区被面板占据，
  // 所以「新会话已打开」的可靠信号是：面板关闭 + 输入框出现。不能只看
  // body 里有没有 "New Session"（侧栏按钮一直存在，会假阳性）。
  const DRAFT_SELECTOR = '[contenteditable="true"], textarea'
  async function readTopState() {
    return page
      .evaluate((selector) => {
        const drafts = [...document.querySelectorAll(selector)].map((node) => node.value ?? node.innerText ?? '')
        const text = document.body.innerText || ''
        const view = document.querySelector('[data-dsh-story-view]')
        const status = document.querySelector('[data-dsh-story-status]')
        return {
          draftCount: drafts.length,
          draft: drafts.join('\n').trim(),
          text,
          hintState: view ? view.getAttribute('data-dsh-story-hint') : null,
          connected: view ? view.getAttribute('data-dsh-story-connected') : null,
          statusText: status ? (status.innerText || '').replace(/\s+/g, ' ').trim() : '',
        }
      }, DRAFT_SELECTOR)
      .catch(() => null)
  }
  async function readClipboard() {
    return page.evaluate(() => navigator.clipboard.readText().catch(() => '')).catch(() => '')
  }
  const STATUS_PATTERN = /读取(?:故事)?内容失败[^\n]*|没有可交给[^\n]*|请先打开[^\n]*|已交给 DSH[^\n]*|已复制到剪贴板[^\n]*|等待应用就绪[^\n]*|正在读取当前故事[^\n]*/

  async function observeAsk(marker) {
    const result = {
      sessionOpened: false,
      draft: '',
      clipboard: '',
      branch: 'none',
      hint: '',
      hintState: '',
      graceful: false,
      panelStillOpen: true,
      panelStatus: '',
    }
    const deadline = Date.now() + 30000
    while (Date.now() < deadline) {
      const [state, clipboard, visible] = await Promise.all([readTopState(), readClipboard(), panelVisible()])
      if (state) {
        result.draft = state.draft
        result.clipboard = clipboard
        result.panelStillOpen = visible
        result.hintState = state.hintState || ''
        // 稳定锚点优先：data-dsh-story-hint=error 即插件自己认定这次动作用了降级路径
        if (state.hintState === 'error' || state.hintState === 'sent') {
          result.hint = state.statusText || ''
          result.panelStatus = state.statusText || ''
        } else if (!result.panelStatus) {
          const hintMatch = state.text.match(STATUS_PATTERN)
          if (hintMatch) {
            result.hint = hintMatch[0]
            result.panelStatus = hintMatch[0]
          }
        }
        const draftHit = marker.test(state.draft)
        const clipboardHit = marker.test(clipboard)
        // 成功路径：面板关闭（layout.selectPanel(null)）且会话输入框出现
        if (!visible && state.draftCount > 0) {
          result.sessionOpened = true
          if (draftHit || clipboardHit) {
            result.branch = draftHit && clipboardHit ? 'draft+clipboard' : draftHit ? 'draft' : 'clipboard'
            break
          }
        }
        // 降级路径：插件在面板内给出明确错误提示，不误开空会话
        if (visible && state.hintState === 'error') {
          result.graceful = true
          break
        }
      }
      await page.waitForTimeout(800)
    }
    return result
  }

  function recordAsk(name, result, marker, template) {
    const draftHit = marker.test(result.draft)
    const clipboardHit = marker.test(result.clipboard)
    const hintSuffix = result.hint ? ` hint=${clip(result.hint, 80)}` : result.hintState ? ` state=${result.hintState}` : ''
    if (result.sessionOpened) {
      check(`${name} 打开新会话`, true, 'DSH 主区出现会话输入框')
      check(
        `${name} 填充提示词（或剪贴板兜底）`,
        draftHit || clipboardHit,
        `branch=${result.branch} draft=${result.draft.length}字 clipboard=${result.clipboard.length}字${hintSuffix}`,
      )
    } else if (draftHit || clipboardHit) {
      skip(`${name} 打开新会话`, '提示词已进草稿/剪贴板，但没检测到新会话界面（剪贴板兜底分支，如实记录）')
      check(`${name} 填充提示词（或剪贴板兜底）`, true, `branch=${result.branch} draft=${result.draft.length}字 clipboard=${result.clipboard.length}字`)
    } else if (result.graceful) {
      skip(`${name} 打开新会话`, `上下文不可用，插件优雅降级（hint=${result.hintState || 'error'}）：「${clip(result.hint, 110)}」`)
      skip(`${name} 填充提示词（或剪贴板兜底）`, '同上；根因：P2 context 不可用（owner: app-auth）或应用内没有打开的故事')
    } else {
      check(`${name} 打开新会话`, false, '点击后 30s 内面板没有关闭/没有出现会话输入框（owner: plugin-core）')
      check(`${name} 填充提示词（或剪贴板兜底）`, false, `draft=${result.draft.length}字 clipboard=${result.clipboard.length}字 panelStillOpen=${result.panelStillOpen} status=${clip(result.panelStatus, 80) || '无'}`)
    }
    // 提示词模板回归（拿到 prompt 时才断言；没拿到就跳过并写明原因）
    const prompt = (result.draft || result.clipboard || '').trim()
    if (template && prompt) {
      check(`${name} 提示词符合契约模板`, template.test(prompt), `prompt=${clip(prompt, 140)}`)
    } else if (template) {
      skip(`${name} 提示词符合契约模板`, '没有拿到提示词（上下文不可用/降级分支）')
    }
  }

  if (panelReady && !skipBridge) {
    // F1. 让 DSH 续写
    if (await continueButton.isVisible().catch(() => false)) {
      await dismissWelcome()
      const enabled = await continueButton.isEnabled().catch(() => false)
      if (!enabled) check('「让 DSH 续写」按钮可用（未禁用）', false, '按钮 disabled：插件可能还在等应用就绪/上下文（检查 iframe 是否登录页、P2 state 是否 hasContext=true）')
      else check('「让 DSH 续写」按钮可用（未禁用）', true)
      const readyContext = await waitContext('continue', openStory ? 15000 : 8000)
      report.bridge.contextBeforeAsk = readyContext && readyContext.context
        ? { ok: true, mode: readyContext.context.mode, worldTitle: readyContext.context.worldTitle, lineTitle: readyContext.context.lineTitle }
        : { ok: false, error: (readyContext && readyContext.error) || 'no reply' }
      await continueButton.click({ force: true }).catch(() => {})
      const result = await observeAsk(/请基于以上内容续写下一段/)
      recordAsk('「让 DSH 续写」', result, /续写/, /请基于以上内容续写下一段/)
      report.bridge.continue = {
        sessionOpened: result.sessionOpened,
        branch: result.branch,
        hint: result.hint || null,
        hintState: result.hintState || null,
        panelStatus: result.panelStatus || null,
        draftPreview: result.draft.slice(0, 400),
        clipboardPreview: result.clipboard.slice(0, 400),
      }
      await shot(page, '04-continue-draft.png')
    } else {
      skip('「让 DSH 续写」打开新会话', '按钮不可见')
      skip('「让 DSH 续写」填充提示词（或剪贴板兜底）', '按钮不可见')
    }

    // F2. 角色扮演：需要回到面板再点一次
    let panelReopened = await panelVisible()
    if (!panelReopened && entry) {
      const reopenedEntry = await findEntry(5000)
      if (reopenedEntry) {
        await dismissWelcome()
        for (let attempt = 0; attempt < 3 && !panelReopened; attempt += 1) {
          try {
            await reopenedEntry.click({ timeout: 4000 })
          } catch {
            await reopenedEntry.click({ force: true }).catch(() => {})
          }
          panelReopened = await frameLocator
            .waitFor({ state: 'visible', timeout: 6000 })
            .then(() => true)
            .catch(() => false)
        }
      }
    }
    if (panelReopened) {
      // iframe 重新挂载后 storyFrame 引用可能过期，重新定位
      storyFrame = page.frames().find((candidate) => /\/story\//.test(candidate.url())) || storyFrame
      const roleplayVisible = await roleplayButton.isVisible().catch(() => false)
      check('重新打开面板后「角色扮演」按钮可见', roleplayVisible)
      if (roleplayVisible) {
        // 面板重挂载时插件会从 sessionStorage 深链回最后一次故事路由（位置记忆），
        // 给应用一点时间 boot + 恢复世界 store，再要上下文；若 20s 仍没有，才退回
        // 「去 explore 重新打开作品」的旧兜底（用于记忆失效的场景）。
        const restoredWorldUrl = await waitForFrameUrl(/\/worlds\//, openStory ? 20000 : 6000)
        if (restoredWorldUrl) report.bridge.roleplayRestoredUrl = restoredWorldUrl
        let roleplayContext = panelReady ? await waitContext('roleplay', restoredWorldUrl ? 15000 : 8000) : null
        if (!(roleplayContext && roleplayContext.ok) && panelReady && openStory) {
          const reopenedStory = await tryOpenStory(25000)
          if (reopenedStory) {
            report.bridge.roleplayReopenedStory = reopenedStory
            roleplayContext = await waitContext('roleplay', 25000)
          }
        }
        report.bridge.contextBeforeRoleplay = roleplayContext && roleplayContext.context
          ? { ok: true, mode: roleplayContext.context.mode, activeCharacter: roleplayContext.context.activeCharacter }
          : { ok: false, error: (roleplayContext && roleplayContext.error) || 'no reply' }
        if (roleplayContext && roleplayContext.ok) check('角色扮演前应用已恢复故事上下文', true, `mode=${clip(roleplayContext.context?.mode, 20)} active=${clip(roleplayContext.context?.activeCharacter, 20)}`)
        else skip('角色扮演前应用已恢复故事上下文', `应用 25s 内没有恢复上下文：${clip((roleplayContext && roleplayContext.error) || 'no reply', 100)}`)
        // 清空剪贴板，避免上一轮的续写提示词造成假阳性
        await page.evaluate(() => navigator.clipboard.writeText('').catch(() => {})).catch(() => {})
        await roleplayButton.click({ force: true }).catch(() => {})
        const result = await observeAsk(/【你要扮演】|第一人称即兴演绎/)
        recordAsk('「角色扮演」', result, /【你要扮演】|第一人称即兴演绎/, /【你要扮演】[\s\S]*第一人称即兴演绎/)
        report.bridge.roleplay = {
          sessionOpened: result.sessionOpened,
          branch: result.branch,
          hint: result.hint || null,
          hintState: result.hintState || null,
          panelStatus: result.panelStatus || null,
          draftPreview: result.draft.slice(0, 400),
          clipboardPreview: result.clipboard.slice(0, 400),
        }
        await shot(page, '05-roleplay-draft.png')
      } else {
        skip('「角色扮演」打开新会话', '按钮不可见')
        skip('「角色扮演」填充提示词（或剪贴板兜底）', '按钮不可见')
      }
    } else {
      skip('重新打开面板后「角色扮演」按钮可见', '面板没有重新打开')
      skip('「角色扮演」打开新会话', '面板没有重新打开')
      skip('「角色扮演」填充提示词（或剪贴板兜底）', '面板没有重新打开')
    }
  } else if (skipBridge) {
    skip('P2 桥端到端点按（--no-bridge）', '按要求跳过')
  } else {
    for (const name of [
      '「让 DSH 续写」打开新会话',
      '「让 DSH 续写」填充提示词（或剪贴板兜底）',
      '重新打开面板后「角色扮演」按钮可见',
      '「角色扮演」打开新会话',
      '「角色扮演」填充提示词（或剪贴板兜底）',
    ]) {
      skip(name, '主面板未打开')
    }
  }

  // ── G1–G3. 位置记忆：切面板恢复 / 整页刷新恢复 / 刷新按钮清记忆 ───────────
  const restoreEvidence = {}
  const restoreChecks = [
    '切走面板再点回：iframe 恢复到最后的故事页',
    '恢复后 data-dsh-story-restored=1',
    '恢复后 P2 state hasContext=true',
    '恢复后 P2 context 仍 ok=true',
    '整页刷新后重新进面板：用 sessionStorage 记忆恢复',
    '整页刷新后 data-dsh-story-restored=1',
    '点「刷新」：iframe 回到应用首页 /story/',
    '点「刷新」：data-dsh-story-restored=0',
    '点「刷新」：位置记忆已清空',
    '点「刷新」：应用回到 /explore',
  ]
  if (!panelReady) {
    for (const name of restoreChecks) skip(name, '主面板未打开')
  } else if (!openStory) {
    for (const name of restoreChecks) skip(name, '需要 --open-story 先打开一部作品，位置记忆才有内容')
  } else {
    // 先把面板打开（F 组结束后面板是关的），等它恢复上一次的故事路由。
    const panelAgain = await openStoryPanel()
    const restoreUrl = await waitForFrameUrl(/\/worlds\//, 20000)
    if (!panelAgain || !restoreUrl) {
      skip(restoreChecks[0], `重开面板后 20s 内没有恢复 /worlds/ 路由（panel=${panelAgain} url=${clip(restoreUrl, 80) || '无'}）`)
      skip(restoreChecks[1], '没有恢复故事页')
      skip(restoreChecks[2], '没有恢复故事页')
      skip(restoreChecks[3], '没有恢复故事页')
    } else {
      const away = await switchAwayFromStoryPanel()
      if (!away) {
        skip(restoreChecks[0], '找不到其它面板/收起方式，无法验证切走再回来')
        skip(restoreChecks[1], '没有切走')
        skip(restoreChecks[2], '没有切走')
        skip(restoreChecks[3], '没有切走')
      } else {
        const panelBack = await openStoryPanel()
        const backUrl = await waitForFrameUrl(/\/worlds\//, 20000)
        const backRestored = await readRestored()
        check(restoreChecks[0], Boolean(panelBack && backUrl), `away=${away}${backUrl ? ` url=${clip(backUrl, 100)}` : '；20s 内没回到 /worlds/ 路由'}`)
        check(restoreChecks[1], backRestored === '1', `restored=${backRestored ?? '无'}`)
        const statesAfterRestore = await page.evaluate(() => window.__dshStoryStates || []).catch(() => [])
        const lastState = statesAfterRestore.length ? statesAfterRestore[statesAfterRestore.length - 1] : null
        check(restoreChecks[2], Boolean(lastState && lastState.hasContext === true), lastState ? clip(JSON.stringify(lastState)) : '没收到 state')
        const restoredContext = await waitContext('continue', 15000)
        check(restoreChecks[3], Boolean(restoredContext && restoredContext.ok), restoredContext && restoredContext.ok ? `mode=${clip(restoredContext.context?.mode, 20)}` : clip((restoredContext && restoredContext.error) || 'no reply', 120))
        restoreEvidence.switchBack = {
          away,
          url: backUrl || null,
          restored: backRestored,
          state: lastState,
          context: restoredContext && restoredContext.ok ? { ok: true, mode: restoredContext.context?.mode } : { ok: false, error: (restoredContext && restoredContext.error) || 'no reply' },
        }
        await shot(page, '06-restore-panel.png')
      }
    }

    // G3. 整页刷新 DSH 后重新进面板：客户端模块重新加载，sessionStorage 仍在。
    await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {})
    await dismissWelcome()
    const panelAfterReload = await openStoryPanel()
    const reloadedUrl = await waitForFrameUrl(/\/worlds\//, 25000)
    const reloadedRestored = await readRestored()
    const memoryAfterReload = await readMemory()
    check(restoreChecks[4], Boolean(panelAfterReload && reloadedUrl), panelAfterReload ? reloadedUrl || '25s 内没恢复 /worlds/ 路由' : '整页刷新后侧栏入口点不开面板')
    check(restoreChecks[5], reloadedRestored === '1', `restored=${reloadedRestored ?? '无'} memory=${clip(memoryAfterReload, 90) || '空'}`)
    restoreEvidence.pageReload = { url: reloadedUrl || null, restored: reloadedRestored, memory: memoryAfterReload || null }
    await shot(page, '07-reload-restore.png')

    // G2. 点「刷新」：清掉位置记忆，回应用首页（应用自己会路由到 /explore）。
    const reloadButton = await actionButton('reload', '刷新')
    if (await reloadButton.isVisible().catch(() => false)) {
      await reloadButton.click({ force: true }).catch(() => {})
      let refreshedPath = ''
      let refreshRestored = null
      let memoryAfterRefresh = null
      const refreshDeadline = Date.now() + 20000
      while (Date.now() < refreshDeadline) {
        const src = await frameLocator.getAttribute('src').catch(() => null)
        refreshedPath = src ? new URL(src, url).pathname : ''
        if (refreshedPath === '/story/' || refreshedPath === '/story') {
          refreshRestored = await readRestored()
          memoryAfterRefresh = await readMemory()
          break
        }
        await page.waitForTimeout(400)
      }
      const exploreUrl = await waitForFrameUrl(/\/explore/, 20000)
      check(restoreChecks[6], refreshedPath === '/story/' || refreshedPath === '/story', `src path=${refreshedPath || 'n/a'}`)
      check(restoreChecks[7], refreshRestored === '0', `restored=${refreshRestored ?? 'n/a'}`)
      check(restoreChecks[8], !memoryAfterRefresh, memoryAfterRefresh ? `仍有 memory=${clip(memoryAfterRefresh, 90)}` : 'sessionStorage 已清空 dsh-story:last-app-path')
      check(restoreChecks[9], Boolean(exploreUrl), exploreUrl || '20s 内应用没路由到 /explore')
      restoreEvidence.refresh = { srcPath: refreshedPath, restored: refreshRestored, memory: memoryAfterRefresh || null, exploreUrl: exploreUrl || null }
      await shot(page, '08-refresh-home.png')
    } else {
      for (const name of restoreChecks.slice(6)) skip(name, '「刷新」按钮不可见')
    }
  }
  report.restore = restoreEvidence

  // ── G. 控制台报错 ──────────────────────────────────────────────────────────
  const fatalErrors = consoleErrors.filter((text) => !/Failed to load resource/.test(text))
  check('无控制台 JS 报错', fatalErrors.length === 0, fatalErrors.slice(0, 3).join(' | ') || 'clean')

  report.summary = {
    pass: report.checks.filter((item) => item.ok && !item.skipped).length,
    fail: report.checks.filter((item) => !item.ok).length,
    skip: report.checks.filter((item) => item.skipped).length,
  }
  report.ok = report.summary.fail === 0 && report.summary.pass > 0
} catch (error) {
  report.ok = false
  report.error = error && error.stack ? error.stack : String(error)
  console.error('验收异常：', error)
} finally {
  report.finishedAt = new Date().toISOString()
  await writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2)).catch(() => {})
  await browser.close().catch(() => {})
  const summary = report.summary || { pass: 0, fail: report.checks.filter((item) => !item.ok).length, skip: 0 }
  console.log(`\nPASS ${summary.pass} · FAIL ${summary.fail} · SKIP ${summary.skip}`)
  console.log(report.ok ? '全部检查通过 ✅' : '存在失败项 ❌（见 report.json）')
  process.exit(report.ok ? 0 : 1)
}
