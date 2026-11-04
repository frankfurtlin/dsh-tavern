<script setup lang="ts">
import {computed,ref,onMounted,onUnmounted,watch,nextTick} from 'vue'
import {ArrowRight,BookOpen,Check,Info,Square,Sparkles} from 'lucide-vue-next'
import {api,allPages,newKey,ApiError} from '../api'
import type {Anchor,Branch,Profile,Operation,Task,Candidate,AcceptResult,StoryRun} from '../api/types'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import {defaultProfileId} from '../api/profiles'
import {writingChapterCount,writingOperation,writingError,writingReviewMessages} from '../api/story-writing'
import {watchTask,taskFinished} from '../api/task-watch'
import {watchStoryRun} from '../api/story-run-watch'
import '../styles/story-generation.css'

const props=defineProps<{anchor:Anchor;branch:Branch;operation:string}>(),emit=defineEmits<{accepted:[AcceptResult];prepared:[]}>()
const store=useApp(),profileId=ref(''),instruction=ref(''),choice=ref<number|'custom'>(1),customCount=ref<number|string>(2)
const loading=ref(true),busy=ref(false),error=ref(''),conflict=ref(false),run=ref<StoryRun|null>(null),task=ref<Task|null>(null),candidate=ref<Candidate|null>(null)
const previewElement=ref<HTMLElement|null>(null)
const history=ref<{id:string;kind:'run'|'task';createdAt:string;state:string;warningCount?:number}[]>([]),historyId=ref('')
const capture=useRequestScope(()=>[props.anchor,props.branch.id,props.operation])
const count=computed(()=>writingChapterCount(choice.value==='custom'?customCount.value:choice.value))
const running=computed(()=>run.value?!['succeeded','failed','cancelled'].includes(run.value.state):!!task.value&&!taskFinished(task.value))
const rejected=computed(()=>!running.value&&((run.value?.state||task.value?.state)==='failed'||!!candidate.value&&candidate.value.outcome!=='complete'))
const reviewMessages=computed(()=>writingReviewMessages(candidate.value?.reviewWarnings))
const canContinue=computed(()=>candidate.value?.canAccept??(candidate.value?.outcome==='complete'&&candidate.value?.validationStatus==='passed'))
const requested=computed(()=>run.value?.requestedChapters||task.value?.result.requestedChapters||candidate.value?.requestedChapters||1)
const completed=computed(()=>run.value?.completedChapters??task.value?.result.completedChapters??candidate.value?.completedChapters??0)
const progress=computed(()=>Math.min(requested.value,completed.value))
const activeWriting=computed(()=>task.value?.state==='running'||run.value?.state==='running'||run.value?.phase==='writing')
const statusText=computed(()=>{
  if(task.value?.persistenceState==='recoverable'||run.value?.phase==='recovering')return '写作结果正在恢复保存'
  if(running.value&&activeWriting.value)return `正在写第 ${Math.min(completed.value+1,requested.value)} / ${requested.value} 章`
  const state=run.value?.state||task.value?.state||''
  return ({queued:'写作已排队',preparing:'正在整理故事背景',running:'正在写作',validating:'正在检查新章节',persisting:'正在保存新章节',succeeded:'新章节已保存',cancelled:'这次写作已停止',failed:'这次写作未完成'} as Record<string,string>)[state]||'正在准备写作'
})
const cacheKey=()=> 'generation:'+props.anchor.branchId
interface PendingWriting {key:string;request:Operation;runId?:string}
interface StoredWriting {runId?:string;pending?:PendingWriting;taskId?:string;request?:unknown;path?:string;key?:string}
// A confirmed run stays in memory even if updating local storage fails.
const pending=ref<PendingWriting|null>(null),confirmed=ref<StoryRun|null>(null)
let unwatch:(()=>void)|undefined,watchGeneration=0,commandGeneration=0
watch([instruction,choice,customCount],()=>{if(!busy.value&&!pending.value&&!running.value)error.value=''})
watch(()=>candidate.value?.id,async()=>{
  const selected=candidate.value,scope=capture();if(!selected?.text)return
  await nextTick()
  if(scope.valid()&&candidate.value?.id===selected.id)previewElement.value?.scrollIntoView({block:'start',behavior:'auto'})
})

