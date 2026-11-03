import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

async function setup(page:Page){
  await page.goto('/register');await page.getByLabel('用户名').fill('stream_'+Date.now().toString(36));await page.getByLabel('密码').fill('chat-streaming-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page);await page.getByLabel('通道名称').fill('流式对话测试')
  await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic');await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-KEY-NOT-REAL')
  await page.getByRole('button',{name:'保存配置'}).click();await expect(page.getByRole('heading',{name:'流式对话测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口');await page.waitForTimeout(3500)
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'聊角色',exact:true}).click()
  const talk=page.getByRole('button',{name:'和 TA 聊天',exact:true}).first();await expect(talk).toBeEnabled({timeout:45000});await talk.click()
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled()
}
async function send(page:Page,text:string){
  const response=page.waitForResponse(value=>value.request().method()==='POST'&&/\/role-sessions\/[^/]+\/send$/.test(value.url()))
  await page.getByRole('textbox',{name:'给角色的消息'}).fill(text);await page.getByRole('button',{name:'发送消息',exact:true}).click()
  const result=await(await response).json();return result.taskId as string
}
async function reopen(page:Page){
  await page.goto('/conversations');await expect(page.locator('.chat-sidebar button').first()).toBeVisible();await page.locator('.chat-sidebar button').first().click()
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled()
}
function requests(page:Page){
  const sent:string[]=[],cancelled:string[]=[]
  page.on('request',request=>{if(request.method()==='POST'&&/\/role-sessions\/[^/]+\/send$/.test(request.url()))sent.push(request.postDataJSON().turnId);if(request.method()==='POST'&&/\/tasks\/[^/]+\/cancel$/.test(request.url()))cancelled.push(request.url())})
  return {sent,cancelled}
}

test('Streaming chat: actual growing snapshot text, one user bubble and next draft survive confirmed local write failure',async({page})=>{
  test.setTimeout(120000);await setup(page);const traffic=requests(page),snapshots:string[]=[]
  page.on('response',response=>{if(response.request().method()==='GET'&&/\/tasks\/[^/?]+$/.test(response.url()))void response.json().then(value=>{if(value.replyStream?.text)snapshots.push(value.replyStream.text)}).catch(()=>{})})
  await page.evaluate(()=>{const original=IDBObjectStore.prototype.put;let remaining=1;IDBObjectStore.prototype.put=function(value,key){if(remaining&&String(key).includes(':chat-pending:')&&value?.taskId){remaining--;throw new DOMException('Intentional confirmed request write failure','QuotaExceededError')}return original.call(this,value,key)}})
  const taskId=await send(page,'[fixture:stream-slow]')
  await expect(page.locator('.chat-message.user')).toHaveCount(1);await expect(page.locator('.chat-message.user .chat-message-text')).toHaveText('[fixture:stream-slow]')
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toHaveValue('');await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled()
  await expect(page.locator('[data-chat-thinking]')).toBeVisible();await expect(page.getByRole('button',{name:'发送消息',exact:true})).toBeDisabled()
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('[fixture:stream-slow]')
  const live=page.locator('.chat-message.assistant[data-chat-live="true"] .chat-message-text');await expect(live).toBeVisible({timeout:30000})
  const first=await live.innerText();expect(first.length).toBeGreaterThan(0);await expect(page.locator('.chat-message.assistant .chat-message-text')).not.toHaveText(first)
  await expect(page.getByRole('button',{name:'停止回复',exact:true})).toHaveCount(0,{timeout:30000})
  const final=await page.locator('.chat-message.assistant .chat-message-text').innerText();expect(final.startsWith(first)).toBe(true);expect(final.length).toBeGreaterThan(first.length)
  // SSE can deliver growing text with only one final HTTP snapshot. The live
  // DOM checks above still require a real prefix and a longer final response.
  expect(snapshots.some(text=>text===final)).toBe(true)
  await expect(page.locator('.chat-message.user')).toHaveCount(1);await expect(page.locator('.chat-message.assistant')).toHaveCount(1);await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-task-id',taskId)
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toHaveValue('[fixture:stream-slow]');expect(traffic.sent).toHaveLength(1)
  await page.screenshot({path:path.join(artifacts,'chat-stream-complete.png'),fullPage:true})
})

test('Streaming chat: stopping preserves received words as one saved partial message on reopen',async({page})=>{
  test.setTimeout(120000);await setup(page);const traffic=requests(page);const taskId=await send(page,'[fixture:stream-slow]')
  const live=page.locator('.chat-message.assistant[data-chat-live="true"] .chat-message-text');await expect(live).toBeVisible({timeout:30000});const before=await live.innerText()
  await page.getByRole('button',{name:'停止回复',exact:true}).click()
  await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-stream-status','stopped',{timeout:30000});await expect(page.locator('.chat-message.assistant .chat-message-note')).toContainText('已停止')
  const partial=await page.locator('.chat-message.assistant .chat-message-text').innerText();expect(partial.startsWith(before)).toBe(true)
  await expect(page.getByRole('button',{name:'停止回复',exact:true})).toHaveCount(0);await reopen(page)
  await expect(page.locator('.chat-message.assistant .chat-message-text')).toHaveText(partial);await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-task-id',taskId)
  expect(traffic.sent).toHaveLength(1);expect(traffic.cancelled).toHaveLength(1)
  await page.screenshot({path:path.join(artifacts,'chat-stream-stopped.png'),fullPage:true})
})

