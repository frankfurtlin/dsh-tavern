<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {api} from '../api'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import type {UsageFilters,UsagePage,UsageRecord,UsageSummary} from '../api/usage-types'
import '../styles/usage.css'
import ActionUsage from '../experience/ActionUsage.vue'
const props=defineProps<{refresh?:number}>(),store=useApp()
const rows=ref<UsageRecord[]>([]),summary=ref<UsageSummary|null>(null),options=ref<UsageFilters>({tasks:[],stories:[],profiles:[],taskLimit:200})
const taskId=ref(''),storyId=ref(''),profileId=ref(''),cursor=ref<string|null>(null),busy=ref(false),error=ref('')
const capture=useRequestScope(()=>[taskId.value,storyId.value,profileId.value]);let generation=0,disposed=false
const names:Record<string,string>={analysis:'已读资料分析',generation:'正文生成',review:'结果核验',director:'行动规划',narrator:'行动正文',connection_test:'连接测试',world_prepare:'世界准备',world_turn:'世界行动',world_write:'世界章节',role_chat:'角色对话',rewrite:'重写'}
const states:Record<string,string>={completed:'已收到回执',not_sent:'确认未发送',intent:'发送状态未确认',known:'供应商实测',unknown:'未知',estimated:'估算'}
function label(value:string){return names[value.split(':')[0]!]||value}
function number(value:number|null|undefined){return value==null?'未知':value.toLocaleString('zh-CN')}
function formatCost(value:number){return value>0&&value<1e-8?value.toString():value.toLocaleString('zh-CN',{maximumFractionDigits:8})}
function fee(row:UsageRecord){return row.costState==='known'&&row.cost!=null&&row.currency?`${row.currency} ${formatCost(row.cost)}`:'费用未知／套餐消耗'}
function query(next?:string){const value=new URLSearchParams({limit:'20'});for(const [key,item] of Object.entries({taskId:taskId.value,storyId:storyId.value,profileId:profileId.value,cursor:next}))if(item)value.set(key,item);return '/usage?'+value.toString()}
async function load(more=false,loadOptions=false){
  if(disposed)return
  const scope=capture(),epoch=++generation,valid=()=>!disposed&&scope.valid()&&epoch===generation,next=more?cursor.value:null
  if(more&&!next)return
  if(!more){rows.value=[];summary.value=null;cursor.value=null}
  busy.value=true;error.value=''
  try{
    const pagePromise=api<UsagePage>(query(next||undefined))
    // Observe both promises immediately; an options failure cannot leave an
    // unhandled records rejection after account or route changes.
    const [page,filters]=await Promise.all([pagePromise,loadOptions?api<UsageFilters>('/usage/filters'):Promise.resolve(null)])
    if(!valid())return
    rows.value=more?[...rows.value,...page.items.filter(row=>!rows.value.some(old=>old.id===row.id))]:page.items
    summary.value=page.summary;cursor.value=page.nextCursor;if(filters)options.value=filters
  }catch(reason){if(valid())error.value=(reason as Error).message}
  finally{if(valid())busy.value=false}
}
watch(()=>[props.refresh,store.user?.id],()=>{++generation;rows.value=[];summary.value=null;options.value={tasks:[],stories:[],profiles:[],taskLimit:200};taskId.value='';storyId.value='';profileId.value='';void load(false,true)},{immediate:true})
onUnmounted(()=>{disposed=true;++generation})
</script>
<template>
  <section class="divider usage-records" aria-labelledby="usage-records-title">
    <h2 id="usage-records-title">调用与用量</h2>
    <p class="muted">供应商实际 token 与预留上限分开记录。思考 token 已计入总输出，不重复相加；不展示思考内容。缺少费用信息时不能视为免费。</p>
    <div class="usage-filters">
      <label>故事<select v-model="storyId" @change="load()"><option value="">全部故事</option><option v-for="story in options.stories" :key="story.id" :value="story.id">{{story.title}}</option></select></label>
      <label>模型<select v-model="profileId" @change="load()"><option value="">全部模型</option><option v-for="profile in options.profiles" :key="profile.id" :value="profile.id">{{profile.name}} · {{profile.modelId}}</option></select></label>
      <label class="usage-task-filter">任务 ID<input v-model.trim="taskId" list="usage-task-options" placeholder="全部任务；可粘贴历史任务 ID" maxlength="32" @change="load()"><datalist id="usage-task-options"><option v-for="task in options.tasks" :key="task.id" :value="task.id">{{label(task.type)}} · {{new Date(task.createdAt).toLocaleString('zh-CN')}}</option></datalist></label>
    </div>
    <ActionUsage :refresh="refresh" :story-id="storyId" :profile-id="profileId"/><details><summary>详细调用、预算与原始核查记录</summary><p v-if="error" class="error" role="alert">{{error}}</p>
    <div v-if="summary" class="usage-summary" aria-label="筛选后的用量汇总">
      <strong>{{summary.calls}} 次调用记录</strong><span v-if="summary.notSentCalls">另有 {{summary.notSentCalls}} 次确认未发送</span><span v-if="summary.unconfirmedCalls">{{summary.unconfirmedCalls}} 次发送状态未确认</span>
      <span>输入 {{number(summary.inputTokens)}} / 总输出 {{number(summary.outputTokens)}} token</span>
      <span>其中思考 {{number(summary.reasoningTokens)}} token</span>
      <span v-for="(amount,currency) in summary.knownCostByCurrency" :key="currency">已知费用 {{currency}} {{formatCost(amount)}}</span>
      <span v-if="summary.unknownCostCalls">{{summary.unknownCostCalls}} 条费用未知／套餐消耗</span>
    </div>
    <p v-if="busy" role="status">正在读取调用记录…</p>
    <p v-else-if="!rows.length&&!error" class="muted">此范围尚无供应商调用记录。</p>
    <article v-for="row in rows" :key="row.id" class="list-item usage-record">
      <div class="row between"><strong>{{label(row.phase)}}</strong><span class="badge">{{states[row.usageState]||row.usageState}}</span></div>
      <p>{{row.story?.title||'未关联故事'}} · {{row.profile.name}} · {{row.profile.modelId||'历史模型未提供'}}<br><small>{{label(row.task.type)}} · 任务 {{row.taskId}}</small></p>
      <p>输入 {{number(row.inputTokens)}} / 总输出 {{number(row.outputTokens)}} token<br>其中思考 {{number(row.reasoningTokens)}} token · {{fee(row)}}</p>
      <p class="muted"><small>{{states[row.status]||row.status}} · 预留上限 {{number(row.reservedInput)}} / {{number(row.reservedOutput)}} token<br>{{new Date(row.createdAt).toLocaleString('zh-CN')}}</small></p>
    </article>
    <button v-if="cursor" :disabled="busy" @click="load(true)">载入更多调用记录</button>
    <button v-if="error" :disabled="busy" @click="load(false,true)">重新读取用量</button>
    </details>
  </section>
</template>
