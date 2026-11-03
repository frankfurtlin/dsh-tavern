import {test,expect,type Page} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import {expectLibraryLanding,chooseCustomChannel,expectSavedInline,readSavedInline} from './helpers/landing'

async function account(page:Page){
  await page.goto('/register')
  await page.getByLabel('用户名').fill('current_'+randomUUID().replaceAll('-','').slice(0,18))
  await page.getByLabel('密码').fill('isolated-current-flow-password')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  await expect(page).toHaveURL(/\/worlds$/)
  await expect(page.getByRole('heading',{name:'先体验蓝宝石案',exact:true})).toBeVisible()
}
async function model(page:Page){
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道',exact:true}).click()
  await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('当前流程隔离模型')
  await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('SYNTHETIC-NOT-A-REAL-KEY')
  await page.getByRole('button',{name:'保存配置',exact:true}).click()
  await expect(page.getByRole('heading',{name:'当前流程隔离模型 默认模型 可用配置'})).toBeVisible()
}
async function demo(page:Page){
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书',exact:true}).click()
  await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
  await page.waitForTimeout(1000)
}
for(const width of [390,1440])test(`current inline generation: explicit acceptance, reload and original preservation ${width}px`,async({page})=>{
  test.setTimeout(120000);await page.setViewportSize({width,height:900})
  await account(page);await model(page);await demo(page)
  const original=page.url(),text=await page.locator('.paragraph').allTextContents()
  await page.locator('.paragraph').first().click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await page.getByLabel('希望接下来怎样写？').fill('从新的线索继续调查')
  await page.getByRole('button',{name:/^开始写作/}).click();await expectSavedInline(page)
  await expect(page).toHaveURL(original)
  await page.reload();await expectSavedInline(page)
  await readSavedInline(page);expect(page.url()).not.toBe(original)
  await expect(page.locator('.reader-body [data-content-source="ai"]').first()).toBeVisible()
  await page.reload();await expect(page.locator('.reader-body [data-content-source="ai"]').first()).toBeVisible()
  await page.goto(original);await expect(page.locator('.paragraph')).toHaveCount(text.length)
  expect(await page.locator('.paragraph').allTextContents()).toEqual(text)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true)
})

test('current inline generation: accept a prefix then preserve the remaining saved chapters',async({page})=>{
  test.setTimeout(120000);await account(page);await model(page);await demo(page)
  await page.locator('.paragraph').first().click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await page.getByRole('button',{name:'3 章',exact:true}).click()
  await page.getByRole('button',{name:/^开始写作/}).click();await expectSavedInline(page,3)
  await page.getByLabel('连续保存至章节',{exact:true}).selectOption('1')
  await page.getByRole('button',{name:'保存前 1 章',exact:true}).click()
  await expect(page.locator('.generation-chapter .badge').first()).toContainText('已加入故事线')
  await expect(page.locator('.generation-chapter .badge').nth(1)).toContainText('已保存')
  await page.getByLabel('连续保存至章节',{exact:true}).selectOption('3')
  await page.getByRole('button',{name:'保存全部完整章节',exact:true}).click()
  await expect(page.locator('.generation-chapter .badge').nth(2)).toContainText('已加入故事线')
  await readSavedInline(page)
})

test('first case onboarding reaches the prepared Blue Carbuncle without a generation request',async({page})=>{
  await account(page)
  let calls=0
  page.on('request',r=>{if(r.method()==='POST'&&/\/generation-tasks$|\/actions$|\/books\/.+\/generations$/.test(r.url()))calls++})
  await page.getByRole('link',{name:'载入已校对案例',exact:true}).click()
  await page.getByRole('button',{name:'使用已校对版本 · 包含三条善后',exact:true}).click()
  await expect(page.getByText('完整拆解',{exact:true})).toBeVisible()
  expect(calls).toBe(0)
})
