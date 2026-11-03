import {accountId,backendBase,cacheGeneration,cacheGet,cacheSet} from './bridge'
import type {BookmarkDeletionReceipt,BookmarkDeletionRequest} from '../api/bookmark-types'
const prefix='reader-bookmark-deleted:v1:'
const volatile=new Set<string>()
function identity(){return encodeURIComponent(backendBase())+':'+encodeURIComponent(accountId())+':'}
function key(bookId:string,id:string){return prefix+identity()+encodeURIComponent(bookId)+':'+encodeURIComponent(id)}
export function isBookmarkDeleted(bookId:string,id:string){if(!accountId())return false;const identity=key(bookId,id);return volatile.has(identity)||typeof localStorage!=='undefined'&&localStorage.getItem(identity)!==null}
export function bookmarkScope(){const owner=accountId(),base=backendBase(),epoch=cacheGeneration();return ()=>!!owner&&owner===accountId()&&base===backendBase()&&epoch===cacheGeneration()}
export function markBookmarkDeleted(bookId:string,bookmarkId:string,receipt?:BookmarkDeletionReceipt){
  if(!accountId())return
  // The marker contains no note, prose, anchor, title or authentication data.
  volatile.add(key(bookId,bookmarkId));localStorage.setItem(key(bookId,bookmarkId),JSON.stringify({bookId,bookmarkId,...(receipt?{version:receipt.version,receiptId:receipt.receiptId,deletedAt:receipt.deletedAt}:{})}))
  window.dispatchEvent(new CustomEvent('reader-bookmark-deleted',{detail:{bookId,bookmarkId}}))
}
export const bookmarkDeleteRequestKey=(bookId:string,id:string)=>'bookmark-delete-request:'+encodeURIComponent(backendBase())+':'+bookId+':'+id
export async function bookmarkDeletionRequest(bookId:string,id:string){return cacheGet<BookmarkDeletionRequest|null>(bookmarkDeleteRequestKey(bookId,id))}
export async function saveBookmarkDeletionRequest(request:BookmarkDeletionRequest|null,bookId:string,id:string){await cacheSet(bookmarkDeleteRequestKey(bookId,id),request)}
export function filterDeletedBookmarks<T>(value:T):T {
  const visit=(item:unknown):unknown=>{
    if(Array.isArray(item))return item.filter(note=>!deleted(note)).map(visit)
    if(!item||typeof item!=='object')return item
    const record=item as Record<string,unknown>
    if(Array.isArray(record.items))return {...record,items:visit(record.items)}
    return item
  }
  return visit(value) as T
}
function deleted(value:unknown){if(!value||typeof value!=='object')return false;const item=value as {id?:unknown;anchor?:{bookId?:unknown}};return typeof item.id==='string'&&typeof item.anchor?.bookId==='string'&&isBookmarkDeleted(item.anchor.bookId,item.id)}
export function bookmarkRequestDeleted(path:string){const parts=new URL(path,'https://bookmark.invalid').pathname.split('/').filter(Boolean);return parts[0]==='books'&&parts[2]==='bookmarks'&&parts[3]&&parts.length===4&&isBookmarkDeleted(parts[1],parts[3])?{bookId:parts[1],bookmarkId:parts[3]}:undefined}
export function bookmarkResponseDeleted(value:unknown){if(!deleted(value))return;const item=value as {id:string;anchor:{bookId:string}};return {bookId:item.anchor.bookId,bookmarkId:item.id}}
if(typeof window!=='undefined')window.addEventListener('storage',event=>{const changed=event as StorageEvent;if(!changed.key?.startsWith(prefix+identity())||!changed.newValue)return;try{const marker=JSON.parse(changed.newValue);if(typeof marker.bookId==='string'&&typeof marker.bookmarkId==='string')window.dispatchEvent(new CustomEvent('reader-bookmark-deleted',{detail:{bookId:marker.bookId,bookmarkId:marker.bookmarkId}}))}catch{}})
