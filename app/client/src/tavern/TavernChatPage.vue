<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,ChevronLeft,ChevronRight,Download,Pencil,RefreshCw,Send,Square,Trash2,X} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {deleteMessage,editMessage,exportChatBlob,getChat,messageText,selectSwipe,updateChat,updateChatSampling,type ChatDetail,TavernMessage} from './api'
import {saveBlob,safeFileName} from '../platform/download'
import {streamChat} from './sse'
import Sheet from '../components/Sheet.vue'

const route=useRoute(),router=useRouter(),store=useApp(),scope=useRequestScope()
const chatId=computed(()=>String(route.params.id||''))
const detail=ref<ChatDetail|null>(null)
const messages=ref<TavernMessage[]>([])
const loading=ref(true)
const error=ref('')
const streamError=ref('')
const streaming=ref(false)
const streamText=ref('')
const input=ref('')
const editingId=ref('')
const editText=ref('')
const savingEdit=ref(false)
const swipeBusy=ref(false)
const titleEditing=ref(false)
const titleText=ref('')
const deletingId=ref('')
const exporting=ref('')
const exportMenuOpen=ref(false)
const samplingOpen=ref(false)
const savingSampling=ref(false)
const samplingError=ref('')
const sampling=ref({temperature:0.8,topP:0.95,maxTokens:1024,stopText:''})
const hasMore=ref(false)
const loadingEarlier=ref(false)
const log=ref<HTMLElement|null>(null)
let abort:AbortController|null=null
let mounted=true
const PAGE_SIZE=200

const characterName=computed(()=>detail.value?.chat.characterName||detail.value?.chat.title||'聊天')
const canRegenerate=computed(()=>!streaming.value&&messages.value.length>0&&messages.value[messages.value.length-1]?.role==='assistant')

function scrollToBottom(){void nextTick(()=>{if(log.value)log.value.scrollTop=log.value.scrollHeight})}

async function load(silent=false){
  if(!silent)loading.value=true
  error.value=''
  const ticket=scope()
  try{
    const value=await getChat(chatId.value,{limit:PAGE_SIZE})
    if(!ticket.valid())return
    detail.value=value
    messages.value=value.messages||[]
    hasMore.value=value.hasMore??messages.value.length>=PAGE_SIZE
    if(!titleEditing.value)titleText.value=value.chat.title||''
    scrollToBottom()
  }catch(e){
    if(ticket.valid())error.value=(e as Error).message||'聊天加载失败，请稍后重试。'
  }finally{
    if(ticket.valid())loading.value=false
  }
}

async function loadEarlier(){
  const first=messages.value[0]?.sequence
  if(loadingEarlier.value||first===undefined||first<=1)return
  const ticket=scope()
  const node=log.value
  const previousHeight=node?.scrollHeight??0
  const previousTop=node?.scrollTop??0
  loadingEarlier.value=true
  try{
    const value=await getChat(chatId.value,{limit:PAGE_SIZE,before:first})
    if(!ticket.valid())return
    const known=new Set(messages.value.map(message=>message.id))
    const earlier=(value.messages||[]).filter(message=>!known.has(message.id))
    messages.value=[...earlier,...messages.value]
    hasMore.value=value.hasMore??earlier.length>=PAGE_SIZE
    if(node)await nextTick(()=>{node.scrollTop=node.scrollHeight-previousHeight+previousTop})
  }catch(e){
    if(ticket.valid())streamError.value=(e as Error).message||'更早的消息加载失败，请稍后重试。'
  }finally{loadingEarlier.value=false}
}

function extractUserMessage(payload:{userMessage?:unknown;message?:unknown;text?:string}|unknown):TavernMessage|null{
  if(!payload||typeof payload!=='object')return null
  const envelope=payload as {userMessage?:unknown;message?:unknown}
  const value=envelope.userMessage??envelope.message??payload
  if(!value||typeof value!=='object')return null
  const message=value as Partial<TavernMessage>
  if(!message.id||message.role!=='user')return null
  return message as TavernMessage
}

