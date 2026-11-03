import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
import {writeFileSync} from 'node:fs'
const artifactDir=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
let runUsername='',firstRunUsername=''
async function register(page:Page){
  const creating=!runUsername,name=runUsername||'e2e_'+Date.now().toString(36)
  await page.goto(creating?'/register':'/login')
  await page.getByLabel('用户名').fill(name)
  await page.getByLabel('密码').fill('test-password-e2e-123')
  await page.getByRole('button',{name:creating?'创建账号并进入':'登录',exact:true}).click()
  await expectLibraryLanding(page)
  runUsername=name
  if(!firstRunUsername)firstRunUsername=name
  return name
}
async function addDemo(page:Page){await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toBeVisible();if(await page.locator('.reader-body h1').innerText()!=='第一章 渡口'){await page.getByRole('button',{name:'打开目录',exact:true}).click();await page.getByRole('dialog').locator('.chapter-link').first().click()}await expect(page.getByRole('heading',{name:'第一章 渡口'})).toBeVisible();await page.waitForTimeout(3500)}
async function switchLine(page:Page,name:string){
  await page.getByRole('button',{name:'故事时间线',exact:true}).click()
  await expect(page.getByRole('heading',{name:'故事时间线',exact:true})).toBeVisible()
  await page.locator('.timeline-line-name').filter({hasText:name}).click()
  await expect(page.locator('.reader-title small')).toContainText(name+' ·')
  await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
}
for(const width of [360,390,430,1440])test(`UC01-UC04 complete deterministic flow ${width}px`,async({page})=>{
  test.setTimeout(120000);await page.setViewportSize({width,height:900});runUsername=''
  await register(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('确定性测试')
  await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-KEY-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'确定性测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await addDemo(page)
  expect(await page.locator('.reader-body').evaluate(element=>getComputedStyle(element).fontSize)).toBe(width>=1024?'20px':'18px')
  const originalUrl=page.url(),originalParagraphs=await page.locator('.reader-body .paragraph').allTextContents(),branchName='让林渡留下救阿岑';await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await page.getByLabel('希望接下来怎样写？').fill(branchName)
  await expect(page.getByRole('group',{name:'选择章节数量'})).toBeVisible()
  await expect(page.getByRole('heading',{name:'本次调用计划'})).toHaveCount(0)
  await page.getByRole('button',{name:/^开始写作/}).click();await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible({timeout:30000})
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();await page.goto('/settings');await page.getByRole('button',{name:'退出登录',exact:true}).click();await register(page)
  await page.goto(originalUrl);await expect(page.locator('.paragraph').nth(1)).toBeVisible();await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible();await expect(page.locator('.writing-result-origin')).toContainText('原文');await page.getByRole('button',{name:'接着读',exact:true}).click()
  await expect(page.locator('.reader-body')).toContainText('救出了阿岑');expect(page.url()).not.toBe(originalUrl)
  let branchUrl=page.url();await page.reload();await expect(page.locator('.reader-body')).toContainText('救出了阿岑')
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'为当前位置加书签',exact:true}).click();await expect(page.getByText('已添加段落书签',{exact:true})).toBeVisible();await page.getByRole('button',{name:'关闭',exact:true}).last().click()
  const timings:number[]=[]
  for(let i=0;i<30;i++){
    await page.getByRole('button',{name:'故事时间线',exact:true}).click()
    await expect(page.locator('.timeline-line-name').filter({hasText:i%2?branchName:'原文'})).toBeVisible()
    timings.push(await page.evaluate(async name=>{const start=performance.now();const button=Array.from(document.querySelectorAll('.timeline-line-name')).find(b=>b.textContent?.trim()===name) as HTMLButtonElement;if(!button)throw new Error('Target story line missing');button.click();await new Promise<void>((resolve,reject)=>{function ready(){if(performance.now()-start>3000){reject(new Error('Branch switch deadline exceeded'));return}if(document.querySelector('.reader-title small')?.textContent?.startsWith(name+' ·')&&document.querySelector('.reader-body .paragraph')&&!document.querySelector('.story-timeline'))requestAnimationFrame(()=>resolve());else requestAnimationFrame(ready)}ready()});return performance.now()-start},i%2?branchName:'原文'))
    if(i%2===0)expect(await page.locator('.reader-body .paragraph').allTextContents()).toEqual(originalParagraphs)
  }
  const branchP95=timings.slice().sort((a,b)=>a-b)[28],switchEvidence=JSON.stringify({width,samples:30,p95Ms:branchP95,values:timings,measurement:'Browser DOM click on rendered timeline line through actual router and reader load, next paint with target branch and paragraph; short fixture, warm backend/application. Opening the timeline is excluded.'},null,2);writeFileSync(path.join(artifactDir,`browser-branch-switch-${width}.json`),switchEvidence);writeFileSync(path.join(artifactDir,'browser-branch-switch.json'),switchEvidence);expect(branchP95).toBeLessThan(200)
  branchUrl=page.url()
  // A delayed old-branch search must not enter the newly selected reader.
  let releaseSearch!:()=>void,markSearchStarted!:()=>void
  const searchGate=new Promise<void>(resolve=>releaseSearch=resolve),searchStarted=new Promise<void>(resolve=>markSearchStarted=resolve)
  await page.route('**/api/reader/v1/books/*/search?*',async route=>{markSearchStarted();await searchGate;await route.fulfill({json:{items:[{snippet:'OLD_BRANCH_PRIVATE_RESULT',anchor:{branchId:'old',revisionId:'old',chapterId:'old',blockId:'old'}}]}})})
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'搜索已读内容',exact:true}).click();await page.getByLabel('关键词').fill('救');await page.getByRole('button',{name:'搜索',exact:true}).click();await searchStarted
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();await switchLine(page,'原文')
  releaseSearch();await page.waitForTimeout(150);await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'搜索已读内容',exact:true}).click();await expect(page.getByText('OLD_BRANCH_PRIVATE_RESULT',{exact:true})).toHaveCount(0);await page.getByRole('button',{name:'关闭',exact:true}).last().click();await page.unroute('**/api/reader/v1/books/*/search?*')
  await switchLine(page,branchName);branchUrl=page.url()
  await page.waitForTimeout(3500);await page.locator('.paragraph').first().click();await page.getByRole('button',{name:'聊角色',exact:true}).click()
  await expect(page.getByRole('button',{name:'和 TA 聊天'}).first()).toBeEnabled({timeout:30000});await page.getByRole('button',{name:'和 TA 聊天'}).first().click()
  await expect(page.locator('.chat-heading')).toContainText(branchName)
  await page.getByRole('textbox',{name:'给角色的消息'}).fill('此刻你在想什么？');await page.getByRole('button',{name:'发送消息',exact:true}).click();await expect(page.locator('.chat-message.assistant')).toBeVisible({timeout:30000})
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();expect(page.url()).toBe(branchUrl)
  await page.screenshot({path:path.join(artifactDir,`reader-complete-${width}.png`),fullPage:true})
})
for(const width of [360,390,430,1440])test(`F27 responsive reader ${width}px`,async({page})=>{await page.setViewportSize({width,height:900});await register(page);await addDemo(page);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.getByRole('button',{name:'打开目录'}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);await page.screenshot({path:path.join(artifactDir,`reader-${width}.png`),fullPage:true})})
test('F38 offline cached reading restores latest saved chapter and logout isolation',async({page,context})=>{
  await register(page)
  const firstResponse=page.waitForResponse(response=>response.request().method()==='GET'&&response.url().includes('/chapters/'))
  await addDemo(page);const firstChapter=(await(await firstResponse).json()).id,url=page.url()
  const saved=page.waitForResponse(response=>response.request().method()==='PUT'&&response.url().endsWith('/progress')&&response.request().postDataJSON().anchor.chapterId!==firstChapter)
  await page.getByRole('button',{name:'下一章',exact:true}).click();await expect(page.getByRole('heading',{name:'第二章 空船'})).toBeVisible();expect((await saved).ok()).toBeTruthy()
  await page.evaluate(async()=>{await navigator.serviceWorker.ready});expect(await page.evaluate(()=>localStorage.getItem('reader-account'))).toBeTruthy()
  await context.setOffline(true);await page.goto(url);await expect(page.getByRole('heading',{name:'第二章 空船'})).toBeVisible();await expect(page.getByText('离线阅读 · 仅显示此账号已缓存内容')).toBeVisible()
  await context.setOffline(false);await page.goto('/settings');await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible();await context.setOffline(true);await page.getByRole('button',{name:'退出登录',exact:true}).click();await expect(page).toHaveURL(/\/login$/)
  expect(await page.evaluate(()=>localStorage.getItem('reader-account'))).toBeNull()
  let refreshes=0;const count=(request:import('@playwright/test').Request)=>{if(request.url().endsWith('/auth/refresh'))refreshes++};page.on('request',count)
  await context.setOffline(false);await page.reload();await expect(page.getByRole('heading',{name:'回到你的故事'})).toBeVisible();await page.goto('/library');await expect(page).toHaveURL(/\/login$/);expect(refreshes).toBe(0);page.off('request',count)
  expect(await page.evaluate(()=>new Promise<number>((resolve,reject)=>{const request=indexedDB.open('story-reader-private');request.onerror=()=>reject(request.error);request.onsuccess=()=>{const db=request.result,count=db.transaction('cache').objectStore('cache').count();count.onsuccess=()=>{db.close();resolve(count.result)}}}))).toBe(0)
})

