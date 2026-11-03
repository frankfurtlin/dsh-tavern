<script setup lang="ts">
import PublicStreamText from './PublicStreamText.vue'
import {ref,computed,onMounted,onUnmounted,watch,nextTick} from 'vue'
import {useRoute} from 'vue-router'
import {Send,Square,ArrowDown,LoaderCircle,MapPin} from 'lucide-vue-next'
import {api,allPages,newKey,authGeneration,ApiError} from '../api'
import type {RoleSession,Message,Task,Page,ReceivedReply} from '../api/types'
import {cacheGet,cacheSet,accountId,appForeground} from '../platform/bridge'
import {useApp} from '../stores/app'
import {watchTask,taskFinished} from '../api/task-watch'
import {finalizeChatCache} from '../api/chat-finalization'
import type {ChatCleanupReceipt} from '../api/chat-finalization'
import {pendingChatPreview,mergeChatPreview,chatRows,chatRowNote,chatFollowingScroll,confirmedChatDraft,chatAccessDenied,chatAccessMessage,applyChatEvent} from '../api/chat-stream'
import type {ChatPreview} from '../api/chat-stream'
import '../styles/chat-stream.css'

type Pending={taskId?:string;submittedMessage?:string;draftAtSubmit?:string;draftCleared?:boolean;body?:{message:string;turnId:string;consentId?:string};key?:string}
type Draft=string|{text:string;afterTurnId:string|null}
const props=defineProps<{session:RoleSession}>(),store=useApp(),messages=ref<Message[]>([]),input=ref(''),error=ref(''),transportError=ref(''),task=ref<Task|null>(null),busy=ref(false),messageArea=ref<HTMLElement>(),pending=ref(''),retry=ref<Pending|null>(null),preview=ref<ChatPreview|null>(null),ready=ref(false),stopping=ref(false),needsRecovery=ref(false),accessDenied=ref(false)
const cleanupReceipt=ref<ChatCleanupReceipt|null>(null),cleanupBusy=ref(false),cleanupError=ref(''),following=ref(true),newWords=ref(false)
const receivedAvailable=ref(false),receivedReplies=ref<ReceivedReply[]>([])
const route=useRoute()
const meetingRoute=computed(()=>{const anchor=props.session.pinnedAnchor,origin=String(route.query.story||'');return{path:`/books/${anchor.bookId}/read`,query:{branch:anchor.branchId,revision:anchor.revisionId,chapter:anchor.chapterId,block:anchor.blockId,anchor:JSON.stringify(anchor),...(/^(book|world)-[a-f0-9]{32}$/.test(origin)?{story:origin}:{})}}})
const rows=computed(()=>chatRows(props.session.id,messages.value,preview.value))
const waiting=computed(()=>!!pending.value&&!rows.value.some(row=>row.role==='assistant'&&row.taskId===pending.value))
const streamLabel=computed(()=>stopping.value||task.value?.cancelRequested?'正在停止，已有文字会保留…':!preview.value?.text?'TA 正在思考…':task.value?.state==='validating'||task.value?.persistenceState==='recoverable'||['complete','stopped','interrupted'].includes(preview.value.status)?'正在保存回复…':'TA 正在回复')
let unwatch:(()=>void)|undefined,generation=0,watchGeneration=0,foregroundGeneration=0,disposed=false,initialized=false,submittedMessage='',lastScrollTop=0,programmaticScroll=false,scrollFrame=0,touchY=0
function sessionIdentity(){return JSON.stringify([props.session.id,props.session.branchId,props.session.pinnedRevisionId,props.session.knowledgeMode,props.session.followProgress])}
function scope(foreground=false){const id=props.session.id,identity=sessionIdentity(),version=generation,epoch=authGeneration(),owner=accountId(),active=foregroundGeneration;return{id,valid:()=>!disposed&&identity===sessionIdentity()&&version===generation&&epoch===authGeneration()&&owner===accountId()&&(!foreground||(active===foregroundGeneration&&!document.hidden&&appForeground()))}}
async function followRender(context=scope(true),changed=false){
  await nextTick();if(!context.valid())return
  const area=messageArea.value;if(!area)return
  if(!following.value){if(changed)newWords.value=true;return}
  programmaticScroll=true;area.scrollTop=area.scrollHeight;lastScrollTop=area.scrollTop;newWords.value=false
  cancelAnimationFrame(scrollFrame);scrollFrame=requestAnimationFrame(()=>{programmaticScroll=false})
}
function onScroll(){const area=messageArea.value;if(!area)return;if(!programmaticScroll)following.value=chatFollowingScroll(lastScrollTop,area.scrollTop,area.scrollHeight,area.clientHeight,following.value);lastScrollTop=area.scrollTop;if(following.value)newWords.value=false}
function latest(){following.value=true;void followRender()}
function wheel(event:WheelEvent){if(event.deltaY<0){following.value=false;programmaticScroll=false}}
function touchStart(event:TouchEvent){touchY=event.touches[0]?.clientY||0}
function touchMove(event:TouchEvent){if((event.touches[0]?.clientY||0)>touchY+3){following.value=false;programmaticScroll=false}}
function scrollKey(event:KeyboardEvent){if(['ArrowUp','PageUp','Home'].includes(event.key)){following.value=false;programmaticScroll=false}}
async function load(cache=true,context=scope(true)){
  let data:Message[]
  if(cache){try{data=await allPages<Message>(`/role-sessions/${context.id}/messages`)}catch(e){if(!context.valid())return;if(chatAccessDenied(e))throw e;await load(false,context);return}}
  else{data=[];let cursor:string|null=null;do{const page:Page<Message>=await api<Page<Message>>(`/role-sessions/${context.id}/messages`+(cursor?'?cursor='+encodeURIComponent(cursor):''));if(!context.valid())return;data.push(...page.items);cursor=page.nextCursor||null}while(cursor)}
  if(!context.valid())return;messages.value=data
  if(receivedAvailable.value){
    const restored=await allPages<ReceivedReply>(`/role-sessions/${context.id}/received-replies`)
    if(!context.valid())return
    receivedReplies.value=restored.filter(value=>value.sessionId===context.id&&value.historical===true&&value.durability==='unconfirmed'&&value.text)
  }
  await followRender(context)
}
async function cleanup(){
  const receipt=cleanupReceipt.value;if(!receipt||cleanupBusy.value||pending.value)return
  const context=scope(),current=()=>context.valid()&&cleanupReceipt.value===receipt&&!pending.value
  cleanupBusy.value=true;cleanupError.value=''
  try{if(await finalizeChatCache(context.id,receipt,()=>input.value,current,cacheSet)&&current())cleanupReceipt.value=null}
  catch{if(current())cleanupError.value='本机记录暂时未能整理；重试只清理本机记录，不会重新发送消息。'}
  finally{if(context.valid())cleanupBusy.value=false}
}
function poll(){
  if(!pending.value||document.hidden||!appForeground())return
  const watchScope=scope(),taskId=pending.value,watchVersion=++watchGeneration;unwatch?.()
  unwatch=watchTask(taskId,async value=>{
    const context=scope(true),valid=()=>watchScope.valid()&&context.valid()&&watchVersion===watchGeneration&&pending.value===taskId
    if(!valid()||value.id!==taskId)return
    if(value.errorCode&&chatAccessDenied({code:value.errorCode})){denyAccess({code:value.errorCode});return}
    task.value=value;transportError.value='';error.value=''
    const previous=preview.value?.text;preview.value=mergeChatPreview(preview.value,value,context.id)
    await followRender(context,previous!==preview.value?.text);if(!valid()||!taskFinished(value))return
    const receipt={taskId,clearDraft:!input.value,replySaved:!!value.result.replySaved}
    // Canonical history wins over its matching live preview, including a stopped
    // partial reply. A local storage failure cannot hide a confirmed message.
    try{await load(false,context)}catch(e){if(valid()){if(chatAccessDenied(e)){denyAccess(e);return}error.value=receipt.replySaved?'回复已经保存，暂时无法加载。请重新打开这段对话查看。':'暂时无法加载已发送的消息，重新打开对话即可继续查看。'}}
    if(!valid())return
    if(!receipt.replySaved&&!preview.value?.text)error.value=value.cancelRequested||value.state==='cancelled'?'已停止这次回复。':value.errorCode==='BUDGET_NOT_AUTHORIZED'?'这次回复未完成，请在设置中检查模型额度。':'这次回复未完成，已发送的消息仍保留。'
    // Release RAM before local cleanup. Its receipt can be retried without HTTP.
    pending.value='';retry.value=null;stopping.value=false;cleanupReceipt.value=receipt
    await cleanup();if(context.valid())await followRender(context)
  },e=>{if(watchScope.valid()&&watchVersion===watchGeneration&&pending.value===taskId){if(chatAccessDenied(e)){denyAccess(e);return}transportError.value='连接暂时中断，正在恢复同一条回复。'}},event=>{
    const context=scope(true)
    if(!watchScope.valid()||!context.valid()||watchVersion!==watchGeneration||pending.value!==taskId||!task.value)return false
    const next=applyChatEvent(task.value,event);if(!next)return false
    const previous=preview.value?.text;task.value=next;preview.value=mergeChatPreview(preview.value,next,context.id)
    void followRender(context,previous!==preview.value?.text)
    return true
  })
}
async function send(){
  if(!initialized||busy.value||cleanupBusy.value||pending.value||!store.online||(!input.value.trim()&&!retry.value?.body))return
  const context=scope();busy.value=true;error.value='';transportError.value=''
  try{
    const saved=retry.value?.body&&retry.value.key?retry.value:{body:{message:input.value.trim(),turnId:newKey()},key:newKey(),draftAtSubmit:input.value};retry.value=saved
    // A durable stable turn precedes HTTP. Storage failure never creates a turn.
    await cacheSet('chat-pending:'+context.id,saved);if(!context.valid())return
    cleanupReceipt.value=null;cleanupError.value='';following.value=true
    preview.value=pendingChatPreview(context.id,saved.body!.turnId,saved.body!.message);await followRender();if(!context.valid())return
    const endpoint=saved.body?.consentId?'turns':'send'
    const response=await api<{taskId:string}>(`/role-sessions/${context.id}/${endpoint}`,{method:'POST',body:saved.body,key:saved.key});if(!context.valid())return
    pending.value=response.taskId;submittedMessage=saved.body!.message;preview.value={...preview.value!,taskId:response.taskId}
    retry.value={...saved,taskId:response.taskId,submittedMessage,draftCleared:true}
    input.value=confirmedChatDraft(input.value,submittedMessage,saved.draftAtSubmit);busy.value=false
    poll()
    const confirmed=retry.value
    try{await cacheSet('chat-pending:'+context.id,confirmed)}catch{if(context.valid()&&pending.value===response.taskId)transportError.value='回复已开始，本机请求记录暂未同步；正在跟随同一条回复。'}
  }catch(e){if(context.valid()){
    if(chatAccessDenied(e)){denyAccess(e);return}
    if(e instanceof ApiError&&['SESSION_BUSY','TURN_IN_PROGRESS'].includes(e.code)){
      try{const current=await api<RoleSession>('/role-sessions/'+context.id);if(!context.valid())return;if(current.pendingTaskId){pending.value=current.pendingTaskId;retry.value=null;preview.value=null;await load(false);if(context.valid())poll()}}catch(e){if(context.valid()){if(chatAccessDenied(e))denyAccess(e);else error.value='这段对话已有回复正在进行，请重新打开查看。'}}
    }else{if(preview.value&&!preview.value.taskId)preview.value={...preview.value,userStatus:'retry'};error.value=(e as Error).message}
  }}finally{if(context.valid())busy.value=false}
}
async function stop(){
  if(!pending.value||stopping.value||!store.online)return
  const context=scope(),taskId=pending.value;stopping.value=true
  try{await api('/tasks/'+taskId+'/cancel',{method:'POST'});if(context.valid()&&pending.value===taskId)poll()}
  catch(e){if(context.valid()&&pending.value===taskId){if(chatAccessDenied(e)){denyAccess(e);return}stopping.value=false;error.value='暂时未能停止，已有文字仍保留；可以再次点停止。'}}
}
function reset(){messages.value=[];receivedAvailable.value=false;receivedReplies.value=[];input.value='';error.value='';transportError.value='';task.value=null;pending.value='';retry.value=null;preview.value=null;cleanupReceipt.value=null;cleanupBusy.value=false;cleanupError.value='';busy.value=false;stopping.value=false;ready.value=false;needsRecovery.value=false;accessDenied.value=false;following.value=true;newWords.value=false;lastScrollTop=0}
function denyAccess(e:unknown){generation++;watchGeneration++;initialized=false;unwatch?.();reset();accessDenied.value=true;error.value=chatAccessMessage(e)}
async function init(){
  generation++;watchGeneration++;initialized=false;unwatch?.();reset();const context=scope(true)
  try{
    const draft=await cacheGet<Draft>('chat-draft:'+context.id);if(!context.valid())return;input.value=typeof draft==='string'?draft:draft?.text||''
    const saved=await cacheGet<Pending>('chat-pending:'+context.id);if(!context.valid())return
    retry.value=saved||null;pending.value=saved?.taskId||'';submittedMessage=saved?.submittedMessage||saved?.body?.message||''
    if(saved?.body)preview.value=pendingChatPreview(context.id,saved.body.turnId,saved.body.message,saved.taskId||null)
    const current=await api<RoleSession>('/role-sessions/'+context.id);if(!context.valid())return
    receivedAvailable.value=current.receivedHistory===true
    await load(true,context);if(!context.valid())return
    // A response lost after POST is recovered by server pendingTaskId or the
    // already persisted user turn, so reopening does not create another request.
    const known=saved?.body?messages.value.find(message=>message.turnId===saved.body!.turnId&&message.role==='user'):undefined
    pending.value=current.pendingTaskId||saved?.taskId||known?.taskId||''
    retry.value=saved||null;submittedMessage=saved?.submittedMessage||saved?.body?.message||known?.text||'';preview.value=null
    if(pending.value){
      const user=messages.value.find(message=>message.taskId===pending.value&&message.role==='user')
      if(user){submittedMessage=user.text;preview.value=pendingChatPreview(context.id,user.turnId,user.text,pending.value)}
      else if(saved?.body&&(!saved.taskId||saved.taskId===pending.value))preview.value=pendingChatPreview(context.id,saved.body.turnId,saved.body.message,pending.value)
      const matchesIntent=!!saved&&(!saved.taskId||saved.taskId===pending.value)&&(!user||!saved.body||saved.body.turnId===user.turnId)
      const cleared=saved?.draftCleared||!!draft&&typeof draft==='object'&&draft.afterTurnId===(user?.turnId||saved?.body?.turnId)
      if(matchesIntent)input.value=confirmedChatDraft(input.value,submittedMessage,saved?.draftAtSubmit,!!cleared)
    }
    initialized=true;ready.value=true
    if(pending.value)poll();else if(saved?.body&&saved.key)await send()
    await followRender(context)
  }catch(e){if(context.valid()){if(chatAccessDenied(e)){denyAccess(e);return}error.value=(e as Error).message;initialized=true;ready.value=true;needsRecovery.value=true}}
}
function pause(){foregroundGeneration++;watchGeneration++;unwatch?.()}
function resume(){foregroundGeneration++;if(document.hidden||!appForeground()||disposed||accessDenied.value)return;if(!initialized||needsRecovery.value){void init();return}if(pending.value)poll()}
function visibility(){if(document.hidden)pause();else resume()}
function privateCleared(){generation++;watchGeneration++;initialized=false;unwatch?.();reset()}
watch(()=>[sessionIdentity(),store.user?.id],()=>{if(store.user)void init();else privateCleared()})
watch(()=>store.online,online=>{if(online)resume()})
watch(input,value=>{if(initialized&&!disposed){const context=scope();cacheSet('chat-draft:'+context.id,{text:value,afterTurnId:pending.value?preview.value?.turnId||retry.value?.body?.turnId||null:null}).catch(()=>{if(context.valid())error.value='草稿暂时未能保存，请保持此页面打开。'})}},{flush:'sync'})
onMounted(()=>{document.addEventListener('visibilitychange',visibility);window.addEventListener('reader-save',pause);window.addEventListener('reader-resume',resume);window.addEventListener('reader-private-cleared',privateCleared);void init()})
onUnmounted(()=>{disposed=true;generation++;watchGeneration++;unwatch?.();cancelAnimationFrame(scrollFrame);document.removeEventListener('visibilitychange',visibility);window.removeEventListener('reader-save',pause);window.removeEventListener('reader-resume',resume);window.removeEventListener('reader-private-cleared',privateCleared)})
</script>

