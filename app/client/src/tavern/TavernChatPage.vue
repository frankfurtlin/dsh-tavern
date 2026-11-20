<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowDown,ArrowLeft,Check,ChevronDown,ChevronLeft,ChevronRight,ChevronUp,Copy,Download,HelpCircle,Pencil,Quote,RefreshCw,Search,Send,Square,Trash2,X} from 'lucide-vue-next'
import {authorizationHeaders} from '../api'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import {deleteMessage,editMessage,exportChatBlob,getCharacter,getChat,listCharacters,messageText,selectSwipe,sendMessage,tavernAvatarUrl,updateChat,updateChatMembers,updateChatSampling,type ChatDetail,TavernCharacter,TavernMessage} from './api'
import {saveBlob,safeFileName} from '../platform/download'
import {streamChat} from './sse'
import Sheet from '../components/Sheet.vue'
import {clearChatContextSnapshot,patchChatContextSnapshot,setChatContextSnapshot} from './context-snapshot'
import {keyboardBottomOffset} from './mobile'
import {buildChatRows,splitSegments} from './chat-format'

const route=useRoute(),router=useRouter(),store=useApp(),scope=useRequestScope()
const chatId=computed(()=>String(route.params.id||''))
const detail=ref<ChatDetail|null>(null)
const messages=ref<TavernMessage[]>([])
const character=ref<TavernCharacter|null>(null)
const groupAvatars=ref<Record<string,string>>({})
const streamSpeaker=ref<{cardId:string;name:string}|null>(null)
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
const searchOpen=ref(false)
const searchQuery=ref('')
const searchIndex=ref(0)
const searchHitId=ref('')
const searchInput=ref<HTMLInputElement|null>(null)
const quote=ref<{text:string;messageId:string}|null>(null)
const quotePreview=computed(()=>quote.value?quote.value.text.slice(0,120):'')
const keyboardOffset=ref(0)
let quoteLongPressTimer:ReturnType<typeof setTimeout>|undefined
let quotePulseTimer:ReturnType<typeof setTimeout>|undefined
let searchPulseTimer:ReturnType<typeof setTimeout>|undefined
const membersOpen=ref(false)
const memberCards=ref<TavernCharacter[]>([])
const memberSelection=ref<string[]>([])
const membersLoading=ref(false)
const membersError=ref('')
const savingMembers=ref(false)
const memberCountLabel=computed(()=>`${memberSelection.value.length}/5`)
const memberSelectionValid=computed(()=>memberSelection.value.length>=2&&memberSelection.value.length<=5)
const membersChanged=computed(()=>{
  const current=aliveMembers.value.map(member=>member.cardId)
  return memberSelection.value.length!==current.length||memberSelection.value.some((cardId,index)=>cardId!==current[index])
})
const canSaveMembers=computed(()=>memberSelectionValid.value&&membersChanged.value&&!streaming.value&&!savingMembers.value)
let abort:AbortController|null=null
let mounted=true
const PAGE_SIZE=200

const characterName=computed(()=>detail.value?.chat.characterName||detail.value?.chat.title||'聊天')
const groupMembers=computed(()=>detail.value?.chat.mode==='group'?(detail.value.chat.members??[]).slice().sort((a,b)=>(a.order??0)-(b.order??0)).slice(0,5):[])
const aliveMembers=computed(()=>groupMembers.value.filter(member=>!member.deleted))
const deletedMembers=computed(()=>groupMembers.value.filter(member=>member.deleted))
const isGroup=computed(()=>detail.value?.chat.mode==='group')
const rows=computed(()=>buildChatRows(messages.value,{isGroup:isGroup.value}))
const atBottom=ref(true)
const unread=ref(false)
const showJumpToLatest=computed(()=>!atBottom.value&&unread.value)
function segmentsFor(value:string){return splitSegments(value)}
const assistantCount=computed(()=>messages.value.filter(message=>message.role==='assistant').length)
const allMembersDeleted=computed(()=>{
  if(!isGroup.value)return false
  const count=detail.value?.chat.aliveMemberCount
  if(typeof count==='number')return count===0
  // 旧数据没有 members/aliveMemberCount 时不要误判为全删，交给服务端校验。
  return groupMembers.value.length>0&&aliveMembers.value.length===0
})
const nextSpeaker=computed(()=>{
  const members=aliveMembers.value
  if(!members.length)return null
  return members[assistantCount.value%members.length]??null
})
const exampleChips=['你好','继续','我来了'] as const
const showChips=computed(()=>!streaming.value&&!allMembersDeleted.value&&!input.value.trim()&&messages.value.length>0&&!messages.value.some(message=>message.role==='user'))
function useChip(text:string){input.value=text}
const canRegenerate=computed(()=>!streaming.value&&!allMembersDeleted.value&&messages.value.length>0&&messages.value[messages.value.length-1]?.role==='assistant')
const searchHits=computed<TavernMessage[]>(()=>{
  const query=searchQuery.value.trim().toLowerCase()
  if(!query)return []
  return messages.value.filter(message=>messageText(message).toLowerCase().includes(query))
})
const searchCountLabel=computed(()=>{
  if(!searchQuery.value.trim())return ''
  if(!searchHits.value.length)return '没有匹配'
  return `${searchIndex.value+1}/${searchHits.value.length}`
})

