import {canonical,sha256,ContractError,type Document,type DomainKind,type ProviderConfiguration} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {slotModel} from '../repositories/local-models'
import {agentTools,parseAgentPlan,type AgentRun,type AgentSession,type AgentChange,type AgentResult} from './contract'
import {childReferences,parentTools,teamId,validateAgentTeam,type AgentTeam,type AgentChildInput} from './team-contract'
import {startAgentRun,planAgentRun,stopAgentRun} from './runtime'
const copy=<T>(v:T):T=>JSON.parse(canonical(v)),now=()=>new Date().toISOString(),works=new Map<string,Promise<void>>()
function fail(code:string,message:string):never{throw new ContractError(code,message)}
type Condition={id:string;revision:number;kind:DomainKind}
async function commit(vault:LocalVault,operation:string,documents:WriteDocument[],conditions:Condition[]=[],signal?:AbortSignal){const hash=await sha256(canonical(conditions.length?{documents,preconditions:conditions}:documents));try{await vault.commitBundle(operation,documents,signal,conditions)}catch(error){if(!vault.unlocked||(await vault.receipt(operation))?.payloadHash!==hash)throw error}}

export async function teamAuthority(vault:LocalVault,team:Document<AgentTeam>,signal?:AbortSignal){
 signal?.throwIfAborted();if(team.value.historical||['cancelled','unknown'].includes(team.value.status))fail('AGENT_PARENT_STOPPED','父子任务已停止或待核对，未继续执行')
 const parent=await vault.read<AgentRun>(team.value.parentId),session=await vault.read<AgentSession>(team.value.sessionId)
 if(!parent||parent.kind!=='agentRun'||parent.value.historical||['cancelled','failed','unknown'].includes(parent.value.status)||!session||session.value.archived)fail('AGENT_PARENT_STOPPED','父任务或会话已停止')
 if(team.value.limits.calls>session.value.limits.calls||team.value.limits.steps>session.value.limits.steps)fail('AGENT_PARENT_LIMIT','父会话的总上限已调低，请停止这组任务重新分配')
 for(const child of team.value.children)if(child.referenceIds.some(root=>!session.value.referenceIds.includes(root)))fail('AGENT_REVOKED','父会话已撤销子任务引用')
 const messages=await vault.readMany(parent.value.references.filter(ref=>ref.kind==='message'&&team.value.children.some(child=>child.referenceIds.includes(ref.rootId))).map(ref=>ref.id))
 if(messages.some(row=>(row.value as {hidden:boolean}).hidden!==false))fail('AGENT_HIDDEN','父任务消息已隐藏，子任务访问停止')
 signal?.throwIfAborted();return {parent,session}
}
export async function delegatedAuthority(vault:LocalVault,run:AgentRun,signal?:AbortSignal){
 if(!run.delegation)return null
 const row=await vault.read<AgentTeam>(run.delegation.teamId);if(!row||row.value.assetType!=='agent-team')fail('AGENT_PARENT','父任务分配不存在');const authority=await teamAuthority(vault,row!,signal),allocation=row!.value.children[run.delegation.index]
 if(!allocation||allocation.calls===1&&canonical(run.configSnapshot??null)!==canonical(row!.value.configSnapshot??null)||run.delegation.parentId!==row!.value.parentId||canonical(run.references)!==canonical(childReferences(authority.parent.value,allocation.referenceIds))||canonical(run.delegation.tools)!==canonical(allocation.tools))fail('AGENT_SCOPE','子任务资料或工具超过父分配')
 return {team:row!,...authority,allocation}
}

