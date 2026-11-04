<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {ArrowRight,MessagesSquare,Users,RefreshCw} from 'lucide-vue-next'
import {api,newKey,ApiError} from '../api'
import type {Anchor,RoleSession,StoryRun} from '../api/types'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import {watchStoryRun} from '../api/story-run-watch'
import {writingError} from '../api/story-writing'
import '../styles/character-cards.css'
import StoryImages from './StoryImages.vue'
import CharacterRecognition from './CharacterRecognition.vue'
import type {Recognition,RecognizedCard} from './character-recognition-types'

const props=defineProps<{bookId:string;anchor:Anchor}>(),emit=defineEmits<{opened:[session:RoleSession]}>()
interface Card {id:string;identityId?:string;name:string;description:string;traits:string[];relations:string[];available:boolean;reason?:string;reasonCode?:string;recognition?:Recognition}
interface CardList {items:Card[];mode:string;modeVersion:number;indexState:string;indexTaskId?:string|null;indexScope?:string}
interface IndexIntent {key:string;body:{branchId:string;retry?:boolean};runId?:string|null}
interface IndexResult {indexState:string;indexTaskId:string|null;cached:boolean;errorCode?:string|null}
interface ChatIntent {key:string;body:{anchor:Anchor;characterId:string}}
const store=useApp(),cards=ref<Card[]>([]),mode=ref('read_progress'),indexState=ref(''),indexScope=ref(''),loading=ref(true),busy=ref(false),error=ref(''),opening=ref('')
const indexRun=ref<StoryRun|null>(null),pendingIndex=ref<IndexIntent|null>(null),confirmedIndex=ref<IndexResult|null>(null)
const pendingChat=ref<ChatIntent|null>(null),confirmedChat=ref<RoleSession|null>(null)
const recognizing=ref<Card|null>(null)
function corrected(value:RecognizedCard){const index=cards.value.findIndex(card=>card.id===value.id);if(index>=0)cards.value[index]={...cards.value[index]!,...value}}
const capture=useRequestScope(()=>[props.bookId,props.anchor]),indexKey=()=>`character-index:${props.bookId}:${props.anchor.branchId}:${props.anchor.revisionId}:${indexScope.value||'legacy'}`
const indexing=computed(()=>!!indexRun.value&&!['succeeded','failed','cancelled'].includes(indexRun.value.state)||['queued','preparing','running','indexing','persisting'].includes(indexState.value))
const fullBook=computed(()=>['full_book','whole_book','full'].includes(mode.value))
let commandGeneration=0,watchGeneration=0,unwatch:(()=>void)|undefined
function stopWatching(){watchGeneration++;unwatch?.();unwatch=undefined}
function cardError(error:unknown){
  if(error instanceof ApiError&&['NO_PROVIDER','BUDGET_NOT_AUTHORIZED','NETWORK_UNAVAILABLE','SESSION_CHANGED'].includes(error.code))return writingError(error)
  return '角色暂时没能整理完成，已保存的阅读内容仍然可用。'
}
function reason(card:Card){
  if(card.reasonCode==='ROLE_DECEASED')return '这个时间点暂时无法开始对话，可以试试更早的章节。'
  return card.reason||'这个时间点的角色资料还不完整，暂时无法开始对话。'
}
async function fetchCards(valid:()=>boolean){
  const path=`/books/${props.bookId}/character-cards?branchId=${encodeURIComponent(props.anchor.branchId)}&anchor=${encodeURIComponent(JSON.stringify(props.anchor))}`
  const result=await api<CardList>(path);if(!valid())return null
  cards.value=result.items;mode.value=result.mode;indexState.value=result.indexState;indexScope.value=result.indexScope||''
  return result
}
function followIndex(id:string,known?:StoryRun){
  const scope=capture(),generation=++watchGeneration,valid=()=>scope.valid()&&generation===watchGeneration
  unwatch?.();indexRun.value=known||{id,state:'preparing',currentTaskId:null,candidateId:null,requestedChapters:1,completedChapters:0,errorCode:null,scopeAnchor:props.anchor,createdAt:new Date().toISOString()}
  unwatch=watchStoryRun(id,async(value,current)=>{
    const live=()=>valid()&&current()
    if(!live())return
    await fetchCards(live);if(!live())return
    indexRun.value=value
    if(value.state==='failed')error.value=value.errorCode==='NO_PROVIDER'?writingError('NO_PROVIDER'):'角色整理暂时中断，可以重新整理。'
    else if(!pendingChat.value)error.value=''
  },e=>{if(valid())error.value=cardError(e)})
}
async function startIndex(retry=false){
  if(busy.value||!store.online||indexing.value&&!confirmedIndex.value)return
  const scope=capture(),command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration,key=indexKey()
  busy.value=true;error.value=''
  try{
    if(retry&&!pendingIndex.value&&!confirmedIndex.value){pendingIndex.value={key:newKey(),body:{branchId:props.anchor.branchId,retry:true}}}
    if(!pendingIndex.value){const saved=await cacheGet<IndexIntent>(key);if(!valid())return;pendingIndex.value=saved||{key:newKey(),body:{branchId:props.anchor.branchId}}}
    const intent=pendingIndex.value
    if(!confirmedIndex.value){
      await cacheSet(key,intent);if(!valid())return
      const result=await api<IndexResult>(`/books/${props.bookId}/character-index`,{method:'POST',body:intent.body,key:intent.key});if(!valid())return
      confirmedIndex.value=result;intent.runId=result.indexTaskId
    }
    const result=confirmedIndex.value
    await cacheSet(key,{...intent,runId:result.indexTaskId});if(!valid())return
    pendingIndex.value=null;confirmedIndex.value=null
    indexState.value=result.indexState
    if(result.indexTaskId)followIndex(result.indexTaskId)
    else {indexRun.value=null;await fetchCards(valid)}
  }catch(e){if(valid())error.value=confirmedIndex.value?'角色整理已开始，本机记录暂时未能保存。点击「查看角色整理」即可继续。':cardError(e)}
  finally{if(valid())busy.value=false}
}
async function openChat(card:Card){
  if(busy.value||!store.online||!card.available||pendingChat.value&&pendingChat.value.body.characterId!==card.id)return
  const scope=capture(),command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration
  const key=`character-chat:${props.bookId}:${props.anchor.branchId}:${props.anchor.revisionId}:${props.anchor.blockId}:${props.anchor.end}:${card.id}`
  busy.value=true;opening.value=card.id;error.value=''
  try{
    if(!pendingChat.value){const saved=await cacheGet<ChatIntent>(key);if(!valid())return;pendingChat.value=saved||{key:newKey(),body:{anchor:{...props.anchor},characterId:card.id}}}
    const intent=pendingChat.value
    if(!confirmedChat.value){
      await cacheSet(key,intent);if(!valid())return
      const session=await api<RoleSession>(`/books/${props.bookId}/character-chats`,{method:'POST',body:intent.body,key:intent.key});if(!valid())return
      confirmedChat.value=session
    }
    const session=confirmedChat.value
    await cacheSet(key,null);if(!valid())return
    pendingChat.value=null;confirmedChat.value=null;emit('opened',session)
  }catch(e){if(valid())error.value=confirmedChat.value?'对话已经准备好，本机记录暂时未能清理。再次点击角色即可进入同一段对话。':cardError(e)}
  finally{if(valid()){busy.value=false;opening.value=''}}
}
async function initialize(){
  const scope=capture(),command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration
  stopWatching();loading.value=true;busy.value=false;error.value='';cards.value=[];indexScope.value='';indexRun.value=null;pendingIndex.value=null;confirmedIndex.value=null;pendingChat.value=null;confirmedChat.value=null;opening.value='';recognizing.value=null
  try{
    const result=await fetchCards(valid);if(!valid()||!result)return
    if(result.indexTaskId)followIndex(result.indexTaskId)
    else if(!['ready','succeeded','complete','failed','cancelled'].includes(result.indexState)&&store.online){await startIndex()}
  }catch(e){if(valid())error.value=cardError(e)}finally{if(scope.valid())loading.value=false}
}
watch(()=>[props.bookId,props.anchor],initialize,{deep:true})
onMounted(initialize)
onUnmounted(()=>{stopWatching();commandGeneration++})
</script>

