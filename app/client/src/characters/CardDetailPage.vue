<script setup lang="ts">
import {ref,reactive,computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute,useRouter,RouterLink} from 'vue-router'
import {ArrowLeft,Brain,BookOpen,Download,Eye,Heart,MessageSquareText,Pencil,Plus,Replace,Save,Sparkles,Trash2,UserRound} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import PersonaControl from './PersonaControl.vue'
import {saveBlob,safeFileName} from '../platform/download'
import {useRequestScope} from '../platform/request-scope'
import {cardAvatarBlob,cardGreetings,contextPreview,createLore,createMemory,createPreset,deleteLore,deleteMemory,exportCardBlob,extractMemories,getCard,listLore,listMemories,listPresets,setCompanion,startCardChat,updateCard,updateLore,updateMemory,type CardOut,type CardUpdate,type ContextPreview,type LoreEntry,type Memory,type Preset} from './api'

const route=useRoute(),router=useRouter(),capture=useRequestScope()
const cardId=computed(()=>String(route.params.id||''))
const card=ref<CardOut|null>(null),lore=ref<LoreEntry[]>([]),presets=ref<Preset[]>([])
const loading=ref(true),error=ref(''),notice=ref(''),opening=ref(false),avatarUrl=ref('')
const form=reactive({name:'',description:'',personality:'',scenario:'',firstMessage:'',systemPrompt:'',postHistoryInstructions:'',tagsText:''})
const saving=ref(false),saveError=ref('')
const presetId=ref(''),presetBusy=ref(false),presetNotice=ref(''),showPresetForm=ref(false),presetSaving=ref(false)
const presetForm=reactive({name:'',systemSuffix:''})
const loreEditing=ref(''),loreSaving=ref(false),loreError=ref(''),deletingLore=ref(''),loreBusy=ref('')
const loreForm=reactive({name:'',keys:'',content:'',enabled:true,order:100})
const previewMessage=ref(''),preview=ref<ContextPreview|null>(null),previewLoading=ref(false),previewError=ref('')
const memories=ref<Memory[]>([]),memoryError=ref(''),memoryEditing=ref(''),memorySaving=ref(false),memoryBusy=ref(''),deletingMemory=ref('')
const extracting=ref(false),extractNotice=ref(''),companionBusy=ref(false),companionNotice=ref('')
const exporting=ref(false),exportError=ref(''),personaId=ref<string|null>(null)
const greetings=ref<string[]>([]),greetingsBusy=ref(false),greetingsError=ref('')
const memoryForm=reactive({kind:'fact' as Memory['kind'],title:'',content:'',salience:50,enabled:true})
const memoryKinds:{value:Memory['kind'];label:string}[]=[
  {value:'fact',label:'事实'},{value:'preference',label:'偏好'},{value:'relationship',label:'关系'},{value:'event',label:'事件'},
]
function kindLabel(kind:string){return memoryKinds.find(item=>item.value===kind)?.label||kind}
let epoch=0