/** Reservations include the parent's complete plan, including its remaining steps. */
export async function createAgentTeam(vault:LocalVault,parentId:string,inputs:AgentChildInput[],signal?:AbortSignal){
 signal?.throwIfAborted();const parent=await vault.read<AgentRun>(parentId)
 if(!parent||parent.kind!=='agentRun'||parent.value.delegation||parent.value.historical||!['ready','needs_approval','completed'].includes(parent.value.status))fail('AGENT_PARENT','请从已准备的顶层任务派生子任务')
 const session=await vault.read<AgentSession>(parent.value.sessionId);if(!session||session.value.archived)fail('AGENT_REVOKED','父会话已停用');if(await vault.read(teamId(parentId)))fail('AGENT_TEAM_EXISTS','此父任务已有分配，查看原子任务即可')
 if(!inputs.length||inputs.length>3)fail('AGENT_TEAM_COUNT','一次可派生 1 至 3 个子任务')
 const at=now(),documents:WriteDocument[]=[],children:AgentTeam['children']=[],profile=inputs.some(c=>!c.plan)?parent.value.configSnapshot||await slotModel(vault,'text'):null
 for(const [index,input] of inputs.entries()){
  const references=childReferences(parent.value,input.referenceIds),tools=[...input.tools];if(!tools.length||tools.some(tool=>!parentTools(parent.value).includes(tool)))fail('AGENT_CAPABILITY','子任务工具不能超出父任务')
  const plan=input.plan?parseAgentPlan(input.plan,input.steps):[];if(plan.some(call=>!tools.includes(call.tool)))fail('AGENT_CAPABILITY','子计划使用了未分配工具')
  const id=crypto.randomUUID(),allocation={runId:id,instruction:input.instruction,steps:input.steps,calls:input.plan?0:1,referenceIds:[...input.referenceIds],tools};children.push(allocation)
  const value:AgentRun={sessionId:parent.value.sessionId,instruction:input.instruction,references,policy:{...copy(parent.value.policy),mode:parent.value.policy.mode==='read'?'read':'confirm',limits:{steps:input.steps,calls:1,parallel:1},grants:parent.value.policy.grants.filter(g=>input.referenceIds.includes(g.targetId)).map(g=>({...g,tools:g.tools.filter(tool=>tools.includes(tool))})).filter(g=>g.tools.length)},delegation:{teamId:teamId(parentId),parentId,index,tools},plan,cursor:0,status:input.plan?'ready':'queued',results:[],changeSetId:null,createdAt:at,updatedAt:at,error:null,...(!input.plan?{configSnapshot:copy(profile!)}:{})}
  documents.push({kind:'agentRun',id,expectedRevision:0,value})
 }
 const limits={...parent.value.policy.limits},value:AgentTeam={assetType:'agent-team',parentId,parentRevision:parent.revision,sessionId:parent.value.sessionId,children,limits,rootSteps:parent.value.plan.length,rootCalls:parent.value.provider?1:0,activeIds:[],status:'ready',historical:false,createdAt:at,updatedAt:at,error:null,...(profile?{configSnapshot:copy(profile)}:{})};validateAgentTeam(value,teamId(parentId));if(children.some(child=>child.referenceIds.some(id=>!session.value.referenceIds.includes(id))))fail('AGENT_REVOKED','引用已撤销')
 await commit(vault,'agent-team-create:'+parentId,[...documents,{kind:'settings',id:teamId(parentId),expectedRevision:0,value},{kind:'settings',id:session.id,expectedRevision:session.revision,value:{...session.value,runIds:[...session.value.runIds,...children.map(c=>c.runId)]}}],[{id:parent.id,kind:'agentRun',revision:parent.revision},...(profile?[{id:profile.id,kind:'settings' as const,revision:(await vault.read(profile.id))!.revision}]:[])],signal)
 signal?.throwIfAborted();return (await vault.read<AgentTeam>(teamId(parentId)))!
}

