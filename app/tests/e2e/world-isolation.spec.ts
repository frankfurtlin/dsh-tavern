import {expectSavedInline,readSavedInline} from './helpers/landing'
import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import type {Anchor,Bookmark,Chapter,Progress,RoleSession} from '../../client/src/api/types'
import type {WorldCheckpoint,WorldInfo,WorldLine} from '../../client/src/world/types'

// These tests use a newly registered account and the deterministic provider.
// X13 intercepts only the frontend config response; it is not a backend flag test.
// D14 covers position isolation here; source deletion has a separate backend test.
type ReaderFixture={headers:Record<string,string>;readerUrl:string;chapter:Chapter;anchor:Anchor;progressPath:string}

async function getJson<T>(page:Page,url:string,headers:Record<string,string>):Promise<T>{
  const response=await page.request.get(url,{headers})
  expect(response.ok(),`GET ${url}: ${response.status()}`).toBeTruthy()
  return response.json()
}

async function prepareReader(page:Page):Promise<ReaderFixture>{
  await page.goto('/register')
  await page.getByLabel('用户名').fill('wi_'+Date.now().toString(36)+'_'+randomUUID().slice(0,6))
  await page.getByLabel('密码').fill('world-isolation-fixture-123')
  const login=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/reader/v1/auth/login')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  const credentials=await(await login).json()
  const headers={Authorization:'Bearer '+credentials.accessToken}
  await expectLibraryLanding(page)
  // Refuse to exercise AI flows on a server running real providers.
  await expect(page.locator('.mock-banner')).toContainText('确定性 mock')
  await page.goto('/settings/models')
  await page.getByRole('button',{name:'添加通道',exact:true}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('位置隔离测试')
  await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('world-isolation-fixture')
  await page.getByLabel('API Key').fill('FIXTURE-ISOLATION-KEY-NOT-REAL')
  await page.getByRole('button',{name:'保存配置',exact:true}).click()
  await expect(page.getByRole('heading',{name:'位置隔离测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library')
  const opening=page.waitForResponse(response=>response.request().method()==='GET'&&/\/api\/reader\/v1\/branches\/[^/]+\/chapters\/[^/]+$/.test(new URL(response.url()).pathname))
  await page.getByRole('button',{name:'加入示例书',exact:true}).click()
  const chapter:Chapter=await(await opening).json()
  await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
  const anchor=chapter.blocks[0].endAnchor
  const progressPath=`/api/reader/v1/books/${anchor.bookId}/progress?branchId=${anchor.branchId}&revisionId=${anchor.revisionId}`
  await page.locator('.reader-body .paragraph').last().scrollIntoViewIfNeeded()
  await expect.poll(async()=>(await getJson<Progress>(page,progressPath,headers)).maxObservedAnchor?.blockId,{timeout:15000}).toBe(chapter.blocks.at(-1)!.id)
  return{headers,readerUrl:page.url(),chapter,anchor,progressPath}
}

async function expectReaderAnchor(page:Page,anchor:Anchor){
  await expect.poll(()=>{
    const url=new URL(page.url())
    return{path:url.pathname,branch:url.searchParams.get('branch'),revision:url.searchParams.get('revision'),chapter:url.searchParams.get('chapter'),block:url.searchParams.get('block')||(url.searchParams.get('anchor')?JSON.parse(url.searchParams.get('anchor')!).blockId:null)}
  }).toEqual({path:`/books/${anchor.bookId}/read`,branch:anchor.branchId,revision:anchor.revisionId,chapter:anchor.chapterId,block:anchor.blockId})
  await expect(page.locator('#block-'+anchor.blockId)).toBeInViewport()
}

async function openRoleChat(page:Page):Promise<RoleSession>{
  await page.getByRole('button',{name:'聊角色',exact:true}).click()
  const talk=page.getByRole('button',{name:'和 TA 聊天',exact:true}).first()
  await expect(talk).toBeEnabled({timeout:45000})
  const creating=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname.endsWith('/character-chats'))
  await talk.click()
  const session:RoleSession=await(await creating).json()
  await expect(page.getByRole('textbox',{name:'给角色的消息'})).toBeEnabled()
  return session
}

async function sendRoleMessage(page:Page,message:string){
  await page.getByRole('textbox',{name:'给角色的消息'}).fill(message)
  await page.getByRole('button',{name:'发送消息',exact:true}).click()
  await expect(page.locator('.chat-message.assistant:not([data-chat-live="true"]) .chat-message-text')).toBeVisible({timeout:30000})
  await expect(page.locator('.chat-message.assistant')).toHaveAttribute('data-stream-status',/^(saved|review_warning)$/)
  await expect(page.getByRole('button',{name:'停止回复',exact:true})).toHaveCount(0,{timeout:30000})
  return page.locator('.chat-message.assistant .chat-message-text').innerText()
}

async function enterWorld(page:Page,fixture:ReaderFixture){
  await page.getByRole('button',{name:'阅读工具',exact:true}).click()
  await page.getByRole('button',{name:'从这里进入故事',exact:true}).click()
  await expect(page.getByRole('heading',{name:'走进一个故事'})).toBeVisible()
  await page.getByRole('button',{name:'建立世界',exact:true}).click()
  await expect(page.getByRole('heading',{name:'你想成为谁？'})).toBeVisible({timeout:30000})
  await page.getByRole('button',{name:'进入故事',exact:true}).click()
  await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:30000})
  const playUrl=page.url(),url=new URL(playUrl),worldId=url.pathname.split('/')[2]
  const world=await getJson<WorldInfo>(page,`/api/world/v1/worlds/${worldId}`,fixture.headers)
  const lineId=url.searchParams.get('line')||world.activeLineId
  expect(lineId).toBeTruthy()
  return{playUrl,worldId,lineId:lineId!,linePath:`/api/world/v1/worlds/${worldId}/lines/${lineId}`}
}