function splitList(value:string):string[]{
  return [...new Set(value.split(/[,，、\n]/).map(item=>item.trim()).filter(Boolean))]
}
function fillForm(value:CardOut){
  form.name=value.name||''
  form.description=value.description||''
  form.personality=value.personality||''
  form.scenario=value.scenario||''
  form.firstMessage=value.firstMessage||''
  form.systemPrompt=value.systemPrompt||''
  form.postHistoryInstructions=value.postHistoryInstructions||''
  form.tagsText=(value.tags||[]).join('，')
}
function resetForm(){if(card.value)fillForm(card.value)}
function revokeAvatar(){if(avatarUrl.value){URL.revokeObjectURL(avatarUrl.value);avatarUrl.value=''}}
async function loadAvatar(){
  if(!card.value?.hasAvatar)return
  // Older test hosts and non-browser shells have no object URLs.
  if(typeof URL==='undefined'||typeof URL.createObjectURL!=='function')return
  try{
    const blob=await cardAvatarBlob(cardId.value)
    if(!blob)return
    revokeAvatar();avatarUrl.value=URL.createObjectURL(blob)
  }catch{/* the initial letter placeholder is enough when the avatar request fails */}
}
async function load(){
  const id=cardId.value
  if(!id)return
  const scope=capture(),version=++epoch
  loading.value=true;error.value=''
  try{
    const fetched=await getCard(id)
    if(!scope.valid()||version!==epoch)return
    card.value=fetched;fillForm(fetched);presetId.value=fetched.presetId||''
    void loadAvatar()
    // A card without a world book or preset must still open while the B-step
    // server endpoints are rolling out, so these two load independently.
    const [loreResult,presetResult,memoryResult]=await Promise.allSettled([listLore(id),listPresets(),listMemories(id)])
    if(!scope.valid()||version!==epoch)return
    if(loreResult.status==='fulfilled'){lore.value=[...loreResult.value].sort((a,b)=>a.order-b.order);loreError.value=''}
    else loreError.value=(loreResult.reason as Error).message||'世界书暂时不可用。'
    if(presetResult.status==='fulfilled')presets.value=presetResult.value
    else presetNotice.value=(presetResult.reason as Error).message||'预设列表暂时不可用。'
    if(memoryResult.status==='fulfilled'){memories.value=sortMemories(memoryResult.value);memoryError.value=''}
    else memoryError.value=(memoryResult.reason as Error).message||'长期记忆暂时不可用。'
  }catch(cause){
        if(scope.valid()&&version===epoch)error.value=(cause as Error).message
  }finally{
    if(scope.valid()&&version===epoch)loading.value=false
  }
}
async function refreshLore(){
  const id=cardId.value
  if(!id)return
  const scope=capture()
  try{const items=await listLore(id);if(scope.valid())lore.value=[...items].sort((a,b)=>a.order-b.order) }catch(cause){if(scope.valid())loreError.value=(cause as Error).message}
}
async function saveCard(){
  const id=cardId.value
  if(!id||saving.value)return
  if(!form.name.trim()){saveError.value='请填写角色名称。';return}
  const scope=capture(),version=epoch
  saving.value=true;saveError.value='';notice.value=''
  const patch:CardUpdate={
    name:form.name.trim(),
    description:form.description,
    personality:form.personality,
    scenario:form.scenario,
    firstMessage:form.firstMessage,
    systemPrompt:form.systemPrompt,
    postHistoryInstructions:form.postHistoryInstructions,
    tags:splitList(form.tagsText).slice(0,20),
  }
  try{
    const updated=await updateCard(id,patch)
    if(!scope.valid()||version!==epoch)return
    card.value={...card.value,...updated} as CardOut
    fillForm(card.value)
    notice.value='角色设定已保存，下一轮对话生效。'
  }catch(cause){
    if(scope.valid()&&version===epoch)saveError.value=(cause as Error).message
  }finally{
    if(scope.valid()&&version===epoch)saving.value=false
  }
}
function newLore(){
  loreEditing.value='new'
  Object.assign(loreForm,{name:'',keys:'',content:'',enabled:true,order:100})
  loreError.value='';deletingLore.value=''
}
function editLore(entry:LoreEntry){
  loreEditing.value=entry.id
  Object.assign(loreForm,{name:entry.name,keys:entry.keys.join('，'),content:entry.content,enabled:entry.enabled,order:entry.order})
  loreError.value='';deletingLore.value=''
}
function cancelLore(){loreEditing.value='';loreError.value=''}
async function saveLore(){
  const id=cardId.value
  if(!id||loreSaving.value)return
  if(!loreForm.content.trim()){loreError.value='请填写世界书正文。';return}
  const scope=capture(),target=loreEditing.value
  loreSaving.value=true;loreError.value=''
  const payload={name:loreForm.name.trim(),keys:splitList(loreForm.keys).slice(0,20),content:loreForm.content,enabled:loreForm.enabled,order:Number(loreForm.order)||100}
  try{
    if(target==='new')await createLore(id,payload)
    else await updateLore(id,target,payload)
    if(!scope.valid())return
    loreEditing.value=''
    await refreshLore()
    if(scope.valid())notice.value='世界书条目已保存。'
  }catch(cause){
    if(scope.valid())loreError.value=(cause as Error).message
  }finally{
    if(scope.valid())loreSaving.value=false
  }
}
async function toggleLore(entry:LoreEntry){
  const id=cardId.value
  if(!id||loreBusy.value)return
  const scope=capture()
  loreBusy.value=entry.id;loreError.value=''
  try{
    const updated=await updateLore(id,entry.id,{enabled:!entry.enabled})
    if(!scope.valid())return
    const index=lore.value.findIndex(item=>item.id===entry.id)
    if(index>=0)lore.value[index]={...lore.value[index],...updated}
  }catch(cause){
    if(scope.valid())loreError.value=(cause as Error).message
  }finally{
    if(scope.valid())loreBusy.value=''
  }
}
async function removeLore(entry:LoreEntry){
  const id=cardId.value
  if(!id||loreBusy.value)return
  const scope=capture()
  loreBusy.value=entry.id;loreError.value=''
  try{
    await deleteLore(id,entry.id)
    if(!scope.valid())return
    deletingLore.value=''
    lore.value=lore.value.filter(item=>item.id!==entry.id)
    notice.value='世界书条目已删除。'
  }catch(cause){
    if(scope.valid())loreError.value=(cause as Error).message
  }finally{
    if(scope.valid())loreBusy.value=''
  }
}
async function bindPreset(){
  const id=cardId.value
  if(!id||presetBusy.value)return
  const scope=capture()
  presetBusy.value=true;presetNotice.value=''
  try{
    const updated=await updateCard(id,{presetId:presetId.value||null})
    if(!scope.valid())return
    card.value={...card.value,...updated} as CardOut
    presetNotice.value=presetId.value?'已绑定该预设，下一轮对话生效。':'已解除预设绑定。'
  }catch(cause){
    if(scope.valid())presetNotice.value=(cause as Error).message
  }finally{
    if(scope.valid())presetBusy.value=false
  }
}
async function createNewPreset(){
  const id=cardId.value
  if(!id||presetSaving.value)return
  if(!presetForm.name.trim()){presetNotice.value='请填写预设名称。';return}
  const scope=capture()
  presetSaving.value=true;presetNotice.value=''
  try{
    const created=await createPreset({name:presetForm.name.trim(),systemSuffix:presetForm.systemSuffix})
    if(!scope.valid())return
    presets.value=[...presets.value,created]
    presetId.value=created.id
    await updateCard(id,{presetId:created.id})
    if(!scope.valid())return
    presetForm.name='';presetForm.systemSuffix='';showPresetForm.value=false
    presetNotice.value='预设已创建并绑定到这张角色卡。'
  }catch(cause){
    if(scope.valid())presetNotice.value=(cause as Error).message
  }finally{
    if(scope.valid())presetSaving.value=false
  }
}
async function runPreview(){
  const id=cardId.value
  if(!id||previewLoading.value)return
  if(!previewMessage.value.trim()){previewError.value='先写一句对话，再预览会注入的内容。';return}
  const scope=capture()
  previewLoading.value=true;previewError.value='';preview.value=null
  try{
    const result=await contextPreview(id,previewMessage.value)
    if(scope.valid())preview.value=result
  }catch(cause){
    if(scope.valid())previewError.value=(cause as Error).message
  }finally{
    if(scope.valid())previewLoading.value=false
  }
}
async function openChat(){
  const id=cardId.value
  if(!id||opening.value)return
  const scope=capture()
  opening.value=true;error.value=''
  try{
    const result=await startCardChat(id)
    if(scope.valid())await router.push('/conversations/world/'+result.sessionId)
  }catch(cause){
    if(scope.valid())error.value=(cause as Error).message
  }finally{
    if(scope.valid())opening.value=false
  }
}
function enterWorld(){
  const target=card.value?.chatTarget
  if(target)void router.push('/worlds/'+target.worldId+'/play?line='+target.lineId)
}
async function generateGreetings(){
  const sid=cardId.value
  if(!sid||greetingsBusy.value)return
  const scope=capture()
  greetingsBusy.value=true;greetingsError.value=''
  try{
    const list=await cardGreetings(sid,3)
    if(scope.valid()){greetings.value=list;if(!list.length)greetingsError.value='这次没有生成新的开场白，再试一次。'}
  }catch(cause){
    if(scope.valid())greetingsError.value=(cause as Error).message
  }finally{
    if(scope.valid())greetingsBusy.value=false
  }
}
function useGreeting(text:string){
  form.firstMessage=text
  notice.value='已填入开场白，保存设定后生效。'
}
async function exportCard(){
  const id=cardId.value
  if(!id||exporting.value)return
  const scope=capture()
  exporting.value=true;exportError.value=''
  try{
    const blob=await exportCardBlob(id,'png')
    if(!scope.valid())return
    saveBlob(blob,safeFileName(card.value?.name||'character-card')+'.png')
    notice.value='角色卡已导出为 PNG，可以再次导入其他客户端。'
  }catch(cause){
    if(scope.valid())exportError.value=(cause as Error).message||'导出失败，请稍后重试。'
  }finally{
    if(scope.valid())exporting.value=false
  }
}
function sortMemories(items:Memory[]):Memory[]{
  return [...items].sort((a,b)=>(b.salience-a.salience)||String(b.updatedAt||'').localeCompare(String(a.updatedAt||'')))
}
async function refreshMemories(){
  const id=cardId.value
  if(!id)return
  const scope=capture()
  try{const items=await listMemories(id);if(scope.valid())memories.value=sortMemories(items)}catch(cause){if(scope.valid())memoryError.value=(cause as Error).message}
}
function newMemory(){
  memoryEditing.value='new'
  Object.assign(memoryForm,{kind:'fact',title:'',content:'',salience:50,enabled:true})
  memoryError.value='';deletingMemory.value='';extractNotice.value=''
}
function editMemory(entry:Memory){
  memoryEditing.value=entry.id
  Object.assign(memoryForm,{kind:entry.kind,title:entry.title,content:entry.content,salience:entry.salience,enabled:entry.enabled})
  memoryError.value='';deletingMemory.value='';extractNotice.value=''
}
function cancelMemory(){memoryEditing.value='';memoryError.value=''}
async function saveMemory(){
  const id=cardId.value
  if(!id||memorySaving.value)return
  if(!memoryForm.title.trim()){memoryError.value='请填写记忆标题。';return}
  if(!memoryForm.content.trim()){memoryError.value='请填写记忆内容。';return}
  const scope=capture(),target=memoryEditing.value
  memorySaving.value=true;memoryError.value=''
  const payload={kind:memoryForm.kind,title:memoryForm.title.trim(),content:memoryForm.content.trim(),salience:Number(memoryForm.salience)||50,enabled:memoryForm.enabled}
  try{
    if(target==='new')await createMemory(id,payload)
    else await updateMemory(id,target,payload)
    if(!scope.valid())return
    memoryEditing.value=''
    await refreshMemories()
    if(scope.valid())notice.value='记忆已保存，下一轮对话生效。'
  }catch(cause){
    if(scope.valid())memoryError.value=(cause as Error).message
  }finally{
    if(scope.valid())memorySaving.value=false
  }
}
async function toggleMemory(entry:Memory){
  const id=cardId.value
  if(!id||memoryBusy.value)return
  const scope=capture()
  memoryBusy.value=entry.id;memoryError.value=''
  try{
    const updated=await updateMemory(id,entry.id,{enabled:!entry.enabled})
    if(!scope.valid())return
    const index=memories.value.findIndex(item=>item.id===entry.id)
    if(index>=0)memories.value[index]={...memories.value[index],...updated}
  }catch(cause){
    if(scope.valid())memoryError.value=(cause as Error).message
  }finally{
    if(scope.valid())memoryBusy.value=''
  }
}
async function removeMemory(entry:Memory){
  const id=cardId.value
  if(!id||memoryBusy.value)return
  const scope=capture()
  memoryBusy.value=entry.id;memoryError.value=''
  try{
    await deleteMemory(id,entry.id)
    if(!scope.valid())return
    deletingMemory.value=''
    memories.value=memories.value.filter(item=>item.id!==entry.id)
    notice.value='记忆已删除。'
  }catch(cause){
    if(scope.valid())memoryError.value=(cause as Error).message
  }finally{
    if(scope.valid())memoryBusy.value=''
  }
}
async function runExtract(){
  const id=cardId.value
  if(!id||extracting.value)return
  const scope=capture()
  extracting.value=true;memoryError.value='';extractNotice.value=''
  try{
    const result=await extractMemories(id)
    if(!scope.valid())return
    memories.value=sortMemories(await listMemories(id))
    if(!scope.valid())return
    extractNotice.value=result.added.length?'新增 '+result.added.length+' 条记忆。':'暂无新记忆。'
  }catch(cause){
    if(scope.valid())memoryError.value=(cause as Error).message||'记忆整理失败，请稍后重试。'
  }finally{
    if(scope.valid())extracting.value=false
  }
}
async function toggleCompanion(){
  const id=cardId.value
  if(!id||companionBusy.value||!card.value)return
  const scope=capture(),next=!card.value.companion
  companionBusy.value=true;companionNotice.value=''
  try{
    const updated=await setCompanion(id,next)
    if(!scope.valid())return
    card.value={...card.value,...updated,companion:next} as CardOut
    companionNotice.value=next?'已设为长期伙伴，会置顶展示在角色卡库。':'已取消长期伙伴。'
  }catch(cause){
    if(scope.valid())companionNotice.value=(cause as Error).message
  }finally{
    if(scope.valid())companionBusy.value=false
  }
}
watch(cardId,id=>{if(id){revokeAvatar();void load()}})
onMounted(load)
onUnmounted(revokeAvatar)
</script>
<template>
  <div class="workspace-page card-detail-page">
    <PageHeader :title="card?.name||'角色卡详情'" eyebrow="角色卡" description="编辑角色设定、世界书、预设与长期记忆，并预览下一轮对话会注入什么。">
      <RouterLink class="inline-link" to="/characters"><ArrowLeft :size="17"/>角色卡库</RouterLink>
      <RouterLink class="inline-link" to="/characters/regex"><Replace :size="17"/>输出替换规则</RouterLink>
      <button :disabled="exporting||!card" @click="exportCard"><Download :size="17"/>{{exporting?'正在导出…':'导出角色卡'}}</button>
      <button v-if="card?.chatTarget" :disabled="opening||!card" @click="enterWorld"><MessageSquareText :size="17"/>进入互动世界</button>
      <button class="primary" :disabled="opening||!card" @click="openChat"><MessageSquareText :size="17"/>{{opening?'正在开聊…':'开聊'}}</button>
    </PageHeader>
    <p v-if="exportError" class="error" role="alert">{{exportError}}</p>
    <p v-if="error" class="error" role="alert">{{error}} <button v-if="!card" @click="load">重新加载</button></p>
    <p v-if="notice" class="product-hint" role="status">{{notice}}</p>
    <p v-if="loading" class="workspace-loading" role="status">正在加载角色卡…</p>
    <template v-else-if="card">
      <section class="workspace-card card-hero">
        <img v-if="avatarUrl" :src="avatarUrl" class="card-avatar" :alt="card.name+' 的头像'">
        <div v-else class="card-avatar card-avatar-placeholder" aria-hidden="true">{{card.name.slice(0,1)||'卡'}}</div>
        <div class="card-hero-copy">
          <h2>{{card.name}}</h2>
          <p>{{card.description||'还没有描述。'}}</p>
          <p v-if="card.tags.length" class="tag-row"><span v-for="tag in card.tags" :key="tag" class="badge">{{tag}}</span></p>
          <small>{{card.source==='png'?'PNG 角色卡':'JSON 角色卡'}} · {{card.hasAvatar?'已有头像':'暂无头像'}}</small>
          <div class="companion-line">
            <label class="check-line"><input type="checkbox" :checked="!!card.companion" :disabled="companionBusy" @change="toggleCompanion">{{card.companion?'长期伙伴':'设为长期伙伴'}}</label>
            <span v-if="card.companion" class="badge">长期伙伴</span>
            <small>{{companionBusy?'正在更新…':'设为长期伙伴后会置顶显示在角色卡库'}}</small>
          </div>
          <p v-if="companionNotice" class="product-hint" role="status">{{companionNotice}}</p>
        </div>
      </section>
      <section class="workspace-card">
        <div class="workspace-section-title"><h2>角色设定</h2><small>保存后立即用于下一轮对话</small></div>
        <form @submit.prevent="saveCard">
          <label>角色名称<input v-model="form.name" required maxlength="120" :disabled="saving"></label>
          <label>描述<textarea v-model="form.description" rows="2" maxlength="2000" :disabled="saving" placeholder="一句话介绍这个角色"></textarea></label>
          <div class="form-grid">
            <label>性格<textarea v-model="form.personality" rows="3" maxlength="4000" :disabled="saving"></textarea></label>
            <label>场景<textarea v-model="form.scenario" rows="3" maxlength="4000" :disabled="saving"></textarea></label>
          </div>
          <label>开场白<textarea v-model="form.firstMessage" rows="3" maxlength="8000" :disabled="saving"></textarea></label>
          <div class="greeting-candidates">
            <div class="greeting-head"><small>开场白候选</small><button type="button" :disabled="greetingsBusy" @click="generateGreetings"><Sparkles :size="15"/>{{greetingsBusy?'正在生成…':'生成候选'}}</button></div>
            <p v-if="greetingsError" class="error" role="alert">{{greetingsError}}</p>
            <ul v-if="greetings.length"><li v-for="(item,index) in greetings" :key="index"><span>{{item}}</span><button type="button" @click="useGreeting(item)">用作开场白</button></li></ul>
          </div>
          <label>系统提示<textarea v-model="form.systemPrompt" rows="3" maxlength="8000" :disabled="saving" placeholder="角色的行为约束，会拼在系统提示前部"></textarea></label>
          <label>历史后指令<textarea v-model="form.postHistoryInstructions" rows="3" maxlength="8000" :disabled="saving" placeholder="追加在对话历史之后的指令"></textarea></label>
          <label>标签<input v-model="form.tagsText" maxlength="300" :disabled="saving" placeholder="用逗号分隔，例如：奇幻，骑士"></label>
          <p v-if="saveError" class="error" role="alert">{{saveError}}</p>
          <div class="actions">
            <button class="primary" :disabled="saving"><Save :size="16"/>{{saving?'正在保存…':'保存设定'}}</button>
            <button type="button" :disabled="saving" @click="resetForm">撤销修改</button>
          </div>
        </form>
      </section>
      <section class="workspace-card">
        <div class="workspace-section-title">
          <h2><BookOpen :size="18"/>世界书</h2>
          <button :disabled="!!loreEditing" @click="newLore"><Plus :size="16"/>新增条目</button>
        </div>
        <p v-if="loreError" class="error" role="alert">{{loreError}}</p>
        <form v-if="loreEditing" class="lore-editor" @submit.prevent="saveLore">
          <h3>{{loreEditing==='new'?'新增世界书条目':'编辑世界书条目'}}</h3>
          <label>条目名称<input v-model="loreForm.name" maxlength="160" placeholder="例如：雨夜的约定"></label>
          <label>关键词<input v-model="loreForm.keys" placeholder="用逗号分隔；消息包含任一关键词时注入"></label>
          <label>正文<textarea v-model="loreForm.content" required rows="5" maxlength="8000"></textarea></label>
          <div class="form-grid">
            <label>顺序<input v-model.number="loreForm.order" type="number" min="0" step="10"></label>
            <label class="check-line"><input v-model="loreForm.enabled" type="checkbox">启用这个条目</label>
          </div>
          <div class="actions">
            <button class="primary" :disabled="loreSaving">{{loreSaving?'正在保存…':'保存条目'}}</button>
            <button type="button" :disabled="loreSaving" @click="cancelLore">取消</button>
          </div>
        </form>
        <p v-if="!lore.length&&!loreEditing" class="product-hint">还没有世界书条目。导入带 character_book 的 v2 角色卡会自动带入条目，也可以手动新增；关键词命中时正文会注入下一轮上下文。</p>
        <ul v-else-if="lore.length" class="lore-list">
          <li v-for="entry in lore" :key="entry.id" class="lore-item">
            <div class="row between">
              <strong>{{entry.name||'未命名条目'}}
                <span class="badge">{{entry.source==='import'?'导入':'手写'}}</span>
                <span v-if="!entry.enabled" class="badge">已停用</span>
              </strong>
              <span class="row">
                <button :disabled="loreBusy===entry.id" aria-label="编辑世界书条目" @click="editLore(entry)"><Pencil :size="15"/></button>
                <button :disabled="loreBusy===entry.id" @click="toggleLore(entry)">{{entry.enabled?'停用':'启用'}}</button>
                <template v-if="deletingLore===entry.id">
                  <button class="danger" :disabled="loreBusy===entry.id" @click="removeLore(entry)">确认删除</button>
                  <button @click="deletingLore=''">取消</button>
                </template>
                <button v-else class="danger" aria-label="删除世界书条目" @click="deletingLore=entry.id"><Trash2 :size="15"/></button>
              </span>
            </div>
            <p v-if="entry.keys.length" class="lore-keys">关键词：{{entry.keys.join('、')}}</p>
            <p class="lore-content">{{entry.content}}</p>
          </li>
        </ul>
      </section>
      <section class="workspace-card">
        <div class="workspace-section-title">
          <h2><Sparkles :size="18"/>预设</h2>
          <button :disabled="presetSaving" @click="showPresetForm=!showPresetForm">{{showPresetForm?'收起新建':'新建预设'}}</button>
        </div>
        <label>当前卡绑定的预设<select v-model="presetId" :disabled="presetBusy" @change="bindPreset"><option value="">不使用预设</option><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{preset.name}}{{preset.enabled?'':'（已停用）'}}</option></select></label>
        <p v-if="presetNotice" class="product-hint" role="status">{{presetNotice}}</p>
        <form v-if="showPresetForm" class="preset-editor" @submit.prevent="createNewPreset">
          <label>预设名称<input v-model="presetForm.name" required maxlength="80" placeholder="例如：细腻的环境描写"></label>
          <label>系统提示后缀<textarea v-model="presetForm.systemSuffix" rows="4" maxlength="4000" placeholder="追加到系统提示末尾，例如：多描写环境与心理活动。"></textarea></label>
          <div class="actions">
            <button class="primary" :disabled="presetSaving">{{presetSaving?'正在创建…':'创建并绑定'}}</button>
            <button type="button" @click="showPresetForm=false">取消</button>
          </div>
        </form>
      </section>
      <section class="workspace-card">
        <div class="workspace-section-title">
          <h2><UserRound :size="18"/>我是谁</h2>
          <small>聊天时注入你的身份；在聊天页顶部可随时切换</small>
        </div>
        <PersonaControl v-model="personaId"/>
        <p class="product-hint">人设在聊天开始时注入【你的对话对象】姓名与简介。这里只管理人设内容；具体使用哪一个人设由聊天页顶部选择。</p>
      </section>
      <section class="workspace-card">
        <div class="workspace-section-title">
          <h2><Brain :size="18"/>长期记忆</h2>
          <span class="row">
            <button :disabled="extracting||memorySaving" @click="runExtract">{{extracting?'正在整理…':'立即整理'}}</button>
            <button :disabled="!!memoryEditing" @click="newMemory"><Plus :size="16"/>新增记忆</button>
          </span>
        </div>
        <p class="product-hint">聊天时最多注入 6 条高权重记忆；「立即整理」会从最近的对话中抽取新记忆，需要先聊过几句。</p>
        <p v-if="memoryError" class="error" role="alert">{{memoryError}}</p>
        <p v-if="extractNotice" class="product-hint" role="status">{{extractNotice}}</p>
        <form v-if="memoryEditing" class="lore-editor memory-editor" @submit.prevent="saveMemory">
          <h3>{{memoryEditing==='new'?'新增长期记忆':'编辑长期记忆'}}</h3>
          <div class="form-grid">
            <label>类型<select v-model="memoryForm.kind" :disabled="memorySaving"><option v-for="item in memoryKinds" :key="item.value" :value="item.value">{{item.label}}</option></select></label>
            <label>权重（1–100）<input v-model.number="memoryForm.salience" type="number" min="1" max="100" step="1" :disabled="memorySaving"></label>
          </div>
          <label>标题<input v-model="memoryForm.title" required maxlength="120" :disabled="memorySaving" placeholder="例如：读者养猫"></label>
          <label>内容<textarea v-model="memoryForm.content" required rows="4" maxlength="2000" :disabled="memorySaving" placeholder="例如：读者养了一只叫团子的猫。"></textarea></label>
          <label class="check-line"><input v-model="memoryForm.enabled" type="checkbox" :disabled="memorySaving">启用这条记忆</label>
          <div class="actions">
            <button class="primary" :disabled="memorySaving">{{memorySaving?'正在保存…':'保存记忆'}}</button>
            <button type="button" :disabled="memorySaving" @click="cancelMemory">取消</button>
          </div>
        </form>
        <p v-if="!memories.length&&!memoryEditing" class="product-hint">还没有长期记忆。可以手工新增，或聊过几句后点「立即整理」让模型从对话里抽取。</p>
        <ul v-else-if="memories.length" class="lore-list memory-list">
          <li v-for="entry in memories" :key="entry.id" class="lore-item memory-item">
            <div class="row between">
              <strong>{{entry.title}}
                <span class="badge">{{kindLabel(entry.kind)}}</span>
                <span class="badge">{{entry.source==='auto'?'自动':'手记'}}</span>
                <span class="badge">权重 {{entry.salience}}</span>
                <span v-if="!entry.enabled" class="badge">已停用</span>
              </strong>
              <span class="row">
                <button :disabled="memoryBusy===entry.id" aria-label="编辑记忆" @click="editMemory(entry)"><Pencil :size="15"/></button>
                <button :disabled="memoryBusy===entry.id" @click="toggleMemory(entry)">{{entry.enabled?'停用':'启用'}}</button>
                <template v-if="deletingMemory===entry.id">
                  <button class="danger" :disabled="memoryBusy===entry.id" @click="removeMemory(entry)">确认删除</button>
                  <button @click="deletingMemory=''">取消</button>
                </template>
                <button v-else class="danger" aria-label="删除记忆" @click="deletingMemory=entry.id"><Trash2 :size="15"/></button>
              </span>
            </div>
            <p class="lore-content">{{entry.content}}</p>
          </li>
        </ul>
      </section>
      <section class="workspace-card">
        <div class="workspace-section-title">
          <h2><Eye :size="18"/>注入预览</h2>
          <button :disabled="previewLoading" @click="runPreview">{{previewLoading?'正在预览…':'预览'}}</button>
        </div>
        <label>试写一句对话<input v-model="previewMessage" maxlength="2000" placeholder="例如：外面下雨了，我们在屋里待一会儿吧"></label>
        <p v-if="previewError" class="error" role="alert">{{previewError}}</p>
        <div v-if="preview" class="preview-result">
          <p><strong>命中的世界书：</strong>{{preview.loreMatched.length?preview.loreMatched.map(item=>item.name).join('、'):'无'}}</p>
          <p><strong>预设：</strong>{{preview.preset?.name||'未绑定'}}</p>
          <p><strong>注入的人设：</strong>{{preview.persona?.name||'未选择'}}</p>
          <p><strong>系统提示后缀：</strong></p>
          <pre class="preview-suffix">{{preview.systemSuffix||'（空）'}}</pre>
          <p><strong>注入的长期记忆：</strong>{{preview.memories?.length?preview.memories.map(item=>item.title).join('、'):'无'}}</p>
          <pre v-if="preview.memorySection" class="preview-suffix">{{preview.memorySection}}</pre>
        </div>
      </section>
    </template>
  </div>