/** Atomically claim/release a child slot alongside its execution ownership and budget receipt. */
export async function augmentTeamCommit(vault:LocalVault,documents:WriteDocument[],conditions:Condition[],signal?:AbortSignal){
 const additions:WriteDocument[]=[],checks=[...conditions]
 for(const document of documents){if(document.kind!=='agentRun')continue;const next=document.value as AgentRun;if(!next.delegation)continue;const previous=await vault.read<AgentRun>(document.id),team=await vault.read<AgentTeam>(next.delegation.teamId);if(!team)fail('AGENT_PARENT','父子任务记录不存在');const claiming=!!next.execution&&!previous?.value.execution,releasing=!next.execution&&!!previous?.value.execution
  if(claiming){const {session}=await teamAuthority(vault,team,signal);if(!['running','completed'].includes(team.value.status))fail('AGENT_TEAM_PAUSED','先开始这组子任务');if(team.value.activeIds.length>=Math.min(team.value.limits.parallel,session.value.limits.parallel)&&!team.value.activeIds.includes(document.id))fail('AGENT_PARALLEL_LIMIT','父任务并发位置已用完');const value={...team.value,status:'running' as const,activeIds:[...new Set([...team.value.activeIds,document.id])],updatedAt:now()};additions.push({kind:'settings',id:team.id,expectedRevision:team.revision,value});checks.push({id:session.id,revision:session.revision,kind:'settings'})}
  else if(releasing&&team.value.activeIds.includes(document.id)){const ids=team.value.activeIds.filter(id=>id!==document.id),others=await vault.readMany<AgentRun>(team.value.children.filter(child=>child.runId!==document.id).map(child=>child.runId));additions.push({kind:'settings',id:team.id,expectedRevision:team.revision,value:{...team.value,status:ids.length===0&&!others.some(row=>row.value.status==='queued')?'completed':team.value.status,activeIds:ids,updatedAt:now()}})}
 }
 return {documents:[...documents,...additions],conditions:checks,delegated:additions.length>0}
}
export function startAgentTeam(vault:LocalVault,id:string,transport:typeof fetch=fetch){
 const key=vault.id+':'+id,old=works.get(key)
 if(old)return old
 const work=(async()=>{
  let team=await vault.read<AgentTeam>(id)
  if(!team||team.value.status!=='ready'||team.value.historical)fail('AGENT_TEAM_STARTED','这组任务已有执行记录，不会重放未知模型请求')
  const authority=await teamAuthority(vault,team)
  await commit(vault,'agent-team-start:'+id,[{kind:'settings',id,expectedRevision:team.revision,value:{...team.value,status:'running',updatedAt:now()}}],[{id:authority.session.id,kind:'settings',revision:authority.session.revision},{id:authority.parent.id,kind:'agentRun',revision:authority.parent.revision}])
  const waiting=[...team.value.children],limit=Math.min(team.value.limits.parallel,authority.session.value.limits.parallel),deadline=Date.now()+30000
  const worker=async()=>{
   while(waiting.length&&vault.unlocked){
    const live=(await vault.read<AgentTeam>(id))!
    if(['cancelled','unknown'].includes(live.value.status))return
    await teamAuthority(vault,live)
    const child=waiting.shift()!,row=await vault.read<AgentRun>(child.runId)
    if(!row||!['ready','queued'].includes(row.value.status))continue
    try{
     if(row.value.status==='queued')await planAgentRun(vault,row.id,transport)
     else await startAgentRun(vault,row.id)
    }catch(error){
     if((error as {code?:string}).code==='AGENT_PARALLEL_LIMIT'){
      waiting.unshift(child)
      if(Date.now()>deadline)fail('AGENT_PARALLEL_LIMIT','并发位置等待超时，未开始的请求保留')
      await new Promise(resolve=>setTimeout(resolve,100))
     }else{
      const current=await vault.read<AgentRun>(child.runId)
      if(current&&['ready','queued'].includes(current.value.status)&&!current.value.execution){
       await commit(vault,'agent-team-child-failed:'+child.runId,[{kind:'agentRun',id:current.id,expectedRevision:current.revision,value:{...current.value,status:'failed',updatedAt:now(),error:error instanceof ContractError?error.message:'子任务未完成，已保存结果保留'}}])
      }else if(current?.value.execution)throw error
     }
    }
   }
  }
  try{
   await Promise.all(Array.from({length:Math.min(limit,waiting.length)},worker))
   if(vault.unlocked){
    team=(await vault.read<AgentTeam>(id))!
    if(team.value.status==='running'){
     const children=await vault.readMany<AgentRun>(team.value.children.map(child=>child.runId))
     if(children.length!==team.value.children.length||children.some(child=>['queued','planning','streaming','running'].includes(child.value.status)))fail('AGENT_TEAM_INCOMPLETE','仍有未确认完成的子任务，已保存结果保留')
     await commit(vault,'agent-team-finish:'+id,[{kind:'settings',id,expectedRevision:team.revision,value:{...team.value,status:'completed',activeIds:[],updatedAt:now()}}])
    }
   }
  }catch(error){
   if(vault.unlocked){
    team=(await vault.read<AgentTeam>(id))!
    if(team.value.status==='running')await commit(vault,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:team.revision,value:{...team.value,status:'unknown',error:error instanceof ContractError?error.message:'子任务已暂停，已知结果保留',activeIds:[],updatedAt:now()}}]).catch(()=>{})
   }
   throw error
  }
 })().finally(()=>works.delete(key))
 works.set(key,work)
 return work
}
export async function stopAgentTeam(vault:LocalVault,id:string){for(let retry=0;retry<3;retry++){const team=await vault.read<AgentTeam>(id);if(!team)return;if(team.value.status!=='cancelled')try{await commit(vault,'agent-team-stop:'+id+':'+team.revision,[{kind:'settings',id,expectedRevision:team.revision,value:{...team.value,status:'cancelled',activeIds:[],updatedAt:now(),error:'子任务已停止；已保存结果和合并版本保留'}}])}catch(error){if((error as {code?:string}).code==='REVISION_CONFLICT'&&retry<2)continue;throw error}await Promise.all(team.value.children.map(child=>stopAgentRun(vault,child.runId)));return}}
export async function recoverAgentTeam(vault:LocalVault,row:Document<AgentTeam>){if(row.value.status!=='running'||works.has(vault.id+':'+row.id)||Date.parse(row.updatedAt)+30000>Date.now())return row;const children=await vault.readMany<AgentRun>(row.value.activeIds);if(children.some(child=>child.value.execution&&Date.parse(child.value.execution.leaseUntil)>Date.now()))return row;await commit(vault,crypto.randomUUID(),[{kind:'settings',id:row.id,expectedRevision:row.revision,value:{...row.value,status:'unknown',activeIds:[],error:'调度已断开；已保存结果保留，不重发原模型请求',updatedAt:now()}}]);return (await vault.read<AgentTeam>(row.id))!}