function upsertHistory(value:{id:string;kind:'run'|'task';createdAt:string;state:string;warningCount?:number}){
  const index=history.value.findIndex(item=>item.id===value.id&&item.kind===value.kind)
  if(index>=0)history.value[index]=value;else history.value.unshift(value)
}
function stopWatching(){watchGeneration++;unwatch?.();unwatch=undefined}
async function viewRun(id:string,known?:StoryRun){
  const scope=capture(),generation=++watchGeneration,valid=()=>scope.valid()&&generation===watchGeneration
  unwatch?.();historyId.value='run:'+id;run.value=known||{id,state:'queued',currentTaskId:null,candidateId:null,requestedChapters:count.value||1,completedChapters:0,errorCode:null,scopeAnchor:props.anchor,createdAt:new Date().toISOString()};task.value=null;candidate.value=null;conflict.value=false;error.value=''
  const update=async(saved:StoryRun,current:()=>boolean)=>{
    if(!valid()||!current())return
    const result=saved.candidateId?await api<Candidate>('/candidates/'+saved.candidateId,{cache:true}):null;if(!valid()||!current())return
    run.value=saved;task.value=null;candidate.value=result;error.value=''
    upsertHistory({id:saved.id,kind:'run',createdAt:saved.createdAt,state:saved.state,warningCount:saved.warningCount})
    if(saved.state==='failed')error.value=writingError(saved.errorCode||'GENERATION_FAILED')
  }
  unwatch=watchStoryRun(id,update,e=>{if(valid())error.value=writingError(e)})
}
async function viewLegacyTask(id:string){
  const scope=capture(),generation=++watchGeneration,valid=()=>scope.valid()&&generation===watchGeneration
  unwatch?.();historyId.value='task:'+id;run.value=null;task.value=null;candidate.value=null;conflict.value=false;error.value=''
  unwatch=watchTask(id,async value=>{
    if(!valid())return
    const saved=value.result.candidateId?await api<Candidate>('/candidates/'+value.result.candidateId,{cache:true}):null;if(!valid())return
    task.value=value;candidate.value=saved;error.value='';upsertHistory({id,kind:'task',createdAt:value.createdAt,state:value.state})
    if(value.taskType==='analysis'&&value.result.aiStatus==='ai_ready'){emit('prepared')}
    if(value.state==='failed'&&taskFinished(value))error.value=writingError(value.errorCode||'GENERATION_FAILED')
    else if(saved&&saved.outcome!=='complete')error.value=writingError(value.errorCode||(saved.validationStatus==='unknown'?'VALIDATION_UNCERTAIN':saved.outcome==='blocked'?'GENERATION_BLOCKED':'GENERATION_INCOMPLETE'))
  },e=>{if(valid())error.value=writingError(e)})
}
async function selectHistory(){
  if(busy.value||pending.value||running.value)return
  commandGeneration++
  const kind=historyId.value.startsWith('run:')?'run':'task',id=historyId.value.slice(kind.length+1)
  if(kind==='run')await viewRun(id);else await viewLegacyTask(id)
}

