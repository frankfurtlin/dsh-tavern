import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

async function setup(page:Page){
  const username='finish_'+Date.now().toString(36)
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill('story-finalization-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('本机收尾测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-KEY-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'本机收尾测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口');await page.waitForTimeout(3500)
}
async function openChat(page:Page){
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'聊角色',exact:true}).click()
  const button=page.getByRole('button',{name:'和 TA 聊天',exact:true}).first();await expect(button).toBeEnabled({timeout:45000});await button.click()
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeVisible()
}
async function failChatCleanupOnce(page:Page){
  await page.evaluate(()=>{
    const original=IDBObjectStore.prototype.put;let remaining=1
    IDBObjectStore.prototype.put=function(value:unknown,key?:IDBValidKey){
      if(remaining&&String(key).includes(':chat-pending:')&&value===null){remaining--;throw new DOMException('Intentional terminal cleanup failure','QuotaExceededError')}
      return original.call(this,value,key)
    }
  })
}
async function failImportCleanupOnce(page:Page){
  await page.evaluate(()=>{
    const original=IDBObjectStore.prototype.put;let remaining=1
    IDBObjectStore.prototype.put=function(value:unknown,key?:IDBValidKey){
      if(remaining&&String(key).endsWith(':pending-import')&&value===''){remaining--;throw new DOMException('Intentional confirmed import cleanup failure','QuotaExceededError')}
      return original.call(this,value,key)
    }
  })
}
test('Story cache: confirmed reply survives terminal local cleanup failure without another send',async({page})=>{
  test.setTimeout(120000);await setup(page);await openChat(page);await failChatCleanupOnce(page)
  let sends=0;page.on('request',request=>{if(request.method()==='POST'&&/\/role-sessions\/[^/]+\/send$/.test(request.url()))sends++})
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('此刻你在想什么？');await page.getByRole('button',{name:'发送消息',exact:true}).click()
  await expect(page.locator('.chat-message.assistant')).toHaveCount(1,{timeout:30000})
  await expect(page.locator('[data-chat-cleanup]')).toContainText('本机记录暂时未能整理')
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled();await expect(page.getByRole('status').filter({hasText:'TA 正在回复'})).toHaveCount(0);expect(sends).toBe(1)
  await page.getByRole('button',{name:'整理本机记录',exact:true}).click();await expect(page.locator('[data-chat-cleanup]')).toHaveCount(0)
  expect(sends).toBe(1);await expect(page.locator('.chat-message.assistant')).toHaveCount(1);await expect(page.locator('.chat-message.user')).toHaveText('此刻你在想什么？')
  await page.screenshot({path:path.join(artifacts,'story-cache-chat-terminal.png'),fullPage:true})
})
test('Story cache: a durable next message safely replaces the prior terminal cleanup receipt',async({page})=>{
  test.setTimeout(120000);await setup(page);await openChat(page);await failChatCleanupOnce(page)
  const turns:string[]=[];page.on('request',request=>{if(request.method()==='POST'&&/\/role-sessions\/[^/]+\/send$/.test(request.url()))turns.push(request.postDataJSON().turnId)})
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('此刻你在想什么？');await page.getByRole('button',{name:'发送消息',exact:true}).click()
  await expect(page.locator('.chat-message.assistant')).toHaveCount(1,{timeout:30000});await expect(page.locator('[data-chat-cleanup]')).toContainText('本机记录暂时未能整理')
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('我们现在在哪里？');await page.getByRole('button',{name:'发送消息',exact:true}).click()
  await expect(page.locator('.chat-message.assistant')).toHaveCount(2,{timeout:30000});await expect(page.locator('.chat-message.user')).toHaveText(['此刻你在想什么？','我们现在在哪里？'])
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled();await expect(page.locator('[data-chat-cleanup]')).toHaveCount(0)
  expect(turns).toHaveLength(2);expect(new Set(turns).size).toBe(2)
})
test('Story cache: accepted chapters open even when the local generation record cannot clear',async({page})=>{
  test.setTimeout(120000);await setup(page);const originalUrl=page.url()
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click();await page.getByLabel('希望接下来怎样写？').fill('留下救人')
  await page.getByRole('button',{name:/开始写作/}).click();await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible({timeout:45000})
  await page.evaluate(()=>{
    const original=IDBObjectStore.prototype.put;let remaining=1
    IDBObjectStore.prototype.put=function(value:unknown,key?:IDBValidKey){
      if(remaining&&String(key).includes(':generation:')&&!!value&&typeof value==='object'&&'taskId' in value&&(value as {taskId:unknown}).taskId===null){remaining--;throw new DOMException('Intentional accepted-record cleanup failure','QuotaExceededError')}
      return original.call(this,value,key)
    }
  })
  let accepts=0;page.on('request',request=>{if(request.method()==='POST'&&/\/candidates\/[^/]+\/accept$/.test(request.url()))accepts++})
  const response=page.waitForResponse(value=>value.request().method()==='POST'&&/\/candidates\/[^/]+\/accept$/.test(value.url()))
  await page.getByRole('button',{name:'接着读',exact:true}).click();const saved=await(await response).json()
  await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]').first()).toBeVisible();expect(page.url()).not.toBe(originalUrl)
  expect(new URL(page.url()).searchParams.get('branch')).toBe(saved.branchId);expect(new URL(page.url()).searchParams.get('chapter')).toBe(saved.readingAnchor.chapterId)
  await expect(page.getByText('新章节已保存，本机写作记录暂未同步。',{exact:true})).toBeVisible();expect(accepts).toBe(1)
  await page.reload();await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]').first()).toBeVisible();expect(accepts).toBe(1)
})
test('Story cache: import confirmed cleanup failure still opens the original and restores the library',async({page})=>{
  test.setTimeout(120000);await setup(page);await page.goto('/library')
  let confirms=0;page.on('request',request=>{if(request.method()==='POST'&&/\/books\/imports\/[^/]+\/confirm$/.test(request.url()))confirms++})
  const upload=page.waitForRequest(request=>request.method()==='POST'&&/\/books\/imports$/.test(request.url()))
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click()
  await(await chooser).setFiles(path.resolve('../tests/fixtures/bom-story.txt'))
  expect((await upload).headers()['content-type']).toMatch(/^multipart\/form-data; boundary=/)
  await expect(page.getByRole('button',{name:'确认并开始阅读',exact:true})).toBeVisible({timeout:15000})
  await page.getByLabel('书名',{exact:true}).fill('缓存收尾原故事');await failImportCleanupOnce(page)
  const confirmation=page.waitForResponse(response=>response.request().method()==='POST'&&/\/books\/imports\/[^/]+\/confirm$/.test(response.url()))
  await page.getByRole('button',{name:'确认并开始阅读',exact:true}).click();const saved=await(await confirmation).json()
  await expect(page.locator('.reader-body h1')).toHaveText('第一章 小桥');await expect(page.locator('.reader-body')).toContainText('甲😀乙')
  expect(new URL(page.url()).pathname).toBe('/books/'+saved.bookId+'/read');expect(new URL(page.url()).searchParams.get('branch')).toBe(saved.branchId)
  await expect(page.getByText('书籍已保存，本机导入记录暂未同步。',{exact:true})).toBeVisible();await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]')).toHaveCount(0);expect(confirms).toBe(1)
  await page.reload();await expect(page.locator('.reader-body h1')).toHaveText('第一章 小桥')
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.locator('.timeline-line-name')).toHaveCount(1);await expect(page.locator('.timeline-row.is-original')).toHaveAttribute('data-line-id',saved.branchId)
  await failImportCleanupOnce(page);await page.locator('.app-header a.brand').click()
  await expect(page.getByRole('heading',{name:'缓存收尾原故事',exact:true})).toBeVisible();await expect(page.getByRole('dialog',{name:'导入小说',exact:true})).toHaveCount(0)
  await expect(page.getByText('书籍已保存，本机导入记录暂未同步。',{exact:true})).toBeVisible();expect(confirms).toBe(1)
  await page.reload();await expect(page.getByRole('heading',{name:'缓存收尾原故事',exact:true})).toBeVisible();await expect(page.getByRole('dialog',{name:'导入小说',exact:true})).toHaveCount(0);expect(confirms).toBe(1)
  await page.screenshot({path:path.join(artifacts,'story-cache-import-terminal.png'),fullPage:true})
})
