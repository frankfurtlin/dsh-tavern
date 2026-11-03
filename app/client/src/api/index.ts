import {backendBase,native,secureRefresh,cacheGet,cacheSet,clearPrivate,accountId} from '../platform/bridge'
import {offlineRead} from '../offline/storage'
import {offlineWorldRead} from '../offline/world-storage'
import {worldRequestDeleted,invalidateWorldCache,recordWorldReferences,filterDeletedWorldCache} from '../platform/world-cache'
import {bookmarkRequestDeleted,bookmarkResponseDeleted,filterDeletedBookmarks,markBookmarkDeleted} from '../platform/bookmark-cache'
import {bookRequestDeleted,bookIdsForRequest,recordBookReferences,markBookDeleted,invalidateBookCache,captureBookCacheScope,type RetainedBookStory} from '../platform/book-cache'
import {parseSessionBootstrap,type SessionBootstrap} from './session-bootstrap'
let accessToken=''
let bootstrap:{value:SessionBootstrap;epoch:number;base:string}|undefined
let authEpoch=0
window.addEventListener('reader-private-cleared',()=>{accessToken='';bootstrap=undefined;authEpoch++})
let refreshing:Promise<boolean>|null=null
export function setAccess(token:string){accessToken=token;bootstrap=undefined}
export function beginAuthentication(){accessToken='';bootstrap=undefined;return ++authEpoch}
export function refreshedSession(){return bootstrap&&bootstrap.epoch===authEpoch&&bootstrap.base===backendBase()&&(!accountId()||accountId()===bootstrap.value.user.id)?bootstrap.value:undefined}
export function authGeneration(){return authEpoch}
export function authorizationHeaders():Record<string,string>{return accessToken?{Authorization:'Bearer '+accessToken}:{}}
function metric(path:string,method:string,statusCode:number,started:number,errorCode=''){
  if(path.startsWith('/auth/')||path.startsWith('/product/telemetry/'))return
  const safePath=path.split(/[?#]/)[0].replace(/\/[a-f0-9]{32}(?=\/|$)/g,'/:id')
  window.dispatchEvent(new CustomEvent('ainovel-api-metric',{detail:{path:safePath,method,statusCode,durationMs:Math.max(0,Math.round(performance.now()-started)),errorCode}}))
}
export class ApiError extends Error{constructor(public code:string,message:string,public status:number,public details:Record<string,unknown>={}){super(message)}}
export async function refreshAuth(){
  if(localStorage.getItem('reader-session-revoked'))return false
  if(native&&!backendBase())return false
  if(refreshing)return refreshing
  refreshing=(async()=>{
    const epoch=authEpoch,owner=accountId(),base=backendBase(),still=()=>epoch===authEpoch&&owner===accountId()&&base===backendBase()
    try{const token=await secureRefresh();if(!still())return false;const response=await fetch(base+'/api/reader/v1/auth/refresh',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(native?{refreshToken:token}:{})});if(!still())return false;if(!response.ok){if(response.status===401)await clearPrivate();return false}const data=await response.json();if(!still())return false;if(owner&&data.user?.id!==owner){await clearPrivate();return false}setAccess(data.accessToken);const value=parseSessionBootstrap(data);if(value)bootstrap={value,epoch,base};if(native)await secureRefresh(data.refreshToken);return still()}catch{return false}
  })().finally(()=>{refreshing=null})
  return refreshing
}
export async function api<T>(path:string,options:{method?:string;body?:unknown;key?:string;cache?:boolean;retry?:boolean;namespace?:'world';readOnlyWorld?:boolean}={}):Promise<T>{
  const epoch=authEpoch,owner=accountId(),base=backendBase(),method=options.method||'GET'
  const valid=()=>epoch===authEpoch&&owner===accountId()&&base===backendBase()
  const maintenance=options.namespace==='world'||path.split('?')[0]==='/reading-context/resolve'&&method==='POST'||/^\/books\/[^/]+\/deletion(?:\?|$)/.test(path)&&method==='GET'||/^\/books\/[^/?]+(?:\?|$)/.test(path)&&method==='DELETE'
  const checkBook=(result?:unknown)=>{if(!valid())throw new ApiError('SESSION_CHANGED','账号或服务器已变化，请重新打开内容',401);if(options.namespace==='world'){const deleted=worldRequestDeleted(path,method,result);if(deleted)throw new ApiError(deleted.lineId?'WORLD_LINE_DELETED':'WORLD_DELETED',deleted.lineId?'这条世界线已删除':'这个世界已删除',404)}if(!maintenance&&bookRequestDeleted(path,options.body,result))throw new ApiError('BOOK_DELETED','来源小说已移除',404)}
  const checkBookmark=()=>{if(options.namespace!=='world'&&['GET','PATCH'].includes(method)){const removed=bookmarkRequestDeleted(path);if(removed)throw new ApiError('BOOKMARK_DELETED','这条笔记已删除',404,removed)}}
  checkBook();checkBookmark()
  const prefix=options.namespace==='world'?'/api/world/v1':'/api/reader/v1',cacheKey=(options.namespace==='world'?'world-api:':'api:')+path
  async function fallback(cachedFirst=false){
    if(!options.cache||method!=='GET')return
    if(cachedFirst&&options.namespace!=='world'){const cached=await cacheGet<T>(cacheKey);checkBook(cached);checkBookmark();if(cached!==undefined)return filterDeletedBookmarks(cached)}
    const downloaded=options.namespace==='world'?(options.readOnlyWorld?await offlineWorldRead<T>(path):undefined):await offlineRead<T>(path)
    checkBook(downloaded);checkBookmark();if(downloaded!==undefined||options.namespace==='world'&&options.readOnlyWorld)return options.namespace==='world'?downloaded:filterDeletedBookmarks(downloaded)
    const cached=await cacheGet<T>(cacheKey);checkBook(cached);checkBookmark();return options.namespace==='world'?cached:filterDeletedBookmarks(cached)
  }
  if(native&&!backendBase()){const cached=await fallback(true);if(cached!==undefined)return cached;throw new ApiError('BACKEND_NOT_CONFIGURED','此安装包尚未配置可用后端，请在登录页选择部署者允许的服务器',0)}
  const headers:Record<string,string>={};if(accessToken&&!['/auth/login','/auth/register'].includes(path))headers.Authorization='Bearer '+accessToken
  if(options.key)headers['Idempotency-Key']=options.key
  const form=options.body instanceof FormData;if(options.body!==undefined&&!form)headers['Content-Type']='application/json'
  let response:Response;const started=performance.now()
  try{response=await fetch(backendBase()+prefix+path,{method,headers,credentials:'include',body:options.body===undefined?undefined:form?options.body as FormData:JSON.stringify(options.body)})}
  catch{checkBook();metric(path,method,0,started,'NETWORK_UNAVAILABLE');const cached=await fallback();if(cached!==undefined)return cached;throw new ApiError('NETWORK_UNAVAILABLE','网络不可用，已保存的内容仍可阅读',0)}
  checkBook()
  metric(path,method,response.status,started)
  if(epoch!==authEpoch)throw new ApiError('SESSION_CHANGED','账号或服务器已变化，请重新打开内容',401)
  if(response.status===401&&!path.startsWith('/auth/')){
    if(options.retry!==false){
      const refreshed=await refreshAuth()
      checkBook()
      if(refreshed)return api<T>(path,{...options,retry:false})
      // refreshAuth clears private data only after an authoritative refresh 401
      // or account mismatch. A failed/aborted network request proves neither.
      const cached=await fallback(true);if(cached!==undefined)return cached
      throw new ApiError('NETWORK_UNAVAILABLE','网络不可用，已保存的内容仍可阅读',0)
    }
    await clearPrivate()
  }
  const result=await response.json().catch(()=>({code:'SERVICE_UNAVAILABLE',message:'服务暂时不可用，请稍后查询已有请求'}))
  checkBook(response.ok?result:undefined)
  if(options.namespace==='world'&&owner){
    const resource=path.split('?')[0].match(/^\/worlds\/([^/]+)(?:\/lines\/([^/]+))?$/)
    const line=path.split('?')[0].match(/^\/worlds\/([^/]+)\/lines\/([^/]+)(?:\/|$)/)
    const committed=method==='DELETE'&&resource&&((response.ok&&result.deleted===true)||(result.code==='WORLD_CLEANUP_PENDING'&&result.details?.deleted===true&&result.details.worldId===resource[1]))
    const missingWorld=method==='GET'&&resource&&!resource[2]&&response.status===404&&['RESOURCE_NOT_FOUND','WORLD_DELETED'].includes(result.code)
    const missingLine=response.status===404&&result.code==='WORLD_LINE_DELETED'&&line
    if(committed||missingWorld||missingLine){const selected=missingLine?line!:resource!;try{await invalidateWorldCache(selected[1],selected[2])}catch{/* The persisted tombstone still blocks reads and late writes; cleanup retries on reopen. */}if(!valid())throw new ApiError('SESSION_CHANGED','账号或服务器已变化，请重新打开内容',401)}
    if(response.ok)recordWorldReferences(path,result)
  }
  if(!response.ok){
    if(options.namespace!=='world'&&result.code==='BOOKMARK_DELETED'&&owner){const ids=path.split('?')[0].split('/'),details=result.details||{};if(ids[1]==='books'&&ids[3]==='bookmarks'&&details.bookId===ids[2]&&typeof details.bookmarkId==='string'&&(!ids[4]||ids[4]===details.bookmarkId))markBookmarkDeleted(details.bookId,details.bookmarkId)}
    if(options.namespace!=='world'&&result.code==='BOOK_DELETED'){
      const bookId=bookIdsForRequest(path,options.body)[0]
      if(bookId&&owner){
        const still=captureBookCacheScope();markBookDeleted(bookId)
        try{const receipt=await api<{retainedStories:RetainedBookStory[]}>('/books/'+bookId+'/deletion');if(still()&&valid())await invalidateBookCache(bookId,receipt.retainedStories)}catch{/* The durable tombstone remains pending; never fall back to source text. */}
      }
    }
    throw new ApiError(result.code||'HTTP_ERROR',result.message||'请求失败',response.status,result.details)
  }
  if(options.namespace!=='world'){
    if(method==='DELETE'&&/^\/books\/[^/?]+$/.test(path.split('?')[0])&&owner)markBookDeleted(path.split('/')[2],result.retainedStories||[])
    if(!maintenance)recordBookReferences(path,result,options.body)
  }
  checkBookmark();if(options.namespace!=='world'){const removed=bookmarkResponseDeleted(result);if(removed)throw new ApiError('BOOKMARK_DELETED','这条笔记已删除',404,removed)}
  const output=options.namespace==='world'&&method==='GET'?filterDeletedWorldCache(cacheKey,result):options.namespace==='world'?result:filterDeletedBookmarks(result)
  if(options.cache)try{await cacheSet(cacheKey,output)}catch(error){checkBook(output);throw error}
  checkBook(output);checkBookmark()
  if(options.namespace!=='world'){const removed=bookmarkResponseDeleted(output);if(removed)throw new ApiError('BOOKMARK_DELETED','这条笔记已删除',404,removed)}
  return (options.namespace==='world'?output:filterDeletedBookmarks(output)) as T
}
export async function allPages<T>(path:string):Promise<T[]>{
  const epoch=authEpoch,result:T[]=[];let cursor:string|null=null
  do{const page:{items:T[];nextCursor?:string|null}=await api(path+(cursor?(path.includes('?')?'&':'?')+'cursor='+encodeURIComponent(cursor):''),{cache:true});if(epoch!==authEpoch)throw new ApiError('SESSION_CHANGED','账号或服务器已变化，请重新打开内容',401);result.push(...page.items);cursor=page.nextCursor||null}while(cursor)
  return result
}
export function newKey(){return crypto.randomUUID()}