async function start(){
  if(busy.value||running.value||!profileId.value||!store.online||(!pending.value&&!count.value))return
  const scope=capture(),command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration,cache=cacheKey()
  stopWatching();busy.value=true;error.value='';conflict.value=false
  if(!pending.value){pending.value={key:newKey(),request:writingOperation(props.anchor,props.branch,props.operation,profileId.value,instruction.value,count.value!)};confirmed.value=null;run.value=null;task.value=null;candidate.value=null;historyId.value=''}
  const intent=pending.value
  try{
    // Save before HTTP; uncertain retries preserve the request and original key.
    if(!confirmed.value){
      await cacheSet(cache,{pending:intent});if(!valid())return
      const result=await api<StoryRun>('/story-runs',{method:'POST',body:intent.request,key:intent.key});if(!valid())return
      confirmed.value=result;intent.runId=result.id
    }
    const result=confirmed.value
    await cacheSet(cache,{runId:result.id});if(!valid())return
    pending.value=null;confirmed.value=null;candidate.value=null
    await viewRun(result.id,result)
  }catch(e){
    if(!valid())return
    error.value=confirmed.value?'写作已提交，本机记录暂时未能保存。点击「查看已提交的写作」即可继续。':writingError(e)
    // A definitive validation rejection created no run. Allow changing the
    // wish/count, but keep uncertain network/server results on the original key.
    if(!confirmed.value&&e instanceof ApiError&&e.status>=400&&e.status<500&&e.status!==408){
      try{await cacheSet(cache,null);if(valid())pending.value=null}catch{if(valid())error.value+=' 本机待提交记录暂时无法清理，请重试同一次写作。'}
    }
  }
  finally{if(valid())busy.value=false}
}
async function accept(pinned=false){
  if(!candidate.value||!canContinue.value||busy.value||!store.online)return
  const scope=capture(),selected=candidate.value,command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration&&candidate.value?.id===selected.id
  busy.value=true;error.value=''
  try{
    const result=await api<AcceptResult>('/candidates/'+selected.id+'/accept',{method:'POST',key:'accept-'+selected.id+(pinned?'-pinned':''),body:{expectedHeadRevisionId:selected.baseRevisionId,sourcePolicy:pinned?'fork_pinned_revision':'require_unchanged_head',target:{...selected.target,...(pinned?{mode:'new_child_branch'}:{})}}})
    if(!valid())return;selected.status='accepted';emit('accepted',result)
  }catch(e){if(valid()){error.value=writingError(e);conflict.value=e instanceof ApiError&&e.code==='REVISION_CONFLICT'}}
  finally{if(scope.valid()&&command===commandGeneration)busy.value=false}
}
async function discard(){
  if(!candidate.value||busy.value||!store.online)return
  const scope=capture(),selected=candidate.value,command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration&&candidate.value?.id===selected.id
  busy.value=true;error.value=''
  try{await api('/candidates/'+selected.id+'/discard',{method:'POST'});if(!valid())return;selected.status='discarded';store.message('这次写作已放弃')}
  catch(e){if(valid())error.value=writingError(e)}finally{if(scope.valid()&&command===commandGeneration)busy.value=false}
}
async function stop(){
  if(busy.value||!store.online)return
  const scope=capture(),command=++commandGeneration,id=run.value?.id||task.value?.id,kind=run.value?'run':'task';if(!id)return
  const valid=()=>scope.valid()&&command===commandGeneration
  busy.value=true;error.value=''
  try{await api(kind==='run'?'/story-runs/'+id+'/cancel':'/tasks/'+id+'/cancel',{method:'POST'});if(valid())store.message('正在停止这次写作')}
  catch(e){if(valid())error.value=writingError(e)}finally{if(valid())busy.value=false}
}
onMounted(async()=>{
  const scope=capture(),command=++commandGeneration,valid=()=>scope.valid()&&command===commandGeneration,cache=cacheKey()
  try{
    const profiles=await allPages<Profile>('/model-profiles');if(!valid())return;profileId.value=defaultProfileId(profiles)
    const saved=await cacheGet<StoredWriting>(cache);if(!valid())return
    const runs=await allPages<StoryRun>('/story-runs?branchId='+props.anchor.branchId);if(!valid())return
    const childTaskIds=new Set(runs.map(item=>item.currentTaskId).filter(Boolean))
    const tasks=await allPages<Task>('/tasks?branchId='+props.anchor.branchId+'&taskType=generation');if(!valid())return
    history.value=[...runs.map(item=>({id:item.id,kind:'run' as const,createdAt:item.createdAt,state:item.state,warningCount:item.warningCount})),...tasks.filter(item=>!childTaskIds.has(item.id)).map(item=>({id:item.id,kind:'task' as const,createdAt:item.createdAt,state:item.state}))].sort((a,b)=>Date.parse(b.createdAt)-Date.parse(a.createdAt))
    const active=runs.find(item=>!['succeeded','failed','cancelled'].includes(item.state))
    if(saved?.pending){pending.value=saved.pending;instruction.value=saved.pending.request.instruction||'';const savedCount=saved.pending.request.chapterCount||1;choice.value=[1,3,5].includes(savedCount)?savedCount:'custom';customCount.value=savedCount;if(saved.pending.runId){const result=await api<StoryRun>('/story-runs/'+saved.pending.runId);if(!valid())return;await viewRun(result.id,result)}else error.value='这次请求的回执尚未确认，输入已保留。确认后可提交同一次请求。'}
    else if(active)await viewRun(active.id,active)
    else if(saved?.runId)await viewRun(saved.runId)
    else if(saved?.taskId)await viewLegacyTask(saved.taskId)
    else if(saved?.request&&saved.path&&saved.key){
      error.value='旧写作请求的回执尚未确认，请先从写作记录查看已有任务。此处不会自动重新提交。'
    }else if(history.value.length){historyId.value=history.value[0].kind+':'+history.value[0].id;await selectHistory()}
  }catch(e){if(valid())error.value=writingError(e)}finally{if(scope.valid())loading.value=false}
})
onUnmounted(()=>{stopWatching();commandGeneration++})
</script>