function toggleSearch(){
  if(searchOpen.value){closeSearch();return}
  searchOpen.value=true
  void nextTick(()=>searchInput.value?.focus?.())
}

function closeSearch(){
  searchOpen.value=false
  searchQuery.value=''
  searchIndex.value=0
  searchHitId.value=''
  if(searchPulseTimer!==undefined){clearTimeout(searchPulseTimer);searchPulseTimer=undefined}
}

function scrollToSearchHit(id:string){
  const root=log.value as (HTMLElement&{querySelector?:(selector:string)=>Element|null})|null
  const target=root?.querySelector?.(`[data-message-id="${id}"]`)
  target?.scrollIntoView?.({block:'center',behavior:'smooth'})
}

function stepSearch(delta:number){
  const hits=searchHits.value
  if(!hits.length)return
  searchIndex.value=(searchIndex.value+delta+hits.length)%hits.length
  const target=hits[searchIndex.value]!
  searchHitId.value=target.id
  if(searchPulseTimer!==undefined)clearTimeout(searchPulseTimer)
  searchPulseTimer=setTimeout(()=>{searchHitId.value='';searchPulseTimer=undefined},1500)
  scrollToSearchHit(target.id)
}

watch(searchQuery,()=>{searchIndex.value=0;searchHitId.value=''})

function syncKeyboardOffset(){
  if(typeof window==='undefined')return
  const viewport=window.visualViewport
  keyboardOffset.value=viewport?keyboardBottomOffset(window.innerHeight,viewport.height):0
  scrollToBottom()
}

function openMembers(){
  if(streaming.value)return
  membersOpen.value=true
  membersError.value=''
  memberSelection.value=aliveMembers.value.map(member=>member.cardId)
  void loadMemberCards()
}

async function loadMemberCards(){
  membersLoading.value=true
  const ticket=scope()
  try{
    const cards=await listCharacters()
    if(!ticket.valid())return
    memberCards.value=cards
    for(const card of cards)void loadAvatar(card.id)
  }catch(e){
    if(ticket.valid())membersError.value=(e as Error).message||'角色卡列表加载失败，请稍后重试。'
  }finally{
    if(ticket.valid())membersLoading.value=false
  }
}

function toggleMember(cardId:string){
  if(memberSelection.value.includes(cardId)){
    memberSelection.value=memberSelection.value.filter(id=>id!==cardId)
    return
  }
  if(memberSelection.value.length>=5){store.message('群聊最多选择 5 位角色。');return}
  memberSelection.value=[...memberSelection.value,cardId]
}

function closeMembers(){
  if(savingMembers.value)return
  membersOpen.value=false
  membersError.value=''
}

async function saveMembers(){
  if(!canSaveMembers.value||!detail.value)return
  savingMembers.value=true
  membersError.value=''
  try{
    await updateChatMembers(detail.value.chat.id,memberSelection.value)
    membersOpen.value=false
    store.message('成员已更新。')
    await load(true)
  }catch(e){
    membersError.value=(e as Error).message||'成员更新失败，请稍后重试。'
  }finally{
    savingMembers.value=false
  }
}

function avatarFor(cardId?:string|null){
  return cardId?groupAvatars.value[cardId]??'':('')
}

function speakerNameFor(message:TavernMessage){
  const member=groupMembers.value.find(item=>item.cardId===message.speakerCardId)
  const name=message.speakerName||member?.name||''
  return member?.deleted&&name?name+'（已删除）':name
}

function memberTitle(member:{name:string;deleted?:boolean}){
  return member.deleted?member.name+'（已删除）':member.name
}

function speakerAvatarFor(message:TavernMessage){
  return avatarFor(message.speakerCardId)
}

async function loadAvatar(cardId:string){
  if(!cardId||groupAvatars.value[cardId])return
  try{
    const response=await fetch(tavernAvatarUrl(cardId),{credentials:'include',headers:authorizationHeaders()})
    if(!response.ok)return
    const blob=await response.blob()
    if(!mounted)return
    groupAvatars.value={...groupAvatars.value,[cardId]:URL.createObjectURL(blob)}
  }catch{/* 头像失败不影响聊天 */}
}

function ensureGroupAvatars(){
  if(!isGroup.value)return
  for(const member of aliveMembers.value)void loadAvatar(member.cardId)
  for(const message of messages.value)if(message.speakerCardId&&!deletedMembers.value.some(member=>member.cardId===message.speakerCardId))void loadAvatar(message.speakerCardId)
}

function scrollToBottom(){void nextTick(()=>{if(log.value){log.value.scrollTop=log.value.scrollHeight;atBottom.value=true;unread.value=false}})}
function onLogScroll(){
  const node=log.value
  if(!node)return
  const nearBottom=node.scrollTop+node.clientHeight>=node.scrollHeight-80
  atBottom.value=nearBottom
  if(nearBottom)unread.value=false
}
function jumpToLatest(){unread.value=false;scrollToBottom()}
function markUnreadIfDetached(){if(!atBottom.value)unread.value=true}
watch([()=>messages.value.length,streaming],markUnreadIfDetached)
watch(streamText,markUnreadIfDetached)
async function copyMessage(message:TavernMessage){
  try{await navigator.clipboard?.writeText(messageText(message));store.message('已复制这条消息。')}
  catch{store.message('复制失败，请手动选择文本。')}
}

