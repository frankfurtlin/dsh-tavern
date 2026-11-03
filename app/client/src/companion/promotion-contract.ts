import {canonical,ContractError,type DomainKind,type Document} from '../domain/local-contract'
import type {CharacterCard} from '../tavern/card-format'
import type {ChatSession,ChatMessage} from '../tavern/chat-contract'

export interface PromotionSource {kind:'card'|'session';id:string;revision:number;cardId:string;cardRevision:number;identityId:string}
export interface PromotionMemory {messageId:string;messageRevision:number;variantId:string;role:'user'|'assistant';speakerId:string|null;text:string;at:string}
export interface CompanionPromotion {version:'companion-promotion-v1';source:PromotionSource;memories:PromotionMemory[]}
export interface PromotedMemorySource {kind:'promotion';text:string;sessionId:string;messageId:string;messageRevision:number;variantId:string;at:string}
type Lookup=(id:string,revision?:number)=>{kind:DomainKind;revision?:number;value:unknown}|undefined
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const id=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=160
const revision=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>0
const date=(v:unknown):v is string=>typeof v==='string'&&v.length<=50&&Number.isFinite(Date.parse(v))
function fail(message:string):never{throw new ContractError('COMPANION_PROMOTION',message+'；原角色与原聊天保留')}
export function validatePromotion(value:unknown):asserts value is CompanionPromotion{
 if(!record(value)||value.version!=='companion-promotion-v1'||!record(value.source)||!['card','session'].includes(String(value.source.kind))||!id(value.source.id)||!revision(value.source.revision)||!id(value.source.cardId)||!revision(value.source.cardRevision)||!id(value.source.identityId)||!Array.isArray(value.memories)||value.memories.length>50)fail('升格来源无效')
 if(value.source.kind==='card'&&(value.source.cardId!==value.source.id||value.source.cardRevision!==value.source.revision||value.memories.length))fail('角色模板不能冒用聊天记忆')
 for(const memory of value.memories)if(!record(memory)||!id(memory.messageId)||!revision(memory.messageRevision)||!id(memory.variantId)||!['user','assistant'].includes(String(memory.role))||(memory.role==='user'?memory.speakerId!==null:!id(memory.speakerId))||typeof memory.text!=='string'||!memory.text.trim()||memory.text.length>8000||!date(memory.at))fail('选定共同记忆无效')
 if(new Set(value.memories.map(memory=>memory.messageId)).size!==value.memories.length||value.memories.reduce((n,m)=>n+m.text.length,0)>128000)fail('选定记忆重复或超过本次上限')
}
export function validatePromotedMemorySource(value:unknown):asserts value is PromotedMemorySource{
 if(!record(value)||value.kind!=='promotion'||!id(value.sessionId)||!id(value.messageId)||!revision(value.messageRevision)||!id(value.variantId)||typeof value.text!=='string'||!value.text.trim()||value.text.length>8000||!date(value.at))fail('共同记忆来源无效')
}
export function promotionReferences(kind:DomainKind,value:Record<string,unknown>):{id:string;kind:DomainKind;revision?:number}[]{
 if(kind==='companion'&&value.promotion){const p=value.promotion as CompanionPromotion,card=value.sourceCard as {id:string;revision:number}|undefined;return [{id:p.source.id,kind:p.source.kind,revision:p.source.revision},...(card?[{id:card.id,kind:'card' as const,revision:card.revision}]:[]),...p.memories.map(m=>({id:m.messageId,kind:'message' as const,revision:m.messageRevision}))]}
 if(kind==='memory'&&(value.source as Record<string,unknown>)?.kind==='promotion'){const source=value.source as unknown as PromotedMemorySource;return [{id:String(value.companionId),kind:'companion'},{id:source.sessionId,kind:'session'},{id:source.messageId,kind:'message',revision:source.messageRevision}]}
 return []
}
export function validatePromotionRelations(kind:DomainKind,value:Record<string,unknown>,lookup:Lookup){
 if(kind==='companion'&&value.promotion){const p=value.promotion as CompanionPromotion,root=lookup(p.source.id,p.source.revision);if(root?.kind!==p.source.kind||root.revision!==p.source.revision)fail('升格来源版本缺失')
  let card:CharacterCard
  if(p.source.kind==='card')card=root.value as CharacterCard
  else{const session=root.value as ChatSession,member=session.cards[0];if(session.companionId||session.cards.length!==1||member?.id!==p.source.cardId||member.revision!==p.source.cardRevision)fail('只能从原角色的独立聊天升格');card=member.value}
  if(card.identityId!==p.source.identityId||value.identityId===card.identityId)fail('伴侣与原角色的独立身份不一致')
  if(value.sourceCard){const selected=value.sourceCard as {id:string;revision:number},library=lookup(selected.id,selected.revision);if(selected.id!==p.source.cardId||selected.revision!==p.source.cardRevision||library?.kind!=='card'||library.revision!==selected.revision||(library.value as CharacterCard).identityId!==p.source.identityId)fail('原角色回跳不属于选定的升格身份')}
  for(const memory of p.memories){const session=root.value as ChatSession,row=lookup(memory.messageId,memory.messageRevision),message=row?.value as ChatMessage|undefined,variant=message?.variants.find(v=>v.id===memory.variantId);if(!session.messageIds.includes(memory.messageId)||row?.kind!=='message'||row.revision!==memory.messageRevision||message?.sessionId!==p.source.id||message.hidden||message.selectedVariantId!==memory.variantId||message.role!==memory.role||message.speakerId!==memory.speakerId||variant?.state!=='complete'||variant.text!==memory.text||variant.createdAt!==memory.at)fail('共同记忆不属于选定的可见完整消息')}
 }
 if(kind==='memory'&&(value.source as Record<string,unknown>)?.kind==='promotion'){const source=value.source as unknown as PromotedMemorySource,companion=lookup(String(value.companionId)),p=(companion?.value as {promotion?:CompanionPromotion}|undefined)?.promotion,memory=p?.memories.find(m=>m.messageId===source.messageId&&m.messageRevision===source.messageRevision&&m.variantId===source.variantId);if(companion?.kind!=='companion'||!memory||p!.source.kind!=='session'||p!.source.id!==source.sessionId||source.text!==memory.text||source.at!==memory.at)fail('此共同记忆没有这位伴侣的升格授权')}
}
export function selectPromotionMemory(row:Document<ChatMessage>,session:Document<ChatSession>):PromotionMemory{
 const message=row.value,variant=message.variants.find(v=>v.id===message.selectedVariantId)
 if(row.kind!=='message'||message.sessionId!==session.id||!session.value.messageIds.includes(row.id)||message.hidden||!['user','assistant'].includes(message.role)||!variant||variant.state!=='complete'||!variant.text.trim()||variant.text.length>8000)fail('只能选择此聊天中可见的完整消息')
 return {messageId:row.id,messageRevision:row.revision,variantId:variant.id,role:message.role as PromotionMemory['role'],speakerId:message.speakerId,text:variant.text,at:variant.createdAt}
}
/** The new identity owns a portable snapshot; vault-bound novel provenance stays on its source. */
export function detachNovelCard(card:CharacterCard):CharacterCard{
 const detached=JSON.parse(canonical(card)) as CharacterCard
 if(detached.novelOrigin){delete detached.novelOrigin;for(const holder of [detached.data,detached.source,(detached.source as Record<string,unknown>).data])if(record(holder)&&record(holder.extensions)&&record(holder.extensions.readerNovel)&&holder.extensions.readerNovel.format==='reader-novel-character-v1')delete holder.extensions.readerNovel}
 return detached
}
