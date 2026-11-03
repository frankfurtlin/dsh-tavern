import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useApp} from './app'
import type {User} from '../api/types'
import {api,ApiError,refreshAuth,refreshedSession} from '../api'
import {cacheGet,cacheSet,clearPrivate} from '../platform/bridge'

const scope=vi.hoisted(()=>({owner:'',base:'',epoch:0}))
vi.mock('../api',()=>({api:vi.fn(),setAccess:vi.fn(),refreshAuth:vi.fn(),refreshedSession:vi.fn(),authGeneration:()=>scope.epoch,beginAuthentication:()=>++scope.epoch,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({cacheSet:vi.fn(),cacheGet:vi.fn(),clearPrivate:vi.fn(),setAccount:vi.fn((id:string)=>{scope.owner=id}),secureRefresh:vi.fn(),clientType:'web',accountId:()=>scope.owner,backendBase:()=>scope.base}))
beforeEach(()=>{scope.owner='';scope.base='';scope.epoch=0;vi.clearAllMocks();vi.mocked(api).mockReset();vi.mocked(refreshAuth).mockReset();vi.mocked(cacheGet).mockReset();vi.mocked(cacheSet).mockReset();setActivePinia(createPinia());vi.stubGlobal('window',new EventTarget());vi.stubGlobal('navigator',{onLine:true});vi.stubGlobal('innerWidth',390);const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)})})
afterEach(()=>{vi.mocked(refreshedSession).mockReset();vi.unstubAllGlobals()})
it('uses 18px on mobile and 20px on desktop until reading settings are saved',()=>{
  const store=useApp();store.user={id:'fresh',settings:{version:1,fontSize:18,lineHeight:1.8,theme:'light'}} as User
  expect(store.settings.fontSize).toBe(18);vi.stubGlobal('innerWidth',1440);window.dispatchEvent(new Event('resize'));expect(store.settings.fontSize).toBe(20)
  vi.stubGlobal('innerWidth',430);window.dispatchEvent(new Event('resize'));expect(store.settings.fontSize).toBe(18)
})
it('keeps a saved account font size across viewport changes, including an explicit 18px choice',()=>{
  const store=useApp();store.user={id:'saved',settings:{version:2,fontSize:18,lineHeight:1.8,theme:'dark'}} as User
  vi.stubGlobal('innerWidth',1440);window.dispatchEvent(new Event('resize'));expect(store.settings.fontSize).toBe(18);expect(store.settings.theme).toBe('dark')
  store.user.settings.fontSize=24;vi.stubGlobal('innerWidth',360);window.dispatchEvent(new Event('resize'));expect(store.settings.fontSize).toBe(24)
})

