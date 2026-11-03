<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {newKey,ApiError} from '../api'
import {readingExportAvailable,loadReadingIntent,saveReadingIntent,createReadingExport,queryReadingExport,downloadReadingExport,captureReadingExportScope,validateReadingRequest,type ReadingExportScope,type ReadingExportIntent,type ReadingExportJob} from '../api/reading-export'
import {native} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import '../styles/reading-export.css'
const props=defineProps<{scope:ReadingExportScope}>(),store=useApp(),capture=useRequestScope(()=>props.scope)
const loading=ref(true),ready=ref(false),busy=ref(false),error=ref(''),format=ref<'txt'|'epub'>('epub'),range=ref<'observed'|'selected'>('observed'),start=ref('1'),end=ref('1'),unseen=ref(false),pending=ref<ReadingExportIntent|null>(null),job=ref<ReadingExportJob|null>(null),missing=ref(false),received=ref(0),saved=ref(false)
let epoch=0,disposed=false
const expired=computed(()=>!!job.value&&(job.value.status==='expired'||Date.parse(job.value.expiresAt)<=Date.now()))
const locked=computed(()=>!!pending.value)
const status=computed(()=>expired.value?'文件已过期':job.value?.status==='ready'?'阅读文件已准备好':job.value?.status==='failed'?'这次导出未完成':pending.value?'等待导出回执':'')
function active(){const version=epoch,scope=capture(),valid=captureReadingExportScope(props.scope,()=>!disposed&&version===epoch);return()=>scope.valid()&&valid()}
function restoreFields(value:ReadingExportIntent){format.value=value.request.format;range.value=value.request.range;start.value=String(value.request.startChapter||1);end.value=String(value.request.endChapter||1);unseen.value=value.request.allowUnseen}
async function receive(value:ReadingExportJob,request:ReadingExportIntent,valid:()=>boolean){
  if(!valid())return
  // Preserve a received acknowledgement in memory even if local persistence
  // fails. Reopen can recover it by the already persisted original request key.
  job.value=value;pending.value={...request,jobId:value.id};missing.value=false;saved.value=false
  await saveReadingIntent(props.scope,pending.value,valid)
}
async function query(){if(busy.value||!pending.value||!ready.value||!store.online)return;const valid=active(),request=pending.value;busy.value=true;error.value='';try{const value=await queryReadingExport(request,valid);if(!valid())return;if(value)await receive(value,request,valid);else{missing.value=true;error.value='尚未查到回执；没有再次提交。'}}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}}
async function init(){const valid=active();loading.value=true;try{const capability=await readingExportAvailable();if(!valid())return;ready.value=capability;if(!ready.value)return;const value=await loadReadingIntent(props.scope,valid);if(!valid())return;pending.value=value;if(value){restoreFields(value);await query()}}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())loading.value=false}}
async function submit(){
  if(busy.value||!ready.value||!store.online||pending.value&&!missing.value)return
  const valid=active();error.value='';busy.value=true
  try{
    const request=pending.value||{schema:1 as const,requestId:newKey(),request:validateReadingRequest({format:format.value,scope:props.scope,range:range.value,allowUnseen:range.value==='selected'&&unseen.value,...(range.value==='selected'?{startChapter:Number(start.value),endChapter:Number(end.value)}:{})})}
    await saveReadingIntent(props.scope,request,valid);if(!valid())return;pending.value=request;missing.value=false
    const value=await createReadingExport(request.request,request.requestId,valid);if(valid())await receive(value,request,valid)
  }catch(e){if(valid()){
    error.value=(e as Error).message
    if(e instanceof ApiError&&e.code==='EXPORT_UNSEEN_CONFIRMATION_REQUIRED'&&pending.value){try{
      const request=pending.value,value=await queryReadingExport(request,valid)
      if(valid()&&value)await receive(value,request,valid)
      else if(valid()){
        // A definite validation rejection plus a missing receipt proves this
        // attempt did not create an export. Keep the user's range and allow
        // them to consent explicitly; unknown responses retain the old key.
        await saveReadingIntent(props.scope,null,valid)
        if(valid()){pending.value=null;missing.value=false;job.value=null}
      }
    }catch{/* Keep the original key until a later explicit GET succeeds. */}}
  }}finally{if(valid())busy.value=false}
}
async function download(){if(busy.value||!store.online||!job.value||expired.value)return;const value=job.value,valid=active();busy.value=true;error.value='';received.value=0;saved.value=false;try{await downloadReadingExport(value,valid,bytes=>{if(valid())received.value=bytes});if(valid())saved.value=true}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}}
async function reset(){if(busy.value||!job.value||!['ready','failed','expired'].includes(job.value.status))return;const valid=active();busy.value=true;error.value='';try{await saveReadingIntent(props.scope,null,valid);if(valid()){pending.value=null;job.value=null;missing.value=false;received.value=0;saved.value=false;unseen.value=false}}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}}
function clear(){epoch++;pending.value=null;job.value=null;ready.value=false;loading.value=true;busy.value=false;error.value='';missing.value=false;saved.value=false;received.value=0;format.value='epub';range.value='observed';start.value='1';end.value='1';unseen.value=false}
function cleared(){clear();void init()}
onMounted(()=>{window.addEventListener('reader-private-cleared',cleared);void init()});onUnmounted(()=>{disposed=true;epoch++;window.removeEventListener('reader-private-cleared',cleared)})
watch([()=>store.user?.id,()=>JSON.stringify(props.scope)],()=>{clear();void init()})
</script>
<template><div class="reading-export"><p v-if="loading" role="status">正在查看导出服务…</p><p v-else-if="!ready" class="muted">{{native?'此安装包尚未提供阅读文件保存，请更新后重试。':'当前服务尚未提供阅读文件导出。'}}</p><template v-else><form @submit.prevent="submit"><div class="export-options"><label>文件格式<select v-model="format" :disabled="busy||locked" aria-label="文件格式"><option value="epub">EPUB</option><option value="txt">TXT</option></select></label><label>导出范围<select v-model="range" :disabled="busy||locked" aria-label="导出范围"><option value="observed">已读部分</option><option value="selected">选择章节</option></select></label></div><template v-if="range==='selected'"><div class="export-options"><label>起始章节<input v-model="start" type="number" inputmode="numeric" min="1" max="10000" step="1" :disabled="busy||locked" aria-label="起始章节"></label><label>结束章节<input v-model="end" type="number" inputmode="numeric" min="1" max="10000" step="1" :disabled="busy||locked" aria-label="结束章节"></label></div><label class="export-checkbox"><input v-model="unseen" type="checkbox" :disabled="busy||locked">包含未读内容</label></template><button v-if="!pending" class="primary" :disabled="busy||!store.online">导出阅读文件</button></form><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="status" role="status">{{status}}</p><div v-if="job?.status==='ready'&&!expired" class="export-file"><strong>{{job.fileName}}</strong><small>{{job.chapterCount}} 章 · {{((job.size||0)/1024).toFixed(1)}} KB</small><button class="primary" :disabled="busy||!store.online" @click="download">保存 {{job.format?.toUpperCase()}}</button><small v-if="received">已接收 {{(received/1024).toFixed(1)}} KB</small><small v-if="saved" role="status">{{native?'文件已校验并保存到所选位置':'文件已校验并交给浏览器保存'}}</small></div><div v-if="pending" class="actions"><button :disabled="busy||!store.online" @click="query">查询本次请求</button><button v-if="missing" :disabled="busy||!store.online" @click="submit">重试原请求</button><button v-if="job&&['ready','failed','expired'].includes(job.status)" :disabled="busy" @click="reset">重新选择范围</button></div></template></div></template>