test('Streaming chat: closing and interrupted observation resume the same request, while authoritative denial clears it',async({page})=>{
  test.setTimeout(120000);await setup(page);const traffic=requests(page);const taskId=await send(page,'[fixture:stream-slow]')
  await expect(page.locator('.chat-message.assistant[data-chat-live="true"]')).toBeVisible({timeout:30000})
  let disconnected=true
  await page.route(`**/tasks/${taskId}**`,route=>route.request().method()==='GET'&&disconnected?route.abort('connectionfailed'):route.continue())
  await page.evaluate(()=>{window.dispatchEvent(new Event('reader-save'));window.dispatchEvent(new Event('reader-resume'))})
  await expect(page.getByText('连接暂时中断，正在恢复同一条回复。',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();await reopen(page)
  expect(traffic.sent).toHaveLength(1);expect(traffic.cancelled).toHaveLength(0)
  disconnected=false;await page.evaluate(()=>window.dispatchEvent(new Event('reader-resume')))
  await expect(page.getByRole('button',{name:'停止回复',exact:true})).toHaveCount(0,{timeout:30000});await expect(page.locator('.chat-message.assistant')).toHaveCount(1)
  await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-task-id',taskId);await expect(page.locator('.chat-message.user')).toHaveCount(1)
  expect(traffic.sent).toHaveLength(1);expect(traffic.cancelled).toHaveLength(0)
  const secondTask=await send(page,'[fixture:stream-slow]');await expect(page.locator('.chat-message.assistant[data-chat-live="true"]')).toBeVisible({timeout:30000})
  let deniedReads=0
  await page.route(`**/tasks/${secondTask}**`,route=>{if(route.request().method()==='GET'){deniedReads++;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({code:'ROLE_SCOPE_CHANGED',message:'Scope withdrawn by fixture'})})}return route.continue()})
  await page.evaluate(()=>window.dispatchEvent(new Event('reader-resume')))
  await expect(page.locator('.chat-message')).toHaveCount(0);await expect(page.getByRole('alert')).toContainText('请关闭后重新选择角色');await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeDisabled()
  await page.evaluate(()=>{window.dispatchEvent(new Event('reader-resume'));window.dispatchEvent(new Event('reader-resume'))});await page.waitForTimeout(500)
  expect(deniedReads).toBe(1);expect(traffic.sent).toHaveLength(2);expect(traffic.cancelled).toHaveLength(0)
})

test('Streaming chat: provider interruption retains the actual partial text and does not resend on reopen',async({page})=>{
  test.setTimeout(120000);await setup(page);const traffic=requests(page);const taskId=await send(page,'[fixture:stream-interrupt]')
  await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-stream-status','interrupted',{timeout:30000})
  const partial=await page.locator('.chat-message.assistant .chat-message-text').innerText();expect(partial.length).toBe(24)
  await expect(page.locator('.chat-message.assistant .chat-message-note')).toContainText('回复中断');await expect(page.getByRole('button',{name:'停止回复',exact:true})).toHaveCount(0)
  await reopen(page);await expect(page.locator('.chat-message.assistant .chat-message-text')).toHaveText(partial);await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-task-id',taskId)
  expect(traffic.sent).toHaveLength(1);expect(traffic.cancelled).toHaveLength(0)
})

test('Streaming chat: upward reading pauses following until explicitly returning to the latest reply',async({page})=>{
  test.setTimeout(120000);await page.setViewportSize({width:390,height:900});await setup(page)
  await send(page,'只谈眼前已经读过的故事，保留我们在渡口看到的事情。'.repeat(60))
  await expect(page.locator('.chat-message.assistant[data-chat-live="true"]')).toBeVisible({timeout:30000})
  await page.locator('.chat-messages').evaluate(area=>{area.dispatchEvent(new WheelEvent('wheel',{deltaY:-600,bubbles:true}));area.scrollTop=0;area.dispatchEvent(new Event('scroll'))})
  await expect(page.getByRole('button',{name:'查看新回复',exact:true})).toBeVisible({timeout:30000})
  await expect(page.getByRole('button',{name:'停止回复',exact:true})).toHaveCount(0,{timeout:30000});expect(await page.locator('.chat-messages').evaluate(area=>area.scrollTop)).toBeLessThanOrEqual(2)
  await page.getByRole('button',{name:'查看新回复',exact:true}).click();await expect.poll(()=>page.locator('.chat-messages').evaluate(area=>area.scrollHeight-area.clientHeight-area.scrollTop)).toBeLessThanOrEqual(48)
  await expect(page.getByRole('button',{name:'查看新回复',exact:true})).toHaveCount(0)
  await page.screenshot({path:path.join(artifacts,'chat-stream-mobile-latest.png'),fullPage:true})
})
