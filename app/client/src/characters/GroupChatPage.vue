<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {RouterLink,useRoute} from 'vue-router'
import {Send,Users} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import {useRequestScope} from '../platform/request-scope'
import {cardAvatarBlob,getGroupChat,sendGroupMessage,type GroupCard,type GroupChat,type GroupMessage} from './api'
const route=useRoute(),capture=useRequestScope()
const chatId=computed(()=>String(route.params.id||''))
const chat=ref<GroupChat|null>(null),cards=ref<GroupCard[]>([]),messages=ref<GroupMessage[]>([])
const loading=ref(true),loadFailed=ref(false),error=ref('')
const draft=ref(''),sending=ref(false),pending=ref(false),pendingSpeaker=ref('')
const sendError=ref(''),pollError=ref(''),avatars=ref<Record<string,string>>({})
const messagesEl=ref<HTMLElement>()
let disposed=false,epoch=0,avatarEpoch=0
let pollTimer:ReturnType<typeof setTimeout>|undefined,pollStarted=0
let baselineAssistantIds=new Set<string>()
const TIMEOUT_MS=120000
const participantNames=computed(()=>cards.value.map(card=>card.name).join('、'))
const assistantCount=computed(()=>messages.value.filter(message=>message.role==='assistant'&&(message.status==='saved'||!message.status)).length)
const nextSpeaker=computed(()=>{
  if(!cards.value.length)return ''
  return cards.value[assistantCount.value%cards.value.length]?.name||''
})
const emptyMessages=computed(()=>!!chat.value&&!messages.value.length)
function revokeAll(){
  for(const url of Object.values(avatars.value))URL.revokeObjectURL(url)
  avatars.value={}
}
async function loadAvatars(items:GroupCard[]){
  const ids=new Set(items.map(card=>card.id)),revision=++avatarEpoch
  for(const [id,url] of Object.entries(avatars.value))if(!ids.has(id)){URL.revokeObjectURL(url);delete avatars.value[id]}
  for(const card of items){
    if(avatars.value[card.id])continue
    try{
      const blob=await cardAvatarBlob(card.id)
      if(disposed||revision!==avatarEpoch||!ids.has(card.id))continue
      if(!blob)continue
      const url=URL.createObjectURL(blob)
      if(disposed||revision!==avatarEpoch||!ids.has(card.id)){URL.revokeObjectURL(url);continue}
      avatars.value={...avatars.value,[card.id]:url}
    }catch{/* Initials stay visible when an avatar cannot be read. */}
  }
}
function apply(detail:{chat:GroupChat;cards:GroupCard[];messages:GroupMessage[]}){
  chat.value=detail.chat
  cards.value=detail.cards
  messages.value=detail.messages
  void loadAvatars(detail.cards)
  void scrollToEnd()
}
async function scrollToEnd(){
  await nextTick()
  const el=messagesEl.value
  if(el)el.scrollTop=el.scrollHeight
}
async function load(){
  const scope=capture(),revision=++epoch
  if(!chat.value)loading.value=true
  error.value=''
  try{
    const detail=await getGroupChat(chatId.value)
    if(!scope.valid()||revision!==epoch)return
    apply(detail)
    loadFailed.value=false
  }catch(e){
    if(scope.valid()&&revision===epoch){
      loadFailed.value=true
      error.value=(e as Error).message||'群聊暂时无法读取，请稍后重试。'
    }
  }finally{
    if(scope.valid()&&revision===epoch)loading.value=false
  }
}
function clearPoll(){if(pollTimer!==undefined){clearTimeout(pollTimer);pollTimer=undefined}}
function stopPending(){pending.value=false;clearPoll()}
async function refreshMessageList(scope:ReturnType<typeof capture>){
  try{
    const detail=await getGroupChat(chatId.value)
    if(scope.valid()&&pending.value)apply(detail)
  }catch{/* The send already succeeded; polling takes over from here. */}
}
function schedulePoll(scope:ReturnType<typeof capture>){
  clearPoll()
  pollTimer=setTimeout(()=>{void poll(scope)},1000)
}
async function poll(scope:ReturnType<typeof capture>){
  if(disposed||!pending.value||!scope.valid())return
  try{
    const detail=await getGroupChat(chatId.value)
    if(disposed||!pending.value||!scope.valid())return
    apply(detail)
    const fresh=detail.messages.find(message=>message.role==='assistant'&&!baselineAssistantIds.has(message.id)&&(message.status==='saved'||!message.status))
    const failed=detail.messages.find(message=>message.role==='assistant'&&!baselineAssistantIds.has(message.id)&&message.status==='failed')
    if(fresh){
      stopPending()
      pollError.value=''
      return
    }
    if(failed){
      stopPending()
      sendError.value='这条回复生成失败，可以再发一条试试。'
      return
    }
    pollError.value=''
    if(Date.now()-pollStarted>=TIMEOUT_MS){
      stopPending()
      sendError.value='回复生成超时，请稍后刷新页面查看结果。'
      return
    }
    schedulePoll(scope)
  }catch(e){
    if(disposed||!pending.value||!scope.valid())return
    const status=(e as {status?:number}).status
    if(status===404){stopPending();return}
    if(status===401){stopPending();return}
    if(Date.now()-pollStarted>=TIMEOUT_MS){
      stopPending()
      sendError.value='回复生成超时，请稍后刷新页面查看结果。'
      return
    }
    pollError.value=''
    schedulePoll(scope)
  }
}
async function send(){
  const text=draft.value.trim()
  if(!text||sending.value||pending.value)return
  const scope=capture()
  sending.value=true
  sendError.value=''
  pollError.value=''
  baselineAssistantIds=new Set(messages.value.filter(message=>message.role==='assistant').map(message=>message.id))
  try{
    const result=await sendGroupMessage(chatId.value,text)
    if(!scope.valid())return
    draft.value=''
    pending.value=true
    pendingSpeaker.value=result.speakerName||nextSpeaker.value
    pollStarted=Date.now()
    await refreshMessageList(scope)
    if(scope.valid()&&pending.value)schedulePoll(scope)
  }catch(e){
    if(scope.valid())sendError.value='发送失败：'+((e as Error).message||'请稍后重试。')
  }finally{
    if(!disposed)sending.value=false
  }
}
watch(chatId,()=>{stopPending();void load()})
onMounted(()=>{void load()})
onUnmounted(()=>{disposed=true;epoch++;avatarEpoch++;stopPending();revokeAll()})
</script>
<template>
 <div class="workspace-page group-chat-page">
  <PageHeader :title="chat?.title||'群聊'" eyebrow="角色群聊" :description="participantNames?'参与者：'+participantNames:'勾选 2–5 张角色卡开始的多人对话。'">
   <RouterLink class="inline-link" to="/characters/groups">返回群聊列表</RouterLink>
   <RouterLink class="inline-link" to="/characters">角色卡库</RouterLink>
  </PageHeader>
  <p v-if="error" class="error" role="alert">{{error}} <button type="button" @click="load">重新载入群聊</button></p>
  <p v-if="loading" class="workspace-loading" role="status">正在打开群聊…</p>
  <template v-else-if="chat">
   <div class="group-participants">
    <span v-for="card in cards" :key="card.id" class="group-participant">
     <span class="group-avatar">
      <img v-if="avatars[card.id]" :src="avatars[card.id]" :alt="card.name+'头像'" loading="lazy">
      <span v-else aria-hidden="true">{{Array.from(card.name.trim())[0]||'·'}}</span>
     </span>
     <small>{{card.name}}</small>
    </span>
   </div>
   <p class="group-next" role="status">
    <Users :size="16"/>
    <template v-if="pending">{{pendingSpeaker}} 正在回应，稍等片刻…</template>
    <template v-else-if="nextSpeaker">下一位：{{nextSpeaker}}</template>
    <template v-else>群聊已就绪</template>
   </p>
   <p v-if="pollError" class="error" role="alert">{{pollError}}</p>
   <p v-if="sendError" class="error" role="alert">{{sendError}}</p>
   <section v-if="emptyMessages" class="workspace-empty group-empty">
    <Users :size="40"/>
    <h2>还没有人说话</h2>
    <p>写一句开场白，{{nextSpeaker||'第一位角色'}} 会先回应。</p>
   </section>
   <div v-else ref="messagesEl" class="group-messages" role="log" aria-live="polite">
    <article v-for="message in messages" :key="message.id" class="group-message" :class="message.role==='user'?'from-user':'from-assistant'">
     <div v-if="message.role==='assistant'" class="group-message-speaker">
      <span class="group-avatar small">
       <img v-if="message.speakerCardId&&avatars[message.speakerCardId]" :src="avatars[message.speakerCardId]" :alt="(message.speakerName||'角色')+'头像'" loading="lazy">
       <span v-else aria-hidden="true">{{Array.from((message.speakerName||'·').trim())[0]}}</span>
      </span>
      <strong>{{message.speakerName||'角色'}}</strong>
     </div>
     <p class="group-bubble" :class="{pending:message.status==='pending',failed:message.status==='failed'}">{{message.status==='pending'?'正在组织语言…':(message.text||(message.status==='failed'?'这条回复生成失败':'…'))}}</p>
     <small v-if="message.status==='failed'" class="group-failed">这条回复生成失败</small>
    </article>
   </div>
   <form class="group-composer" @submit.prevent="send">
    <textarea v-model="draft" rows="3" placeholder="对大家说点什么…" :disabled="sending||pending"></textarea>
    <button class="primary" type="submit" :disabled="sending||pending||!draft.trim()"><Send :size="16"/>{{sending?'发送中…':pending?'等待回应…':'发送'}}</button>
   </form>
   <p class="product-hint">同一时间只会有一位角色回应；等待回复时不能再发下一条。</p>
  </template>
 </div>
