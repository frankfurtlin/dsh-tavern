import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,request as playwrightRequest} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

// Only this fresh synthetic account is seeded. Production/real adapters are refused.
function seedPrivatePromise(owner:string,world:string,checkpoint:string){
  const script=`
import json
from copy import deepcopy
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal,engine
from app.models import User,Task
from app.world.models import World,WorldCheckpoint,WorldEvent
from app.world.state import checkpoint_hash
owner,world_id,checkpoint_id=${JSON.stringify([owner,world,checkpoint])}
assert settings.ai_mode=='mock' and engine.url.database=='reader_e2e_test'
with SessionLocal() as db:
    user=db.get(User,owner)
    assert user and user.username.startswith('wea_')
    world=db.get(World,world_id); cp=db.get(WorldCheckpoint,checkpoint_id)
    assert world.owner_id==cp.owner_id==owner and cp.world_id==world.id
    assert not list(db.scalars(select(Task).where(Task.owner_id==owner,Task.state.notin_(['succeeded','failed','cancelled']))))
    state=deepcopy(cp.state); actor=state['playerId']; minute=state['minute']
    state['queue'].append({'id':'synthetic-legacy-private-choice','type':'promise','entityId':actor,
        'description':'只有原身份收到的渡口约定','visibleTo':['player'],'minute':minute,
        'scheduledAtMinute':minute,'dueMinute':minute+5,'loreIds':[],'requiresDecision':True})
    cp.state=state; cp.content_hash=checkpoint_hash(state,cp.definition,cp.text,cp.parent_checkpoint_id)
    for event_type,description,audience in [('private_note','原身份私记：红蜡留下三道印痕',['player']),('street_news','公共见闻：渡口铜铃响起',['all'])]:
        db.add(WorldEvent(owner_id=owner,world_id=world_id,checkpoint_id=cp.id,entity_id=actor,
            type=event_type,data={'description':description,'type':event_type,'visibleTo':audience},
            visible_to=audience,minute=minute))
    db.commit()
print(json.dumps({'syntheticOnly':True,'legacyAudience':True,'minute':minute,'dueMinute':minute+5}))
`
  return JSON.parse(execFileSync('docker',['compose','-f','compose.e2e.yaml','exec','-T','api','python','-c',script],{
    cwd:path.resolve('..'),encoding:'utf8',windowsHide:true}))
}

for(const width of [1440,390])test(`private history and decision stay with the original identity at ${width}px`,async({page,request,baseURL})=>{
  test.setTimeout(120000);page.setDefaultTimeout(15000)
  expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
  await page.setViewportSize({width,height:width===390?844:950});await page.emulateMedia({reducedMotion:'reduce'})
  const username='wea_'+Date.now().toString(36)+'_'+randomUUID().slice(0,5),password='private-event-fixture-123'
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const registered=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/register'))
  const login=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/login'));void login.catch(()=>{})
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();expect((await registered).ok()).toBeTruthy()
  const account=await(await login).json(),headers={Authorization:'Bearer '+account.accessToken}
  await expectLibraryLanding(page)
  try{
    await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
    await page.getByLabel('通道名称').fill('私记 mock 验收');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
    await page.getByLabel('模型标识').fill('world-fixture');await page.getByLabel('API Key').fill('FIXTURE-ONLY-NOT-REAL')
    await page.getByRole('button',{name:'保存配置'}).click();await expect(page.getByRole('heading',{name:/私记 mock 验收/})).toBeVisible()
    await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click()
    await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
    await page.locator('.reader-body .paragraph').last().scrollIntoViewIfNeeded();await page.waitForTimeout(1800)
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'从这里进入故事',exact:true}).click()
    await page.getByRole('button',{name:'建立世界',exact:true}).click();await expect(page.getByRole('heading',{name:'你想成为谁？'})).toBeVisible({timeout:20000})
    await page.getByRole('button',{name:'进入故事',exact:true}).click();await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:20000})
    const worldId=new URL(page.url()).pathname.split('/')[2],originalLine=new URL(page.url()).searchParams.get('line')!
    const read=async(line=originalLine)=>{const response=await request.get(`/api/world/v1/worlds/${worldId}/lines/${line}`,{headers});expect(response.ok()).toBeTruthy();return response.json()}
    const original=await read(),seed=seedPrivatePromise(account.user.id,worldId,original.headCheckpointId)
    await page.reload();await expect(page.locator('.world-options button')).toHaveCount(3)
    const details=async()=>{
      if(width===390){await page.getByRole('button',{name:'打开世界资料',exact:true}).click();return page.getByRole('dialog').locator('.world-details')}
      return page.locator('.world-state-column .world-details')
    }
    let panel=await details();await panel.getByRole('button',{name:'经历',exact:true}).click()
    await expect(panel).toContainText('原身份私记：红蜡留下三道印痕');await expect(panel).toContainText('公共见闻：渡口铜铃响起')
    await page.screenshot({path:path.join(artifacts,`world-event-original-${width}.png`),fullPage:true})
    if(width===390)await page.getByRole('button',{name:'关闭',exact:true}).click()
    await page.getByRole('button',{name:'世界操作',exact:true}).click();await page.getByRole('button',{name:'换一个身份另开线',exact:true}).click()
    await page.getByLabel('名字',{exact:true}).fill('行人阿青');await page.getByLabel('身份',{exact:true}).fill('刚到渡口的陌生旅人。')
    const fork=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/fork'))
    await page.getByRole('button',{name:'以这个身份另开世界线',exact:true}).click();const next=await(await fork).json()
    await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',next.id)
    panel=await details();await panel.getByRole('button',{name:'经历',exact:true}).click()
    await expect(panel).toContainText('公共见闻：渡口铜铃响起');await expect(panel).not.toContainText('红蜡留下三道印痕')
    if(width===390)await page.getByRole('button',{name:'关闭',exact:true}).click()
    await page.getByRole('button',{name:'等待',exact:true}).click();await page.getByLabel('你的下一步行动').fill('我在渡口等五分钟。')
    await page.getByRole('button',{name:'提交行动',exact:true}).click()
    await expect.poll(async()=>(await read(next.id)).headCheckpointId,{timeout:20000}).not.toBe(next.headCheckpointId)
    await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:20000})
    const waited=await read(next.id)
    expect(waited.headCheckpointId).not.toBe(next.headCheckpointId);expect(waited.head.state.minute).toBe(seed.dueMinute)
    panel=await details();await panel.getByRole('button',{name:'经历',exact:true}).click()
    await expect(panel).toContainText('公共见闻：渡口铜铃响起')
    await expect(panel).not.toContainText('红蜡留下三道印痕');await expect(panel).not.toContainText('约定已到期')
    expect((await read()).headCheckpointId).toBe(original.headCheckpointId)
    await page.screenshot({path:path.join(artifacts,`world-event-new-identity-${width}.png`),fullPage:true})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  }finally{
    const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
    try{
      expect(await(await cleanup.get('/api/reader/v1/auth/me',{headers})).json()).toMatchObject({id:account.user.id,username})
      expect((await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})).ok()).toBeTruthy()
    }finally{await cleanup.dispose()}
  }
})
