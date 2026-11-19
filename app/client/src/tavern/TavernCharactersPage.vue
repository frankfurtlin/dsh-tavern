<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {Check,HelpCircle,History,ListChecks,MessagesSquare,Search,Upload,X} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {importCharacter,listCharacters,characterAvatarBlob,listCharacterChats,createCharacterChat,createGroupChat,type TavernCharacter} from './api'
import LoadState from './LoadState.vue'
import OnboardingGuide from './OnboardingGuide.vue'
import {dismissHint,guideSeen,hintDismissed} from './guide'

const route=useRoute(),router=useRouter()
const store=useApp()
const scope=useRequestScope()
const cards=ref<TavernCharacter[]>([])
const loading=ref(true)
const error=ref('')
const query=ref('')
const searching=ref(false)
const importing=ref(false)
const opening=ref('')
const importError=ref('')
const selected=ref<string[]>([])
const multiselect=ref(false)
const grouping=ref(false)
const groupError=ref('')
const avatars=ref<Record<string,string>>({})
const hintVisible=ref(false)
const guideOpen=ref(false)
let epoch=0
let debounce:ReturnType<typeof setTimeout>|undefined

async function loadAvatars(list:TavernCharacter[]){
  for(const card of list){
    if(!card.hasAvatar||avatars.value[card.id])continue
    try{
      const blob=await characterAvatarBlob(card.id)
      if(blob&&scope().valid())avatars.value[card.id]=URL.createObjectURL(blob)
    }catch{/* 头像失败不影响卡片展示 */}
  }
}

async function load(){
  const ticket=++epoch
  if(!cards.value.length)loading.value=true
  else searching.value=true
  error.value=''
  try{
    const list=await listCharacters(query.value.trim())
    if(ticket!==epoch||!scope().valid())return
    cards.value=list
    selected.value=selected.value.filter(id=>list.some(card=>card.id===id))
    void loadAvatars(list)
  }catch(e){
    if(ticket===epoch&&scope().valid())error.value=(e as Error).message||'角色卡加载失败，请稍后重试。'
  }finally{
    if(ticket===epoch)loading.value=false;searching.value=false
  }
}

watch(query,()=>{clearTimeout(debounce);debounce=setTimeout(()=>void load(),260)})
watch(()=>store.user?.id,()=>void load())

async function onImport(event:Event){
  const input=event.target as HTMLInputElement
  const file=input.files?.[0]
  if(!file)return
  importing.value=true;importError.value=''
  try{
    const card=await importCharacter(file)
    await load()
    store.message('已导入角色卡「'+card.name+'」')
  }catch(e){
    importError.value=(e as Error).message||'导入失败，请确认文件是 SillyTavern 角色卡。'
  }finally{
    importing.value=false
    input.value=''
  }
}

function initial(card:TavernCharacter){return (card.name||'?').slice(0,1)}
function openDetail(card:TavernCharacter){void router.push('/characters/'+encodeURIComponent(card.id))}
async function openChat(card:TavernCharacter){
  if(opening.value)return
  opening.value=card.id
  try{
    const chats=await listCharacterChats(card.id)
    const chat=chats[0]??await createCharacterChat(card.id)
    await router.push('/chats/'+chat.id)
  }catch(e){
    store.message((e as Error).message||'打开聊天失败，请稍后重试。')
    openDetail(card)
  }finally{opening.value=''}
}

const empty=computed(()=>!loading.value&&!error.value&&!cards.value.length)
const selectedCards=computed(()=>selected.value.map(id=>cards.value.find(card=>card.id===id)).filter((card):card is TavernCharacter=>!!card))
const canGroup=computed(()=>selected.value.length>=2)

function toggleMultiselect(){
  multiselect.value=!multiselect.value
  if(!multiselect.value)selected.value=[]
}

function descPreview(card:TavernCharacter){
  const raw=(card.description||'还没有简介').replace(/\s+/g,' ').trim()
  if(raw.length<=68)return raw
  const head=raw.slice(0,66)
  const marks='。！？!?；;，,'
  for(let i=head.length-1;i>=Math.max(0,head.length-26);i--){
    if(marks.includes(head[i]))return head.slice(0,i+1)
  }
  return head.replace(/[。！？!?；;，,、]+$/,'')+'…'
}

function toggleSelect(card:TavernCharacter){
  const index=selected.value.indexOf(card.id)
  if(index>=0){selected.value=selected.value.filter(id=>id!==card.id);return}
  if(selected.value.length>=5){store.message('群聊最多选择 5 张角色卡。');return}
  selected.value=[...selected.value,card.id]
}

