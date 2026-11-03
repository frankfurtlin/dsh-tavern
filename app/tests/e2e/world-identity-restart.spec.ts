import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,request as playwrightRequest} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

for(const width of [1440,390])test(`identity restart preserves the chosen player at ${width}px`,async({page,request,baseURL})=>{
  test.setTimeout(120000);page.setDefaultTimeout(15000)
  expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
  await page.setViewportSize({width,height:width===390?844:950})
  await page.emulateMedia({reducedMotion:'reduce'})
  const username='wr_'+Date.now().toString(36)+'_'+randomUUID().slice(0,5),password='identity-restart-fixture-123'
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const registered=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/register'))
  const login=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/login'));void login.catch(()=>{})
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  expect((await registered).ok()).toBeTruthy()
  const account=await(await login).json(),headers={Authorization:'Bearer '+account.accessToken}
  await expectLibraryLanding(page)
  try{
    await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
    await page.getByLabel('通道名称').fill('重开 mock 验收');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
    await page.getByLabel('模型标识').fill('world-fixture');await page.getByLabel('API Key').fill('FIXTURE-ONLY-NOT-REAL')
    await page.getByRole('button',{name:'保存配置'}).click();await expect(page.getByRole('heading',{name:/重开 mock 验收/})).toBeVisible()
    await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click()
    await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
    await page.locator('.reader-body .paragraph').last().scrollIntoViewIfNeeded();await page.waitForTimeout(1800)
    const readerUrl=page.url()
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'从这里进入故事',exact:true}).click()
    await page.getByRole('button',{name:'建立世界',exact:true}).click();await expect(page.getByRole('heading',{name:'你想成为谁？'})).toBeVisible({timeout:20000})
    await page.getByRole('button',{name:'进入故事',exact:true}).click();await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:20000})
    const worldId=new URL(page.url()).pathname.split('/')[2]
    async function read(lineId=new URL(page.url()).searchParams.get('line')!){
      const response=await request.get(`/api/world/v1/worlds/${worldId}/lines/${lineId}`,{headers});expect(response.ok()).toBeTruthy();return await response.json()
    }
    const original=await read()
    await page.getByRole('button',{name:'世界操作',exact:true}).click();await page.getByRole('button',{name:'换一个身份另开线',exact:true}).click()
    await page.getByLabel('名字',{exact:true}).fill('旅人小禾');await page.getByLabel('身份',{exact:true}).fill('渡口边的药师，寻找一位故友。')
    const identityResponse=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/fork'))
    await page.getByRole('button',{name:'以这个身份另开世界线',exact:true}).click()
    const identity=await(await identityResponse).json()
    await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',identity.id)
    expect(identity.head.kind).toBe('identity');expect(identity.head.state.playerId).not.toBe(original.head.state.playerId)
    await expect(page.locator('.world-presence-bar')).toContainText('旅人小禾')
    await page.getByLabel('你的下一步行动').fill('我观察渡口的灯火。');await page.getByRole('button',{name:'提交行动',exact:true}).click()
    await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:20000})
    const advanced=await read();expect(advanced.headCheckpointId).not.toBe(identity.headCheckpointId)
    // Explicit author correction is a terminal UI fixture. Ordinary death and
    // cost-bearing revival are separately exercised through the real worker tests.
    let dead=advanced
    for(const [field,value] of [['field:money',7],['alive',false]] as const){
      const response=await request.post(`/api/world/v1/worlds/${worldId}/corrections`,{headers:{...headers,'Idempotency-Key':randomUUID()},data:{
        expectedCheckpointId:dead.headCheckpointId,entityId:dead.head.state.playerId,field,value,reason:'建立明确的终局界面夹具'}})
      expect(response.ok()).toBeTruthy();dead=await response.json()
    }
    expect(dead.head.state.ended).toBe(true)
    await page.goto(`/worlds/${worldId}/play?line=${dead.id}`)
    const restart=page.getByRole('button',{name:'从起点再来一次',exact:true})
    await expect(restart).toBeVisible();await expect(page.getByLabel('你的下一步行动')).toBeDisabled()
    expect((await restart.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    const usageBefore=await(await request.get('/api/reader/v1/usage',{headers})).json()
    let actions=0;page.on('request',r=>{if(r.method()==='POST'&&/\/actions$/.test(r.url()))actions++})
    const restartedResponse=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/fork'))
    await restart.click();const restarted=await(await restartedResponse).json()
    await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',restarted.id)
    await expect(page.getByLabel('你的下一步行动')).toBeEnabled();await expect(page.locator('.world-presence-bar')).toContainText('旅人小禾')
    expect(restarted.forkCheckpointId).toBe(identity.headCheckpointId)
    expect(restarted.head.state).toEqual(identity.head.state)
    expect(await read(dead.id)).toEqual(dead);expect(await read(original.id)).toEqual(original)
    await page.waitForTimeout(700);expect(actions).toBe(0)
    expect(await(await request.get('/api/reader/v1/usage',{headers})).json()).toEqual(usageBefore)
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.screenshot({path:path.join(artifacts,`world-identity-restart-${width}.png`),fullPage:true})
    await page.goto(readerUrl);await expect(page.getByRole('heading',{name:'第一章 渡口',exact:true})).toBeVisible()
    await expect(page.locator('.reader-body')).not.toContainText('旅人小禾')
  }finally{
    const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
    try{
      expect(await(await cleanup.get('/api/reader/v1/auth/me',{headers})).json()).toMatchObject({id:account.user.id,username})
      expect((await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})).ok()).toBeTruthy()
    }finally{await cleanup.dispose()}
  }
})
