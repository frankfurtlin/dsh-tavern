import {canonical,ContractError,type Document} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {emptyCard,type CharacterCard} from '../tavern/card-format'
import type {ChatSession} from '../tavern/chat-contract'
import {detachNovelCard,type CompanionPromotion,type PromotedMemorySource} from './promotion-contract'
import {companionWorldContext,type CompanionWorld} from './world-context'
export const styles={gentle:'温柔',cheerful:'开朗',reserved:'内敛',thoughtful:'细腻',playful:'俏皮',steady:'稳重',custom:'自定义'} as const
export interface CompanionProfile {name:string;identityId:string;personality:string;style:keyof typeof styles;createdAt:string;sessionId?:string;user:{name:string;description:string};modelOverride:string|null;memories?:{text:string;source:'user';at:string}[];proactiveEnabled:false;deleted?:boolean;sourceCard?:{id:string;revision:number};promotion?:CompanionPromotion;world?:CompanionWorld}
export interface CompanionMemory {companionId:string;text:string;status:'active'|'retired';source:{kind:'user'|'message'|'import';text:string;messageId?:string;sessionId?:string;at:string}|PromotedMemorySource;createdAt:string}
export interface RelationshipEvent {id:string;type:'conversation'|'gift'|'anniversary';at:string;sourceId:string;label:string;amount?:number}
export interface Relationship {companionId:string;algorithm:'shared-events-v1';events:RelationshipEvent[];wallet:number;anniversaries:{id:string;title:string;date:string}[]}
export function relationshipScore(value:Relationship){return {familiarity:value.events.filter(event=>event.type==='conversation').length,sharedDays:new Set(value.events.filter(event=>event.type==='conversation').map(event=>event.at.slice(0,10))).size}}
export function appendRelationship(value:Relationship,event:RelationshipEvent):Relationship {const previous=value.events.find(item=>item.id===event.id);if(previous){if(canonical(previous)!==canonical(event))throw new ContractError('IDEMPOTENCY_CONFLICT','这条共同记录已存在，内容不同');return value}return {...value,events:[...value.events,event]}}
export async function relationshipWrite(vault:LocalVault,companionId:string,event:RelationshipEvent):Promise<WriteDocument>{const id='relationship:'+companionId,row=await vault.read<Relationship>(id),value=row?.value||{companionId,algorithm:'shared-events-v1' as const,events:[],wallet:1000,anniversaries:[]};if(row&&row.kind!=='relationship'||value.companionId!==companionId)throw new ContractError('COMPANION_SCOPE','共同记录不属于这位伴侣');return {kind:'relationship',id,expectedRevision:row?.revision||0,value:appendRelationship(value,event)}}
export async function createCompanion(vault:LocalVault,input:Pick<CompanionProfile,'name'|'personality'|'style'>,sourceCard?:Document<CharacterCard>):Promise<string>{
  const id=crypto.randomUUID(),sessionId=crypto.randomUUID(),at=new Date().toISOString(),card=sourceCard?detachNovelCard(sourceCard.value):emptyCard();card.identityId=crypto.randomUUID();card.name=input.name.trim();card.personality=input.personality;card.firstMessage='';if(!card.name)throw new ContractError('COMPANION_NAME','请填写伴侣名字')
  const profile:CompanionProfile={...input,name:card.name,identityId:card.identityId,createdAt:at,sessionId,user:{name:'我',description:''},modelOverride:null,proactiveEnabled:false,...(sourceCard?{sourceCard:{id:sourceCard.id,revision:sourceCard.revision}}:{})}
  const chat:ChatSession={title:card.name,cards:[{id:'companion-card:'+id,revision:1,value:card}],messageIds:[],persona:profile.user,mode:'roleplay',authorNote:'',modelOverride:null,archived:false,parent:null,createdAt:at,currentTaskId:null,companionId:id}
  await vault.commitBundle('create-companion:'+id,[{kind:'companion',id,expectedRevision:0,value:profile},{kind:'session',id:sessionId,expectedRevision:0,value:chat},{kind:'relationship',id:'relationship:'+id,expectedRevision:0,value:{companionId:id,algorithm:'shared-events-v1',events:[],wallet:1000,anniversaries:[]}}]);return id
}
/** A separate private scope; the caller freezes this exact version in the task. */
export async function companionContext(vault:LocalVault,session:ChatSession):Promise<{id:string;text:string}[]>{
  if(!session.companionId)return [];if(session.cards.length!==1)throw new ContractError('COMPANION_SCOPE','伴侣记忆只用于这位伴侣的独立会话')
  const row=await vault.read<CompanionProfile>(session.companionId);if(!row||row.kind!=='companion'||row.value.deleted||row.value.identityId!==session.cards[0]?.value.identityId)throw new ContractError('COMPANION_SCOPE','伴侣资料已失效，原会话保留')
  const profile=row.value,memories=(await vault.list<CompanionMemory>('memory')).filter(memory=>memory.value.companionId===row.id&&memory.value.status==='active')
  const identity={id:'companion:'+row.id+':'+row.revision,text:'本轮伴侣资料（本版本优先于旧对话）：\n'+JSON.stringify({name:profile.name,personality:profile.personality,style:styles[profile.style],user:profile.user||{name:'我',description:''}})+'\n这是双方自愿的虚构陪伴互动。不要声称知道真实位置、联系人或设备行为。不要替用户发言。'}
  const world=companionWorldContext(profile.world)
  return [identity,...(world?[{id:'companion-world:'+row.id+':'+row.revision,text:world}]:[]),...memories.map(memory=>({id:'memory:'+memory.id+':'+memory.revision,text:'用户明确保留的记忆（与旧对话矛盾时按此更正）：'+memory.value.text})),...(profile.memories||[]).map((memory,index)=>({id:'legacy-memory:'+row.id+':'+row.revision+':'+index,text:'用户明确保留的记忆：'+memory.text}))]
}
