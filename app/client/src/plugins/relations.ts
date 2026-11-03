import {canonical,ContractError,type Document,type DomainKind} from '../domain/local-contract'
import {selectedText,type ChatSession,type ChatMessage} from '../tavern/chat-contract'
import type {CompanionMemory} from '../companion/contract'
import type {LocalLorebook} from '../prompt-runtime/lorebook'
import type {InstalledPlugin,PluginSnapshot,PluginOverrides,PluginReport} from './contract'
type Lookup=(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined
export function snapshotReferences(snapshot:PluginSnapshot):{id:string;kind:DomainKind}[]{return [{id:snapshot.targetId,kind:'session'},...snapshot.plugins.map(plugin=>({id:plugin.id,kind:'settings' as const})),...snapshot.references.filter(ref=>ref.kind!=='card').map(ref=>({id:ref.id,kind:({messages:'message',memory:'memory',lorebook:'lorebook'} as const)[ref.kind as 'messages'|'memory'|'lorebook']}))]}
export function validateSnapshotRelations(snapshot:PluginSnapshot,lookup:Lookup){
 const fail=():never=>{throw new ContractError('PLUGIN_REFERENCE','插件冻结来源或权限不一致')},target=lookup(snapshot.targetId,snapshot.targetRevision)
 if(!target||target.kind!=='session'||target.revision!==snapshot.targetRevision)fail()
 const session=target!.value as ChatSession
 if(!session.cards.some(card=>card.value.identityId===snapshot.speakerId))fail()
 for(const plugin of snapshot.plugins){const original=lookup(plugin.id,plugin.revision);if(!original||original.kind!=='settings'||original.revision!==plugin.revision||canonical(original.value)!==canonical(plugin.value))fail()}
 for(const ref of snapshot.references){
  if(!snapshot.plugins.some(plugin=>plugin.value.grants.includes(ref.kind)))fail()
  if(ref.kind==='card'){const card=session.cards.find(card=>card.id===ref.id&&card.revision===ref.revision&&card.value.identityId===snapshot.speakerId);if(!card||ref.text!==card.value.description+'\n'+card.value.personality+'\n'+card.value.scenario)fail();continue}
  const original=lookup(ref.id,ref.revision);if(!original||original.revision!==ref.revision)fail()
  if(ref.kind==='messages'){const message=original!.value as ChatMessage;if(original!.kind!=='message'||message.hidden||message.sessionId!==snapshot.targetId||selectedText(message)!==ref.text)fail()}
  if(ref.kind==='memory'){const memory=original!.value as CompanionMemory;if(original!.kind!=='memory'||!session.companionId||memory.companionId!==session.companionId||memory.status!=='active'||memory.text!==ref.text)fail()}
  if(ref.kind==='lorebook'){const book=original!.value as LocalLorebook;if(original!.kind!=='lorebook'||!book.enabled||!session.lorebookIds?.includes(ref.id)||!book.entries.some(entry=>entry.enabled&&entry.content===ref.text&&(!entry.speakerIds.length||entry.speakerIds.includes(snapshot.speakerId))))fail()}
 }
}
export function validateOverrideRelations(value:PluginOverrides,lookup:Lookup){if(lookup(value.targetId)?.kind!==value.targetKind||Object.keys(value.entries).some(id=>(lookup(id)?.value as InstalledPlugin|undefined)?.assetType!=='chat-plugin'))throw new ContractError('PLUGIN_REFERENCE','插件覆盖对象不存在')}
export function validateReportRelations(value:PluginReport,lookup:Lookup){validateSnapshotRelations(value.snapshot,lookup);if(value.taskId&&(lookup(value.taskId)?.kind!=='task'||(lookup(value.taskId)?.value as {targetId?:string}).targetId!==value.targetId))throw new ContractError('PLUGIN_REFERENCE','插件诊断不属于此请求')}
