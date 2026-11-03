import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
async function setup(page:Page){
  await page.goto('/register');await page.getByLabel('用户名').fill('unified_'+Date.now().toString(36));await page.getByLabel('密码').fill('isolated-unified-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page);await page.getByLabel('通道名称').fill('统一写作测试')
  await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic');await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'统一写作测试 默认模型 可用配置'})).toBeVisible();await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
}
async function write(page:Page,wish=''){
  await page.getByRole('button',{name:'从这里写',exact:true}).click();await expect(page.getByRole('heading',{name:'从这里写',exact:true})).toBeVisible()
  const instruction=page.getByRole('textbox',{name:/希望接下来怎样写/});await instruction.fill(wish)
  const request=page.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith('/story-runs'))
  await page.getByRole('button',{name:/开始写作/}).click();const body=(await request).postDataJSON()
  await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible({timeout:30000})
  const title=await page.locator('.writing-chapter h4').first().innerText();await page.getByRole('button',{name:'接着读',exact:true}).click();await expect(page.locator('.reader-body h1')).toHaveText(title)
  await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]').first()).toBeVisible()
  return body
}
for(const width of [390,1440])test(`Unified writing: one entry naturally continues and an explicit wish forks at the tail ${width}px`,async({page})=>{
  test.setTimeout(120000);await page.setViewportSize({width,height:1000});await setup(page)
  await expect(page.locator('.reader-toolbar button')).toHaveCount(2);await expect(page.getByRole('button',{name:'改剧情',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'续写',exact:true})).toHaveCount(0)
  await page.locator('.paragraph').last().click();const first=await write(page)
  expect(first.operation).toBe('continue_from_anchor');expect(first.instruction).toBe('');expect(first.target.mode).toBe('new_child_branch')
  const childId=new URL(page.url()).searchParams.get('branch')
  await page.locator('.paragraph').last().click();const append=await write(page)
  expect(append.operation).toBe('continue_at_tail');expect(append.target.mode).toBe('append_existing');expect(new URL(page.url()).searchParams.get('branch')).toBe(childId)
  const originalText=await page.locator('.paragraph').allTextContents()
  await page.locator('.paragraph').last().click();const alternate=await write(page,'让林渡留在东岸，等待梅舟回来。')
  expect(alternate.operation).toBe('counterfactual');expect(alternate.instruction).toContain('等待梅舟');expect(alternate.target.mode).toBe('new_child_branch')
  expect(new URL(page.url()).searchParams.get('branch')).not.toBe(childId)
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.locator('.timeline-row')).toHaveCount(3)
  const oldLine=page.locator(`.timeline-row[data-line-id="${childId}"]`);await expect(oldLine.locator('.timeline-chapter')).toHaveCount(2)
  await oldLine.locator('.timeline-chapter').last().focus();await oldLine.locator('.timeline-chapter').last().click();await expect(page.locator('.reader-body .paragraph')).toHaveText(originalText)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:path.join(artifacts,`unified-writing-${width}.png`),fullPage:true})
})