async function runStream(path:string,body:unknown){
  if(streaming.value)return
  streaming.value=true;streamText.value='';streamError.value=''
  abort=new AbortController()
  const controller=abort
  scrollToBottom()
  try{
    await streamChat(path,body,{
      onMessage(payload){
        const message=extractUserMessage(payload)
        if(message&&!messages.value.some(item=>item.id===message.id)){
          messages.value=[...messages.value,message]
          scrollToBottom()
        }
      },
      onDelta(text){
        streamText.value+=text
        scrollToBottom()
      },
      onDone(){
        void finishStream()
      },
      onError(problem){
        streamError.value=problem.message||'生成失败，请重试。'
      },
    },{signal:controller.signal})
  }catch(e){
    if((e as Error)?.name==='AbortError')return
    streamError.value=(e as Error).message||'生成失败，请重试。'
  }finally{
    if(abort===controller)abort=null
    if(streaming.value){
      streaming.value=false
      streamText.value=''
      if(mounted)void load(true)
    }
  }
}

async function finishStream(){
  streaming.value=false
  streamText.value=''
  if(mounted)await load(true)
  scrollToBottom()
}

function send(){
  const text=input.value.trim()
  if(!text||streaming.value)return
  input.value=''
  void runStream('/chats/'+encodeURIComponent(chatId.value)+'/messages',{text})
}

function stop(){
  if(!abort)return
  abort.abort()
  abort=null
  streaming.value=false
  const partial=streamText.value
  streamText.value=''
  store.message(partial?'已停止生成，已收到的内容不会保存。':'已停止生成。')
  if(mounted)void load(true)
  scrollToBottom()
}

function regenerate(){
  if(!canRegenerate.value)return
  void runStream('/chats/'+encodeURIComponent(chatId.value)+'/regenerate',{})
}

function startEdit(message:TavernMessage){
  if(streaming.value)return
  editingId.value=message.id
  editText.value=messageText(message)
}

async function saveEdit(message:TavernMessage){
  if(savingEdit.value||!editingId.value)return
  if(!editText.value.trim()){streamError.value='消息内容不能为空。';return}
  savingEdit.value=true;streamError.value=''
  try{
    await editMessage(chatId.value,message.id,editText.value.trim())
    editingId.value=''
    await load(true)
  }catch(e){
    streamError.value=(e as Error).message||'编辑失败，请稍后重试。'
  }finally{savingEdit.value=false}
}

async function moveSwipe(message:TavernMessage,delta:number){
  if(swipeBusy.value||streaming.value)return
  const target=(message.activeSwipe??0)+delta
  if(target<0||target>=(message.swipes?.length||0))return
  swipeBusy.value=true
  try{
    await selectSwipe(chatId.value,message.id,target)
    await load(true)
  }catch(e){
    streamError.value=(e as Error).message||'切换版本失败，请稍后重试。'
  }finally{swipeBusy.value=false}
}

function askDelete(message:TavernMessage){if(streaming.value)return;deletingId.value=message.id}
async function confirmDelete(message:TavernMessage){
  if(deletingId.value!==message.id)return
  try{
    await deleteMessage(chatId.value,message.id)
    deletingId.value=''
    await load(true)
  }catch(e){streamError.value=(e as Error).message||'删除消息失败，请稍后重试。'}
}
function openSampling(){
  const value=detail.value?.chat.sampling
  sampling.value={temperature:value?.temperature??0.8,topP:value?.topP??0.95,maxTokens:value?.maxTokens??1024,stopText:(value?.stop||[]).join('\n')}
  samplingError.value='';samplingOpen.value=true;exportMenuOpen.value=false
}
async function saveSampling(){
  if(savingSampling.value)return
  const value=sampling.value
  if(value.temperature<0||value.temperature>2){samplingError.value='temperature 需要在 0 到 2 之间。';return}
  if(value.topP<0||value.topP>1){samplingError.value='top_p 需要在 0 到 1 之间。';return}
  if(value.maxTokens<16||value.maxTokens>8192){samplingError.value='max_tokens 需要在 16 到 8192 之间。';return}
  savingSampling.value=true;samplingError.value=''
  try{
    const chat=await updateChatSampling(chatId.value,{temperature:value.temperature,topP:value.topP,maxTokens:value.maxTokens,stop:value.stopText.split(/\n/).map(item=>item.trim()).filter(Boolean).slice(0,8)})
    if(detail.value)detail.value.chat={...detail.value.chat,...chat}
    samplingOpen.value=false
    store.message('采样参数已保存。')
  }catch(e){
    samplingError.value=(e as Error).message||'保存采样参数失败，服务端可能尚未支持聊天级采样。'
  }finally{savingSampling.value=false}
}
async function exportChat(format:'jsonl'|'json'){
  if(exporting.value)return
  exporting.value=format;exportMenuOpen.value=false;streamError.value=''
  try{
    const blob=await exportChatBlob(chatId.value,format)
    const suffix=format==='jsonl'?'.jsonl':'.json'
    saveBlob(blob,safeFileName(characterName.value,'chat')+suffix)
    store.message(format==='jsonl'?'聊天已导出为 JSONL。':'聊天已导出为 JSON。')
  }catch(e){streamError.value=(e as Error).message||'导出聊天失败，请稍后重试。'}
  finally{exporting.value=''}
}