function clearSelection(){selected.value=[]}

async function startGroup(){
  if(!canGroup.value||grouping.value)return
  grouping.value=true;groupError.value=''
  try{
    const chat=await createGroupChat(selected.value)
    selected.value=[]
    await router.push('/chats/'+encodeURIComponent(chat.id))
  }catch(e){
    groupError.value=(e as Error).message||'发起群聊失败，请稍后重试。'
  }finally{grouping.value=false}
}
function closeHint(){hintVisible.value=false;dismissHint()}
function closeGuide(){
  guideOpen.value=false
  if(route.query.guide!==undefined)void router.replace({path:'/',query:{}})
}
onMounted(()=>{
  hintVisible.value=!hintDismissed()
  guideOpen.value=route.query.guide==='1'||!guideSeen()
  void load()
})
onUnmounted(()=>{clearTimeout(debounce);for(const url of Object.values(avatars.value))URL.revokeObjectURL(url)})
</script>

<template>
 <section class="tavern-home" aria-label="角色卡">
  <div class="tavern-toolbar">
   <label class="tavern-search">
    <Search :size="17" aria-hidden="true"/>
    <input v-model="query" type="search" placeholder="搜索角色名或简介" aria-label="搜索角色卡" autocomplete="off">
    <span v-if="searching" class="tavern-searching" aria-hidden="true">…</span>
   </label>
   <button type="button" class="tavern-history" :class="{on:multiselect}" :aria-pressed="multiselect" aria-label="多选" @click="toggleMultiselect"><ListChecks :size="17" aria-hidden="true"/><span>{{multiselect?'完成':'多选'}}</span></button>
   <button type="button" class="tavern-history" aria-label="聊天历史" @click="router.push('/chats')"><History :size="17" aria-hidden="true"/><span>聊天历史</span></button>
   <label class="tavern-import" :class="{busy:importing}">
    <Upload :size="17" aria-hidden="true"/>
    <span>{{importing?'正在导入…':'导入卡'}}</span>
    <input type="file" accept=".png,.json" :disabled="importing" aria-label="导入角色卡文件" @change="onImport">
   </label>
  </div>

  <div v-if="hintVisible" class="tavern-hint" role="note">
   <span class="tavern-hint-text">点卡开聊 · 右上「详情」可编辑</span>
   <button type="button" class="tavern-hint-help" aria-label="使用帮助" title="使用帮助" @click="router.push('/settings/help')"><HelpCircle :size="18"/></button>
   <button type="button" class="tavern-hint-close" aria-label="关闭提示" title="关闭提示" @click="closeHint"><X :size="18"/></button>
  </div>

  <p v-if="groupError" class="error" role="alert">{{groupError}}<button class="inline-link" type="button" @click="groupError=''">知道了</button></p>

  <div v-if="multiselect&&selected.length" class="tavern-select-bar" role="status">
   <span class="tavern-select-count">已选 {{selected.length}} 张</span>
   <span class="tavern-select-names">{{selectedCards.map(card=>card.name).join('、')}}</span>
   <button type="button" class="tavern-select-clear" @click="clearSelection">清空</button>
   <button type="button" class="tavern-select-group" :disabled="!canGroup||grouping" @click="startGroup">
    <MessagesSquare :size="16" aria-hidden="true"/>{{grouping?'正在创建…':'发起群聊（'+selected.length+'）'}}
   </button>
  </div>

  <p v-if="importError" class="error" role="alert">{{importError}}</p>
  <LoadState v-if="loading||error" :loading="loading" :error="error" loading-text="正在打开角色卡…" @retry="load"/>

  <LoadState v-else-if="empty" empty
   :empty-title="query.trim()?'没有匹配的角色卡':'还没有角色卡'"
   :empty-text="query.trim()?'换个关键词试试，或清空搜索看看全部角色卡。':'导入一张 SillyTavern 角色卡（PNG 或 JSON），就能开始聊天。'">
   <template #action>
    <button v-if="query.trim()" type="button" class="tavern-empty-action" @click="query=''">清空搜索</button>
    <label v-else class="tavern-empty-import">
     <Upload :size="18" aria-hidden="true"/><span>导入第一张角色卡</span>
     <input type="file" accept=".png,.json" :disabled="importing" aria-label="导入第一张角色卡" @change="onImport">
    </label>
   </template>
  </LoadState>

  <div v-else class="tavern-card-grid">
   <div v-for="card in cards" :key="card.id" class="tavern-card" :class="{selected:multiselect&&selected.includes(card.id)}">
    <button v-if="multiselect" type="button" class="tavern-select" :class="{on:selected.includes(card.id)}" :aria-pressed="selected.includes(card.id)" :aria-label="'选择 '+card.name+' 加入群聊'" @click="toggleSelect(card)"><Check :size="18"/></button>
    <button type="button" class="tavern-card-open" :disabled="opening===card.id" :aria-label="'和 '+card.name+' 聊天'" @click="openChat(card)">
     <span class="tavern-avatar" aria-hidden="true">
      <img v-if="avatars[card.id]" :src="avatars[card.id]" alt="">
      <span v-else>{{initial(card)}}</span>
     </span>
     <span class="tavern-card-copy">
      <span class="tavern-card-name-row">
       <span class="tavern-card-name">{{card.name}}</span>
       <span v-if="card.builtin" class="tavern-card-preset">预设</span>
      </span>
      <span class="tavern-card-desc">{{descPreview(card)}}</span>
      <span class="tavern-card-meta"><template v-if="card.lastChatAt">最近聊天 {{new Date(card.lastChatAt).toLocaleDateString('zh-CN')}}</template><template v-else>还没聊过</template><template v-if="card.tags.length"> · {{card.tags.slice(0,3).join(' · ')}}</template></span>
     </span>
    </button>
    <button type="button" class="tavern-card-detail" :aria-label="'查看 '+card.name+' 详情'" @click="openDetail(card)">详情</button>
   </div>
  </div>
  <OnboardingGuide :open="guideOpen" @close="closeGuide"/>
 </section>