<template>
  <section class="character-cards" aria-label="角色卡">
    <div class="character-intro"><span class="eyebrow"><Users :size="16"/> 故事里的人</span><h3>想和谁聊一聊？</h3><p>{{fullBook?'全书模式已开启，可以聊整本书。':'角色资料跟着你的阅读进度展开。'}}</p><span class="character-mode-link">{{fullBook?'当前为全书模式':'角色资料跟随阅读进度'}}</span></div>
    <p v-if="loading" class="muted" role="status">正在打开角色卡…</p>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <div v-if="indexing" class="character-index-status" role="status"><span class="character-status-dot"></span><p>正在整理这本书的角色<small>可以继续阅读，角色卡会陆续出现。</small></p></div>
    <div v-if="cards.length" class="character-card-list"><article v-for="card in cards" :key="card.id" class="character-card"><div class="character-card-heading"><StoryImages :scope="card.identityId?{kind:'book',bookId,branchId:anchor.branchId,revisionId:anchor.revisionId,anchor,identityId:card.identityId}:null" purpose="avatar" :editable="!!card.identityId" :alt="card.name"><template #fallback><span class="character-avatar" aria-hidden="true">{{card.name.slice(0,1)}}</span></template></StoryImages><div><h4>{{card.name}}</h4><p>{{card.description||'这段故事里出现的人物'}}</p></div></div><div v-if="card.traits?.length" class="character-traits"><span v-for="trait in card.traits.slice(0,4)" :key="trait">{{trait}}</span></div><ul v-if="card.relations?.length" class="character-relations"><li v-for="relation in card.relations.slice(0,3)" :key="relation">{{relation}}</li></ul><p v-if="!card.available" class="character-unavailable">{{reason(card)}}</p><p v-if="card.recognition?.identityStatus==='uncertain'" class="character-unavailable">存在身份歧义，可以查看原文证据并纠正。</p><button v-if="card.recognition" class="secondary" :disabled="busy" @click="recognizing=card">查看识别证据与纠正</button><button class="primary" :disabled="busy||!store.online||!card.available||!!pendingChat&&pendingChat.body.characterId!==card.id" @click="openChat(card)"><MessagesSquare :size="17"/>{{opening===card.id?'正在打开…':'和 TA 聊天'}} <ArrowRight :size="16"/></button></article></div>
    <div v-else-if="!loading&&!indexing&&!error" class="character-empty"><Users :size="30"/><p>这个阅读进度还没有可展示的角色。</p><small>继续读下去，新的角色卡会出现在这里。</small></div>
    <button v-if="confirmedIndex||pendingIndex||indexRun?.state==='failed'||indexState==='failed'||error&&!indexing&&!pendingChat" class="character-retry" :disabled="busy||!store.online" @click="startIndex(indexRun?.state==='failed'||indexState==='failed')"><RefreshCw :size="16"/>{{confirmedIndex?'查看角色整理':'重新整理角色'}}</button>
    <span v-if="error" class="inline-link">可检查角色卡索引或稍后重试</span>
  </section>
  <CharacterRecognition v-if="recognizing" :book-id="bookId" :anchor="anchor" :card="recognizing" @close="recognizing=null" @saved="corrected"/>
</template>
