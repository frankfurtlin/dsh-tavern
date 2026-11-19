<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRouter} from 'vue-router'
import {X} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {authorizationHeaders} from '../api'
import {listChats,tavernAvatarUrl,type TavernChat} from './api'
import LoadState from './LoadState.vue'

const router=useRouter()
const store=useApp()
const scope=useRequestScope()
const chats=ref<TavernChat[]>([])
const loading=ref(true)
const error=ref('')
const query=ref('')
const searchBusy=ref(false)
const searchTerm=ref('')
const avatars=ref<Record<string,string>>({})
let searchTimer:ReturnType<typeof setTimeout>|undefined
let mounted=true

function chatName(chat:TavernChat){
  if(chat.mode==='group'&&chat.members?.length)return chat.members.map(member=>member.name).join(' · ')
  return chat.characterName||chat.title||'聊天'
}

function chatCardIds(chat:TavernChat):string[]{
  if(chat.mode==='group')return (chat.members??[]).map(member=>member.cardId).slice(0,5)
  return chat.cardId?[chat.cardId]:[]
}

function when(chat:TavernChat){
  const value=chat.lastMessageAt||chat.updatedAt
  return value?new Date(value).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):''
}

async function loadAvatar(cardId:string){
  if(avatars.value[cardId])return
  try{
    const response=await fetch(tavernAvatarUrl(cardId),{credentials:'include',headers:authorizationHeaders()})
    if(!response.ok)return
    const blob=await response.blob()
    if(!mounted)return
    avatars.value={...avatars.value,[cardId]:URL.createObjectURL(blob)}
  }catch{/* 头像失败不影响列表 */}
}

async function load(){
  loading.value=true;error.value=''
  const ticket=scope()
  try{
    const list=await listChats()
    if(!ticket.valid())return
    chats.value=[...list].sort((a,b)=>{
      const left=a.lastMessageAt||a.updatedAt||''
      const right=b.lastMessageAt||b.updatedAt||''
      return right.localeCompare(left)
    })
    for(const chat of chats.value)for(const cardId of chatCardIds(chat))void loadAvatar(cardId)
  }catch(e){
    if(ticket.valid())error.value=(e as Error).message||'聊天历史加载失败，服务端可能尚未提供列表接口。'
  }finally{
    if(ticket.valid())loading.value=false
  }
}

function escapeRegExp(value:string){return value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}

function nameParts(chat:TavernChat):{text:string;hit:boolean}[]{
  const name=chatName(chat)
  const term=searchTerm.value.trim()
  if(!term)return [{text:name,hit:false}]
  const pattern=new RegExp('('+escapeRegExp(term)+')','gi')
  return name.split(pattern).filter(part=>part!=='').map(part=>({text:part,hit:part.toLowerCase()===term.toLowerCase()}))
}

async function search(q:string){
  searchBusy.value=true;error.value=''
  const ticket=scope()
  try{
    const list=await listChats({q})
    if(!ticket.valid())return
    chats.value=[...list].sort((a,b)=>{
      const left=a.lastMessageAt||a.updatedAt||''
      const right=b.lastMessageAt||b.updatedAt||''
      return right.localeCompare(left)
    })
    searchTerm.value=q
    for(const chat of chats.value)for(const cardId of chatCardIds(chat))void loadAvatar(cardId)
    if(typeof window!=='undefined')window.scrollTo?.({top:0,behavior:'smooth'})
  }catch(e){
    if(ticket.valid())error.value=(e as Error).message||'搜索失败，请稍后重试。'
  }finally{
    if(ticket.valid())searchBusy.value=false
  }
}

watch(query,(value)=>{
  if(searchTimer!==undefined){clearTimeout(searchTimer);searchTimer=undefined}
  const term=value.trim()
  if(!term){searchTerm.value='';if(!loading.value)void load();return}
  searchTimer=setTimeout(()=>{searchTimer=undefined;void search(term)},300)
})

const empty=computed(()=>!loading.value&&!error.value&&!chats.value.length)
const searching=computed(()=>!!query.value.trim())

onMounted(()=>void load())
onUnmounted(()=>{mounted=false;if(searchTimer!==undefined)clearTimeout(searchTimer);for(const url of Object.values(avatars.value))URL.revokeObjectURL(url)})
</script>