<template>
  <section class="story-writing" aria-label="故事写作">
    <div class="writing-origin"><span class="writing-origin-mark"><BookOpen :size="20"/></span><div><small>{{branch.name}}</small><p>从选中段落{{anchor.end===0?'之前':'之后'}}开始写作</p></div></div>
    <p v-if="loading" class="muted" role="status">正在打开写作记录…</p>
    <p v-else-if="!profileId" class="hint">先添加一个模型，就能继续写故事。</p>
    <template v-else>
      <fieldset class="chapter-count" :disabled="busy||running||!!pending">
        <legend>接下来，写几章？</legend>
        <div class="chapter-count-options" role="group" aria-label="选择章节数量"><button v-for="number in [1,3,5]" :key="number" type="button" :aria-pressed="choice===number" :class="{selected:choice===number}" @click="choice=number">{{number}} 章</button><button type="button" :aria-pressed="choice==='custom'" :class="{selected:choice==='custom'}" @click="choice='custom'">自定义</button></div>
        <label v-if="choice==='custom'" class="custom-count">章节数量<input v-model="customCount" type="number" min="1" max="10" step="1" inputmode="numeric" aria-describedby="chapter-count-help"></label>
        <small v-if="choice==='custom'" id="chapter-count-help" class="muted">每次可以写 1—10 章。</small>
      </fieldset>
      <label class="writing-wish">希望接下来怎样写？<span class="optional">选填</span><textarea v-model="instruction" maxlength="2000" :disabled="busy||running||!!pending" rows="3" placeholder="留空就顺着故事继续写；也可以告诉我：让林渡留下来救人。"></textarea></label>
      <p class="writing-destination">{{branch.parentBranchId&&props.operation==='continue_at_tail'&&!instruction.trim()?'新章节会接在这条故事线后面。':instruction.trim()?'将从这里展开新的走向，当前故事线保留。':'新章节会保存在一条新的故事线里。'}}<span>模型与用量由账号服务管理</span></p>
      <button v-if="!running" class="primary writing-start" :disabled="loading||busy||!store.online||(!pending&&!count)" @click="start"><Sparkles :size="18"/>{{busy?'正在提交…':confirmed?'查看已提交的写作':pending?'继续这次写作':'开始写作'}}<span v-if="!busy&&!pending&&count">{{count}} 章</span></button>
    </template>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <section v-if="(run||task)&&!rejected" class="writing-progress" aria-label="写作进度"><div class="row between"><p role="status" aria-live="polite">{{statusText}}</p><button v-if="running" class="icon-button" :disabled="busy||!store.online" aria-label="停止写作" @click="stop"><Square :size="16"/></button><Check v-else-if="(run?.state||task?.state)==='succeeded'" :size="18"/></div><progress :value="progress" :max="requested" :aria-label="`已完成 ${progress} / ${requested} 章`"></progress><small v-if="running" class="muted">可以关闭面板，回来查看已保存的写作记录。</small></section>
    <section v-if="candidate&&candidate.text" ref="previewElement" class="writing-result" aria-label="新章节预览">
        <div class="row between"><h3>{{candidate.outcome==='complete'?'新章节':'已生成的草稿'}}</h3><span class="badge">AI 写作<span v-if="candidate.outcome==='complete'"> · {{candidate.completedChapters||candidate.chapters?.length||1}} 章</span></span></div>
        <p class="writing-result-origin">{{candidate.anchorLocation||'选中段落'}}之后 · {{branch.name}}</p>
        <aside v-if="reviewMessages.length" class="writing-notes" aria-label="内容提示"><Info :size="18"/><div><strong>内容提示</strong><p v-for="message in reviewMessages" :key="message">{{message}}</p><small>{{canContinue?'AI 检查可能有误，正文已保留。你可以接着读，也可以重新写作。':'正文已保留为草稿，可以查看或重新写作。'}}</small></div></aside>
        <template v-if="candidate.chapters?.length"><article v-for="chapter in candidate.chapters" :key="chapter.index" class="writing-chapter"><span class="eyebrow">新章节 {{chapter.index}}</span><h4>{{chapter.title}}</h4><div class="candidate-text">{{chapter.text}}</div></article></template>
        <div v-else class="candidate-text">{{candidate.text}}</div>
        <p v-if="candidate.status==='accepted'" class="hint">这些章节已经加入故事，可以继续阅读。</p>
        <p v-else-if="candidate.status==='discarded'" class="muted">这次写作已放弃。</p>
        <div v-else class="actions writing-result-actions"><button v-if="canContinue" class="primary" :disabled="busy||!store.online" @click="accept()">接着读 <ArrowRight :size="18"/></button><button :disabled="busy||!store.online" @click="discard">放弃</button></div>
        <p v-if="!canContinue&&candidate.status==='pending'" class="muted">这份草稿的篇幅或结构还未形成完整章节，正文已保留供你查看。</p>
        <button v-if="conflict&&canContinue" class="primary writing-fork" :disabled="busy||!store.online" @click="accept(true)">从原起点另开故事线</button>
    </section>
    <label v-if="history.length" class="writing-history">写作记录<select v-model="historyId" :disabled="busy||running||!!pending" @change="selectHistory"><option v-for="item in history" :key="item.kind+item.id" :value="item.kind+':'+item.id">{{new Date(item.createdAt).toLocaleString('zh-CN')}} · {{item.state==='succeeded'?(item.warningCount?'已保存 · 有内容提示':'已保存'):item.state==='cancelled'?'已停止':item.state==='failed'?'未完成':'写作中'}}</option></select></label>
  </section>
</template>
