<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRouter} from 'vue-router'
import {PackagePlus,Search,Sparkles,Upload,Users,MessageCircle,Pencil,Replace,Trash2} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import ReadingPreferencePanel from '../components/ReadingPreferencePanel.vue'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {SAMPLE_CARDS,sampleCardFile} from './sample-cards'
import {cardAvatarBlob,deleteCard,draftCard,importCard,listCards,startCardChat,type CardDraft,type CardOut} from './api'

const router=useRouter(),store=useApp(),capture=useRequestScope()
const cards=ref<CardOut[]>([]),loading=ref(true),listError=ref(''),importError=ref('')
const importing=ref(false),chatting=ref(''),deleting=ref(''),avatars=ref<Record<string,string>>({})
const fileInput=ref<HTMLInputElement>(),batchInput=ref<HTMLInputElement>()
const query=ref(''),sort=ref<'recent'|'name'>('recent'),showReading=ref(false)
const importingBatch=ref(false),importingSamples=ref(false),batchNotice=ref('')
const showDraft=ref(false),draftBrief=ref(''),draftBusy=ref(false),draftError=ref(''),draftReady=ref(false)
const draftForm=ref<CardDraft&{tagsText:string}>({name:'',description:'',personality:'',scenario:'',firstMessage:'',tags:[],tagsText:''})
const empty=computed(()=>!loading.value&&!listError.value&&!cards.value.length)
type GridRow={key:string;heading?:string;hint?:string;card?:CardOut}
function sortCards(items:CardOut[]){
  const copy=[...items]
  if(sort.value==='name')return copy.sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'))
  return copy.sort((a,b)=>(Date.parse(b.updatedAt||b.createdAt||'')||0)-(Date.parse(a.updatedAt||a.createdAt||'')||0))
}
const companionCards=computed(()=>sortCards(cards.value.filter(card=>card.companion)))
const regularCards=computed(()=>sortCards(cards.value.filter(card=>!card.companion)))
const gridRows=computed<GridRow[]>(()=>{
  const rows:GridRow[]=[]
  if(companionCards.value.length){
    rows.push({key:'heading-companion',heading:'长期伙伴',hint:'置顶展示，常聊的角色'})
    for(const card of companionCards.value)rows.push({key:'card-'+card.id,card})
  }
  if(companionCards.value.length&&regularCards.value.length)rows.push({key:'heading-all',heading:'全部角色卡'})
  for(const card of regularCards.value)rows.push({key:'card-'+card.id,card})
  return rows
})
let disposed=false,listEpoch=0,avatarEpoch=0,searchTimer:ReturnType<typeof setTimeout>|undefined
function description(card:CardOut){return card.description||card.personality||card.scenario||'暂无描述'}
function updatedLabel(card:CardOut){const time=Date.parse(card.updatedAt||card.createdAt||'');return Number.isFinite(time)?'最近更新 '+new Date(time).toLocaleDateString('zh-CN'):''}
function pickFile(){if(!importing.value&&!importingBatch.value)fileInput.value?.click()}
function pickBatch(){if(!importing.value&&!importingBatch.value)batchInput.value?.click()}
function revokeAll(){for(const url of Object.values(avatars.value))URL.revokeObjectURL(url);avatars.value={}}
function revokeExcept(ids:Set<string>){for(const [id,url] of Object.entries(avatars.value))if(!ids.has(id)){URL.revokeObjectURL(url);delete avatars.value[id]}}
async function loadAvatars(items:CardOut[]){
  const ids=new Set(items.map(card=>card.id)),revision=++avatarEpoch
  revokeExcept(ids)
  for(const card of items){
    if(!card.hasAvatar||avatars.value[card.id])continue
    try{
      const blob=await cardAvatarBlob(card.id)
      if(disposed||revision!==avatarEpoch||!ids.has(card.id))continue
      if(!blob){continue}
      const url=URL.createObjectURL(blob)
      if(disposed||revision!==avatarEpoch||!ids.has(card.id)){URL.revokeObjectURL(url);continue}
      avatars.value={...avatars.value,[card.id]:url}
    }catch{/* Placeholder initials stay visible when an avatar cannot be read. */}
  }
}
async function load(){
  const scope=capture(),epoch=++listEpoch
  if(!cards.value.length)loading.value=true
  listError.value=''
  try{
    const items=await listCards(query.value.trim())
    if(!scope.valid()||epoch!==listEpoch)return
    cards.value=items
    void loadAvatars(items)
  }catch(e){
    if(scope.valid()&&epoch===listEpoch)listError.value=(e as Error).message||'角色卡暂时无法读取，请稍后重试。'
  }finally{
    if(scope.valid()&&epoch===listEpoch)loading.value=false
  }
}
async function importFile(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0]
  input.value=''
  if(!file||importing.value)return
  const scope=capture()
  importing.value=true
  importError.value=''
  try{
    const result=await importCard(file)
    if(!scope.valid())return
    store.message('已导入「'+(result.card?.name||file.name)+'」，可以开聊了。')
    await load()
  }catch(e){
    if(scope.valid())importError.value=(e as Error).message||'导入失败，请确认这是 SillyTavern 角色卡后重试。'
  }finally{
    if(!disposed)importing.value=false
  }
}
async function importBatch(event:Event){
  const input=event.target as HTMLInputElement,files=[...(input.files||[])]
  input.value=''
  if(!files.length||importingBatch.value||importing.value)return
  const scope=capture()
  importingBatch.value=true;batchNotice.value='';importError.value=''
  let imported=0,failed=0
  for(const file of files){
    try{
      await importCard(file)
      imported++
      if(scope.valid()&&!disposed)batchNotice.value='正在批量导入… '+imported+'/'+files.length
    }catch(e){
      failed++
      if(scope.valid()&&!disposed)importError.value=(e as Error).message
    }
    if(!scope.valid()||disposed)break
  }
  if(!disposed&&scope.valid()){
    batchNotice.value='批量导入完成：成功 '+imported+' 张'+(failed?'，失败 '+failed+' 张。':'。')
    if(imported)await load()
  }
  if(!disposed)importingBatch.value=false
}
function openDraft(){
  showDraft.value=true;draftReady.value=false;draftError.value=''
}
function closeDraft(){
  if(draftBusy.value)return
  showDraft.value=false;draftReady.value=false;draftError.value=''
}
async function generateDraft(){
  const brief=draftBrief.value.trim()
  if(!brief||draftBusy.value)return
  const scope=capture()
  draftBusy.value=true;draftError.value=''
  try{
    const draft=await draftCard(brief)
    if(!scope.valid())return
    draftForm.value={...draft,tags:Array.isArray(draft.tags)?draft.tags:[],tagsText:Array.isArray(draft.tags)?draft.tags.join('，'):''}
    draftReady.value=true
  }catch(e){
    if(scope.valid())draftError.value=(e as Error).message||'AI 起草失败，请稍后重试。'
  }finally{
    if(scope.valid())draftBusy.value=false
  }
}
async function importDraft(){
  if(draftBusy.value||!draftForm.value.name.trim())return
  const scope=capture(),form=draftForm.value
  draftBusy.value=true;draftError.value=''
  const payload={spec:'chara_card_v2',spec_version:'2.0',data:{name:form.name.trim(),description:form.description,personality:form.personality,scenario:form.scenario,first_mes:form.firstMessage,mes_example:'',creator_notes:'由 AI 起草，导入后可以继续编辑。',system_prompt:'',post_history_instructions:'',tags:form.tagsText.split(/[,，、\n]/).map(item=>item.trim()).filter(Boolean),creator:'酒馆 AI 助手',character_version:'1.0',alternate_greetings:[]}}
  try{
    const file=new File([JSON.stringify(payload)],form.name.trim()+'.json',{type:'application/json'})
    const result=await importCard(file)
    if(!scope.valid())return
    store.message('已导入「'+(result.card?.name||form.name.trim())+'」，可以继续编辑或开聊。')
    showDraft.value=false;draftReady.value=false;draftBrief.value=''
    await load()
  }catch(e){
    if(scope.valid())draftError.value=(e as Error).message||'导入失败，请稍后重试。'
  }finally{
    if(scope.valid())draftBusy.value=false
  }
}
async function importSamples(){
  if(importingSamples.value||importingBatch.value||importing.value)return
  const scope=capture()
  importingSamples.value=true;batchNotice.value='';importError.value=''
  let imported=0,skipped=0,failed=0
  for(const card of SAMPLE_CARDS){
    if(!scope.valid()||disposed)break
    const exists=cards.value.some(item=>item.name.trim()===card.name)
    if(exists){skipped++;continue}
    try{
      await importCard(sampleCardFile(card))
      imported++
    }catch(e){
      failed++
      if(scope.valid()&&!disposed)importError.value=(e as Error).message
    }
  }
  if(!disposed&&scope.valid()){
    batchNotice.value='示例卡包：导入 '+imported+' 张，跳过 '+skipped+' 张同名卡'+(failed?'，失败 '+failed+' 张。':'。')
    if(imported)await load()
  }
  if(!disposed)importingSamples.value=false
}
function openDetail(card:CardOut){if(!chatting.value&&!deleting.value)void router.push('/characters/'+card.id)}
async function openChat(card:CardOut){
  if(chatting.value||deleting.value)return
  const scope=capture()
  chatting.value=card.id
  listError.value=''
  try{
    const result=await startCardChat(card.id)
    if(!scope.valid())return
    await router.push('/conversations/world/'+result.sessionId)
  }catch(e){
    if(scope.valid())listError.value='开聊失败：'+((e as Error).message||'请稍后重试。')
  }finally{
    if(!disposed)chatting.value=''
  }
}
async function remove(card:CardOut){
  if(chatting.value||deleting.value)return
  const scope=capture()
  deleting.value=card.id
  listError.value=''
  try{
    await deleteCard(card.id)
    if(!scope.valid())return
    store.message('已删除「'+card.name+'」，聊天记录仍保留。')
    await load()
  }catch(e){
    if(scope.valid())listError.value='删除失败：'+((e as Error).message||'请稍后重试。')
  }finally{
    if(!disposed)deleting.value=''
  }
}
watch(query,()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{void load()},300)})
onMounted(()=>{void load()})
onUnmounted(()=>{disposed=true;listEpoch++;avatarEpoch++;clearTimeout(searchTimer);revokeAll()})
</script>
<template>
 <div class="workspace-page characters-page">
  <PageHeader title="角色卡库" eyebrow="独立角色卡" description="导入 SillyTavern 角色卡（PNG / JSON），选一张单独聊天。">
   <label class="workspace-file-button character-import" :aria-busy="importing?'true':'false'">
    <Upload :size="17"/>{{importing?'正在导入…':'导入角色卡'}}
    <input ref="fileInput" type="file" accept=".png,.json" :disabled="importing" @change="importFile">
   </label>
   <button type="button" @click="router.push('/characters/regex')"><Replace :size="17"/>输出替换规则</button>
  </PageHeader>
  <section class="character-toolbar" aria-label="角色卡库工具">
   <label class="character-search"><Search :size="16"/><input v-model="query" aria-label="搜索角色卡" type="search" maxlength="80" placeholder="搜索名字、描述或标签"></label>
   <label class="character-sort">排序<select v-model="sort" aria-label="角色卡排序"><option value="recent">最近更新</option><option value="name">名称</option></select></label>
   <label class="workspace-file-button character-batch" :aria-busy="importingBatch?'true':'false'"><Upload :size="16"/>{{importingBatch?'正在批量导入…':'批量导入'}}<input ref="batchInput" type="file" accept=".png,.json" multiple :disabled="importingBatch||importing" @change="importBatch"></label>
   <button type="button" :disabled="importingSamples||importingBatch||importing" @click="importSamples"><PackagePlus :size="16"/>{{importingSamples?'正在导入示例…':'导入示例卡包'}}</button>
   <button type="button" @click="showDraft?closeDraft():openDraft()"><Sparkles :size="16"/>AI 写卡</button>
   <button type="button" @click="showReading=!showReading">{{showReading?'收起阅读感设置':'正文阅读感'}}</button>
  </section>
  <ReadingPreferencePanel v-if="showReading"/>
  <section v-if="showDraft" class="workspace-card character-draft" aria-label="AI 写卡">
   <div class="workspace-section-title"><h2>AI 写卡</h2><small>描述想要的角色，生成草稿后可以改，确认后才导入。</small></div>
   <label>角色简述<textarea v-model="draftBrief" rows="3" maxlength="2000" :disabled="draftBusy" aria-label="AI 角色简述" placeholder="例如：雨夜里守着一家旧书店的青年，说话温柔，记性不好。"></textarea></label>
   <div class="actions"><button type="button" class="primary" :disabled="draftBusy||!draftBrief.trim()" @click="generateDraft"><Sparkles :size="16"/>{{draftBusy?'正在生成…':'生成草稿'}}</button><button type="button" :disabled="draftBusy" @click="closeDraft">取消</button></div>
   <p v-if="draftError" class="error" role="alert">{{draftError}}</p>
   <form v-if="draftReady" class="character-draft-form" @submit.prevent="importDraft">
    <label>角色名<input v-model="draftForm.name" maxlength="160" aria-label="草稿角色名" :disabled="draftBusy"></label>
    <label>描述<textarea v-model="draftForm.description" rows="2" maxlength="2000" aria-label="草稿描述" :disabled="draftBusy"></textarea></label>
    <div class="form-grid">
     <label>性格<textarea v-model="draftForm.personality" rows="3" maxlength="4000" aria-label="草稿性格" :disabled="draftBusy"></textarea></label>
     <label>场景<textarea v-model="draftForm.scenario" rows="3" maxlength="4000" aria-label="草稿场景" :disabled="draftBusy"></textarea></label>
    </div>
    <label>开场白<textarea v-model="draftForm.firstMessage" rows="3" maxlength="8000" aria-label="草稿开场白" :disabled="draftBusy"></textarea></label>
    <label>标签<input v-model="draftForm.tagsText" maxlength="300" aria-label="草稿标签" :disabled="draftBusy" placeholder="用逗号分隔"></label>
    <div class="actions"><button class="primary" :disabled="draftBusy||!draftForm.name.trim()">{{draftBusy?'正在导入…':'导入这张卡'}}</button></div>
   </form>
  </section>
  <p v-if="batchNotice" class="product-hint" role="status">{{batchNotice}}</p>
  <p v-if="importError" class="error" role="alert">{{importError}}</p>
  <p v-if="listError" class="error" role="alert">{{listError}} <button type="button" @click="load">重新载入角色卡</button></p>
  <p v-if="loading" class="workspace-loading" role="status">正在读取角色卡…</p>
  <section v-else-if="empty&&!query.trim()" class="workspace-empty">
   <Users :size="40"/>
   <h2>还没有角色卡</h2>
   <p>导入一张 SillyTavern 角色卡开始聊天。支持内嵌角色数据的 PNG，以及 v1 / v2 / v3 格式的 JSON 卡；也可以先导入内置示例卡包。</p>
   <button class="primary" :disabled="importing" @click="pickFile">{{importing?'正在导入…':'选择 PNG 或 JSON 文件'}}</button>
  </section>
  <section v-else-if="empty" class="workspace-empty">
   <Search :size="36"/>
   <h2>没有匹配的角色卡</h2>
   <p>换个关键词试试，或清空搜索查看全部角色卡。</p>
   <button type="button" @click="query=''">清空搜索</button>
  </section>
  <template v-else>
   <div class="character-grid">
   <template v-for="row in gridRows" :key="row.key">
    <div v-if="row.heading" class="grid-heading">
     <h2>{{row.heading}}</h2><small v-if="row.hint">{{row.hint}}</small>
    </div>
    <article v-else-if="row.card" :class="['workspace-card','character-card',{companion:row.card.companion}]" :data-card-id="row.card.id">
     <div class="character-card-head">
      <span class="character-avatar" data-character-avatar>
       <img v-if="avatars[row.card.id]" :src="avatars[row.card.id]" :alt="row.card.name+'头像'" loading="lazy">
       <span v-else aria-hidden="true">{{Array.from(row.card.name.trim())[0]||'·'}}</span>
      </span>
      <div class="character-card-title"><h2>{{row.card.name}}</h2><small>{{row.card.creator||'角色卡'}} · {{row.card.source==='png'?'PNG':'JSON'}} <span v-if="row.card.companion" class="badge companion-badge">长期伙伴</span></small></div>
     </div>
     <p class="character-description">{{description(row.card)}}</p>
     <div v-if="row.card.tags?.length" class="character-tags"><span v-for="tag in row.card.tags.slice(0,6)" :key="tag" class="badge">{{tag}}</span></div>
     <small class="character-updated">{{updatedLabel(row.card)}}</small>
     <div class="workspace-card-actions">
      <button class="primary" :disabled="!!chatting||!!deleting" @click="openChat(row.card)"><MessageCircle :size="16"/>{{chatting===row.card.id?'正在打开…':'开聊'}}</button>
      <button :disabled="!!chatting||!!deleting" @click="openDetail(row.card)"><Pencil :size="15"/>编辑</button>
      <button class="danger" :disabled="!!chatting||!!deleting" @click="remove(row.card)"><Trash2 :size="15"/>{{deleting===row.card.id?'正在删除…':'删除'}}</button>
     </div>
    </article>
   </template>
   </div>
  </template>
 </div>
