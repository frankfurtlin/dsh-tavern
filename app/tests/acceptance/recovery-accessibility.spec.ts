import {chooseCustomChannel} from '../e2e/helpers/landing'
import {test,expect,type Page} from '@playwright/test'
import {spawnSync} from 'node:child_process'
import {createServer} from 'node:http'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'
import {randomUUID} from 'node:crypto'

// Additional real-browser acceptance. All accounts, fault injection and worker
// control belong exclusively to the explicit local deterministic fixture.
const root=path.resolve('..'),artifacts=process.env.TEST_ARTIFACTS_DIR||path.join(root,'artifacts/tests')
mkdirSync(artifacts,{recursive:true})
let username=''
async function login(page:Page){
  const creating=!username;username||='acceptance_'+Date.now().toString(36)
  await page.goto(creating?'/register':'/login')
  await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill('acceptance-fixture-password')
  const response=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:creating?'创建账号并进入':'登录',exact:true}).click()
  await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const identity=await(await response).json()
  return {accessToken:identity.accessToken as string}
}
async function readDemo(page:Page){
  await page.getByRole('button',{name:'加入示例书'}).click()
  await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
  if(await page.locator('.reader-body h1').innerText()!=='第一章 渡口'){
    await page.getByRole('button',{name:'打开目录',exact:true}).click()
    await page.getByRole('dialog').locator('.chapter-link').first().click()
  }
}
function fixtureWorker(action:'stop'|'start'){
  const result=spawnSync('docker',['compose','-f',path.join(root,'compose.e2e.yaml'),action,'worker'],{cwd:root,encoding:'utf8',timeout:60000})
  expect(result.status,'Only isolated mock worker may be controlled').toBe(0)
}
function save(name:string,data:unknown){writeFileSync(path.join(artifacts,name),JSON.stringify(data,null,2))}

