import {test,expect} from '@playwright/test'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'

const artifacts=process.env.TEST_ARTIFACTS_DIR
if(!artifacts)throw new Error('A new isolated artifact directory is required')
mkdirSync(artifacts,{recursive:true})
const base='/api/reader/v1',worldBase='/api/world/v1'

test('M1 standalone world: no book/model preparation, explicit opening, public publication and entry resume',async({page})=>{
  test.setTimeout(150000)
  const url=process.env.E2E_BASE_URL||''
  if(!/^http:\/\/127\.0\.0\.1:1882[3-9]$/.test(url))throw new Error('Only a new owned Next mock fixture is allowed')
  const health=await page.request.get(url+'/health/ready');expect(health.ok()).toBeTruthy();expect((await health.json()).aiMode).toBe('mock')
  await page.goto('/register');await page.getByLabel('用户名').fill('standalone_'+Date.now().toString(36));await page.getByLabel('密码').fill('standalone-fixture-password-20261005')
  const login=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const headers={Authorization:'Bearer '+(await(await login).json()).accessToken}
  const booksBefore=(await(await page.request.get(base+'/books',{headers})).json()).items
  expect(booksBefore).toHaveLength(0)
  const templates=(await(await page.request.get(worldBase+'/templates',{headers})).json()).items
  expect(templates).toHaveLength(3)
  expect(templates.map((t:{genre:string})=>t.genre).sort()).toEqual(['fantasy','mystery','romance'])
  await page.goto('/worlds/new');await expect(page.getByRole('button',{name:'建立新故事',exact:true})).toHaveAttribute('aria-pressed','true')
  const selected=templates.find((t:{genre:string})=>t.genre==='mystery')
  await page.getByRole('button',{name:new RegExp(selected.title)}).click()
  for(const width of [320,390,768,1440]){
    await page.setViewportSize({width,height:width===1440?900:844});await page.evaluate(()=>window.scrollTo(0,0))
    await expect(page.locator('.world-template')).toHaveCount(3)
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    for(const box of await page.locator('.world-source-tabs button,.world-template').evaluateAll(elements=>elements.map(e=>{const r=e.getBoundingClientRect();return {width:r.width,height:r.height}}))){expect(box.width).toBeGreaterThanOrEqual(44);expect(box.height).toBeGreaterThanOrEqual(44)}
    await page.screenshot({path:path.join(artifacts,`standalone-start-${width}.png`)})
  }
  await page.getByLabel('世界名称',{exact:true}).fill('Next-独立雨夜')
  const createdPromise=page.waitForResponse(r=>r.url().endsWith(worldBase+'/worlds')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'建立世界',exact:true}).click()
  const created=await createdPromise;expect(created.status()).toBe(202);const receipt=await created.json()
  expect(receipt.taskId).toBeNull();expect(receipt.status).toBe('active')
  await expect(page.getByRole('heading',{name:'你想成为谁？'})).toBeVisible()
  const world=(await(await page.request.get(worldBase+'/worlds/'+receipt.worldId,{headers})).json())
  expect(world.sourceKind).toBe('standalone');expect(world.sourceBookId).toBeNull();expect(world.sourceAnchor).toBeNull();expect(world.sourceRemoved).toBe(false)
  expect((await(await page.request.get(base+'/books',{headers})).json()).items).toHaveLength(0)
  const storyId='world-'+world.id
  const beforeOpening=await(await page.request.get(base+'/stories/'+storyId,{headers})).json();expect(beforeOpening.source).toBeNull();expect(beforeOpening.resume.play.available).toBe(false)
  // There is no model profile during template creation. Only the user's next
  // explicit opening starts the standard bounded mock generation pipeline.
  const profile=await page.request.post(base+'/model-profiles',{headers,data:{name:'Owned standalone fixture',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'TEST-KEY-NOT-REAL',contextWindow:32768,callBudget:30,inputBudget:1000000,outputBudget:200000}})
  expect(profile.status()).toBe(201);expect((await page.request.put(base+'/model-profiles/'+(await profile.json()).id+'/default',{headers})).ok()).toBeTruthy()
  await page.getByRole('button',{name:'进入故事',exact:true}).click();await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:30000})
  const playUrl=page.url(),lineId=new URL(playUrl).searchParams.get('line')!
  await page.getByLabel('你的下一步行动').fill('我观察四周，向身边的人询问眼前的情况。')
  const beforeAction=await(await page.request.get(worldBase+`/worlds/${world.id}/lines/${lineId}`,{headers})).json()
  const scenes=await page.locator('.world-scene').count();await page.getByRole('button',{name:'提交行动',exact:true}).click();await expect(page.locator('.world-scene')).toHaveCount(scenes+1,{timeout:30000})
  // A live prose card already counts as a scene before the action commits.
  // Wait for the actual checkpoint transaction before measuring publication.
  await expect.poll(async()=>{const line=await(await page.request.get(worldBase+`/worlds/${world.id}/lines/${lineId}`,{headers})).json();return !line.pendingTaskId&&line.headCheckpointId!==beforeAction.headCheckpointId}).toBe(true)
  await expect(page.locator('.world-live-scene')).toHaveCount(0)
  const lineBefore=await(await page.request.get(worldBase+`/worlds/${world.id}/lines/${lineId}`,{headers})).json()
  await page.screenshot({path:path.join(artifacts,'standalone-play.png')})
  await page.getByRole('button',{name:'世界操作',exact:true}).click();await expect(page.getByRole('link',{name:'回到来源小说'})).toHaveCount(0)
  await page.getByRole('button',{name:'存为阅读故事线',exact:true}).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible({timeout:30000})
  const publishedBookId=new URL(page.url()).pathname.split('/')[2]
  const publication=await(await page.request.get(base+'/books/'+publishedBookId,{headers})).json()
  const lineAfter=await(await page.request.get(worldBase+`/worlds/${world.id}/lines/${lineId}`,{headers})).json();expect(lineAfter.headCheckpointId).toBe(lineBefore.headCheckpointId)
  await page.getByRole('button',{name:'返回故事入口并保存进度',exact:true}).click();await expect(page).toHaveURL(new RegExp('/stories/'+storyId+'$'))
  const entry=await(await page.request.get(base+'/stories/'+storyId,{headers})).json();expect(entry.source).toBeNull();expect(entry.resume.read.context.bookId).toBe(publishedBookId)
  await expect(page.getByRole('heading',{name:'Next-独立雨夜'})).toBeVisible();await expect(page.locator('.story-source')).toHaveCount(0)
  await page.getByRole('button',{name:'继续游玩',exact:true}).click();await expect(page).toHaveURL(new RegExp(`/worlds/${world.id}/play\\?`));await expect(page.locator('.world-options button')).toHaveCount(3)
  const final=await(await page.request.get(worldBase+`/worlds/${world.id}/lines/${lineId}`,{headers})).json();expect(final.headCheckpointId).toBe(lineBefore.headCheckpointId)
  writeFileSync(path.join(artifacts,'standalone-observations.json'),JSON.stringify({testType:'chromium-postgresql-mock-provider',providerCallsReal:0,bookCountBeforeCreate:0,bookCountAfterCreate:0,templateCreationTaskId:null,sourceKind:world.sourceKind,worldId:world.id,publicationBookId:publication.id,publicationOriginalRevisionId:publication.originalRevisionId,worldHeadUnchangedByPublishAndResume:true,fixtureAccountCleanup:'deferred to owned project teardown',limitations:['No literary quality, physical-device or native certification']},null,2))
})