test('D14: original progress, world reading, viewed history and action head stay independent',async({page},testInfo)=>{
  test.setTimeout(180000)
  await page.setViewportSize({width:390,height:844})
  await page.emulateMedia({reducedMotion:'reduce'})
  const fixture=await prepareReader(page),world=await enterWorld(page,fixture)
  for(const message of ['我询问梅舟信的来历。','我观察渡口旁的道路。']){
    const before=await page.locator('.world-scene').count()
    await page.getByLabel('你的下一步行动').fill(message)
    await page.getByRole('button',{name:'提交行动',exact:true}).click()
    await expect(page.locator('.world-scene')).toHaveCount(before+1,{timeout:30000})
    await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:30000})
  }
  const actionLine=await getJson<WorldLine>(page,world.linePath,fixture.headers)
  expect(actionLine.pendingTaskId).toBeNull()
  const history=await getJson<{items:WorldCheckpoint[]}>(page,world.linePath+'/history',fixture.headers)
  const scenes=history.items.filter(cp=>cp.text&&cp.kind!=='start')
  expect(scenes).toHaveLength(3)
  const viewed=scenes[0],reading=scenes[1]
  expect(new Set([viewed.id,reading.id,actionLine.headCheckpointId]).size).toBe(3)
  const oldProgress=await getJson<Progress>(page,fixture.progressPath,fixture.headers)
  let readonlyWorldPosts=0
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.startsWith('/api/world/v1/'))readonlyWorldPosts++})

  await page.getByRole('link',{name:'阅读',exact:true}).click()
  await expect(page.locator('.world-read-chapter')).toHaveCount(3)
  const readUrl=page.url()
  const saving=page.waitForResponse(response=>{
    if(response.request().method()!=='PATCH'||new URL(response.url()).pathname!==world.linePath)return false
    return response.request().postDataJSON().readingPosition?.checkpointId===reading.id
  })
  await page.getByRole('button',{name:'世界正文目录',exact:true}).click()
  await page.getByRole('dialog').locator('.chapter-link').nth(1).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect((await saving).ok()).toBeTruthy()
  const readLine=await getJson<WorldLine>(page,world.linePath,fixture.headers)
  expect(readLine.readingPosition.checkpointId).toBe(reading.id)
  expect(readLine.readingPosition.scroll).toBeGreaterThan(0)
  expect(readLine.headCheckpointId).toBe(actionLine.headCheckpointId)
  expect(readLine.head).toEqual(actionLine.head)
  const readingPosition=readLine.readingPosition
  const assertUnchanged=async()=>{
    const line=await getJson<WorldLine>(page,world.linePath,fixture.headers)
    expect(line.headCheckpointId).toBe(actionLine.headCheckpointId)
    expect(line.head).toEqual(actionLine.head)
    expect(line.pendingTaskId).toBeNull()
    expect(line.readingPosition).toEqual(readingPosition)
    const progress=await getJson<Progress>(page,fixture.progressPath,fixture.headers)
    expect(progress.anchor).toEqual(oldProgress.anchor)
    expect(progress.maxObservedAnchor).toEqual(oldProgress.maxObservedAnchor)
    expect(readonlyWorldPosts).toBe(0)
  }

  await page.getByRole('link',{name:'游玩',exact:true}).click()
  await expect(page.locator('.world-scene')).toHaveCount(3)
  const viewing=page.waitForResponse(response=>response.request().method()==='GET'&&new URL(response.url()).pathname===`/api/world/v1/worlds/${world.worldId}/checkpoints/${viewed.id}`)
  await page.locator('.world-scene').first().getByRole('button',{name:/历史操作/}).click()
  expect((await(await viewing).json()).id).toBe(viewed.id)
  await expect(page.getByRole('dialog')).toContainText('回合 '+viewed.sequence)
  await assertUnchanged()
  await page.getByRole('button',{name:'查看这一刻的世界',exact:true}).click()
  await expect(page.getByRole('dialog').locator('.world-player-card')).toBeVisible()
  await assertUnchanged()
  await page.getByRole('button',{name:'关闭',exact:true}).click()

  await page.getByRole('link',{name:'阅读',exact:true}).click()
  await expect(page.locator('.world-read-chapter')).toHaveCount(3)
  await expect.poll(()=>page.locator('.world-read-scroll').evaluate(element=>element.scrollTop)).toBeCloseTo(readingPosition.scroll!,0)
  await expect(page.locator(`[data-checkpoint="${reading.id}"]`)).toBeInViewport()
  await assertUnchanged()

  // Move only the original reader. A world reading restore must not use it.
  await page.getByRole('link',{name:'游玩',exact:true}).click()
  await expect(page.getByLabel('你的下一步行动')).toBeVisible()
  await page.getByRole('button',{name:'世界操作',exact:true}).click()
  await page.getByRole('link',{name:'回到来源小说',exact:true}).click()
  await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
  const progressSaved=page.waitForResponse(response=>{
    if(response.request().method()!=='PUT'||new URL(response.url()).pathname!==`/api/reader/v1/books/${fixture.anchor.bookId}/progress`)return false
    return response.request().postDataJSON().anchor.chapterId!==fixture.chapter.id
  })
  await page.getByRole('button',{name:'下一章',exact:true}).click()
  await expect(page.locator('.reader-body h1')).toHaveText('第二章 空船')
  expect((await progressSaved).ok()).toBeTruthy()
  await page.getByRole('button',{name:'返回故事入口并保存进度',exact:true}).click()
  await expect(page).toHaveURL(new RegExp('/stories/book-'+fixture.anchor.bookId+'$'))
  const movedProgress=await getJson<Progress>(page,fixture.progressPath,fixture.headers)
  expect(movedProgress.anchor?.chapterId).not.toBe(fixture.chapter.id)
  await page.goto(readUrl)
  await expect(page.locator('.world-read-chapter')).toHaveCount(3)
  await expect.poll(()=>page.locator('.world-read-scroll').evaluate(element=>element.scrollTop)).toBeCloseTo(readingPosition.scroll!,0)
  const finalLine=await getJson<WorldLine>(page,world.linePath,fixture.headers)
  expect(finalLine.readingPosition).toEqual(readingPosition)
  expect(finalLine.head).toEqual(actionLine.head)
  expect(finalLine.headCheckpointId).toBe(actionLine.headCheckpointId)
  expect((await getJson<Progress>(page,fixture.progressPath,fixture.headers)).anchor).toEqual(movedProgress.anchor)
  expect(readonlyWorldPosts).toBe(0)
  await testInfo.attach('four-independent-positions',{contentType:'application/json',body:Buffer.from(JSON.stringify({originalBefore:oldProgress.anchor,originalAfter:movedProgress.anchor,worldReading:readingPosition,viewedHistory:{id:viewed.id,sequence:viewed.sequence},actionHead:{id:actionLine.headCheckpointId,sequence:actionLine.head.sequence},readonlyWorldPosts},null,2))})
})

