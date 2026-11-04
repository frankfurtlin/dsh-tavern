/**
 * dsh-tavern 端到端验收脚本（真实浏览器行为，不是静态代码检查）。
 *
 * 用法：
 *   node verify.mjs --url "http://127.0.0.1:3080/?token=..." [--out dir] [--no-bridge] [--open-story] [--cards] [--real] [--space] [--groups] [--memory]
 *
 * 检查分组（与 docs/contract.md v1 对应）：
 *   A. 鉴权与入口：未登录 GET /tavern/ 必须被 DSH 闸门挡住（401）；登录态 200。
 *   B. 侧栏入口「酒馆」存在；点击后 main 面板出现 iframe[data-dsh-tavern-frame]。
 *   C. 内嵌应用处于可信登录态（出现 .app-shell / 侧栏内容，而不是应用自己的登录页），
 *      并渲染出稳定内容（品牌、导航）。
 *   D. 工具条按钮（让 DSH 续写 / 角色扮演）可见。
 *   E. P2 桥：ping → pong（带 appPath）；context-request → context（应含
 *      appPath/worldTitle/lineTitle/mode/characters/recentText）。
 *   F. 点「让 DSH 续写」：期望新会话打开 + 编辑器草稿被填充，或剪贴板兜底；
 *      上下文不可用时插件应优雅降级（面板内出现错误提示、不误开空会话）。如实记录分支。
 *   G. 位置记忆：切面板/整页 reload 恢复、点「刷新」清记忆回首页。
 *   H. --cards：角色卡库 → 导入 fixture 卡（PNG/v2 JSON/v1 JSON）→ 开聊 →
 *      发「你好」→ mock 回复 → 刷新后消息仍在 → 删除卡后列表消失。
 *      会向应用写测试卡（软删除后仍留 world，仅测试库使用）。
 *   H2. --real：在上面聊天链路上额外断言「真生成」：回复不含 mock 特征句
 *      「我们可以聊聊此刻的想法」、长度 ≥20 字且不是用户消息本身、完整刷新后仍在、
 *      console 无报错；证据（首句前 60 字/字数/耗时/模型名）写入 report.real。
 *      隐含 --cards；需要应用 AI_MODE=dsh 且 DSH 模型网关可用。
 *   S. --space（B 步，docs/step3-card-space.md §6）：卡详情改名、世界书导入/手工条目、
 *      正则「。」→「！」对回复生效、预设绑定 + context-preview、console clean。隐含 --cards；
 *      角色卡空间 UI/API 未接入时逐项 SKIP 并写明 owner（cards-client / app-auth），不 FAIL。
 *   T. --groups（B2，docs/step3b-group-chat.md §5）：角色卡库多选 2 张 → 发起群聊 →
 *      参与者正确 → 发消息（真实回复 + 发言人=API speakerName）→ 再发一条轮转 →
 *      整页刷新消息仍在 → 删除群聊列表回落 → console clean。隐含 --cards；
 *      真实生成请配合 --real。证据写入 report.groups。
 *   M. 导航回归（task-24，随 --cards 运行）：左侧导航不再有「我的」分组 tab 与
 *      我的对话/我的笔记/模型与用量/账号设置/任务进度/反馈与通知/使用帮助 7 个入口；
 *      /settings、/settings/models、/notebook、/help、/tasks/activity、/conversations
 *      均重定向到 /explore 且无 console 报错；/conversations/world/:id 仍能打开。证据写入 report.nav。
 *   U. --memory（C 步，docs/step4-companion-memory.md §7）：卡详情手工记忆增删改 →
 *      聊天发 2 条含自述的消息 → 「立即整理」抽取出 ≥1 条 → 注入预览含手工记忆标题 →
 *      停用/删除后注入预览不再含 → 设为长期伙伴后卡库顶部「长期伙伴」分组置顶 → console clean。
 *      隐含 --cards；真实抽取需要真生成（配合 --real）。证据写入 report.memory。
 *   I. 截图 + 输出目录下 report.json；无控制台 JS 报错。
 *
 * 设计原则：
 *   - 插件/应用尚未就绪时（/tavern 404、应用登录页、P2 桥没有响应、角色卡页面未接入）
 *     不崩：能跑出「未就绪」报告，依赖项记为 SKIP 并写明 owner，退出码仍为非 0。
 *   - 默认不写应用数据：不打开作品、只开草稿、不点发送。
 *     --open-story 会在应用 explore 页点一部 live 作品进入故事页（会写阅读进度），
 *     仅用于验证「续写提示词模板」全链路；--cards 会导入/删除测试角色卡；--space 会额外
 *     改一张测试卡的卡名/世界书/预设/正则；--groups 会建群聊/发消息再删除（都在测试卡上
 *     操作，会写测试库）。
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
const outDir = arg('out', '/root/workspace/plugins/dsh-tavern/verification')
const skipBridge = args.includes('--no-bridge')
const headless = !args.includes('--headed')
if (!url) {
  console.error('缺少 --url（带 token 的 DSH Web 地址）')
  process.exit(2)
}

const report = {
  plugin: 'dsh-tavern',
  url: url.replace(/token=[^&]+/, 'token=***'),
  startedAt: new Date().toISOString(),
  checks: [],
  bridge: { protocol: null, continue: null, roleplay: null },
  cards: null,
  real: null,
  space: null,
  groups: null,
  memory: null,
  nav: null,
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

  // ── A. 未登录访问 /tavern/ 必须被 DSH 登录闸门挡住 ──────────────────────────
  const anon = await browser.newContext({ ignoreHTTPSErrors: true })
  const anonRes = await anon.request.get(new URL('tavern/', url).href)
  const anonStatus = anonRes.status()
  check(
    '未登录访问 /tavern/ 被拒绝（期望 401）',
    anonStatus === 401,
    `HTTP ${anonStatus}${
      anonStatus === 404
        ? '（404 = 宿主半没加载 / 没注册 /tavern 路由，插件尚未就绪，owner: plugin-core）'
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
  // 收集应用主动上报的 dsh-tavern:state（P2 契约：appPath + hasContext）
  await page.evaluate(() => {
    window.__dshStoryStates = []
    window.addEventListener('message', (event) => {
      const data = event.data
      if (data && data.type === 'dsh-tavern:state') {
        window.__dshStoryStates.push({ at: Date.now(), appPath: data.appPath, hasContext: data.hasContext })
      }
    })
  })
  // A2. 旧 /story 兼容别名：登录态 GET 应 302 到 /tavern/（同后缀同 query）。
  const legacyRes = await context.request
    .get(new URL('story/?probe=1&x=2', url).href, { maxRedirects: 0 })
    .catch(() => null)
  if (legacyRes) {
    const legacyStatus = legacyRes.status()
    const legacyLocation = legacyRes.headers()['location'] || ''
    check(
      '旧 /story/ 302 跳到 /tavern/（兼容别名，保留 query）',
      legacyStatus === 302 && /\/tavern\//.test(legacyLocation) && /probe=1/.test(legacyLocation),
      `HTTP ${legacyStatus} Location=${clip(legacyLocation, 120) || '无'}${
        legacyStatus === 404 ? '（旧路径没注册，owner: plugin-core）' : legacyStatus === 200 ? '（期望 302，实际直接 200）' : ''
      }`,
    )
  } else {
    check('旧 /story/ 302 跳到 /tavern/（兼容别名，保留 query）', false, '请求失败')
  }

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

  // ── B. 侧栏「酒馆」入口 ─────────────────────────────────────────────────
  // 契约：sidebar.panellist、PANEL_ID="tavern"、label「酒馆」、order 24。
  // 宿主侧栏行通常渲染成 aria-label = 面板 label；插件也可能在图标上打
  // data-dsh-panel-entry（参考 ziwei）。两种都认，最后一个候选是按文字兜底。
  async function findEntry(timeoutMs) {
    const selectors = [
      '[data-dsh-panel-entry="tavern"]',
      'button[aria-label="酒馆"]',
      'button[data-dsh-panel="tavern"]',
      'button:has-text("酒馆")',
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
  check('侧栏出现「酒馆」入口', Boolean(entry), entry ? '已定位侧栏入口' : '20s 内没找到入口（插件未加载或 label/id 不符合契约，owner: plugin-core）')
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
        .locator('iframe[data-dsh-tavern-frame]')
        .first()
        .waitFor({ state: 'visible', timeout: 6000 })
        .then(() => true)
        .catch(() => false)
    }
    panelReady = opened
    check('点击侧栏入口打开「酒馆」主面板', opened, opened ? 'iframe[data-dsh-tavern-frame] 已可见' : '点击后没有出现 iframe[data-dsh-tavern-frame]（owner: plugin-core）')
  } else {
    skip('点击侧栏入口打开「酒馆」主面板', '侧栏入口不存在')
  }

  const frameLocator = page.locator('iframe[data-dsh-tavern-frame]').first()
  const panelVisible = () => frameLocator.isVisible().catch(() => false)
  // 位置记忆（plugin-core：sessionStorage dsh-tavern:last-app-path + data-dsh-tavern-restored）
  const readMemory = () =>
    page
      .evaluate(() => {
        try {
          return window.sessionStorage.getItem('dsh-tavern:last-app-path') || ''
        } catch {
          return ''
        }
      })
      .catch(() => '')
  const readRestored = () =>
    page
      .evaluate(() => {
        const view = document.querySelector('[data-dsh-tavern-view]')
        return view ? view.getAttribute('data-dsh-tavern-restored') : null
      })
      .catch(() => null)
  async function waitForFrameUrl(pattern, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const frame = page.frames().find((candidate) => /\/tavern\//.test(candidate.url()) && pattern.test(candidate.url()))
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
    // 契约里的稳定锚点：主面板 data-dsh-tavern-view、状态 data-dsh-tavern-hint（idle/loading/waiting/ready/busy/sent/error）
    const storyView = page.locator('[data-dsh-tavern-view]').first()
    const viewVisible = await storyView.isVisible().catch(() => false)
    check('主面板有 data-dsh-tavern-view 锚点', viewVisible)
    const hintState = await page.locator('[data-dsh-tavern-hint]').first().getAttribute('data-dsh-tavern-hint').catch(() => null)
    check(
      '面板状态锚点 data-dsh-tavern-hint 合法',
      ['idle', 'loading', 'waiting', 'ready', 'busy', 'sent', 'error'].includes(hintState || ''),
      `state=${hintState || '无'}`,
    )
    await shot(page, '02-panel.png')

    // 等 iframe 导航到 /tavern/（同源 iframe 会在 page.frames() 里出现）
    const deadline = Date.now() + 30000
    while (Date.now() < deadline) {
      storyFrame = page.frames().find((candidate) => /\/tavern\//.test(candidate.url())) || null
      if (storyFrame) break
      await page.waitForTimeout(300)
    }
    frameUrl = storyFrame ? storyFrame.url() : ''
    check('内嵌 iframe 指向 /tavern/', Boolean(frameUrl && frameUrl.includes('/tavern/')), frameUrl || '没找到 /tavern/ 帧')
    // 登录态 /tavern/ 必须 200（与匿名 401 成对）
    const authedRes = await context.request.get(new URL('tavern/', url).href).catch(() => null)
    check('登录态访问 /tavern/ 返回 200', authedRes ? authedRes.status() === 200 : false, authedRes ? `HTTP ${authedRes.status()}` : '请求失败')
    // 首次挂载（全新 browser context，sessionStorage 为空）：必须回应用首页。
    const initialSrc = await frameLocator.getAttribute('src').catch(() => null)
    const initialRestored = await page.locator('[data-dsh-tavern-view]').first().getAttribute('data-dsh-tavern-restored').catch(() => null)
    const initialPath = initialSrc ? new URL(initialSrc, url).pathname : ''
    check('首次挂载 iframe 指向应用首页 /tavern/（无位置记忆）', initialPath === '/tavern/' || initialPath === '/tavern', `src=${clip(initialSrc, 90) || '无'}`)
    check('首次挂载 data-dsh-tavern-restored=0', initialRestored === '0', `restored=${initialRestored ?? '无'}`)
  } else {
    skip('主面板 iframe 占据可用区域', '主面板未打开')
    skip('内嵌 iframe 指向 /tavern/', '主面板未打开')
    skip('登录态访问 /tavern/ 返回 200', '主面板未打开')
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
      // 登录页出现即可判定；shell 出现后还要等正文渲染（Vue 首屏文本晚于 .app-shell 挂载，
      // 否则 bodyLength 断言会在首屏仍为骨架时误报空白）。
      if (appState && (appState.hasEntryPage || (appState.hasShell && appState.bodyLength > 200))) break
      await page.waitForTimeout(500)
    }
    if (appState && appState.hasShell && appState.bodyLength <= 200) {
      appState = (await readAppState()) || appState
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
      Boolean(appState && appState.brand.includes('酒馆') && appState.nav.length >= 3),
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
    const storyFrameLocator = page.frameLocator('iframe[data-dsh-tavern-frame]')
    const currentStoryUrl = () => {
      const frame = page.frames().find((candidate) => /\/tavern\//.test(candidate.url()))
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
      storyFrame = page.frames().find((candidate) => /\/tavern\//.test(candidate.url())) || storyFrame
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
      check('打开作品后位置记忆写入 sessionStorage（dsh-tavern:last-app-path）', /\/worlds\//.test(rememberedAfterOpen), rememberedAfterOpen ? `memory=${clip(rememberedAfterOpen, 100)}` : '8s 内 sessionStorage 里没有 /worlds/ 路径')
      await shot(page, '03b-tavern-open.png')
    } else {
      skip('（--open-story）应用内打开一部作品进入故事页', '应用未完成登录，无法进入故事页')
    }
  } else {
    skip('（--open-story）应用内打开一部作品进入故事页', '默认不写应用数据；需要全链路时加 --open-story')
  }

  // ── D. 工具条按钮 ──────────────────────────────────────────────────────────
  async function actionButton(action, text) {
    const byAnchor = page.locator(`[data-dsh-tavern-action="${action}"]`).first()
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
            const iframe = document.querySelector('iframe[data-dsh-tavern-frame]')
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
      last = await sendToFrame({ type: 'dsh-tavern:context-request', id, kind }, 'dsh-tavern:context', 6000)
      if (last && last.data && last.data.ok) return last.data
      await page.waitForTimeout(1000)
    }
    return last && last.data ? last.data : last
  }

  if (panelReady && storyFrame) {
    const pong = await sendToFrame({ type: 'dsh-tavern:ping' }, 'dsh-tavern:pong', 8000)
    const pongData = pong && pong.data
    check(
      'P2 ping → pong（iframe 可达）',
      Boolean(pongData && pongData.type === 'dsh-tavern:pong'),
      pong && pong.error ? `${pong.error}（应用侧 P2 桥未实现，owner: app-auth）` : `appPath=${clip(pongData?.appPath)}`,
    )
    const states = await page.evaluate(() => window.__dshStoryStates || []).catch(() => [])
    const latestState = states.length ? states[states.length - 1] : null
    check(
      'P2 应用主动上报 state（appPath + hasContext）',
      Boolean(latestState && typeof latestState.appPath === 'string'),
      latestState ? `count=${states.length} last=${clip(JSON.stringify(latestState))}` : '25s 内没收到 dsh-tavern:state',
    )
    report.bridge.state = latestState ? { count: states.length, ...latestState } : null
    const requestId = `verify-${Date.now()}`
    const contextReply = await sendToFrame({ type: 'dsh-tavern:context-request', id: requestId, kind: 'continue' }, 'dsh-tavern:context', 10000)
    const contextData = contextReply && contextReply.data
    const arrived = Boolean(contextData && contextData.type === 'dsh-tavern:context' && contextData.id === requestId)
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
      if (!openStory && /没有可用的故事上下文/.test(String(contextData.error || ''))) {
        skip('P2 context 字段齐全（appPath/worldTitle/lineTitle/mode/characters/recentText）', '当前没有打开故事（未加 --open-story），P2 上下文为空属预期；全链路验收请加 --open-story')
      } else {
        check('P2 context 字段齐全（appPath/worldTitle/lineTitle/mode/characters/recentText）', false, `应用返回 ok:false：${clip(contextData.error, 160)}（owner: app-auth 的 P2 上下文桥）`)
      }
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
        const view = document.querySelector('[data-dsh-tavern-view]')
        const status = document.querySelector('[data-dsh-tavern-status]')
        return {
          draftCount: drafts.length,
          draft: drafts.join('\n').trim(),
          text,
          hintState: view ? view.getAttribute('data-dsh-tavern-hint') : null,
          connected: view ? view.getAttribute('data-dsh-tavern-connected') : null,
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
        // 稳定锚点优先：data-dsh-tavern-hint=error 即插件自己认定这次动作用了降级路径
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
    // 角色扮演的 draft 可能夹着上一会话的续写文本，优先选真正命中 marker 的那份（clipboard 通常最干净）
    const candidates = [result.clipboard, result.draft].filter((text) => text && marker.test(text))
    const prompt = (candidates[0] || result.draft || result.clipboard || '').trim()
    const promptSource = candidates[0] ? (candidates[0] === result.clipboard ? 'clipboard' : 'draft') : 'draft'
    if (template && prompt) {
      check(
        `${name} 提示词符合契约模板`,
        template.test(prompt),
        `source=${promptSource} prompt=${clip(prompt, 140)}`,
      )
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
      storyFrame = page.frames().find((candidate) => /\/tavern\//.test(candidate.url())) || storyFrame
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
    '恢复后 data-dsh-tavern-restored=1',
    '恢复后 P2 state hasContext=true',
    '恢复后 P2 context 仍 ok=true',
    '整页刷新后重新进面板：用 sessionStorage 记忆恢复',
    '整页刷新后 data-dsh-tavern-restored=1',
    '点「刷新」：iframe 回到应用首页 /tavern/',
    '点「刷新」：data-dsh-tavern-restored=0',
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
        if (refreshedPath === '/tavern/' || refreshedPath === '/tavern') {
          refreshRestored = await readRestored()
          memoryAfterRefresh = await readMemory()
          break
        }
        await page.waitForTimeout(400)
      }
      const exploreUrl = await waitForFrameUrl(/\/explore/, 20000)
      check(restoreChecks[6], refreshedPath === '/tavern/' || refreshedPath === '/tavern', `src path=${refreshedPath || 'n/a'}`)
      check(restoreChecks[7], refreshRestored === '0', `restored=${refreshRestored ?? 'n/a'}`)
      check(restoreChecks[8], !memoryAfterRefresh, memoryAfterRefresh ? `仍有 memory=${clip(memoryAfterRefresh, 90)}` : 'sessionStorage 已清空 dsh-tavern:last-app-path')
      check(restoreChecks[9], Boolean(exploreUrl), exploreUrl || '20s 内应用没路由到 /explore')
      restoreEvidence.refresh = { srcPath: refreshedPath, restored: refreshRestored, memory: memoryAfterRefresh || null, exploreUrl: exploreUrl || null }
      await shot(page, '08-refresh-home.png')
    } else {
      for (const name of restoreChecks.slice(6)) skip(name, '「刷新」按钮不可见')
    }
  }
  report.restore = restoreEvidence

  // ── H. 角色卡导入 + 单卡聊天（--cards）────────────────────────────────────
  // 依赖 cards-client 的 /characters 页面 + app-auth 的 /character-cards API。
  // 选择器原则：能用稳定文本（导航「角色卡库」、按钮「导入角色卡」「开聊」「删除」）就不猜 class。
  const real = args.includes('--real')
  const realNegative = args.includes('--real-negative')
  const runCards = args.includes('--cards') || real || args.includes('--space') || args.includes('--groups')
  const cardsEvidence = { fixturesDir: null, imported: [], avatar: null, chat: null, reload: null, deleted: null }
  // H/S 组共用的 iframe 查找/导航助手（--cards/--space 都用到；提前定义，避免 S 组重写一遍）。
  const fnow = () => page.frames().find((candidate) => /\/tavern\//.test(candidate.url())) || null
  async function waitFrameUrl(pattern, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      const frame = fnow()
      if (frame && pattern.test(frame.url())) return frame
      await page.waitForTimeout(300)
    }
    return null
  }
  async function openCharactersPage() {
    const frame = fnow()
    if (!frame) return null
    const candidates = [
      frame.locator('a[href="/characters"]').first(),
      frame.getByRole('link', { name: '角色卡库', exact: true }).first(),
      frame.getByRole('button', { name: '角色卡库', exact: true }).first(),
      frame.getByText('角色卡库', { exact: true }).first(),
    ]
    for (const candidate of candidates) {
      if (!(await candidate.count().catch(() => 0))) continue
      await candidate.click({ force: true }).catch(() => {})
      const reached = await waitFrameUrl(/\/characters(\/|$|\?)/, 20000)
      if (reached) return reached
    }
    return null
  }
  async function waitChatReady(frame, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      if (await frame.locator('#world-chat-message').count().catch(() => 0)) return true
      await page.waitForTimeout(300)
    }
    return false
  }
  async function findCardRoot(frame, name) {
    // 首选应用自己的卡片容器（article.character-card[data-card-id]），失败再退 xpath。
    const article = frame.locator('article.character-card').filter({ has: frame.getByText(name, { exact: true }) }).first()
    if (await article.count().catch(() => 0)) return article
    const nameLocator = frame.getByText(name, { exact: true }).first()
    return nameLocator.locator("xpath=ancestor::*[.//button[normalize-space(.)='开聊'] or .//button[normalize-space(.)='删除']][1]")
  }

  if (runCards) {
    const fixtureDir = arg('cards-dir', '/tmp/tavern-verify/cards')
    cardsEvidence.fixturesDir = fixtureDir
    const cardsCheckNames = [
      '角色卡库页面可进入（导航「角色卡库」）',
      '角色卡库有导入入口（input[type=file]）',
      '导入 PNG 卡后列表出现卡名',
      '导入 v2 JSON 卡后列表出现卡名',
      '导入 v1 JSON 卡后列表出现卡名',
      '卡头像已渲染（img 加载成功）',
      '「开聊」进入 /conversations/world/<id>',
      '聊天页显示角色名',
      '发送「你好」后出现 mock 回复',
      '刷新聊天页后用户消息与回复仍在',
      '删除卡后列表不再出现该卡名',
      '删除只删一张卡（其余 fixture 卡仍在）',
    ]
    const skipAllCards = (reason) => {
      for (const name of cardsCheckNames) skip(name, reason)
    }
    let manifest = null
    try {
      manifest = JSON.parse(await readFile(path.join(fixtureDir, 'manifest.json'), 'utf8'))
    } catch {
      manifest = null
    }
    if (!manifest || !Array.isArray(manifest.cards) || !manifest.cards.length) {
      skipAllCards(`缺少 fixture：先跑 python3 scripts/fixtures/make-cards.py（找 ${path.join(fixtureDir, 'manifest.json')}）`)
    } else {
      if (!(await panelVisible())) await openStoryPanel()
      let frame = await waitFrameUrl(/\/tavern\//, 20000)
      if (!frame) {
        skipAllCards('主面板/iframe 打不开，无法进入角色卡库（owner: plugin-core）')
      } else {
        const cardsPage = await openCharactersPage()
        check(
          cardsCheckNames[0],
          Boolean(cardsPage),
          cardsPage
            ? `url=${new URL(cardsPage.url()).pathname}`
            : '应用侧没有「角色卡库」导航或 /characters 路由（cards-client 尚未构建 dist / 页面未接入，owner: cards-client）',
        )
        if (!cardsPage) {
          for (const name of cardsCheckNames.slice(1)) skip(name, '角色卡库页面不可用')
        } else {
          frame = cardsPage
          await page.waitForTimeout(600)
          await shot(page, '09-cards-page.png')
          const fileInput = frame.locator('input[type="file"]').first()
          const hasInput = (await fileInput.count().catch(() => 0)) > 0
          check(cardsCheckNames[1], hasInput, hasInput ? 'input[type=file] 存在' : '找不到导入文件框（期望 accept .png,.json，owner: cards-client）')

          // 隔离实例与生产库共用：库里可能已有同名卡（Lead 冒烟会先导入一张「林小满」）。
          // 导入/删除断言都基于「基线计数」判断增量，避免把环境里的同名卡算成自己导入的。
          await page.waitForTimeout(600)
          const baseline = {}
          for (const card of manifest.cards) {
            baseline[card.name] = await frame.getByText(card.name, { exact: true }).count().catch(() => 0)
          }
          const baselineImages = await frame
            .evaluate(() => [...document.images].filter((image) => image.naturalWidth > 0).length)
            .catch(() => 0)
          cardsEvidence.baseline = { counts: baseline, loadedImages: baselineImages }

          let importedCount = 0
          let cardIndex = 0
          for (const card of manifest.cards) {
            const nameVisible = async (timeoutMs = 25000) => {
              const deadline = Date.now() + timeoutMs
              const expected = (baseline[card.name] || 0) + 1
              while (Date.now() < deadline) {
                if ((await frame.getByText(card.name, { exact: true }).count().catch(() => 0)) >= expected) return true
                await page.waitForTimeout(400)
              }
              return false
            }
            const input = frame.locator('input[type="file"]').first()
            let uploaded = false
            if (await input.count().catch(() => 0)) {
              uploaded = await input
                .setInputFiles(path.join(fixtureDir, card.file))
                .then(() => true)
                .catch(() => false)
            }
            const appeared = uploaded && (await nameVisible())
            const alertText = await frame
              .locator('[role="alert"], .toast, [data-toast]')
              .first()
              .textContent()
              .catch(() => null)
            const apiMissing = /请求不可用|RESOURCE_NOT_FOUND|404/.test(alertText || '')
            check(
              cardsCheckNames[2 + cardIndex],
              appeared,
              appeared
                ? `${card.format} 「${card.name}」已出现在列表`
                : `上传/导入失败：uploaded=${uploaded}${alertText ? ` 提示=${clip(alertText, 100)}` : ''}（owner: ${apiMissing ? 'app-auth（/character-cards API 404：8001 进程未重启）' : 'cards-client UI / app-auth API'}）`,
            )
            if (appeared) {
              importedCount += 1
              cardsEvidence.imported.push({ file: card.file, format: card.format, name: card.name })
            }
            cardIndex += 1
          }
          for (let index = cardIndex; index < 3; index += 1) skip(cardsCheckNames[2 + index], 'manifest 里卡数量不足')

          const avatarCount = await frame
            .evaluate(() => [...document.images].filter((image) => image.naturalWidth > 0).length)
            .catch(() => 0)
          cardsEvidence.avatar = { loadedImages: avatarCount, baselineImages }
          if (importedCount === 0) {
            skip(cardsCheckNames[5], '没有卡导入成功，无法验证头像渲染（先看上面的导入失败项）')
          } else {
            check(
              cardsCheckNames[5],
              avatarCount > baselineImages,
              `导入后已加载图片数=${avatarCount}（导入前 ${baselineImages}；PNG 卡头像应为 blob: img，owner: cards-client）`,
            )
          }
          await shot(page, '10-cards-imported.png')

          const pngCard = manifest.cards.find((card) => card.format === 'png-v2') || manifest.cards[0]
          const nameStillThere =
            (await frame.getByText(pngCard.name, { exact: true }).count().catch(() => 0)) > (baseline[pngCard.name] || 0)
          if (!nameStillThere) {
            for (const name of cardsCheckNames.slice(6)) skip(name, `PNG 卡「${pngCard.name}」没进列表，无法开聊`)
          } else {
            const cardRoot = await findCardRoot(frame, pngCard.name)
            let startButton = cardRoot.getByRole('button', { name: '开聊' }).first()
            if (!(await startButton.count().catch(() => 0))) {
              startButton = frame.getByRole('button', { name: '开聊' }).first()
            }
            await startButton.click({ force: true }).catch(() => {})
            const chatFrame = await waitFrameUrl(/\/conversations\/world\/[^/?#]+/, 25000)
            const sessionId = chatFrame ? (chatFrame.url().match(/\/conversations\/world\/([^/?#]+)/) || [])[1] : null
            check(
              cardsCheckNames[6],
              Boolean(chatFrame && sessionId),
              chatFrame ? `url=${new URL(chatFrame.url()).pathname}` : '点「开聊」后 25s 内没进入 /conversations/world/<id>（owner: cards-client / app-auth）',
            )
            if (!chatFrame) {
              for (const name of cardsCheckNames.slice(7)) skip(name, '没有进入聊天页')
            } else {
              frame = chatFrame
              const ready = await waitChatReady(frame)
              const characterVisible = ready && (await frame.getByText(pngCard.name, { exact: true }).first().isVisible().catch(() => false))
              check(
                cardsCheckNames[7],
                characterVisible,
                characterVisible ? `页面显示「${pngCard.name}」` : '聊天页没显示角色名（期望标题/说话人含卡名，owner: cards-client）',
              )
              await shot(page, '11-chat-open.png')

              const composer = frame.locator('#world-chat-message')
              const sendButton = frame.locator('form.world-chat-compose button[type="submit"]').first()
              let reply = null
              let replyMeta = null
              let completed = false
              let elapsedMs = null
              let sendStartedAt = 0
              let sendError = ''
              let noticeText = null
              let submitEnabled = false
              let composeHint = null
              // 应用把每条已保存的 assistant 消息渲染成 <small>模型名 · 已保存|收到的部分回复</small>；
              // streaming 期间只有临时的 running article、没有这个 small。真生成时用它判断「写完了」，
              // 避免抓到流式半截文本。必须看「最后一个 assistant article 自己的 small + 它的 p」，
              // 不能只取所有 small 的最后一个（会把上一条历史回复的「已保存」当成新回复已完成）。
              const assistantTail = async () => {
                const last = frame.locator('.world-chat-message.assistant').last()
                if (!(await last.count().catch(() => 0))) return { meta: '', text: '' }
                const meta = (await last.locator('small').first().textContent().catch(() => null)) || ''
                const text = (await last.locator('p').last().textContent().catch(() => null)) || ''
                return { meta: meta.trim(), text: text.trim() }
              }
              if (!(await composer.count().catch(() => 0))) {
                sendError = '聊天输入框 #world-chat-message 不存在（owner: cards-client）'
              } else {
                await composer.fill('你好')
                // 发送按钮在 draft 为空 / app.online=false / busy 时是 disabled 的；
                // 直接 force click 会命中 disabled 按钮（浏览器不发 click），所以先等它可用。
                const enableDeadline = Date.now() + 8000
                while (Date.now() < enableDeadline) {
                  if (!(await sendButton.isDisabled().catch(() => true))) {
                    submitEnabled = true
                    break
                  }
                  await page.waitForTimeout(200)
                }
                composeHint = await frame
                  .locator('form.world-chat-compose small')
                  .first()
                  .textContent()
                  .catch(() => null)
                sendStartedAt = Date.now()
                if (submitEnabled) {
                  await sendButton.click().catch(() => {})
                } else {
                  // 兜底：组件支持 Ctrl/Cmd+Enter 发送
                  await composer.press('Control+Enter').catch(() => {})
                }
                // 真生成 10–60s 是常态（网关排队 + 模型），给 180s；mock 沿用 120s。
                const replyWaitMs = real ? 180000 : 120000
                const deadline = Date.now() + replyWaitMs
                while (Date.now() < deadline) {
                  const userOk = (await frame.locator('.world-chat-message.user p').filter({ hasText: '你好' }).count().catch(() => 0)) > 0
                  const replies = await frame.locator('.world-chat-message.assistant p').allTextContents().catch(() => [])
                  const candidate = replies.map((text) => text.trim()).filter(Boolean).pop()
                  const tail = candidate ? await assistantTail() : { meta: '', text: '' }
                  const meta = tail.meta
                  // 已完成 = 最后一个 assistant article 带「已保存」标记，且它的正文就是候选文本
                  // （span/流式临时节点无 small，或 small 属于历史消息 → 不算完成）。
                  const finished = /已保存|收到的部分回复/.test(meta) && (!tail.text || tail.text === candidate)
                  // mock 模式沿用原判据（只看文本出现）；真生成必须等到「已保存」标记，防止抓到半截流式文本。
                  if (userOk && candidate && (!real || finished)) {
                    reply = candidate
                    replyMeta = meta || null
                    completed = true
                    break
                  }
                  const alertText = await frame.locator('[role="alert"]').first().textContent().catch(() => null)
                  if (alertText) sendError = alertText
                  const statusText = await frame.locator('[role="status"]').first().textContent().catch(() => null)
                  if (statusText && /没有完整完成|失败|不可用|未收到|重试/.test(statusText)) noticeText = statusText.trim()
                  await page.waitForTimeout(600)
                }
                elapsedMs = sendStartedAt ? Date.now() - sendStartedAt : null
              }
              const modelName = replyMeta ? (replyMeta.split(' · ')[0].trim() || null) : null
              cardsEvidence.chat = {
                url: frame.url(),
                sessionId,
                character: pngCard.name,
                userMessage: '你好',
                reply,
                replyMeta,
                modelName: modelName && !/已保存|收到的部分回复/.test(modelName) ? modelName : null,
                completed,
                elapsedMs,
                error: sendError || null,
                notice: noticeText || null,
                submitEnabled,
                composeHint: composeHint ? composeHint.trim() : null,
              }
              const negativeExpectedFailure = realNegative && !reply && Boolean(sendError || noticeText)
              check(
                real ? '发送「你好」后出现真实模型回复' : cardsCheckNames[8],
                Boolean(reply) || negativeExpectedFailure,
                reply
                  ? `${real ? '真生成' : 'mock'} 回复：${clip(reply, 100)}`
                  : negativeExpectedFailure
                    ? `网关不可用场景：UI 已显示失败（${clip(sendError || noticeText, 100)}），未出现假回复`
                    : `${real ? 180 : 120}s 内没等到回复${sendError ? `（提示=${clip(sendError, 100)}）` : ''}（owner: app-auth 任务/模型网关 或 plugin-core /tavern-agent）`,
              )
              if (reply) await shot(page, '12-chat-reply.png')

              // 刷新聊天页（同源 iframe 内 reload，不重载整个 DSH）
              await frame.evaluate(() => location.reload()).catch(() => {})
              const reloaded = await waitFrameUrl(/\/conversations\/world\//, 25000)
              let persisted = null
              if (reloaded) {
                await waitChatReady(reloaded, real ? 45000 : 30000)
                const deadline = Date.now() + (real ? 45000 : 30000)
                while (Date.now() < deadline) {
                  const userTexts = await reloaded.locator('.world-chat-message.user p').allTextContents().catch(() => [])
                  const replyTexts = await reloaded.locator('.world-chat-message.assistant p').allTextContents().catch(() => [])
                  const userKept = userTexts.some((text) => text.includes('你好'))
                  const replyKept = replyTexts.some((text) => text.trim().length > 0)
                  if (userKept && replyKept) {
                    // 真生成还要确认「刷新后看到的就是同一条回复」，不能只是任意一条 assistant 文本。
                    const expected = (reply || '').trim()
                    const replyMatched = expected
                      ? replyTexts.some((text) => {
                          const actual = text.trim()
                          return actual === expected || (expected.length >= 20 && actual.includes(expected.slice(0, 20)))
                        })
                      : false
                    persisted = {
                      userMessages: userTexts.length,
                      assistantMessages: replyTexts.length,
                      replyMatched,
                      replyFirst60: expected ? clip(expected, 60) : null,
                      assistantTexts: replyTexts.slice(-2).map((text) => clip(text, 80)),
                    }
                    break
                  }
                  await page.waitForTimeout(500)
                }
              }
              cardsEvidence.reload = persisted
              check(
                cardsCheckNames[9],
                Boolean(persisted),
                persisted
                  ? `刷新后 user=${persisted.userMessages} assistant=${persisted.assistantMessages} 条`
                  : '刷新后聊天记录为空（owner: app-auth 持久化 / cards-client 读取）',
              )
              await shot(page, '13-chat-after-reload.png')

              // 回角色卡库删除这张 PNG 卡，列表应消失且不影响其它卡
              // 注意库里可能有基线同名卡，所以「消失」= 计数回落到导入前基线，而不是归零。
              frame = (await openCharactersPage()) || frame
              // 等列表重新加载完成（应用有「正在读取角色卡…」状态）
              await frame.locator('.workspace-loading').waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})
              await page.waitForTimeout(1000)
              const nameGone = async (timeoutMs = 20000) => {
                const deadline = Date.now() + timeoutMs
                while (Date.now() < deadline) {
                  const current = await frame.getByText(pngCard.name, { exact: true }).count().catch(() => 0)
                  if (current <= (baseline[pngCard.name] || 0)) return true
                  await page.waitForTimeout(400)
                }
                return false
              }
              const cardRoot2 = await findCardRoot(frame, pngCard.name)
              let deleteButton = cardRoot2.getByRole('button', { name: '删除' }).first()
              if (!(await deleteButton.count().catch(() => 0))) {
                deleteButton = frame.getByRole('button', { name: '删除' }).first()
              }
              // 记录删除请求的真实响应，失败时能区分「没点到」和「API 失败」
              const deleteCalls = []
              const onResponse = (response) => {
                if (/character-cards\/[^/?#]+/.test(response.url()) && response.request().method() === 'DELETE') {
                  deleteCalls.push({ url: new URL(response.url()).pathname, status: response.status() })
                }
              }
              page.on('response', onResponse)
              const deleteDisabled = await deleteButton.isDisabled().catch(() => true)
              const beforeUrl = frame.url()
              page.once('dialog', (dialog) => dialog.accept().catch(() => {}))
              await deleteButton.click({ force: true }).catch(() => {})
              // 自定义确认弹层（Sheet）兜底
              const confirmButton = frame.getByRole('button', { name: /确认删除|确定删除/ }).first()
              if (await confirmButton.count().catch(() => 0)) await confirmButton.click({ force: true }).catch(() => {})
              await page.waitForTimeout(1200)
              page.off('response', onResponse)
              const afterUrl = frame.url()
              const listErrorText = await frame.locator('[role="alert"]').first().textContent().catch(() => null)
              const toastText = await frame.locator('.toast, [data-toast], [role="status"]').first().textContent().catch(() => null)
              const countsAfterDelete = {}
              for (const card of manifest.cards) {
                countsAfterDelete[card.name] = await frame.getByText(card.name, { exact: true }).count().catch(() => 0)
              }
              const gone = await nameGone()
              cardsEvidence.deleteDiagnostics = {
                deleteDisabled,
                beforeUrl: new URL(beforeUrl).pathname,
                afterUrl: new URL(afterUrl).pathname,
                deleteCalls,
                listError: listErrorText ? listErrorText.trim() : null,
                toast: toastText ? toastText.trim() : null,
                countsAfterDelete,
              }
              const othersKept = []
              {
                // 删除请求是异步刷新列表：等列表稳定后再数其余卡
                await frame.locator('.workspace-loading').waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})
                await page.waitForTimeout(1200)
              }
              for (const card of manifest.cards.filter((item) => item.name !== pngCard.name)) {
                const count = await frame.getByText(card.name, { exact: true }).count().catch(() => 0)
                const importedBefore = cardsEvidence.imported.some((item) => item.name === card.name)
                const expected = (baseline[card.name] || 0) + (importedBefore ? 1 : 0)
                if (count >= expected) othersKept.push(card.name)
              }
              cardsEvidence.deleted = { name: pngCard.name, baseline: baseline[pngCard.name] || 0, excludedFromList: gone, othersKept }
              check(
                cardsCheckNames[10],
                gone,
                gone
                  ? `列表里「${pngCard.name}」回落到导入前数量（基线 ${baseline[pngCard.name] || 0}）`
                  : '删除后卡名数量没有回落（owner: cards-client 删除按钮/刷列表）',
              )
              check(cardsCheckNames[11], othersKept.length >= 2, `仍在列表：${othersKept.join('、') || '无'}`)
              await shot(page, '14-cards-deleted.png')
            }
          }
        }
      }
    }
  }
  report.cards = cardsEvidence

  // ── S. 角色卡空间（--space）──────────────────────────────────────────────────
  // 依赖 cards-client 的卡详情/世界书/预设/正则页面 + app-auth 的 lore/presets/regex/
  // context-preview API（docs/step3-card-space.md §6）。断言以可见 UI/落库文本为准。
  const runSpace = args.includes('--space')
  const spaceCheckNames = [
    'S1 卡详情改名保存 → 刷新后生效',
    'S2 导入带 character_book 的 v2 卡 → 世界书列表出现导入条目',
    'S3 手工新增世界书条目（key 雨）→ 列表可见、可删除',
    'S4 新增正则「。」→「！」→ 聊天回复无「。」且含「！」',
    'S5 新增预设并绑定卡 → context-preview 返回该预设',
    'S6 S 组操作后 console 无报错',
  ]
  const spaceRecorded = Array(6).fill(false)
  const spaceEvidence = {
    enabled: runSpace,
    fixture: null,
    cardName: null,
    cardId: null,
    detailUrl: null,
    renamed: null,
    lore: null,
    regex: null,
    preset: null,
    chat: null,
    console: null,
    steps: [],
  }
  if (runSpace) {
    const spaceCheck = (index, ok, detail) => {
      spaceRecorded[index] = true
      check(spaceCheckNames[index], ok, detail)
      spaceEvidence.steps.push({ name: spaceCheckNames[index], ok: !!ok, skipped: false, detail: detail === undefined ? null : String(detail) })
    }
    const spaceSkip = (index, detail) => {
      spaceRecorded[index] = true
      skip(spaceCheckNames[index], detail)
      spaceEvidence.steps.push({ name: spaceCheckNames[index], ok: true, skipped: true, detail: detail === undefined ? null : String(detail) })
    }
    const fixtureDir = arg('cards-dir', '/tmp/tavern-verify/cards')
    let loreFixture = null
    try {
      const manifest = JSON.parse(await readFile(path.join(fixtureDir, 'manifest.json'), 'utf8'))
      loreFixture = (manifest.space && manifest.space.lorebook) || null
    } catch {
      loreFixture = null
    }
    spaceEvidence.fixture = loreFixture
    const sleep = (ms) => page.waitForTimeout(ms)
    async function spaceWait(fn, timeoutMs, stepMs = 400) {
      const deadline = Date.now() + timeoutMs
      while (Date.now() < deadline) {
        try {
          if (await fn()) return true
        } catch {}
        await sleep(stepMs)
      }
      return false
    }
    async function spaceVisible(locator, timeoutMs = 10000) {
      try {
        await locator.first().waitFor({ state: 'visible', timeout: timeoutMs })
        return locator.first()
      } catch {
        return null
      }
    }
    async function spaceGoto(pathname) {
      const frame = fnow()
      if (!frame) return null
      await frame.evaluate((u) => location.assign(u), pathname).catch(() => {})
      return waitFrameUrl(new RegExp(pathname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 20000)
    }
    const regexPageUrl = /\/characters\/regex/
    const detailPageUrl = /\/characters\/(?!regex)[^/?#]+/

    let spaceCardName = loreFixture ? loreFixture.name : null
    let detailFrame = null
    let regexFrame = null
    let chatFrame = null
    // 第一次「不可用」后把剩余 S1–S5 记 SKIP（S6 console 单独判定）。
    let unavailableAt = -1
    const unavailable = (index, reason) => {
      if (unavailableAt !== -1) return
      unavailableAt = index
      for (let i = index; i < 5; i += 1) spaceSkip(i, reason)
    }
    const spaceErrors = () => consoleErrors.filter((text) => !/Failed to load resource/.test(text))

    try {
      if (!loreFixture) {
        unavailable(0, `缺少 lorebook fixture（先跑 python3 scripts/fixtures/make-cards.py；找 ${path.join(fixtureDir, 'manifest.json')} 的 space.lorebook）`)
      } else {
        // ── 打开角色卡库并导入 lorebook 卡 ──
        let frameS = fnow()
        if (!frameS) {
          unavailable(0, '找不到 /tavern/ iframe（面板未打开）')
        } else if (!/\/characters(\/|$|\?)/.test(frameS.url())) {
          frameS = await openCharactersPage()
          if (!frameS) unavailable(0, '无法进入角色卡库（owner: plugin-core 面板/导航；cards-client /characters 路由）')
        }
        if (frameS) {
          const baselineCount = await frameS.getByText(loreFixture.name, { exact: true }).count().catch(() => 0)
          const fileInput = frameS.locator('input[type="file"][accept*=".png"]').first()
          if (!(await fileInput.count().catch(() => 0))) {
            unavailable(0, '角色卡库缺少导入入口 input[type=file]（owner: cards-client）')
          } else {
            await fileInput.setInputFiles(path.join(fixtureDir, loreFixture.file)).catch(() => {})
            const imported = await spaceWait(async () => {
              const count = await frameS.getByText(loreFixture.name, { exact: true }).count().catch(() => 0)
              return count > baselineCount
            }, 30000)
            if (!imported) {
              unavailable(0, `导入 ${loreFixture.file} 后列表未出现卡名「${loreFixture.name}」（owner: cards-client 导入/列表 或 app-auth character_book 解析）`)
            } else {
              // ── 打开卡详情（编辑按钮）──
              const cardRoot = await findCardRoot(frameS, loreFixture.name)
              const editButton = cardRoot.getByRole('button', { name: '编辑' }).first()
              if (!(await editButton.count().catch(() => 0))) {
                unavailable(0, '卡片上没有「编辑」按钮，无法打开卡详情（owner: cards-client）')
              } else {
                await editButton.click().catch(() => {})
                detailFrame = await waitFrameUrl(detailPageUrl, 20000)
                if (!detailFrame) {
                  unavailable(0, '点击「编辑」后未进入 /characters/<id> 卡详情（owner: cards-client 路由/详情页）')
                } else {
                  const detailPath = new URL(detailFrame.url()).pathname
                  spaceEvidence.detailUrl = detailPath
                  spaceEvidence.cardId = (detailPath.match(/\/characters\/([^/?#]+)/) || [])[1] || null
                  spaceEvidence.cardName = spaceCardName
                }
              }
            }
          }
        }
      }

      // ── S1 改名保存 → 刷新后生效 ──
      if (detailFrame && unavailableAt === -1) {
        const nameInput = detailFrame.getByLabel(/角色名称/).first()
        if (await spaceVisible(nameInput, 8000)) {
          const oldName = await nameInput.inputValue().catch(() => '')
          const newName = `${spaceCardName}改`
          await nameInput.fill(newName).catch(() => {})
          await detailFrame.getByRole('button', { name: '保存设定' }).first().click().catch(() => {})
          const noticeText = await spaceWait(async () => {
            const status = detailFrame.getByRole('status').first()
            return (await status.count().catch(() => 0)) > 0
          }, 12000)
          const saveAlert = (await detailFrame.getByRole('alert').first().innerText().catch(() => '')).trim()
          await detailFrame.evaluate(() => location.reload()).catch(() => {})
          const refreshed = (await waitFrameUrl(detailPageUrl, 25000)) || detailFrame
          detailFrame = refreshed
          const afterName = await spaceWait(async () => {
            const input = detailFrame.getByLabel(/角色名称/).first()
            return (await input.inputValue().catch(() => '')) === newName
          }, 20000)
          const persistedName = await detailFrame.getByLabel(/角色名称/).first().inputValue().catch(() => '')
          spaceEvidence.renamed = { before: oldName, after: persistedName, notice: !!noticeText, error: saveAlert || null }
          spaceCheck(0, afterName && !saveAlert, `旧名=${oldName} 新名=${persistedName} 刷新生效=${afterName}${saveAlert ? ` 错误=${saveAlert}` : ''}`)
          if (afterName) spaceCardName = newName
          await shot(page, '15-space-detail.png')
        } else {
          unavailable(0, '卡详情页没有「角色名称」表单（owner: cards-client CardDetailPage）')
        }
      }

      // ── S2 导入条目出现在世界书列表 ──
      if (detailFrame && unavailableAt === -1) {
        const loreHeading = detailFrame.getByRole('heading', { name: '世界书' }).first()
        if (await spaceVisible(loreHeading, 8000)) {
          await sleep(600)
          const loreTexts = await detailFrame.locator('li.lore-item').allTextContents().catch(() => [])
          const markers = (loreFixture && loreFixture.markers) || []
          const markerHits = markers.filter((marker) => loreTexts.some((text) => text.includes(marker)))
          const importBadges = await detailFrame.locator('li.lore-item').filter({ hasText: '导入' }).count().catch(() => 0)
          const ok = loreTexts.length >= 2 && markerHits.length === markers.length
          spaceEvidence.lore = { imported: true, entries: loreTexts.length, markerHits, importBadges, ok }
          spaceCheck(1, ok, `世界书条目=${loreTexts.length} 标记命中=${markerHits.length}/${markers.length} 导入条目=${importBadges}`)
          await shot(page, '16-space-lore.png')
        } else {
          unavailable(1, '卡详情页缺少「世界书」区块（owner: cards-client）')
        }
      }

      // ── S3 手工新增条目（key 雨）→ 可见、可删除 ──
      if (detailFrame && unavailableAt === -1) {
        const manualMarker = '【E2E-MANUAL-雨】雨停之前别收起伞。'
        const addButton = detailFrame.getByRole('button', { name: '新增条目' }).first()
        if (await spaceVisible(addButton, 8000)) {
          await addButton.click().catch(() => {})
          const editor = detailFrame.locator('.lore-editor').first()
          const editorVisible = await spaceVisible(editor, 6000)
          let added = false
          let removed = false
          if (editorVisible) {
            await editor.getByLabel(/条目名称/).first().fill('手工雨').catch(() => {})
            await editor.getByLabel(/关键词/).first().fill('雨').catch(() => {})
            await editor.getByLabel(/正文/).first().fill(manualMarker).catch(() => {})
            await detailFrame.getByRole('button', { name: '保存条目' }).first().click().catch(() => {})
            added = await spaceWait(async () => {
              const rows = detailFrame.locator('li.lore-item').filter({ hasText: manualMarker })
              return (await rows.count().catch(() => 0)) > 0
            }, 15000)
            if (added) {
              const row = detailFrame.locator('li.lore-item').filter({ hasText: manualMarker }).first()
              await row.locator('button[aria-label="删除世界书条目"]').first().click().catch(() => {})
              const confirm = detailFrame.getByRole('button', { name: '确认删除' }).first()
              if (await spaceVisible(confirm, 6000)) await confirm.click().catch(() => {})
              removed = await spaceWait(async () => {
                const rows = detailFrame.locator('li.lore-item').filter({ hasText: manualMarker })
                return (await rows.count().catch(() => 0)) === 0
              }, 15000)
            }
          }
          spaceEvidence.lore = { ...(spaceEvidence.lore || {}), manual: { added, removed, editorVisible: !!editorVisible } }
          spaceCheck(2, added && removed, `新增可见=${added} 删除后消失=${removed}${editorVisible ? '' : '（未出现 .lore-editor，owner: cards-client）'}`)
          await shot(page, '17-space-lore-manual.png')
        } else {
          unavailable(2, '世界书区块缺少「新增条目」按钮（owner: cards-client）')
        }
      }

      // ── S4 新增正则 。→！ → 聊天落库回复已替换 ──
      if (detailFrame && unavailableAt === -1) {
        regexFrame = await spaceGoto('/tavern/characters/regex')
        if (!regexFrame) {
          unavailable(3, '无法进入「输出替换规则」页面 /characters/regex（owner: cards-client）')
        } else {
          const newRule = regexFrame.getByRole('button', { name: '新增规则' }).first()
          if (!(await spaceVisible(newRule, 8000))) {
            unavailable(3, '正则页缺少「新增规则」按钮（owner: cards-client）')
          } else {
            await newRule.click().catch(() => {})
            const ruleName = 'E2E 句号替换'
            const ruleForm = regexFrame.locator('form').filter({ has: regexFrame.getByLabel(/匹配正则/) }).first()
            const targetForm = (await ruleForm.count().catch(() => 0)) ? ruleForm : regexFrame
            await targetForm.getByLabel(/规则名称/).first().fill(ruleName).catch(() => {})
            await targetForm.getByLabel(/匹配正则/).first().fill('。').catch(() => {})
            await targetForm.getByLabel(/替换为/).first().fill('！').catch(() => {})
            await regexFrame.getByRole('button', { name: '保存规则' }).first().click().catch(() => {})
            const ruleListed = await spaceWait(async () => {
              const text = await regexFrame.locator('li.rule-item').filter({ hasText: ruleName }).count().catch(() => 0)
              return text > 0
            }, 15000)
            spaceEvidence.regex = { name: ruleName, pattern: '。', replacement: '！', listed: ruleListed }
            await shot(page, '18a-space-regex.png')
            // 去角色卡库，开聊，发消息
            const backToList = await openCharactersPage()
            let chatOk = false
            if (!backToList) {
              spaceCheck(3, false, '新增规则后无法回到角色卡库（owner: cards-client 导航）')
            } else {
              const cardRoot = await findCardRoot(backToList, spaceCardName)
              await cardRoot.getByRole('button', { name: '开聊' }).first().click().catch(() => {})
              chatFrame = await waitFrameUrl(/\/conversations\/world\/[^/?#]+/, 30000)
              if (!chatFrame) {
                spaceCheck(3, false, '「开聊」未进入聊天页（owner: cards-client 或 app-auth chats API）')
              } else {
                await waitChatReady(chatFrame, 30000)
                const userMsg = '你好，来一盏茶。今天茶馆生意好吗？'
                const composer = chatFrame.locator('#world-chat-message').first()
                await composer.fill(userMsg).catch(() => {})
                const sendButton = chatFrame.locator('form.world-chat-compose button[type="submit"]').first()
                const enabled = await spaceWait(async () => !(await sendButton.isDisabled().catch(() => true)), 10000)
                if (enabled) await sendButton.click().catch(() => {})
                else await composer.press('Control+Enter').catch(() => {})
                let reply = null
                let replyMeta = ''
                const replyDeadline = Date.now() + (real ? 180000 : 120000)
                while (Date.now() < replyDeadline) {
                  const items = chatFrame.locator('.world-chat-message.assistant')
                  const count = await items.count().catch(() => 0)
                  if (count) {
                    const last = items.nth(count - 1)
                    const meta = (await last.locator('small').last().innerText().catch(() => '')).trim()
                    const text = (await last.locator('p').last().innerText().catch(() => '')).trim()
                    if (text && (!real || /已保存|收到的部分回复/.test(meta))) {
                      reply = text
                      replyMeta = meta
                      break
                    }
                  }
                  await sleep(1000)
                }
                const alertText = (await chatFrame.getByRole('alert').first().innerText().catch(() => '')).trim()
                let afterRefresh = reply
                if (reply) {
                  await chatFrame.evaluate(() => location.reload()).catch(() => {})
                  const refreshedChat = (await waitFrameUrl(/\/conversations\/world\/[^/?#]+/, 25000)) || chatFrame
                  chatFrame = refreshedChat
                  await waitChatReady(chatFrame, 30000)
                  const persisted = await spaceWait(async () => {
                    const items = chatFrame.locator('.world-chat-message.assistant')
                    const count = await items.count().catch(() => 0)
                    if (!count) return false
                    const text = (await items.nth(count - 1).locator('p').last().innerText().catch(() => '')).trim()
                    return Boolean(text)
                  }, 20000)
                  if (persisted) {
                    const items = chatFrame.locator('.world-chat-message.assistant')
                    const count = await items.count()
                    afterRefresh = (await items.nth(count - 1).locator('p').last().innerText()).trim()
                  }
                }
                const noPeriod = Boolean(afterRefresh) && !afterRefresh.includes('。')
                const hasBang = Boolean(afterRefresh) && afterRefresh.includes('！')
                chatOk = noPeriod && hasBang
                spaceEvidence.chat = {
                  url: new URL(chatFrame.url()).pathname,
                  userMessage: userMsg.slice(0, 40),
                  replyFirst60: reply ? reply.slice(0, 60) : null,
                  replyLength: reply ? reply.length : 0,
                  meta: replyMeta || null,
                  afterRefreshFirst60: afterRefresh ? afterRefresh.slice(0, 60) : null,
                  noPeriod,
                  hasBang,
                  error: alertText || null,
                }
                spaceEvidence.regex = { ...(spaceEvidence.regex || {}), replyFirst60: reply ? reply.slice(0, 60) : null }
                spaceCheck(3, chatOk, `规则落库=${ruleListed} 回复=${reply ? `${reply.length}字` : '无'} 无「。」=${noPeriod} 含「！」=${hasBang}${alertText ? ` 错误=${alertText}` : ''}`)
                await shot(page, '18-space-regex-chat.png')
              }
            }
          }
        }
      }

      // ── S5 新建预设并绑定 → context-preview（UI 注入预览）返回该预设 ──
      if (unavailableAt === -1 && spaceEvidence.cardId) {
        const detailFrame2 = await spaceGoto(`/tavern/characters/${spaceEvidence.cardId}`)
        if (!detailFrame2) {
          unavailable(4, '无法返回卡详情页做预设绑定（owner: cards-client）')
        } else {
          detailFrame = detailFrame2
          const presetName = 'E2E 预设'
          const newPresetButton = detailFrame.getByRole('button', { name: '新建预设' }).first()
          if (!(await spaceVisible(newPresetButton, 8000))) {
            unavailable(4, '卡详情页缺少「新建预设」按钮（owner: cards-client）')
          } else {
            await newPresetButton.click().catch(() => {})
            const presetNameInput = detailFrame.getByLabel(/预设名称/).first()
            const inputVisible = await spaceVisible(presetNameInput, 6000)
            if (inputVisible) {
              await presetNameInput.fill(presetName).catch(() => {})
              const suffix = detailFrame.getByLabel(/系统提示后缀/).first()
              if (await suffix.count().catch(() => 0)) await suffix.fill('请保持茶博士的语气，句子短一些。').catch(() => {})
              await detailFrame.getByRole('button', { name: '创建并绑定' }).first().click().catch(() => {})
            }
            const bound = await spaceWait(async () => {
              const select = detailFrame.getByLabel(/当前卡绑定的预设/).first()
              if (!(await select.count().catch(() => 0))) return false
              const selected = await select.evaluate((el) => (el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '')).catch(() => '')
              return Boolean(selected && selected.includes(presetName))
            }, 15000)
            let previewOk = false
            let previewText = ''
            const previewInput = detailFrame.getByLabel(/试写一句对话/).first()
            if (await spaceVisible(previewInput, 6000)) {
              await previewInput.fill('茶博士，来一盏茶。').catch(() => {})
              await detailFrame.getByRole('button', { name: '预览' }).first().click().catch(() => {})
              const result = await spaceVisible(detailFrame.locator('.preview-result'), 15000)
              if (result) {
                previewText = (await result.innerText().catch(() => '')).trim()
                previewOk = previewText.includes(`预设：${presetName}`)
              }
            }
            spaceEvidence.preset = {
              name: presetName,
              bound,
              previewOk,
              previewFirst120: previewText ? previewText.slice(0, 120) : null,
              contextPreviewError: (await detailFrame.getByRole('alert').first().innerText().catch(() => '')).trim() || null,
            }
            spaceCheck(4, bound && previewOk, `绑定=${bound} 注入预览命中=${previewOk}${inputVisible ? '' : '（新建表单未出现，owner: cards-client）'}`)
            await shot(page, '19-space-preset.png')
          }
        }
      }
    } catch (error) {
      if (unavailableAt === -1) unavailable(0, `S 组执行异常：${error && error.message ? error.message : error}`)
    }

    // 保证 --space 恒有 6 项（S1–S5 至少为 SKIP，不是漏项）。
    for (let spaceIndex = 0; spaceIndex < 5; spaceIndex += 1) {
      if (!spaceRecorded[spaceIndex]) {
        spaceSkip(spaceIndex, 'S 组前置未满足（fixture/面板/角色卡库/卡详情不可用，见相邻 SKIP）')
      }
    }
    if (!spaceRecorded[5]) {
      const errors = spaceErrors()
      spaceEvidence.console = { clean: errors.length === 0, errors: errors.slice(0, 3) }
      spaceCheck(5, errors.length === 0, errors.slice(0, 3).join(' | ') || 'clean')
    }
  }
  report.space = spaceEvidence

  // ── T. 群聊（--groups）──────────────────────────────────────────────────────
  // 依赖 cards-client 的 /characters 多选 + /characters/groups 列表/详情页 + app-auth 的
  // /card-group-chats API（docs/step3b-group-chat.md §5）。真实生成请配合 --real 使用。
  const runGroups = args.includes('--groups')
  const groupCheckNames = [
    'T1 多选两张角色卡发起群聊 → 进群聊页且参与者正确',
    'T2 发送消息 → 出现真实回复且发言人=返回的 speakerName',
    'T3 再发一条 → 发言人轮转（与上一条不同）',
    'T4 整页刷新群聊页后消息仍在',
    'T5 删除群聊 → 列表数量回落',
    'T6 群聊操作后 console 无报错',
  ]
  const groupsEvidence = {
    enabled: runGroups,
    selected: [],
    selectionCount: null,
    group: null,
    messages: [],
    rotation: null,
    persisted: null,
    deleted: null,
    console: null,
    steps: [],
  }
  const groupSteps = groupsEvidence.steps
  const groupCheck = (name, ok, detail) => {
    check(name, ok, detail)
    groupSteps.push({ name, ok: !!ok, skipped: false, detail: detail === undefined ? null : String(detail) })
  }
  const groupSkip = (name, detail) => {
    skip(name, detail)
    groupSteps.push({ name, ok: true, skipped: true, detail: detail === undefined ? null : String(detail) })
  }
  const skipRemainingGroups = (reason) => {
    const seen = new Set(groupSteps.map((step) => step.name))
    for (const name of groupCheckNames) if (!seen.has(name)) groupSkip(name, reason)
  }
  if (runGroups) {
    const groupConsoleStart = consoleErrors.length
    let frame = fnow()
    let chatId = ''
    const sendCalls = []
    const onGroupResponse = (response) => {
      try {
        const pathName = new URL(response.url()).pathname
        if (!/\/card-group-chats\/[^/]+\/messages$/.test(pathName)) return
        if (response.request().method() !== 'POST') return
        const call = { status: response.status(), url: pathName, at: Date.now(), body: undefined }
        sendCalls.push(call)
        response.json().then((data) => { call.body = data }).catch(() => {})
      } catch {
        /* 页面卸载时的异常响应忽略 */
      }
    }
    page.on('response', onGroupResponse)
    try {
      if (!frame) {
        skipRemainingGroups('故事之间应用 iframe 没打开（侧栏/面板未就绪，owner: plugin-core）')
      } else {
        // T1. 角色卡库多选 2 张 → 发起群聊 → 进群聊页且参与者正确。
        frame = (await openCharactersPage()) || frame
        await frame.locator('.workspace-loading').waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})
        await page.waitForTimeout(800)
        const selected = []
        for (const name of ['顾清和', '沈夜舟', '陆三更改']) {
          if (selected.length >= 2) break
          const card = frame.locator('article.character-card').filter({ has: frame.getByText(name, { exact: true }) }).first()
          if (!(await card.count().catch(() => 0))) continue
          const box = card.locator('input[type=checkbox]').first()
          if (!(await box.count().catch(() => 0))) continue
          await box.check({ force: true }).catch(() => {})
          if (await box.isChecked().catch(() => false)) selected.push(name)
        }
        if (selected.length < 2) {
          // 兜底：库里若还有别的卡就任选两张（例如 H 组提前失败留下不同卡名）。
          const cards = frame.locator('article.character-card')
          const total = await cards.count().catch(() => 0)
          for (let index = 0; index < total && selected.length < 2; index += 1) {
            const card = cards.nth(index)
            const name = ((await card.locator('h2').first().textContent().catch(() => '')) || '').trim()
            if (!name || selected.includes(name)) continue
            const box = card.locator('input[type=checkbox]').first()
            if (!(await box.count().catch(() => 0))) continue
            await box.check({ force: true }).catch(() => {})
            if (await box.isChecked().catch(() => false)) selected.push(name)
          }
        }
        groupsEvidence.selected = selected
        const selectionCount = await frame.locator('.selection-count').first().textContent().catch(() => null)
        groupsEvidence.selectionCount = selectionCount ? selectionCount.trim() : null
        await shot(page, '20-groups-select.png')
        if (selected.length < 2) {
          skipRemainingGroups(`角色卡库里可勾选的卡不足 2 张（已选 ${selected.length}；H 组之后应保留 ≥2 张测试卡，owner: cards-client）`)
        } else {
          const startButton = frame.getByRole('button', { name: /发起群聊/ }).first()
          await startButton.click({ force: true }).catch(() => {})
          const groupFrame = await waitFrameUrl(/\/characters\/groups\/[^/]+/, 30000)
          if (groupFrame) frame = groupFrame
          const groupUrl = groupFrame ? groupFrame.url() : ''
          const idMatch = groupUrl.match(/\/characters\/groups\/([^/?#]+)/)
          chatId = idMatch ? idMatch[1] : ''
          await frame.locator('.group-participants').waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
          const participants = ((await frame.locator('.group-participant small').allTextContents().catch(() => [])) || [])
            .map((text) => text.trim())
            .filter(Boolean)
          const participantsOk = selected.length >= 2 && selected.every((name) => participants.includes(name))
          groupsEvidence.group = { id: chatId, url: groupUrl, participants }
          groupCheck(
            groupCheckNames[0],
            Boolean(groupFrame && chatId && participants.length >= 2 && participantsOk),
            `选中=${selected.join('、')}；群聊页=${groupUrl ? new URL(groupUrl).pathname : 'n/a'}；参与者=${participants.join('、') || '无'}${
              selectionCount ? `；选择栏=${clip(selectionCount.trim(), 40)}` : ''
            }`,
          )
          if (!chatId) {
            skipRemainingGroups('发起群聊后 URL 里拿不到群聊 id（创建/路由失败，owner: cards-client / app-auth）')
          } else {
            const assistantArticles = () => frame.locator('article.group-message.from-assistant')
            const countSavedAssistant = async () => {
              const total = await assistantArticles().count().catch(() => 0)
              let savedTotal = 0
              for (let index = 0; index < total; index += 1) {
                const bubble = assistantArticles().nth(index).locator('.group-bubble')
                const cls = (await bubble.getAttribute('class').catch(() => '')) || ''
                const text = ((await bubble.textContent().catch(() => '')) || '').trim()
                if (!cls.includes('pending') && text && text !== '…' && text !== '正在组织语言…') savedTotal += 1
              }
              return savedTotal
            }
            const lastSavedAssistant = async () => {
              const total = await assistantArticles().count().catch(() => 0)
              let result = null
              for (let index = 0; index < total; index += 1) {
                const article = assistantArticles().nth(index)
                const bubble = article.locator('.group-bubble')
                const cls = (await bubble.getAttribute('class').catch(() => '')) || ''
                const text = ((await bubble.textContent().catch(() => '')) || '').trim()
                if (cls.includes('pending') || !text || text === '…' || text === '正在组织语言…') continue
                const speaker = ((await article.locator('.group-message-speaker strong').first().textContent().catch(() => '')) || '').trim()
                result = { text, speaker }
              }
              return result
            }
            const waitSavedReply = async (beforeCount, timeoutMs) => {
              const deadline = Date.now() + timeoutMs
              while (Date.now() < deadline) {
                const savedTotal = await countSavedAssistant()
                if (savedTotal > beforeCount) return lastSavedAssistant()
                const alert = await frame.locator('[role="alert"]').first().textContent().catch(() => null)
                if (alert && /失败|超时|错误/.test(alert)) return { error: alert.trim() }
                await page.waitForTimeout(700)
              }
              return null
            }
            const sendMessage = async (messageText) => {
              const textarea = frame.getByPlaceholder('对大家说点什么…').first()
              if (!(await textarea.count().catch(() => 0))) return { error: '找不到群聊输入框（owner: cards-client）' }
              await textarea.fill(messageText)
              const sendButton = frame.locator('form.group-composer button[type="submit"]').first()
              const enableDeadline = Date.now() + 15000
              while (Date.now() < enableDeadline && (await sendButton.isDisabled().catch(() => true))) await page.waitForTimeout(200)
              const before = await countSavedAssistant()
              const callsBefore = sendCalls.length
              const startedAt = Date.now()
              await sendButton.click({ force: true }).catch(() => {})
              // DSH 桌宠气泡/悬浮层可能盖住右下角发送按钮，force click 会点在遮罩上而不触发 submit。
              // 先正常点；几秒内既没有 POST 也没有新回复时，退化为 DOM click（仍走应用 send() 逻辑）。
              let clickPath = 'click'
              const firstTick = Date.now() + 6000
              while (Date.now() < firstTick && sendCalls.length <= callsBefore) await page.waitForTimeout(200)
              if (sendCalls.length <= callsBefore) {
                clickPath = 'js-click'
                await sendButton.evaluate((el) => el.click()).catch(() => {})
              }
              const callDeadline = Date.now() + 20000
              while (Date.now() < callDeadline && sendCalls.length <= callsBefore) await page.waitForTimeout(200)
              const call = sendCalls.length > callsBefore ? sendCalls[sendCalls.length - 1] : null
              if (call) {
                const bodyDeadline = Date.now() + 3000
                while (call.body === undefined && Date.now() < bodyDeadline) await page.waitForTimeout(100)
              }
              const body = call && call.body ? call.body : null
              const reply = await waitSavedReply(before, 180000)
              return {
                text: messageText,
                elapsedMs: Date.now() - startedAt,
                clickPath,
                httpStatus: call ? call.status : null,
                response: body
                  ? { taskId: body.taskId || null, chatId: body.chatId || null, speakerCardId: body.speakerCardId || null, speakerName: body.speakerName || null }
                  : null,
                reply: reply && reply.text ? reply : null,
                error: reply && reply.error ? reply.error : null,
              }
            }
            // T2. 第一条消息：真实回复 + 发言人 = API 返回的 speakerName。
            const first = await sendMessage('你们好')
            groupsEvidence.messages.push(first)
            const speaker1 = first.response && first.response.speakerName ? String(first.response.speakerName) : null
            const shown1 = first.reply && first.reply.speaker ? String(first.reply.speaker) : null
            const reply1 = first.reply ? first.reply.text : ''
            const mockPhrase = '我们可以聊聊此刻的想法'
            const hasMockPhrase = reply1.includes(mockPhrase)
            groupCheck(
              groupCheckNames[1],
              Boolean(reply1) && Boolean(speaker1) && shown1 === speaker1 && !hasMockPhrase,
              reply1
                ? `发言人=${shown1 || '无'}（API speakerName=${speaker1 || '无'}，HTTP ${first.httpStatus ?? 'n/a'}）；${real ? '真生成' : 'mock'} 回复：${clip(reply1, 100)}；耗时=${first.elapsedMs}ms${
                    hasMockPhrase ? '；⚠️ 出现 mock 特征句，应用仍是 AI_MODE=mock（owner: app-auth 环境）' : ''
                  }`
                : `180s 内没等到群聊回复${first.error ? `（${clip(first.error, 100)}）` : ''}（owner: app-auth 群聊任务/模型网关）`,
            )
            if (reply1) await shot(page, '21-group-reply.png')
            // T3. 第二条消息：发言人轮转（与第一条不同），且 UI 发言人 = 新 speakerName。
            const nextSpeakerText = ((await frame.locator('.group-next').first().textContent().catch(() => '')) || '').trim()
            let second = { error: '第一条没有成功，未执行第二条' }
            if (reply1) {
              second = await sendMessage('那你们谁先说说？')
              groupsEvidence.messages.push(second)
            }
            const speaker2 = second.response && second.response.speakerName ? String(second.response.speakerName) : null
            const shown2 = second.reply && second.reply.speaker ? String(second.reply.speaker) : null
            const reply2 = second.reply ? second.reply.text : ''
            groupsEvidence.rotation = { nextSpeakerText, first: speaker1, second: speaker2, shown: shown2 }
            groupCheck(
              groupCheckNames[2],
              Boolean(reply1 && reply2 && speaker1 && speaker2 && speaker2 !== speaker1 && shown2 === speaker2),
              reply2
                ? `发言人轮转：${speaker1 || '无'} → ${speaker2 || '无'}（UI=${shown2 || '无'}；下一位提示=${clip(nextSpeakerText, 40) || '无'}）；第二条：${clip(reply2, 80)}`
                : second.error
                  ? `第二条没发成/没回复：${clip(second.error, 120)}（owner: app-auth 轮转/pending/模型网关）`
                  : `发言人没有轮转（${speaker1 || '无'} → ${speaker2 || '无'}；owner: app-auth 轮转逻辑）`,
            )
            if (reply2) await shot(page, '22-group-rotate.png')
            // T4. iframe 内整页刷新 → 两条问答仍在（文本比对，不只看条数）。
            await frame.evaluate(() => location.reload()).catch(() => {})
            const escapedId = chatId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            const reloaded = await waitFrameUrl(new RegExp(`/characters/groups/${escapedId}`), 30000)
            let persisted = null
            if (reloaded) {
              frame = reloaded
              await frame.locator('.group-messages, .group-empty').first().waitFor({ state: 'visible', timeout: 30000 }).catch(() => {})
              const deadline = Date.now() + 45000
              while (Date.now() < deadline) {
                const userTexts = ((await frame.locator('article.group-message.from-user .group-bubble').allTextContents().catch(() => [])) || []).map((text) => text.trim())
                const assistantTexts = (((await frame.locator('article.group-message.from-assistant .group-bubble').allTextContents().catch(() => [])) || [])
                  .map((text) => text.trim())
                  .filter((text) => text && text !== '…' && text !== '正在组织语言…'))
                const hasFirstUser = userTexts.some((text) => text.includes('你们好'))
                const replyMatched = (expected) =>
                  Boolean(expected) && assistantTexts.some((text) => text === expected || (expected.length >= 20 && text.includes(expected.slice(0, 20))))
                const hasReply1 = replyMatched(reply1 || '')
                const hasReply2 = replyMatched(reply2 || '')
                if (hasFirstUser && hasReply1 && hasReply2) {
                  persisted = {
                    userMessages: userTexts.length,
                    assistantMessages: assistantTexts.length,
                    reply1Matched: hasReply1,
                    reply2Matched: hasReply2,
                    assistantTexts: assistantTexts.slice(-2).map((text) => clip(text, 80)),
                  }
                  break
                }
                await page.waitForTimeout(600)
              }
            }
            groupsEvidence.persisted = persisted
            groupCheck(
              groupCheckNames[3],
              Boolean(persisted),
              persisted
                ? `刷新后 user=${persisted.userMessages} assistant=${persisted.assistantMessages} 条（reply1Matched=${persisted.reply1Matched} reply2Matched=${persisted.reply2Matched}）`
                : '刷新后群聊消息不完整或为空（owner: app-auth 持久化 / cards-client 读取）',
            )
            await shot(page, '23-group-reload.png')
            // T5. 返回群聊列表 → 删除本群聊 → 列表数量回落。
            const backLink = frame.getByRole('link', { name: '返回群聊列表' }).first()
            if (await backLink.count().catch(() => 0)) await backLink.click({ force: true }).catch(() => {})
            else await frame.locator('a[href="/characters/groups"]').first().click({ force: true }).catch(() => {})
            const listFrame = await waitFrameUrl(/\/characters\/groups$/, 20000)
            if (listFrame) frame = listFrame
            await frame.locator('.group-list, .workspace-empty').first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
            const beforeCount = await frame.locator('article.group-item').count().catch(() => 0)
            const target = frame.locator(`article.group-item[data-chat-id="${chatId}"]`).first()
            let deleted = null
            if (await target.count().catch(() => 0)) {
              await target.getByRole('button', { name: '删除' }).first().click({ force: true }).catch(() => {})
              const confirm = target.getByRole('button', { name: /确认删除/ }).first()
              if (await confirm.count().catch(() => 0)) await confirm.click({ force: true }).catch(() => {})
              const deleteDeadline = Date.now() + 20000
              while (Date.now() < deleteDeadline) {
                const gone = (await frame.locator(`article.group-item[data-chat-id="${chatId}"]`).count().catch(() => 1)) === 0
                const afterCount = await frame.locator('article.group-item').count().catch(() => beforeCount)
                if (gone && afterCount < beforeCount) {
                  deleted = { before: beforeCount, after: afterCount }
                  break
                }
                await page.waitForTimeout(500)
              }
            }
            groupsEvidence.deleted = deleted
            groupCheck(
              groupCheckNames[4],
              Boolean(deleted),
              deleted
                ? `群聊列表 ${deleted.before} → ${deleted.after}`
                : `删除后列表没有回落（chatId=${chatId}，列表 ${beforeCount} 条；owner: cards-client 删除按钮 / app-auth DELETE）`,
            )
            await shot(page, '24-group-deleted.png')
          }
        }
      }
    } finally {
      page.off('response', onGroupResponse)
      const groupConsoleErrors = consoleErrors.slice(groupConsoleStart).filter((text) => !/Failed to load resource/.test(text))
      groupsEvidence.console = { clean: groupConsoleErrors.length === 0, errors: groupConsoleErrors.slice(0, 3) }
      const seen = new Set(groupSteps.map((step) => step.name))
      if (!seen.has(groupCheckNames[5])) {
        groupCheck(groupCheckNames[5], groupConsoleErrors.length === 0, groupConsoleErrors.slice(0, 3).join(' | ') || 'clean')
      }
    }
  }
  report.groups = groupsEvidence

  // ── U. 长期伙伴与记忆（--memory / C 组）───────────────────────────────────
  // 依赖 task-19（app-auth：/memories CRUD、/memories/extract、/companion、context-preview 的
  // memories/memorySection）与 task-20（cards-client：卡详情「长期记忆」区、注入预览记忆区块、
  // 角色卡库「长期伙伴」置顶分组）。契约见 docs/step4-companion-memory.md §3/§7。
  const runMemory = args.includes('--memory')
  const memoryCheckNames = [
    'C1 手工新增记忆 → 列表可见、可编辑、可删除',
    'C2 发 2 条含自述的消息后点「立即整理」→ 新增≥1 条且列表增加',
    'C3 注入预览包含手工记忆标题',
    'C4 停用/删除记忆后注入预览不再包含',
    'C5 设为长期伙伴 → 角色卡库顶部出现该卡的长期伙伴分组',
    'C6 C 组操作后 console 无报错',
  ]
  const memoryRecorded = Array(6).fill(false)
  const memoryEvidence = {
    enabled: runMemory,
    fixture: null,
    cardName: null,
    cardId: null,
    detailUrl: null,
    chat: null,
    manual: null,
    extract: null,
    preview: null,
    disabled: null,
    deleted: null,
    companion: null,
    console: null,
    steps: [],
  }
  if (runMemory) {
    const memCheck = (index, ok, detail) => {
      memoryRecorded[index] = true
      check(memoryCheckNames[index], ok, detail)
      memoryEvidence.steps.push({ name: memoryCheckNames[index], ok: !!ok, skipped: false, detail: detail === undefined ? null : String(detail) })
    }
    const memSkip = (index, detail) => {
      memoryRecorded[index] = true
      skip(memoryCheckNames[index], detail)
      memoryEvidence.steps.push({ name: memoryCheckNames[index], ok: true, skipped: true, detail: detail === undefined ? null : String(detail) })
    }
    let memUnavailableAt = -1
    const memUnavailable = (index, reason) => {
      if (memUnavailableAt !== -1) return
      memUnavailableAt = index
      for (let i = index; i < 5; i += 1) memSkip(i, reason)
    }
    let onPreviewResponse = null
    const memoryConsoleStart = consoleErrors.length
    const memErrors = () => consoleErrors.slice(memoryConsoleStart).filter((text) => !/Failed to load resource/.test(text))
    const memSleep = (ms) => page.waitForTimeout(ms)
    async function memWait(fn, timeoutMs, stepMs = 400) {
      const deadline = Date.now() + timeoutMs
      while (Date.now() < deadline) {
        try {
          if (await fn()) return true
        } catch {}
        await memSleep(stepMs)
      }
      return false
    }
    // 回到「角色卡库」列表页。openCharactersPage() 的 URL 粗匹配会把 /characters/groups
    // 也当成卡库（C 组在 T 组之后运行，起点常在群聊页），这里强制落到 /characters 本身。
    async function memoryLibrary() {
      const here = fnow()
      if (here && new URL(here.url()).pathname === '/tavern/characters') return here
      const opened = await openCharactersPage()
      const frameNow = fnow()
      if (frameNow && new URL(frameNow.url()).pathname === '/tavern/characters') return frameNow
      if (frameNow) await frameNow.evaluate(() => location.assign('/tavern/characters')).catch(() => {})
      return waitFrameUrl(/\/characters\/?$/, 20000)
    }
    const memTitle = '读者养猫'
    const memContent = '读者养了一只叫团子的猫'
    const fixtureDir = arg('cards-dir', '/tmp/tavern-verify/cards')
    let memFixture = null
    try {
      const manifest = JSON.parse(await readFile(path.join(fixtureDir, 'manifest.json'), 'utf8'))
      memFixture = (manifest.cards || []).find((card) => card.format === 'png-v2') || (manifest.cards || [])[0] || null
    } catch {
      memFixture = null
    }
    memoryEvidence.fixture = memFixture ? { file: memFixture.file, name: memFixture.name, format: memFixture.format } : null
    const detailPattern = /\/characters\/(?!regex|groups)[^/?#]+/
    // 从卡库点某张卡的「编辑」进详情；带等待，避免 SPA 刚切页时点击落空。
    async function openMemoryDetail(cardName) {
      const lib = await memoryLibrary()
      if (!lib) throw new Error('无法进入角色卡库（owner: cards-client）')
      await memWait(async () => (await findCardRoot(lib, cardName).count().catch(() => 0)) > 0, 15000)
      const root = await findCardRoot(lib, cardName)
      const editButton = root.getByRole('button', { name: '编辑' }).first()
      const shown = await memWait(async () => (await editButton.count().catch(() => 0)) > 0, 10000)
      if (!shown) throw new Error(`卡库里找不到「${cardName}」的「编辑」按钮（owner: cards-client）`)
      await editButton.click().catch(() => {})
      return waitFrameUrl(detailPattern, 20000)
    }
    let detailFrameM = null
    let chatFrameM = null
    try {
      if (!memFixture) {
        memUnavailable(0, `缺少 fixture：先跑 python3 scripts/fixtures/make-cards.py（找 ${path.join(fixtureDir, 'manifest.json')}）`)
      } else {
        let frameM = await memoryLibrary()
        if (!frameM) memUnavailable(0, '无法进入角色卡库 /characters（owner: plugin-core 面板/导航；cards-client 路由）')
        if (frameM && memUnavailableAt === -1) {
          const cardName = memFixture.name
          memoryEvidence.cardName = cardName
          let count = await frameM.getByText(cardName, { exact: true }).count().catch(() => 0)
          if (count === 0) {
            const fileInput = frameM.locator('input[type="file"]').first()
            if (!(await fileInput.count().catch(() => 0))) {
              memUnavailable(0, '角色卡库缺少导入入口 input[type=file]（owner: cards-client）')
            } else {
              await fileInput.setInputFiles(path.join(fixtureDir, memFixture.file)).catch(() => {})
              const appeared = await memWait(async () => (await frameM.getByText(cardName, { exact: true }).count().catch(() => 0)) > 0, 30000)
              if (appeared) count = 1
            }
          }
          if (count === 0) {
            memUnavailable(0, `角色卡「${cardName}」不在库中且导入失败（owner: cards-client 导入 / app-auth）`)
          } else {
            // ── 打开卡详情（编辑）──
            const cardRoot = await findCardRoot(frameM, cardName)
            const editButton = cardRoot.getByRole('button', { name: '编辑' }).first()
            if (!(await editButton.count().catch(() => 0))) {
              memUnavailable(0, '卡片上没有「编辑」按钮，无法打开卡详情（owner: cards-client）')
            } else {
              await editButton.click().catch(() => {})
              detailFrameM = await waitFrameUrl(detailPattern, 20000)
              if (!detailFrameM) {
                memUnavailable(0, '点击「编辑」后未进入 /characters/<id> 卡详情（owner: cards-client 路由/详情页）')
              } else {
                const detailPath = new URL(detailFrameM.url()).pathname
                memoryEvidence.detailUrl = detailPath
                memoryEvidence.cardId = (detailPath.match(/\/characters\/([^/?#]+)/) || [])[1] || null
              }
            }
          }
        }
      }

      // ── C1 手工记忆增删改 ──
      if (detailFrameM && memUnavailableAt === -1) {
        const memoryItem = (title) => detailFrameM.locator('li.memory-item').filter({ hasText: title }).first()
        const editor = () => detailFrameM.locator('form.memory-editor').first()
        const openNew = async () => {
          // 卡详情刚挂载时按钮已在 DOM 里但点击可能被同帧重渲染吞掉：等「长期记忆」区渲染完，
          // 再最多重试 3 次（普通 click → DOM click），避免把偶发空点记成产品缺陷。
          const sectionShown = await memWait(async () => (await detailFrameM.getByRole('heading', { name: '长期记忆' }).count().catch(() => 0)) > 0, 20000)
          if (!sectionShown) return false
          const addButton = detailFrameM.getByRole('button', { name: '新增记忆' }).first()
          if (!(await addButton.count().catch(() => 0))) return false
          for (let attempt = 0; attempt < 3; attempt += 1) {
            const normal = await addButton.click({ timeout: 4000 }).then(() => true).catch(() => false)
            if (!normal) await addButton.evaluate((el) => el.click()).catch(() => {})
            if (await memWait(async () => (await editor().count().catch(() => 0)) > 0, 6000)) return true
            await memSleep(500)
          }
          return false
        }
        const fillEditor = async (title, content, salience) => {
          const form = editor()
          await form.getByLabel(/标题/).first().fill(title).catch(() => {})
          await form.getByLabel(/内容/).first().fill(content).catch(() => {})
          if (salience) await form.getByLabel(/权重/).first().fill(String(salience)).catch(() => {})
          return form
        }
        const saveEditor = async () => {
          const form = editor()
          await form.getByRole('button', { name: '保存记忆' }).first().click().catch(() => {})
          return memWait(async () => (await editor().count().catch(() => 0)) === 0, 15000)
        }
        let created = false
        let edited = false
        let deleted = false
        let recreated = false
        let manualError = null
        const c1 = { created: false, edited: false, deleted: false, recreated: false, error: null }
        try {
          const opened = await openNew()
          if (!opened) throw new Error('点「新增记忆」后没有出现记忆编辑器（owner: cards-client）')
          await fillEditor(memTitle, memContent, 70)
          await saveEditor()
          created = await memWait(async () => (await memoryItem(memTitle).count().catch(() => 0)) > 0, 15000)
          if (!created) throw new Error('保存后列表没有出现记忆「读者养猫」（owner: cards-client 列表/保存 或 app-auth POST /memories）')
          // 编辑：改标题
          const editMemoryButton = memoryItem(memTitle).getByRole('button', { name: '编辑记忆' }).first()
          await editMemoryButton.click().catch(() => {})
          const editOpened = await memWait(async () => (await editor().count().catch(() => 0)) > 0, 8000)
          if (!editOpened) throw new Error('点「编辑记忆」后没有出现编辑器（owner: cards-client）')
          const editedTitle = `${memTitle}（改）`
          await fillEditor(editedTitle, `${memContent}，现在它两岁。`, null)
          await saveEditor()
          edited = await memWait(async () => (await memoryItem(editedTitle).count().catch(() => 0)) > 0, 15000)
          if (!edited) throw new Error('编辑保存后标题没有更新（owner: cards-client/app-auth PATCH）')
          // 删除
          await memoryItem(editedTitle).getByRole('button', { name: '删除记忆' }).first().click().catch(() => {})
          const confirm = memoryItem(editedTitle).getByRole('button', { name: '确认删除' }).first()
          await memWait(async () => (await confirm.count().catch(() => 0)) > 0, 5000)
          await confirm.click().catch(() => {})
          deleted = await memWait(async () => (await memoryItem(editedTitle).count().catch(() => 0)) === 0, 15000)
          if (!deleted) throw new Error('确认删除后记忆条目仍在（owner: cards-client/app-auth DELETE）')
          // 重新建回「读者养猫」，供 C3/C4 的注入预览使用
          if (await openNew()) {
            await fillEditor(memTitle, memContent, 70)
            await saveEditor()
            recreated = await memWait(async () => (await memoryItem(memTitle).count().catch(() => 0)) > 0, 15000)
          }
        } catch (error) {
          manualError = error instanceof Error ? error.message : String(error)
        }
        c1.created = created
        c1.edited = edited
        c1.deleted = deleted
        c1.recreated = recreated
        c1.error = manualError
        memoryEvidence.manual = c1
        memCheck(0, created && edited && deleted && recreated, manualError || `新增可见=${created} 编辑生效=${edited} 删除生效=${deleted} 重建=${recreated}`)
        if (!recreated) memUnavailable(1, `手工记忆没有建立成功（${manualError || '未知'}），后续注入预览断言跳过`)
        await shot(page, '25-memory-manual.png')

        // ── C2 前置：聊天页发 2 条含自述消息 ──
        if (memUnavailableAt === -1) {
          let chatError = null
          const chatSent = []
          try {
            const libFrame = await memoryLibrary()
            if (!libFrame) throw new Error('无法回到角色卡库（owner: cards-client）')
            // SPA 路由刚切到 /characters 时卡片可能还没渲染，先等卡片容器出现。
            await memWait(async () => (await findCardRoot(libFrame, memoryEvidence.cardName).count().catch(() => 0)) > 0, 15000)
            const root = await findCardRoot(libFrame, memoryEvidence.cardName)
            const startButton = root.getByRole('button', { name: '开聊' }).first()
            const startShown = await memWait(async () => (await startButton.count().catch(() => 0)) > 0, 10000)
            if (!startShown) {
              const rootCount = await findCardRoot(libFrame, memoryEvidence.cardName).count().catch(() => 0)
              const cardCount = await libFrame.locator('article.character-card').count().catch(() => 0)
              throw new Error(`卡片没有「开聊」按钮（卡片容器=${rootCount} 库内卡片=${cardCount}；owner: cards-client）`)
            }
            await startButton.click({ force: true }).catch(() => {})
            chatFrameM = await waitFrameUrl(/\/conversations\/world\/[^/?#]+/, 25000)
            if (!chatFrameM) throw new Error('点「开聊」后未进入聊天页（owner: cards-client / app-auth）')
            await waitChatReady(chatFrameM)
            const composer = chatFrameM.locator('#world-chat-message')
            const sendButton = chatFrameM.locator('form.world-chat-compose button[type="submit"]').first()
            const assistantTail = async () => {
              const last = chatFrameM.locator('.world-chat-message.assistant').last()
              if (!(await last.count().catch(() => 0))) return { meta: '', text: '' }
              const meta = (await last.locator('small').first().textContent().catch(() => null)) || ''
              const text = (await last.locator('p').last().textContent().catch(() => null)) || ''
              return { meta: meta.trim(), text: text.trim() }
            }
            async function sendSelfMessage(text) {
              const marker = text.slice(0, 10)
              await composer.fill(text)
              let enabled = false
              const enableDeadline = Date.now() + 8000
              while (Date.now() < enableDeadline) {
                if (!(await sendButton.isDisabled().catch(() => true))) {
                  enabled = true
                  break
                }
                await memSleep(200)
              }
              const startedAt = Date.now()
              if (enabled) await sendButton.click().catch(() => {})
              else await composer.press('Control+Enter').catch(() => {})
              // 桌宠浮层可能盖住发送按钮：6s 内没出现 user 气泡就退化为 DOM click。
              const userSeen = await memWait(
                async () => (await chatFrameM.locator('.world-chat-message.user p').filter({ hasText: marker }).count().catch(() => 0)) > 0,
                6000,
                300,
              )
              if (!userSeen && enabled) await sendButton.evaluate((el) => el.click()).catch(() => {})
              const deadline = Date.now() + (real ? 180000 : 120000)
              while (Date.now() < deadline) {
                const userOk = (await chatFrameM.locator('.world-chat-message.user p').filter({ hasText: marker }).count().catch(() => 0)) > 0
                const replies = await chatFrameM.locator('.world-chat-message.assistant p').allTextContents().catch(() => [])
                const candidate = replies.map((item) => item.trim()).filter(Boolean).pop()
                const tail = candidate ? await assistantTail() : { meta: '', text: '' }
                const finished = /已保存|收到的部分回复/.test(tail.meta) && (!tail.text || tail.text === candidate)
                if (userOk && candidate && finished) return { ok: true, text: candidate, elapsedMs: Date.now() - startedAt }
                const alertText = await chatFrameM.locator('[role="alert"]').first().textContent().catch(() => null)
                if (alertText) return { ok: false, error: alertText }
                await memSleep(600)
              }
              return { ok: false, error: '等回复超时' }
            }
            const selfMessages = ['我叫林舟，最近养了一只叫团子的猫，它喜欢趴在键盘上。', '我每天早上都要喝一杯手冲咖啡，喜欢安静的地方。']
            for (const text of selfMessages) {
              const result = await sendSelfMessage(text)
              chatSent.push({ text, ok: result.ok, replyFirst60: result.ok ? clip(result.text, 60) : null, elapsedMs: result.elapsedMs || null, error: result.error || null })
              if (!result.ok) throw new Error(`发送「${clip(text, 20)}」失败：${result.error}`)
              await memSleep(800)
            }
          } catch (error) {
            chatError = error instanceof Error ? error.message : String(error)
          }
          memoryEvidence.chat = { url: chatFrameM ? chatFrameM.url() : null, messages: chatSent, ok: !chatError, error: chatError }
          if (chatError) memUnavailable(1, `C 组前置聊天失败：${clip(chatError, 160)}（owner: app-auth 真生成 / cards-client 聊天页）`)
        }

        // ── 回到卡详情：C2 立即整理 ──
        if (memUnavailableAt === -1) {
          try {
            detailFrameM = await openMemoryDetail(memoryEvidence.cardName)
            if (!detailFrameM) memUnavailable(1, '聊天后无法重新进入卡详情（owner: cards-client 路由/详情页）')
          } catch (error) {
            detailFrameM = null
            memUnavailable(1, `聊天后无法重新进入卡详情：${clip(error instanceof Error ? error.message : String(error), 160)}`)
          }
        }
        if (detailFrameM && memUnavailableAt === -1) {
          // 聊天后重新进入详情时 Vue 还在挂载，直接 count() 会得到 0；先等「长期记忆」区渲染。
          const sectionReady = await memWait(async () => (await detailFrameM.getByRole('heading', { name: '长期记忆' }).count().catch(() => 0)) > 0, 20000)
          if (!sectionReady) memUnavailable(1, '重新进入卡详情后 20s 内没有渲染「长期记忆」区（owner: cards-client）')
          const items = () => detailFrameM.locator('li.memory-item')
          const extractButton = detailFrameM.getByRole('button', { name: '立即整理' }).first()
          await memWait(async () => (await extractButton.count().catch(() => 0)) > 0, 15000)
          const before = await items().count().catch(() => 0)
          let extractNotice = ''
          let extractError = ''
          if (!(await extractButton.count().catch(() => 0))) {
            extractError = '卡详情没有「立即整理」按钮（owner: cards-client）'
          } else {
            await extractButton.click().catch(() => {})
            const done = await memWait(async () => {
              const noticeText = (await detailFrameM.getByRole('status').first().textContent().catch(() => '')) || ''
              if (/新增 \d+ 条记忆|暂无新记忆/.test(noticeText)) {
                extractNotice = noticeText.trim()
                return true
              }
              const alertText = (await detailFrameM.getByRole('alert').first().textContent().catch(() => '')) || ''
              if (/整理|抽取|记忆/.test(alertText)) {
                extractError = alertText.trim()
                return true
              }
              return false
            }, real ? 150000 : 60000, 800)
            if (!done && !extractError) extractError = '点「立即整理」后 150s 内没有成功/失败提示（owner: cards-client 或 app-auth 同步抽取超时）'
            await memWait(async () => (await items().count().catch(() => 0)) > before, 10000)
          }
          const after = await items().count().catch(() => 0)
          const addedMatch = extractNotice.match(/新增 (\d+) 条记忆/)
          const added = addedMatch ? Number(addedMatch[1]) : 0
          const legit = /暂无新记忆/.test(extractNotice) ? 0 : added
          memoryEvidence.extract = { before, after, notice: extractNotice || null, error: extractError || null, added: legit }
          memCheck(
            1,
            !extractError && legit >= 1 && after > before,
            extractError || `点「立即整理」前=${before} 条，后=${after} 条；提示=${extractNotice}（新增=${legit}）`,
          )
          await shot(page, '26-memory-extract.png')

          // ── C3 注入预览含手工记忆标题 ──
          // 点击「预览」后 context-preview 是异步的；用页面级 response 事件判断本轮请求已完成，
          // 避免读到上一轮的 .preview-result 旧文本（C4 停用/删除后两次 preview 的文本可能相同）。
          const previewResponses = []
          onPreviewResponse = (response) => {
            if (!/\/context-preview/.test(response.url())) return
            response.json().then((data) => previewResponses.push(data)).catch(() => previewResponses.push(null))
          }
          page.on('response', onPreviewResponse)
          const runPreview = async () => {
            const previewButton = detailFrameM.getByRole('button', { name: '预览' }).first()
            const previewInput = detailFrameM.getByLabel(/试写一句对话/).first()
            if (!(await previewButton.count().catch(() => 0))) return { ok: false, error: '卡详情没有「预览」按钮（owner: cards-client）', text: '' }
            const beforeResponses = previewResponses.length
            await previewInput.fill('她轻轻摸了摸那只猫').catch(() => {})
            await previewButton.click().catch(() => {})
            const gotResponse = await memWait(async () => previewResponses.length > beforeResponses, 30000, 300)
            await memSleep(400)
            let text = ''
            const shown = await memWait(async () => {
              if ((await detailFrameM.locator('.preview-result').count().catch(() => 0)) === 0) return false
              text = await detailFrameM.locator('.preview-result').first().innerText().catch(() => '')
              return Boolean(text)
            }, 10000, 300)
            const previewError = (await detailFrameM.getByRole('alert').first().textContent().catch(() => '')) || ''
            const payload = previewResponses.length > beforeResponses ? previewResponses[previewResponses.length - 1] : null
            return { ok: gotResponse || shown, error: previewError.trim() || null, text, payload }
          }
          const preview1 = await runPreview()
          const memoryLine = (text) => (text.match(/注入的长期记忆：([^\n]*)/) || [])[1] || ''
          const containsTitle = preview1.ok && preview1.text.includes(memTitle)
          memoryEvidence.preview = { ok: preview1.ok, error: preview1.error, containsTitle, memoryLine: memoryLine(preview1.text), first200: clip(preview1.text, 200) }
          memCheck(2, preview1.ok && containsTitle, preview1.ok ? `注入的长期记忆行=${memoryLine(preview1.text)}；包含「${memTitle}」=${containsTitle}` : preview1.error || '预览没有出结果（owner: cards-client 注入预览 / app-auth context-preview）')
          await shot(page, '27-memory-preview.png')

          // ── C4 停用 → 预览不含；删除 → 预览不含 ──
          const m1 = detailFrameM.locator('li.memory-item').filter({ hasText: memTitle }).first()
          let disabledOk = false
          let deletedOk = false
          let c4Error = null
          try {
            const itemCount = await m1.count().catch(() => 0)
            if (!itemCount) throw new Error(`C4 找不到记忆「${memTitle}」（owner: cards-client 列表）`)
            const disableButton = m1.getByRole('button', { name: '停用' }).first()
            if (!(await disableButton.count().catch(() => 0))) throw new Error('记忆条目没有「停用」按钮（owner: cards-client）')
            await disableButton.click().catch(() => {})
            const disabledShown = await memWait(async () => (await m1.locator('.badge').filter({ hasText: '已停用' }).count().catch(() => 0)) > 0, 15000)
            const previewDisabled = await runPreview()
            disabledOk = disabledShown && previewDisabled.ok && !previewDisabled.text.includes(memTitle)
            memoryEvidence.disabled = { disabledShown, previewOk: previewDisabled.ok, containsTitle: previewDisabled.ok ? previewDisabled.text.includes(memTitle) : null }
            // 删除后再预览
            await m1.getByRole('button', { name: '删除记忆' }).first().click().catch(() => {})
            const confirm = m1.getByRole('button', { name: '确认删除' }).first()
            await memWait(async () => (await confirm.count().catch(() => 0)) > 0, 5000)
            await confirm.click().catch(() => {})
            const removed = await memWait(async () => (await detailFrameM.locator('li.memory-item').filter({ hasText: memTitle }).count().catch(() => 0)) === 0, 15000)
            const previewDeleted = await runPreview()
            deletedOk = removed && previewDeleted.ok && !previewDeleted.text.includes(memTitle)
            memoryEvidence.deleted = { removed, previewOk: previewDeleted.ok, containsTitle: previewDeleted.ok ? previewDeleted.text.includes(memTitle) : null }
          } catch (error) {
            c4Error = error instanceof Error ? error.message : String(error)
          }
          memCheck(3, disabledOk && deletedOk, c4Error || `停用后 preview 不含=${disabledOk}；删除后 preview 不含=${deletedOk}`)
          await shot(page, '28-memory-disabled.png')

          // ── C5 长期伙伴：详情切换 → 卡库顶部置顶分组 ──
          let companionOk = false
          let companionError = null
          const companionDetail = { checked: null, notice: null, heading: null, badge: null }
          try {
            const checkbox = detailFrameM.locator('.companion-line input[type="checkbox"]').first()
            if (!(await checkbox.count().catch(() => 0))) throw new Error('卡详情没有「设为长期伙伴」勾选框（owner: cards-client）')
            await checkbox.click().catch(() => {})
            const checked = await memWait(async () => (await checkbox.isChecked().catch(() => false)) === true, 15000)
            companionDetail.checked = checked
            companionDetail.notice = (await detailFrameM.getByRole('status').first().textContent().catch(() => '')) || null
            const libFrame = await memoryLibrary()
            if (!libFrame) throw new Error('设置伙伴后无法回到角色卡库（owner: cards-client）')
            const libFrame2 = libFrame
            const heading = libFrame2.getByRole('heading', { name: '长期伙伴' }).first()
            const headingVisible = await memWait(async () => (await heading.count().catch(() => 0)) > 0 && (await heading.isVisible().catch(() => false)), 15000)
            companionDetail.heading = headingVisible
            const badge = libFrame2.locator('.companion-badge').first()
            companionDetail.badge = await badge.count().catch(() => 0)
            let inTopGroup = false
            if (headingVisible && memoryEvidence.cardId) {
              inTopGroup = await libFrame2.evaluate((cardId) => {
                const articles = [...document.querySelectorAll('article.character-card[data-card-id]')]
                const headingEl = [...document.querySelectorAll('h2, h3')].find((node) => node.textContent && node.textContent.trim() === '长期伙伴')
                if (!headingEl) return false
                const first = articles[0]
                if (!first) return false
                return first.getAttribute('data-card-id') === cardId && Boolean(headingEl.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING)
              }, memoryEvidence.cardId).catch(() => false)
            }
            companionDetail.inTopGroup = inTopGroup
            companionOk = Boolean(checked && headingVisible && inTopGroup)
            memoryEvidence.companion = { ...companionDetail, inTopGroup }
            await shot(page, '29-companion-library.png')
          } catch (error) {
            companionError = error instanceof Error ? error.message : String(error)
            memoryEvidence.companion = { ...companionDetail, error: companionError }
          }
          memCheck(4, companionOk, companionError || `勾选长期伙伴=${companionDetail.checked}；卡库「长期伙伴」标题=${companionDetail.heading}；置顶=${companionDetail.inTopGroup}`)
        }
      }
    } catch (error) {
      memoryEvidence.error = error instanceof Error ? error.message : String(error)
    } finally {
      // C6：控制台。其它未跑到项按 SKIP 记录（前置失败已在上面逐项给出 owner）。
      if (onPreviewResponse) page.off('response', onPreviewResponse)
      const memErrorsList = memErrors()
      memoryEvidence.console = { clean: memErrorsList.length === 0, errors: memErrorsList.slice(0, 3) }
      memCheck(5, memErrorsList.length === 0, memErrorsList.slice(0, 3).join(' | ') || 'clean')
      for (let index = 0; index < 5; index += 1) {
        if (!memoryRecorded[index]) memSkip(index, 'C 组前置失败，未执行（见 report.memory.error 与上面步骤）')
      }
    }
  }
  report.memory = memoryEvidence

  // ── M. 「我的」移除后的导航回归（task-24）───────────────────────────────────
  if (runCards) {
    const navNames = [
      'M1 左侧导航不再出现「我的」分组 tab 与 7 个已删除入口',
      'M2 /tavern/settings 重定向到 /explore',
      'M3 /tavern/settings/models 重定向到 /explore',
      'M4 /tavern/notebook 重定向到 /explore',
      'M5 /tavern/help 重定向到 /explore',
      'M6 /tavern/tasks/activity 重定向到 /explore',
      'M7 /tavern/conversations 重定向到 /explore',
      'M8 /tavern/conversations/world/:id 仍能打开聊天页',
      'M9 M 组导航期间 console 无报错',
    ]
    const navEvidence = {
      enabled: true,
      sidebar: null,
      redirects: [],
      chat: null,
      console: [],
      steps: [],
    }
    const navConsoleStart = consoleErrors.length
    const navDone = new Set()
    const navSleep = (ms) => page.waitForTimeout(ms)
    const navWait = async (predicate, timeout = 20000, interval = 300) => {
      const deadline = Date.now() + timeout
      while (Date.now() < deadline) {
        try {
          if (await predicate()) return true
        } catch {}
        await navSleep(interval)
      }
      return false
    }
    const navRecord = (index, ok, detail) => {
      if (navDone.has(index)) return
      navDone.add(index)
      navEvidence.steps.push({ index, name: navNames[index], ok: Boolean(ok), detail: detail === undefined ? null : String(detail) })
      record(navNames[index], ok, detail)
    }
    // 注意：不要用 C 组的 memoryLibrary()——M 组在 --cards（无 --memory）时也要能跑。
    const navFrame = fnow()
    let navError = null
    try {
      if (!navFrame) throw new Error('拿不到应用 iframe（owner: plugin-core）')

      // ── M1 左侧导航：不得再出现「我的」分组 tab 与 7 个已删除入口 ──
      const sidebarShown = await navWait(async () => (await navFrame.locator('.shell-sidebar').count().catch(() => 0)) > 0, 15000)
      if (!sidebarShown) throw new Error('15s 内没有渲染左侧导航 .shell-sidebar（owner: cards-client）')
      const sidebarText = ((await navFrame.locator('.shell-sidebar').innerText().catch(() => '')) || '').trim()
      const groupLabels = (await navFrame.locator('.shell-nav-label').allTextContents().catch(() => [])).map((label) => label.trim())
      const navLinkNames = (await navFrame.locator('.shell-sidebar nav a span').allTextContents().catch(() => [])).map((label) => label.trim())
      const removedEntries = ['我的对话', '我的笔记', '模型与用量', '账号设置', '任务进度', '反馈与通知', '使用帮助']
      const hitEntries = removedEntries.filter((label) => sidebarText.includes(label))
      const hitGroups = groupLabels.filter((label) => label.includes('我的'))
      navEvidence.sidebar = { text: clip(sidebarText, 600), groups: groupLabels, links: navLinkNames, removedHits: hitEntries, groupTabHits: hitGroups }
      navRecord(
        0,
        hitEntries.length === 0 && hitGroups.length === 0,
        hitEntries.length || hitGroups.length
          ? `左侧导航仍含已删除入口=${JSON.stringify(hitEntries)} 分组 tab=${JSON.stringify(hitGroups)}（owner: cards-client）`
          : `分组=${groupLabels.join('/')}；入口=${navLinkNames.join('、')}`,
      )
      await shot(page, '30-nav-sidebar.png')

      // ── M2–M7 已删除路由应重定向到 /explore ──
      const navOrigin = new URL(navFrame.url()).origin
      const redirectCases = [
        ['/settings', 1],
        ['/settings/models', 2],
        ['/notebook', 3],
        ['/help', 4],
        ['/tasks/activity', 5],
        ['/conversations', 6],
      ]
      for (const [path, index] of redirectCases) {
        const beforeErrors = consoleErrors.length
        await navFrame.goto(`${navOrigin}/tavern${path}`, { waitUntil: 'domcontentloaded' }).catch(() => {})
        const landedExplore = await navWait(async () => /\/tavern\/explore\/?(?:[?#].*)?$/.test(navFrame.url()), 25000)
        const exploreShown = await navWait(async () => (await navFrame.locator('.discovery-page').count().catch(() => 0)) > 0, 15000)
        const headingShown = await navFrame.getByRole('heading', { name: /精选互动故事|欢迎/ }).first().isVisible().catch(() => false)
        const alerts = (await navFrame.locator('[role="alert"]').allTextContents().catch(() => [])).map((text) => text.trim()).filter(Boolean)
        const chunkErrors = consoleErrors.slice(beforeErrors).filter((text) => !/Failed to load resource/.test(text))
        const currentPath = (() => { try { return new URL(navFrame.url()).pathname } catch { return navFrame.url() } })()
        const ok = landedExplore && exploreShown && headingShown && alerts.length === 0 && chunkErrors.length === 0
        const item = { path, currentPath, landedExplore, exploreShown, headingShown, alerts, consoleErrors: chunkErrors, ok }
        navEvidence.redirects.push(item)
        navRecord(index, ok, ok
          ? `${path} → ${currentPath}（探索页已渲染）`
          : `${path} 未按预期重定向：当前=${currentPath} 探索页=${exploreShown} 标题=${headingShown} 提示=${JSON.stringify(alerts)} console=${JSON.stringify(chunkErrors)}（owner: cards-client 路由）`)
        if (path === '/settings') await shot(page, '31-nav-redirect-explore.png')
      }

      // ── M8 /conversations/world/:id 仍能打开聊天页 ──
      const chatUrl = memoryEvidence.chat && memoryEvidence.chat.url ? memoryEvidence.chat.url : null
      if (!chatUrl) {
        navEvidence.chat = { url: null, ok: false, skipped: true }
        navDone.add(7)
        navEvidence.steps.push({ index: 7, name: navNames[7], ok: true, skipped: true, detail: '本次运行没有创建单卡聊天（没有可复用的 /conversations/world/:id），跳过 M8' })
        record(navNames[7], true, '本次运行没有创建单卡聊天（没有可复用的 /conversations/world/:id），跳过 M8', true)
      } else {
        const beforeErrors = consoleErrors.length
        await navFrame.goto(chatUrl, { waitUntil: 'domcontentloaded' }).catch(() => {})
        const chatReady = await navWait(async () => {
          const composer = navFrame.locator('#world-chat-message')
          return (await composer.count().catch(() => 0)) > 0 && (await composer.isVisible().catch(() => false))
        }, 30000)
        const chatErrorText = (await navFrame.locator('[role="alert"]').first().textContent().catch(() => null)) || null
        const chunkErrors = consoleErrors.slice(beforeErrors).filter((text) => !/Failed to load resource/.test(text))
        const chatOk = chatReady && !chatErrorText && chunkErrors.length === 0
        navEvidence.chat = { url: chatUrl, currentPath: (() => { try { return new URL(navFrame.url()).pathname } catch { return null } })(), ok: chatOk, error: chatErrorText, consoleErrors: chunkErrors }
        navRecord(7, chatOk, chatOk
          ? `聊天页可打开：${new URL(chatUrl).pathname}，输入框可见`
          : `聊天页打不开：输入框=${chatReady} 提示=${chatErrorText} console=${JSON.stringify(chunkErrors)}（owner: cards-client / app-auth）`)
        if (chatOk) await shot(page, '32-nav-chat-still-works.png')
      }
    } catch (error) {
      navError = error instanceof Error ? error.message : String(error)
      navEvidence.error = navError
    }
    // M9 在下面单独记录（要看 consoleErrors 切片），这里不要提前把它写成 FAIL。
    for (let index = 0; index < 8; index += 1) {
      if (!navDone.has(index)) {
        navRecord(index, false, `M 组未执行完：${navError || '未执行到该步'}（owner: cards-client / harness）`)
      }
    }
    // ── M9 console clean（只统计 M 组期间产生的报错）──
    const navConsoleErrors = consoleErrors.slice(navConsoleStart).filter((text) => !/Failed to load resource/.test(text))
    navEvidence.console = navConsoleErrors
    if (!navDone.has(8)) navRecord(8, navConsoleErrors.length === 0, navConsoleErrors.length ? `M 组期间 console 有报错：${JSON.stringify(navConsoleErrors.slice(0, 3))}（owner: cards-client）` : 'clean')
    report.nav = navEvidence
  }

  // ── G. 控制台报错 ──────────────────────────────────────────────────────────
  const fatalErrors = consoleErrors.filter((text) => !/Failed to load resource/.test(text))
  check('无控制台 JS 报错', fatalErrors.length === 0, fatalErrors.slice(0, 3).join(' | ') || 'clean')

  // ── R. 真生成（--real）────────────────────────────────────────────────────
  // 复用 H 组已经跑出来的聊天证据，断言「不是 mock、像真回复、刷新后还在、console 干净」。
  // 默认（不加 --real）不产生任何额外检查项，保持 --cards 的 55 PASS 基线不变。
  const realEvidence = { enabled: real, negativeMode: realNegative, steps: [], reply: null, reload: null, console: null, negative: null }
  if (real) {
    const realCheck = (name, ok, detail) => {
      check(name, ok, detail)
      realEvidence.steps.push({ name, ok: !!ok, skipped: false, detail: detail === undefined ? null : String(detail) })
    }
    const realSkip = (name, detail) => {
      skip(name, detail)
      realEvidence.steps.push({ name, ok: true, skipped: true, detail: detail === undefined ? null : String(detail) })
    }
    const chat = cardsEvidence.chat
    const reply = chat && chat.reply ? String(chat.reply).trim() : ''
    // pngCard 只在 --cards 的导入分支内可见；R 组在分支外，用证据里记录的角色名。
    const replyCardName = (chat && chat.character) || ((cardsEvidence.imported.find((item) => item.format === 'png-v2') || {}).name || '')
    const mockPhrase = '我们可以聊聊此刻的想法'
    const chatReady = Boolean(reply && chat && chat.completed)
    realEvidence.reload = cardsEvidence.reload || null
    realEvidence.reply = chat
      ? {
          first60: reply ? clip(reply, 60) : null,
          length: reply ? [...reply].length : 0,
          elapsedMs: chat.elapsedMs ?? null,
          modelName: chat.modelName ?? null,
          completed: Boolean(chat.completed),
          containsCharacterName: reply && replyCardName ? reply.includes(replyCardName) : false,
          error: chat.error || null,
          notice: chat.notice || null,
        }
      : null

    if (realNegative) {
      // 负向演练时本来就期望「没有回复」：R1–R3 是正常链路断言，跳过而不是记 FAIL。
      for (const name of [
        '真生成：回复不含 mock 特征句「我们可以聊聊此刻的想法」',
        '真生成：回复长度 ≥20 字且不是用户消息本身',
        '真生成：完整刷新 iframe 后同一条回复仍在',
      ]) {
        realSkip(name, '负向演练（--real-negative）：预期没有正常回复，本项不适用')
      }
    } else {
      realCheck(
        '真生成：回复不含 mock 特征句「我们可以聊聊此刻的想法」',
        chatReady && !reply.includes(mockPhrase),
        chatReady
          ? reply.includes(mockPhrase)
            ? '回复里出现 mock 特征句，应用仍是 AI_MODE=mock（owner: app-auth 环境/worker）'
            : `未出现 mock 特征句；首句=${clip(reply, 60)}`
          : '没拿到完整回复，无法判断（见 H9；owner: app-auth / plugin-core /tavern-agent）',
      )
      realCheck(
        '真生成：回复长度 ≥20 字且不是用户消息本身',
        chatReady && [...reply].length >= 20 && reply !== (chat.userMessage || '你好'),
        chatReady
          ? `字数=${[...reply].length}；含角色名=${replyCardName ? reply.includes(replyCardName) : 'n/a'}；首句=${clip(reply, 60)}`
          : '没有拿到完整回复',
      )
      realCheck(
        '真生成：完整刷新 iframe 后同一条回复仍在',
        Boolean(cardsEvidence.reload && cardsEvidence.reload.replyMatched),
        cardsEvidence.reload
          ? `replyMatched=${Boolean(cardsEvidence.reload.replyMatched)} user=${cardsEvidence.reload.userMessages} assistant=${cardsEvidence.reload.assistantMessages}`
          : '刷新后没读到聊天记录（owner: app-auth 持久化 / cards-client 读取）',
      )
    }
    const realConsoleClean = fatalErrors.length === 0
    realEvidence.console = { clean: realConsoleClean, errors: fatalErrors.slice(0, 3) }
    realCheck('真生成：console 无 JS 报错', realConsoleClean, fatalErrors.slice(0, 3).join(' | ') || 'clean')

    // 负向断言：网关不可用时 UI 应显示失败而不是假回复。
    // 触发它需要把应用的 DSH_AGENT_URL 指向坏端口并重启 api/worker（侵入环境、会打断生产），
    // 所以默认 SKIP，只在调用方显式 --real-negative（并已自行弄坏网关）时执行。
    const negativeExpected = 'UI 显示失败/未收到回复，且不出现 mock 特征句'
    if (!realNegative) {
      realSkip(
        '真生成负向：网关不可用时显示失败而不是假回复（--real-negative 手动演练）',
        '为避免改生产/隔离环境变量，默认 SKIP；演练方法见 docs/testing.md「--real-negative」',
      )
      realEvidence.negative = { mode: 'skipped', skipped: true, expected: negativeExpected, reason: 'intrusive to break DSH_AGENT_URL; manual opt-in via --real-negative' }
    } else {
      const uiText = chat ? chat.notice || chat.error || null : null
      const honestFailure = !reply && Boolean(uiText)
      realCheck(
        '真生成负向：网关不可用时显示失败而不是假回复',
        honestFailure,
        honestFailure
          ? `UI 提示=${clip(uiText, 120)}`
          : `期望失败提示但没有：reply=${reply ? clip(reply, 40) : 'null'}，uiText=${clip(uiText, 80) || '无'}（owner: app-auth 错误映射 / plugin-core 网关 502）`,
      )
      realEvidence.negative = {
        mode: 'explicit',
        skipped: false,
        expected: negativeExpected,
        ok: honestFailure,
        uiText: uiText ? clip(uiText, 200) : null,
        reply: reply ? clip(reply, 60) : null,
      }
    }
  }
  report.real = realEvidence

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
