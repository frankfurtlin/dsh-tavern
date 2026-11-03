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
assert username.startswith('wcr_')
with SessionLocal() as db:
    assert not db.scalar(select(User.id).where(User.username==username))
    user=User(username=username,password_hash=password_hasher.hash(password))
    db.add(user);db.flush();db.add(UserSettings(owner_id=user.id));db.commit()
print('own chapter read fixture created')`
  execFileSync('docker',['compose','-f','compose.e2e.yaml','exec','-T','api','python','-c',code],{cwd:path.resolve('..'),encoding:'utf8',windowsHide:true})
}

for(const width of [1440,390])test(`complete chapter survives actual older HTTP snapshot at ${width}px`,async({page,request,baseURL},info)=>{
  test.setTimeout(110000);expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
  await page.setViewportSize({width,height:width===390?844:950});await page.emulateMedia({reducedMotion:'reduce'})
  const username='wcr_'+Date.now().toString(36)+randomUUID().slice(0,6),password='chapter-read-fixture-123'
  seed(username,password)
  const response=await request.post('/api/reader/v1/auth/login',{data:{username,password,clientType:'web',deviceName:'chapter-read-fixture'}})
  expect(response.ok()).toBeTruthy();const account=await response.json(),headers={Authorization:'Bearer '+account.accessToken}
  const get=async(url:string)=>{const r=await request.get(url,{headers});expect(r.ok()).toBeTruthy();return r.json()}
  const post=async(url:string,data:unknown)=>{const r=await request.post(url,{headers:{...headers,'Idempotency-Key':randomUUID()},data});expect(r.ok()).toBeTruthy();return r.json()}
  let release:()=>void=()=>{},released=false,older:any=null,oldReturned=false,turnId='',writePosts=0
  const gate=new Promise<void>(resolve=>{release=()=>{released=true;resolve()}})
  try{
    await post('/api/reader/v1/model-profiles',{name:'chapter read mock',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-ONLY-NOT-REAL',contextWindow:32768,callBudget:100,inputBudget:10000000,outputBudget:1000000})
    const book=await post('/api/reader/v1/books/demo',{}),directory=await get(`/api/reader/v1/branches/${book.branchId}/chapters?revisionId=${book.revisionId}`)
    const sourceUrl=`/api/reader/v1/branches/${book.branchId}/chapters/${directory.items[0].id}?revisionId=${book.revisionId}`,source=await get(sourceUrl),anchor=source.blocks.at(-1).endAnchor
    const progressUrl=`/api/reader/v1/books/${book.bookId}/progress?branchId=${book.branchId}&revisionId=${book.revisionId}`
    expect((await request.put(`/api/reader/v1/books/${book.bookId}/progress`,{headers,data:{branchId:book.branchId,revisionId:book.revisionId,anchor,expectedProgressVersion:0,deviceId:account.sessionId,clientSeq:1}})).ok()).toBeTruthy()
    const originalProgress=await get(progressUrl),created=await post('/api/world/v1/worlds',{name:'候选读取验收',anchor,rules:{}}),root='/api/world/v1/worlds/'+created.worldId
    const done=async(id:string)=>expect.poll(async()=>(await get('/api/world/v1/tasks/'+id)).state,{timeout:30000}).toBe('succeeded')
    await done(created.taskId);const world=await get(root),begun=await post(root+'/begin',{characterId:world.characters.find((c:any)=>c.available).id})
    await done(begun.taskId);const line=await get(root+'/lines/'+begun.lineId)
    await page.goto('/login');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
    await page.getByRole('button',{name:'登录',exact:true}).click();await expectLibraryLanding(page)
    await page.goto(`/worlds/${created.worldId}/play?line=${begun.lineId}`);await expect(page.getByLabel('你的下一步行动')).toBeEnabled()
    page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/write'))writePosts++})
    await page.route('**/api/world/v1/worlds/*/turns/*',async route=>{
      const url=new URL(route.request().url());if(route.request().method()!=='GET'||!/\/turns\/[^/]+$/.test(url.pathname)){await route.continue();return}
      const actual=await route.fetch(),body=await actual.json()
      if(!older&&body.status==='queued'&&body.chapters.length===0){
        older={id:body.id,status:body.status,chapterCount:body.chapters.length};turnId=body.id
        await gate
        if(!page.isClosed()){await route.fulfill({response:actual});oldReturned=true}
      }else await route.fulfill({response:actual})
    })
    await page.getByRole('button',{name:'写几章',exact:true}).click()
    const dialog=page.getByRole('dialog',{name:'让故事继续几章'})
    await dialog.getByRole('button',{name:/^开始写作/}).click()
    await expect.poll(()=>older,{timeout:10000}).not.toBeNull()
    await expect(dialog.locator('.world-candidate')).toHaveCount(1,{timeout:30000})
    await expect(dialog.getByRole('button',{name:'保存并继续游玩',exact:true})).toBeVisible()
    const latest=await get(root+'/turns/'+turnId);expect(latest.chapters).toHaveLength(1)
    expect(latest.status).toBe('candidate');expect(older.id).toBe(latest.id)
    const usageBeforeDelayedRead=await get('/api/reader/v1/usage')
    release();await expect.poll(()=>oldReturned).toBe(true)
    await page.waitForTimeout(300)
    await expect(dialog.locator('.world-candidate')).toHaveCount(1)
    await expect(dialog.locator('.world-candidate .world-prose')).toHaveText(latest.chapters[0].text)
    await expect(dialog.getByRole('button',{name:'保存并继续游玩',exact:true})).toBeEnabled()
    expect(await get('/api/reader/v1/usage')).toEqual(usageBeforeDelayedRead);expect(writePosts).toBe(1)
    expect((await get(root+'/lines/'+begun.lineId)).headCheckpointId).toBe(line.headCheckpointId)
    await page.screenshot({path:path.join(artifacts,`chapter-read-preserved-${width}.png`)})
    await dialog.getByRole('button',{name:'保存并继续游玩',exact:true}).click();await expect(dialog).toHaveCount(0)
    const saved=await get(root+'/lines/'+begun.lineId);expect(saved.headCheckpointId).not.toBe(line.headCheckpointId);expect(saved.head.text).toBe(latest.chapters[0].text)
    expect(await get(sourceUrl)).toEqual(source);expect(await get(progressUrl)).toEqual(originalProgress)
    expect(await get('/api/reader/v1/usage')).toEqual(usageBeforeDelayedRead);expect(writePosts).toBe(1)
    await page.screenshot({path:path.join(artifacts,`chapter-read-saved-${width}.png`)})
    await info.attach('chapter-read-order-evidence',{body:Buffer.from(JSON.stringify({width,strictMock:true,ownSyntheticOnly:true,olderActualHttpSnapshot:older,newerChapterCount:1,olderReleasedAfterFullCandidate:true,proseAndSavePreserved:true,writePostCount:writePosts,usageUnchangedDuringDelayedReadAndAccept:true,sourceAndProgressUnchanged:true,headChangedOnlyAfterExplicitAccept:true,paidCallsAdded:0})),contentType:'application/json'})
  }finally{
    if(!released)release()
    await page.unrouteAll({behavior:'wait'})
    const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
    try{expect(await(await cleanup.get('/api/reader/v1/auth/me',{headers})).json()).toMatchObject({id:account.user.id,username});expect((await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})).ok()).toBeTruthy()}finally{await cleanup.dispose()}
  }
})
