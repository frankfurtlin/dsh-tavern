<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {api,ApiError,newKey,authGeneration} from '../api'
import type {Bookmark} from '../api/types'
import {validBookmarkReceipt,type BookmarkDeletionLookup,type BookmarkDeletionRequest,type BookmarkDeletionReceipt} from '../api/bookmark-types'
import {accountId,backendBase} from '../platform/bridge'
import {bookmarkDeletionRequest,saveBookmarkDeletionRequest,markBookmarkDeleted,bookmarkScope} from '../platform/bookmark-cache'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const props=defineProps<{note:Bookmark}>(),emit=defineEmits<{deleted:[id:string];cancel:[]}>()
const store=useApp(),capture=useRequestScope(()=>[props.note.id,props.note.anchor.bookId]),ready=ref(false),busy=ref(false),error=ref(''),pending=ref<BookmarkDeletionRequest|null>(null),latest=ref(props.note),conflict=ref(false),notFound=ref(false)
const bookId=props.note.anchor.bookId,id=props.note.id,path=`/books/${bookId}/bookmarks/${id}`
function identity(){const valid=bookmarkScope(),epoch=authGeneration(),owner=accountId(),base=backendBase();return ()=>valid()&&owner===accountId()&&base===backendBase()&&epoch===authGeneration()}
async function finish(receipt:BookmarkDeletionReceipt,active:()=>boolean){
  if(!validBookmarkReceipt(receipt,bookId,id))throw new Error('删除回执不匹配，请重新查看结果')
  if(!active())return
  markBookmarkDeleted(bookId,id,receipt)
  await saveBookmarkDeletionRequest(null,bookId,id)
}
async function lookup(request:BookmarkDeletionRequest,active:()=>boolean){
  const result=await api<BookmarkDeletionLookup>(path+'/deletion?requestId='+encodeURIComponent(request.key))
  if(!active())return
  if(result?.found===true){await finish(result.receipt,active);return true}
  if(result?.found!==false)throw new Error('删除确认服务尚未就绪，请稍后查询')
  return false
}
async function check(){
  if(busy.value||!pending.value||!store.online)return
  const scope=capture(),active=identity(),request=pending.value;busy.value=true;error.value=''
  try{const found=await lookup(request,active);if(!scope.valid()||!active())return;if(found){emit('deleted',id);store.message('笔记已删除')}else{notFound.value=true;error.value='尚未查到删除回执。笔记未被当作已删除；可明确重试原请求。'}}
  catch(e){if(scope.valid())error.value=(e as Error).message}
  finally{if(scope.valid())busy.value=false}
}
async function refresh(){
  if(busy.value||!conflict.value||!store.online)return
  const scope=capture(),active=identity();busy.value=true;error.value=''
  try{if(pending.value){await saveBookmarkDeletionRequest(null,bookId,id);if(!scope.valid()||!active())return;pending.value=null}const value=await api<Bookmark>(path);if(!scope.valid()||!active())return;if(value.id!==id||value.anchor.bookId!==bookId||!Number.isInteger(value.version)||Number(value.version)<1)throw new Error('笔记信息不匹配，请重新打开');latest.value=value;conflict.value=false;notFound.value=false}
  catch(e){if(scope.valid())error.value=(e as Error).message}
  finally{if(scope.valid())busy.value=false}
}
async function remove(){
  if(busy.value||!ready.value||conflict.value||!store.online||pending.value&&!notFound.value)return
  const scope=capture(),active=identity();busy.value=true;error.value=''
  try{
    if(!pending.value){if(!Number.isInteger(latest.value.version)||Number(latest.value.version)<1)throw new Error('笔记版本尚未确认，请重新打开');pending.value={bookId,bookmarkId:id,expectedVersion:latest.value.version!,key:newKey(),attempted:false};await saveBookmarkDeletionRequest(pending.value,bookId,id);if(!scope.valid()||!active())return}
    const request=pending.value
    // Pure lookup also gates the new DELETE contract on old servers. It never
    // turns a reopen or unknown response into an automatic mutation.
    if(await lookup(request,active)){if(scope.valid()&&active()){emit('deleted',id);store.message('笔记已删除')}return}
    if(!scope.valid()||!active())return
    pending.value={...request,attempted:true};await saveBookmarkDeletionRequest(pending.value,bookId,id);if(!scope.valid()||!active())return
    notFound.value=false
    const receipt=await api<BookmarkDeletionReceipt>(path,{method:'DELETE',key:request.key,body:{expectedVersion:request.expectedVersion}})
    await finish(receipt,active);if(scope.valid()&&active()){emit('deleted',id);store.message('笔记已删除')}
  }catch(e){
    if(!scope.valid()||!active())return
    if(e instanceof ApiError&&e.code==='NOTE_CONFLICT'){conflict.value=true;try{await saveBookmarkDeletionRequest(null,bookId,id);if(!scope.valid()||!active())return;pending.value=null}catch{if(scope.valid())error.value='笔记已有更新，本机请求清理失败；载入最新信息后再确认。';return}}
    error.value=e instanceof ApiError&&(e.status===0||e.status>=500)?'删除结果尚未确认，请查看删除结果。':(e as Error).message
  }finally{if(scope.valid())busy.value=false}
}
onMounted(async()=>{const scope=capture(),active=identity();try{const value=await bookmarkDeletionRequest(bookId,id);if(!scope.valid()||!active())return;if(value&&(value.bookId!==bookId||value.bookmarkId!==id||!Number.isInteger(value.expectedVersion)||value.expectedVersion<1||typeof value.key!=='string'||!value.key))throw new Error('本机删除请求不匹配');pending.value=value||null;ready.value=true;if(value&&store.online)void check()}catch(e){if(scope.valid())error.value=(e as Error).message}})
</script>
<template><div class="stack"><p>只删除这条笔记及书签，正文和阅读进度会保留。</p><p v-if="latest.note" class="note-copy">{{latest.note}}</p><p v-if="error" class="error" role="alert">{{error}}</p><button v-if="conflict" :disabled="busy||!store.online" @click="refresh">载入最新信息</button><button v-if="pending" :disabled="busy||!store.online" @click="check">查看删除结果</button><div class="actions"><button :disabled="busy" @click="emit('cancel')">保留笔记</button><button class="danger" :disabled="busy||!ready||conflict||!store.online||!!pending&&!notFound" @click="remove">{{busy?'正在确认…':pending?'重试同一删除请求':'确认删除笔记'}}</button></div></div></template>
