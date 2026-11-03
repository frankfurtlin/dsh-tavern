import {api,authGeneration,ApiError,newKey} from '../api'
import type {Bookmark,Anchor} from '../api/types'
import {cacheUpdate,accountId,backendBase} from '../platform/bridge'
import {isBookmarkDeleted} from '../platform/bookmark-cache'
import {noteQueue,queueKey,privateScope,applySyncedNote,conflictCopiesKey,type PendingNote} from './storage'

export async function queueNote(anchor:Anchor,text:string,note?:Bookmark,knownKey?:string,attempted=false){
  const valid=privateScope();let saved!:PendingNote
  const result=await cacheUpdate<PendingNote[]>(queueKey,items=>{
    const values=items||[],existing=note?values.find(item=>item.id===note.id||item.remoteId===note.id):knownKey?values.find(item=>item.key===knownKey):undefined
    if(existing&&!note&&knownKey){
      if(Object.entries(anchor).some(([field,value])=>existing.anchor[field as keyof Anchor]!==value))throw new Error('这次保存请求属于另一个文字位置，请重新打开对应笔记')
      // Restoring a create is not an edit; preserve subsequent local edits.
      saved={...existing,attempted:existing.attempted||attempted}
      return values.map(item=>item.id===saved.id?saved:item)
    }
    if(existing?.attempted&&!attempted)throw new Error('这条笔记正在确认同步，请先同步或保留另一份新笔记')
    saved={id:existing?.id||note?.id||'local-'+newKey(),anchor:{...anchor},note:text,remoteId:existing?.remoteId||(note&&!note.id.startsWith('local-')?note.id:undefined),baseVersion:existing?.baseVersion||note?.version||1,key:existing?.key||knownKey||newKey(),kind:existing?.kind||(note&&!note.id.startsWith('local-')?'update':'create'),attempted:existing?.attempted||attempted}
    return [...values.filter(item=>item.id!==saved.id),saved]
  },valid)
  if(!result)throw new Error('本机笔记未能保存，请保留文字后重试')
  window.dispatchEvent(new Event('reader-notes-changed'))
  return {id:saved.remoteId||saved.id,anchor:saved.anchor,note:saved.note,version:saved.baseVersion,status:'pending_sync'} satisfies Bookmark
}
async function updatePending(id:string,transform:(value:PendingNote)=>PendingNote|null,valid:()=>boolean){
  return cacheUpdate<PendingNote[]>(queueKey,items=>(items||[]).flatMap(item=>item.id!==id?[item]:transform(item)||[]),valid)
}
const syncing=new Map<string,Promise<{synced:number;remaining:number}>>()
export function syncNotes(){
  const key=accountId()+':'+backendBase()+':'+authGeneration(),pending=syncing.get(key)
  if(pending)return pending
  const promise=runSync().finally(()=>{syncing.delete(key)})
  syncing.set(key,promise);return promise
}
async function runSync(){
  const scope=privateScope(),epoch=authGeneration(),valid=()=>scope()&&epoch===authGeneration();let synced=0
  for(const note of await noteQueue()){
    if(!valid())break
    if(note.blocked||note.remoteId&&isBookmarkDeleted(note.anchor.bookId,note.remoteId))continue
    await updatePending(note.id,item=>({...item,attempted:true}),valid);if(!valid())break
    try{
      if(note.kind==='create'){const saved=await api<Bookmark>(`/books/${note.anchor.bookId}/bookmarks`,{method:'POST',key:note.key,body:{anchor:note.anchor,note:note.note}});if(valid())await applySyncedNote(saved,valid)}
      else if(note.remoteId){
        // A lost PATCH response can already have committed. Confirm content
        // before deciding between the original CAS and a conflict copy.
        const remote=await api<Bookmark>(`/books/${note.anchor.bookId}/bookmarks/${note.remoteId}`)
        if(!valid())break
        if(note.kind==='delete')throw new Error('删除笔记需要在线确认')
        if(remote.note!==note.note){
          if((remote.version||1)===note.baseVersion){const saved=await api<Bookmark>(`/books/${note.anchor.bookId}/bookmarks/${note.remoteId}`,{method:'PATCH',body:{expectedVersion:note.baseVersion,note:note.note}});if(valid())await applySyncedNote(saved,valid)}
          else {await applySyncedNote(remote,valid);await conflictCopy(note,valid)}
        }else await applySyncedNote(remote,valid)
      }
      if(!valid())break
      await updatePending(note.id,()=>null,valid);synced++
    }catch(e){
      if(!valid())break
      if(e instanceof ApiError&&e.code==='NOTE_CONFLICT'){
        try{await conflictCopy(note,valid);if(!valid())break;await updatePending(note.id,()=>null,valid);synced++}catch{break}
      }else if(e instanceof ApiError&&[404,409,422].includes(e.status))await updatePending(note.id,item=>({...item,blocked:e.message}),valid)
      else break
    }
  }
  if(valid())window.dispatchEvent(new Event('reader-notes-changed'))
  return {synced,remaining:valid()?(await noteQueue()).length:0}
}
async function conflictCopy(note:PendingNote,valid:()=>boolean){
  const key=note.conflictKey||newKey()
  await updatePending(note.id,item=>({...item,conflictKey:key}),valid);if(!valid())return
  const saved=await api<Bookmark>(`/books/${note.anchor.bookId}/bookmarks`,{method:'POST',key,body:{anchor:note.anchor,note:note.note}})
  if(!valid())return
  await applySyncedNote(saved,valid)
  await cacheUpdate<Record<string,string>>(conflictCopiesKey,items=>({...items,[saved.id]:note.remoteId||''}),valid)
  if(valid())window.dispatchEvent(new CustomEvent('reader-note-conflict-copy',{detail:{originalId:note.remoteId}}))
}
