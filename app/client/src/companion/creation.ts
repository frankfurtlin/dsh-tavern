import {canonical,ContractError,sha256,type Document,type DomainKind} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {emptyCard} from '../tavern/card-format'
import {styles,type CompanionProfile,type Relationship} from './contract'
import type {ChatSession} from '../tavern/chat-contract'
export interface CompanionCreationDraft {assetType:'companion-create-draft';status:'editing'|'ready'|'completed'|'discarded';name:string;personality:string;style:CompanionProfile['style'];sourceCard:{id:string;revision:number}|null;companionId:string;sessionId:string;identityId:string;operationId:string;createdAt:string}
const copy=<T>(v:T):T=>JSON.parse(canonical(v)),id=(v:unknown):v is string=>typeof v==='string'&&!!v&&v.length<=120
const keys='assetType,companionId,createdAt,identityId,name,operationId,personality,sessionId,sourceCard,status,style'
function fail(message:string):never{throw new ContractError('COMPANION_DRAFT',message+'，已保存内容保留')}
export function validateCompanionCreation(value:unknown,documentId?:string):asserts value is CompanionCreationDraft {
 const v=value as CompanionCreationDraft
 if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join(',')!==keys||v.assetType!=='companion-create-draft'||!['editing','ready','completed','discarded'].includes(v.status)||typeof v.name!=='string'||v.name.length>200||typeof v.personality!=='string'||v.personality.length>20000||typeof v.style!=='string'||!Object.hasOwn(styles,v.style)||![v.companionId,v.sessionId,v.identityId,v.operationId].every(id)||new Set([v.companionId,v.sessionId,v.identityId,v.operationId]).size!==4||typeof v.createdAt!=='string'||v.createdAt.length>50||!Number.isFinite(Date.parse(v.createdAt))||documentId!==undefined&&(!documentId.startsWith('companion-draft:')||documentId.length<=16||documentId.length>160))fail('伴侣草稿无效')
 if(v.sourceCard!==null&&(!v.sourceCard||typeof v.sourceCard!=='object'||Array.isArray(v.sourceCard)||Object.keys(v.sourceCard).sort().join(',')!=='id,revision'||typeof v.sourceCard.id!=='string'||!v.sourceCard.id||v.sourceCard.id.length>160||!Number.isSafeInteger(v.sourceCard.revision)||v.sourceCard.revision<1))fail('角色来源无效')
 if(['ready','completed'].includes(v.status)&&(!v.name.trim()||v.name!==v.name.trim()||v.sourceCard!==null))fail('独立伴侣需要名字；角色升格请先选择相识记录')
}
export function newCompanionCreation(){return {id:'companion-draft:'+crypto.randomUUID(),value:{assetType:'companion-create-draft' as const,status:'editing' as const,name:'',personality:'',style:'gentle' as const,sourceCard:null,companionId:crypto.randomUUID(),sessionId:crypto.randomUUID(),identityId:crypto.randomUUID(),operationId:'companion-create:'+crypto.randomUUID(),createdAt:new Date().toISOString()} as CompanionCreationDraft}}
export function readyCompanionCreation(value:CompanionCreationDraft){validateCompanionCreation(value);if(value.status!=='editing')fail('这份草稿已冻结');const next={...copy(value),name:value.name.trim(),status:'ready' as const};validateCompanionCreation(next);return next}
export function validateCompanionCreationUpdate(before:CompanionCreationDraft,after:CompanionCreationDraft){
 validateCompanionCreation(before);validateCompanionCreation(after)
 for(const key of ['companionId','sessionId','identityId','operationId','createdAt'] as const)if(before[key]!==after[key])fail('创建身份不能变更')
 const allowed={editing:['editing','ready','discarded'],ready:['ready','completed','discarded'],completed:['completed'],discarded:['discarded']}
 if(!allowed[before.status].includes(after.status))fail('已结束的创建不能重启')
 if(before.status!=='editing')for(const key of ['name','personality','style','sourceCard'] as const)if(canonical(before[key])!==canonical(after[key]))fail('已冻结的设定不能变更')
}
function createdObjects(v:CompanionCreationDraft){
 const card=emptyCard();card.identityId=v.identityId;card.name=v.name;card.personality=v.personality;card.firstMessage=''
 const profile:CompanionProfile={name:v.name,personality:v.personality,style:v.style,identityId:v.identityId,createdAt:v.createdAt,sessionId:v.sessionId,user:{name:'我',description:''},modelOverride:null,proactiveEnabled:false}
 const session:ChatSession={title:v.name,cards:[{id:'companion-card:'+v.companionId,revision:1,value:card}],messageIds:[],persona:profile.user,mode:'roleplay',authorNote:'',modelOverride:null,archived:false,parent:null,createdAt:v.createdAt,currentTaskId:null,companionId:v.companionId}
 const relationship:Relationship={companionId:v.companionId,algorithm:'shared-events-v1',events:[],wallet:1000,anniversaries:[]}
 return [{id:v.companionId,kind:'companion' as const,value:profile},{id:v.sessionId,kind:'session' as const,value:session},{id:'relationship:'+v.companionId,kind:'relationship' as const,value:relationship}]
}
export function companionCreationReferences(v:CompanionCreationDraft):{id:string;kind:DomainKind}[]{return [...(v.sourceCard?[{id:v.sourceCard.id,kind:'card' as const}]:[]),...(v.status==='completed'?createdObjects(v).map(({id,kind})=>({id,kind})):[])]}
export function validateCompanionCreationRelations(v:CompanionCreationDraft,lookup:(id:string,revision?:number)=>{kind:DomainKind;revision?:number;value:unknown}|null|undefined){
 if(v.sourceCard){const row=lookup(v.sourceCard.id,v.sourceCard.revision);if(row?.kind!=='card'||row.revision!==v.sourceCard.revision)fail('草稿角色来源版本缺失')}
 if(v.status==='completed')for(const expected of createdObjects(v)){const row=lookup(expected.id,1);if(row?.kind!==expected.kind||row.revision!==1||canonical(row.value)!==canonical(expected.value))fail('创建结果与冻结的设定不一致')}
}
async function acknowledged(v:LocalVault,operationId:string,writes:WriteDocument[]){const hash=await sha256(canonical(writes));try{await v.commitBundle(operationId,writes)}catch(error){if(!v.unlocked||(await v.receipt(operationId))?.payloadHash!==hash)throw error}}
export async function saveCompanionCreation(v:LocalVault,id:string,revision:number,value:CompanionCreationDraft){const frozen=copy(value);validateCompanionCreation(frozen,id);await acknowledged(v,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:revision,value:frozen}]);return (await v.read<CompanionCreationDraft>(id))!}
export async function createDraftCompanion(v:LocalVault,input:Document<CompanionCreationDraft>){
 const row=copy(input);validateCompanionCreation(row.value,row.id);const source=await v.read(row.id,row.revision)
 if(row.kind!=='settings'||source?.kind!=='settings'||canonical(source.value)!==canonical(row.value)||row.value.status!=='ready')fail('请使用实际保存并冻结的伴侣草稿')
 const value=row.value,writes:WriteDocument[]=[...createdObjects(value).map(item=>({...item,expectedRevision:0})),{id:row.id,kind:'settings',expectedRevision:row.revision,value:{...value,status:'completed'}}]
 await acknowledged(v,value.operationId,writes);return value.companionId
}
