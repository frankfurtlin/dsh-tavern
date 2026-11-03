import {canonical,ContractError,type Document,type ProviderConfiguration} from '../domain/local-contract'
import {agentTools,type AgentCall,type AgentReference,type AgentRun,type AgentTool} from './contract'
export interface AgentChild {runId:string;instruction:string;steps:number;calls:number;referenceIds:string[];tools:AgentTool[]}
export interface AgentTeam {assetType:'agent-team';parentId:string;parentRevision:number;sessionId:string;configSnapshot?:Document<ProviderConfiguration>;children:AgentChild[];limits:{steps:number;calls:number;parallel:number};rootSteps:number;rootCalls:number;activeIds:string[];status:'ready'|'running'|'completed'|'cancelled'|'unknown';historical:boolean;createdAt:string;updatedAt:string;error:string|null}
export interface AgentDelegation {teamId:string;parentId:string;index:number;tools:AgentTool[]}
export interface AgentChildInput {instruction:string;referenceIds:string[];tools:AgentTool[];steps:number;plan?:AgentCall[]}
export const teamId=(parent:string)=>'agent-team:'+parent
const id=(value:unknown)=>typeof value==='string'&&!!value&&value.length<=160,num=(value:unknown)=>Number.isSafeInteger(value)&&Number(value)>=0
function fail(message:string):never{throw new ContractError('AGENT_TEAM',message+'；原任务保留')}
export function validateAgentTeam(value:unknown,documentId?:string):asserts value is AgentTeam{
 const v=value as AgentTeam
 if(!v||v.assetType!=='agent-team'||!id(v.parentId)||!id(v.sessionId)||!num(v.parentRevision)||v.parentRevision<1||documentId!==undefined&&documentId!==teamId(v.parentId)||!v.limits||!num(v.limits.steps)||v.limits.steps<1||v.limits.steps>64||!num(v.limits.calls)||v.limits.calls<1||v.limits.calls>8||!num(v.limits.parallel)||v.limits.parallel<1||v.limits.parallel>3||!num(v.rootSteps)||!num(v.rootCalls)||!Array.isArray(v.children)||!v.children.length||v.children.length>3||!Array.isArray(v.activeIds)||v.activeIds.length>v.limits.parallel||new Set(v.activeIds).size!==v.activeIds.length||!['ready','running','completed','cancelled','unknown'].includes(v.status)||typeof v.historical!=='boolean'||typeof v.createdAt!=='string'||typeof v.updatedAt!=='string'||v.error!==null&&typeof v.error!=='string')fail('子任务账本格式无效')
 for(const c of v.children)if(!c||!id(c.runId)||c.runId===v.parentId||typeof c.instruction!=='string'||!c.instruction.trim()||c.instruction.length>20000||!num(c.steps)||c.steps<1||!num(c.calls)||c.calls>1||!Array.isArray(c.referenceIds)||!c.referenceIds.length||c.referenceIds.length>32||!c.referenceIds.every(id)||new Set(c.referenceIds).size!==c.referenceIds.length||!Array.isArray(c.tools)||!c.tools.length||!c.tools.every(tool=>agentTools.includes(tool))||new Set(c.tools).size!==c.tools.length)fail('子任务分配无效')
 if(v.children.some(child=>child.calls===1)&&(!v.configSnapshot||v.configSnapshot.kind!=='settings'||!id(v.configSnapshot.id)||!v.configSnapshot.id.startsWith('model:')||!num(v.configSnapshot.revision)||v.configSnapshot.revision<1||!v.configSnapshot.value||v.configSnapshot.value.id!==v.configSnapshot.id))fail('模型子任务必须共享冻结配置')
 if(new Set(v.children.map(c=>c.runId)).size!==v.children.length||v.activeIds.some(id=>!v.children.some(c=>c.runId===id))||v.rootSteps+v.children.reduce((n,c)=>n+c.steps,0)>v.limits.steps||v.rootCalls+v.children.reduce((n,c)=>n+c.calls,0)>v.limits.calls)fail('子任务超出父任务总预算或范围')
}
export function childReferences(parent:AgentRun,roots:string[]):AgentReference[]{
 if(!roots.length||new Set(roots).size!==roots.length||roots.some(id=>!parent.references.some(ref=>ref.id===id&&ref.rootId===id)))fail('子任务仅可选择父任务已冻结的根资料')
 const refs=parent.references.filter(ref=>roots.includes(ref.rootId));if(refs.some(ref=>ref.kind==='message'&&ref.value.hidden!==false))fail('隐藏消息不可派生')
 return JSON.parse(canonical(refs)) as AgentReference[]
}
export function parentTools(parent:AgentRun):AgentTool[]{return parent.policy.mode==='read'?['search','read','compare']:[...agentTools]}
