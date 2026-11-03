import {expectLibraryLanding} from './helpers/landing'
import {test,expect,request as playwrightRequest} from '@playwright/test'
import {randomUUID} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')

function seedAccount(username:string,password:string){
  // Registration throttling is outside this lifecycle test. Create only this
  // own synthetic account; keep all application throttles and budgets intact.
  const script=`from app.db import SessionLocal,engine
from app.config import settings
from app.models import User,UserSettings
from app.security import password_hasher
from sqlalchemy import select
username,password=${JSON.stringify([username,password])}
assert settings.ai_mode=='mock' and engine.url.database=='reader_e2e_test'
assert username.startswith('wll_')
with SessionLocal() as db:
    assert not db.scalar(select(User.id).where(User.username==username))
    user=User(username=username,password_hash=password_hasher.hash(password))
    db.add(user);db.flush();db.add(UserSettings(owner_id=user.id))
    db.commit()
print('own synthetic lifecycle account created')`
  execFileSync('docker',['compose','-f','compose.e2e.yaml','exec','-T','api','python','-c',script],{cwd:path.resolve('..'),encoding:'utf8',windowsHide:true})
}

for(const width of [1440,390])for(const scenario of ['current','shared','archived','last'] as const){
  test(`world line ${scenario} lifecycle at ${width}px`,async({page,request,baseURL},testInfo)=>{
    test.setTimeout(100000);page.setDefaultTimeout(12000)
    expect(baseURL).toBe('http://127.0.0.1:8811')
    expect((await(await request.get('/health/ready')).json()).aiMode).toBe('mock')
    await page.setViewportSize({width,height:width===390?844:950});await page.emulateMedia({reducedMotion:'reduce'})
    const username='wll_'+Date.now().toString(36)+'_'+randomUUID().slice(0,5),password='line-lifecycle-fixture-123'
    seedAccount(username,password)
    const account=await(await request.post('/api/reader/v1/auth/login',{data:{username,password,clientType:'web',deviceName:'line-lifecycle'}})).json()
    const headers={Authorization:'Bearer '+account.accessToken}
    async function get(url:string){const response=await request.get(url,{headers});expect(response.ok()).toBeTruthy();return response.json()}
    async function post(url:string,data:unknown){const response=await request.post(url,{headers:{...headers,'Idempotency-Key':randomUUID()},data});expect(response.ok()).toBeTruthy();return response.json()}
    try{
      await post('/api/reader/v1/model-profiles',{name:'line lifecycle mock',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-ONLY-NOT-REAL',contextWindow:32768,callBudget:100,inputBudget:10000000,outputBudget:1000000})
      const book=await post('/api/reader/v1/books/demo',{})
      const directory=await get(`/api/reader/v1/branches/${book.branchId}/chapters?revisionId=${book.revisionId}`)
      const sourceUrl=`/api/reader/v1/branches/${book.branchId}/chapters/${directory.items[0].id}?revisionId=${book.revisionId}`,source=await get(sourceUrl),anchor=source.blocks.at(-1).endAnchor
      const progress=await request.put(`/api/reader/v1/books/${book.bookId}/progress`,{headers,data:{branchId:book.branchId,revisionId:book.revisionId,anchor,expectedProgressVersion:0,deviceId:account.sessionId,clientSeq:1}})
      expect(progress.ok()).toBeTruthy()
      const prepared=await post('/api/world/v1/worlds',{anchor,rules:{},name:'世界线生命周期'})
      const w=prepared.worldId,root=`/api/world/v1/worlds/${w}`
      const done=async(task:string)=>expect.poll(async()=>(await get('/api/world/v1/tasks/'+task)).state,{timeout:20000}).toBe('succeeded')
      await done(prepared.taskId)
      const info=await get(root),begun=await post(root+'/begin',{characterId:info.characters.find((c:any)=>c.available).id})
      await done(begun.taskId)
      const progressUrl=`/api/reader/v1/books/${book.bookId}/progress?branchId=${book.branchId}&revisionId=${book.revisionId}`
      const parent=await get(root+'/lines/'+begun.lineId),beforeProgress=await get(progressUrl)
      const usage=await get('/api/reader/v1/usage')
      let child=scenario==='last'||scenario==='current'?null:await post(root+'/fork',{checkpointId:parent.headCheckpointId,mode:'continue',name:'保留的子线'})
      let generationPosts=0
      page.on('request',r=>{if(r.method()==='POST'&&/\/(actions|write|rewrite|begin|prepare)$/.test(new URL(r.url()).pathname))generationPosts++})
      await page.goto('/login');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
      await page.getByRole('button',{name:'登录',exact:true}).click();await expectLibraryLanding(page)
      if(scenario==='current'){
        await page.goto(`/worlds/${w}/play?line=${parent.id}`);await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',parent.id)
        await page.getByRole('button',{name:'世界操作',exact:true}).click();await page.getByRole('button',{name:'存一个节点',exact:true}).click()
        await page.getByLabel('存档名称',{exact:true}).fill('保留的子线')
        const forked=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/fork'))
        await page.getByRole('button',{name:'保存节点',exact:true}).click();child=await(await forked).json()
        await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',child.id)
        await page.getByRole('button',{name:'世界操作',exact:true}).click()
        for(const label of ['保存名称','存一个节点','换一个身份另开线'])await expect(page.getByRole('button',{name:label,exact:true})).toBeEnabled()
        await page.getByRole('button',{name:'关闭',exact:true}).click()
      }
      const selected=scenario==='shared'?parent:child||parent
      await page.goto(`/worlds/${w}/lines?line=${selected.id}`)
      await expect(page.locator('.world-graph-line-list button')).toHaveCount(child?2:1)
      async function manage(name:string){await page.locator('.world-graph-line-list button').filter({hasText:name}).click();await expect(page.getByRole('dialog',{name:'管理世界线'})).toBeVisible()}
      async function remove(name:string){await manage(name);await page.getByText('删除世界线',{exact:true}).click();await page.getByRole('button',{name:'确认删除',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)}
      if(scenario==='current'){
        await manage(child!.name);await page.getByLabel('名称',{exact:true}).fill('另一条路线')
        await page.getByRole('button',{name:'保存名称',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
        await expect(page.locator('.world-graph-line-list button').filter({hasText:'另一条路线'})).toBeVisible();child!.name='另一条路线'
      }
      if(scenario==='archived'){
        await manage(parent.name);await page.getByRole('button',{name:'归档',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
        await remove(child!.name)
        await expect(page.locator('.world-graph-line-list button')).toHaveCount(1)
        await page.getByRole('link',{name:'回到故事',exact:true}).click()
        await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',parent.id)
        await expect(page.locator('.world-archived-note')).toBeVisible();await expect(page.getByLabel('你的下一步行动')).toHaveCount(0)
        await page.screenshot({path:path.join(artifacts,`world-lines-archived-${width}.png`)})
        await page.getByRole('link',{name:'世界线',exact:true}).click();await manage(parent.name)
        await page.getByRole('button',{name:'恢复',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
        await page.goto('/worlds');await page.locator('.world-card-art').click()
        await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',parent.id);await expect(page.getByLabel('你的下一步行动')).toBeEnabled()
        expect((await get(root)).activeLineId).toBe(parent.id)
        const currentWorld=await get(root),archivedWorld=await request.patch(root,{headers,data:{expectedVersion:currentWorld.version,status:'archived'}})
        expect(archivedWorld.ok()).toBeTruthy();await page.reload();await expect(page.locator('.world-archived-note')).toBeVisible()
        await page.getByRole('button',{name:'世界操作',exact:true}).click()
        for(const label of ['存一个节点','换一个身份另开线','调整世界资料'])await expect(page.getByRole('button',{name:label,exact:true})).toBeDisabled()
        await page.getByRole('button',{name:'关闭',exact:true}).click();await page.getByRole('link',{name:'世界线',exact:true}).click()
        await page.getByRole('button',{name:'查看全部世界线',exact:true}).click();await page.locator('.world-graph-node').last().click()
        for(const label of ['从这里继续','改选这一步'])await expect(page.getByRole('button',{name:label,exact:true})).toBeDisabled()
        await page.getByRole('button',{name:'关闭',exact:true}).click();await page.goto('/worlds')
        await page.getByRole('button',{name:'已归档',exact:true}).click();await page.locator('.world-card-copy .icon-button').click()
        await page.getByRole('button',{name:'恢复',exact:true}).click();await page.goto(`/worlds/${w}/play?line=${parent.id}`)
        await expect(page.getByLabel('你的下一步行动')).toBeEnabled()
      }else if(scenario==='shared'){
        await remove(parent.name);await expect(page.locator('.world-graph-line-list button')).toHaveCount(1)
        await page.getByRole('button',{name:'查看全部世界线',exact:true}).click()
        await page.locator('.world-graph-node').filter({has:page.locator('strong',{hasText:parent.head.title})}).first().click()
        const reading=page.getByRole('link',{name:'阅读这里',exact:true})
        await expect(reading).toHaveAttribute('href',`/worlds/${w}/read?line=${child!.id}&checkpoint=${parent.headCheckpointId}`)
        await reading.click();await expect(page.locator('.world-read-chapter .world-prose').first()).toHaveText(parent.head.text)
        await expect(page.locator('.error')).toHaveCount(0)
        expect((await get(root+'/lines/'+child!.id)).head).toEqual(child!.head)
      }else{
        await remove(selected.name)
        await expect(page.locator('.world-graph-line-list button')).toHaveCount(scenario==='last'?0:1)
        const back=page.getByRole('link',{name:'回到故事',exact:true})
        await expect(back).toHaveAttribute('href',scenario==='last'?`/worlds/new?world=${w}`:`/worlds/${w}/play?line=${parent.id}`)
        expect(new URL(page.url()).searchParams.get('line')).toBe(scenario==='last'?null:parent.id)
        await back.click()
        if(scenario==='last')await expect(page.getByRole('heading',{name:'你想成为谁？',exact:true})).toBeVisible()
        else{await expect(page.locator('.world-play')).toHaveAttribute('data-line-id',parent.id);await expect(page.getByLabel('你的下一步行动')).toBeEnabled()}
      }
      await expect(page.locator('.error')).toHaveCount(0)
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
      expect(generationPosts).toBe(0);expect(await get('/api/reader/v1/usage')).toEqual(usage)
      expect(await get(sourceUrl)).toEqual(source);expect(await get(progressUrl)).toEqual(beforeProgress)
      await page.screenshot({path:path.join(artifacts,`world-lines-${scenario}-final-${width}.png`)})
      await testInfo.attach('line-lifecycle-evidence',{body:Buffer.from(JSON.stringify({scenario,width,strictMock:true,world:w,parent:parent.id,child:child?.id||null,noGenerationPosts:true,usageUnchanged:true,sourceAndReaderProgressUnchanged:true})),contentType:'application/json'})
    }finally{
      const cleanup=await playwrightRequest.newContext({baseURL,timeout:15000})
      try{
        expect(await(await cleanup.get('/api/reader/v1/auth/me',{headers})).json()).toMatchObject({id:account.user.id,username})
        expect((await cleanup.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})).ok()).toBeTruthy()
      }finally{await cleanup.dispose()}
    }
  })
}
