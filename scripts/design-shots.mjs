/**
 * 设计 A3 实机截图与断言（桌面 1440 / 移动 390）。
 *
 * 复用已登录的浏览器，因此不需要 token/cookie：
 *   - WebPilot 在跑：自动从进程参数里找到它的 CDP 端口并连上去（只开自己的标签页，绝不关闭它）；
 *   - WebPilot 没跑：用同一个登录 profile（默认 ~/.dsh/webpilot/profile）自起无头浏览器，截完即关。
 *
 *   node scripts/design-shots.mjs --out docs/screenshots/design-a3
 *   node scripts/design-shots.mjs --cdp http://127.0.0.1:39561 --base http://127.0.0.1:3080/tavern
 *   node scripts/design-shots.mjs --profile ~/.dsh/webpilot/profile --out /tmp/a3
 *
 * 会做一次「导入酒馆聊天 JSONL」来获得带动作/跨天消息的聊天，结束后尝试删除该聊天。
 * 只在已经登录的生产实例上读取与截图；除导入的那条测试聊天外不改任何数据。
 */
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { homedir } from 'node:os'
import path from 'node:path'

// playwright 装在兄弟仓库 hermes-site/tools 下（本仓库不带 node_modules）：
// 路径运行时推导，可用 DSH_TAVERN_PLAYWRIGHT_MODULES 覆盖。
const HERMES_TOOLS =
  process.env.DSH_TAVERN_PLAYWRIGHT_MODULES ||
  path.join(homedir(), 'workspace', 'hermes-site', 'tools', 'node_modules')
const require = createRequire(HERMES_TOOLS + '/')
const { chromium } = require('playwright')

const args = process.argv.slice(2)
function arg(name, fallback) {
  const index = args.indexOf(`--${name}`)
  if (index === -1) return fallback
  const value = args[index + 1]
  return value === undefined || value.startsWith('--') ? fallback : value
}

/** WebPilot 每次重启会换调试端口：从浏览器进程参数里找，找不到再用默认值。 */
async function discoverCdp() {
  const { readdirSync, readFileSync } = await import('node:fs')
  for (const pid of readdirSync('/proc').filter((name) => /^\d+$/.test(name))) {
    try {
      const cmd = readFileSync(`/proc/${pid}/cmdline`, 'utf8')
      if (!cmd.includes('webpilot/profile') || !cmd.includes('remote-debugging-port')) continue
      const matched = cmd.match(/--remote-debugging-port=(\d+)/)
      if (matched) return `http://127.0.0.1:${matched[1]}`
    } catch {
      /* 进程可能在扫描时退出 */
    }
  }
  return 'http://127.0.0.1:39561'
}

