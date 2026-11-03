<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {api,ApiError,newKey,authGeneration} from '../api'
import {accountId,backendBase,cacheGet,cacheSet} from '../platform/bridge'
import {invalidateBookCache} from '../platform/book-cache'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const props=defineProps<{bookId:string;version:number;blocked?:boolean}>(),emit=defineEmits<{deleted:[];conflict:[]}>()
const store=useApp(),capture=useRequestScope(()=>props.bookId),pending=ref<{key:string;expectedVersion:number}|null>(null),ready=ref(false),busy=ref(false),error=ref('')
interface Receipt {deletionId:string;bookId:string;deleted:boolean;cleanupPending:boolean;cleanupState:string;retainedStories:{bookId:string;storyId:string;branchIds:string[]}[]}
const storageKey='book-delete-request:'+props.bookId
function identity(){const owner=accountId(),epoch=authGeneration(),base=backendBase();return()=>!!owner&&owner===accountId()&&epoch===authGeneration()&&base===backendBase()}
async function finish(receipt:Receipt,active:()=>boolean){
  if(!active()||!receipt.deleted||receipt.bookId!==props.bookId)throw new Error('删除回执不匹配，请重新查看')
  await invalidateBookCache(receipt.bookId,receipt.retainedStories)
  if(!active())return
  await cacheSet(storageKey,null)
}
async function check(){
  const scope=capture(),active=identity();busy.value=true;error.value=''
  try{const receipt=await api<Receipt>(`/books/${props.bookId}/deletion`);await finish(receipt,active);if(scope.valid()){store.message(receipt.cleanupPending?'小说已移除，文件正在清理':'小说已移除');emit('deleted')}}
  catch(e){if(scope.valid())error.value=e instanceof ApiError&&e.status===404?'尚未查到删除回执，可以重试同一请求。':(e as Error).message}
  finally{if(scope.valid())busy.value=false}
}
async function remove(){
  if(busy.value||!ready.value||props.blocked||!store.online)return
  const scope=capture(),active=identity();busy.value=true;error.value=''
  try{
    if(!pending.value){pending.value={key:newKey(),expectedVersion:props.version};await cacheSet(storageKey,pending.value);if(!scope.valid()||!active())return}
    const request=pending.value
    const receipt=await api<Receipt>(`/books/${props.bookId}`,{method:'DELETE',key:request.key,body:{expectedVersion:request.expectedVersion}})
    await finish(receipt,active)
    if(scope.valid()){store.message(receipt.cleanupPending?'小说已移除，文件正在清理':'小说已移除');emit('deleted')}
  }catch(e){
    if(!scope.valid())return
    if(e instanceof ApiError&&e.status===409){pending.value=null;await cacheSet(storageKey,null);if(!scope.valid())return;emit('conflict')}
    error.value=e instanceof ApiError&&e.status===0?'删除请求尚未确认，请查看结果或重试同一请求。':(e as Error).message
  }finally{if(scope.valid())busy.value=false}
}
onMounted(async()=>{const scope=capture();try{const request=await cacheGet<typeof pending.value>(storageKey);if(scope.valid()){pending.value=request||null;ready.value=true;if(request&&store.online)void check()}}catch(e){if(scope.valid())error.value=(e as Error).message}})
</script>
<template><div class="stack"><p>已有世界和存为阅读的故事会保留。</p><p v-if="error" class="error" role="alert">{{error}}</p><button v-if="pending" :disabled="busy||!store.online" @click="check">查看删除结果</button><button class="danger" :disabled="busy||!ready||blocked||!store.online" @click="remove">{{busy?'正在移除…':pending?'重试同一删除请求':'删除这本小说'}}</button></div></template>
