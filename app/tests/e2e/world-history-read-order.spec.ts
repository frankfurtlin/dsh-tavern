import {expectLibraryLanding} from './helpers/landing'
import {test,expect,request as playwrightRequest} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

function seed(username:string,password:string){
  const code=`from app.db import SessionLocal,engine
from app.config import settings
from app.models import User,UserSettings
from app.security import password_hasher
from sqlalchemy import select
username,password=${JSON.stringify([username,password])}
assert settings.ai_mode=='mock' and engine.url.database=='reader_e2e_test'
assert username.startswith('whr_')
with SessionLocal() as db:
    assert not db.scalar(select(User.id).where(User.username==username))
    user=User(username=username,password_hash=password_hasher.hash(password))
    db.add(user);db.flush();db.add(UserSettings(owner_id=user.id));db.commit()
print('own history fixture created')`
  execFileSync('docker',['compose','-f','compose.e2e.yaml','exec','-T','api','python','-c',code],{cwd:path.resolve('..'),encoding:'utf8',windowsHide:true})
}

for(const width of [1440,390])for(const view of ['play','lines'] as const)test(`latest ${view} history selection survives older actual HTTP response at ${width}px`,async({page,request,baseURL},info)=>{
  test.setTimeout(110000);expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
  await page.setViewportSize({width,height:width===390?844:950});await page.emulateMedia({reducedMotion:'reduce'})
  const username='whr_'+Date.now().toString(36)+randomUUID().slice(0,6),password='history-read-fixture-123'
  seed(username,password)
  const login=await request.post('/api/reader/v1/auth/login',{data:{username,password,clientType:'web',deviceName:'history-read'}})
  expect(login.ok()).toBeTruthy();const account=await login.json(),headers={Authorization:'Bearer '+account.accessToken}
  const get=async(url:string)=>{const r=await request.get(url,{headers});expect(r.ok()).toBeTruthy();return r.json()}
  const post=async(url:string,data:unknown)=>{const r=await request.post(url,{headers:{...headers,'Idempotency-Key':randomUUID()},data});expect(r.ok()).toBeTruthy();return r.json()}
  let release:()=>void=()=>{},released=false,older:any=null,returned=false
  const gate=new Promise<void>(resolve=>{release=()=>{released=true;resolve()}})
  try{
    await post('/api/reader/v1/model-profiles',{name:'history mock',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-ONLY-NOT-REAL',contextWindow:32768,callBudget:100,inputBudget:10000000,outputBudget:1000000})
    const book=await post('/api/reader/v1/books/demo',{}),directory=await get(`/api/reader/v1/branches/${book.branchId}/chapters?revisionId=${book.revisionId}`)
    const sourceUrl=`/api/reader/v1/branches/${book.branchId}/chapters/${directory.items[0].id}?revisionId=${book.revisionId}`,source=await get(sourceUrl),anchor=source.blocks.at(-1).endAnchor
    const progressUrl=`/api/reader/v1/books/${book.bookId}/progress?branchId=${book.branchId}&revisionId=${book.revisionId}`
    expect((await request.put(`/api/reader/v1/books/${book.bookId}/progress`,{headers,data:{branchId:book.branchId,revisionId:book.revisionId,anchor,expectedProgressVersion:0,deviceId:account.sessionId,clientSeq:1}})).ok()).toBeTruthy()
    const originalProgress=await get(progressUrl),created=await post('/api/world/v1/worlds',{name:'历史选择验收',anchor,rules:{}}),root='/api/world/v1/worlds/'+created.worldId
    const done=async(id:string)=>expect.poll(async()=>(await get('/api/world/v1/tasks/'+id)).state,{timeout:30000}).toBe('succeeded')
    await done(created.taskId);const world=await get(root),begun=await post(root+'/begin',{characterId:world.characters.find((c:any)=>c.available).id})
    await done(begun.taskId);const originalLine=await get(root+'/lines/'+begun.lineId),a=originalLine.head
    const action=await post(root+'/lines/'+begun.lineId+'/actions',{message:'我观察渡口的灯火。',kind:'observe',turnId:randomUUID(),expectedCheckpointId:a.id})
    await done(action.taskId);const line=await get(root+'/lines/'+begun.lineId),b=line.head
    expect(b.sequence).toBe(a.sequence+1)
    await page.goto('/login');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
    await page.getByRole('button',{name:'登录',exact:true}).click();await expectLibraryLanding(page)
    await page.goto(`/worlds/${created.worldId}/${view}?line=${begun.lineId}`)
    if(view==='play')await expect(page.locator('.world-scene')).toHaveCount(2)
    else{await expect(page.locator('.world-graph-node')).toHaveCount(3);await page.getByRole('button',{name:'查看全部世界线',exact:true}).click()}
    const usageBefore=await get('/api/reader/v1/usage')
    await page.route('**/api/world/v1/worlds/*/checkpoints/*',async route=>{
      if(route.request().method()!=='GET'||new URL(route.request().url()).pathname!==root+'/checkpoints/'+a.id){await route.continue();return}
      const actual=await route.fetch();older=await actual.json();await gate
      if(!page.isClosed()){await route.fulfill({response:actual});returned=true}
    })
    const buttons=view==='play'?page.locator('.world-scene-heading button'):page.locator('.world-graph-node')
    await buttons.nth(view==='play'?0:1).click();await expect.poll(()=>older,{timeout:10000}).not.toBeNull();expect(older.id).toBe(a.id)
    await buttons.nth(view==='play'?1:2).click()
    const dialog=page.getByRole('dialog')
    await expect(dialog.locator('small').first()).toHaveText(`${b.state.timeLabel} · 回合 ${b.sequence}`)
    release();await expect.poll(()=>returned).toBe(true);await page.waitForTimeout(300)
    await expect(dialog.locator('small').first()).toHaveText(`${b.state.timeLabel} · 回合 ${b.sequence}`)
    await expect(dialog.locator('.world-prose')).toHaveText(b.text)
    await page.screenshot({path:path.join(artifacts,`history-newer-${view}-${width}.png`)})
    const forkResponse=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===root+'/fork')
    await dialog.getByRole('button',{name:'从这里继续',exact:true}).click()
    const response=await forkResponse;expect(response.ok()).toBeTruthy();expect(response.request().postDataJSON().checkpointId).toBe(b.id)
    const fork=await response.json();expect(fork.forkCheckpointId).toBe(b.id)
    expect((await get(root+'/lines/'+begun.lineId)).head).toEqual(b)
    expect(await get(sourceUrl)).toEqual(source);expect(await get(progressUrl)).toEqual(originalProgress);expect(await get('/api/reader/v1/usage')).toEqual(usageBefore)
    await page.screenshot({path:path.join(artifacts,`history-fork-${view}-${width}.png`)})
    await info.attach('history-order-evidence',{body:Buffer.from(JSON.stringify({width,view,strictMock:true,ownSyntheticOnly:true,olderActualCheckpointId:a.id,latestCheckpointId:b.id,delayedResponseUnchanged:true,latestSelectedAfterOlderRelease:true,forkFromLatestSelection:true,parentHeadFrozen:true,sourceAndProgressUnchanged:true,noModelUsageDuringSelectionAndFork:true,paidCallsAdded:0})),contentType:'application/json'})
  }finally{
    if(!released)release();await page.unrouteAll({behavior:'wait'})
    const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
    try{expect(await(await cleanup.get('/api/reader/v1/auth/me',{headers})).json()).toMatchObject({id:account.user.id,username});expect((await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})).ok()).toBeTruthy()}finally{await cleanup.dispose()}
  }
})
