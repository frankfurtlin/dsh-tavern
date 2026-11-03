import {expectLibraryLanding} from './helpers/landing'
import {test,expect,request as playwrightRequest} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

function seed(username:string,password:string){
  const script=`from app.db import SessionLocal,engine
from app.config import settings
from app.models import User,UserSettings
from app.security import password_hasher
from sqlalchemy import select
username,password=${JSON.stringify([username,password])}
assert settings.ai_mode=='mock' and engine.url.database=='reader_e2e_test'
assert username.startswith('wmg_')
with SessionLocal() as db:
    assert not db.scalar(select(User.id).where(User.username==username))
    user=User(username=username,password_hash=password_hasher.hash(password))
    db.add(user);db.flush();db.add(UserSettings(owner_id=user.id));db.commit()
print('own management fixture created')`
  execFileSync('docker',['compose','-f','compose.e2e.yaml','exec','-T','api','python','-c',script],{cwd:path.resolve('..'),encoding:'utf8',windowsHide:true})
}

for(const width of [1440,390])for(const scenario of ['identity'] as const){
  test(`world list ${scenario} at ${width}px`,async({page,request,baseURL},info)=>{
    test.setTimeout(110000);page.setDefaultTimeout(10000)
    expect(baseURL).toBe('http://127.0.0.1:8811')
    expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
    await page.setViewportSize({width,height:width===390?844:950});await page.emulateMedia({reducedMotion:'reduce'})
    const username='wmg_'+Date.now().toString(36)+randomUUID().slice(0,6),password='world-management-fixture-123'
    seed(username,password)
    const login=await request.post('/api/reader/v1/auth/login',{data:{username,password,clientType:'web',deviceName:'world-management'}})
    expect(login.ok()).toBeTruthy();const account=await login.json(),headers={Authorization:'Bearer '+account.accessToken}
    async function get(url:string){const r=await request.get(url,{headers});expect(r.ok()).toBeTruthy();return r.json()}
    async function post(url:string,data:unknown){const r=await request.post(url,{headers:{...headers,'Idempotency-Key':randomUUID()},data});expect(r.ok()).toBeTruthy();return r.json()}
    try{
      await post('/api/reader/v1/model-profiles',{name:'management mock',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-ONLY-NOT-REAL',contextWindow:32768,callBudget:100,inputBudget:10000000,outputBudget:1000000})
      const book=await post('/api/reader/v1/books/demo',{}),directory=await get(`/api/reader/v1/branches/${book.branchId}/chapters?revisionId=${book.revisionId}`)
      const sourceUrl=`/api/reader/v1/branches/${book.branchId}/chapters/${directory.items[0].id}?revisionId=${book.revisionId}`,source=await get(sourceUrl),anchor=source.blocks.at(-1).endAnchor
      const progressUrl=`/api/reader/v1/books/${book.bookId}/progress?branchId=${book.branchId}&revisionId=${book.revisionId}`
      const progress=await request.put(`/api/reader/v1/books/${book.bookId}/progress`,{headers,data:{branchId:book.branchId,revisionId:book.revisionId,anchor,expectedProgressVersion:0,deviceId:account.sessionId,clientSeq:1}})
      expect(progress.ok()).toBeTruthy()
      const created=await post('/api/world/v1/worlds',{name:'渡口管理验收',anchor,rules:{}}),root='/api/world/v1/worlds/'+created.worldId
      const done=async(id:string)=>expect.poll(async()=>(await get('/api/world/v1/tasks/'+id)).state,{timeout:25000}).toBe('succeeded')
      await done(created.taskId)
      let line:any=null,publication:any=null,publishedChapter:any=null,publishedUrl=''
      if(String(scenario)!=='identity'){
        const world=await get(root),begun=await post(root+'/begin',{characterId:world.characters.find((c:any)=>c.available).id})
        await done(begun.taskId);line=await get(root+'/lines/'+begun.lineId)
        publication=await post(root+'/checkpoints/'+line.headCheckpointId+'/publish',{})
        publishedUrl=`/api/reader/v1/branches/${publication.branchId}/chapters/${publication.readingAnchor.chapterId}?revisionId=${publication.revisionId}`
        publishedChapter=await get(publishedUrl)
      }
      const beforeUsage=await get('/api/reader/v1/usage'),beforeProgress=await get(progressUrl)
      let generationPosts=0
      page.on('request',r=>{if(r.method()==='POST'&&/\/(actions|write|rewrite|begin|prepare)$/.test(new URL(r.url()).pathname))generationPosts++})
      await page.goto('/login');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
      await page.getByRole('button',{name:'登录',exact:true}).click();await expectLibraryLanding(page)
      await page.goto('/worlds');await expect(page.locator('.world-card')).toHaveCount(1)
      async function manage(name:string){await page.getByRole('button',{name:'管理'+name,exact:true}).click();await expect(page.getByRole('dialog',{name:'管理世界'})).toBeVisible()}
      const dialog=page.getByRole('dialog',{name:'管理世界'})
      await manage('渡口管理验收')
      const renamed='雾港 · '+width
      await page.getByLabel('世界名称',{exact:true}).fill(renamed)
      if(String(scenario)==='conflict'){
        const fresh=await get(root)
        const other=await request.patch(root,{headers,data:{expectedVersion:fresh.version,name:'另一设备的名称'}})
        expect(other.ok()).toBeTruthy()
        const rejection=page.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().endsWith(root))
        await dialog.getByRole('button',{name:'保存名称',exact:true}).click();expect((await rejection).status()).toBe(409)
        await expect(dialog.getByRole('alert')).toContainText('世界已更新')
        await expect(page.getByLabel('世界名称',{exact:true})).toHaveValue(renamed)
        expect((await get(root)).name).toBe('另一设备的名称')
        await page.screenshot({path:path.join(artifacts,`world-management-conflict-${width}.png`)})
        await dialog.getByRole('button',{name:'载入最新信息',exact:true}).click()
        await expect(dialog.getByText('最新名称：另一设备的名称',{exact:true})).toBeVisible()
        await expect(page.getByLabel('世界名称',{exact:true})).toHaveValue(renamed)
      }
      await dialog.getByRole('button',{name:'保存名称',exact:true}).click();await expect(dialog).toHaveCount(0)
      await expect(page.getByRole('button',{name:'管理'+renamed,exact:true})).toBeVisible()
      expect((await get(root)).name).toBe(renamed)
      await page.getByLabel('搜索世界').fill(renamed);await expect(page.locator('.world-card')).toHaveCount(1);await page.getByLabel('搜索世界').clear()
      await manage(renamed);await dialog.getByRole('button',{name:'归档',exact:true}).click();await expect(dialog).toHaveCount(0)
      await expect(page.locator('.world-card')).toHaveCount(0)
      await page.getByRole('button',{name:'已归档',exact:true}).click();await expect(page.locator('.world-card')).toHaveCount(1)
      expect((await get(root)).status).toBe('archived')
      // Follow the real archived card link before restore, including a reload.
      // No API response substitution: the existing source is the strict fixture.
      await page.locator('.world-card-art').click()
      await expect(page.getByRole('status')).toContainText('已归档')
      for(const action of ['重新准备','停止准备','进入故事'])await expect(page.getByRole('button',{name:action,exact:true})).toHaveCount(0)
      await page.screenshot({path:path.join(artifacts,`world-archived-entry-readonly-${width}.png`)})
      await page.reload();await expect(page.getByRole('status')).toContainText('已归档')
      for(const action of ['重新准备','停止准备','进入故事'])await expect(page.getByRole('button',{name:action,exact:true})).toHaveCount(0)
      expect((await get(root)).prepareTaskId).toBe(created.taskId)
      expect((await get('/api/world/v1/tasks/'+created.taskId)).state).toBe('succeeded')
      expect(await get('/api/reader/v1/usage')).toEqual(beforeUsage);expect(generationPosts).toBe(0)
      await page.getByRole('link',{name:'回到世界列表',exact:true}).click()
      await page.getByRole('button',{name:'已归档',exact:true}).click()
      if(line){
        await page.locator('.world-card-art').click();await expect(page.locator('.world-archived-note')).toBeVisible()
        await expect(page.getByLabel('你的下一步行动')).toHaveCount(0)
        expect((await get(root+'/lines/'+line.id)).head).toEqual(line.head)
        await page.getByRole('link',{name:'返回世界列表',exact:true}).click();await page.getByRole('button',{name:'已归档',exact:true}).click()
      }
      await manage(renamed);await dialog.getByRole('button',{name:'恢复',exact:true}).click();await expect(dialog).toHaveCount(0)
      await expect(page.locator('.world-card')).toHaveCount(0)
      await page.getByRole('button',{name:/^我的世界/}).click();await expect(page.locator('.world-card')).toHaveCount(1)
      expect((await get(root)).status).toBe('active')
      await page.locator('.world-card-art').click()
      if(line){await expect(page.getByLabel('你的下一步行动')).toBeEnabled();expect((await get(root+'/lines/'+line.id)).head).toEqual(line.head)}
      else await expect(page.getByRole('heading',{name:'你想成为谁？',exact:true})).toBeVisible()
      await page.screenshot({path:path.join(artifacts,`world-management-${scenario}-restored-${width}.png`)})
      await page.goto('/worlds');await manage(renamed)
      await dialog.getByRole('button',{name:'删除世界',exact:true}).click();await dialog.getByRole('button',{name:'保留',exact:true}).click()
      expect((await get(root)).id).toBe(created.worldId)
      await dialog.getByRole('button',{name:'删除世界',exact:true}).click();await dialog.getByRole('button',{name:'确认删除',exact:true}).click()
      await expect(dialog).toHaveCount(0);await expect(page.locator('.world-card')).toHaveCount(0)
      await expect(page.getByRole('heading',{name:'故事里，还有你的位置',exact:true})).toBeVisible()
      expect((await request.get(root,{headers})).status()).toBe(404)
      if(line){expect((await request.get(root+'/lines/'+line.id,{headers})).status()).toBe(404);expect(await get(publishedUrl)).toEqual(publishedChapter)}
      expect(await get(sourceUrl)).toEqual(source);expect(await get(progressUrl)).toEqual(beforeProgress)
      const branches=await get(`/api/reader/v1/books/${book.bookId}/branches`)
      expect(branches.items.find((b:any)=>b.id===book.branchId).headRevisionId).toBe(book.revisionId)
      expect(await get('/api/reader/v1/usage')).toEqual(beforeUsage);expect(generationPosts).toBe(0)
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
      await page.screenshot({path:path.join(artifacts,`world-management-${scenario}-deleted-${width}.png`)})
      await page.goto(`/books/${book.bookId}/read?branch=${book.branchId}`);await expect(page.locator('.reader-body h1')).toHaveText(source.title)
      await info.attach('world-management-evidence',{body:Buffer.from(JSON.stringify({scenario,width,strictMock:true,noGenerationPosts:true,usageUnchanged:true,sourceAndProgressUnchangedDuringManagement:true,originalHeadUnchanged:true,publishedReadingPreserved:!!line,worldReadAfterDeletion:404,ownSyntheticOnly:true})),contentType:'application/json'})
    }finally{
      const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
      try{
        expect(await(await cleanup.get('/api/reader/v1/auth/me',{headers})).json()).toMatchObject({id:account.user.id,username})
        expect((await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})).ok()).toBeTruthy()
      }finally{await cleanup.dispose()}
    }
  })
}