it('restores only the same cached owner when refresh succeeds but identity transport is offline',async()=>{
  scope.owner='cached-owner';scope.base='https://own-reader.example';const cached={id:scope.owner,settings:{version:2,fontSize:20,lineHeight:1.8,theme:'light'}} as User
  vi.mocked(refreshAuth).mockResolvedValue(true);vi.mocked(api).mockRejectedValue(new ApiError('NETWORK_UNAVAILABLE','Offline',0));vi.mocked(cacheGet).mockResolvedValue(cached)
  const store=useApp();await store.init();expect(store.user?.id).toBe(cached.id);expect(store.online).toBe(false);expect(store.booted).toBe(true);expect(cacheGet).toHaveBeenCalledWith('identity')
})
it.each(['identity-401','identity-403','revoked','changed-owner','changed-server','changed-auth','wrong-cached-owner'] as const)('does not restore cached identity after %s',async(reason)=>{
  scope.owner='cached-owner';scope.base='https://own-reader.example';vi.mocked(refreshAuth).mockResolvedValue(true)
  vi.mocked(api).mockImplementation(async()=>{if(reason==='revoked')localStorage.setItem('reader-session-revoked','1');if(reason==='changed-owner')scope.owner='other-owner';if(reason==='changed-server')scope.base='https://other-reader.example';if(reason==='changed-auth')scope.epoch++;const status=reason==='identity-401'?401:reason==='identity-403'?403:0;throw new ApiError(status?'UNAUTHORIZED':'NETWORK_UNAVAILABLE','Unavailable',status)})
  vi.mocked(cacheGet).mockResolvedValue({id:reason==='wrong-cached-owner'?'other-owner':'cached-owner'} as User)
  const store=useApp();await store.init();expect(store.user).toBeNull()
})
it('discards a late cached identity after the authentication generation changes during its read',async()=>{
  scope.owner='cached-owner';vi.mocked(refreshAuth).mockResolvedValue(true);vi.mocked(api).mockRejectedValue(new ApiError('NETWORK_UNAVAILABLE','Offline',0))
  let resolve!:(value:User)=>void;vi.mocked(cacheGet).mockReturnValue(new Promise<User>(r=>{resolve=r}));const store=useApp(),initializing=store.init()
  await vi.waitFor(()=>expect(cacheGet).toHaveBeenCalledWith('identity'));scope.epoch++;resolve({id:'cached-owner'} as User);await initializing;expect(store.user).toBeNull()
})
it('opens from one authenticated bootstrap without identity or world config round trips',async()=>{
 const current={id:'bootstrap-owner',username:'fixture',sessionId:'session',aiMode:'real',settings:{version:1,fontSize:18,lineHeight:1.8,theme:'light'}} as User
 vi.mocked(refreshAuth).mockResolvedValue(true);vi.mocked(refreshedSession).mockReturnValue({version:1,user:current,world:{enabled:true,ready:true},defaultModels:{text:{enabled:true,modelId:'text'},image:{enabled:true,modelId:'image'}}})
 const store=useApp();await store.init();expect(store.user?.id).toBe(current.id);expect(store.worldEnabled).toBe(true);expect(store.defaultModels?.text.enabled).toBe(true);expect(api).not.toHaveBeenCalled();expect(cacheSet).toHaveBeenCalledWith('identity',current)
})
it('discards bootstrap identity when account changes while its cache is being written',async()=>{
 const current={id:'bootstrap-owner'} as User;vi.mocked(refreshAuth).mockResolvedValue(true);vi.mocked(refreshedSession).mockReturnValue({version:1,user:current,world:{enabled:true,ready:true},defaultModels:{text:{enabled:true,modelId:'text'},image:{enabled:true,modelId:'image'}}})
 let release!:()=>void;vi.mocked(cacheSet).mockReturnValue(new Promise<void>(r=>{release=r}));const store=useApp(),starting=store.init();await vi.waitFor(()=>expect(cacheSet).toHaveBeenCalled());scope.owner='other-owner';release();await starting;expect(store.user).toBeNull();expect(store.defaultModels).toBeUndefined()
})

function loginResponses(){
  const current={id:'login-owner',username:'fixture',settings:{version:1,fontSize:18,lineHeight:1.8,theme:'light'}} as User
  vi.mocked(api).mockImplementation(async path=>path==='/auth/login'?{accessToken:'fixture',user:{id:current.id}}:path==='/auth/me'?current:{enabled:true,ready:true})
  return current
}
it('finishes login before publishing identity and clears the logout marker for subsequent reloads',async()=>{
  const current=loginResponses(),store=useApp();localStorage.setItem('reader-session-revoked','1')
  let release!:()=>void;vi.mocked(cacheSet).mockReturnValue(new Promise<void>(resolve=>{release=resolve}))
  const loggingIn=store.login('fixture','fixture-password',()=>!store.user)
  await vi.waitFor(()=>expect(cacheSet).toHaveBeenCalledWith('identity',current))
  expect(store.user).toBeNull();expect(localStorage.getItem('reader-session-revoked')).toBe('1')
  release();await loggingIn
  expect(localStorage.getItem('reader-session-revoked')).toBeNull();expect(store.user?.id).toBe(current.id);expect(store.worldEnabled).toBe(true)
})
it('distinguishes explicit account exit from session expiry and resets it on login',async()=>{
  const store=useApp();scope.owner='previous-owner';vi.mocked(api).mockResolvedValue({})
  vi.mocked(clearPrivate).mockImplementationOnce(async()=>{scope.owner='';scope.epoch++})
  await store.logout();expect(store.exitingAccount).toBe(true)
  scope.owner='';loginResponses();await store.login('fixture','fixture-password')
  expect(store.exitingAccount).toBe(false);expect(store.user?.id).toBe('login-owner')
})
it('does not publish identity or clear the logout marker if the form is abandoned while login is pending',async()=>{
  loginResponses();const store=useApp();localStorage.setItem('reader-session-revoked','1');let active=true,release!:()=>void
  vi.mocked(cacheSet).mockReturnValue(new Promise<void>(resolve=>{release=resolve}))
  const loggingIn=store.login('fixture','fixture-password',()=>active),rejected=expect(loggingIn).rejects.toMatchObject({code:'SESSION_CHANGED'})
  await vi.waitFor(()=>expect(cacheSet).toHaveBeenCalled());active=false;release();await rejected
  expect(store.user).toBeNull();expect(localStorage.getItem('reader-session-revoked')).toBe('1')
})