</template>
<style scoped>
.tavern-home{width:100%;max-width:1180px;margin:0 auto;padding:24px clamp(16px,3vw,28px) 64px}
.tavern-home :focus-visible{outline:2px solid var(--accent-ink);outline-offset:2px}
.tavern-toolbar{display:flex;gap:10px;align-items:center;margin-bottom:20px}
.tavern-search{flex:1;display:flex;align-items:center;gap:8px;padding:0 12px;min-height:46px;border:1px solid var(--edge);border-radius:var(--r-control);background:var(--canvas);color:var(--quiet)}
.tavern-search input{flex:1;min-width:0;border:0;background:transparent;min-height:44px;font-size:14px;color:var(--ink);outline:none}
.tavern-searching{font-size:13px;color:var(--accent-ink)}
.tavern-history{display:flex;align-items:center;gap:6px;min-height:46px;padding:0 14px;border:1px solid var(--edge);border-radius:var(--r-control);background:var(--canvas);color:var(--quiet);font-size:14px;cursor:pointer;white-space:nowrap;transition:color 140ms ease-out,border-color 140ms ease-out}
.tavern-history:hover{color:var(--accent-ink);border-color:var(--accent)}
.tavern-history.on{color:var(--accent-ink);border-color:var(--accent);background:var(--wash)}
.tavern-import,.tavern-empty-import{position:relative;display:flex;align-items:center;gap:8px;min-height:46px;padding:0 16px;border:1px solid var(--accent);border-radius:var(--r-control);background:var(--accent);color:var(--on-accent);font-size:14px;cursor:pointer;white-space:nowrap}
.tavern-import.busy{opacity:.6;pointer-events:none}
.tavern-import input,.tavern-empty-import input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}
.tavern-hint{display:flex;align-items:center;gap:4px;margin:0 0 18px;padding:4px 6px 4px 12px;min-height:40px;border:1px solid var(--edge);border-radius:var(--r-control);background:var(--wash);color:var(--quiet);font-size:12.5px;line-height:1.4}
.tavern-hint-text{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tavern-hint-help,.tavern-hint-close{flex:none;display:flex;align-items:center;justify-content:center;width:32px;height:32px;min-height:32px;padding:0;border:0;border-radius:8px;background:transparent;color:var(--quiet);cursor:pointer}
.tavern-hint-help{color:var(--accent-ink)}
.tavern-hint-help:hover,.tavern-hint-close:hover{background:var(--canvas);color:var(--accent-ink)}
.tavern-select-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:16px;padding:10px 12px;border:1px solid var(--edge);border-radius:var(--r-card);background:var(--canvas)}
.tavern-select-count{font-size:14px;color:var(--ink)}
.tavern-select-names{flex:1;min-width:0;font-size:13px;color:var(--quiet);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tavern-select-clear,.tavern-select-group{min-height:40px;padding:0 14px;border-radius:var(--r-control);border:1px solid var(--edge);background:var(--canvas);color:var(--quiet);font-size:13px;cursor:pointer}
.tavern-select-group{display:flex;align-items:center;gap:6px;border-color:var(--accent);background:var(--accent);color:var(--on-accent)}
.tavern-select-group:disabled{opacity:.45;cursor:not-allowed}
.tavern-card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}
.tavern-card{position:relative;display:block;min-width:0;border:1px solid var(--edge);border-radius:var(--r-card);background:var(--canvas)}
.tavern-card:hover{border-color:var(--accent);box-shadow:0 6px 18px rgba(43,38,34,.06)}
.tavern-card.selected{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.tavern-card-open{display:flex;gap:14px;align-items:flex-start;text-align:left;width:100%;padding:14px;border:0;border-radius:var(--r-card);background:transparent;color:inherit;cursor:pointer}
.tavern-card-open:disabled{opacity:.55;cursor:progress}
.tavern-select{position:absolute;top:8px;left:8px;z-index:2;display:flex;align-items:center;justify-content:center;width:36px;height:36px;min-height:0;padding:0;border:1.5px solid var(--edge);border-radius:10px;background:rgba(255,253,249,.92);color:transparent;cursor:pointer}
.tavern-select.on{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.tavern-avatar{flex:none;width:64px;height:64px;border-radius:14px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent-ink);font-family:var(--font-serif);font-size:26px}
.tavern-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.tavern-card-copy{display:flex;flex-direction:column;min-width:0;gap:4px;flex:1}
.tavern-card-name-row{display:flex;align-items:center;gap:6px;min-width:0}
.tavern-card-name{flex:0 1 auto;min-width:0;font-family:var(--font-serif);font-size:17px;font-weight:600;letter-spacing:.01em;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tavern-card-preset{flex:none;font-size:12px;line-height:1.6;padding:0 6px;border:1px solid var(--accent-ink);border-radius:6px;color:var(--accent-ink);background:var(--wash);white-space:nowrap;letter-spacing:.02em}
.tavern-card-desc{font-size:13px;line-height:1.6;color:var(--quiet);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tavern-card-meta{display:block;font-size:12px;color:var(--quiet);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tavern-card-detail{position:absolute;top:8px;right:8px;min-height:32px;display:inline-flex;align-items:center;padding:0 10px;border:0;border-radius:8px;background:transparent;color:var(--accent-ink);font-size:12.5px;cursor:pointer;opacity:0;transition:opacity 140ms ease-out,background 140ms ease-out}
.tavern-card:hover .tavern-card-detail,.tavern-card-detail:focus-visible{opacity:1}
.tavern-card-detail:hover{background:var(--wash)}
.tavern-empty-import{display:inline-flex}
.tavern-empty-action{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 18px;border:1px solid var(--accent);border-radius:var(--r-control);background:var(--accent);color:var(--on-accent);font-size:14px;cursor:pointer}
.inline-link{min-height:0;padding:0 6px;border:0;background:transparent;color:var(--accent-ink);text-decoration:underline;cursor:pointer}
@media(max-width:900px){
 .tavern-card-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
 .tavern-card-detail{opacity:1}
}
@media(max-width:767px){
 .tavern-toolbar{flex-wrap:wrap;gap:8px;margin-bottom:16px}
 .tavern-search{flex:1 1 100%;order:-1}
 .tavern-history,.tavern-import{flex:1;justify-content:center;min-height:44px;padding:0 10px}
 .tavern-hint{margin-bottom:14px;min-height:40px;padding:0 4px 0 12px;gap:2px}
 .tavern-hint-help,.tavern-hint-close{width:40px;height:40px;min-height:40px}
 .tavern-select-bar .tavern-select-group{flex:1 1 auto;justify-content:center;min-height:44px}
 .tavern-select-clear{min-height:44px}
}
@media(max-width:600px){
 .tavern-card-grid{grid-template-columns:1fr;gap:12px}
 .tavern-card-open{flex-direction:row;align-items:center;gap:14px;padding:12px 78px 12px 12px;min-height:96px}
 .tavern-avatar{width:72px;height:72px;font-size:28px;border-radius:14px}
 .tavern-card-name{font-size:16px}
 .tavern-card-detail{top:50%;right:10px;transform:translateY(-50%);opacity:1;min-height:44px;padding:0 14px;border:1px solid var(--edge);border-radius:8px;background:var(--canvas)}
}
</style>
