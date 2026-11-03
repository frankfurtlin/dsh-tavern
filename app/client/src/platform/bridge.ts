import {Capacitor,registerPlugin} from '@capacitor/core'
import {App} from '@capacitor/app'
import {Network} from '@capacitor/network'
import {Keyboard} from '@capacitor/keyboard'
import {Filesystem,Directory,Encoding} from '@capacitor/filesystem'
import {FilePicker} from '@capawesome/capacitor-file-picker'
import {SecureStoragePlugin} from 'capacitor-secure-storage-plugin'
import {openDB} from 'idb'
import {createDeletionReceiptStore} from './deletion-receipt'
import {assertBookCacheWrite,filterDeletedCache,filterDeletedCacheForWrite,filterBookCacheForRemoval,cacheBookIds,recordBookReferences,BookDeletedError} from './book-cache'
import {APP_BASE} from './base'
import {assertWorldCacheWrite,filterDeletedWorldCache,recordWorldReferences,shouldPurgeWorldCache,WorldDeletedError,markWorldDeleted,isWorldLineDeleted} from './world-cache'

export const native = Capacitor.isNativePlatform()
export const clientType = native ? Capacitor.getPlatform() : 'web'
const androidCredentials=registerPlugin<{get(options:{key:string}):Promise<{value:string}>;set(options:{key:string;value:string}):Promise<void>;remove(options:{key:string}):Promise<void>}>('ReaderCredentials')
const credentials=clientType==='android'?androidCredentials:SecureStoragePlugin
const db = openDB('story-reader-private',1,{upgrade(database){database.createObjectStore('cache')}})
let account = localStorage.getItem('reader-account') || ''
type CacheObserver={owner:string;base:string;key:string;invalidate:()=>void}
const cacheObservers=new Set<CacheObserver>()
const cacheChannel=typeof BroadcastChannel==='undefined'?undefined:new BroadcastChannel('story-reader-cache-invalidation:v1')
function invalidateCacheObservers(key?:string,owner=account,base=backendBase()){
  for(const observer of [...cacheObservers])if(!key||observer.key===key&&observer.owner===owner&&observer.base===base)observer.invalidate()
}
function cacheChanging(key:string,owner=account,base=backendBase()){
  invalidateCacheObservers(key,owner,base)
  // Only scope/key identities cross tabs. No cached value or prose is sent.
  const message={owner,base,key}
  if(cacheChannel)cacheChannel.postMessage(message)
  else localStorage.setItem('reader-cache-invalidation:v1',JSON.stringify({...message,nonce:crypto.randomUUID()}))
}
function cacheMessage(value:unknown){
  if(!value||typeof value!=='object')return
  const message=value as Record<string,unknown>
  if(typeof message.owner==='string'&&typeof message.base==='string'&&typeof message.key==='string')invalidateCacheObservers(message.key,message.owner,message.base)
}
if(cacheChannel)cacheChannel.onmessage=event=>cacheMessage(event.data)
window.addEventListener('storage',event=>{
  const changed=event as StorageEvent
  if(['reader-account','reader-server','reader-session-revoked'].includes(changed.key||''))invalidateCacheObservers()
  else if(changed.key==='reader-cache-invalidation:v1'){try{cacheMessage(JSON.parse(changed.newValue||'null'))}catch{}}
})
for(const event of ['reader-private-cleared','reader-backend-changed','reader-book-deleted'])window.addEventListener(event,()=>invalidateCacheObservers())
export function observeCacheKey(key:string,invalidate:()=>void){
  const observer={owner:account,base:backendBase(),key,invalidate};cacheObservers.add(observer)
  return ()=>{cacheObservers.delete(observer)}
}
export function cacheKeyVisible(key:string){return filteredCache(key,{})!==undefined}
let cacheEpoch=0,cacheWork:Promise<void>=Promise.resolve(),credentialWork:Promise<unknown>=Promise.resolve()
export function setAccount(id:string){if(account!==id){cacheEpoch++;invalidateCacheObservers()}account=id;if(id)localStorage.setItem('reader-account',id);else localStorage.removeItem('reader-account')}
export function accountId(){return account}
export function cacheGeneration(){return cacheEpoch}
export type {DeletionReceipt} from './deletion-receipt'
const deletionReceiptPath='account-deletion-receipt.json'
const deletionReceipts=createDeletionReceiptStore({
  readLocal:()=>localStorage.getItem('reader-deletion-receipt'),
  writeLocal:value=>localStorage.setItem('reader-deletion-receipt',value),
  ...(native?{
    readNative:async()=>String((await Filesystem.readFile({path:deletionReceiptPath,directory:Directory.Data,encoding:Encoding.UTF8})).data),
    writeNative:async(value:string)=>{await Filesystem.writeFile({path:deletionReceiptPath,data:value,directory:Directory.Data,encoding:Encoding.UTF8})}
  }:{})
})
export const saveDeletionReceipt=deletionReceipts.save
export const loadDeletionReceipt=deletionReceipts.load
export async function secureRefresh(value?:string|null):Promise<string|null>{
  if(!native)return null
  if(value===null)localStorage.setItem('reader-credential-revoked','1')
  const epoch=cacheEpoch
  const work=credentialWork.catch(()=>{}).then(async()=>{
    if(epoch!==cacheEpoch)return null
    if(value===null){await credentials.remove({key:'reader-refresh'});return null}
    if(value!==undefined){await credentials.set({key:'reader-refresh',value});if(epoch===cacheEpoch)localStorage.removeItem('reader-credential-revoked');return epoch===cacheEpoch?value:null}
    if(localStorage.getItem('reader-credential-revoked'))return null
    const result=(await credentials.get({key:'reader-refresh'}).catch(()=>({value:''}))).value||null
    return epoch===cacheEpoch?result:null
  })
  credentialWork=work;return work
}
async function cachePath(key:string,owner=account){const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(key));return 'cache/'+owner+'/'+Array.from(new Uint8Array(hash)).map(n=>n.toString(16).padStart(2,'0')).join('')+'.json'}
interface CacheIndexEntry {key:string;bookIds:string[]}
type CacheIndex=Record<string,CacheIndexEntry>
const indexKey=(base:string)=>'__cache-key-index:v1:'+base
async function nativeIndexPath(owner:string,base:string){return await cachePath(indexKey(base),owner)}
async function readIndex(owner:string,base:string):Promise<CacheIndex>{
  if(!native)return (await(await db).get('cache',owner+':'+indexKey(base)))||{}
  try{const parsed=JSON.parse(String((await Filesystem.readFile({path:await nativeIndexPath(owner,base),directory:Directory.Data,encoding:Encoding.UTF8})).data));if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('缓存目录索引损坏');return parsed}
  catch(error){if(/not exist|not found|ENOENT/i.test(String(error)))return {};throw error}
}
async function writeIndex(owner:string,base:string,index:CacheIndex){
  if(native)await Filesystem.writeFile({path:await nativeIndexPath(owner,base),data:JSON.stringify(index),directory:Directory.Data,encoding:Encoding.UTF8,recursive:true})
  else await(await db).put('cache',index,owner+':'+indexKey(base))
}
async function rawCacheGet(key:string,owner:string):Promise<unknown>{
  if(native){try{return JSON.parse(String((await Filesystem.readFile({path:await cachePath(key,owner),directory:Directory.Data,encoding:Encoding.UTF8})).data))}catch{return undefined}}
  return (await db).get('cache',owner+':'+key)
}
async function rawCacheRemove(key:string,owner:string){
  cacheChanging(key,owner)
  try{if(native){try{await Filesystem.deleteFile({path:await cachePath(key,owner),directory:Directory.Data})}catch(error){if(!/not exist|not found|ENOENT/i.test(String(error)))throw error}}
    else await(await db).delete('cache',owner+':'+key)
  }finally{cacheChanging(key,owner)}
}
function rememberCache(key:string,value:unknown){
  if(key.startsWith('world-api:')){recordWorldReferences(key.slice(10),value);return}
  if(key.startsWith('world-'))return
  const books=cacheBookIds(key,value)
  recordBookReferences(key.startsWith('api:')?key.slice(4):books.length?'/books/'+books[0]:'/cache',value)
}
function filteredCache(key:string,value:unknown,allowDeletedBook=false){return filterDeletedWorldCache(key,allowDeletedBook?value:filterDeletedCache(key,value))}
function filteredWriteCache(key:string,value:unknown,previous:unknown,allowDeletedBook=false){return filterDeletedWorldCache(key,allowDeletedBook?value:filterDeletedCacheForWrite(key,value,previous))}
function assertCacheWrite(key:string,value:unknown,allowDeletedBook=false){if(!allowDeletedBook)assertBookCacheWrite(key,value);assertWorldCacheWrite(key,value)}
async function registerCache(key:string,value:unknown,owner:string,base:string){
  const index=await readIndex(owner,base)
  index[key]={key,bookIds:cacheBookIds(key,value)}
  // Register the actual key before writing a hashed native file. A failed
  // cache write leaves a harmless index entry; an unindexed write is avoided.
  await writeIndex(owner,base,index)
}
async function writeNativeCache(key:string,value:unknown,owner:string,active:()=>boolean,allowDeleted=false,previous?:unknown):Promise<unknown>{
  const path=await cachePath(key,owner);let current=value
  while(active()){
    assertCacheWrite(key,current,allowDeleted)
    const data=JSON.stringify(current)
    await Filesystem.writeFile({path,data,directory:Directory.Data,encoding:Encoding.UTF8,recursive:true})
    if(!active())return current
    const next=filteredWriteCache(key,current,previous,allowDeleted)
    if(next===undefined){await rawCacheRemove(key,owner);if(key.startsWith('world-'))throw new WorldDeletedError();throw new BookDeletedError()}
    if(JSON.stringify(next)===data)return current
    // A deletion landed while the SDK write was pending. Shared queues/indexes
    // must not put removed records back on disk alongside surviving books.
    current=next
  }
  return current
}
export async function cacheSet(key:string,value:unknown){
  if(!account)return
  const owner=account,epoch=cacheEpoch,base=backendBase(),snapshot=JSON.parse(JSON.stringify(value))
  cacheChanging(key,owner,base)
  const deletedLine=key.match(/^world-line-deleted:([^:]+):([^:]+)$/)
  if(snapshot===true&&deletedLine&&!isWorldLineDeleted(deletedLine[1],deletedLine[2]))markWorldDeleted(deletedLine[1],deletedLine[2])
  const active=()=>epoch===cacheEpoch&&base===backendBase()&&owner===account
  const work=cacheWork.catch(()=>{}).then(async()=>{try{
    if(!active())return
    assertCacheWrite(key,snapshot);const previous=await rawCacheGet(key,owner);if(!active())return;let filtered=filteredWriteCache(key,snapshot,previous)
    rememberCache(key,filtered);await registerCache(key,filtered,owner,base)
    if(!active())return
    assertCacheWrite(key,filtered);filtered=filteredWriteCache(key,filtered,previous)
    if(native)await writeNativeCache(key,filtered,owner,active,false,previous)
    else {const tx=(await db).transaction('cache','readwrite');if(!active())return;assertCacheWrite(key,filtered);await tx.store.put(filtered,owner+':'+key);if(JSON.stringify(filteredWriteCache(key,filtered,previous))!==JSON.stringify(filtered)){tx.abort();await tx.done.catch(()=>{});if(key.startsWith('world-'))throw new WorldDeletedError();throw new BookDeletedError()}await tx.done}
  }finally{cacheChanging(key,owner,base)}})
  cacheWork=work;await work
}
export async function cacheGet<T>(key:string,options:{allowDeleted?:boolean}={}):Promise<T|undefined>{
  if(!account)return undefined
  const owner=account,epoch=cacheEpoch,base=backendBase(),value=await rawCacheGet(key,owner)
  if(epoch!==cacheEpoch||base!==backendBase()||owner!==account)return
  if(value!==undefined){rememberCache(key,value);if(native){const work=cacheWork.catch(()=>{}).then(async()=>{if(epoch===cacheEpoch&&base===backendBase())await registerCache(key,value,owner,base)});cacheWork=work;await work}}
  if(epoch!==cacheEpoch||base!==backendBase()||owner!==account)return
  return filteredCache(key,value,options.allowDeleted) as T|undefined
}
export async function cacheUpdate<T>(key:string,update:(value:T|undefined)=>T,active:()=>boolean=()=>true,options:{allowDeleted?:boolean}={}):Promise<T|undefined>{
  if(!account)return undefined
  const owner=account,epoch=cacheEpoch,base=backendBase(),valid=()=>epoch===cacheEpoch&&base===backendBase()&&active()
  cacheChanging(key,owner,base)
  let result:T|undefined
  const work=cacheWork.catch(()=>{}).then(async()=>{try{
    if(!valid())return
    if(native){
      let value:T|undefined
      value=await rawCacheGet(key,owner) as T|undefined
      if(!valid())return
      result=update(filteredCache(key,value,options.allowDeleted) as T|undefined)
      assertCacheWrite(key,result,options.allowDeleted);result=filteredWriteCache(key,result,value,options.allowDeleted) as T
      rememberCache(key,result);await registerCache(key,result,owner,base);if(!valid())return
      assertCacheWrite(key,result,options.allowDeleted);result=filteredWriteCache(key,result,value,options.allowDeleted) as T
      result=await writeNativeCache(key,result,owner,valid,options.allowDeleted,value) as T
    }else{
      const transaction=(await db).transaction('cache','readwrite')
      const value=await transaction.store.get(owner+':'+key)
      if(!valid()){transaction.abort();await transaction.done.catch(()=>{});return}
      result=update(filteredCache(key,value,options.allowDeleted) as T|undefined)
      assertCacheWrite(key,result,options.allowDeleted);result=filteredWriteCache(key,result,value,options.allowDeleted) as T
      rememberCache(key,result)
      const index:CacheIndex=await transaction.store.get(owner+':'+indexKey(base))||{};index[key]={key,bookIds:cacheBookIds(key,result)}
      await transaction.store.put(index,owner+':'+indexKey(base));await transaction.store.put(result,owner+':'+key)
      if(JSON.stringify(filteredWriteCache(key,result,value,options.allowDeleted))!==JSON.stringify(result)){transaction.abort();await transaction.done.catch(()=>{});if(key.startsWith('world-'))throw new WorldDeletedError();throw new BookDeletedError()}
      await transaction.done
    }
  }finally{cacheChanging(key,owner,base)}})
  cacheWork=work;await work
  return valid()?filteredCache(key,result,options.allowDeleted) as T|undefined:undefined
}
export async function purgeWorldCache(worldId:string,lineId:string|undefined,active:()=>boolean){
  const owner=account,epoch=cacheEpoch,base=backendBase(),valid=()=>!!owner&&epoch===cacheEpoch&&owner===account&&base===backendBase()&&active()
  const work=cacheWork.catch(()=>{}).then(async()=>{
    if(!valid())throw new Error('账号或服务器已变化')
    const index=await readIndex(owner,base),keys=new Set(Object.keys(index))
    if(!native)for(const stored of await(await db).getAllKeys('cache'))if(String(stored).startsWith(owner+':world-'))keys.add(String(stored).slice(owner.length+1))
    for(const key of keys){
      if(!valid())throw new Error('账号或服务器已变化')
      if(!key.startsWith('world-'))continue
      const value=await rawCacheGet(key,owner);if(!valid())throw new Error('账号或服务器已变化')
      if(shouldPurgeWorldCache(key,value,worldId,lineId)){await rawCacheRemove(key,owner);delete index[key];continue}
      const filtered=filterDeletedWorldCache(key,value)
      if(filtered===undefined){await rawCacheRemove(key,owner);delete index[key]}
      else if(JSON.stringify(filtered)!==JSON.stringify(value)){
        if(native)await writeNativeCache(key,filtered,owner,valid)
        else await(await db).put('cache',filtered,owner+':'+key)
      }
    }
    if(!valid())throw new Error('账号或服务器已变化')
    await writeIndex(owner,base,index)
  })
  cacheWork=work;await work
}
export async function purgeBookCache(bookId:string,active:()=>boolean){
  const owner=account,epoch=cacheEpoch,base=backendBase(),valid=()=>!!owner&&epoch===cacheEpoch&&owner===account&&base===backendBase()&&active()
  const work=cacheWork.catch(()=>{}).then(async()=>{
    if(!valid())throw new Error('账号或服务器已变化')
    const index=await readIndex(owner,base),keys=new Set(Object.keys(index))
    if(!native)for(const stored of await(await db).getAllKeys('cache'))if(String(stored).startsWith(owner+':')&&!String(stored).slice(owner.length+1).startsWith('__cache-key-index:'))keys.add(String(stored).slice(owner.length+1))
    const values=new Map<string,unknown>()
    for(const key of keys){if(!valid())throw new Error('账号或服务器已变化');const value=await rawCacheGet(key,owner);values.set(key,value);rememberCache(key,value)}
    for(const [key,value] of values){
      if(!valid())throw new Error('账号或服务器已变化')
      const filtered=filterBookCacheForRemoval(key,value,bookId),shared=Array.isArray(value)||!!(value&&typeof value==='object'&&Array.isArray((value as {items?:unknown}).items))
      if(filtered===undefined||!shared&&index[key]?.bookIds.includes(bookId)){
        await rawCacheRemove(key,owner);delete index[key]
      }else if(shared){
        if(native)await Filesystem.writeFile({path:await cachePath(key,owner),data:JSON.stringify(filtered),directory:Directory.Data,encoding:Encoding.UTF8,recursive:true})
        else await(await db).put('cache',filtered,owner+':'+key)
        index[key]={key,bookIds:cacheBookIds(key,filtered)}
      }
    }
    if(!valid())throw new Error('账号或服务器已变化')
    await writeIndex(owner,base,index)
  })
  cacheWork=work;await work
}
export async function clearPrivate(){
  localStorage.setItem('reader-session-revoked','1')
  const owner=account;setAccount('');window.dispatchEvent(new Event('reader-private-cleared'))
  const clearCredential=secureRefresh(null)
  const work=cacheWork.catch(()=>{}).then(async()=>{if(owner){if(native)await Filesystem.rmdir({path:'cache/'+owner,directory:Directory.Data,recursive:true}).catch(()=>{});else{const database=await db;const tx=database.transaction('cache','readwrite');for(const key of await tx.store.getAllKeys()){if(String(key).startsWith(owner+':'))await tx.store.delete(key)}await tx.done}}})
  cacheWork=work;await Promise.all([work,clearCredential])
}
const developmentServer=import.meta.env.VITE_NATIVE_DEV_SERVER||''
const configuredBase=import.meta.env.VITE_API_BASE_URL||APP_BASE
const origin=(value:string)=>{try{return new URL(value).origin}catch{return ''}}
const allowedServers=(import.meta.env.VITE_NATIVE_ALLOWED_SERVERS||import.meta.env.VITE_API_BASE_URL||'').split(',').filter(Boolean).map(origin).filter(Boolean)
if(developmentServer)allowedServers.push(origin(developmentServer))
export function backendBase(){if(!native)return configuredBase;const value=localStorage.getItem('reader-server')||developmentServer||configuredBase;if(!value)return '';try{const url=new URL(value);return allowedServers.includes(url.origin)&&(!url.username&&!url.password)&&(url.protocol==='https:'||value===developmentServer)?url.origin:''}catch{return ''}}
export async function changeBackend(url:string,active:()=>boolean=()=>true){
  const epoch=cacheEpoch,owner=account,previousBase=backendBase()
  const parsed=new URL(url)
  if((parsed.protocol!=='https:'&&parsed.origin!==developmentServer)||!allowedServers.includes(parsed.origin)||parsed.username||parsed.password||parsed.search||parsed.hash)throw new Error('请选择部署者允许的 HTTPS 服务器')
  await fetch(parsed.origin+'/health/ready').then(r=>{if(!r.ok)throw new Error('后端尚未就绪')})
  if(epoch!==cacheEpoch||owner!==account||previousBase!==backendBase()||!active())throw new Error('账号、页面或服务器已变化，请重新连接')
  const clearing=clearPrivate(),clearedEpoch=cacheEpoch;await clearing
  if(clearedEpoch!==cacheEpoch||account)throw new Error('清理期间账号已变化，请重新连接')
  localStorage.setItem('reader-server',parsed.origin);window.dispatchEvent(new Event('reader-backend-changed'))
}
export async function pickFile():Promise<File|null>{
  if(native){
    try{
      const result=await FilePicker.pickFiles({types:['text/plain','application/epub+zip'],limit:1,readData:false})
      const selected=result.files[0];if(!selected)return null
      if(!Number.isFinite(selected.size)||selected.size<=0)throw new Error('文件为空或无法确认大小，请先保存到本机再导入')
      if(selected.size>50*1024*1024)throw new Error('材料超过 50 MiB')
      if(!selected.path)throw new Error('系统未返回可读取的文件地址')
      // Decode bounded chunks rather than copying the entire input as Base64.
      const parts:Blob[]=[];let total=0
      await new Promise<void>((resolve,reject)=>{
        let settled=false
        const fail=(error:unknown)=>{if(!settled){settled=true;reject(error)}}
        // Native binary readers round upward to a three-byte Base64 boundary.
        // Leave three bytes of headroom so the actual callback stays <=64 KiB.
        Filesystem.readFileInChunks({path:selected.path!,chunkSize:64*1024-3,length:50*1024*1024+1},(chunk,error)=>{
          if(settled)return
          if(error){fail(new Error('文件读取失败，请重新选择本机文件'));return}
          if(!chunk||typeof chunk.data!=='string'){fail(new Error('系统未返回文件内容'));return}
          if(chunk.data===''){settled=true;resolve();return}
          try{const bytes=Uint8Array.from(atob(chunk.data),c=>c.charCodeAt(0));total+=bytes.byteLength;if(total>50*1024*1024){fail(new Error('材料超过 50 MiB'));return}parts.push(new Blob([bytes]))}catch(error){fail(error)}
        }).catch(fail)
      })
      if(!total||total!==selected.size)throw new Error('读取时文件内容发生变化，请重新选择文件')
      return new File(parts,selected.name,{type:selected.mimeType})
    }catch(error){if(String(error).toLowerCase().includes('cancel'))return null;throw error}
  }
  return new Promise(resolve=>{const input=document.createElement('input');input.type='file';input.accept='.txt,.epub';input.onchange=()=>resolve(input.files?.[0]||null);input.addEventListener('cancel',()=>resolve(null));input.click()})
}
let foreground=!document.hidden
export function appForeground(){return foreground}
export async function lifecycle(onResume:()=>void,onHide:()=>void,onBack:()=>void,onNetwork:(online:boolean)=>void){
  const resume=()=>{foreground=true;onResume()},hide=()=>{foreground=false;onHide()}
  if(native){
    let keyboardVisible=false,stateGeneration=0
    const handles=await Promise.all([App.addListener('appStateChange',({isActive})=>{stateGeneration++;isActive?resume():hide()}),App.addListener('backButton',()=>{if(keyboardVisible)Keyboard.hide();else onBack()}),Network.addListener('networkStatusChange',({connected})=>onNetwork(connected)),Keyboard.addListener('keyboardDidShow',()=>{keyboardVisible=true}),Keyboard.addListener('keyboardDidHide',()=>{keyboardVisible=false})])
    const initialGeneration=stateGeneration,initialState=await App.getState()
    if(initialGeneration===stateGeneration)foreground=initialState.isActive
    onNetwork((await Network.getStatus()).connected)
    return()=>handles.forEach(h=>h.remove())
  }
  const visibility=()=>document.hidden?hide():resume()
  const online=()=>onNetwork(navigator.onLine)
  document.addEventListener('visibilitychange',visibility);window.addEventListener('online',online);window.addEventListener('offline',online)
  return()=>{document.removeEventListener('visibilitychange',visibility);window.removeEventListener('online',online);window.removeEventListener('offline',online)}
}
