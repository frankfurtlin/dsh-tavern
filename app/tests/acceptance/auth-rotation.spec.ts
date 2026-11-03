import {test,expect,type Page,type BrowserContext} from '@playwright/test'
import {createServer} from 'node:http'
import {spawnSync} from 'node:child_process'
import {createHash} from 'node:crypto'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'
const root=path.resolve('..'),artifacts=process.env.TEST_ARTIFACTS_DIR||path.join(root,'artifacts/tests'),base='http://127.0.0.1:8811'
mkdirSync(artifacts,{recursive:true})
let username=''
function gate(){let resolve!:()=>void;const promise=new Promise<void>(r=>resolve=r);return{promise,resolve}}
function record(name:string,data:unknown){writeFileSync(path.join(artifacts,name),JSON.stringify(data,null,2))}
function state(id:string){
  expect(id).toMatch(/^[a-f0-9]{32}$/)
  const query=`SELECT json_build_object('rotations',jsonb_array_length(previous_hashes),'revoked',revoked_at IS NOT NULL) FROM device_sessions WHERE id='${id}';`
  const result=spawnSync('docker',['compose','-f',path.join(root,'compose.e2e.yaml'),'exec','-T','db','psql','-U','test','-d','reader_e2e_test','-At','-c',query],{encoding:'utf8',timeout:30000})
  expect(result.status).toBe(0);return JSON.parse(result.stdout.trim()) as {rotations:number;revoked:boolean}
}
async function login(page:Page){
  const creating=!username;username||='authnav_'+Date.now().toString(36)
  await page.goto(creating?'/register':'/login');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill('auth-navigation-fixture-password')
  const authenticated=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:creating?'创建账号并进入':'登录',exact:true}).click()
  const data=await(await authenticated).json();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible();return data
}
async function cookieHash(context:BrowserContext){const cookie=(await context.cookies(base+'/api/reader/v1/auth/refresh')).find(c=>c.name==='reader_refresh');expect(cookie?.httpOnly).toBe(true);return createHash('sha256').update(cookie!.value).digest('hex')}
async function cacheAuthRequests(page:Page){return page.evaluate(async()=>{const urls=[];for(const name of await caches.keys())for(const request of await(await caches.open(name)).keys())if(new URL(request.url).pathname.startsWith('/api/'))urls.push(request.url);return urls})}
async function holdActualRefresh(context:BrowserContext){
  const entered=gate(),release=gate(),calls:{status:number;at:number}[]=[],ownedRequests:string[]=[]
  const server=createServer(async(req,res)=>{
    res.setHeader('Access-Control-Allow-Origin',base);res.setHeader('Access-Control-Allow-Credentials','true');res.setHeader('Access-Control-Allow-Headers','content-type,authorization')
    if(req.method==='OPTIONS'){res.writeHead(204);res.end();return}
    try{
      const chunks=[];for await(const chunk of req)chunks.push(chunk)
      const upstream=await fetch(base+'/api/reader/v1/auth/refresh',{method:'POST',headers:{cookie:req.headers.cookie||'',origin:base,'content-type':'application/json'},body:Buffer.concat(chunks)})
      const content=Buffer.from(await upstream.arrayBuffer());calls.push({status:upstream.status,at:Date.now()});entered.resolve()
      await release.promise
      const cookies=upstream.headers.getSetCookie();if(cookies.length)res.setHeader('Set-Cookie',cookies)
      res.writeHead(upstream.status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(content)
    }catch{res.writeHead(502);res.end('Explicit fixture proxy failed');entered.resolve()}
  })
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));const port=(server.address() as {port:number}).port
  context.on('request',request=>{if(request.serviceWorker()&&new URL(request.url()).pathname.startsWith('/api/reader/v1/auth/'))ownedRequests.push(new URL(request.url()).pathname)})
  await context.route('**/api/reader/v1/auth/refresh',route=>route.continue({url:`http://127.0.0.1:${port}/api/reader/v1/auth/refresh`}))
  return {entered,release,calls,ownedRequests,async close(){release.resolve();await context.unrouteAll({behavior:'ignoreErrors'});server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()))}}
}
test('F38 rotating cookie response survives immediate hard navigation with one server rotation',async({page,context,baseURL})=>{
  expect(baseURL).toBe(base);const authenticated=await login(page);await page.goto('/settings');await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible()
  const before=state(authenticated.sessionId),beforeCookie=await cookieHash(context),proxy=await holdActualRefresh(context)
  const countRefresh=()=>{const original=window.fetch;window.fetch=(...args)=>{if(String(args[0]).endsWith('/auth/refresh'))sessionStorage.setItem('navigation-refresh-calls',String(Number(sessionStorage.getItem('navigation-refresh-calls')||0)+1));return original(...args)}}
  await page.addInitScript(countRefresh);await page.evaluate(countRefresh)
  try{
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await proxy.entered.promise
    expect(proxy.calls[0].status).toBe(200);expect(state(authenticated.sessionId).rotations-before.rotations).toBe(1);expect(await cookieHash(context)).toBe(beforeCookie)
    await page.goto('/settings');await page.waitForFunction(()=>Number(sessionStorage.getItem('navigation-refresh-calls'))>=2)
    // Deliberately keep the successful upstream response hidden during navigation.
    await page.waitForTimeout(500);expect(proxy.calls).toHaveLength(1);proxy.release.resolve()
    await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible();const after=state(authenticated.sessionId),afterCookie=await cookieHash(context)
    expect(after.rotations-before.rotations).toBe(1);expect(after.revoked).toBe(false);expect(afterCookie).not.toBe(beforeCookie);expect(proxy.calls).toHaveLength(1);expect(await cacheAuthRequests(page)).toEqual([])
    record('auth-navigation.json',{passed:true,actualUpstreamStatuses:proxy.calls.map(c=>c.status),rotationDelta:1,revoked:false,oldCookieSha256:beforeCookie,newCookieSha256:afterCookie,hiddenResponseDuringNavigationMs:500,pageRefreshInvocations:await page.evaluate(()=>Number(sessionStorage.getItem('navigation-refresh-calls'))),authCached:false,paidCalls:0})
  }finally{await proxy.close()}
})
test('F10 refresh logout login B and refresh B preserve cookie mutation order',async({page,context,baseURL})=>{
  expect(baseURL).toBe(base);const a=await login(page),bUsername='authB_'+Date.now().toString(36),password='auth-navigation-fixture-password'
  const registered=await context.request.post(base+'/api/reader/v1/auth/register',{data:{username:bUsername,password,clientType:'web',deviceName:'fixture'}});expect(registered.status()).toBe(201);const bId=(await registered.json()).id
  const before=state(a.sessionId),proxy=await holdActualRefresh(context)
  try{
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await proxy.entered.promise;expect(proxy.calls[0].status).toBe(200)
    await page.evaluate(({accessToken,bUsername,password})=>{
      const w=window as any
      w.__orderedAuth=Promise.all([
        fetch('/api/reader/v1/auth/logout',{method:'POST',credentials:'include',headers:{Authorization:'Bearer '+accessToken}}).then(r=>r.status),
        fetch('/api/reader/v1/auth/login',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:bUsername,password,clientType:'web',deviceName:'fixture B'})}).then(async r=>{const d=await r.json();return{status:r.status,userId:d.user?.id,sessionId:d.sessionId}}),
        fetch('/api/reader/v1/auth/refresh',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:'{}'}).then(async r=>({status:r.status,userId:(await r.json()).user?.id}))
      ])
    },{accessToken:a.accessToken,bUsername,password})
    await page.waitForTimeout(500);expect(proxy.calls).toHaveLength(1);proxy.release.resolve()
    const results=await page.evaluate(()=>(window as any).__orderedAuth)
    expect(results[0]).toBe(200);expect(results[1].status).toBe(200);expect(results[1].userId).toBe(bId);expect(results[2]).toEqual({status:200,userId:bId})
    const aAfter=state(a.sessionId),bAfter=state(results[1].sessionId);expect(aAfter.revoked).toBe(true);expect(aAfter.rotations-before.rotations).toBe(1);expect(bAfter.revoked).toBe(false);expect(bAfter.rotations).toBe(1)
    expect(proxy.calls).toHaveLength(2);expect(proxy.ownedRequests.slice(-4)).toEqual(['/api/reader/v1/auth/refresh','/api/reader/v1/auth/logout','/api/reader/v1/auth/login','/api/reader/v1/auth/refresh']);expect(await cacheAuthRequests(page)).toEqual([])
    record('auth-mutation-order.json',{passed:true,orderedOperations:proxy.ownedRequests.slice(-4),statuses:[results[0],results[1].status,results[2].status],aRotationDelta:1,aRevokedByLogout:true,bRotations:1,bRevoked:false,finalCookieOwnerIsB:true,authCached:false,paidCalls:0})
  }finally{await proxy.close()}
})
test('F31 old shell-only service worker upgrades before authenticated initialization',async({page,context,baseURL})=>{
  expect(baseURL).toBe(base);const authenticated=await login(page),before=state(authenticated.sessionId)
  await page.evaluate(async()=>{
    const registration=await navigator.serviceWorker.register('/legacy-shell-worker.js',{scope:'/'}),worker=registration.installing||registration.waiting||registration.active!
    if(worker.state!=='activated')await new Promise<void>(resolve=>worker.addEventListener('statechange',()=>{if(worker.state==='activated')resolve()}))
    if(!navigator.serviceWorker.controller?.scriptURL.endsWith('/legacy-shell-worker.js'))await new Promise<void>(resolve=>navigator.serviceWorker.addEventListener('controllerchange',()=>resolve(),{once:true}))
  })
  expect(await page.evaluate(()=>navigator.serviceWorker.controller!.scriptURL)).toBe(base+'/legacy-shell-worker.js')
  await page.reload();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible();expect(await page.evaluate(()=>navigator.serviceWorker.controller!.scriptURL)).toBe(base+'/sw.js')
  const after=state(authenticated.sessionId);expect(after.rotations-before.rotations).toBe(1);expect(after.revoked).toBe(false);expect(await cacheAuthRequests(page)).toEqual([]);expect(context.serviceWorkers().some(w=>w.url()===base+'/sw.js')).toBe(true)
  record('auth-worker-upgrade.json',{passed:true,oldController:'legacy-shell-worker.js',newController:'sw.js',rotationDelta:1,revoked:false,authCached:false,paidCalls:0})
})
test('F10 unavailable service worker fails closed before any authentication request',async({page,baseURL})=>{
  expect(baseURL).toBe(base);let authRequests=0
  await page.addInitScript(()=>{let prototype:object|null=Object.getPrototypeOf(navigator);while(prototype){if(Object.prototype.hasOwnProperty.call(prototype,'serviceWorker')){Reflect.deleteProperty(prototype,'serviceWorker');break}prototype=Object.getPrototypeOf(prototype)}})
  page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/reader/v1/auth/'))authRequests++})
  await page.goto('/library');await expect(page.getByRole('alert')).toHaveText('无法准备安全会话，请检查网络后重试。');await expect(page.getByRole('button',{name:'重新加载',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>'serviceWorker' in navigator)).toBe(false);expect(authRequests).toBe(0)
  record('auth-worker-unavailable.json',{passed:true,serviceWorkerAvailable:false,authRequests:0,recoveryButtonVisible:true,paidCalls:0})
})
