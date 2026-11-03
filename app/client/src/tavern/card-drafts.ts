import {canonical,ContractError,sha256,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {emptyCard,type CharacterCard} from './card-format'
export const cardFieldNames=['name','description','personality','scenario','firstMessage','exampleMessages','creatorNotes','systemPrompt','postHistoryInstructions','tags','alternateGreetings'] as const
export type CardFields=Pick<CharacterCard,typeof cardFieldNames[number]>
export interface CardDraft {assetType:'card-editor-draft';cardId:string;cardRevision:number;identityId:string;fields:CardFields;dataText:string;createdAt:string;conflict:boolean;retired:boolean;requiresFieldReview:boolean}
const copy=<T>(v:T):T=>JSON.parse(canonical(v))
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const id=(v:unknown)=>typeof v==='string'&&!!v&&v.length<=160
function fail(message:string):never{throw new ContractError('CARD_DRAFT',message+'；已保存的角色保留')}
export function validateCardDraft(value:unknown):asserts value is CardDraft{
 if(!record(value)||value.assetType!=='card-editor-draft'||!id(value.cardId)||!Number.isSafeInteger(value.cardRevision)||Number(value.cardRevision)<0||!id(value.identityId)||!record(value.fields)||typeof value.dataText!=='string'||new TextEncoder().encode(value.dataText).byteLength>8*1024*1024||typeof value.createdAt!=='string'||value.createdAt.length>50||typeof value.conflict!=='boolean'||typeof value.retired!=='boolean'||typeof value.requiresFieldReview!=='boolean')fail('角色草稿范围或正文无效')
 for(const field of cardFieldNames){const v=value.fields[field];if(field==='tags'||field==='alternateGreetings'){if(!Array.isArray(v)||v.length>1000||v.some(item=>typeof item!=='string'||item.length>1_000_000))fail('角色草稿列表无效')}else if(typeof v!=='string'||v.length>(field==='name'?200:1_000_000))fail('角色草稿字段无效')}
 if(new TextEncoder().encode(canonical(value)).byteLength>32*1024*1024)fail('角色草稿超过32MiB')
}
export function cardDraftValue(cardId:string,cardRevision:number,card:CharacterCard,dataText:string,prior?:CardDraft,conflict=false):CardDraft{const value:CardDraft={assetType:'card-editor-draft',cardId,cardRevision,identityId:card.identityId,fields:Object.fromEntries(cardFieldNames.map(field=>[field,copy(card[field])])) as unknown as CardFields,dataText,createdAt:prior?.createdAt||new Date().toISOString(),conflict:prior?.conflict??conflict,retired:false,requiresFieldReview:prior?.requiresFieldReview||false};validateCardDraft(value);return value}
export const cardDraftKey=(id:string)=>'card-draft:'+id
/** Draft JSON is an editing buffer: invalid JSON and an empty name are intentionally durable. */
export async function saveCardDraft(vault:LocalVault,id:string,revision:number,value:CardDraft,signal?:AbortSignal){validateCardDraft(value);const documents=[{kind:'settings' as const,id,expectedRevision:revision,value}],operation=crypto.randomUUID(),hash=await sha256(canonical(documents));try{await vault.commitBundle(operation,documents,signal)}catch(error){if(!vault.unlocked||(await vault.receipt(operation))?.payloadHash!==hash)throw error}return (await vault.read<CardDraft>(id))!}
export async function resumeCardDraft(vault:LocalVault,row:Document<CardDraft>){validateCardDraft(row.value);if(row.kind!=='settings'||row.value.retired)fail('这份角色草稿已结束');const draft=row.value,source=draft.cardRevision?await vault.read<CharacterCard>(draft.cardId,draft.cardRevision):null;if(draft.cardRevision&&(!source||source.kind!=='card'||source.value.identityId!==draft.identityId))fail('角色草稿来源版本缺失');const card=source?copy(source.value):emptyCard();card.identityId=draft.identityId;Object.assign(card,copy(draft.fields));return {card,dataText:draft.dataText,cardRevision:draft.cardRevision,requiresFieldReview:draft.requiresFieldReview}}
