<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {MessageSquareText} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {authorizationHeaders} from '../api'
import {listChats,tavernAvatarUrl,type TavernChat} from './api'

const router=useRouter()
const store=useApp()
const scope=useRequestScope()
const chats=ref<TavernChat[]>([])
const loading=ref(true)
const error=ref('')
const avatars=ref<Record<string,string>>({})
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

const empty=computed(()=>!loading.value&&!error.value&&!chats.value.length)

onMounted(()=>void load())
onUnmounted(()=>{mounted=false;for(const url of Object.values(avatars.value))URL.revokeObjectURL(url)})
</script>

<template>
 <section class="tavern-home" aria-label="聊天历史">
  <header class="tavern-page-head">
   <h1>聊天历史</h1>
   <button type="button" class="tavern-back" @click="router.push('/')">返回角色卡</button>
  </header>
  <p v-if="error" class="error" role="alert">{{error}}<button class="inline-link" type="button" @click="load">重试</button></p>
  <p v-if="loading" class="tavern-loading" role="status">正在加载聊天…</p>
  <div v-else-if="empty" class="tavern-empty">
   <MessageSquareText :size="30" aria-hidden="true"/>
   <p class="tavern-empty-title">还没有聊天</p>
   <p>挑一张角色卡开始聊天，历史会自动出现在这里。</p>
   <button type="button" class="tavern-empty-action" @click="router.push('/')">去角色卡库</button>
  </div>
  <div v-else class="chat-history-list">
   <button v-for="chat in chats" :key="chat.id" type="button" class="chat-history-row" @click="router.push('/chats/'+encodeURIComponent(chat.id))">
    <span class="chat-history-avatars" aria-hidden="true">
     <span v-for="cardId in chatCardIds(chat)" :key="cardId" class="chat-history-avatar">
      <img v-if="avatars[cardId]" :src="avatars[cardId]" alt="">
      <span v-else>{{(chat.members?.find(member=>member.cardId===cardId)?.name||chat.characterName||'?').slice(0,1)}}</span>
     </span>
    </span>
    <span class="chat-history-copy">
     <span class="chat-history-name">{{chatName(chat)}}<span v-if="chat.mode==='group'" class="chat-history-mode">群聊</span></span>
     <span class="chat-history-last">{{chat.lastMessage||'还没有消息'}}</span>
    </span>
    <span class="chat-history-time">{{when(chat)}}</span>
   </button>
  </div>
 </section>
</template>

<style scoped>
.tavern-page-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
.tavern-page-head h1{font-family:var(--font-serif);font-size:24px;margin:0;color:var(--ink)}
.tavern-back{min-height:40px;padding:0 14px;border:1px solid var(--edge);border-radius:10px;background:var(--canvas);color:var(--quiet);font-size:13px;cursor:pointer}
.tavern-back:hover{color:var(--accent);border-color:var(--accent)}
.chat-history-list{display:flex;flex-direction:column;gap:8px}
.chat-history-row{display:flex;align-items:center;gap:12px;width:100%;padding:12px 14px;border:1px solid var(--edge);border-radius:14px;background:var(--canvas);color:inherit;text-align:left;cursor:pointer}
.chat-history-row:hover{border-color:var(--accent)}
.chat-history-avatars{display:flex;flex:none}
.chat-history-avatar{width:34px;height:34px;margin-left:-8px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent);font-size:13px;border:2px solid var(--canvas)}
.chat-history-avatar:first-child{margin-left:0}
.chat-history-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.chat-history-copy{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.chat-history-name{font-size:15px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-history-mode{display:inline-block;margin-left:6px;padding:0 6px;border-radius:6px;background:var(--wash);color:var(--accent);font-size:11px}
.chat-history-last{font-size:13px;color:var(--quiet);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-history-time{flex:none;font-size:12px;color:var(--quiet)}
.tavern-empty{display:flex;flex-direction:column;align-items:center;gap:8px;padding:64px 16px;text-align:center;color:var(--quiet)}
.tavern-empty-action{min-height:40px;padding:0 16px;border:1px solid var(--accent);border-radius:10px;background:var(--accent);color:#fff;font-size:14px;cursor:pointer}
@media(max-width:520px){.chat-history-time{display:none}}
</style>
