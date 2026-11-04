<script setup lang="ts">
import {ref,onMounted,onUnmounted,watch} from 'vue'
import {Upload,Download,LockKeyhole,BookOpen} from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import {listVaults,unlockVault,type LocalVault} from '../repositories/local-vault'
import {originalSource} from '../repositories/local-source'
import {exportLocalBackup} from '../repositories/local-backup'
import type {Document,LocalStory} from '../domain/local-contract'
import type {ImportStatus} from '../api/types'
import {api,newKey} from '../api'
import {storyPath} from '../api/stories'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const store=useApp(),capture=useRequestScope(),available=ref<{id:string;createdAt:string}[]>([]),selected=ref(''),password=ref(''),backupPassword=ref(''),rows=ref<Document<LocalStory>[]>([]),busy=ref(false),loading=ref(true),error=ref(''),notice=ref(''),unlocked=ref(false),imported=ref<Record<string,string>>({})
let vault:LocalVault|null=null,disposed=false,controller:AbortController|null=null
const intents=new Map<string,{key:string;task?:string}>()
function lock(){vault?.lock();vault=null;unlocked.value=false;password.value='';backupPassword.value='';rows.value=[];intents.clear();imported.value={};controller?.abort()}
async function load(){const scope=capture();loading.value=true;error.value='';try{const items=await listVaults();if(scope.valid()){available.value=items;selected.value=items[0]?.id||''}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())loading.value=false}}
async function open(){if(busy.value||!selected.value)return;const scope=capture();busy.value=true;error.value='';try{const opened=await unlockVault(selected.value,password.value);if(!scope.valid()){opened.lock();return}vault=opened;const stories=await opened.list<LocalStory>('story');if(scope.valid()){rows.value=stories;unlocked.value=true}else opened.lock()}catch(e){if(scope.valid())error.value=(e as Error).message}finally{password.value='';if(scope.valid())busy.value=false}}
async function importStory(row:Document<LocalStory>){
 const opened=vault;if(!opened||busy.value)return;const scope=capture(),valid=()=>scope.valid()&&vault===opened&&opened.unlocked
 busy.value=true;error.value='';notice.value='正在读取原文…'
 const intent=intents.get(row.id)||{key:newKey(),task:undefined as string|undefined};intents.set(row.id,intent)
 try{
  const source=await originalSource(opened,row.value);if(!valid())return
  if(!intent.task){const form=new FormData();form.append('file',new File([Uint8Array.from(source.bytes)],source.name));const created=await api<{importTaskId:string}>('/books/imports',{method:'POST',key:intent.key,body:form});if(!valid())return;intent.task=created.importTaskId}
  for(let attempt=0;attempt<90&&valid();attempt++){
   const preview=await api<ImportStatus&{bookId?:string}>('/books/imports/'+intent.task);if(!valid())return
   if(preview.importStatus==='reading_ready'&&preview.bookId){imported.value={...imported.value,[row.id]:preview.bookId};notice.value='已导入当前账号，原资料保持不变。';return}
   if(['failed','cancelled'].includes(preview.state))throw new Error('原文导入未完成，可重试或先导出备份。')
   if(preview.importStatus==='awaiting_confirmation'){
    const result=await api<{bookId:string}>('/books/imports/'+intent.task+'/confirm',{method:'POST',key:'history-confirm-'+intent.task,body:{parserVersion:preview.parserVersion,title:row.value.title,encoding:preview.encoding,reuseDuplicate:true,prepareCharacters:false,chapterOrder:preview.chapters.map(c=>c.id)}})
    if(valid()){imported.value={...imported.value,[row.id]:result.bookId};notice.value='已加入我的作品，原资料保持不变。'}return
   }
   notice.value='正在导入原文，请稍候…';await new Promise(resolve=>setTimeout(resolve,500))
  }
  if(valid())notice.value='原文仍在处理，再次点击会继续查询本次导入。'
 }catch(e){if(valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}
}
async function exportAll(){const opened=vault;if(!opened||busy.value)return;const scope=capture();busy.value=true;error.value='';controller=new AbortController();try{const bytes=await exportLocalBackup(opened,backupPassword.value,false,controller.signal);if(!scope.valid()||vault!==opened)return;const url=URL.createObjectURL(new Blob([Uint8Array.from(bytes)],{type:'application/octet-stream'})),a=document.createElement('a');a.href=url;a.download='旧版内容备份.storyvault';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice.value='完整备份已导出，原资料保持不变。'}catch(e){if(scope.valid())error.value=(e as Error).message}finally{backupPassword.value='';controller=null;if(scope.valid())busy.value=false}}
watch(()=>store.user?.id,lock)
onMounted(()=>{void load();window.addEventListener('reader-backend-changed',lock);window.addEventListener('reader-private-cleared',lock)})
onUnmounted(()=>{disposed=true;lock();window.removeEventListener('reader-backend-changed',lock);window.removeEventListener('reader-private-cleared',lock)})
</script>
<template><div class="workspace-page"><PageHeader title="导入旧版内容" description="把以前的故事导入当前账号，之后继续阅读。"></PageHeader><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" class="product-hint" role="status">{{notice}}</p><p v-if="loading" role="status">正在检查可恢复的内容…</p><section v-else-if="!available.length" class="workspace-empty"><BookOpen :size="36"/><h2>没有发现旧版内容</h2><p>可以直接回到探索页继续使用。</p><RouterLink to="/explore" class="inline-link">返回探索</RouterLink></section><section v-else-if="!unlocked" class="workspace-card history-import-card"><h2><LockKeyhole :size="20"/>打开旧版内容</h2><p>旧版内容使用独立口令加密。这里只用于恢复以前的资料，不会创建另一套工作区。</p><form @submit.prevent="open"><label v-if="available.length>1">选择记录<select v-model="selected" :disabled="busy"><option v-for="(v,i) in available" :key="v.id" :value="v.id">记录 {{i+1}} · {{new Date(v.createdAt).toLocaleDateString()}}</option></select></label><label>旧版内容口令<input v-model="password" type="password" autocomplete="off" required minlength="8" maxlength="1024" :disabled="busy"></label><button class="primary" :disabled="busy">{{busy?'正在打开…':'查看可恢复内容'}}</button></form></section><template v-else><section class="workspace-card"><h2>选择故事原文</h2><p>原文会加入当前账号书架，相同文件会复用已有书籍。分支、对话和图片仍保留在原资料中，可通过完整备份带走。导入不调用 AI。</p><p v-if="!rows.length">没有可导入的故事原文，可以导出完整备份。</p><article v-for="row in rows" :key="row.id" class="history-import-row"><div><strong>{{row.value.title}}</strong><small>{{row.value.chapters.length}} 章</small></div><RouterLink v-if="imported[row.id]" :to="storyPath('book',imported[row.id]!)" class="inline-link">打开作品</RouterLink><button v-else :disabled="busy" @click="importStory(row)"><Upload :size="16"/>导入当前账号</button></article></section><section class="workspace-card history-import-card"><h2>导出完整备份</h2><p>保留原有故事、分支、对话和图片等内容。备份不包含模型密钥，原资料不会删除。</p><form @submit.prevent="exportAll"><label>设置备份口令<input v-model="backupPassword" type="password" autocomplete="new-password" required minlength="8" maxlength="1024" :disabled="busy"></label><button :disabled="busy"><Download :size="17"/>导出完整备份</button></form></section></template></div></template>
<style scoped>.history-import-card{max-width:720px;margin:24px 0}.history-import-card h2{display:flex;gap:8px;align-items:center}.history-import-card form{max-width:480px}.history-import-row{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:20px 0;border-top:1px solid var(--edge)}.history-import-row>div{display:grid;gap:8px}.history-import-row small{color:var(--quiet);font-size:12px}@media(max-width:600px){.history-import-row{align-items:start;flex-direction:column;gap:12px}}</style>
