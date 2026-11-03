import {canonical,ContractError,sha256,type Document} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import type {CharacterCard} from './card-format'
import type {CardDraft} from './card-drafts'
export interface CardCollectionPatch {addTags:string[];removeTags:string[];group?:{mode:'add'|'remove'|'clear';name?:string};archived?:boolean}
interface LibraryMetadata {format:'reader-library-v1';groups:string[];archived:boolean;[field:string]:unknown}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const copy=<T>(v:T):T=>JSON.parse(canonical(v))
const label=(v:unknown):v is string=>typeof v==='string'&&!!v.trim()&&v.length<=120
function fail(message:string):never{throw new ContractError('CARD_COLLECTION',message+'；原角色与聊天保留')}
export function cardLibrary(card:CharacterCard):Pick<LibraryMetadata,'groups'|'archived'>{const extensions=card.data.extensions,value=record(extensions)&&record(extensions.readerLibrary)?extensions.readerLibrary:null;return value?.format==='reader-library-v1'?{groups:Array.isArray(value.groups)?value.groups.filter(label).slice(0,32):[],archived:value.archived===true}:{groups:[],archived:false}}
export function validateCollectionPatch(value:unknown):asserts value is CardCollectionPatch{
 if(!record(value)||!Array.isArray(value.addTags)||!Array.isArray(value.removeTags)||value.addTags.length>64||value.removeTags.length>64||[...value.addTags,...value.removeTags].some(tag=>!label(tag))||value.archived!==undefined&&typeof value.archived!=='boolean')fail('批量修改字段无效')
 if(value.group!==undefined&&(!record(value.group)||!['add','remove','clear'].includes(String(value.group.mode))||value.group.mode!=='clear'&&!label(value.group.name)))fail('分组名称无效')
 const added=value.addTags.map(s=>String(s).trim().normalize('NFC')),removed=value.removeTags.map(s=>String(s).trim().normalize('NFC'));if(added.some(tag=>removed.includes(tag)))fail('同一个标签不能同时添加和移除')
}
export function collectionCard(card:CharacterCard,patch:CardCollectionPatch):CharacterCard{
 validateCollectionPatch(patch);if(card.tags.some(tag=>!label(tag)))fail('原卡标签需要先修正')
 const next=copy(card),removed=new Set(patch.removeTags.map(t=>t.trim().normalize('NFC'))),tags=[...new Set([...card.tags.map(t=>t.trim().normalize('NFC')).filter(t=>!removed.has(t)),...patch.addTags.map(t=>t.trim().normalize('NFC'))])];if(tags.length>128)fail('标签超过128个，请先缩小本次修改')
 next.tags=tags;next.data.tags=[...tags]
 if(patch.group||patch.archived!==undefined){const extensions=record(next.data.extensions)?next.data.extensions:{},old=extensions.readerLibrary;if(old!==undefined&&(!record(old)||old.format!=='reader-library-v1'))fail('角色有另一种同名扩展，请先在完整字段中确认')
  const current=cardLibrary(card),group=patch.group,name=group?.name?.trim().normalize('NFC'),existing=[...new Set(current.groups.map(value=>value.trim().normalize('NFC')))],groups=group?.mode==='clear'?[]:group?.mode==='add'?[...new Set([...existing,name!])]:group?.mode==='remove'?existing.filter(value=>value!==name):existing;if(groups.length>32)fail('分组超过32个')
  next.data.extensions={...extensions,readerLibrary:{...(record(old)?old:{}),format:'reader-library-v1',groups,archived:patch.archived??current.archived}}
 }
 return next
}
async function commitCards(vault:LocalVault,operation:string,writes:WriteDocument[],signal:AbortSignal,conditions:Parameters<LocalVault['commitBundle']>[3]=[]){const hash=await sha256(canonical(conditions.length?{documents:writes,preconditions:conditions}:writes));try{await vault.commitBundle(operation,writes,signal,conditions)}catch(error){if(!vault.unlocked||(await vault.receipt(operation))?.payloadHash!==hash)throw error}}
async function verifySnapshot(vault:LocalVault,row:Document<CharacterCard>){if(row.kind!=='card'||!Number.isSafeInteger(row.revision)||row.revision<1)fail('选择的对象不是角色版本');const source=await vault.read<CharacterCard>(row.id,row.revision);if(!source||source.kind!=='card'||canonical(source.value)!==canonical(row.value))fail('选择的角色副本与库中版本不一致')}
/** An exact user-selected set changes atomically. Names, identities, containers and unknown fields remain intact. */
export async function updateCardCollection(vault:LocalVault,rows:Document<CharacterCard>[],patch:CardCollectionPatch,intentId:string,signal:AbortSignal){
 signal.throwIfAborted();validateCollectionPatch(patch);if(!rows.length||rows.length>64||new Set(rows.map(row=>row.id)).size!==rows.length||!intentId||intentId.length>160)fail('本次请选择1–64张不同的角色卡')
 const writes:WriteDocument[]=[];for(const row of rows){await verifySnapshot(vault,row);const value=collectionCard(row.value,patch);if(canonical(value)!==canonical(row.value))writes.push({kind:'card',id:row.id,expectedRevision:row.revision,value})}
 if(writes.length)await commitCards(vault,'card-collection:'+intentId,writes,signal,rows.map(row=>({id:row.id,kind:'card',revision:row.revision})))
 const current=await vault.readMany<CharacterCard>(rows.map(row=>row.id));signal.throwIfAborted();if(!writes.length&&current.some((row,index)=>row.kind!=='card'||row.revision!==rows[index]!.revision))throw new ContractError('REVISION_CONFLICT','所选角色已在其他窗口修改，请比较后再整理');return current
}
export async function listCardVersions(vault:LocalVault,id:string,before?:number){const head=await vault.read<CharacterCard>(id);if(!head||head.kind!=='card')fail('这张角色卡不存在');const end=before===undefined?head.revision:Math.min(head.revision,before-1);if(!Number.isSafeInteger(end)||end<0)fail('角色修订范围无效');const rows:Document<CharacterCard>[]=[];for(let revision=end;revision>=Math.max(1,end-19);revision--){const row=await vault.read<CharacterCard>(id,revision);if(!row||row.kind!=='card')fail('角色修订版本缺失');rows.push(row)}return {head,rows,hasMore:end>20}}
/** Restoring adds a new template version. Existing session snapshots and the old versions never change. */
export async function restoreCardVersion(vault:LocalVault,head:Document<CharacterCard>,targetRevision:number,intentId:string,signal:AbortSignal){
 signal.throwIfAborted();await verifySnapshot(vault,head);if(!Number.isSafeInteger(targetRevision)||targetRevision<1||targetRevision>head.revision||!intentId||intentId.length>160)fail('要恢复的版本无效');const target=await vault.read<CharacterCard>(head.id,targetRevision);if(!target||target.kind!=='card'||target.value.identityId!==head.value.identityId)fail('旧版本不属于这个角色身份')
 if(canonical(target.value)===canonical(head.value)){const current=await vault.read<CharacterCard>(head.id);if(!current||current.revision!==head.revision)throw new ContractError('CARD_REVISION','其他窗口已更新角色，请比较后再恢复');return current}
 await commitCards(vault,'card-restore:'+intentId,[{kind:'card',id:head.id,expectedRevision:head.revision,value:copy(target.value)}],signal);return (await vault.read<CharacterCard>(head.id))!
}
/** The template and completion of its encrypted editing buffer share the same receipt. */
export async function saveCardTemplate(vault:LocalVault,id:string,revision:number,card:CharacterCard,intentId:string,signal:AbortSignal,draft?:Document<CardDraft>){const writes:WriteDocument[]=[{kind:'card',id,expectedRevision:revision,value:copy(card)}];if(draft){if(draft.value.cardId!==id||draft.value.identityId!==card.identityId||draft.value.cardRevision!==revision||draft.value.retired)fail('草稿不属于本次角色编辑');writes.push({kind:'settings',id:draft.id,expectedRevision:draft.revision,value:{...draft.value,retired:true}})}await commitCards(vault,'card-save:'+intentId,writes,signal);return (await vault.read<CharacterCard>(id))!}