test('R11: saved notes and conversation history return through their actual location links',async({page})=>{
  test.setTimeout(150000)
  await page.setViewportSize({width:1440,height:950})
  const fixture=await prepareReader(page)
  await page.locator('.reader-body .paragraph').nth(1).click()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click()
  await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
  const noteText='位置隔离：记住这封信在原文里的段落。'
  await page.getByLabel('我的阅读笔记').fill(noteText)
  const saved=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname.endsWith('/bookmarks'))
  await page.getByRole('button',{name:'保存笔记',exact:true}).click()
  const note:Bookmark=await(await saved).json()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.goto('/notebook')
  const card=page.locator('.note-card').filter({hasText:noteText})
  await expect(card).toHaveCount(1)
  await card.getByRole('button',{name:'回到这一段',exact:true}).click()
  await expectReaderAnchor(page,note.anchor)

  await page.locator('#block-'+note.anchor.blockId).click()
  const session=await openRoleChat(page)
  expect(session.pinnedAnchor).toEqual(note.anchor)
  await expect.poll(async()=>{
    const response=await page.request.get('/api/reader/v1/stories/book-'+session.bookId,{headers:fixture.headers})
    return response.ok()?(await response.json()).resume.chat.context?.sessionId:null
  }).toBe(session.id)
  let sends=0
  page.on('request',request=>{if(request.method()==='POST'&&/\/role-sessions\/[^/]+\/(send|turns)$/.test(new URL(request.url()).pathname))sends++})
  const message='此刻这封信让你想到了什么？',reply=await sendRoleMessage(page,message)
  expect(reply.length).toBeGreaterThan(0)
  await page.getByRole('button',{name:'关闭',exact:true}).last().click()
  const reopen=async()=>{
    await page.goto('/conversations')
    await page.locator('.chat-sidebar button').filter({hasText:session.characterName}).click()
    await expect(page.locator('.chat-message.user .chat-message-text')).toHaveText(message)
    await expect(page.locator('.chat-message.assistant .chat-message-text')).toHaveText(reply)
  }
  await reopen()
  await page.getByRole('link',{name:'回到相遇段落',exact:true}).click()
  await expectReaderAnchor(page,session.pinnedAnchor)
  await reopen()
  expect(sends).toBe(1)
  // Opening a different pinned conversation from ReaderPage must replace the
  // previously valid chat target even when no message has been sent in it.
  await page.goto(fixture.readerUrl);await page.locator('.reader-body .paragraph').first().click()
  const nextSession=await openRoleChat(page)
  expect(nextSession.id).not.toBe(session.id)
  await expect.poll(async()=>{
    const response=await page.request.get('/api/reader/v1/stories/book-'+session.bookId,{headers:fixture.headers})
    return response.ok()?(await response.json()).resume.chat.context?.sessionId:null
  }).toBe(nextSession.id)
  expect(sends).toBe(1)
})