<template>
  <div class="chat-main chat-stream-panel">
    <div class="chat-heading"><div class="row between"><strong>{{session.characterName}}</strong><RouterLink v-if="!accessDenied" :to="meetingRoute" class="chat-source-link"><MapPin :size="15" aria-hidden="true"/>回到相遇段落</RouterLink></div><small class="muted" style="display:block">{{session.branchName}} · {{session.knowledgeMode==='full'?'全书模式':session.followProgress?'跟随已读故事':'保存的相遇时刻'}}<br>你是故事外的读者，聊天不会改写正文</small></div>
    <div ref="messageArea" class="chat-messages" role="log" aria-label="角色对话消息" aria-live="polite" aria-relevant="additions text" :aria-busy="!ready" tabindex="0" @scroll="onScroll" @wheel.passive="wheel" @touchstart.passive="touchStart" @touchmove.passive="touchMove" @keydown="scrollKey">
      <p v-if="!ready&&!accessDenied" class="muted" role="status">正在打开这段对话…</p>
      <p v-else-if="!rows.length&&!receivedReplies.length&&!accessDenied" class="muted">从一句话开始，聊聊你在故事里遇见的 TA。</p>
      <div v-for="row in rows" :key="row.id" class="chat-message" :class="row.role" :data-turn-id="row.turnId" :data-task-id="row.taskId" :data-stream-status="row.status" :data-chat-live="row.preview&&row.role==='assistant'?'true':undefined"><p class="chat-message-text"><PublicStreamText v-if="row.preview&&row.role==='assistant'" :text="row.text" :content-id="preview?.contentId||row.taskId" :stream-version="preview?.streamVersion||1" @rendered="followRender(scope(true),true)"/><template v-else>{{row.text}}</template></p><small v-if="chatRowNote(row)" class="chat-message-note">{{chatRowNote(row)}}</small></div>
      <section v-for="reply in receivedReplies" :key="'received:'+reply.id" class="chat-received-history" :data-received-task-id="reply.taskId" aria-label="从备份恢复的回复片段">
        <div class="chat-message user"><p class="chat-message-text">{{reply.userMessage}}</p></div>
        <div class="chat-message assistant"><p class="chat-message-text">{{reply.text}}</p><small class="chat-message-note">从备份恢复 · 已收到的回复片段</small></div>
      </section>
      <div v-if="waiting" class="chat-thinking" role="status" data-chat-thinking><LoaderCircle :size="17" aria-hidden="true"/><span>TA 正在思考…</span></div>
      <p v-if="transportError" class="chat-transport-note" role="status">{{transportError}}</p>
      <p v-if="error" class="error" role="alert">{{error}}</p>
      <div v-if="cleanupReceipt" class="hint" data-chat-cleanup><p>{{cleanupReceipt.replySaved?'回复已保存。':'这次回复已结束。'}}{{cleanupError||'正在整理本机记录…'}}</p><button :disabled="busy||cleanupBusy||!!pending" @click="cleanup">{{cleanupBusy?'正在整理…':'整理本机记录'}}</button></div>
    </div>
    <div v-if="!following&&newWords" class="chat-latest"><button @click="latest"><ArrowDown :size="16" aria-hidden="true"/>查看新回复</button></div>
    <div v-if="pending" class="chat-stream-toolbar"><span role="status">{{streamLabel}}</span><button class="chat-stop" aria-label="停止回复" :disabled="stopping||!store.online" @click="stop"><Square :size="15" aria-hidden="true"/>{{stopping?'正在停止…':'停止回复'}}</button></div>
    <form class="chat-compose" @submit.prevent="send"><textarea v-model="input" aria-label="给角色的消息" :placeholder="pending?'可以先写下一条，等 TA 回复完再发送':'想和角色说些什么？'" maxlength="4000" :disabled="!ready||busy||cleanupBusy||!!retry?.body&&!pending"></textarea><button class="primary" :aria-label="retry?.body&&!pending?'重试发送':'发送消息'" :disabled="!ready||busy||cleanupBusy||!!pending||(!input.trim()&&!retry?.body)||!store.online"><Send :size="20" aria-hidden="true"/></button></form>
  </div>
</template>