function startTitleEdit(){if(!detail.value)return;titleEditing.value=true;titleText.value=detail.value.chat.title||''}
async function saveTitle(){
  const title=titleText.value.trim()
  if(!title||!detail.value){titleEditing.value=false;return}
  try{
    const chat=await updateChat(chatId.value,title)
    if(detail.value)detail.value.chat={...detail.value.chat,...chat}
    titleEditing.value=false
  }catch(e){streamError.value=(e as Error).message||'重命名失败，请稍后重试。'}
}

function goBack(){
  if(detail.value?.chat.cardId)void router.push('/characters/'+encodeURIComponent(detail.value.chat.cardId))
  else void router.push('/')
}

watch(chatId,()=>void load())
onMounted(()=>void load())
onUnmounted(()=>{mounted=false;abort?.abort();abort=null})
</script>

<template>
 <section class="tavern-chat" aria-label="聊天">
  <header class="chat-top">
   <button type="button" class="chat-icon" aria-label="返回角色卡" @click="goBack"><ArrowLeft :size="20"/></button>
   <div class="chat-title">
    <button v-if="!titleEditing" type="button" class="chat-name" @click="startTitleEdit">{{characterName}}</button>
    <form v-else class="chat-title-form" @submit.prevent="saveTitle">
     <input v-model="titleText" aria-label="聊天名称" maxlength="120">
     <button type="submit">保存</button>
    </form>
   </div>
   <div class="chat-menu">
    <button type="button" class="chat-icon" aria-label="聊天菜单" @click="exportMenuOpen=!exportMenuOpen"><Download :size="19"/></button>
    <div v-if="exportMenuOpen" class="chat-menu-pop" role="menu">
     <button type="button" role="menuitem" :disabled="!!exporting" @click="exportChat('jsonl')">{{exporting==='jsonl'?'导出中…':'导出聊天 (JSONL)'}}</button>
     <button type="button" role="menuitem" :disabled="!!exporting" @click="exportChat('json')">{{exporting==='json'?'导出中…':'导出聊天 (JSON)'}}</button>
     <button type="button" role="menuitem" @click="openSampling">采样参数</button>
    </div>
   </div>
   <span class="chat-status" role="status">{{streaming?'正在回复…':(streamError?'出错':'')}}</span>
  </header>

  <p v-if="error" class="error" role="alert">{{error}}<button class="inline-link" type="button" @click="load()">重试</button></p>
  <p v-if="loading" class="chat-loading" role="status">正在加载聊天…</p>

  <div v-else ref="log" class="chat-log" aria-live="polite">
   <div v-if="hasMore" class="chat-load-earlier">
    <button type="button" :disabled="loadingEarlier" @click="loadEarlier">{{loadingEarlier?'加载中…':'加载更早消息'}}</button>
   </div>
   <p v-if="!messages.length&&!streaming" class="chat-empty">还没有消息，说点什么开始吧。</p>
   <article v-for="message in messages" :key="message.id" class="bubble-row" :class="message.role">
    <div class="bubble" :class="message.role">
     <p v-if="message.role==='user'" class="bubble-meta">你</p>
     <template v-if="editingId===message.id">
      <textarea v-model="editText" rows="3" aria-label="编辑消息"></textarea>
      <div class="bubble-actions">
       <button type="button" class="primary" :disabled="savingEdit" @click="saveEdit(message)">{{savingEdit?'保存中…':'保存'}}</button>
       <button type="button" @click="editingId=''">取消</button>
      </div>
     </template>
     <template v-else>
      <p class="bubble-text">{{messageText(message)}}</p>
      <p v-if="message.createdAt" class="bubble-time">{{new Date(message.createdAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}}</p>
     </template>
    </div>
    <div v-if="editingId!==message.id&&!streaming" class="bubble-actions">
     <template v-if="deletingId===message.id">
      <span class="bubble-confirm">删除这条消息？</span>
      <button type="button" class="danger-text" aria-label="确认删除消息" @click="confirmDelete(message)">确认删除</button>
      <button type="button" @click="deletingId=''">取消</button>
     </template>
     <template v-else>
      <template v-if="message.role==='user'">
       <button type="button" aria-label="编辑这条消息" @click="startEdit(message)"><Pencil :size="15"/>编辑</button>
      </template>
      <template v-else-if="message.id===messages[messages.length-1]?.id">
       <span v-if="(message.swipes?.length||0)>1" class="swipe-controls">
        <button type="button" aria-label="上一个版本" :disabled="swipeBusy||(message.activeSwipe??0)<=0" @click="moveSwipe(message,-1)"><ChevronLeft :size="15"/></button>
        <span class="swipe-count">{{(message.activeSwipe??0)+1}}/{{message.swipes.length}}</span>
        <button type="button" aria-label="下一个版本" :disabled="swipeBusy||(message.activeSwipe??0)>=message.swipes.length-1" @click="moveSwipe(message,1)"><ChevronRight :size="15"/></button>
       </span>
       <button v-if="canRegenerate" type="button" @click="regenerate"><RefreshCw :size="15"/>重新生成</button>
      </template>
      <button v-if="message.role==='user'||message.id===messages[messages.length-1]?.id" type="button" aria-label="删除这条消息" @click="askDelete(message)"><Trash2 :size="15"/>删除</button>
     </template>
    </div>
   </article>
   <article v-if="streaming" class="bubble-row assistant">
    <div class="bubble assistant">
     <p v-if="streamText" class="bubble-text">{{streamText}}</p>
     <p v-else class="bubble-text bubble-pending">正在写…</p>
    </div>
   </article>
  </div>

  <p v-if="streamError" class="error chat-stream-error" role="alert">{{streamError}}<button class="inline-link" type="button" @click="streamError=''"><X :size="14"/></button></p>

  <Sheet v-if="samplingOpen" title="采样参数" @close="!savingSampling&&(samplingOpen=false)">
   <form class="sampling-form" @submit.prevent="saveSampling">
    <p class="muted">这些参数只影响当前聊天；留空/默认值表示沿用角色卡设置。</p>
    <p v-if="samplingError" class="error" role="alert">{{samplingError}}</p>
    <div class="form-pair">
     <label>temperature<input v-model.number="sampling.temperature" type="number" min="0" max="2" step="0.05" aria-label="temperature"></label>
     <label>top_p<input v-model.number="sampling.topP" type="number" min="0" max="1" step="0.01" aria-label="top_p"></label>
    </div>
    <label>max_tokens<input v-model.number="sampling.maxTokens" type="number" min="16" max="8192" aria-label="max_tokens"></label>
    <label>停止词（每行一个，最多 8 个）<textarea v-model="sampling.stopText" rows="3" aria-label="停止词"></textarea></label>
    <div class="form-actions">
     <button type="submit" class="primary" :disabled="savingSampling">{{savingSampling?'保存中…':'保存参数'}}</button>
     <button type="button" @click="samplingOpen=false">取消</button>
    </div>
   </form>
  </Sheet>

  <form class="chat-compose" @submit.prevent="send">
   <textarea v-model="input" rows="1" placeholder="说点什么…" aria-label="聊天输入框" :disabled="streaming" @keydown.enter.exact.prevent="send"></textarea>
   <button v-if="streaming" type="button" class="chat-stop" aria-label="停止生成" @click="stop"><Square :size="18"/>停止</button>
   <button v-else type="submit" class="chat-send" aria-label="发送消息" :disabled="!input.trim()"><Send :size="18"/>发送</button>
  </form>
 </section>
</template>

<style scoped>
.tavern-chat{display:flex;flex-direction:column;height:100dvh;background:var(--paper);color:var(--ink)}
.chat-top{display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:8px;min-height:56px;padding:6px 10px;padding-top:calc(6px + env(safe-area-inset-top));border-bottom:1px solid var(--edge);background:var(--paper);position:sticky;top:0;z-index:20}
.chat-icon{display:flex;align-items:center;justify-content:center;width:44px;height:44px;border:0;background:transparent;color:var(--quiet);border-radius:10px;cursor:pointer}
.chat-icon:hover{background:var(--wash);color:var(--ink)}
.chat-menu{position:relative}
.chat-menu-pop{position:absolute;right:0;top:46px;z-index:30;min-width:190px;padding:6px;border:1px solid var(--edge);border-radius:12px;background:var(--canvas);box-shadow:0 12px 32px rgba(0,0,0,.12);display:flex;flex-direction:column;gap:2px}
.chat-menu-pop button{min-height:40px;justify-content:flex-start;border:0;background:transparent;color:var(--ink);text-align:left;padding:0 10px;border-radius:8px;cursor:pointer}
.chat-menu-pop button:hover:not(:disabled){background:var(--wash)}
.chat-load-earlier{display:flex;justify-content:center}
.chat-load-earlier button{min-height:36px;border:1px solid var(--edge);background:var(--canvas);border-radius:999px;padding:0 14px;font-size:13px;color:var(--quiet);cursor:pointer}
.bubble-confirm{font-size:12px;color:var(--quiet)}
.bubble-actions .danger-text{color:#b23b3b}
.bubble-actions .danger-text:hover{background:rgba(178,59,59,.08)}
.sampling-form{display:flex;flex-direction:column;gap:12px}
.sampling-form label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--quiet)}
.sampling-form .form-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.sampling-form .form-actions{display:flex;gap:10px}
.chat-title{min-width:0;text-align:center}
.chat-name{max-width:100%;min-height:40px;padding:0 10px;border:0;background:transparent;font-family:var(--font-serif);font-size:17px;color:var(--ink);cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-title-form{display:flex;gap:6px;align-items:center;justify-content:center}
.chat-title-form input{min-height:38px;width:min(46vw,260px)}
.chat-title-form button{min-height:38px;padding:0 12px}
.chat-status{font-size:12px;color:var(--quiet);min-width:64px;text-align:right}
.chat-log{flex:1;min-height:0;overflow-y:auto;padding:18px clamp(12px,3vw,28px) 12px;display:flex;flex-direction:column;gap:14px;max-width:860px;width:100%;margin:0 auto}
.chat-loading{flex:1;display:flex;align-items:center;justify-content:center;color:var(--quiet)}
.chat-empty{color:var(--quiet);text-align:center;margin:auto}
.bubble-row{display:flex;flex-direction:column;gap:4px;max-width:84%}
.bubble-row.user{align-self:flex-end;align-items:flex-end}
.bubble-row.assistant{align-self:flex-start;align-items:flex-start}
.bubble{border-radius:16px;padding:10px 13px;line-height:1.65;font-size:15px;word-break:break-word;white-space:pre-wrap}
.bubble.user{background:var(--accent);color:#fff;border-bottom-right-radius:6px}
.bubble.assistant{background:var(--wash);color:var(--ink);border-bottom-left-radius:6px}
.bubble-text{margin:0}
.bubble-pending{color:var(--quiet);font-style:italic}
.bubble-meta{margin:0 0 2px;font-size:11px;opacity:.75}
.bubble-time{margin:4px 0 0;font-size:10px;opacity:.55}
.bubble-actions{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--quiet)}
.bubble-actions button{display:flex;align-items:center;gap:4px;min-height:32px;padding:0 8px;border:0;background:transparent;color:var(--quiet);font-size:12px;border-radius:8px;cursor:pointer}
.bubble-actions button:hover{background:var(--wash);color:var(--ink)}
.bubble textarea{width:100%;min-width:240px;max-width:100%}
.swipe-controls{display:inline-flex;align-items:center;gap:2px}
.swipe-count{font-size:12px;min-width:34px;text-align:center}
.chat-stream-error{margin:0 auto;max-width:860px;width:100%}
.chat-compose{position:sticky;bottom:0;display:flex;gap:8px;align-items:flex-end;padding:10px clamp(12px,3vw,24px);padding-bottom:calc(10px + env(safe-area-inset-bottom));border-top:1px solid var(--edge);background:var(--paper);max-width:860px;width:100%;margin:0 auto}
.chat-compose textarea{flex:1;min-height:46px;max-height:160px;resize:none;padding:11px 12px;font-size:15px;line-height:1.5}
.chat-send,.chat-stop{display:flex;align-items:center;gap:6px;min-height:46px;padding:0 16px;border-radius:12px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer;white-space:nowrap}
.chat-stop{background:var(--canvas);color:var(--ink);border-color:var(--edge)}
.chat-send:disabled{opacity:.45;cursor:not-allowed}
.inline-link{min-height:0;padding:0 6px;border:0;background:transparent;color:var(--accent);text-decoration:underline;cursor:pointer;display:inline-flex;align-items:center}
@media(min-width:900px){.chat-compose{position:sticky}.tavern-chat{max-width:900px;margin:0 auto;width:100%}}
</style>