</template>
<style scoped>
.characters-page{width:100%;min-width:0}.character-import{display:inline-flex;align-items:center;gap:8px;font-size:13px;padding:11px 16px;min-height:44px}.character-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px}.character-toolbar button,.character-toolbar .workspace-file-button{display:inline-flex;align-items:center;gap:6px;font-size:13px;padding:8px 12px;min-height:40px}.character-search{display:flex;align-items:center;gap:8px;flex:1;min-width:220px;padding:0 12px;border:1px solid var(--edge);border-radius:10px;background:var(--paper)}.character-search input{border:0;background:transparent;min-height:40px;width:100%;padding:0}.character-sort{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--quiet);margin:0}.character-sort select{min-height:40px;width:auto}.character-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}.character-card{display:flex;flex-direction:column;gap:14px}.character-card-head{display:flex;gap:14px;align-items:center;min-width:0}.character-avatar{width:72px;height:72px;flex:none;display:grid;place-items:center;border:1px solid var(--edge);border-radius:50%;overflow:hidden;background:var(--wash);color:var(--accent);font-family:var(--font-ui);font-size:26px;font-weight:600;line-height:1}.character-avatar img{display:block;width:100%;height:100%;object-fit:cover}.character-card-title{min-width:0}.character-card-title h2{font-family:var(--font-ui);font-size:18px;margin:0 0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.character-card-title small{font-size:12px;color:var(--quiet)}.character-description{font-size:13px;line-height:1.8;color:var(--quiet);margin:0;min-height:46px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.character-tags{display:flex;flex-wrap:wrap;gap:6px}.character-tags .badge{font-size:11px}.character-updated{color:var(--quiet);font-size:11px}.character-card .workspace-card-actions{margin-top:auto}.grid-heading{grid-column:1/-1;display:flex;align-items:baseline;gap:10px;margin:6px 0 -2px}.grid-heading h2{font-family:var(--font-ui);font-size:18px;margin:0;display:inline-flex;align-items:center;gap:8px}.grid-heading small{color:var(--quiet);font-size:12px}.character-card.companion{border-color:var(--accent)}.companion-badge{margin-left:6px;background:var(--accent);color:var(--paper);border-color:var(--accent)}.character-draft{display:grid;gap:14px}.character-draft .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.character-draft-form{display:grid;gap:14px;border-top:1px solid var(--edge);padding-top:14px}@media(max-width:767px){.character-grid{grid-template-columns:1fr}.character-sort{flex:1}.character-sort select{flex:1}}
</style>