</template>
<style scoped>
.group-chat-page{width:100%;min-width:0}.group-participants{display:flex;flex-wrap:wrap;gap:18px;margin:0 0 16px}.group-participant{display:flex;align-items:center;gap:8px;color:var(--quiet);font-size:12px}.group-avatar{width:40px;height:40px;flex:none;display:grid;place-items:center;border:1px solid var(--edge);border-radius:50%;overflow:hidden;background:var(--wash);color:var(--accent);font-family:var(--font-ui);font-weight:600;font-size:16px;line-height:1}.group-avatar.small{width:26px;height:26px;font-size:12px}.group-avatar img{display:block;width:100%;height:100%;object-fit:cover}.group-next{display:flex;align-items:center;gap:8px;margin:0 0 14px;padding:10px 14px;border:1px solid var(--edge);border-radius:10px;background:var(--wash);color:var(--quiet);font-size:13px}.group-messages{display:grid;gap:14px;max-height:56vh;overflow-y:auto;padding:4px 2px 10px}.group-message{display:grid;gap:6px;max-width:min(680px,92%)}.from-assistant{justify-self:start}.from-user{justify-self:end;justify-items:end}.group-message-speaker{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--ink)}.group-bubble{margin:0;padding:12px 15px;border:1px solid var(--edge);border-radius:12px;background:var(--paper);color:var(--ink);font-size:14px;line-height:1.85;white-space:pre-wrap;overflow-wrap:anywhere}.from-user .group-bubble{background:var(--accent);border-color:var(--accent);color:#fff}.group-bubble.pending{color:var(--quiet);font-style:italic}.group-bubble.failed{border-color:#e4b4b0}.group-failed{margin:0;color:#b3261e;font-size:12px}.group-empty{margin-top:8px}.group-composer{display:grid;gap:10px;margin-top:18px;padding-top:16px;border-top:1px solid var(--edge)}.group-composer textarea{min-height:88px}.group-composer button{justify-self:end;display:inline-flex;align-items:center;gap:8px}@media(max-width:767px){.group-messages{max-height:50vh}.group-message{max-width:100%}}
</style>
