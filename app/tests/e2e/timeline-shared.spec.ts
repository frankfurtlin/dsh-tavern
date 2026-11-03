import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

async function setup(page:Page){
  await page.goto('/register');await page.getByLabel('用户名').fill('trunk_'+Date.now().toString(36));await page.getByLabel('密码').fill('isolated-timeline-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('共用时间线测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'共用时间线测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
  await page.getByRole('button',{name:'下一章',exact:true}).click();await expect(page.locator('.reader-body h1')).toHaveText('第二章 空船')
  await page.getByRole('button',{name:'下一章',exact:true}).click();await expect(page.locator('.reader-body h1')).toHaveText('第三章 旧信')
}
async function fork(page:Page){
  await page.locator('.reader-body .paragraph').last().click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await page.getByRole('textbox',{name:/希望接下来怎样写/}).fill('从这个时刻展开另一种剧情。')
  await page.getByRole('button',{name:/开始写作/}).click();await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible({timeout:30000})
  const title=await page.locator('.writing-chapter h4').first().innerText()
  await page.getByRole('button',{name:'接着读',exact:true}).click();await expect(page.locator('.reader-body h1')).toHaveText(title)
  await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]').first()).toBeVisible()
  await expect(page.locator('.reader-body')).not.toContainText('AI 生成 · 已接受')
}

for(const width of [360,390,430,1440])test(`Shared timeline: chapter three forks into two fourth chapters ${width}px`,async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width,height:1000});await setup(page)
  const originalUrl=page.url(),thirdText=await page.locator('.reader-body .paragraph').allTextContents()
  await fork(page);const branchUrl=page.url(),writtenText=await page.locator('.reader-body .paragraph').allTextContents()
  await page.reload();await expect(page.locator('.reader-body .paragraph').first()).toHaveText(writtenText[0]);await expect(page.locator('.ai-mark')).toHaveCount(0)
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.locator('.timeline-row')).toHaveCount(2)
  const original=page.locator('.timeline-row.is-original'),child=page.locator('.timeline-row:not(.is-original)')
  await expect(original.locator('.timeline-chapter')).toHaveCount(4);await expect(child.locator('.timeline-chapter')).toHaveCount(1)
  await expect(child.locator('.timeline-node-footer')).toContainText('04');await expect(page.locator('.story-line-chip:not(.is-original) .timeline-fork-link')).toContainText('第 3 章')
  await expect(page.locator('.timeline-shared-note,.timeline-scroll-hint,.timeline-fork-summary')).toHaveCount(0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  if(width>=768){
    const a=await original.locator('.timeline-chapter').nth(3).boundingBox(),b=await child.locator('.timeline-chapter').boundingBox(),third=await original.locator('.timeline-chapter').nth(2).boundingBox()
    expect(Math.abs(a!.x-b!.x)).toBeLessThan(1);expect(b!.x).toBeGreaterThan(third!.x+third!.width)
    await expect(page.locator('.timeline-edges path')).toHaveCount(1)
    const edge=await page.locator('.timeline-edges path').getAttribute('d');expect(edge).toContain(' C ')
  }
  await page.screenshot({path:path.join(artifacts,`shared-trunk-${width}.png`),fullPage:true})
  await child.locator('.timeline-chapter').focus();await child.locator('.timeline-chapter').click();await expect(page.locator('.reader-body .paragraph').first()).toHaveText(writtenText[0]);expect(page.url()).toContain(new URL(branchUrl).searchParams.get('branch')!)
  // The child still has its complete reading directory, and its shared chapter
  // gets a reading marker on the single ancestor node in the graph.
  await page.getByRole('button',{name:'打开目录',exact:true}).click();await page.locator('.sheet .chapter-link').filter({hasText:'第二章 空船'}).click()
  await expect(page.locator('.reader-body h1')).toHaveText('第二章 空船');await page.getByRole('button',{name:'故事时间线',exact:true}).click()
  await expect(original.locator('.timeline-chapter').nth(1)).toHaveClass(/is-reading/)
  const chip=page.locator('.story-line-chip:not(.is-original)');await chip.locator('summary').click();await chip.locator('.timeline-fork-link').click();await expect(page.locator('.reader-body h1')).toHaveText('第三章 旧信')
  expect(page.url()).toContain(new URL(originalUrl).searchParams.get('branch')!)
  await expect(page.locator('.reader-body .paragraph')).toHaveText(thirdText)
})

test('Shared timeline: nested continuation shares an existing AI fourth chapter',async({page})=>{
  test.setTimeout(120000);await page.setViewportSize({width:1440,height:1000});await setup(page);await fork(page);await fork(page)
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.locator('.timeline-row')).toHaveCount(3)
  const rows=page.locator('.timeline-row');await expect(rows.nth(1).locator('.timeline-chapter')).toHaveCount(1);await expect(rows.nth(2).locator('.timeline-chapter')).toHaveCount(1)
  await expect(rows.nth(2).locator('.timeline-node-footer')).toContainText('05');await expect(page.locator('.story-line-chip').nth(2).locator('.timeline-fork-link')).toContainText('第 4 章')
  await expect(page.locator('.timeline-edges path')).toHaveCount(2)
  const fourth=await rows.nth(1).locator('.timeline-chapter').boundingBox(),fifth=await rows.nth(2).locator('.timeline-chapter').boundingBox()
  expect(fifth!.x).toBeGreaterThan(fourth!.x+fourth!.width)
  await page.screenshot({path:path.join(artifacts,'shared-trunk-nested.png'),fullPage:true})
  await page.locator('.story-line-chip').nth(1).locator('summary').click();await page.locator('.story-line-chip').nth(1).getByRole('button',{name:'归档',exact:true}).click();await expect(page.locator('.timeline-row')).toHaveCount(2)
  await expect(page.locator('.timeline-edges path')).toHaveCount(0)
  await page.getByLabel(/显示归档/).check();await expect(page.locator('.timeline-row')).toHaveCount(3);await expect(page.locator('.timeline-edges path')).toHaveCount(2)
})
