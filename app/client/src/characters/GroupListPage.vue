<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {RouterLink,useRouter} from 'vue-router'
import {MessagesSquare,Plus,Trash2} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {deleteGroupChat,listGroupChats,type GroupChatSummary} from './api'
const router=useRouter(),store=useApp(),capture=useRequestScope()
const chats=ref<GroupChatSummary[]>([]),loading=ref(true),error=ref('')
const deleting=ref(''),confirming=ref('')
let disposed=false,epoch=0
const empty=computed(()=>!loading.value&&!error.value&&!chats.value.length)
function names(chat:GroupChatSummary){return (chat.cards||[]).map(card=>card.name).join('、')||'角色卡群聊'}
function time(chat:GroupChatSummary){
  if(!chat.updatedAt)return ''
  const date=new Date(chat.updatedAt)
  return Number.isNaN(date.getTime())?'':date.toLocaleString('zh-CN')
}
async function load(){
  const scope=capture(),revision=++epoch
  if(!chats.value.length)loading.value=true
  error.value=''
  try{
    const items=await listGroupChats()
    if(!scope.valid()||revision!==epoch)return
    chats.value=items
  }catch(e){
    if(scope.valid()&&revision===epoch)error.value=(e as Error).message||'群聊列表暂时无法读取，请稍后重试。'
  }finally{
    if(scope.valid()&&revision===epoch)loading.value=false
  }
}
async function remove(chat:GroupChatSummary){
  if(deleting.value)return
  if(confirming.value!==chat.id){confirming.value=chat.id;return}
  const scope=capture()
  deleting.value=chat.id
  error.value=''
  try{
    await deleteGroupChat(chat.id)
    if(!scope.valid())return
    store.message('已删除群聊「'+chat.title+'」。')
    await load()
  }catch(e){
    if(scope.valid())error.value='删除失败：'+((e as Error).message||'请稍后重试。')
  }finally{
    if(!disposed){deleting.value='';confirming.value=''}
  }
}
onMounted(()=>{void load()})
onUnmounted(()=>{disposed=true;epoch++})
</script>
<template>
 <div class="workspace-page group-list-page">
  <PageHeader title="群聊" eyebrow="角色卡库" description="勾选 2–5 张角色卡，让 TA 们坐进同一间屋子聊天。">
   <RouterLink class="workspace-file-button" to="/characters"><Plus :size="17"/>发起群聊</RouterLink>
   <RouterLink class="workspace-file-button" to="/characters/regex">输出替换规则</RouterLink>
  </PageHeader>
  <p v-if="error" class="error" role="alert">{{error}} <button type="button" @click="load">重新载入群聊</button></p>
  <p v-if="loading" class="workspace-loading" role="status">正在读取群聊…</p>
  <section v-else-if="empty" class="workspace-empty">
   <MessagesSquare :size="40"/>
   <h2>还没有群聊</h2>
   <p>在角色卡库里勾选 2–5 张角色卡，点「发起群聊」就能让 TA 们同台对话。</p>
   <RouterLink class="primary inline-link" to="/characters">去角色卡库挑选角色</RouterLink>
  </section>
  <div v-else class="group-list">
   <article v-for="chat in chats" :key="chat.id" class="workspace-card group-item" :data-chat-id="chat.id">
    <div class="group-item-main">
     <RouterLink class="group-item-title" :to="'/characters/groups/'+chat.id">{{chat.title}}</RouterLink>
     <small class="group-item-cards">{{names(chat)}}</small>
     <p class="group-item-last">{{chat.lastText||'还没有消息，进去打个招呼吧。'}}</p>
     <small v-if="time(chat)" class="group-item-time">{{time(chat)}}</small>
    </div>
    <div class="workspace-card-actions">
     <RouterLink class="inline-link" :to="'/characters/groups/'+chat.id">进入群聊</RouterLink>
     <button v-if="confirming===chat.id" class="danger" :disabled="!!deleting" @click="remove(chat)">确认删除</button>
     <button v-else class="danger" :disabled="!!deleting" @click="remove(chat)"><Trash2 :size="15"/>{{deleting===chat.id?'正在删除…':'删除'}}</button>
     <button v-if="confirming===chat.id" :disabled="!!deleting" @click="confirming=''">取消</button>
    </div>
   </article>
  </div>
 </div>
</template>
<style scoped>
.group-list-page{width:100%;min-width:0}.group-list{display:grid;gap:14px}.group-item{display:flex;gap:18px;align-items:flex-start}.group-item-main{display:grid;gap:6px;min-width:0;flex:1}.group-item-title{font-family:var(--font-ui);font-size:18px;font-weight:600;color:var(--ink);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.group-item-title:hover{color:var(--accent)}.group-item-cards{color:var(--quiet);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.group-item-last{margin:0;color:var(--quiet);font-size:13px;line-height:1.8;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.group-item-time{color:var(--quiet);font-size:12px}.group-item .workspace-card-actions{flex:none;align-items:flex-end;flex-direction:column}@media(max-width:767px){.group-item{flex-direction:column}.group-item .workspace-card-actions{flex-direction:row;align-items:center;width:100%}}
</style>
