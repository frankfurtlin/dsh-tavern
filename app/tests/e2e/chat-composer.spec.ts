import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
async function setup(page:Page){
  await page.goto('/register');await page.getByLabel('用户名').fill('composer_'+Date.now().toString(36));await page.getByLabel('密码').fill('composer-fixture-123');await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page);await page.getByLabel('通道名称').fill('输入框布局测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic');await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-ONLY');await page.getByRole('button',{name:'保存配置'}).click();await expect(page.getByRole('heading',{name:'输入框布局测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口');await page.waitForTimeout(3500);await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'聊角色',exact:true}).click();const talk=page.getByRole('button',{name:'和 TA 聊天',exact:true}).first();await expect(talk).toBeEnabled({timeout:30000});await talk.click();await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled()
}
async function composerInFrame(page:Page){
  await expect.poll(async()=>{
    const input=await page.getByRole('textbox',{name:'给角色的消息'}).boundingBox(),send=await page.getByRole('button',{name:'发送消息',exact:true}).boundingBox(),view=page.viewportSize()!
    return !!input&&!!send&&input.y>=0&&input.y+input.height<=view.height-2&&send.y>=0&&send.y+send.height<=view.height-2
  }).toBe(true)
}
async function openSaved(page:Page){await page.goto('/conversations');await expect(page.locator('.chat-sidebar button').first()).toBeVisible();await page.locator('.chat-sidebar button').first().click();await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled()}

test('Chat composer: desktop empty and long history keep the input visible at every window size',async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width:1440,height:900});await setup(page);await openSaved(page);await composerInFrame(page)
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1)).toBe(true)
  await page.screenshot({path:path.join(artifacts,'composer-desktop-empty.png')})
  // Layout-only history fixture; streaming behavior has its own real mock-provider suite.
  const history=Array.from({length:60},(_,index)=>({id:'layout-'+index,turnId:'turn-'+Math.floor(index/2),taskId:'layout-task-'+Math.floor(index/2),sequence:index,role:index%2?'assistant':'user',text:'第'+index+'条：雾从河面升起，渡口的灯依旧亮着。'.repeat(8),status:'succeeded'}))
  await page.route('**/role-sessions/*/messages*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:history,nextCursor:null})}))
  await openSaved(page);await expect(page.locator('.chat-message')).toHaveCount(60)
  for(const size of [{width:1280,height:720},{width:1920,height:1080},{width:1024,height:600},{width:1440,height:900}]){
    await page.setViewportSize(size);await composerInFrame(page)
    const compose=await page.locator('.chat-compose').boundingBox(),area=page.locator('.chat-messages');await area.hover();await page.mouse.wheel(0,-1200);await expect.poll(()=>area.evaluate(node=>node.scrollTop+node.clientHeight<node.scrollHeight-100)).toBe(true)
    const after=await page.locator('.chat-compose').boundingBox();expect(Math.abs(after!.y-compose!.y)).toBeLessThan(1);await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1)).toBe(true)
  }
  await page.screenshot({path:path.join(artifacts,'composer-desktop-history.png')})
})

test('Chat composer: both reader and saved-chat mobile sheets keep input inside their content frame',async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width:390,height:844});await setup(page);await composerInFrame(page)
  const content=page.locator('.sheet-content-fill');expect(await content.evaluate(node=>node.scrollHeight<=node.clientHeight+1)).toBe(true)
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('保留这段尚未发送的草稿')
  await page.screenshot({path:path.join(artifacts,'composer-reader-mobile.png')})
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();await openSaved(page);await composerInFrame(page);await expect(page.getByRole('textbox',{name:'给角色的消息'})).toHaveValue('保留这段尚未发送的草稿')
  await page.setViewportSize({width:390,height:500});await composerInFrame(page);await page.getByRole('textbox',{name:'给角色的消息'}).focus();await composerInFrame(page)
  expect(await content.evaluate(node=>node.scrollHeight<=node.clientHeight+1)).toBe(true)
  await page.setViewportSize({width:360,height:740});await composerInFrame(page);await page.screenshot({path:path.join(artifacts,'composer-saved-mobile.png')})
})
