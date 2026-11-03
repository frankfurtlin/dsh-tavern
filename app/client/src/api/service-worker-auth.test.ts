import {it,expect,vi} from 'vitest'
import {readFileSync} from 'node:fs'
import {createContext,runInContext} from 'node:vm'

const source=readFileSync(new URL('../../scripts/service-worker-auth.js',import.meta.url),'utf8')
function deferred<T>(){let resolve!:(value:T)=>void,reject!:(reason:unknown)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}}
function harness(transport:ReturnType<typeof vi.fn>,configuredOrigin=''){
  const listeners=new Map<string,(event:any)=>void>()
  runInContext(source,createContext({self:{location:{origin:'https://reader.test'},addEventListener:(name:string,handler:(event:any)=>void)=>listeners.set(name,handler)},AUTH_BACKEND_ORIGIN:configuredOrigin,fetch:transport,Request,Response,AbortController,URL,setTimeout,clearTimeout}))
  function request(path='/api/reader/v1/auth/refresh',options:RequestInit={},origin='https://reader.test'){
    let response:Promise<Response>|undefined,lifetime:Promise<void>|undefined
    const original=new Request(origin+path,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:'{}',...options})
    listeners.get('fetch')!({request:original,respondWith:(value:Promise<Response>)=>response=value,waitUntil:(value:Promise<void>)=>lifetime=value})
    return {original,get response(){return response!},get lifetime(){return lifetime!}}
  }
  return {request,listeners}
}
it('shares one refresh across documents, survives the old signal and gives independently readable responses',async()=>{
  const pending=deferred<Response>(),transport=vi.fn((_request:Request)=>pending.promise),{request}=harness(transport),controller=new AbortController()
  const old=request(undefined,{signal:controller.signal}),fresh=request()
  await vi.waitFor(()=>expect(transport).toHaveBeenCalledTimes(1));controller.abort()
  const detached=transport.mock.calls[0][0] as Request
  expect(detached.signal.aborted).toBe(false);expect(detached.keepalive).toBe(true);expect(detached.credentials).toBe('include');expect(detached.cache).toBe('no-store')
  pending.resolve(new Response(JSON.stringify({user:{id:'A'},accessToken:'ephemeral-test-token'})))
  const [a,b]=await Promise.all([old.response,fresh.response]);expect(a).not.toBe(b)
  expect(await a.json()).toEqual(await b.json());await Promise.all([old.lifetime,fresh.lifetime])
})
it('orders login between two refresh generations instead of returning the old account response',async()=>{
  const first=deferred<Response>(),paths:string[]=[],bodies:string[]=[],transport=vi.fn(async(request:Request)=>{
    paths.push(new URL(request.url).pathname);bodies.push(await request.text())
    return paths.length===1?first.promise:new Response(JSON.stringify({user:{id:'B'}}))
  }),{request}=harness(transport)
  const a=request(),login=request('/api/reader/v1/auth/login',{body:JSON.stringify({username:'B',password:'fixture-only'})}),b=request()
  await vi.waitFor(()=>expect(paths).toHaveLength(1));first.resolve(new Response(JSON.stringify({user:{id:'A'}})))
  expect((await(await a.response).json()).user.id).toBe('A');await login.response
  expect((await(await b.response).json()).user.id).toBe('B')
  expect(paths).toEqual(['/api/reader/v1/auth/refresh','/api/reader/v1/auth/login','/api/reader/v1/auth/refresh'])
  expect(JSON.parse(bodies[1])).toEqual({username:'B',password:'fixture-only'});await Promise.all([a.lifetime,login.lifetime,b.lifetime])
})
it('releases a failed shared request and permits the next login without a retry',async()=>{
  const transport=vi.fn().mockRejectedValueOnce(new TypeError('Network failed')).mockResolvedValueOnce(new Response('{}')),{request}=harness(transport)
  const failure=request();await expect(failure.response).rejects.toThrow('Network failed');await failure.lifetime
  const next=request('/api/reader/v1/auth/login');expect((await next.response).status).toBe(200);await next.lifetime;expect(transport).toHaveBeenCalledTimes(2)
})
it('does not persist a 401 response and preserves an empty logout response',async()=>{
  const transport=vi.fn().mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce(new Response(null,{status:204})),{request}=harness(transport)
  const failed=request();expect((await failed.response).status).toBe(401);await failed.lifetime
  const logout=request('/api/reader/v1/auth/logout');expect((await logout.response).status).toBe(204);expect(await(await logout.response).text()).toBe('');await logout.lifetime
  expect(transport).toHaveBeenCalledTimes(2)
})
it('keeps an explicitly configured backend independent while one same-origin refresh remains pending',async()=>{
  const blocked=deferred<Response>(),transport=vi.fn((request:Request)=>new URL(request.url).origin==='https://reader.test'?blocked.promise:Promise.resolve(new Response('{}'))),{request}=harness(transport,'https://other-reader.test')
  const a=request(),b=request(undefined,{},'https://other-reader.test')
  expect((await b.response).status).toBe(200);await b.lifetime;expect(transport).toHaveBeenCalledTimes(2)
  blocked.resolve(new Response('{}'));await a.response;await a.lifetime
})
it('does not intercept authentication requests to an unconfigured external origin',()=>{
  const transport=vi.fn(),{request}=harness(transport)
  expect(request(undefined,{},'https://unconfigured.test').response).toBeUndefined();expect(transport).not.toHaveBeenCalled()
})
it('leaves GET identity and generation requests to their original transport',()=>{
  const transport=vi.fn(),{request}=harness(transport)
  expect(request('/api/reader/v1/auth/me',{method:'GET',body:undefined}).response).toBeUndefined()
  expect(request('/api/reader/v1/generation-tasks').response).toBeUndefined();expect(transport).not.toHaveBeenCalled()
})
it('refuses an oversized auth body before network use and releases the queue',async()=>{
  const transport=vi.fn().mockResolvedValue(new Response('{}')),{request}=harness(transport)
  const oversized=request('/api/reader/v1/auth/login',{body:'x'.repeat(65537)})
  await expect(oversized.response).rejects.toThrow('Authentication request exceeds keepalive limit');await oversized.lifetime;expect(transport).not.toHaveBeenCalled()
  const valid=request();expect((await valid.response).status).toBe(200);await valid.lifetime;expect(transport).toHaveBeenCalledTimes(1)
})
