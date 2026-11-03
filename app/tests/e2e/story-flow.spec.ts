import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
async function setup(page:Page){
  const name='story_'+Date.now().toString(36)
  await page.goto('/register');await page.getByLabel('用户名').fill(name);await page.getByLabel('密码').fill('story-flow-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('故事流程测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-KEY-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'故事流程测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口');await page.waitForTimeout(3500)
  return name
}
for(const width of [360,390,430,1440])test(`Story flow: three real saved chapters and timeline ${width}px`,async({page})=>{
  test.setTimeout(120000);await page.setViewportSize({width,height:900});await setup(page)
  const originalUrl=page.url(),originalText=await page.locator('.paragraph').first().innerText();let posts=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/story-runs'))posts++})
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await expect(page.getByRole('group',{name:'选择章节数量'})).toBeVisible();await page.getByRole('button',{name:'3 章',exact:true}).click()
  await page.getByRole('textbox',{name:/希望接下来怎样写/}).fill('留下救人，三章之后一起回到渡口')
  await expect(page.getByText('本次调用计划',{exact:true})).toHaveCount(0)
  await expect(page.getByRole('combobox',{name:'模型通道'})).toHaveCount(0)
  await page.getByRole('button',{name:/开始写作/}).click()
  await expect(page.locator('.writing-chapter')).toHaveCount(3,{timeout:45000});expect(posts).toBe(1)
  const titles=await page.locator('.writing-chapter h4').allTextContents();expect(new Set(titles).size).toBe(3)
  await page.getByRole('button',{name:'接着读',exact:true}).click();await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]').first()).toBeVisible()
  const branchUrl=page.url();expect(branchUrl).not.toBe(originalUrl);await page.reload();await expect(page.locator('.reader-body h1')).toHaveText(titles[0])
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.getByRole('heading',{name:'故事时间线',exact:true})).toBeVisible()
  await expect(page.locator('.timeline-row')).toHaveCount(2);await expect(page.locator('.timeline-row.is-original .timeline-chapter')).toHaveCount(4)
  const child=page.locator('.timeline-row:not(.is-original)');await expect(child.locator('.timeline-chapter.is-ai')).toHaveCount(3)
  await expect(page.locator('.story-line-chip:not(.is-original) .timeline-fork-link')).toContainText('第 1 章');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:path.join(artifacts,`story-timeline-${width}.png`),fullPage:true})
  const childMenu=page.locator('.story-line-chip:not(.is-original)');await childMenu.locator('summary').click();await childMenu.getByRole('button',{name:'重命名',exact:true}).click();await page.getByLabel('故事线名称').fill('雨夜另一条路');await page.getByRole('button',{name:'保存名称',exact:true}).click();await expect(childMenu.locator('.timeline-line-name')).toHaveText('雨夜另一条路');await expect(childMenu.locator('.timeline-line-name')).toBeVisible()
  await page.locator('.story-line-chip.is-original .timeline-line-name').click();await expect(page.locator('.reader-title small')).toContainText('原文');await expect(page.locator('.paragraph').first()).toHaveText(originalText)
})

test('Story flow: automatic cards, one-step chat and explicit full-book preference',async({page})=>{
  test.setTimeout(120000);await setup(page);await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'聊角色',exact:true}).click()
  await expect(page.getByRole('heading',{name:'想和谁聊一聊？'})).toBeVisible();await expect(page.getByRole('button',{name:'准备角色上下文'})).toHaveCount(0)
  await expect(page.locator('.character-card').first()).toBeVisible({timeout:45000})
  await page.screenshot({path:path.join(artifacts,'story-character-cards.png'),fullPage:true})
  await page.locator('.character-card').filter({has:page.getByRole('button',{name:'和 TA 聊天',exact:true})}).first().getByRole('button',{name:'和 TA 聊天',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeVisible();await expect(page.getByText('确认本次对话预算')).toHaveCount(0)
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('此刻你在想什么？');await page.getByRole('button',{name:'发送消息',exact:true}).click()
  await expect(page.locator('.chat-message.assistant')).toBeVisible({timeout:30000});await expect(page.locator('.chat-message.user')).toHaveText('此刻你在想什么？')
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();await page.goto('/settings')
  await expect(page.getByLabel('角色阅读范围')).toHaveValue('progress');await page.getByLabel('角色阅读范围').selectOption('full');await page.getByRole('button',{name:'保存角色设置'}).click();await expect(page.getByText('角色阅读范围已保存',{exact:true})).toBeVisible()
  await page.reload();await expect(page.getByLabel('角色阅读范围')).toHaveValue('full');await page.getByLabel('角色阅读范围').selectOption('progress');await page.getByRole('button',{name:'保存角色设置'}).click()
})

test('Story flow: confirmed writing survives a failed local recovery write',async({page})=>{
  test.setTimeout(90000);await setup(page);let posts=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/story-runs'))posts++})
  await page.evaluate(()=>{const original=IDBObjectStore.prototype.put;let failed=false;IDBObjectStore.prototype.put=function(value,key){if(!failed&&String(key).includes(':generation:')&&value?.runId){failed=true;throw new DOMException('Injected recovery write failure','QuotaExceededError')}return original.call(this,value,key)}})
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click();await page.getByRole('button',{name:/开始写作/}).click()
  await expect(page.getByRole('button',{name:'查看已提交的写作',exact:false})).toBeVisible();expect(posts).toBe(1)
  await page.getByRole('button',{name:'查看已提交的写作',exact:false}).click();await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible({timeout:30000});expect(posts).toBe(1)
})

test('Story flow: confirmed role chat survives local cleanup failure without duplicate creation',async({page})=>{
  test.setTimeout(90000);await setup(page);await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'聊角色',exact:true}).click();await expect(page.locator('.character-card').first()).toBeVisible({timeout:45000})
  let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/character-chats'))posts++})
  await page.evaluate(()=>{const original=IDBObjectStore.prototype.put;let failed=false;IDBObjectStore.prototype.put=function(value,key){if(!failed&&String(key).includes(':character-chat:')&&value===null){failed=true;throw new DOMException('Injected cleanup failure','QuotaExceededError')}return original.call(this,value,key)}})
  const card=page.locator('.character-card').first();await card.getByRole('button',{name:'和 TA 聊天',exact:true}).click();await expect(page.getByText('对话已经准备好，本机记录暂时未能清理。再次点击角色即可进入同一段对话。')).toBeVisible();expect(posts).toBe(1)
  await card.getByRole('button',{name:'和 TA 聊天',exact:true}).click();await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeVisible();expect(posts).toBe(1)
})
