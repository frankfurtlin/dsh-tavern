import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page,Locator,request as playwrightRequest} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

async function edit(page:Page,field:string,fill:(form:Locator)=>Promise<void>,reason:string){
  await page.getByRole('button',{name:'世界操作',exact:true}).click()
  await page.getByRole('dialog').getByRole('button',{name:'调整世界资料',exact:true}).click()
  const form=page.getByRole('dialog').locator('.world-correction-form')
  await form.getByLabel('调整内容',{exact:true}).selectOption(field);await fill(form)
  await form.getByLabel('调整原因',{exact:true}).fill(reason)
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/corrections'),{timeout:15000})
  await form.getByRole('button',{name:'保存并另开世界线',exact:true}).click()
  return await response
}

for(const width of [1440,390])test(`nonempty world cards and explicit corrections at ${width}px`,async({page,request,baseURL})=>{
  test.setTimeout(180000);page.setDefaultTimeout(15000);expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
  await page.setViewportSize({width,height:width===390?844:950});await page.emulateMedia({reducedMotion:'reduce'})
  const username='wc_'+Date.now().toString(36)+'_'+randomUUID().slice(0,5),password='world-correction-fixture-123'
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const registered=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/register'),{timeout:15000})
  const login=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/login'),{timeout:15000});void login.catch(()=>{})
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  expect((await registered).ok(),await(await registered).text()).toBeTruthy()
  const account=await(await login).json(),headers={Authorization:'Bearer '+account.accessToken}
  await expectLibraryLanding(page)
  try{
    await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page);await page.getByLabel('通道名称').fill('显式 mock 验收');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic');await page.getByLabel('模型标识').fill('world-fixture');await page.getByLabel('API Key').fill('FIXTURE-ONLY-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click();await expect(page.getByRole('heading',{name:/显式 mock 验收/})).toBeVisible()
    await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible();await page.locator('.reader-body .paragraph').last().scrollIntoViewIfNeeded();await page.waitForTimeout(1800)
    const originalReaderUrl=page.url();await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'从这里进入故事',exact:true}).click();await page.getByRole('button',{name:'建立世界',exact:true}).click();await expect(page.getByRole('heading',{name:'你想成为谁？'})).toBeVisible({timeout:20000});await page.getByRole('button',{name:'进入故事',exact:true}).click();await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:20000})
    const originUrl=page.url(),worldId=new URL(page.url()).pathname.split('/')[2],originLine=new URL(page.url()).searchParams.get('line')!
    async function current(){const line=new URL(page.url()).searchParams.get('line')!;return await(await request.get(`/api/world/v1/worlds/${worldId}/lines/${line}`,{headers})).json()}
    const origin=await current(),player=origin.head.state.entities.find((e:{id:string})=>e.id===origin.head.state.playerId)
    await expect(page.locator('.world-presence-bar')).toContainText(player.name);await expect(page.locator('.world-presence-bar')).toContainText(origin.head.state.timeLabel)
    async function save(field:string,fill:(form:Locator)=>Promise<void>,reason:string){const r=await edit(page,field,fill,reason);expect(r.ok(),await r.text()).toBeTruthy();const saved=await r.json();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page).toHaveURL(new RegExp('line='+saved.id));await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',saved.id);return saved}
    for(const name of ['向导阿澄','药师小禾'])await save('entity:add',async form=>{await form.getByLabel('资料类型').selectOption('character');await form.getByLabel('名称',{exact:true}).fill(name);await form.getByLabel('公开资料',{exact:true}).fill('在渡口相遇的新朋友。')},'明确补充人物 '+name)
    await save('entity:add',async form=>{await form.getByLabel('资料类型').selectOption('item');await form.getByLabel('名称',{exact:true}).fill('随身药丸');await form.getByLabel('可持有多件').check()},'补充可计数物品')
    await save('inventory',async form=>{await form.getByLabel('物品',{exact:true}).selectOption({label:'随身药丸'});await form.getByLabel('新的数量').fill('3')},'确认持有三颗药丸')
    await save('custom',async form=>{await form.getByLabel('属性名称').fill('勇气');await form.getByLabel('属性类型').selectOption('number');await form.getByLabel('新的内容').fill('7')},'确认勇气数值')
    await save('relation',async form=>{await form.getByLabel('另一位人物').selectOption({label:'向导阿澄'});await form.getByLabel('新的内容').fill('互相信任')},'一起保管旧信而建立信任')
    async function details(){if(width===390){await page.getByRole('button',{name:'打开世界资料',exact:true}).click();return page.getByRole('dialog').locator('.world-details')}return page.locator('.world-state-column .world-details')}
    let card=await details();await expect(card.locator('.world-player-card')).toContainText('勇气7');await expect(card.locator('.world-player-card')).toContainText('一起保管旧信而建立信任')
    await card.getByRole('button',{name:'背包',exact:true}).click();await expect(card.locator('.world-bag-item')).toContainText('随身药丸');await expect(card.locator('.world-bag-item')).toContainText('× 3')
    await page.screenshot({path:path.join(artifacts,`world-corrections-bag-${width}.png`),fullPage:true})
    await card.getByRole('button',{name:'人物',exact:true}).click();await card.getByRole('button',{name:/向导阿澄/}).click()
    const talk=card.getByRole('button',{name:'与 TA 说话',exact:true});expect((await talk.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    if(width===390)await page.getByRole('button',{name:'关闭',exact:true}).click()
    await page.getByLabel('你的下一步行动').fill('这封信要送给谁？')
    card=await details();if(width===390)await card.getByRole('button',{name:/向导阿澄/}).click()
    await card.getByRole('button',{name:'与 TA 说话',exact:true}).click();await expect(page.getByLabel('你的下一步行动')).toHaveValue('我对向导阿澄说：“这封信要送给谁？')
    card=await details();await card.getByRole('button',{name:/药师小禾/}).click();await card.getByRole('button',{name:'与 TA 说话',exact:true}).click();await expect(page.getByLabel('你的下一步行动')).toHaveValue('我对药师小禾说：“这封信要送给谁？')
    const before=await current();expect(before.headCheckpointId).toBe((await current()).headCheckpointId)
    await page.getByRole('button',{name:'提交行动',exact:true}).click();await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:20000})
    card=await details();await expect(card.locator('.world-player-card')).toContainText('一起保管旧信而建立信任');if(width===390)await page.getByRole('button',{name:'关闭',exact:true}).click()
    await save('alive',async form=>{await form.getByLabel('新的内容').selectOption('false')},'测试死亡终局')
    await expect(page.getByLabel('你的下一步行动')).toBeDisabled();await expect(page.getByRole('button',{name:'从起点再来一次',exact:true})).toBeVisible()
    await save('rule:deathPolicy',async form=>{await form.getByLabel('新的内容').selectOption('rescue')},'明确允许等待救援')
    await expect(page.getByLabel('你的下一步行动')).toBeEnabled();await page.getByRole('button',{name:'等待救援',exact:true}).click();await expect(page.getByLabel('你的下一步行动')).toHaveValue('我等待可能到来的救援。')
    await save('rule:deathPolicy',async form=>{await form.getByLabel('新的内容').selectOption('rebirth')},'明确允许有代价重生');await expect(page.locator('.world-death-options')).toContainText('重生需要付出代价')
    await save('alive',async form=>{await form.getByLabel('新的内容').selectOption('true')},'读者显式纠正生命设定');await expect(page.locator('.world-death-options')).toHaveCount(0)
    await save('entity:add',async form=>{await form.getByLabel('资料类型').selectOption('place');await form.getByLabel('名称',{exact:true}).fill('北岸灯塔')},'补充已知地点')
    await save('rule:routes',async form=>{await form.getByLabel('起点',{exact:true}).selectOption(origin.head.state.location);await form.getByLabel('北岸灯塔',{exact:true}).check()},'确认从渡口通往灯塔')
    card=await details();await card.getByRole('button',{name:'资料',exact:true}).click();await expect(card.locator('.world-place-map-links')).toContainText('北岸灯塔');await page.screenshot({path:path.join(artifacts,`world-corrections-details-${width}.png`),fullPage:true});if(width===390)await page.getByRole('button',{name:'关闭',exact:true}).click()
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    const frozen=await(await request.get(`/api/world/v1/worlds/${worldId}/lines/${originLine}`,{headers})).json();expect(frozen).toEqual(origin)
    await page.goto(originUrl);await expect(page.locator('.world-options button')).toHaveCount(3)
    await page.locator('.world-scene .world-scene-heading button').last().click();await page.getByRole('button',{name:'查看这一刻的世界',exact:true}).click();await expect(page.getByRole('dialog').getByRole('button',{name:'调整世界资料',exact:true})).toHaveCount(0);await expect(page.getByRole('dialog').getByRole('button',{name:'与 TA 说话',exact:true})).toHaveCount(0)
    await page.goto(originalReaderUrl);await expect(page.getByRole('heading',{name:'第一章 渡口',exact:true})).toBeVisible();await expect(page.locator('.reader-body')).not.toContainText('向导阿澄')
  }finally{
    const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
    try{const me=await cleanup.get('/api/reader/v1/auth/me',{headers});expect(await me.json()).toMatchObject({id:account.user.id,username})
    const deleted=await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}});expect(deleted.ok(),await deleted.text()).toBeTruthy()}finally{await cleanup.dispose()}
  }
})
