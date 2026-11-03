<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {Upload,Users,MessageCircle,Trash2} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {cardAvatarBlob,deleteCard,importCard,listCards,startCardChat,type CardOut} from './api'
const router=useRouter(),store=useApp(),capture=useRequestScope()
const cards=ref<CardOut[]>([]),loading=ref(true),listError=ref(''),importError=ref('')
const importing=ref(false),chatting=ref(''),deleting=ref(''),avatars=ref<Record<string,string>>({})
const fileInput=ref<HTMLInputElement>()
const empty=computed(()=>!loading.value&&!listError.value&&!cards.value.length)
let disposed=false,listEpoch=0,avatarEpoch=0
function description(card:CardOut){return card.description||card.personality||card.scenario||'暂无描述'}
function pickFile(){if(!importing.value)fileInput.value?.click()}
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
    const items=await listCards()
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
    store.message('已删除「'+card.name+'」，聊天记录仍保留在我的对话里。')
    await load()
  }catch(e){
    if(scope.valid())listError.value='删除失败：'+((e as Error).message||'请稍后重试。')
  }finally{
    if(!disposed)deleting.value=''
  }
}
onMounted(()=>{void load()})
onUnmounted(()=>{disposed=true;listEpoch++;avatarEpoch++;revokeAll()})
</script>
<template>
 <div class="workspace-page characters-page">
  <PageHeader title="角色卡库" eyebrow="独立角色卡" description="导入 SillyTavern 角色卡（PNG / JSON），选择一张就能和 TA 单独聊天。">
   <label class="workspace-file-button character-import" :aria-busy="importing?'true':'false'">
    <Upload :size="17"/>{{importing?'正在导入…':'导入角色卡'}}
    <input ref="fileInput" type="file" accept=".png,.json" :disabled="importing" @change="importFile">
   </label>
  </PageHeader>
  <p v-if="importError" class="error" role="alert">{{importError}}</p>
  <p v-if="listError" class="error" role="alert">{{listError}} <button type="button" @click="load">重新载入角色卡</button></p>
  <p v-if="loading" class="workspace-loading" role="status">正在读取角色卡…</p>
  <section v-else-if="empty" class="workspace-empty">
   <Users :size="40"/>
   <h2>还没有角色卡</h2>
   <p>导入一张 SillyTavern 角色卡开始聊天。支持内嵌角色数据的 PNG，以及 v1 / v2 / v3 格式的 JSON 卡。</p>
   <button class="primary" :disabled="importing" @click="pickFile">{{importing?'正在导入…':'选择 PNG 或 JSON 文件'}}</button>
  </section>
  <div v-else class="character-grid">
   <article v-for="card in cards" :key="card.id" class="workspace-card character-card" :data-card-id="card.id">
    <div class="character-card-head">
     <span class="character-avatar" data-character-avatar>
      <img v-if="avatars[card.id]" :src="avatars[card.id]" :alt="card.name+'头像'" loading="lazy">
      <span v-else aria-hidden="true">{{Array.from(card.name.trim())[0]||'·'}}</span>
     </span>
     <div class="character-card-title"><h2>{{card.name}}</h2><small>{{card.creator||'角色卡'}} · {{card.source==='png'?'PNG':'JSON'}}</small></div>
    </div>
    <p class="character-description">{{description(card)}}</p>
    <div v-if="card.tags?.length" class="character-tags"><span v-for="tag in card.tags.slice(0,6)" :key="tag" class="badge">{{tag}}</span></div>
    <div class="workspace-card-actions">
     <button class="primary" :disabled="!!chatting||!!deleting" @click="openChat(card)"><MessageCircle :size="16"/>{{chatting===card.id?'正在打开…':'开聊'}}</button>
     <button class="danger" :disabled="!!chatting||!!deleting" @click="remove(card)"><Trash2 :size="15"/>{{deleting===card.id?'正在删除…':'删除'}}</button>
    </div>
   </article>
  </div>
 </div>
</template>
<style scoped>
.characters-page{width:100%;min-width:0}.character-import{display:inline-flex;align-items:center;gap:8px;font-size:13px;padding:11px 16px;min-height:44px}.character-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}.character-card{display:flex;flex-direction:column;gap:14px}.character-card-head{display:flex;gap:14px;align-items:center;min-width:0}.character-avatar{width:72px;height:72px;flex:none;display:grid;place-items:center;border:1px solid var(--edge);border-radius:50%;overflow:hidden;background:var(--wash);color:var(--accent);font-family:var(--font-ui);font-size:26px;font-weight:600;line-height:1}.character-avatar img{display:block;width:100%;height:100%;object-fit:cover}.character-card-title{min-width:0}.character-card-title h2{font-family:var(--font-ui);font-size:18px;margin:0 0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.character-card-title small{font-size:12px;color:var(--quiet)}.character-description{font-size:13px;line-height:1.8;color:var(--quiet);margin:0;min-height:46px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.character-tags{display:flex;flex-wrap:wrap;gap:6px}.character-tags .badge{font-size:11px}.character-card .workspace-card-actions{margin-top:auto}@media(max-width:767px){.character-grid{grid-template-columns:1fr}}
</style>
