<script setup lang="ts">
import {ref,watch,onMounted,onUnmounted} from 'vue'
import {useRouter} from 'vue-router'
import {api,newKey} from '../api'
import {imageCapabilities} from '../api/assets'
import {cacheGet,cacheSet,accountId,backendBase} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import '../styles/assets.css'
const emit=defineEmits<{installed:[]}>(),router=useRouter(),capture=useRequestScope(),store=useApp()
const items=ref<{id:string;title:string;description:string;imageCount:number;license:string}[]>([]),busy=ref(false),error=ref(''),ready=ref(false),pending=ref<{id:string;key:string}|null>(null)
let version=0
const cacheKey='original-image-story-intent:v1'
async function lookup(valid:()=>boolean){
  const intent=pending.value;if(!intent)return false
  const found=await api<{found:boolean;receipt?:{bookId:string;branchId:string}}>('/assets/examples/'+intent.id+'/requests?requestId='+encodeURIComponent(intent.key))
  if(!valid())return false
  if(!found.found)return false
  if(!found.receipt)throw new Error('故事回执暂时不完整，请稍后再确认。')
  await cacheSet(cacheKey,null);if(!valid())return false
  pending.value=null;emit('installed');await router.push('/stories/book-'+found.receipt.bookId);return true
}
async function install(id:string){
  if(busy.value||!ready.value||!store.online||pending.value&&pending.value.id!==id)return
  const operation=++version,scope=capture(),owner=accountId(),base=backendBase(),valid=()=>scope.valid()&&operation===version&&owner===accountId()&&base===backendBase()
  busy.value=true;error.value=''
  try{
    if(pending.value&&await lookup(valid)||!valid())return
    const intent=pending.value||{id,key:newKey()};await cacheSet(cacheKey,intent);if(!valid())return;pending.value=intent
    const receipt=await api<{bookId:string;branchId:string}>('/assets/examples/'+id+'/install',{method:'POST',body:{},key:intent.key});if(!valid())return
    await cacheSet(cacheKey,null);if(!valid())return;pending.value=null;emit('installed');await router.push('/stories/book-'+receipt.bookId)
  }catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function confirm(){const operation=++version,scope=capture(),valid=()=>scope.valid()&&operation===version;busy.value=true;error.value='';try{if(!await lookup(valid)&&valid())error.value='暂未找到回执。可以由你再次明确加入同一套故事。'}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}}
async function load(){const operation=++version,scope=capture(),valid=()=>scope.valid()&&operation===version;try{const cap=await imageCapabilities();if(!valid())return;ready.value=!!cap;if(!cap)return;const value=await api<{items:typeof items.value}>('/assets/examples');if(!valid())return;items.value=value.items;const saved=await cacheGet<{id:string;key:string}>(cacheKey);if(!valid())return;pending.value=saved||null;if(pending.value)await lookup(valid)}catch(e){if(valid())error.value=(e as Error).message}}
function clear(){version++;items.value=[];pending.value=null;ready.value=false;busy.value=false;error.value=''}
watch(()=>store.user,()=>{clear();void load()})
onMounted(()=>{void load();window.addEventListener('reader-private-cleared',clear);window.addEventListener('reader-backend-changed',clear)})
onUnmounted(()=>{version++;window.removeEventListener('reader-private-cleared',clear);window.removeEventListener('reader-backend-changed',clear)})
</script>
<template><section aria-label="原创图文故事"><p v-if="!ready" class="muted">图文示例服务暂不可用。你仍可以导入自己的故事。</p><p v-else class="muted">三套自编故事与插画，CC0 许可。只有你选择后才加入书架。</p><p v-if="error" class="error" role="alert">{{error}}</p><div v-if="pending" class="hint">这套故事的加入结果待确认。<button :disabled="busy||!store.online" @click="confirm">确认加入结果</button></div><div class="image-original-choices"><article v-for="item in items" :key="item.id" class="image-original-card"><h3>{{item.title}}</h3><p>{{item.description}}</p><small>{{item.imageCount}} 张原创插画 · {{item.license}}</small><button class="primary" :disabled="busy||!store.online||!!pending&&pending.id!==item.id" @click="install(item.id)">加入这套图文故事</button></article></div></section></template>
