import {accountId,backendBase,cacheGeneration,purgeBookCache} from './bridge'
import type {Book} from '../api/types'

export interface RetainedBookStory {bookId:string;storyId:string;branchIds:string[];book?:Book}
export interface BookTombstone {bookId:string;retainedStories:RetainedBookStory[];retainedKnown:boolean;removedAt:string;cleanupPending:boolean}
export class BookDeletedError extends Error {
  readonly code='BOOK_DELETED';readonly status=404
  constructor(){super('来源小说已移除')}
}
const prefix='reader-book-deleted:v1:',referencePrefix='reader-book-reference:v1:'
const volatile=new Map<string,BookTombstone>()
const pending=new Map<string,Promise<void>>()
function scope(){return encodeURIComponent(backendBase())+':'+encodeURIComponent(accountId())+':'}
function tombstoneKey(bookId:string){return prefix+scope()+encodeURIComponent(bookId)}
function readJSON<T>(key:string):T|undefined {try{return JSON.parse(localStorage.getItem(key)||'null')||undefined}catch{return undefined}}
export function deletedBook(bookId:string):BookTombstone|undefined {
  if(!accountId()||!bookId)return
  const key=tombstoneKey(bookId)
  return readJSON<BookTombstone>(key)||volatile.get(key)
}
export const isBookDeleted=(bookId:string)=>!!deletedBook(bookId)
export function captureBookCacheScope(){
  const owner=accountId(),base=backendBase(),epoch=cacheGeneration()
  return ()=>!!owner&&owner===accountId()&&base===backendBase()&&epoch===cacheGeneration()
}
function references(id:string):string[]{return readJSON<string[]>(referencePrefix+scope()+encodeURIComponent(id))||[]}
function retainReference(id:string,books:string[]){
  if(!id||!accountId()||!books.length)return
  // An exact carrier response takes precedence over the removed source mapping.
  const alive=books.filter(book=>!isBookDeleted(book)),existing=references(id),existingAlive=existing.filter(book=>!isBookDeleted(book))
  const next=alive.length?alive:existingAlive.length?existingAlive:[...new Set([...existing,...books])]
  localStorage.setItem(referencePrefix+scope()+encodeURIComponent(id),JSON.stringify(next))
}
function url(path:string){return new URL(path,'https://book-cache.invalid')}
function requestBooks(path:string,body?:unknown):string[]{
  const u=url(path),parts=u.pathname.split('/').filter(Boolean),result=new Set<string>()
  if(parts[0]==='books'&&parts[1]&&parts[1]!=='imports'&&parts[1]!=='demo')result.add(parts[1])
  if(parts[0]==='stories'&&parts[1]?.startsWith('book-'))result.add(parts[1].slice(5))
  if(['branches','revisions','role-sessions','tasks','story-runs','candidates','generations'].includes(parts[0])&&parts[1]&&!(parts[0]==='generations'&&['capabilities','request-status'].includes(parts[1])))for(const book of references(parts[1]))result.add(book)
  for(const name of ['bookId','branchId','revisionId','sessionId']){
    const id=u.searchParams.get(name);if(id){if(name==='bookId')result.add(id);else for(const book of references(id))result.add(book)}
  }
  for(const book of valueBooks(body))result.add(book)
  if(!valueBooks(body).length&&body&&typeof body==='object')for(const name of ['branchId','sessionId','revisionId']){
    const id=(body as Record<string,unknown>)[name];if(typeof id==='string')for(const book of references(id))result.add(book)
  }
  return [...result]
}
export const bookIdsForRequest=(path:string,body?:unknown)=>requestBooks(path,body)
function valueBooks(value:unknown):string[]{
  const result=new Set<string>()
  function visit(node:unknown){
    if(Array.isArray(node)){for(const item of node)visit(item);return}
    if(!node||typeof node!=='object')return
    const item=node as Record<string,unknown>
    // World public history remains independent of a removed source book.
    if(item.kind==='world')return
    if(typeof item.bookId==='string')result.add(item.bookId)
    if(typeof item.id==='string'&&typeof item.originalBranchId==='string')result.add(item.id)
    for(const [name,id] of Object.entries(item)){
      if(!item.bookId&&['sessionId','taskId','runId','candidateId'].includes(name)&&typeof id==='string')for(const book of references(id))result.add(book)
      if(name!=='sourceAnchor'&&name!=='sourceBookId'&&name!=='submittedAnchor')visit(id)
    }
  }
  visit(value);return [...result]
}
export function recordBookReferences(path:string,value:unknown,body?:unknown){
  if(!accountId())return
  const inherited=requestBooks(path,body)
  function visit(node:unknown,parents:string[],root=false){
    if(Array.isArray(node)){for(const item of node)visit(item,parents,true);return}
    if(!node||typeof node!=='object')return
    const item=node as Record<string,unknown>;if(item.kind==='world')return
    const books=typeof item.bookId==='string'?[item.bookId]:typeof item.originalBranchId==='string'&&typeof item.id==='string'?[item.id]:parents
    if(root&&typeof item.id==='string')retainReference(item.id,books)
    for(const [name,id] of Object.entries(item)){
      if(['branchId','revisionId','sessionId','taskId','runId','candidateId','generationId','indexTaskId','pendingTaskId','headRevisionId','originalBranchId','originalRevisionId'].includes(name)&&typeof id==='string')retainReference(id,books)
      if(name!=='sourceAnchor'&&name!=='sourceBookId'&&name!=='submittedAnchor')visit(id,books,name==='items'||name==='branch')
    }
  }
  const parts=url(path).pathname.split('/').filter(Boolean)
  if(parts[1]&&['branches','revisions','role-sessions','tasks','story-runs','candidates','generations'].includes(parts[0])&&!(parts[0]==='generations'&&['capabilities','request-status'].includes(parts[1])))retainReference(parts[1],inherited)
  visit(body,inherited);visit(value,inherited,true)
}
export function cacheBookIds(key:string,value?:unknown):string[]{
  if(key.startsWith('world-'))return []
  const result=new Set(valueBooks(value))
  if(key.startsWith('api:'))for(const book of requestBooks(key.slice(4)))result.add(book)
  const direct=key.match(/^(?:asset|note-draft|character-index|character-chat):([^:]+)/)||key.match(/^offline-reading-package:v1:([^:]+)/)
  if(direct)result.add(direct[1])
  const indirect=key.match(/^(?:generation|generation-stream|generation-unconfirmed|pending-progress|chat-draft|chat-pending|selection-draft|selection-ai-input|selection-revision-intent):([^:]+)/)
  if(indirect)for(const book of references(indirect[1]))result.add(book)
  return [...result]
}
function filterBookCache(key:string,value:unknown,removed:(bookId:string)=>boolean):unknown {
  if(value===null)return null
  if(key.startsWith('world-'))return value
  if(cacheBookIds(key).some(removed))return undefined
  // Shared indexes/queues/lists retain other books rather than dropping them.
  if(Array.isArray(value))return value.filter(item=>!valueBooks(item).some(removed))
  if(value&&typeof value==='object'&&Array.isArray((value as {items?:unknown}).items))return {...value,items:((value as {items:unknown[]}).items).filter(item=>!valueBooks(item).some(removed))}
  return valueBooks(value).some(removed)?undefined:value
}
export function filterDeletedCache(key:string,value:unknown):unknown {return filterBookCache(key,value,isBookDeleted)}
export function filterBookCacheForRemoval(key:string,value:unknown,bookId:string):unknown {return filterBookCache(key,value,id=>id===bookId)}
export function filterDeletedCacheForWrite(key:string,value:unknown,previous:unknown):unknown {
  const visible=filterDeletedCache(key,value)
  if(visible===undefined||value===null||key.startsWith('world-'))return visible
  // A pending deletion hides its content immediately, but exact carrier
  // metadata may still be unavailable. Only already persisted shared rows
  // survive in quarantine. A late caller cannot add or modify those rows.
  const quarantined=(items:unknown[])=>items.filter(item=>valueBooks(item).some(id=>deletedBook(id)?.cleanupPending))
  if(Array.isArray(visible)&&Array.isArray(previous))return [...visible,...quarantined(previous)]
  if(visible&&typeof visible==='object'&&Array.isArray((visible as {items?:unknown}).items)&&previous&&typeof previous==='object'&&Array.isArray((previous as {items?:unknown}).items))return {...visible,items:[...(visible as {items:unknown[]}).items,...quarantined((previous as {items:unknown[]}).items)]}
  return visible
}
export function assertBookCacheWrite(key:string,value:unknown){
  if(value!==null&&filterDeletedCache(key,value)===undefined)throw new BookDeletedError()
}
export function bookRequestDeleted(path:string,body?:unknown,result?:unknown):string|undefined {
  const parts=url(path).pathname.split('/').filter(Boolean)
  for(const book of requestBooks(path,body)){
    const receipt=deletedBook(book);if(!receipt)continue
    // A retained branch is now independent. Its response must still contain
    // canonical carrier anchors; an old source response is rejected below.
    if(parts[0]==='branches'&&receipt.retainedStories.some(story=>story.branchIds.includes(parts[1]))&&!valueBooks(body).includes(book))continue
    return book
  }
  return valueBooks(result).find(isBookDeleted)
}
export function captureBookGuard(bookId:string){const valid=captureBookCacheScope();return ()=>valid()&&!isBookDeleted(bookId)}
function saveTombstone(value:BookTombstone){
  const key=tombstoneKey(value.bookId),minimal={...value,retainedStories:value.retainedStories.map(({bookId,storyId,branchIds})=>({bookId,storyId,branchIds}))}
  // Only deletion/alias identities survive outside the private cache. Never
  // leave book titles, original metadata or prose in localStorage receipts.
  volatile.set(key,minimal);localStorage.setItem(key,JSON.stringify(minimal))
}
export function markBookDeleted(bookId:string,retainedStories?:RetainedBookStory[]) {
  if(!accountId())throw new Error('账号已变化，请重新打开内容')
  const previous=deletedBook(bookId)
  const receipt:BookTombstone={bookId,removedAt:previous?.removedAt||new Date().toISOString(),retainedStories:retainedStories??previous?.retainedStories??[],retainedKnown:retainedStories!==undefined||!!previous?.retainedKnown,cleanupPending:true}
  saveTombstone(receipt)
  window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId}}))
  return receipt
}
export function invalidateBookCache(bookId:string,retainedStories:RetainedBookStory[]=[]):Promise<void>{
  const valid=captureBookCacheScope(),receipt=markBookDeleted(bookId,retainedStories),key=tombstoneKey(bookId)
  const existing=pending.get(key);if(existing)return existing
  const work=(async()=>{
    const {removeBookDownloads}=await import('../offline/storage')
    const targets:RetainedBookStory[]=[]
    for(const target of receipt.retainedStories){
      if(!valid())throw new Error('账号或服务器已变化')
      // Do not manufacture a carrier Book from source metadata.
      const {api}=await import('../api')
      const book=target.book||await api<Book>('/books/'+target.bookId)
      if(!valid())throw new Error('账号或服务器已变化')
      if(book.id!==target.bookId)throw new Error('保留故事信息不匹配，请重新载入')
      targets.push({...target,book})
    }
    if(!valid())throw new Error('账号或服务器已变化')
    await removeBookDownloads(bookId,targets,valid)
    if(!valid())throw new Error('账号或服务器已变化')
    await purgeBookCache(bookId,valid)
    if(!valid())throw new Error('账号或服务器已变化')
    saveTombstone({...receipt,retainedStories:targets,cleanupPending:false})
    window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId}}))
  })().finally(()=>{pending.delete(key)})
  pending.set(key,work);return work
}
export async function resumeDeletedBookCleanup(){
  const valid=captureBookCacheScope(),start=prefix+scope(),receipts:BookTombstone[]=[]
  for(let index=0;index<localStorage.length;index++){
    const key=localStorage.key(index);if(key?.startsWith(start)){const receipt=readJSON<BookTombstone>(key);if(receipt?.cleanupPending)receipts.push(receipt)}
  }
  for(const receipt of receipts){
    if(!valid())return
    try{
      let retained=receipt.retainedStories
      if(!receipt.retainedKnown){
        const {api}=await import('../api'),remote=await api<{retainedStories:RetainedBookStory[]}>('/books/'+receipt.bookId+'/deletion')
        if(!valid())return
        retained=remote.retainedStories
      }
      await invalidateBookCache(receipt.bookId,retained)
    }catch{/* Keep the committed tombstone/outbox for the next online reopen. */}
  }
}
window.addEventListener('storage',event=>{
  const changed=event as StorageEvent
  if(changed.key?.startsWith(prefix+scope())){
    const receipt=readJSON<BookTombstone>(changed.key)
    if(receipt)window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:receipt.bookId}}))
  }
})