</template>
<style scoped>
.card-hero{display:flex;gap:22px;align-items:flex-start;margin-bottom:20px}
.card-avatar{width:96px;height:96px;border-radius:18px;object-fit:cover;flex:none;background:var(--wash)}
.card-avatar-placeholder{display:grid;place-items:center;font-family:var(--font-ui);font-size:34px;color:var(--accent);border:1px solid var(--edge)}
.card-hero-copy{min-width:0}
.card-hero-copy h2{margin:0 0 8px;font-size:24px}
.card-hero-copy p{color:var(--quiet);line-height:1.8;margin:0 0 10px}
.card-hero-copy small{color:var(--quiet);font-size:12px}
.tag-row{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px!important}
.card-detail-page>section{margin-bottom:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 18px}
.check-line{display:flex;align-items:center;gap:10px}
.check-line input{width:20px;min-height:20px;margin:0}
.lore-list{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.lore-item{border:1px solid var(--edge);border-radius:12px;padding:14px 16px;background:var(--canvas)}
.lore-item .row{gap:8px;flex-wrap:wrap}
.lore-item strong{display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap}
.lore-item button{font-size:12px;min-height:36px;padding:6px 10px}
.lore-keys{font-size:12px;color:var(--quiet);margin:8px 0 4px}
.lore-content{white-space:pre-wrap;font-size:14px;line-height:1.8;color:var(--ink);margin:0;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.lore-editor{border:1px solid var(--edge);border-radius:12px;padding:16px;background:var(--wash);margin:0 0 16px}
.lore-editor h3{margin:0 0 8px;font-size:16px}
.preset-editor{border-top:1px dashed var(--edge);margin-top:16px;padding-top:8px}
.preview-result{border:1px solid var(--edge);border-radius:12px;padding:14px 16px;background:var(--wash);margin-top:14px}
.preview-result p{margin:0 0 8px;line-height:1.8}
.companion-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px}
.companion-line small{color:var(--quiet);font-size:12px}
.memory-item .lore-content{-webkit-line-clamp:6}
.preview-suffix{white-space:pre-wrap;font-size:13px;line-height:1.8;max-height:220px;overflow:auto;background:var(--canvas);border:1px solid var(--edge);border-radius:10px;padding:10px;margin:0}
.greeting-candidates{margin:-6px 0 14px}
.greeting-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 8px}
.greeting-head small{color:var(--quiet)}
.greeting-candidates ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.greeting-candidates li{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;background:var(--wash);border:1px solid var(--edge);border-radius:10px;padding:10px 12px}
.greeting-candidates li span{white-space:pre-wrap;font-size:14px;line-height:1.6;color:var(--ink)}
.greeting-candidates li button{flex:none}
@media(max-width:767px){
  .card-hero{flex-direction:column;gap:14px}
  .card-avatar{width:72px;height:72px;font-size:26px}
  .form-grid{grid-template-columns:1fr}
}
</style>