test('F10 shared browser cookies cannot replay another account private model configuration',async({page,context})=>{
  const current=await register(page),existing=firstRunUsername!==current?firstRunUsername:'';await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('old-tab-private-profile');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic');await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('OLD-TAB-TEST-ONLY-SECRET')
  const other=await context.newPage();await other.goto('/settings');await other.getByRole('button',{name:'退出登录',exact:true}).click();await expect(other).toHaveURL(/\/login$/);await other.goto(existing?'/login':'/register')
  await other.getByLabel('用户名').fill(existing||'tabs_'+Date.now().toString(36));await other.getByLabel('密码').fill('test-password-e2e-123');await other.getByRole('button',{name:existing?'登录':'创建账号并进入',exact:true}).click();await expect(other.getByRole('heading',{name:'你的书架'})).toBeVisible()
  let posts=0;const count=(request:import('@playwright/test').Request)=>{if(request.method()==='POST'&&request.url().endsWith('/model-profiles'))posts++};page.on('request',count)
  await page.getByRole('button',{name:'保存配置',exact:true}).click();await expect(page.getByRole('heading',{name:'回到你的故事'})).toBeVisible();expect(posts).toBe(1);page.off('request',count)
  await other.getByRole('link',{name:'我的',exact:true}).click();const loaded=other.waitForResponse(response=>response.request().method()==='GET'&&response.url().endsWith('/model-profiles'));await other.getByRole('link',{name:'打开模型设置与使用记录',exact:true}).click();expect((await(await loaded).json()).items.every((profile:{name:string})=>profile.name!=='old-tab-private-profile')).toBe(true);await expect(other.getByRole('heading',{name:'old-tab-private-profile 可用配置'})).toHaveCount(0)
})