<template>
 <section class="tavern-home" aria-label="聊天历史">
  <div class="chat-search-box" role="search">
   <input v-model="query" type="search" aria-label="搜索聊天" placeholder="搜索标题或消息内容">
   <button v-if="query" type="button" class="chat-search-clear" aria-label="清空搜索" title="清空搜索" @click="query=''"><X :size="18"/></button>
  </div>
  <p v-if="searching" class="chat-search-count" role="status">{{searchBusy?'搜索中…':'找到 '+chats.length+' 个聊天'}}</p>

  <LoadState v-if="loading||error" :loading="loading" :error="error" loading-text="正在加载聊天…" @retry="load"/>

  <LoadState v-else-if="empty" empty :empty-title="searching?'没有匹配的聊天':'还没有聊天'"
   :empty-text="searching?'换个关键词试试。':'挑一张角色卡开始聊天，历史会自动出现在这里。'">
   <template #action>
    <button v-if="searching" type="button" class="tavern-empty-action" @click="query=''">清空搜索</button>
    <button v-else type="button" class="tavern-empty-action" @click="router.push('/')">去选角色</button>
   </template>
  </LoadState>
  <div v-else class="chat-history-list">
   <button v-for="chat in chats" :key="chat.id" type="button" class="chat-history-row" @click="router.push('/chats/'+encodeURIComponent(chat.id))">
    <span class="chat-history-avatars" aria-hidden="true">
     <span v-for="cardId in chatCardIds(chat)" :key="cardId" class="chat-history-avatar">
      <img v-if="avatars[cardId]" :src="avatars[cardId]" alt="">
      <span v-else>{{(chat.members?.find(member=>member.cardId===cardId)?.name||chat.characterName||'?').slice(0,1)}}</span>
     </span>
    </span>
    <span class="chat-history-copy">
     <span class="chat-history-name"><template v-for="(part,index) in nameParts(chat)" :key="index"><mark v-if="part.hit" class="chat-history-mark">{{part.text}}</mark><template v-else>{{part.text}}</template></template><span v-if="chat.mode==='group'" class="chat-history-mode">群聊</span></span>
     <span class="chat-history-last">{{chat.lastMessage||'还没有消息'}}</span>
    </span>
    <span class="chat-history-time">{{when(chat)}}</span>
   </button>
  </div>
 </section>
</template>

<style scoped>
.tavern-home{width:100%;max-width:1180px;margin:0 auto;padding:24px clamp(16px,3vw,28px) 64px}
.tavern-home :focus-visible{outline:2px solid var(--accent-ink);outline-offset:2px}
.tavern-page-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
.tavern-page-head h1{font-family:var(--font-serif);font-size:24px;margin:0;color:var(--ink)}
.tavern-back{min-height:40px;padding:0 14px;border:1px solid var(--edge);border-radius:10px;background:var(--canvas);color:var(--quiet);font-size:13px;cursor:pointer}
.tavern-back:hover{color:var(--accent);border-color:var(--accent)}
.chat-history-list{display:flex;flex-direction:column;gap:8px}
.chat-history-row{display:flex;align-items:center;gap:12px;width:100%;padding:10px 14px;min-height:64px;border:1px solid var(--edge);border-radius:var(--r-card);background:var(--canvas);color:inherit;text-align:left;cursor:pointer;transition:border-color 140ms ease-out}
.chat-history-row:hover{border-color:var(--accent)}
.chat-history-avatars{display:flex;flex:none}
.chat-history-avatar{width:34px;height:34px;margin-left:-8px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent-ink);font-size:13px;border:2px solid var(--canvas)}
.chat-history-avatar:first-child{margin-left:0}
.chat-history-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.chat-history-copy{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.chat-history-name{font-size:15px;font-weight:500;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-history-mode{display:inline-block;margin-left:6px;padding:0 6px;border-radius:6px;background:var(--wash);color:var(--accent-ink);font-size:11px}
.chat-history-last{font-size:13px;color:var(--quiet);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.5}
.chat-history-time{flex:none;font-size:12px;color:var(--quiet)}
.tavern-empty{display:flex;flex-direction:column;align-items:center;gap:8px;padding:64px 16px;text-align:center;color:var(--quiet)}
.tavern-empty-action{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 18px;border:1px solid var(--accent);border-radius:var(--r-control);background:var(--accent);color:var(--on-accent);font-size:14px;cursor:pointer}
@media(max-width:767px){
 .chat-history-row{gap:10px;padding:10px 12px}
 .chat-history-avatar{width:30px;height:30px}
 .chat-history-time{display:none}
}
@media(max-width:520px){.chat-history-last{max-width:100%}}

.chat-search-box{display:flex;align-items:center;gap:8px;margin:0 0 14px;padding:4px 8px 4px 12px;border:1px solid var(--edge);border-radius:var(--r-control);background:var(--canvas)}
.chat-search-box input{flex:1;min-width:0;min-height:44px;border:0;background:transparent;color:var(--ink);padding:0 4px}
.chat-search-box input::-webkit-search-cancel-button,.chat-search-box input::-webkit-search-decoration{-webkit-appearance:none;appearance:none;display:none}
.chat-search-clear{flex:none;display:flex;align-items:center;justify-content:center;width:40px;height:40px;border:0;border-radius:8px;background:transparent;color:var(--quiet);cursor:pointer}
.chat-search-clear:hover{background:var(--wash);color:var(--ink)}
.chat-search-count{margin:-6px 0 12px;font-size:13px;color:var(--quiet)}
.chat-history-mark{padding:0 1px;border-radius:3px;background:rgba(196,120,66,.22);color:inherit}
@media (max-width:767px){.chat-search-box input{min-height:44px}.chat-search-clear{width:44px;height:44px}}
</style>