test('X13 frontend config disabled: old reading, writing and saved chat still complete',async({page},testInfo)=>{
  test.setTimeout(180000)
  await page.setViewportSize({width:390,height:844})
  let configReads=0,worldPosts=0
  await page.route('**/api/world/v1/config',route=>{configReads++;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({enabled:false,ready:false})})})
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.startsWith('/api/world/v1/'))worldPosts++})
  const fixture=await prepareReader(page)
  expect(configReads).toBeGreaterThan(0)
  await page.getByRole('button',{name:'阅读工具',exact:true}).click()
  await expect(page.getByRole('button',{name:'从这里进入故事',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'为当前位置加书签',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'关闭',exact:true}).click()
  await page.getByRole('button',{name:'下一章',exact:true}).click()
  await expect(page.locator('.reader-body h1')).toHaveText('第二章 空船')
  await page.getByRole('button',{name:'上一章',exact:true}).click()
  await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')

  await page.locator('.reader-body .paragraph').nth(1).click()
  await page.getByRole('button',{name:'从这里写',exact:true}).click()
  await page.getByRole('textbox',{name:/希望接下来怎样写/}).fill('让林渡留下来救阿岑，保留这条新走向。')
  await page.getByRole('button',{name:/^开始写作/}).click()
  await expectSavedInline(page)
  await readSavedInline(page)
  await expect(page.locator('.reader-body [data-content-source="ai"]').first()).toBeVisible({timeout:15000})
  expect(page.url()).not.toBe(fixture.readerUrl)

  await page.locator('.reader-body .paragraph').first().click()
  const session=await openRoleChat(page)
  const message='世界入口关闭时，这段对话仍应独立保存。',reply=await sendRoleMessage(page,message)
  await page.getByRole('button',{name:'关闭',exact:true}).last().click()
  await page.goto('/conversations')
  await expect(page.getByRole('link',{name:'世界',exact:true})).toHaveCount(0)
  await page.locator('.chat-sidebar button').filter({hasText:session.characterName}).click()
  await expect(page.locator('.chat-message.user .chat-message-text')).toHaveText(message)
  await expect(page.locator('.chat-message.assistant .chat-message-text')).toHaveText(reply)
  expect(worldPosts).toBe(0)
  await testInfo.attach('frontend-disabled-scope',{contentType:'application/json',body:Buffer.from(JSON.stringify({scope:'Frontend config response interception; does not change or validate backend feature flag',configReads,worldPosts,legacyWritingAccepted:true,legacyChatReopened:true},null,2))})
})