const cdp = arg('cdp', process.env.DSH_CDP_URL || (await discoverCdp()))
const profile = arg('profile', process.env.DSH_TAVERN_PROFILE || path.join(homedir(), '.dsh', 'webpilot', 'profile'))
const base = arg('base', 'http://127.0.0.1:3080/tavern').replace(/\/$/, '')
const outDir = path.resolve(arg('out', 'docs/screenshots/design-a3'))
const fixture = arg('fixture', '/tmp/dsh-tavern-native/cards/chat-a2.jsonl')

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok: Boolean(ok), detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`)
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(outDir, name) })
}

async function dismissGuide(page) {
  for (const label of ['跳过', '开始使用', '知道了']) {
    const button = page.getByRole('button', { name: label }).first()
    if (await button.count().catch(() => 0)) {
      await button.click().catch(() => {})
      await page.waitForTimeout(200)
    }
  }
}

/** 用哪一份 Chromium：WebPilot 用的是缓存里的 chromium-*，取最新一个即可。 */
async function resolveChrome() {
  const { readdirSync, existsSync } = await import('node:fs')
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(homedir(), '.cache', 'ms-playwright')
  const versions = readdirSync(root)
    .filter((name) => /^chromium-\d+$/.test(name))
    .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]))
  for (const name of versions) {
    const executable = `${root}/${name}/chrome-linux64/chrome`
    if (existsSync(executable)) return executable
  }
  return undefined
}

let context
let owned = false
try {
  const browser = await chromium.connectOverCDP(cdp)
  context = browser.contexts()[0]
  if (!context) throw new Error('CDP 里没有浏览器上下文')
} catch (error) {
  // WebPilot 没开：用同一个登录 profile 自起无头浏览器，截完即关（不碰用户的面板）。
  console.log(`INFO 连不上 ${cdp}（${error.message.split('\n')[0]}），改用自己启动的无头浏览器`)
  context = await chromium.launchPersistentContext(profile, {
    headless: true,
    executablePath: await resolveChrome(),
    args: ['--no-sandbox'],
    viewport: { width: 1440, height: 900 },
  })
  owned = true
}
// 用独立标签页，别去改 WebPilot 正在展示（watch）的那一页。
const page = await context.newPage()
const consoleErrors = []
function watchConsole(target) {
  target.on('pageerror', (error) => consoleErrors.push(String(error && error.message ? error.message : error)))
  target.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
}
watchConsole(page)

let chatId = ''
try {
  await mkdir(outDir, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  // ── 角色卡 → 详情 ──────────────────────────────────────────────
  console.log(`INFO 打开 ${base}/`)
  await page.goto(`${base}/`, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await page.locator('.tavern-card').first().waitFor({ state: 'visible', timeout: 30000 })
  await dismissGuide(page)
  const detailButton = page.locator('[aria-label^="查看 "][aria-label$=" 详情"]').first()
  await detailButton.click()
  await page.locator('.character-hero').first().waitFor({ state: 'visible', timeout: 20000 })
  const cardUrl = page.url()
  await page.waitForTimeout(250)
  await shot(page, 'a3-detail-1440.png')

  await page.locator('.section-action').first().scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(200)
  await shot(page, 'a3-detail-worldbook-1440.png')
  const worldButton = await page
    .evaluate(() => {
      const button = document.querySelector('.section-action')
      if (!button) return null
      const style = getComputedStyle(button)
      return {
        text: (button.textContent || '').trim(),
        background: style.backgroundColor,
        borderWidth: style.borderTopWidth,
        color: style.color,
        fontSize: style.fontSize,
      }
    })
    .catch(() => null)
  const transparent = (value) => !value || /rgba\(0, 0, 0, 0\)|transparent/.test(value)
  check(
    '详情页「管理世界书」是次要按钮（无底色、无边框）',
    Boolean(worldButton && transparent(worldButton.background) && worldButton.borderWidth === '0px'),
    worldButton ? `text=${worldButton.text} bg=${worldButton.background} border=${worldButton.borderWidth}` : '未找到 .section-action',
  )

  // ── 导入带动作/跨天的聊天 fixture，得到聊天页 ────────────────────
  const importInput = page.locator('input[aria-label="导入酒馆聊天 JSONL"]').first()
  await importInput.setInputFiles(fixture)
  await page.waitForURL(/\/chats\/[^/?#]+/, { timeout: 30000 })
  const matched = page.url().match(/\/chats\/([^/?#]+)/)
  chatId = matched ? matched[1] : ''
  await page.locator('.bubble-row').first().waitFor({ state: 'visible', timeout: 30000 })
  await page.waitForTimeout(300)
  await shot(page, 'a3-chat-1440.png')
  await page.locator('.bubble-row.user').first().hover().catch(() => {})
  await page.waitForTimeout(250)
  await shot(page, 'a3-chat-1440-hover.png')
  await page.mouse.move(8, 8).catch(() => {})

  const chatStyle = await page.evaluate(() => {
    const assistant = document.querySelector('.bubble-row.assistant .bubble')
    const act = document.querySelector('.bubble-text .act')
    const more = document.querySelector('.bubble-more')
    const style = assistant ? getComputedStyle(assistant) : null
    const actStyle = act ? getComputedStyle(act) : null
    return {
      borderWidth: style ? style.borderTopWidth : null,
      borderColor: style ? style.borderTopColor : null,
      boxShadow: style ? style.boxShadow : null,
      background: style ? style.backgroundColor : null,
      actFontStyle: actStyle ? actStyle.fontStyle : null,
      actColor: actStyle ? actStyle.color : null,
      moreDisplay: more ? getComputedStyle(more).display : null,
      actionsOpacity: (() => {
        const actions = document.querySelector('.bubble-actions')
        return actions ? getComputedStyle(actions).opacity : null
      })(),
    }
  })
  check(
    '助手气泡：1px --edge 边 + 极轻阴影',
    chatStyle.borderWidth === '1px' && chatStyle.boxShadow !== 'none' && Boolean(chatStyle.borderColor),
    `border=${chatStyle.borderWidth} ${chatStyle.borderColor} shadow=${String(chatStyle.boxShadow).slice(0, 60)}`,
  )
  check(
    '动作文字不再整段斜体（--quiet 色 + normal）',
    chatStyle.actFontStyle === 'normal' && chatStyle.actColor !== 'rgb(43, 38, 34)',
    `fontStyle=${chatStyle.actFontStyle} color=${chatStyle.actColor}`,
  )
  check(
    '桌面保留悬停操作行、不显示「⋯」',
    chatStyle.moreDisplay === 'none' && chatStyle.actionsOpacity === '0',
    `more=${chatStyle.moreDisplay} actionsOpacity=${chatStyle.actionsOpacity}`,
  )

  // ── 移动端 390：操作行收进「⋯」 ────────────────────────────────
  const mobile = await context.newPage()
  try {
    watchConsole(mobile)
    await mobile.setViewportSize({ width: 390, height: 844 })
    await mobile.goto(`${base}/chats/${chatId}`, { waitUntil: 'domcontentloaded' })
    await mobile.locator('.bubble-row').first().waitFor({ state: 'visible', timeout: 30000 })
    await mobile.waitForTimeout(300)
    const before = await mobile.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('.bubble-row'))
      const log = document.querySelector('.chat-log')
      const actions = document.querySelector('.bubble-actions')
      const more = document.querySelector('.bubble-more')
      return {
        rows: rows.length,
        maxRow: rows.length ? Math.max(...rows.map((row) => row.getBoundingClientRect().width)) : 0,
        logWidth: log ? log.getBoundingClientRect().width : 0,
        actionsDisplay: actions ? getComputedStyle(actions).display : null,
        moreVisible: Boolean(more && more.getBoundingClientRect().width > 0),
        morePerRow: rows.map((row) => row.querySelectorAll('.bubble-more').length),
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      }
    })
    await shot(mobile, 'a3-chat-390.png')
    check(
      '移动端：操作行默认收起、每条消息只留一个「⋯」',
      before.actionsDisplay === 'none' && before.moreVisible && before.morePerRow.every((count) => count === 1),
      `actions=${before.actionsDisplay} moreVisible=${before.moreVisible} perRow=${JSON.stringify(before.morePerRow)}`,
    )
    check(
      '移动端：无横向溢出，气泡行宽 ≤86%',
      before.overflow <= 1 && before.logWidth > 0 && before.maxRow / before.logWidth <= 0.86 + 0.02,
      `overflow=${before.overflow} ratio=${(before.maxRow / before.logWidth).toFixed(2)}`,
    )

    await mobile.locator('.bubble-row.user .bubble-more').first().click()
    await mobile.locator('.bubble-actions.open').waitFor({ state: 'visible', timeout: 5000 })
    await mobile.waitForTimeout(200)
    await shot(mobile, 'a3-chat-390-menu.png')
    const menu = await mobile.evaluate(() => {
      const panel = document.querySelector('.bubble-actions.open')
      if (!panel) return null
      const buttons = Array.from(panel.querySelectorAll('button'))
        .map((button) => ({
          label: (button.textContent || '').trim() || button.getAttribute('aria-label') || '',
          height: button.getBoundingClientRect().height,
        }))
        .filter((item) => item.label)
      return {
        buttons,
        title: (panel.querySelector('.bubble-sheet-title')?.textContent || '').trim(),
        bottom: Math.round(panel.getBoundingClientRect().bottom),
        innerHeight: window.innerHeight,
      }
    })
    check(
      '「⋯」菜单：有标题、≥3 项可见操作且每项 ≥44px',
      Boolean(menu && menu.title && menu.buttons.length >= 3 && menu.buttons.every((item) => item.height >= 44)),
      menu ? `title=${menu.title} items=${menu.buttons.map((item) => `${item.label}:${Math.round(item.height)}`).join('|')}` : '菜单未渲染',
    )
    check(
      '「⋯」菜单在视口内（不越过底部）',
      Boolean(menu && menu.bottom <= menu.innerHeight),
      menu ? `bottom=${menu.bottom} innerHeight=${menu.innerHeight}` : '菜单未渲染',
    )
    await mobile.locator('.bubble-menu-scrim').click({ position: { x: 5, y: 5 } }).catch(() => {})
    await mobile.waitForTimeout(200)
    const closed = await mobile.evaluate(() => !document.querySelector('.bubble-actions.open'))
    check('点击遮罩可关闭「⋯」菜单', closed)

    // ── 移动端设置 ────────────────────────────────────────────────
    await mobile.goto(`${base}/settings`, { waitUntil: 'domcontentloaded' })
    await mobile.locator('.settings-group').first().waitFor({ state: 'visible', timeout: 20000 })
    await mobile.waitForTimeout(250)
    await shot(mobile, 'a3-settings-390.png')
    const settingsMobile = await mobile.evaluate(() => {
      const text = document.body.innerText || ''
      const personaGroups = Array.from(document.querySelectorAll('.settings-group')).filter((group) =>
        (group.getAttribute('aria-label') || '') === '我是谁',
      )
      const group = personaGroups[0] || null
      return {
        defaultPersona: text.includes('默认人设'),
        currentPersona: text.includes('当前人设'),
        groups: Array.from(document.querySelectorAll('.settings-group .group-title')).map((node) =>
          (node.textContent || '').trim(),
        ),
        personaCards: group ? group.querySelectorAll('.settings-card').length : 0,
        personaSelects: group ? group.querySelectorAll('select').length : 0,
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      }
    })
    check(
      '设置页「我是谁」：单一选择器，不再有「当前/默认人设」两套说法',
      !settingsMobile.defaultPersona && !settingsMobile.currentPersona && settingsMobile.personaSelects === 1,
      `selects=${settingsMobile.personaSelects} 默认人设=${settingsMobile.defaultPersona} 当前人设=${settingsMobile.currentPersona}`,
    )
    check(
      '设置页「我是谁」：一层卡片，无横向溢出',
      settingsMobile.personaCards === 1 && settingsMobile.overflow <= 1,
      `cards=${settingsMobile.personaCards} overflow=${settingsMobile.overflow} groups=${JSON.stringify(settingsMobile.groups)}`,
    )

    // ── 移动端详情 ────────────────────────────────────────────────
    await mobile.goto(cardUrl, { waitUntil: 'domcontentloaded' })
    await mobile.locator('.character-hero').first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
    await mobile.waitForTimeout(250)
    await shot(mobile, 'a3-detail-390.png')
    await mobile.locator('.section-action').first().scrollIntoViewIfNeeded().catch(() => {})
    await mobile.waitForTimeout(200)
    await shot(mobile, 'a3-detail-worldbook-390.png')
  } finally {
    await mobile.close().catch(() => {})
  }

  // ── 「我是谁」真正随消息发出（拦截请求，不消耗模型）──────────────
  const personaName = '验收旅人'
  let personaId = ''
  let capturedBody = null
  await page.goto(`${base}/settings`, { waitUntil: 'domcontentloaded' })
  await page.locator('.settings-group').first().waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('[aria-label="我是谁的人设"]').first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(250)
  await shot(page, 'a3-settings-1440.png')
  const existing = page.locator('[aria-label="我是谁的人设"]')
  const alreadyThere = await existing
    .evaluate((node) => Array.from(node.options).some((option) => option.textContent === '验收旅人'))
    .catch(() => false)
  if (!alreadyThere) {
    await page.getByRole('button', { name: '新建', exact: true }).first().click()
    await page.locator('.persona-editor input').fill(personaName)
    await page.locator('.persona-editor textarea').fill('验收用，跑完即删。')
    await page.locator('.persona-editor button[type="submit"]').click()
    await page.locator('.persona-notice').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {})
  } else {
    await page.locator('[aria-label="我是谁的人设"]').selectOption({ label: personaName }).catch(() => {})
  }
  personaId = await page.evaluate(() => window.localStorage.getItem('tavern:persona') || '')
  check('设置页选中的人设写入本机（tavern:persona）', Boolean(personaId), `personaId=${personaId.slice(0, 8)}`)

  if (chatId) {
    await page.route('**/chats/*/messages', async (route) => {
      capturedBody = route.request().postData()
      await route.abort()
    })
    await page.goto(`${base}/chats/${chatId}`, { waitUntil: 'domcontentloaded' })
    await page.locator('.bubble-row').first().waitFor({ state: 'visible', timeout: 20000 })
    await page.locator('#chat-input').fill('验收一句')
    await page.locator('[aria-label="发送消息"]').click()
    await page.waitForTimeout(1200)
    await page.unroute('**/chats/*/messages').catch(() => {})
    let sentPersona = ''
    try {
      sentPersona = capturedBody ? JSON.parse(capturedBody).personaId || '' : ''
    } catch {
      sentPersona = ''
    }
    check(
      '发消息时带上当前人设（服务端按此注入「你的角色」）',
      Boolean(personaId && sentPersona === personaId),
      `sent=${sentPersona ? sentPersona.slice(0, 8) : 'none'}`,
    )
  }

  // 清掉验收人设（UI 两段式删除）；人设列表是异步加载的，先等下拉真正出现。
  await page.goto(`${base}/settings`, { waitUntil: 'domcontentloaded' })
  await page.locator('[aria-label="我是谁的人设"]').first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(300)
  const deletePersona = page.locator('.persona-manage button', { hasText: '删除' }).first()
  if (await deletePersona.count().catch(() => 0)) {
    await deletePersona.click().catch(() => {})
    await page.getByRole('button', { name: '确认删除', exact: true }).first().click().catch(() => {})
    await page.waitForTimeout(600)
    const leftover = await page.evaluate(() => window.localStorage.getItem('tavern:persona')).catch(() => 'unknown')
    console.log(`INFO 验收人设清理：${leftover ? '仍存在 ' + String(leftover).slice(0, 8) : '已删除'}`)
  } else {
    console.log('INFO 未找到可删除的验收人设（可能已不存在）')
  }

  const realErrors = consoleErrors.filter((text) => !/Failed to load resource|favicon/.test(text))
  check('桌面与移动页面无 console 报错', realErrors.length === 0, realErrors.length ? realErrors.slice(0, 3).join(' | ') : 'clean')

  // ── 清理测试聊天（删除接口无 UI 入口，直接调用）─────────────────
  if (chatId) {
    const status = await page
      .evaluate(async (id) => {
        const response = await fetch('/tavern/api/reader/v1/tavern/chats/' + encodeURIComponent(id), {
          method: 'DELETE',
          credentials: 'include',
        })
        return response.status
      }, chatId)
      .catch(() => 0)
    console.log(`INFO 清理测试聊天 ${chatId.slice(0, 8)}：HTTP ${status}${status === 204 || status === 200 ? '' : '（需手动清理）'}`)
  }
} finally {
  // 用 connectOverCDP 连的是用户自己的浏览器：只关自己开的标签页，绝不 browser.close()，
  // 否则会把 WebPilot 的面板一起关掉。进程退出时 WebSocket 自然释放。
  if (owned) await context.close().catch(() => {})
  else await page.close().catch(() => {})
}

const failed = results.filter((item) => !item.ok)
console.log(`\n${results.length - failed.length} PASS / ${failed.length} FAIL · 截图：${outDir}`)
process.exitCode = failed.length ? 1 : 0