for(const filename of ['bom-story.txt','mini-story.epub'])test('UC01 actual file import '+filename,async({page})=>{
  await register(page)
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click();await(await chooser).setFiles(path.resolve('../tests/fixtures',filename))
  await expect(page.getByRole('button',{name:'确认并开始阅读'})).toBeVisible({timeout:15000});await page.getByRole('button',{name:'确认并开始阅读'}).click()
  await expect(page.locator('.reader-body')).toContainText('甲😀乙');await expect(page.locator('.reader-body h1')).toHaveCount(1)
})

test('F12 F38 offline old progress requires explicit multi-device resolution',async({page,context,request})=>{
  const username=await register(page);await addDemo(page);const url=page.url(),bookId=new URL(url).pathname.split('/')[2]
  const login=await request.post('/api/reader/v1/auth/login',{data:{username,password:'test-password-e2e-123',clientType:'android',deviceName:'second-fixture-device'}})
  expect(login.ok()).toBeTruthy();const credentials=await login.json(),headers={Authorization:'Bearer '+credentials.accessToken}
  const branches=await(await request.get(`/api/reader/v1/books/${bookId}/branches`,{headers})).json(),branch=branches.items.find((b:{parentBranchId:string|null})=>!b.parentBranchId)
  const directory=await(await request.get(`/api/reader/v1/branches/${branch.id}/chapters`,{headers})).json()
  const first=await(await request.get(`/api/reader/v1/branches/${branch.id}/chapters/${directory.items[0].id}`,{headers})).json(),second=await(await request.get(`/api/reader/v1/branches/${branch.id}/chapters/${directory.items[1].id}`,{headers})).json()
  const original=await(await request.get(`/api/reader/v1/books/${bookId}/progress?branchId=${branch.id}&revisionId=${branch.headRevisionId}`,{headers})).json(),local=first.blocks[0].endAnchor,remote=second.blocks[0].endAnchor
  await page.evaluate(async({local,version})=>{await navigator.serviceWorker.ready;const db=await new Promise<IDBDatabase>((resolve,reject)=>{const opened=indexedDB.open('story-reader-private',1);opened.onsuccess=()=>resolve(opened.result);opened.onerror=reject});const tx=db.transaction('cache','readwrite');tx.objectStore('cache').put({anchor:local,version,sequence:987},localStorage.getItem('reader-account')+`:pending-progress:${local.branchId}:${local.revisionId}`);await new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onerror=reject});db.close()},{local,version:original.version})
  await context.setOffline(true);await page.goto(url);await expect(page.locator('.reader-body')).toBeVisible();await expect(page.locator('.progress-conflict')).toHaveCount(0)
  const moved=await request.put(`/api/reader/v1/books/${bookId}/progress`,{headers,data:{branchId:branch.id,revisionId:branch.headRevisionId,anchor:remote,expectedProgressVersion:original.version,deviceId:credentials.sessionId,clientSeq:1}});expect(moved.ok()).toBeTruthy()
  await context.setOffline(false);await page.reload();await expect(page.getByRole('heading',{name:'另一设备保存了新的位置'})).toBeVisible();await page.waitForTimeout(3500)
  const unchanged=await(await request.get(`/api/reader/v1/books/${bookId}/progress?branchId=${branch.id}&revisionId=${branch.headRevisionId}`,{headers})).json();expect(unchanged.anchor.blockId).toBe(remote.blockId)
  await page.getByRole('button',{name:'使用已保存位置',exact:true}).click();await expect(page.getByRole('heading',{name:'第二章 空船'})).toBeVisible();await expect(page.locator('.progress-conflict')).toHaveCount(0)
})