/** Publishes what the DSH panel needs to continue this chat (see dsh-context.ts). */
function publishSnapshot(loaded=false){
  const chat=detail.value?.chat
  if(!chat)return
  const recent=messages.value
    .map(message=>({role:message.role,text:messageText(message)}))
    .filter(message=>message.text)
  // Single chats use the card name. Group chats use whoever this turn belongs to
  // (last assistant's speaker, else the next scheduled speaker) so the DSH tools
  // write "请以<发言人名>...", not the joined group title.
  const lastAssistant=[...messages.value].reverse().find(message=>message.role==='assistant')
  const contextSpeaker=chat.mode==='group'
    ?((lastAssistant?speakerNameFor(lastAssistant):'')||nextSpeaker.value?.name||'')
    :''
  setChatContextSnapshot({
    chatId:chat.id,
    cardId:chat.cardId,
    characterName:chat.characterName||contextSpeaker||character.value?.name||chat.title||'',
    character:character.value,
    messages:recent,
    loaded:loaded||!!detail.value,
  })
}

async function loadCharacter(cardId:string){
  if(!cardId)return
  try{
    const card=await getCharacter(cardId)
    character.value=card
    if(detail.value?.chat)patchChatContextSnapshot(detail.value.chat.id,{character:card})
  }catch{/* The card is optional context; the chat itself still works. */}
}

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
    publishSnapshot(true)
    ensureGroupAvatars()
    void loadCharacter(value.chat.cardId)
    scrollToBottom()
  }catch(e){
    if(!ticket.valid())return
    // 记住的路由可能指向已删除的聊天：不要停在错误页，清掉记忆并回到卡库。
    if((e as {status?:number}).status===404){
      try{window.parent?.postMessage({type:'dsh-tavern:route-reset'},window.location.origin)}catch{/* 非 iframe 环境忽略 */}
      try{window.sessionStorage.removeItem('dsh-tavern:last-app-path')}catch{/* 隐私模式忽略 */}
      store.message('这段对话不存在或已被删除，已回到角色卡。')
      void router.replace('/')
      return
    }
    error.value=(e as Error).message||'聊天加载失败，请稍后重试。'
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
  if(!text||streaming.value||allMembersDeleted.value)return
  const request=sendMessage(chatId.value,text,quote.value)
  input.value=''
  quote.value=null
  streamSpeaker.value=nextSpeaker.value
  void runStream(request.path,request.body)
}

function quoteMessage(message:TavernMessage){
  if(streaming.value||allMembersDeleted.value||editingId.value===message.id)return
  quote.value={text:messageText(message).slice(0,2000),messageId:message.id}
  const field=typeof document!=='undefined'&&typeof document.getElementById==='function'?document.getElementById('chat-input') as HTMLTextAreaElement|null:null
  field?.focus?.()
}

function clearQuote(){quote.value=null}

function startQuoteLongPress(message:TavernMessage){
  cancelQuoteLongPress()
  quoteLongPressTimer=setTimeout(()=>{quoteLongPressTimer=undefined;quoteMessage(message)},500)
}

function cancelQuoteLongPress(){
  if(quoteLongPressTimer!==undefined){clearTimeout(quoteLongPressTimer);quoteLongPressTimer=undefined}
}