for(const width of [360,390,430,1440])test(`F27 keyboard focus, 32px text and accessible controls ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});await login(page);await page.goto('/settings')
  await page.getByRole('slider').nth(0).focus();await page.keyboard.press('End')
  await page.getByRole('slider').nth(1).focus();await page.keyboard.press('End')
  await page.getByLabel('主题').selectOption('dark')
  await page.getByRole('button',{name:'保存阅读设置'}).click()
  await expect(page.getByRole('status')).toContainText('阅读设置已保存')
  await page.goto('/library');await readDemo(page)
  await expect(page.locator('.reader-body')).toHaveCSS('font-size','32px')
  await expect(page.locator('.reader-body')).toHaveCSS('line-height','61.6px')
  const position=await page.locator('.paragraph').first().evaluate(e=>e.getBoundingClientRect().top)
  const trigger=page.getByRole('button',{name:'打开目录',exact:true})
  await trigger.click();const dialog=page.getByRole('dialog',{name:'目录',exact:true}),close=dialog.getByRole('button',{name:'关闭',exact:true})
  await expect(close).toBeFocused()
  const controls=dialog.locator('button:not(:disabled),a,input,textarea,select,[tabindex="0"]'),count=await controls.count()
  expect(count).toBeGreaterThan(1)
  await page.keyboard.press('Shift+Tab');await expect(controls.last()).toBeFocused()
  await page.keyboard.press('Tab');await expect(close).toBeFocused()
  for(let i=0;i<count+2;i++){
    await page.keyboard.press('Tab')
    expect(await dialog.evaluate(e=>e.contains(document.activeElement))).toBe(true)
  }
  const accessibleTree=await dialog.ariaSnapshot()
  expect(accessibleTree).toContain('dialog "目录"');expect(accessibleTree).toContain('button "关闭"')
  await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();await expect(trigger).toBeFocused()
  const returnedPosition=await page.locator('.paragraph').first().evaluate(e=>e.getBoundingClientRect().top)
  expect(Math.abs(returnedPosition-position)).toBeLessThanOrEqual(1)
  const overflow=await page.evaluate(()=>({width:innerWidth,documentWidth:document.documentElement.scrollWidth}))
  expect(overflow.documentWidth).toBeLessThanOrEqual(width+1)
  const toolbar=page.locator('.reader-toolbar button'),hits=[]
  for(let i=0;i<await toolbar.count();i++){
    const button=toolbar.nth(i);const hit=await button.evaluate(e=>{
      const box=e.getBoundingClientRect(),at=document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)
      return {label:e.textContent?.trim(),width:box.width,height:box.height,visible:at===e||!!at&&e.contains(at)}
    })
    expect(hit.width).toBeGreaterThanOrEqual(44);expect(hit.height).toBeGreaterThanOrEqual(44);expect(hit.visible).toBe(true);hits.push(hit)
  }
  await page.screenshot({path:path.join(artifacts,`accessibility-${width}.png`),fullPage:true})
  save(`accessibility-${width}.json`,{width,fontSize:32,lineHeight:2.2,theme:'dark',focusCycleSteps:count+2,focusRestored:true,paragraphPositionBefore:position,paragraphPositionAfter:returnedPosition,overflow,toolbar:hits,accessibleTree,passed:true})
  await page.goto('/settings');await page.getByRole('slider').nth(0).focus();await page.keyboard.press('Home')
  for(let i=0;i<4;i++)await page.keyboard.press('ArrowRight')
  await page.getByRole('slider').nth(1).focus();await page.keyboard.press('Home')
  for(let i=0;i<5;i++)await page.keyboard.press('ArrowRight')
  await page.getByLabel('主题').selectOption('light')
  await page.getByRole('button',{name:'保存阅读设置'}).click();await expect(page.getByRole('status')).toContainText('阅读设置已保存')
})

test('F27 ordinary text color tokens exceed 4.5 contrast and reduced motion applies',async({page})=>{
  await login(page);await page.goto('/settings');const themes=[]
  for(const theme of ['light','dark']){
    await page.getByLabel('主题').selectOption(theme)
    const saved=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/api/reader/v1/settings'))
    await page.getByRole('button',{name:'保存阅读设置'}).click();expect((await saved).status()).toBe(200)
    await expect(page.locator('html')).toHaveAttribute('data-theme',theme)
    const pairs=await page.evaluate(()=>{
      const styles=getComputedStyle(document.documentElement)
      function luminance(color:string){let hex=color.trim().replace('#','');if(hex.length===3)hex=[...hex].map(c=>c+c).join('');if(!/^[0-9a-f]{6}$/i.test(hex))throw new Error('Unsupported CSS color '+color);const rgb=[0,2,4].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(n=>n<=0.04045?n/12.92:((n+0.055)/1.055)**2.4);return rgb[0]*0.2126+rgb[1]*0.7152+rgb[2]*0.0722}
      return [['text','bg'],['text','surface'],['muted','bg'],['muted','surface'],['muted','tint'],['primary','bg'],['primary','surface'],['primary','tint'],['error','surface'],['surface','primary']].map(([fg,bg])=>{
        const foreground=styles.getPropertyValue('--'+fg).trim(),background=styles.getPropertyValue('--'+bg).trim(),a=luminance(foreground),b=luminance(background)
        return {foregroundToken:fg,backgroundToken:bg,foreground,background,ratio:(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05)}
      })
    })
    for(const pair of pairs)expect(pair.ratio,theme+' '+pair.foregroundToken+'/'+pair.backgroundToken).toBeGreaterThanOrEqual(4.5)
    themes.push({theme,pairs})
  }
  await page.emulateMedia({reducedMotion:'reduce'})
  const transition=await page.getByRole('button',{name:'保存阅读设置'}).evaluate(e=>getComputedStyle(e).transitionDuration)
  expect(transition).toBe('0s')
  save('contrast-and-motion.json',{themes,reducedMotionTransitionDuration:transition,passed:true})
})

for(const kind of ['http503','slow-stream-cut'] as const)test(`F21 real task recovers via polling after three ${kind} event failures`,async({page,context,baseURL})=>{
  expect(baseURL,'Fault injection must target the explicit private mock fixture').toBe('http://127.0.0.1:8811')
  let workerStopped=false,server:ReturnType<typeof createServer>|undefined,cdp:Awaited<ReturnType<typeof context.newCDPSession>>|undefined
  const cuts:{afterSeq:number;upstreamStatus:number;bytes:number;abruptClose:boolean}[]=[],snapshots:{id:string;at:number}[]=[],posts:string[]=[]
  let bearer='',taskId=''
  await page.addInitScript(()=>{
    const stats=(window as any).__eventReads={bytes:0,chunks:0,errors:0,httpErrors:0},original=window.fetch
    window.fetch=async(...args)=>{
      const response=await original(...args)
      if(String(args[0]).includes('/events?')&&!response.ok)stats.httpErrors++
      if(String(args[0]).includes('/events?')&&response.body){
        const body=response.body,getReader=body.getReader.bind(body)
        body.getReader=((...options:any[])=>{
          const reader=(getReader as any)(...options),read=reader.read.bind(reader)
          reader.read=async()=>{try{const value=await read();if(value.value){stats.bytes+=value.value.byteLength;stats.chunks++}return value}catch(error){stats.errors++;throw error}}
          return reader
        }) as typeof body.getReader
      }
      return response
    }
  })
  page.on('request',r=>{
    const url=new URL(r.url())
    if(r.method()==='POST'&&url.pathname.endsWith('/generation-tasks')){posts.push(url.pathname);bearer=r.headers().authorization||''}
    if(posts.length&&r.method()==='GET'&&/\/tasks\/[^/]+$/.test(url.pathname))snapshots.push({id:url.pathname.split('/').at(-1)!,at:Date.now()})
  })
  try{
    const identity=await login(page);await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
    await page.getByLabel('通道名称').fill('网络恢复 '+kind);await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
    await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('NETWORK-FIXTURE-KEY-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
    const modelCard=page.locator('article').filter({has:page.getByRole('heading',{name:new RegExp('^网络恢复 '+kind+'(?: 默认模型)? 可用配置$')})})
    await expect(modelCard).toBeVisible()
    if(await modelCard.getByRole('button',{name:'设为默认',exact:true}).count())await modelCard.getByRole('button',{name:'设为默认',exact:true}).click()
    await expect(modelCard.getByRole('heading',{name:'网络恢复 '+kind+' 默认模型 可用配置'})).toBeVisible()
    await page.goto('/library');await readDemo(page);await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'改剧情',exact:true}).click()
    await expect(page.getByRole('button',{name:/^开始写作/})).toBeEnabled()
    // Opening the actual writing panel saves the selected reading boundary.
    // Its new parent run uses polling. Preserve this test's real SSE fault
    // coverage through the supported saved legacy task history, not a fake run.
    await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
    const bookId=new URL(page.url()).pathname.split('/')[2],headers={Authorization:'Bearer '+identity.accessToken}
    async function getApi(route:string){const response=await context.request.get(baseURL+'/api/reader/v1'+route,{headers});expect(response.status()).toBe(200);return response.json()}
    async function postApi(route:string,body:unknown){const response=await context.request.post(baseURL+'/api/reader/v1'+route,{headers:{...headers,'Idempotency-Key':randomUUID()},data:body});expect(response.status()).toBe(201);return response.json()}
    const book=await getApi('/books/'+bookId),profiles=await getApi('/model-profiles')
    const profile=profiles.items.find((item:any)=>item.name==='网络恢复 '+kind);expect(profile?.id).toBeTruthy()
    const directory=await getApi('/branches/'+book.originalBranchId+'/chapters?revisionId='+book.originalRevisionId)
    const chapter=await getApi('/branches/'+book.originalBranchId+'/chapters/'+directory.items[0].id+'?revisionId='+book.originalRevisionId)
    const anchor=chapter.blocks[1].endAnchor
    const operation={operation:'counterfactual',profileId:profile.id,anchor,expectedHeadRevisionId:book.originalRevisionId,
      instruction:'让林渡留下救阿岑',target:{mode:'new_child_branch',name:'网络恢复 '+kind}}
    const plan=await postApi('/operation-plans',operation)
    const consent=await postApi('/consents',{planId:plan.planId,planHash:plan.planHash,profileId:profile.id,
      maxCalls:plan.maxCalls,inputTokens:plan.inputTokens,outputTokens:plan.outputTokens})
    fixtureWorker('stop');workerStopped=true
    if(kind==='http503'){
      await page.route('**/tasks/*/events**',async route=>{
        cuts.push({afterSeq:Number(new URL(route.request().url()).searchParams.get('afterSeq')),upstreamStatus:503,bytes:0,abruptClose:false})
        await route.fulfill({status:503,body:'Explicit local acceptance fault'})
      })
    }else{
      server=createServer(async(req,res)=>{
        res.setHeader('Access-Control-Allow-Origin',baseURL!);res.setHeader('Access-Control-Allow-Credentials','true')
        res.setHeader('Access-Control-Allow-Headers','authorization,content-type');res.setHeader('Access-Control-Allow-Methods','GET,OPTIONS')
        if(req.method==='OPTIONS'){res.writeHead(204);res.end();return}
        const abort=new AbortController();res.on('close',()=>abort.abort());res.on('error',()=>{})
        try{
          const upstream=await fetch(baseURL+req.url!,{headers:{authorization:req.headers.authorization||'',cookie:req.headers.cookie||''},signal:abort.signal})
          expect(upstream.status).toBe(200);res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store'});res.flushHeaders()
          const first=await upstream.body!.getReader().read();expect(first.value?.byteLength).toBeGreaterThan(1)
          const bytes=first.value!.slice(0,Math.min(32,first.value!.byteLength-1));res.write(bytes)
          cuts.push({afterSeq:Number(new URL(req.url!,baseURL).searchParams.get('afterSeq')),upstreamStatus:upstream.status,bytes:bytes.byteLength,abruptClose:true})
          setTimeout(()=>res.destroy(),1000)
        }catch(error){if(!abort.signal.aborted){res.destroy();console.error('Loopback stream fault proxy failed:',(error as Error).name)}}
      })
      await new Promise<void>(resolve=>server!.listen(0,'127.0.0.1',resolve))
      const port=(server.address() as {port:number}).port
      await page.route('**/tasks/*/events**',route=>route.continue({url:'http://127.0.0.1:'+port+new URL(route.request().url()).pathname+new URL(route.request().url()).search}))
      cdp=await context.newCDPSession(page);await cdp.send('Network.enable')
      await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:300,downloadThroughput:8*1024,uploadThroughput:8*1024})
    }
    const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/generation-tasks'))
    const submitted=await page.evaluate(async({operation,consentId,token,key})=>{
      const response=await fetch('/api/reader/v1/generation-tasks',{method:'POST',credentials:'include',
        headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':key},
        body:JSON.stringify({...operation,consentId})})
      return {status:response.status,body:await response.json()}
    },{operation,consentId:consent.consentId,token:identity.accessToken,key:randomUUID()})
    expect(submitted.status).toBe(202);taskId=(await(await created).json()).taskId
    expect(submitted.body.taskId).toBe(taskId)
    const queuedResponse=await page.request.get(baseURL+'/api/reader/v1/tasks/'+taskId,{headers:{Authorization:bearer}}),queued=await queuedResponse.json()
    expect(queuedResponse.status()).toBe(200);expect(queued.state).toBe('queued')
    await page.getByRole('button',{name:'改剧情',exact:true}).click()
    await expect(page.getByLabel('写作记录')).toHaveValue('task:'+taskId)
    await expect.poll(()=>cuts.length,{timeout:20000}).toBe(3)
    await expect.poll(()=>page.evaluate(kind=>(window as any).__eventReads[kind==='http503'?'httpErrors':'errors'],kind),{timeout:10000}).toBe(3)
    const fallbackAt=snapshots.length
    await expect.poll(()=>snapshots.length-fallbackAt,{timeout:10000}).toBeGreaterThanOrEqual(2)
    expect(cuts).toHaveLength(3);expect(snapshots.length-fallbackAt).toBeGreaterThanOrEqual(2)
    const pollingSnapshots=snapshots.slice(fallbackAt),pollingIntervalsMs=pollingSnapshots.slice(1).map((s,i)=>s.at-pollingSnapshots[i].at)
    expect(pollingIntervalsMs.length).toBeGreaterThan(0);expect(Math.min(...pollingIntervalsMs)).toBeGreaterThanOrEqual(1800)
    expect(snapshots.every(s=>s.id===taskId)).toBe(true);expect(cuts.every(c=>c.afterSeq===queued.eventCursor)).toBe(true);expect(posts).toHaveLength(1)
    const readEvidence=await page.evaluate(()=>(window as any).__eventReads)
    if(kind==='slow-stream-cut'){expect(readEvidence.bytes).toBeGreaterThan(0);expect(readEvidence.chunks).toBeGreaterThanOrEqual(3);expect(readEvidence.errors).toBeGreaterThanOrEqual(3)}
    fixtureWorker('start');workerStopped=false
    await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeEnabled({timeout:30000})
    expect(posts).toHaveLength(1);expect(cuts).toHaveLength(3)
    const taskResponse=await page.request.get(baseURL+'/api/reader/v1/tasks/'+taskId,{headers:{Authorization:bearer}}),task=await taskResponse.json()
    expect(taskResponse.status()).toBe(200);expect(task.id).toBe(taskId);expect(task.state).toBe('succeeded');expect(task.persistenceState).toBe('saved')
    const usageResponse=await page.request.get(baseURL+'/api/reader/v1/usage',{headers:{Authorization:bearer}}),usage=await usageResponse.json()
    expect(usageResponse.status()).toBe(200)
    const calls=usage.items.filter((item:any)=>item.taskId===taskId);expect(calls.length).toBeGreaterThan(0)
    expect(new Set(calls.map((item:any)=>item.providerCallId)).size).toBe(calls.length)
    await page.screenshot({path:path.join(artifacts,`network-${kind}.png`)})
    save(`network-${kind}.json`,{kind,taskId,observer:'legacy_saved_task_watchTask',provider:'isolated deterministic fixture',paidCalls:0,generationPosts:posts.length,eventConnections:cuts,initialSnapshotCursor:queued.eventCursor,snapshotGets:snapshots.length,pollingSnapshotsAfterThirdFailure:snapshots.length-fallbackAt,pollingRequestTimesMs:pollingSnapshots.map(s=>s.at),pollingIntervalsMs,clientStreamReads:readEvidence,latencyMs:kind==='slow-stream-cut'?300:0,bytesPerSecond:kind==='slow-stream-cut'?8192:null,finalTask:{id:task.id,state:task.state,persistenceState:task.persistenceState},usageRecords:calls.length,uniqueProviderCallIds:calls.length,passed:true})
  }finally{
    if(cdp)await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1}).catch(()=>{})
    await page.unrouteAll({behavior:'ignoreErrors'})
    if(server){server.closeAllConnections();await new Promise<void>(resolve=>server!.close(()=>resolve()))}
    if(workerStopped)fixtureWorker('start')
  }
})
