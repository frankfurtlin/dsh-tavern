import {canonical,ContractError,sha256,type Document} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import type {CharacterCard} from '../tavern/card-format'
import type {ChatSession,ChatMessage} from '../tavern/chat-contract'
import type {CompanionProfile,CompanionMemory} from './contract'
import {detachNovelCard,selectPromotionMemory,validatePromotion,type CompanionPromotion,type PromotionSource,type PromotionMemory} from './promotion-contract'

export type PromotionTarget={kind:'card'|'session';id:string;revision?:number}
export interface PromotionPreview {source:PromotionSource;card:CharacterCard;messages:PromotionMemory[];existingId:string|null;persona:ChatSession['persona'];modelOverride:string|null}
type PromotedProfile=CompanionProfile&{promotion:CompanionPromotion}
const copy=<T>(v:T):T=>JSON.parse(canonical(v))
async function existingCompanion(vault:LocalVault,identityId:string){return (await vault.list<PromotedProfile>('companion')).find(row=>row.value.promotion?.source.identityId===identityId)||null}
export async function promotionPreview(vault:LocalVault,target:PromotionTarget):Promise<PromotionPreview>{
 const root=await vault.read<CharacterCard|ChatSession>(target.id)
 if(!root||root.kind!==target.kind||target.revision&&root.revision!==target.revision)throw new ContractError('COMPANION_SOURCE_CHANGED','原角色或聊天已更新，请重新打开升格面板')
 let member:{id:string;revision:number;value:CharacterCard},messages:PromotionMemory[]=[],persona:ChatSession['persona']={name:'我',description:''},modelOverride:string|null=null
 if(target.kind==='card')member={id:root.id,revision:root.revision,value:root.value as CharacterCard}
 else{const session=root as Document<ChatSession>;if(session.value.companionId||session.value.cards.length!==1)throw new ContractError('COMPANION_SINGLE_CHAT','请从角色的独立聊天开始');member=session.value.cards[0]!;persona=copy(session.value.persona);modelOverride=session.value.modelOverride
  // Complete current variants only. Hidden/off-variant/system/partial text never becomes a candidate.
  for(const row of await vault.readMany<ChatMessage>(session.value.messageIds))try{messages.push(selectPromotionMemory(row,session))}catch(error){if((error as ContractError).code!=='COMPANION_PROMOTION')throw error}
 }
 const source:PromotionSource={kind:target.kind,id:root.id,revision:root.revision,cardId:member.id,cardRevision:member.revision,identityId:member.value.identityId}
 return {source,card:copy(member.value),messages,existingId:(await existingCompanion(vault,source.identityId))?.id||null,persona,modelOverride}
}
/** No provider call. One encrypted CAS transaction creates an independent companion and chosen memories. */
export async function promoteCompanion(vault:LocalVault,preview:PromotionPreview,input:Pick<CompanionProfile,'name'|'personality'|'style'>,selectedIds:string[],signal:AbortSignal):Promise<string>{
 signal.throwIfAborted();if(new Set(selectedIds).size!==selectedIds.length||selectedIds.some(id=>!preview.messages.some(m=>m.messageId===id)))throw new ContractError('COMPANION_SELECTION','选定消息不属于这次预览')
 const prior=await existingCompanion(vault,preview.source.identityId);if(prior)return prior.id
 const latest=await promotionPreview(vault,{kind:preview.source.kind,id:preview.source.id,revision:preview.source.revision})
 if(canonical(latest.source)!==canonical(preview.source)||canonical(latest.card)!==canonical(preview.card)||canonical(latest.persona)!==canonical(preview.persona)||latest.modelOverride!==preview.modelOverride)throw new ContractError('COMPANION_SOURCE_CHANGED','原角色或聊天已更新，请重新预览')
 const memories=preview.messages.filter(m=>selectedIds.includes(m.messageId));for(const m of memories)if(!latest.messages.some(candidate=>canonical(candidate)===canonical(m)))throw new ContractError('COMPANION_MEMORY_CHANGED','选定消息已编辑、隐藏或切换候选，请重新预览')
 const promotion:CompanionPromotion={version:'companion-promotion-v1',source:copy(preview.source),memories:copy(memories)};validatePromotion(promotion)
 const hash=await sha256(preview.source.identityId),id='companion-promoted:'+hash,sessionId='companion-chat:'+hash,at=new Date().toISOString(),card=detachNovelCard(preview.card)
 card.identityId='companion-person:'+hash;card.name=input.name.trim();card.personality=input.personality;card.firstMessage='';card.data={...card.data,name:card.name,personality:card.personality,first_mes:''}
 if(!card.name)throw new ContractError('COMPANION_NAME','请填写伴侣名字')
 const sourceCard=await vault.read<CharacterCard>(preview.source.cardId,preview.source.cardRevision)
 const profile:PromotedProfile={...input,name:card.name,identityId:card.identityId,createdAt:at,sessionId,user:copy(preview.persona),modelOverride:preview.modelOverride,proactiveEnabled:false,promotion,...(sourceCard?.kind==='card'&&sourceCard.value.identityId===preview.source.identityId?{sourceCard:{id:sourceCard.id,revision:sourceCard.revision}}:{})}
 const session:ChatSession={title:card.name,cards:[{id:'companion-card:'+hash,revision:1,value:card}],messageIds:[],persona:copy(preview.persona),mode:'roleplay',authorNote:'',modelOverride:preview.modelOverride,archived:false,parent:null,createdAt:at,currentTaskId:null,companionId:id}
 const writes:WriteDocument[]=[{kind:'companion',id,expectedRevision:0,value:profile},{kind:'session',id:sessionId,expectedRevision:0,value:session},{kind:'relationship',id:'relationship:'+id,expectedRevision:0,value:{companionId:id,algorithm:'shared-events-v1',events:[],wallet:1000,anniversaries:[]}}]
 for(const m of memories){const value:CompanionMemory={companionId:id,text:m.text,status:'active',source:{kind:'promotion',text:m.text,sessionId:preview.source.id,messageId:m.messageId,messageRevision:m.messageRevision,variantId:m.variantId,at:m.at},createdAt:at};writes.push({kind:'memory',id:'promotion-memory:'+await sha256(canonical({id,messageId:m.messageId,variantId:m.variantId})),expectedRevision:0,value})}
 const conditions=[{id:preview.source.id,kind:preview.source.kind,revision:preview.source.revision},...memories.map(m=>({id:m.messageId,kind:'message' as const,revision:m.messageRevision}))],operation='promote-companion:'+hash,payloadHash=await sha256(canonical({documents:writes,preconditions:conditions}))
 try{await vault.commitBundle(operation,writes,signal,conditions)}catch(error){if(!vault.unlocked)throw error;const receipt=await vault.receipt(operation);if(receipt?.payloadHash===payloadHash)return id
  // Another window may have chosen its own first promotion. Never add memories to that decision implicitly.
  if(['REVISION_CONFLICT','IDEMPOTENCY_CONFLICT'].includes((error as ContractError).code)){const winner=await existingCompanion(vault,preview.source.identityId);if(winner)return winner.id}throw error
 }
 return id
}