function jumpToQuote(text:string){
  const needle=text.trim().slice(0,40)
  const target=messages.value.find(message=>needle&&messageText(message).includes(needle))
  if(!target)return
  searchHitId.value=target.id
  if(quotePulseTimer!==undefined)clearTimeout(quotePulseTimer)
  quotePulseTimer=setTimeout(()=>{searchHitId.value=''},1500)
  if(typeof document!=='undefined'&&typeof document.querySelector==='function')document.querySelector('[data-message-id="'+target.id+'"]')?.scrollIntoView?.({block:'center',behavior:'smooth'})
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
  const lastAssistant=[...messages.value].reverse().find(item=>item.role==='assistant')
  streamSpeaker.value=lastAssistant?{cardId:lastAssistant.speakerCardId||'',name:lastAssistant.speakerName||''}:null
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
watch([messages,character],()=>{publishSnapshot();ensureGroupAvatars()})
function onGlobalKeydown(event:KeyboardEvent){
  if(event.key==='Escape'&&quote.value)quote.value=null
}

onMounted(()=>{
  void load()
  if(typeof window!=='undefined')window.addEventListener?.('keydown',onGlobalKeydown)
  if(typeof window!=='undefined'&&window.visualViewport){
    window.visualViewport.addEventListener('resize',syncKeyboardOffset)
    window.visualViewport.addEventListener('scroll',syncKeyboardOffset)
    syncKeyboardOffset()
  }
})
onUnmounted(()=>{
  mounted=false
  if(typeof window!=='undefined')window.removeEventListener?.('keydown',onGlobalKeydown)
  if(quoteLongPressTimer!==undefined){clearTimeout(quoteLongPressTimer);quoteLongPressTimer=undefined}
  if(quotePulseTimer!==undefined){clearTimeout(quotePulseTimer);quotePulseTimer=undefined}
  if(typeof window!=='undefined'&&window.visualViewport){
    window.visualViewport.removeEventListener('resize',syncKeyboardOffset)
    window.visualViewport.removeEventListener('scroll',syncKeyboardOffset)
  }
  if(searchPulseTimer!==undefined){clearTimeout(searchPulseTimer);searchPulseTimer=undefined}
  clearChatContextSnapshot(chatId.value)
  abort?.abort()
  abort=null
  for(const url of Object.values(groupAvatars.value))URL.revokeObjectURL(url)
  groupAvatars.value={}
})
</script>

<template>
 <section class="tavern-chat" aria-label="聊天" :style="{'--chat-keyboard-offset':keyboardOffset+'px'}">
  <header class="chat-top">
   <button type="button" class="chat-icon" aria-label="返回角色卡" @click="goBack"><ArrowLeft :size="20"/></button>
   <div class="chat-title">
    <button v-if="!titleEditing" type="button" class="chat-name" @click="startTitleEdit">{{characterName}}</button>
    <form v-else class="chat-title-form" @submit.prevent="saveTitle">
     <input v-model="titleText" aria-label="聊天名称" maxlength="120">
     <button type="submit">保存</button>
    </form>
    <div v-if="isGroup&&groupMembers.length" class="chat-members" aria-label="群聊参与者">
     <span v-for="member in groupMembers" :key="member.cardId" class="chat-member-avatar" :class="{deleted:member.deleted}" :title="memberTitle(member)" :aria-label="memberTitle(member)">
      <img v-if="avatarFor(member.cardId)" :src="avatarFor(member.cardId)" alt="">
      <span v-else>{{member.name.slice(0,1)}}</span>
     </span>
    </div>
   </div>
   <div class="chat-menu">
    <button type="button" class="chat-icon" aria-label="使用帮助" title="使用帮助" @click="router.push('/settings/help')"><HelpCircle :size="19"/></button>
    <button type="button" class="chat-icon" aria-label="搜索聊天内容" :aria-expanded="searchOpen" @click="toggleSearch"><Search :size="19"/></button>
    <button type="button" class="chat-icon" aria-label="聊天菜单" @click="exportMenuOpen=!exportMenuOpen"><Download :size="19"/></button>
    <div v-if="exportMenuOpen" class="chat-menu-pop" role="menu">
     <button type="button" role="menuitem" :disabled="!!exporting" @click="exportChat('jsonl')">{{exporting==='jsonl'?'导出中…':'导出聊天 (JSONL)'}}</button>
     <button type="button" role="menuitem" :disabled="!!exporting" @click="exportChat('json')">{{exporting==='json'?'导出中…':'导出聊天 (JSON)'}}</button>
     <button v-if="isGroup" type="button" role="menuitem" :disabled="streaming" :title="streaming?'正在生成回复，暂不能管理成员':''" @click="exportMenuOpen=false;openMembers()">管理成员</button>
     <button type="button" role="menuitem" @click="openSampling">采样参数</button>
    </div>
   </div>
   <span class="chat-status" role="status">{{streaming?'正在回复…':(streamError?'出错':'')}}</span>
  </header>

  <div v-if="searchOpen" class="chat-search" role="search">
   <input ref="searchInput" v-model="searchQuery" type="search" placeholder="搜索聊天内容" aria-label="搜索聊天内容输入" @keydown.esc.stop.prevent="closeSearch">
   <span class="chat-search-count" role="status">{{searchCountLabel}}</span>
   <button type="button" class="chat-search-step" aria-label="上一个匹配" :disabled="!searchHits.length" @click="stepSearch(-1)"><ChevronUp :size="16"/>上一个</button>
   <button type="button" class="chat-search-step" aria-label="下一个匹配" :disabled="!searchHits.length" @click="stepSearch(1)"><ChevronDown :size="16"/>下一个</button>
   <button type="button" class="chat-search-step" aria-label="关闭搜索" @click="closeSearch"><X :size="16"/>关闭</button>
   <small class="chat-search-hint">仅搜索已加载消息</small>
  </div>

  <p v-if="allMembersDeleted" class="chat-readonly" role="status">群聊成员已全部删除，无法继续发言</p>
  <p v-if="error" class="error" role="alert">{{error}}<button class="inline-link" type="button" @click="load()">重试</button></p>
  <p v-if="loading" class="chat-loading" role="status">正在加载聊天…</p>

  <div v-else ref="log" class="chat-log" aria-live="polite" @scroll.passive="onLogScroll">
   <div v-if="hasMore" class="chat-load-earlier">
    <button type="button" :disabled="loadingEarlier" @click="loadEarlier">{{loadingEarlier?'加载中…':'加载更早消息'}}</button>
   </div>
   <p v-if="!messages.length&&!streaming" class="chat-empty">还没有消息，说点什么开始吧。</p>
   <template v-for="row in rows" :key="row.key">
    <div v-if="row.kind==='day'" class="chat-day" role="separator"><span>{{row.label}}</span></div>
    <article v-else :data-message-id="row.message.id" class="bubble-row" :class="[row.message.role,{compact:row.compact,'chat-search-hit':searchHitId===row.message.id}]">
     <div v-if="row.showSpeaker" class="bubble-speaker">
      <span class="bubble-speaker-avatar">
       <img v-if="speakerAvatarFor(row.message)" :src="speakerAvatarFor(row.message)" alt="">
       <span v-else>{{speakerNameFor(row.message).slice(0,1)}}</span>
      </span>
      <span class="bubble-speaker-name">{{speakerNameFor(row.message)}}</span>
     </div>
     <div class="bubble" :class="row.message.role" :title="row.time||undefined"
      @touchstart.passive="startQuoteLongPress(row.message)" @touchend="cancelQuoteLongPress" @touchmove="cancelQuoteLongPress">
      <button v-if="row.message.quotedText&&editingId!==row.message.id" type="button" class="bubble-quote" aria-label="查看被引用的消息" @click="jumpToQuote(row.message.quotedText||'')">
       <Quote :size="13"/><span class="bubble-quote-text">{{row.message.quotedText}}</span>
      </button>
      <template v-if="editingId===row.message.id">
       <textarea v-model="editText" rows="3" aria-label="编辑消息"></textarea>
       <div class="bubble-actions editing-actions">
        <button type="button" class="primary" :disabled="savingEdit" @click="saveEdit(row.message)">{{savingEdit?'保存中…':'保存'}}</button>
        <button type="button" @click="editingId=''">取消</button>
       </div>
      </template>
      <template v-else>
       <p class="bubble-text"><template v-for="(seg,si) in segmentsFor(messageText(row.message))" :key="si"><span v-if="seg.kind==='act'" class="act">{{seg.text}}</span><strong v-else-if="seg.kind==='strong'" class="strong">{{seg.text}}</strong><template v-else>{{seg.text}}</template></template></p>
      </template>
     </div>
     <div v-if="editingId!==row.message.id&&!streaming" class="bubble-actions">
      <template v-if="deletingId===row.message.id">
       <span class="bubble-confirm">删除这条消息？</span>
       <button type="button" class="danger-text" aria-label="确认删除消息" title="确认删除" @click="confirmDelete(row.message)"><Check :size="16"/></button>
       <button type="button" aria-label="取消删除" title="取消" @click="deletingId=''"><X :size="16"/></button>
      </template>
      <template v-else>
       <button type="button" aria-label="引用这条消息" title="引用" @click="quoteMessage(row.message)"><Quote :size="16"/></button>
       <button type="button" aria-label="复制这条消息" title="复制" @click="copyMessage(row.message)"><Copy :size="16"/></button>
       <template v-if="row.message.role==='user'">
        <button type="button" aria-label="编辑这条消息" title="编辑" @click="startEdit(row.message)"><Pencil :size="16"/></button>
       </template>
       <template v-else-if="row.message.id===messages[messages.length-1]?.id">
        <span v-if="(row.message.swipes?.length||0)>1" class="swipe-controls">
         <button type="button" aria-label="上一个版本" :disabled="swipeBusy||(row.message.activeSwipe??0)<=0" @click="moveSwipe(row.message,-1)"><ChevronLeft :size="15"/></button>
         <span class="swipe-count">{{(row.message.activeSwipe??0)+1}}/{{row.message.swipes.length}}</span>
         <button type="button" aria-label="下一个版本" :disabled="swipeBusy||(row.message.activeSwipe??0)>=row.message.swipes.length-1" @click="moveSwipe(row.message,1)"><ChevronRight :size="15"/></button>
        </span>
        <button v-if="canRegenerate" type="button" aria-label="重新生成" title="重新生成" @click="regenerate"><RefreshCw :size="16"/></button>
       </template>
       <button v-if="row.message.role==='user'||row.message.id===messages[messages.length-1]?.id" type="button" class="danger-text" aria-label="删除这条消息" title="删除" @click="askDelete(row.message)"><Trash2 :size="16"/></button>
      </template>
     </div>
    </article>
   </template>
   <article v-if="streaming" class="bubble-row assistant">
    <div v-if="isGroup&&streamSpeaker" class="bubble-speaker">
     <span class="bubble-speaker-avatar">
      <img v-if="avatarFor(streamSpeaker.cardId)" :src="avatarFor(streamSpeaker.cardId)" alt="">
      <span v-else>{{streamSpeaker.name.slice(0,1)}}</span>
     </span>
     <span class="bubble-speaker-name">{{streamSpeaker.name}}</span>
    </div>
    <div class="bubble assistant">
     <p class="bubble-text"><template v-for="(seg,si) in segmentsFor(streamText)" :key="si"><span v-if="seg.kind==='act'" class="act">{{seg.text}}</span><strong v-else-if="seg.kind==='strong'" class="strong">{{seg.text}}</strong><template v-else>{{seg.text}}</template></template><span v-if="!streamText" class="bubble-pending">正在回复…</span><span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span></p>
    </div>
   </article>
  </div>

  <button v-if="showJumpToLatest" type="button" class="chat-new" aria-label="有新消息，跳到最新" @click="jumpToLatest"><ArrowDown :size="16"/>新消息</button>

  <p v-if="streamError" class="error chat-stream-error" role="alert">{{streamError}}<button class="inline-link" type="button" @click="streamError=''"><X :size="14"/></button></p>

  <Sheet v-if="membersOpen" title="管理成员" @close="closeMembers">
   <div class="member-manager" role="dialog" aria-label="管理群聊成员">
    <p class="muted">群聊需要 2 至 5 位角色，当前已选 {{memberCountLabel}}。</p>
    <p v-if="membersError" class="error" role="alert">{{membersError}}</p>
    <p v-if="membersLoading" class="muted" role="status">正在加载角色卡…</p>
    <ul v-else class="member-pick-list">
     <li v-for="card in memberCards" :key="card.id">
      <label class="member-pick-row" :class="{selected:memberSelection.includes(card.id)}">
       <input type="checkbox" :checked="memberSelection.includes(card.id)" :aria-label="'选择 '+card.name" @change="toggleMember(card.id)">
       <span class="member-pick-avatar">
        <img v-if="avatarFor(card.id)" :src="avatarFor(card.id)" alt="">
        <span v-else>{{card.name.slice(0,1)}}</span>
       </span>
       <span class="member-pick-name">{{card.name}}</span>
      </label>
     </li>
    </ul>
    <div v-if="deletedMembers.length" class="member-deleted">
     <p class="muted">已删除成员（可选择上方角色卡替换）：</p>
     <p v-for="member in deletedMembers" :key="member.cardId" class="member-deleted-row">{{member.name}}（已删除）</p>
    </div>
    <p v-if="!memberSelectionValid" class="member-count-hint" role="status">群聊需要 2 至 5 位角色</p>
    <p v-else-if="!membersChanged" class="muted">成员没有变化。</p>
    <div class="form-actions">
     <button type="button" class="primary" :disabled="!canSaveMembers" :title="streaming?'正在生成回复，暂不能修改成员':(!membersChanged?'成员没有变化':(!memberSelectionValid?'群聊需要 2 至 5 位角色':''))" @click="saveMembers">{{savingMembers?'保存中…':'保存'}}</button>
     <button type="button" :disabled="savingMembers" @click="closeMembers">取消</button>
    </div>
   </div>
  </Sheet>

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

  <div v-if="quote" class="chat-quote" role="status">
   <Quote :size="15"/>
   <span class="chat-quote-text">{{quotePreview}}</span>
   <button type="button" class="chat-quote-clear" aria-label="取消引用" @click="clearQuote"><X :size="15"/></button>
  </div>

  <div v-if="showChips" class="chat-chips" role="group" aria-label="示例回复">
   <button v-for="chip in exampleChips" :key="chip" type="button" class="chat-chip" @click="useChip(chip)">{{chip}}</button>
  </div>

  <form class="chat-compose" @submit.prevent="send">
   <textarea id="chat-input" v-model="input" rows="1" :placeholder="allMembersDeleted?'群聊成员已全部删除':'说点什么…'" aria-label="聊天输入框" :disabled="streaming||allMembersDeleted" @keydown.enter.exact.prevent="send" @keydown.esc="clearQuote"></textarea>
   <button v-if="streaming" type="button" class="chat-stop" aria-label="停止生成" @click="stop"><Square :size="18"/>停止</button>
   <button v-else type="submit" class="chat-send" aria-label="发送消息" :disabled="!input.trim()||allMembersDeleted"><Send :size="18"/>发送</button>
  </form>
 </section>
</template>

<style scoped>
.tavern-chat{position:relative;display:flex;flex-direction:column;height:100dvh;background:var(--paper);color:var(--ink)}
.chat-top{display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:8px;min-height:56px;padding:6px 10px;padding-top:calc(6px + env(safe-area-inset-top));border-bottom:1px solid var(--edge);background:var(--paper);position:sticky;top:0;z-index:20}
.chat-icon{display:flex;align-items:center;justify-content:center;width:44px;height:44px;border:0;background:transparent;color:var(--quiet);border-radius:10px;cursor:pointer}
.chat-icon:hover{background:var(--wash);color:var(--ink)}
.chat-menu{position:relative;display:flex;align-items:center;gap:2px}
.chat-menu-pop{position:absolute;right:0;top:46px;z-index:30;min-width:190px;padding:6px;border:1px solid var(--edge);border-radius:12px;background:var(--canvas);box-shadow:0 12px 32px rgba(0,0,0,.12);display:flex;flex-direction:column;gap:2px}
.chat-menu-pop button{min-height:40px;justify-content:flex-start;border:0;background:transparent;color:var(--ink);text-align:left;padding:0 10px;border-radius:8px;cursor:pointer}
.chat-menu-pop button:hover:not(:disabled){background:var(--wash)}
.chat-load-earlier{display:flex;justify-content:center}
.chat-load-earlier button{min-height:36px;border:1px solid var(--edge);background:var(--canvas);border-radius:999px;padding:0 14px;font-size:13px;color:var(--quiet);cursor:pointer}
.bubble-confirm{font-size:12px;color:var(--quiet)}
.bubble-actions .danger-text{color:var(--quiet)}
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
.chat-search{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 12px;border-bottom:1px solid var(--edge);background:var(--canvas)}
.chat-search input{flex:1;min-width:140px;min-height:40px;padding:6px 10px}
.chat-search-count{min-width:48px;font-size:12px;color:var(--quiet);text-align:center;font-variant-numeric:tabular-nums}
.chat-search-step{display:inline-flex;align-items:center;gap:2px;min-height:40px;padding:0 8px;border:1px solid var(--edge);border-radius:9px;background:var(--paper);color:var(--ink);font-size:12px;cursor:pointer}
.chat-search-step:disabled{opacity:.45;cursor:not-allowed}
.chat-search-hint{flex-basis:100%;font-size:11px;color:var(--quiet);text-align:right}
.member-manager{display:flex;flex-direction:column;gap:10px}
.member-pick-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px;max-height:46vh;overflow-y:auto}
.member-pick-row{display:flex;align-items:center;gap:10px;min-height:44px;padding:4px 8px;border:1px solid transparent;border-radius:10px;cursor:pointer}
.member-pick-row.selected{background:var(--wash);border-color:var(--edge)}
.member-pick-row input{width:18px;height:18px;min-height:0}
.member-pick-avatar{width:30px;height:30px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent);font-size:12px;flex-shrink:0}
.member-pick-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.member-pick-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.member-deleted{border-top:1px dashed var(--edge);padding-top:8px}
.member-deleted p{margin:2px 0}
.member-deleted-row{padding:6px 8px;border-radius:8px;background:var(--wash);color:var(--quiet);text-decoration:line-through}
.member-count-hint{margin:0;color:#b23b3b;font-size:13px}
.member-manager .form-actions{display:flex;gap:10px}
.chat-search-hit .bubble{box-shadow:0 0 0 2px var(--accent);transition:box-shadow .25s ease}
.chat-members{display:flex;justify-content:center;gap:-2px;margin-top:2px}
.chat-member-avatar{width:20px;height:20px;margin-left:-6px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent);font-size:11px;border:1px solid var(--paper)}
.chat-member-avatar:first-child{margin-left:0}
.chat-member-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.chat-member-avatar.deleted{opacity:.45;filter:grayscale(1)}
.chat-readonly{margin:0;padding:10px 16px;border-bottom:1px solid var(--edge);background:var(--wash);color:#8a5a2a;font-size:13px;text-align:center}
.bubble-speaker{display:flex;align-items:center;gap:6px;margin-bottom:2px;padding-left:2px}
.bubble-speaker-avatar{width:28px;height:28px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;background:var(--wash);color:var(--accent-ink);font-size:12px;flex:none}
.bubble-speaker-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.bubble-speaker-name{font-size:12px;font-weight:600;color:var(--accent-ink)}
.chat-log{flex:1;min-height:0;overflow-y:auto;padding:18px clamp(12px,3vw,28px) 12px;display:flex;flex-direction:column;gap:10px;max-width:860px;width:100%;margin:0 auto}
.chat-loading{flex:1;display:flex;align-items:center;justify-content:center;color:var(--quiet)}
.chat-empty{color:var(--quiet);text-align:center;margin:auto}
.bubble-row{display:flex;flex-direction:column;gap:4px;max-width:min(72%,620px)}
.bubble-row.user{align-self:flex-end;align-items:flex-end}
.bubble-row.assistant{align-self:flex-start;align-items:flex-start}
.bubble-row.compact{margin-top:-4px}
.bubble{border-radius:16px 16px 16px 4px;padding:10px 14px;font-size:15.5px;line-height:1.85;word-break:break-word;white-space:pre-wrap}
.bubble.user{background:var(--accent);color:var(--on-accent);border:1px solid var(--accent);border-radius:16px 16px 4px 16px}
.bubble.assistant{background:var(--canvas);color:var(--ink);border:1px solid var(--edge);border-radius:16px 16px 16px 4px}
.bubble-text{margin:0}
.bubble-pending{color:var(--quiet)}
.bubble-meta{margin:0 0 2px;font-size:11px;opacity:.8}
.bubble-actions{display:flex;align-items:center;gap:2px;font-size:12px;color:var(--quiet);opacity:0;transition:opacity 140ms ease-out}
.bubble-row:hover .bubble-actions,.bubble-row:focus-within .bubble-actions,.bubble-actions.editing-actions{opacity:1}
.bubble-actions button{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;padding:0;border:0;background:transparent;color:var(--quiet);border-radius:10px;cursor:pointer}
.bubble-actions button:hover{background:var(--wash);color:var(--ink)}
.bubble-actions .danger-text:hover{color:var(--danger);background:var(--wash)}
.act{font-style:italic;color:var(--quiet)}
.strong{font-weight:600;color:var(--ink)}
.chat-day{display:flex;align-items:center;gap:12px;margin:18px 0 8px;color:var(--quiet);font-size:12px;align-self:stretch}
.chat-day::before,.chat-day::after{content:'';flex:1;height:1px;background:var(--edge)}
.typing-dots{display:inline-flex;align-items:center;gap:3px;margin-left:2px;vertical-align:middle}
.typing-dots i{width:4px;height:4px;border-radius:50%;background:var(--quiet);animation:typing-breathe 1.2s ease-in-out infinite}
.typing-dots i:nth-child(2){animation-delay:.15s}
.typing-dots i:nth-child(3){animation-delay:.3s}
@keyframes typing-breathe{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-2px)}}
.bubble textarea{width:100%;min-width:240px;max-width:100%}
.swipe-controls{display:inline-flex;align-items:center;gap:2px}
.swipe-count{font-size:12px;min-width:34px;text-align:center}
.chat-stream-error{margin:0 auto;max-width:860px;width:100%}
.chat-chips{display:flex;gap:8px;flex-wrap:wrap;max-width:860px;width:100%;margin:0 auto;padding:0 clamp(12px,3vw,24px) 8px}
.chat-chip{min-height:40px;padding:0 14px;border:1px solid var(--edge);border-radius:999px;background:var(--wash);color:var(--ink);font-size:13.5px;cursor:pointer}
.chat-chip:hover{border-color:var(--accent);color:var(--accent)}
.chat-compose{position:sticky;bottom:0;display:flex;gap:8px;align-items:flex-end;padding:10px clamp(12px,3vw,24px);padding-bottom:calc(10px + env(safe-area-inset-bottom) + var(--chat-keyboard-offset,0px));border-top:1px solid var(--edge);background:var(--paper);max-width:860px;width:100%;margin:0 auto}
.chat-compose textarea{flex:1;min-height:46px;max-height:160px;resize:none;padding:11px 12px;font-size:15px;line-height:1.5}
.chat-send,.chat-stop{display:flex;align-items:center;gap:6px;min-height:46px;padding:0 16px;border-radius:12px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer;white-space:nowrap}
.chat-stop{background:var(--canvas);color:var(--danger);border-color:var(--danger)}
.chat-send:disabled{opacity:.45;cursor:not-allowed}
.inline-link{min-height:0;padding:0 6px;border:0;background:transparent;color:var(--accent);text-decoration:underline;cursor:pointer;display:inline-flex;align-items:center}
.chat-new{position:absolute;right:16px;bottom:calc(88px + env(safe-area-inset-bottom) + var(--chat-keyboard-offset,0px));z-index:15;display:inline-flex;align-items:center;gap:4px;min-height:44px;padding:0 14px;border:1px solid var(--edge);border-radius:999px;background:var(--canvas);color:var(--accent-ink);font-size:13px;cursor:pointer;box-shadow:0 8px 20px rgba(0,0,0,.08)}
.chat-new:hover{border-color:var(--accent)}
@media(min-width:900px){.chat-compose{position:sticky}.tavern-chat{max-width:900px;margin:0 auto;width:100%}}
@media(max-width:900px){.bubble-actions{opacity:1}}
@media(max-width:767px){
 .chat-top{grid-template-columns:auto minmax(0,1fr) auto auto;gap:4px;padding-left:var(--safe-left,10px);padding-right:var(--safe-right,10px)}
 .chat-title{min-width:0}
 .chat-name{max-width:100%;font-size:16px}
 .chat-members{justify-content:center;max-width:100%;overflow:hidden}
 .chat-status{min-width:0;font-size:11px}
 .chat-menu{flex-shrink:0}
 .chat-menu-pop{top:46px}
 .bubble-row{max-width:86%}
 .bubble-speaker{max-width:100%}
 .bubble-speaker-name{display:block;max-width:min(56vw,240px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .bubble-actions button{min-height:44px;min-width:44px}
 .chat-compose{gap:6px}
 .chat-chip{min-height:44px;padding:0 16px;font-size:14px}
 .chat-send,.chat-stop{min-height:44px;padding:0 12px}
 .chat-search{padding:6px 10px}
}

.chat-quote{display:flex;align-items:center;gap:8px;margin:8px 12px 0;padding:8px 10px;border-left:3px solid var(--accent);border-radius:8px;background:var(--wash);color:var(--quiet);font-size:13px}
.chat-quote-text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chat-quote-clear{flex:none;width:32px;height:32px;display:flex;align-items:center;justify-content:center;border:0;border-radius:8px;background:transparent;color:var(--quiet);cursor:pointer}
.chat-quote-clear:hover{background:var(--edge);color:var(--ink)}
.bubble-quote{display:flex;align-items:flex-start;gap:6px;width:100%;margin:0 0 6px;padding:6px 8px;border:0;border-left:2px solid var(--accent);border-radius:6px;background:rgba(0,0,0,.04);color:var(--quiet);font-size:12px;line-height:1.4;text-align:left;cursor:pointer}
.bubble-quote:hover{color:var(--ink);background:rgba(0,0,0,.07)}
.bubble.user .bubble-quote{border-left-color:color-mix(in srgb,var(--on-accent) 55%,transparent);background:rgba(255,255,255,.16);color:var(--on-accent)}
.bubble.user .bubble-quote:hover{background:rgba(255,255,255,.24);color:var(--on-accent)}
.bubble-quote-text{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
@media (max-width:767px){.bubble-quote{min-height:40px}.chat-quote-clear{width:40px;height:40px}}
@media (prefers-reduced-motion: reduce){.typing-dots i{animation:none;opacity:.6}.bubble,.bubble-actions{transition:none}}
</style>