/** All selected non-overlapping targets and receipts commit together. Children never write live content. */
export async function mergeAgentChildren(vault:LocalVault,id:string,changeIds:string[],accept=true,signal?:AbortSignal){
 if(!changeIds.length||new Set(changeIds).size!==changeIds.length)fail('AGENT_TEAM_MERGE','请选择不同的修改提案');const operation='agent-team-merge:'+id+':'+(await sha256(canonical({changeIds:[...changeIds].sort(),accept}))).slice(0,32);if(await vault.receipt(operation))return
 const team=await vault.read<AgentTeam>(id);if(!team)fail('AGENT_PARENT','子任务组不存在');const {parent,session}=await teamAuthority(vault,team!,signal);if(accept&&(parent.value.policy.mode==='read'||session.value.mode==='read'))fail('AGENT_READ_ONLY','父任务当前为只读，不能合并')
 const documents:WriteDocument[]=[],targets=new Set<string>(),runs=new Set<string>();for(const changeId of changeIds){const row=await vault.read<AgentChange>(changeId);if(!row||row.value.status!=='proposed'||!team!.value.children.some(c=>c.runId===row.value.runId))fail('AGENT_TEAM_MERGE','提案不属于本组待确认子任务');const c=row!.value,child=await vault.read<AgentRun>(c.runId);if(!child||child.value.status!=='needs_approval'||child.value.changeSetId!==changeId||runs.has(c.runId))fail('AGENT_TEAM_MERGE','原子任务已停止或提案改变');await delegatedAuthority(vault,child!.value,signal);if(accept&&targets.has(c.targetId))fail('AGENT_MERGE_CONFLICT','两项提案修改同一对象，请只选一项后重新比较');targets.add(c.targetId);runs.add(c.runId)
  const target=await vault.read(c.targetId);if(accept&&(!target||target.revision!==c.expectedRevision||canonical(target.value)!==canonical(c.before)))fail('AGENT_MERGE_CONFLICT','目标已有新版本，未覆盖任何选中对象')
  if(accept)documents.push({kind:c.targetKind,id:c.targetId,expectedRevision:c.expectedRevision,value:c.after})
  const result:AgentResult={callId:c.callId,tool:c.tool,status:accept?'completed':'rejected',summary:accept?'父任务确认并合并':'父任务拒绝，原对象保留',data:{targetId:c.targetId,revision:accept?c.expectedRevision+1:c.expectedRevision},changeSetId:changeId},next={...child!.value,status:child!.value.cursor+1===child!.value.plan.length?'completed' as const:'ready' as const,cursor:child!.value.cursor+1,results:[...child!.value.results,result],changeSetId:null,updatedAt:now()};delete next.execution
  documents.push({kind:'changeSet',id:row!.id,expectedRevision:row!.revision,value:{...c,status:accept?'applied':'rejected',appliedRevision:accept?c.expectedRevision+1:null}},{kind:'agentRun',id:child!.id,expectedRevision:child!.revision,value:next})
 }
 documents.push({kind:'settings',id:team!.id,expectedRevision:team!.revision,value:{...team!.value,activeIds:team!.value.activeIds.filter(id=>!runs.has(id)),updatedAt:now()}})
 await commit(vault,operation,documents,[{id:parent.id,revision:parent.revision,kind:'agentRun'},{id:session.id,revision:session.revision,kind:'settings'}],signal)
}
