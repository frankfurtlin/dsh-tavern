import {accountId,backendBase,cacheGet,cacheSet,cacheUpdate} from '../platform/bridge'
import * as bridge from '../platform/bridge'
import * as client from '../api'
import type {Book,Branch,Chapter,ChapterInfo,Bookmark,Progress,Anchor,ReadingNote} from '../api/types'
import {isBookmarkDeleted} from '../platform/bookmark-cache'
import type {OfflineImages} from '../api/assets'
import {validateOfflineImages,remapBookImages} from '../api/offline-images'

export interface OfflinePayload {
  book:Book;branch:Branch;revisionId:string;directory:ChapterInfo[];chapters:Chapter[]
  bookmarks:Bookmark[];progress:Progress;assets:Record<string,string>;origins:string[];images?:OfflineImages
}
export interface OfflinePackage {schema:1;id:string;downloadedAt:string;sha256:string;payload:OfflinePayload}
export interface OfflineEntry {id:string;bookId:string;branchId:string;revisionId:string;title:string;lineName:string;chapterCount:number;downloadedAt:string;bytes:number}
export interface PendingNote {
  id:string;anchor:Anchor;note:string;remoteId?:string;baseVersion:number;key:string
  kind:'create'|'update'|'delete';attempted:boolean;conflictKey?:string;blocked?:string;submittedAnchor?:Anchor
}
export const indexKey='offline-reading-index:v1',queueKey='offline-note-queue:v1'
export const conflictCopiesKey='offline-note-conflict-copies:v1'
export const packageKey=(id:string)=>'offline-reading-package:v1:'+id
export const packageId=(bookId:string,branchId:string,revisionId:string)=>[bookId,branchId,revisionId].join(':')
export async function digest(value:unknown){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('')}
function scopeIdentity(){return JSON.stringify([accountId(),backendBase(),'cacheGeneration' in bridge?bridge.cacheGeneration():0,'authGeneration' in client?client.authGeneration():0])}
export function privateScope(){const owner=accountId(),scope=scopeIdentity();return ()=>!!owner&&scope===scopeIdentity()&&(typeof localStorage==='undefined'||!localStorage.getItem('reader-session-revoked')&&(!('observeCacheKey' in bridge)||localStorage.getItem('reader-account')===owner))}
export async function entries(allowDeleted=false):Promise<OfflineEntry[]>{const value=await cacheGet<OfflineEntry[]>(indexKey,{allowDeleted});return Array.isArray(value)?value:[]}
type VerifiedPackage={value:OfflinePackage;bytes:number;valid:()=>boolean;stop:()=>void}
// Serialized-size bounds, not a claim about actual heap consumption.
const maxVerifiedPackages=2,maxVerifiedBytes=32*1024*1024
const verifiedPackages=new Map<string,VerifiedPackage>()
function discardVerified(key:string){const entry=verifiedPackages.get(key);if(entry){entry.stop();verifiedPackages.delete(key)}}
async function packageForRead(id:string,allowDeleted=false,hot=false):Promise<OfflinePackage|undefined>{
  const scope=scopeIdentity(),key=JSON.stringify([scope,id,allowDeleted]),validScope=privateScope()
  for(const [previous,entry]of verifiedPackages)if(!entry.valid())discardVerified(previous)
  const previous=verifiedPackages.get(key)
  if(hot&&previous?.valid()){verifiedPackages.delete(key);verifiedPackages.set(key,previous);return previous.value}
  discardVerified(key)
  let changed=false,retained=false
  const stop='observeCacheKey' in bridge?bridge.observeCacheKey(packageKey(id),()=>{changed=true;discardVerified(key)}):()=>{}
  const valid=()=>!changed&&validScope()&&(allowDeleted||!('cacheKeyVisible' in bridge)||bridge.cacheKeyVisible(packageKey(id)))
  try{
    const value=await cacheGet<OfflinePackage|null>(packageKey(id),{allowDeleted})
    if(!value||value.schema!==1||value.id!==id||!valid())return
    const serialized=JSON.stringify(value.payload),bytes=new TextEncoder().encode(serialized)
    const actual=await crypto.subtle.digest('SHA-256',bytes)
    if(Array.from(new Uint8Array(actual),n=>n.toString(16).padStart(2,'0')).join('')!==value.sha256||!valid())return
    if(value.payload.images){try{await validateOfflineImages(value.payload.images,{kind:'book',bookId:value.payload.book.id,branchId:value.payload.branch.id,revisionId:value.payload.revisionId})}catch{return}if(!valid())return}
    if('observeCacheKey' in bridge&&bytes.length<=maxVerifiedBytes){
      verifiedPackages.set(key,{value,bytes:bytes.length,valid,stop});retained=true
      while(verifiedPackages.size>maxVerifiedPackages||[...verifiedPackages.values()].reduce((sum,entry)=>sum+entry.bytes,0)>maxVerifiedBytes)discardVerified(verifiedPackages.keys().next().value!)
    }
    return value
  }finally{if(!retained)stop()}
}
export async function readPackage(id:string,allowDeleted=false):Promise<OfflinePackage|undefined>{
  const value=await packageForRead(id,allowDeleted)
  return value?structuredClone(value):undefined
}
export async function commitPackage(payload:OfflinePayload,active:()=>boolean,migration=false){
  const id=packageId(payload.book.id,payload.branch.id,payload.revisionId),downloadedAt=new Date().toISOString()
  const value:OfflinePackage={schema:1,id,downloadedAt,sha256:await digest(payload),payload}
  if(!active())throw new Error('账号、故事线或下载页面已变化')
  await cacheSet(packageKey(id),value)
  if(!active())throw new Error('账号、故事线或下载页面已变化')
  const verified=await readPackage(id);if(!verified||!active())throw new Error('下载未能完整保存，请重试')
  const entry:OfflineEntry={id,bookId:payload.book.id,branchId:payload.branch.id,revisionId:payload.revisionId,title:payload.book.title,lineName:payload.branch.name,chapterCount:payload.chapters.length,downloadedAt,bytes:new TextEncoder().encode(JSON.stringify(value)).length}
  const result=await cacheUpdate<OfflineEntry[]>(indexKey,items=>[...(items||[]).filter(item=>item.id!==id),entry],active,{allowDeleted:migration})
  if(!result)throw new Error('下载未完成，本机登记未保存')
  return entry
}
export async function removePackage(id:string,active:()=>boolean=()=>true,migration=false){const scope=privateScope(),valid=()=>scope()&&active();const result=await cacheUpdate<OfflineEntry[]>(indexKey,items=>(items||[]).filter(item=>item.id!==id),valid,{allowDeleted:migration});if(!result||!valid())throw new Error('账号或服务器已变化');await cacheSet(packageKey(id),null)}
export async function noteQueue(){const value=await cacheGet<PendingNote[]>(queueKey);return Array.isArray(value)?value:[]}
export async function localNotes(bookId:string,branchId:string,revisionId:string,remote:Bookmark[]){
  const copies=await cacheGet<Record<string,string>>(conflictCopiesKey)||{}
  const notes=new Map(remote.filter(note=>!isBookmarkDeleted(bookId,note.id)).map(note=>[note.id,copies[note.id]!==undefined?{...note,status:'conflict_copy'}:note]))
  for(const pending of await noteQueue()){
    if(pending.anchor.bookId!==bookId||pending.anchor.branchId!==branchId||pending.anchor.revisionId!==revisionId)continue
    const id=pending.remoteId||pending.id
    if(isBookmarkDeleted(bookId,id)){if(pending.note){const copyId='deleted-local:'+pending.id;notes.set(copyId,{id:copyId,anchor:pending.anchor,note:pending.note,version:pending.baseVersion,status:'deleted_original'})}continue}
    if(pending.kind==='delete'){notes.delete(id);continue}
    notes.set(id,{id,anchor:pending.anchor,note:pending.note,version:pending.baseVersion,status:pending.blocked?'sync_conflict':'pending_sync'})
  }
  return [...notes.values()].filter(note=>!isBookmarkDeleted(bookId,note.id))
}
export async function applySyncedNote(note:Bookmark,valid:()=>boolean){
  for(const item of await entries()){
    if(!valid())return
    if(item.bookId!==note.anchor.bookId||item.branchId!==note.anchor.branchId||item.revisionId!==note.anchor.revisionId)continue
    const stored=await readPackage(item.id);if(!stored||!valid())continue
    const payload={...stored.payload,bookmarks:[...stored.payload.bookmarks.filter(item=>item.id!==note.id),note]}
    const sha256=await digest(payload);if(!valid())return
    await cacheSet(packageKey(item.id),{...stored,payload,sha256})
  }
}
export async function offlineAsset(bookId:string,assetId:string){for(const item of await entries()){if(item.bookId!==bookId)continue;const stored=await readPackage(item.id);if(stored?.payload.assets[assetId])return stored.payload.assets[assetId]}}
export async function offlineRead<T>(path:string):Promise<T|undefined>{
  const valid=privateScope(),url=new URL(path,'https://offline.invalid'),parts=url.pathname.split('/').filter(Boolean)
  if(parts[0]==='notebook'){
    const notes=new Map<string,ReadingNote>()
    for(const item of await entries()){
      const stored=await readPackage(item.id);if(!stored||!valid())continue
      for(const note of await localNotes(item.bookId,item.branchId,item.revisionId,stored.payload.bookmarks)){
        if(!note.note)continue
        notes.set(note.id,{...note,bookTitle:item.title,branchName:item.lineName,location:stored.payload.directory.find(chapter=>chapter.id===note.anchor.chapterId)?.title||'保存的段落',createdAt:''})
      }
    }
    return valid()?{items:[...notes.values()],nextCursor:null} as T:undefined
  }
  const candidates=(await entries()).filter(item=>parts[0]==='books'?item.bookId===parts[1]:parts[0]==='branches'?item.branchId===parts[1]:false)
  const requested=url.searchParams.get('revisionId'),branch=url.searchParams.get('branchId')
  for(const item of candidates.slice().reverse()){
    if(requested&&item.revisionId!==requested||branch&&item.branchId!==branch)continue
    const stored=await packageForRead(item.id,false,true);if(!stored||!valid())continue
    const p=stored.payload;let result:unknown
    if(parts[0]==='books'){
      if(parts.length===2)result=p.book
      else if(parts[2]==='branches')result={items:[p.branch],nextCursor:null}
      else if(parts[2]==='progress')result=p.progress
      else if(parts[2]==='bookmarks')result={items:await localNotes(item.bookId,item.branchId,item.revisionId,p.bookmarks),nextCursor:null}
    }else if(parts[2]==='chapters')result=parts[3]?p.chapters.find(chapter=>chapter.id===parts[3]):{items:p.directory,nextCursor:null}
    if(result!==undefined&&valid())return structuredClone(result) as T
  }
}
export async function downloadedOrigin(bookId:string,branchId:string,revisionId:string,origin:string){const stored=await readPackage(packageId(bookId,branchId,revisionId));return !!stored?.payload.origins.includes(origin)}
export interface RetainedOfflineStory {bookId:string;storyId:string;branchIds:string[];book?:Book}
export async function removeBookDownloads(bookId:string,retained:RetainedOfflineStory[],active:()=>boolean){
  const validScope=privateScope(),valid=()=>validScope()&&active()
  const downloaded=await entries(true)
  for(const item of downloaded){const target=retained.find(story=>story.branchIds.includes(item.branchId));if(item.bookId===bookId&&target&&(!target.book||target.book.id!==target.bookId||target.bookId===bookId))throw new Error('保留故事的资料尚未确认，离线包等待迁移')}
  for(const item of downloaded){
    if(!valid())throw new Error('账号或服务器已变化')
    if(item.bookId!==bookId)continue
    const target=retained.find(story=>story.branchIds.includes(item.branchId)),stored=target?await readPackage(item.id,true):undefined
    if(target&&stored){
      const change=(anchor:Anchor)=>({...anchor,bookId:target.bookId})
      const source=stored.payload
      const book=target.book!
      const payload:OfflinePayload={...source,book,branch:{...source.branch,parentBranchId:target.branchIds.includes(source.branch.parentBranchId||'')?source.branch.parentBranchId:null,forkAnchor:source.branch.forkAnchor&&target.branchIds.includes(source.branch.forkAnchor.branchId)?change(source.branch.forkAnchor):null},chapters:source.chapters.map(chapter=>({...chapter,blocks:chapter.blocks.map(block=>({...block,startAnchor:change(block.startAnchor),endAnchor:change(block.endAnchor)}))})),bookmarks:source.bookmarks.map(note=>({...note,anchor:change(note.anchor)})),progress:{...source.progress,anchor:source.progress.anchor?change(source.progress.anchor):null,maxObservedAnchor:source.progress.maxObservedAnchor?change(source.progress.maxObservedAnchor):null},origins:[target.storyId,...source.origins.filter(origin=>origin.startsWith('world-'))],...(source.images?{images:remapBookImages(source.images,bookId,target.bookId,item.branchId)}:{})}
      await commitPackage(payload,valid,true)
    }
    if(!valid())throw new Error('账号或服务器已变化')
    await removePackage(item.id,valid,true)
  }
  const queue=await cacheUpdate<PendingNote[]>(queueKey,items=>(items||[]).flatMap(note=>{
    if(note.anchor.bookId!==bookId)return [note]
    const target=retained.find(story=>story.branchIds.includes(note.anchor.branchId))
    if(!target)return []
    return [{...note,anchor:{...note.anchor,bookId:target.bookId},...(note.attempted?{submittedAnchor:note.submittedAnchor||{...note.anchor},blocked:'原来源已移除；这条笔记的提交结果待确认，本机文字仍保留。'}:{})}]
  }),valid,{allowDeleted:true})
  if(!queue||!valid())throw new Error('账号或服务器已变化，笔记迁移未完成')
}
