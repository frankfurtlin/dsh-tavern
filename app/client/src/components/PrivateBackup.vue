<script setup lang="ts">
import {computed,ref,onMounted,onUnmounted,watch} from 'vue'
import {newKey,ApiError} from '../api'
import {backupCapabilities,loadBackupIntent,saveBackupIntent,lookupBackup,captureBackupScope,exportPrivateBackup,preparePrivateBackup,confirmPrivateBackup,downloadPrivateBackup,MAX_BACKUP_UPLOAD,type BackupIntent,type BackupOperation,type BackupFile} from '../api/backups'
import {nativeBackupCapabilities,selectNativeBackup} from '../platform/reader-backup'
import {accountId,native} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import '../styles/private-backup.css'

const props=defineProps<{mode:'export'|'import';storyId?:string}>(),emit=defineEmits<{restored:[storyId:string]}>()
const store=useApp(),capture=useRequestScope(()=>[props.mode,props.storyId]),ready=ref(false),loading=ref(true),busy=ref(false),error=ref(''),password=ref(''),confirmation=ref(''),visible=ref(false),includeSource=ref(false),file=ref<BackupFile|null>(null),pending=ref<BackupIntent|null>(null),operation=ref<BackupOperation|null>(null),missing=ref(false),confirmed=ref(false),confirmConflict=ref(false),retryConfirm=ref(false),downloadedBytes=ref(0)
let epoch=0,disposed=false
const expectedStory=()=>props.mode==='export'?props.storyId:undefined
const expires=computed(()=>!!operation.value&&Date.parse(operation.value.expiresAt)<=Date.now())
const target=computed(()=>store.user?.username||accountId())
const status=computed(()=>operation.value?.status==='restored'?'恢复副本已创建':operation.value?.status==='ready'?(props.mode==='import'?'预检通过':'加密备份已准备好'):operation.value?.status==='failed'?'本次操作未完成':pending.value?'正在确认已有请求':'')
const labels:Record<string,string>={character_artwork:'角色头像与立绘',character_look:'角色形象选择',world_chat:'独立聊天',world_chat_message:'独立聊天消息',play_control:'手动记忆',play_memory_revision:'手动记忆历史',checkpoint:'时间点',memory:'世界记忆',book:'小说',source:'来源',branch:'故事线',revision:'版本',content_block:'正文段落',world:'世界',world_instance:'世界',world_line:'世界线',world_checkpoint:'时间点',world_turn:'行动历史',world_event:'经历',world_memory:'记忆',world_publication:'已发布正文',character_identity:'人物',character_alias:'人物称呼',character_scope_revision:'人物资料',knowledge_snapshot:'知识',analysis_chunk:'资料段落',role_session:'对话',role_message:'消息',bookmark:'笔记',image_asset:'图片',asset:'图片',asset_variant:'图片尺寸',asset_placement:'图片位置',progress:'已读位置',resume_target:'恢复位置',reading_activity:'阅读记录',task:'历史任务',candidate:'草稿',usage:'历史用量'}
const quantities=computed(()=>{const rows=Object.entries(operation.value?.counts||{}),known=rows.filter(([key])=>labels[key]).map(([key,value])=>({key,label:labels[key]!,value})),other=rows.filter(([key])=>!labels[key]).reduce((sum,[,value])=>sum+value,0);return other?[...known,{key:'other',label:'其他记录',value:other}]:known})
function active(){const scope=capture(),version=epoch,valid=captureBackupScope(expectedStory(),()=>!disposed&&version===epoch);return()=>scope.valid()&&valid()}
function clearMemory(){epoch++;password.value='';confirmation.value='';file.value=null;pending.value=null;operation.value=null;confirmed.value=false;confirmConflict.value=false;retryConfirm.value=false;error.value='';busy.value=false;ready.value=false;visible.value=false;missing.value=false}
async function receive(value:BackupOperation,request:BackupIntent,valid:()=>boolean){
  if(!valid())return
  const refreshed=confirmConflict.value&&value.status==='ready'
  const next:BackupIntent=refreshed?{schema:1,kind:'restore',phase:'prepare',requestId:newKey(),operationId:value.id}:{...request,operationId:value.id}
  await saveBackupIntent(next,expectedStory(),valid);if(!valid())return
  pending.value=next;operation.value=value;missing.value=false;confirmed.value=false;confirmConflict.value=false;retryConfirm.value=next.phase==='confirm'&&value.status==='ready'&&value.version===next.expectedVersion
  if(value.status==='failed')error.value='这次备份未完成'+(value.errorCode?'（'+value.errorCode+'）':'')+'。可重新选择并明确开始。'
  if(value.status==='restored'){
    // Keep the lookup intent until local cleanup succeeds. A response-lost
    // confirm can therefore reopen the one committed copy using only GET.
    await saveBackupIntent(null,expectedStory(),valid);if(!valid())return
    pending.value=null;emit('restored',value.receipt!.storyId)
  }
}
async function query(){
  if(busy.value||!pending.value||!store.online||!ready.value)return
  const valid=active(),request=pending.value;busy.value=true;error.value=''
  try{const value=await lookupBackup(request,valid);if(!valid())return;if(value)await receive(value,request,valid);else{missing.value=true;error.value='尚未查到回执；没有再次提交。请保留原文件和本次密码，可手动重试原请求。'}}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function init(){
  const valid=active();loading.value=true
  try{const capability=await backupCapabilities();if(!valid())return;const files=native?await nativeBackupCapabilities():null;if(!valid())return;ready.value=!!capability&&(!native||!!files&&files.maxFileBytes>=MAX_BACKUP_UPLOAD);if(!ready.value)return;const request=await loadBackupIntent(expectedStory());if(!valid())return;pending.value=request;if(request?.kind==='export')includeSource.value=request.includeSource===true;if(request)await query()}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())loading.value=false}
}
function select(event:Event){const element=event.target as HTMLInputElement;file.value=element.files?.[0]||null;confirmed.value=false}
async function selectNative(){if(busy.value||pending.value&&!missing.value)return;const valid=active();busy.value=true;error.value='';try{const value=await selectNativeBackup();if(valid()){file.value=value;confirmed.value=false}}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}}
async function start(){
  if(busy.value||!ready.value||!store.online||pending.value&&!missing.value)return
  if(!password.value||props.mode==='export'&&password.value!==confirmation.value){error.value='请填写密码，并确认两次输入一致';return}
  if(props.mode==='import'&&!file.value){error.value='请选择 .srbackup 文件';return}
  const valid=active(),secret=password.value,selected=file.value;busy.value=true;error.value='';confirmed.value=false
  const request:BackupIntent=pending.value||{schema:1,kind:props.mode==='export'?'export':'restore',phase:props.mode==='export'?'export':'prepare',requestId:newKey(),...(expectedStory()?{storyId:expectedStory(),includeSource:includeSource.value}: {})}
  try{
    await saveBackupIntent(request,expectedStory(),valid);if(!valid())return;pending.value=request;missing.value=false
    // Passwords never enter cache, operation DTOs, events or a route. Clear
    // the live controls as soon as the explicit request begins.
    password.value='';confirmation.value=''
    const value=props.mode==='export'?await exportPrivateBackup(props.storyId!,secret,request.includeSource===true,request.requestId,valid):await preparePrivateBackup(selected!,secret,request.requestId,valid)
    if(valid())await receive(value,request,valid)
  }catch(e){if(valid())error.value=(e as Error).message+'。已有请求仅查询确认，不会自动重发。'}finally{if(valid())busy.value=false}
}
async function restore(){
  if(busy.value||!ready.value||!store.online||!confirmed.value||!operation.value||operation.value.status!=='ready'||expires.value||pending.value?.phase==='confirm'&&!retryConfirm.value)return
  const valid=active(),value=operation.value,request:BackupIntent=pending.value?.phase==='confirm'?pending.value:{schema:1,kind:'restore',phase:'confirm',requestId:newKey(),operationId:value.id,expectedVersion:value.version,targetOwnerId:accountId()};busy.value=true;error.value='';retryConfirm.value=false
  try{await saveBackupIntent(request,undefined,valid);if(!valid())return;pending.value=request;confirmed.value=false;const result=await confirmPrivateBackup(value,request.requestId,valid);if(valid())await receive(result,request,valid)}
  catch(e){if(valid()){error.value=(e as Error).message+'。请查询已有恢复结果。';if(e instanceof ApiError&&e.status===409&&['BACKUP_PREVIEW_CONFLICT','BACKUP_PREVIEW_EXPIRED'].includes(e.code)){confirmConflict.value=true;operation.value=null}}}finally{if(valid())busy.value=false}
}
async function reset(){
  if(busy.value||pending.value&&!(operation.value?.status==='failed'||expires.value))return
  const valid=active();busy.value=true
  try{await saveBackupIntent(null,expectedStory(),valid);if(!valid())return;pending.value=null;operation.value=null;missing.value=false;confirmed.value=false;password.value='';confirmation.value='';error.value=''}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function download(){
  if(busy.value||!operation.value||!store.online)return
  const valid=active(),value=operation.value;busy.value=true;error.value='';downloadedBytes.value=0
  try{await downloadPrivateBackup(value,valid,bytes=>{if(valid())downloadedBytes.value=bytes});if(valid())store.message('加密备份已交给浏览器保存')}
  catch(e){if(valid())error.value=(e as Error).name==='AbortError'?'已取消保存，本次加密备份仍可下载':(e as Error).message}finally{if(valid())busy.value=false}
}
function cleared(){clearMemory();loading.value=false}
onMounted(()=>{window.addEventListener('reader-private-cleared',cleared);window.addEventListener('reader-backend-changed',cleared);void init()})
onUnmounted(()=>{disposed=true;clearMemory();window.removeEventListener('reader-private-cleared',cleared);window.removeEventListener('reader-backend-changed',cleared)})
watch(()=>[store.user?.id,props.mode,props.storyId],()=>{clearMemory();void init()})
</script>

<template><section class="private-backup" :aria-label="mode==='export'?'导出私人完整备份':'导入私人备份'">
  <p class="muted">{{mode==='export'?'包含故事、世界、角色头像与立绘、独立聊天及主线和聊天的记忆与历史版本。备份含隐藏资料，请私人保存。':'恢复为当前账号的新副本，包含角色形象、独立聊天和记忆历史，不覆盖已有故事。恢复后使用当前账号的默认模型。'}}</p>
  <p v-if="loading" role="status">正在查询备份服务…</p>
  <p v-else-if="!ready" class="muted" role="status">{{native?'此安装包尚未提供流式备份文件读写，请使用网页版。':'备份服务尚未就绪，请稍后再试。'}}</p>
  <template v-else>
    <p v-if="error" id="backup-error" class="error" role="alert">{{error}}</p>
    <p v-if="status" role="status">{{status}}</p>
    <template v-if="!operation||missing">
      <label v-if="mode==='import'&&!native">备份文件<input type="file" accept=".srbackup" :disabled="busy||!!pending&&!missing" @change="select"></label><button v-if="mode==='import'&&native" :disabled="busy||!!pending&&!missing" @click="selectNative">选择备份文件</button><small v-if="native&&file">{{'displayName'in file?file.displayName:''}}</small>
      <label>本次备份密码<input v-model="password" :type="visible?'text':'password'" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="backup-error" :disabled="busy||!!pending&&!missing"></label>
      <label v-if="mode==='export'">再次输入密码<input v-model="confirmation" :type="visible?'text':'password'" autocomplete="off" :disabled="busy||!!pending&&!missing"></label>
      <button class="backup-text-button" :aria-pressed="visible" @click="visible=!visible">{{visible?'隐藏密码':'显示密码'}}</button>
      <label v-if="mode==='export'&&storyId?.startsWith('world-')" class="backup-checkbox"><input v-model="includeSource" type="checkbox" :disabled="busy||!!pending">一并包含整本来源小说</label>
      <button class="primary" :disabled="busy||!store.online||!!pending&&!missing||!password||mode==='import'&&!file||mode==='export'&&password!==confirmation" @click="start">{{busy?'正在处理…':missing?'明确重试原请求':mode==='export'?'生成加密备份':'解密并预检'}}</button>
    </template>
    <template v-if="operation">
      <dl v-if="quantities.length" class="backup-counts"><div v-for="quantity in quantities" :key="quantity.key"><dt>{{quantity.label}}</dt><dd>{{quantity.value}}</dd></div></dl>
      <template v-if="mode==='import'&&operation.status==='ready'">
        <p>目标账号：<strong>{{target}}</strong></p><p class="muted">历史任务不会重放，模型 Key 和消费额度不会转入。</p>
        <label class="backup-checkbox"><input v-model="confirmed" type="checkbox" :disabled="busy||expires||pending?.phase==='confirm'&&!retryConfirm">确认在当前账号恢复一个新副本</label>
        <button class="primary" :disabled="busy||!store.online||!confirmed||expires||pending?.phase==='confirm'&&!retryConfirm" @click="restore">{{retryConfirm?'明确重试原恢复请求':'确认恢复新副本'}}</button>
      </template>
      <button v-if="mode==='export'&&operation.status==='ready'" class="primary" :disabled="busy||!store.online||expires" @click="download">保存 .srbackup</button>
      <small v-if="downloadedBytes" role="status">已接收 {{Math.ceil(downloadedBytes/1024)}} KiB</small>
      <RouterLink v-if="operation.receipt" :to="'/stories/'+operation.receipt.storyId" class="inline-link">打开恢复的故事</RouterLink>
    </template>
    <div class="backup-actions"><button v-if="pending" :disabled="busy||!store.online" @click="query">{{confirmConflict?'载入最新信息':'查询已有请求'}}</button><button v-if="operation?.status==='failed'||expires" :disabled="busy" @click="reset">重新开始</button></div>
  </template>
</section></template>
