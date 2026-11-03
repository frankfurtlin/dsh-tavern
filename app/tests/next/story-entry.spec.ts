import {test,expect,type Page} from '@playwright/test'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'
import {createHash} from 'node:crypto'

const artifacts=process.env.TEST_ARTIFACTS_DIR
if(!artifacts)throw new Error('An explicit isolated artifact directory is required')
mkdirSync(artifacts,{recursive:true})
const base='/api/reader/v1',worldBase='/api/world/v1'
const password='next-ui-fixture-password-20261004'
async function login(page:Page){
  const url=process.env.E2E_BASE_URL
  if(!['http://127.0.0.1:18820','http://127.0.0.1:18821','http://127.0.0.1:18822','http://127.0.0.1:18823','http://127.0.0.1:18824'].includes(url||''))throw new Error('Only an owned Next UI fixture is authorized')
  const health=await page.request.get(url+'/health/ready')
  expect(health.ok()).toBeTruthy();expect((await health.json()).aiMode).toBe('mock')
  const user='next_'+Date.now().toString(36)
  await page.goto('/register');await page.getByLabel('用户名').fill(user);await page.getByLabel('密码').fill(password)
  const response=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200).catch(()=>null)
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const login=await response;expect(login,'The real owned login must succeed').not.toBeNull()
  return {Authorization:'Bearer '+(await login!.json()).accessToken}
}
test('M1 story entry, actual pagination, independent world resume and no navigation calls',async({page})=>{
  test.setTimeout(180000)
  const headers=await login(page),observations:unknown[]=[]
  const profile=await page.request.post(base+'/model-profiles',{headers,data:{name:'Owned deterministic Next fixture',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'TEST-KEY-NOT-REAL',contextWindow:32768,callBudget:100,inputBudget:10000000,outputBudget:1000000}})
  expect(profile.status()).toBe(201)
  expect((await page.request.put(base+'/model-profiles/'+(await profile.json()).id+'/default',{headers})).ok()).toBeTruthy()
  const paragraph=(i:number)=>`这是第${i}段。梅舟在渡口读信，林渡把绳子收好。😀风从旧城吹来，船沿着岸慢慢前行。他们记得灯下的约定，也记得信封上的名字。`.repeat(i===1?40:4)
  const source='第一章 渡口\n\n'+Array.from({length:90},(_,i)=>paragraph(i+1)).join('\n\n')+'\n\n第二章 灯下\n\n他们第二天回到灯下，把最后一封信交给守灯人。'
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click()
  await(await chooser).setFiles({name:'Next-分页锚点.txt',mimeType:'text/plain',buffer:Buffer.from(source,'utf8')})
  await expect(page.getByRole('button',{name:'确认并开始阅读',exact:true})).toBeVisible({timeout:30000})
  await page.getByRole('button',{name:'确认并开始阅读',exact:true}).click()
  await expect(page.locator('.reader-body .paragraph').first()).toBeVisible({timeout:30000})
  const bookId=new URL(page.url()).pathname.split('/')[2],branchId=new URL(page.url()).searchParams.get('branch')!
  const bookBefore=await(await page.request.get(base+'/books/'+bookId,{headers})).json()
  const branchBefore=(await(await page.request.get(base+`/books/${bookId}/branches`,{headers})).json()).items.find((b:{id:string})=>b.id===branchId);expect(branchBefore?.headRevisionId).toBe(bookBefore.originalRevisionId)
  await page.locator('.paragraph').nth(8).scrollIntoViewIfNeeded();await page.waitForTimeout(3400)
  const storyId='book-'+bookId
  await page.goto('/library');await page.getByRole('link',{name:'进入故事',exact:true}).click()
  await expect(page).toHaveURL(new RegExp('/stories/'+storyId+'$'))
  await expect(page.getByRole('heading',{name:'Next-分页锚点'})).toBeVisible()
  await page.getByRole('button',{name:'继续阅读',exact:true}).click()
  await expect(page.locator('.reader-body')).toBeVisible()

  for(const mode of ['slide','page','scroll']){
    const saved=await(await page.request.get(base+'/stories/'+storyId,{headers})).json(),anchor=saved.resume.read.context.anchor
    await page.goto('/settings');await page.getByLabel('阅读方式',{exact:true}).selectOption(mode)
    await page.getByRole('button',{name:'保存阅读设置',exact:true}).click()
    await expect(page.getByRole('status')).toContainText('阅读设置已保存')
    const prefs=await(await page.request.get(base+'/settings',{headers})).json();expect(prefs.readingMode).toBe(mode)
    await page.goto('/stories/'+storyId);await page.getByRole('button',{name:'继续阅读',exact:true}).click()
    await expect(page.locator('.reader-body')).toBeVisible()
    await expect.poll(async()=>page.locator('#block-'+anchor.blockId).evaluate((el,offset)=>{const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),range=document.createRange();let consumed=0;while(walker.nextNode()){const node=walker.currentNode as Text;if(offset<=consumed+node.length){range.setStart(node,offset-consumed);range.collapse(true);break}consumed+=node.length}const rect=range.getClientRects()[0],view=el.closest('.reader-reading-viewport')!.querySelector('.reader-page-window')!.getBoundingClientRect();return !!rect&&rect.left>=view.left-2&&rect.left<=view.right+2},anchor.end)).toBe(true)
    const position=await page.locator('#block-'+anchor.blockId).evaluate((el,offset)=>{const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),range=document.createRange();let consumed=0;while(walker.nextNode()){const node=walker.currentNode as Text;if(offset<=consumed+node.length){range.setStart(node,offset-consumed);range.collapse(true);break}consumed+=node.length}const rect=range.getClientRects()[0]!,viewport=el.closest('.reader-reading-viewport')!.querySelector('.reader-page-window')!.getBoundingClientRect();return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,viewport:{left:viewport.left,right:viewport.right,top:viewport.top,bottom:viewport.bottom}}},anchor.end)
    expect(position.left).toBeGreaterThanOrEqual(position.viewport.left-2);expect(position.right).toBeLessThanOrEqual(position.viewport.right+2)
    if(mode!=='scroll'){
      expect(position.top).toBeGreaterThanOrEqual(position.viewport.top-2);expect(position.bottom).toBeLessThanOrEqual(position.viewport.bottom+2)
      await expect(page.getByRole('navigation',{name:'阅读翻页'})).toBeVisible()
      await expect(page.locator('.reader-page-controls span')).not.toHaveText('1 / 1 页')
      const old=await page.locator('.reader-page-controls span').innerText()
      await page.getByRole('button',{name:'下一页',exact:true}).click();await expect(page.locator('.reader-page-controls span')).not.toHaveText(old)
      await page.getByRole('button',{name:'上一页',exact:true}).click();await expect(page.locator('.reader-page-controls span')).toHaveText(old)
      await expect.poll(()=>page.locator('.reader-page-content').evaluate(element=>{
        const css=getComputedStyle(element),offset=parseFloat(css.getPropertyValue('--page-offset'))||0
        const actual=css.transform==='none'?0:new DOMMatrixReadOnly(css.transform).m41
        return Math.abs(actual+offset)<0.5
      })).toBe(true)
    }else await expect(page.getByRole('navigation',{name:'阅读翻页'})).toHaveCount(0)
    await page.screenshot({path:path.join(artifacts,mode+'-reading.png'),fullPage:false})
    observations.push({mode,position,settings:prefs})
  }
  // World preparation/action uses this fixture's real PostgreSQL and workers;
  // the deterministic provider is the only substituted layer.
  const directory=await(await page.request.get(base+`/branches/${branchId}/chapters?revisionId=${bookBefore.originalRevisionId}`,{headers})).json()
  const chapter=await(await page.request.get(base+`/branches/${branchId}/chapters/${directory.items[0].id}?revisionId=${bookBefore.originalRevisionId}`,{headers})).json()
  const longBlock=chapter.blocks[0],text=longBlock.text as string
  let cursor=901;const split=(at:number)=>text.charCodeAt(at-1)>=0xd800&&text.charCodeAt(at-1)<=0xdbff&&text.charCodeAt(at)>=0xdc00&&text.charCodeAt(at)<=0xdfff
  if(split(cursor))cursor++
  let left=cursor-32,right=cursor+32;if(split(left))left--;if(split(right))right++
  const exactAnchor={...longBlock.endAnchor,start:cursor,end:cursor,quoteHash:createHash('sha256').update(JSON.stringify(['anchor-v1',chapter.revisionId,longBlock.id,cursor,text.slice(left,cursor),text.slice(cursor,right)])).digest('hex')}
  const validation=await page.request.post(base+'/stories/'+storyId+'/context/validate',{headers,data:{storyId,mode:'read',perspective:'reader',entryOrigin:'reader',bookId,readerBranchId:branchId,anchor:exactAnchor}})
  expect(validation.status(),await validation.text()).toBe(200)
  for(const mode of ['slide','page','scroll']){
    const prefs=await(await page.request.get(base+'/settings',{headers})).json()
    const saved=await page.request.put(base+'/settings',{headers,data:{...prefs,version:undefined,expectedVersion:prefs.version,readingMode:mode}})
    expect(saved.status(),await saved.text()).toBe(200)
    await page.goto(`/books/${bookId}/read?`+new URLSearchParams({branch:branchId,revision:chapter.revisionId,chapter:chapter.id,anchor:JSON.stringify(exactAnchor),story:storyId}))
    await expect(page.locator('.reader-body')).toBeVisible()
    await expect.poll(async()=>page.locator('#block-'+longBlock.id).evaluate((el,offset)=>{const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),range=document.createRange();let consumed=0;while(walker.nextNode()){const node=walker.currentNode as Text;if(offset<=consumed+node.length){range.setStart(node,offset-consumed);range.collapse(true);break}consumed+=node.length}const rect=range.getClientRects()[0]!,view=el.closest('.reader-page-window')!.getBoundingClientRect();return rect.left>=view.left-2&&rect.left<=view.right+2&&(el.closest('.reader-paged')?rect.top>=view.top-2&&rect.bottom<=view.bottom+2:rect.top>=0&&rect.bottom<=innerHeight)},cursor)).toBe(true)
    observations.push({kind:'long-paragraph-middle',mode,utf16:cursor,blockLength:text.length})
  }
  // A real SPA navigation must reload a different chapter on the same mounted
  // ReaderPage, including the links used by the role conversation's source.
  const second=await(await page.request.get(base+`/branches/${branchId}/chapters/${directory.items[1].id}?revisionId=${bookBefore.originalRevisionId}`,{headers})).json()
  await page.getByRole('button',{name:'打开目录',exact:true}).click();await expect(page.getByRole('dialog',{name:'目录',exact:true})).toBeVisible()
  await page.evaluate(async({branch,revision,chapter,block,story})=>{
    const application=(document.querySelector('#app') as any).__vue_app__
    await application.config.globalProperties.$router.push({path:location.pathname,query:{branch,revision,chapter,block,edge:'start',story}})
  },{branch:branchId,revision:second.revisionId,chapter:second.id,block:second.blocks[0].id,story:storyId})
  await expect(page.locator('#block-'+second.blocks[0].id)).toBeVisible();await expect(page.locator('#block-'+longBlock.id)).toHaveCount(0)
  await expect(page.getByRole('dialog',{name:'目录',exact:true})).toHaveCount(0)
  observations.push({kind:'same-reader-source-navigation',chapterId:second.id,oldChapterCleared:true,oldInteractionCleared:true})
  await page.getByRole('button',{name:'返回故事入口并保存进度',exact:true}).click()
  await expect(page).toHaveURL(new RegExp('/stories/'+storyId+'$'))
  // The conflict is created by another real HTTP client rather than a mocked
  // rejection. Refresh must keep this browser's draft and use the new CAS.
  const beforeEdit=await(await page.request.get(base+'/books/'+bookId,{headers})).json()
  await page.getByRole('button',{name:'管理故事',exact:true}).click();const manager=page.getByRole('dialog',{name:'管理故事',exact:true})
  await manager.getByLabel('故事名称').fill('Next-本机名称草稿')
  const identity=await(await page.request.get(base+'/auth/me',{headers})).json()
  const other=await page.context().browser()!.newContext({baseURL:process.env.E2E_BASE_URL})
  let secondSession=''
  try{
    const login=await other.request.post(base+'/auth/login',{data:{username:identity.username,password,clientType:'web',deviceName:'Next-second-browser-session'}})
    expect(login.status(),await login.text()).toBe(200);const credentials=await login.json();secondSession=credentials.sessionId
    expect(secondSession).not.toBe(identity.sessionId)
    const remoteEdit=await other.request.patch(base+'/books/'+bookId,{headers:{Authorization:'Bearer '+credentials.accessToken},data:{expectedVersion:beforeEdit.version,title:'Next-另一设备名称'}})
    expect(remoteEdit.status(),await remoteEdit.text()).toBe(200)
  }finally{await other.close()}
  await manager.getByRole('button',{name:'保存名称',exact:true}).click();await expect(manager.getByRole('button',{name:'载入最新信息',exact:true})).toBeVisible()
  await expect(manager.getByLabel('故事名称')).toHaveValue('Next-本机名称草稿');await expect(manager.getByRole('button',{name:'保存名称',exact:true})).toBeDisabled()
  await manager.getByRole('button',{name:'载入最新信息',exact:true}).click();await expect(manager.getByRole('status')).toContainText('Next-另一设备名称')
  await expect(manager.getByLabel('故事名称')).toHaveValue('Next-本机名称草稿');await manager.getByRole('button',{name:'保存名称',exact:true}).click();await expect(manager).toHaveCount(0)
  await expect(page.getByRole('heading',{name:'Next-本机名称草稿',exact:true})).toBeVisible()
  observations.push({kind:'two-browser-session-management-conflict',sessionIds:[identity.sessionId,secondSession],localDraftRetained:true,explicitRefresh:true})
  const anchor=chapter.blocks[0].endAnchor
  const progress=await(await page.request.get(base+`/books/${bookId}/progress?branchId=${branchId}&revisionId=${anchor.revisionId}`,{headers})).json()
  expect(progress.maxObservedAnchor?.branchId).toBe(branchId)
  await page.goto('/stories/'+storyId)
  const created=await page.request.post(worldBase+'/worlds',{headers:{...headers,'Idempotency-Key':'next-owned-world'},data:{anchor,rules:{}}})
  expect(created.status()).toBe(202);const {worldId}=await created.json()
  await expect.poll(async()=> (await(await page.request.get(worldBase+'/worlds/'+worldId,{headers})).json()).status,{timeout:45000}).toBe('active')
  const world=await(await page.request.get(worldBase+'/worlds/'+worldId,{headers})).json(),character=world.characters.find((c:{available:boolean})=>c.available)
  const begun=await page.request.post(worldBase+'/worlds/'+worldId+'/begin',{headers:{...headers,'Idempotency-Key':'next-owned-begin'},data:{characterId:character.id}})
  expect(begun.status()).toBe(202);const {lineId}=await begun.json()
  await expect.poll(async()=> (await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()).head?.sequence,{timeout:45000}).toBe(1)
  const worldListBefore=await(await page.request.get(worldBase+'/worlds',{headers})).json()
  const usageBefore=await(await page.request.get(base+'/usage',{headers})).json()
  await page.goto('/stories/'+storyId);await page.getByRole('button',{name:'继续游玩',exact:true}).click()
  await expect(page).toHaveURL(new RegExp(`/worlds/${worldId}/play\\?line=${lineId}`))
  await expect(page.locator('.world-compose textarea')).toBeVisible()
  await page.getByRole('link',{name:'返回故事入口',exact:true}).click()
  await expect(page).toHaveURL(new RegExp('/stories/'+storyId+'$'))
  const lineBeforeRead=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()
  const worldHistory=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}/history`,{headers})).json()
  const readable=worldHistory.items.find((cp:{text:string;kind:string})=>cp.text&&cp.kind!=='start');expect(readable).toBeTruthy()
  await page.goto(`/worlds/${worldId}/read?`+new URLSearchParams({line:lineId,checkpoint:readable.id,story:storyId}))
  await expect(page.locator('.world-read-chapter').first()).toBeVisible()
  await page.getByRole('button',{name:'返回故事入口',exact:true}).click();await expect(page).toHaveURL(new RegExp('/stories/'+storyId+'$'))
  const lineAfterRead=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()
  expect(lineAfterRead.headCheckpointId).toBe(lineBeforeRead.headCheckpointId)
  expect(lineAfterRead.readingPosition.checkpointId).toBe(readable.id)
  observations.push({kind:'world-read-return-origin',readingPositionSaved:true,worldHeadUnchanged:true})
  await page.getByRole('button',{name:'继续游玩',exact:true}).click()
  await expect(page.getByRole('link',{name:'阅读',exact:true})).toHaveAttribute('href',new RegExp('story='+storyId))
  await page.getByRole('link',{name:'阅读',exact:true}).click()
  await expect(page.getByRole('link',{name:'世界线',exact:true})).toHaveAttribute('href',new RegExp('story='+storyId))
  await page.getByRole('link',{name:'世界线',exact:true}).click()
  await expect(page.getByRole('link',{name:'回到故事',exact:true})).toHaveAttribute('href',new RegExp('story='+storyId))
  await page.getByRole('button',{name:readable.title+'，查看历史节点',exact:true}).click()
  await expect(page.getByRole('link',{name:'阅读这里',exact:true})).toHaveAttribute('href',new RegExp('story='+storyId))
  await page.getByRole('link',{name:'阅读这里',exact:true}).click()
  await expect(page.locator('.world-read-chapter')).toBeVisible()
  await page.getByRole('button',{name:'返回故事入口',exact:true}).click()
  await expect(page).toHaveURL(new RegExp('/stories/'+storyId+'$'))
  const afterViews=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()
  expect(afterViews.headCheckpointId).toBe(lineBeforeRead.headCheckpointId)
  observations.push({kind:'play-read-graph-history-origin',storyId,worldHeadUnchanged:true,originValidatedOnEveryView:true})
  for(const viewport of [{width:320,height:740},{width:375,height:812},{width:390,height:844},{width:768,height:1024},{width:1024,height:768},{width:1440,height:900},{width:800,height:477}]){
    await page.setViewportSize(viewport);await expect(page.getByRole('button',{name:'继续游玩',exact:true})).toBeVisible()
    const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,hits:[...document.querySelectorAll('.story-entry button,.story-entry a')].map(el=>{const r=el.getBoundingClientRect();return {label:el.textContent,w:r.width,h:r.height}})}))
    expect(layout.scroll).toBeLessThanOrEqual(layout.width+1);for(const hit of layout.hits){expect(hit.w).toBeGreaterThanOrEqual(44);expect(hit.h).toBeGreaterThanOrEqual(44)}
    if(viewport.width===768){const labels=await page.locator('.app-header nav a').evaluateAll(elements=>elements.map(element=>({label:element.textContent,whiteSpace:getComputedStyle(element).whiteSpace})));expect(labels).toHaveLength(5);expect(labels.every(label=>label.whiteSpace==='nowrap')).toBe(true)}
    observations.push({viewport,layout});await page.screenshot({path:path.join(artifacts,`entry-${viewport.width}.png`),fullPage:false})
  }
  const usageAfter=await(await page.request.get(base+'/usage',{headers})).json(),worldListAfter=await(await page.request.get(worldBase+'/worlds',{headers})).json()
  expect(worldListAfter.items.map((w:{id:string})=>w.id)).toEqual(worldListBefore.items.map((w:{id:string})=>w.id))
  expect(usageAfter).toEqual(usageBefore)
  const bookAfter=await(await page.request.get(base+'/books/'+bookId,{headers})).json(),branchAfter=(await(await page.request.get(base+`/books/${bookId}/branches`,{headers})).json()).items.find((b:{id:string})=>b.id===branchId)
  expect(bookAfter.originalRevisionId).toBe(bookBefore.originalRevisionId);expect(branchAfter.headRevisionId).toBe(branchBefore.headRevisionId)
  writeFileSync(path.join(artifacts,'observations.json'),JSON.stringify({testType:'browser-mock-provider-postgresql',providerCallsReal:0,worldIdsUnchanged:true,usageUnchangedDuringNavigation:true,sourceHeadUnchanged:true,fixtureAccountCleanup:'deferred to owned isolated project teardown; account not deleted during this test',observations},null,2))
})
