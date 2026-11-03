import {canonical,sha256,ContractError,type Document,type DomainKind} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {selectedText,type ChatSession,type ChatMessage} from '../tavern/chat-contract'
import type {CompanionMemory} from '../companion/contract'
import type {LocalLorebook} from '../prompt-runtime/lorebook'
import {builtinPlugins,executeBuiltin} from './catalog'
import {runPluginSandbox} from './sandbox'
import {pluginBytes,validateInstalledPlugin,validatePluginOverrides,validatePluginOutput,type InstalledPlugin,type PluginOverrides,type PluginSnapshot,type PluginReference,type PluginInput,type PluginHook,type PluginTrace,type PluginOutput} from './contract'
const copy=<T>(value:T):T=>JSON.parse(canonical(value)),now=()=>new Date().toISOString()
export async function commitPluginDocuments(vault:LocalVault,documents:WriteDocument[],signal?:AbortSignal){const operation=crypto.randomUUID(),hash=await sha256(canonical(documents));try{return await vault.commitBundle(operation,documents,signal)}catch(error){if(!vault.unlocked)throw error;const receipt=await vault.receipt(operation);if(receipt?.payloadHash!==hash)throw error;return [receipt]}}
export async function installPlugin(vault:LocalVault,value:InstalledPlugin,signal:AbortSignal){
 validateInstalledPlugin(value,'plugin:'+value.manifest.id);signal.throwIfAborted()
 const id='plugin:'+value.manifest.id,prior=await vault.read<InstalledPlugin>(id)
 if(prior&&!prior.value.deleted&&canonical(prior.value.manifest)===canonical(value.manifest)&&prior.value.source===value.source)return prior
 if(prior&&!prior.value.deleted&&prior.value.manifest.version===value.manifest.version)throw new ContractError('PLUGIN_VERSION','更新内容需使用新版本；旧版保留')
 const next={...copy(value),enabled:false,grants:[],deleted:false,createdAt:prior?.value.createdAt||now()}
 await commitPluginDocuments(vault,[{kind:'settings',id,expectedRevision:prior?.revision||0,value:next}],signal)
 return (await vault.read<InstalledPlugin>(id))!
}
export function parsePluginPackage(source:string):InstalledPlugin{
 if(new TextEncoder().encode(source).length>256*1024)throw new ContractError('PLUGIN_PACKAGE_LIMIT','侧载文件超过 256 KiB')
 const value=JSON.parse(source);if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!['manifest','source'].includes(key)))throw new ContractError('PLUGIN_PACKAGE','侧载文件需要 manifest 和 source')
 const plugin:InstalledPlugin={assetType:'chat-plugin',manifest:value.manifest,source:value.source,builtin:null,enabled:false,grants:[],deleted:false,createdAt:now()};validateInstalledPlugin(plugin,'plugin:'+plugin.manifest.id);if(builtinPlugins.some(row=>row.manifest.id===plugin.manifest.id))throw new ContractError('PLUGIN_BUILTIN','侧载不能替换内置插件')
 return plugin
}
export async function restorePluginVersion(vault:LocalVault,id:string,revision:number,signal:AbortSignal){const [head,old]=await Promise.all([vault.read<InstalledPlugin>(id),vault.read<InstalledPlugin>(id,revision)]);if(!head||!old)throw new ContractError('PLUGIN_VERSION','插件版本不存在');const major=Math.max(Number(head.value.manifest.version.split('.')[0]),Number(old.value.manifest.version.split('.')[0]))+1,value={...copy(old.value),manifest:{...old.value.manifest,version:major+'.0.0'},createdAt:head.value.createdAt,enabled:false,grants:[],deleted:false};await commitPluginDocuments(vault,[{kind:'settings',id,expectedRevision:head.revision,value}],signal);return (await vault.read<InstalledPlugin>(id))!}
export async function freezePlugins(vault:LocalVault,targetId:string,chat:ChatSession,messages:Document<ChatMessage>[],speakerId:string,signal:AbortSignal):Promise<{snapshot:PluginSnapshot;preconditions:{id:string;kind:DomainKind;revision:number}[]}>{
 signal.throwIfAborted();const rows=(await vault.list<InstalledPlugin>('settings')).filter(row=>row.value.assetType==='chat-plugin'&&!row.value.deleted).sort((a,b)=>a.id.localeCompare(b.id)),override=await vault.read<PluginOverrides>('plugin-overrides:'+targetId)
 if(override){validatePluginOverrides(override.value,override.id);if(override.value.targetId!==targetId||override.value.targetKind!=='session')throw new ContractError('PLUGIN_SCOPE','插件覆盖不属于此会话')}
 const plugins=rows.flatMap(row=>{validateInstalledPlugin(row.value,row.id);const setting=override?.value.entries[row.id],enabled=setting?.mode==='off'?false:setting?.mode==='on'?setting.version===row.value.manifest.version:row.value.enabled;return enabled?[{id:row.id,revision:row.revision,value:copy(row.value),parameters:{...row.value.manifest.parameters,...row.value.parameters,...setting?.parameters},reason:setting?.mode==='on'?'on' as const:'global' as const}]:[]})
 const wanted=new Set(plugins.flatMap(row=>row.value.grants)),references:PluginReference[]=[],conditions:{id:string;kind:DomainKind;revision:number}[]=[{id:'plugin-overrides:'+targetId,kind:'settings',revision:override?.revision||0},...plugins.map(row=>({id:row.id,kind:'settings' as const,revision:row.revision}))]
 if(wanted.has('messages'))for(const row of messages.filter(row=>!row.value.hidden)){if(row.value.sessionId!==targetId)throw new ContractError('PLUGIN_SCOPE','消息不属于此会话');references.push({id:row.id,revision:row.revision,kind:'messages',title:row.value.role==='user'?'我的消息':chat.cards.find(c=>c.value.identityId===row.value.speakerId)?.value.name||'消息',text:selectedText(row.value)});conditions.push({id:row.id,revision:(await vault.read(row.id))?.revision||0,kind:'message'})}
 if(wanted.has('card')){const card=chat.cards.find(row=>row.value.identityId===speakerId);if(card)references.push({id:card.id,revision:card.revision,kind:'card',title:card.value.name,text:card.value.description+'\n'+card.value.personality+'\n'+card.value.scenario})}
 if(wanted.has('memory')&&chat.companionId){const memories=(await vault.list<CompanionMemory>('memory')).filter(row=>row.value.companionId===chat.companionId&&row.value.status==='active');for(const row of memories){references.push({id:row.id,revision:row.revision,kind:'memory',title:'已确认记忆',text:row.value.text});conditions.push({id:row.id,revision:row.revision,kind:'memory'})}}
 if(wanted.has('lorebook'))for(const row of await vault.readMany<LocalLorebook>(chat.lorebookIds||[])){if(!row.value.enabled)continue;for(const entry of row.value.entries.filter(entry=>entry.enabled&&(!entry.speakerIds.length||entry.speakerIds.includes(speakerId))))references.push({id:row.id,revision:row.revision,kind:'lorebook',title:row.value.name+' · '+entry.id,text:entry.content});conditions.push({id:row.id,revision:row.revision,kind:'lorebook'})}
 const snapshot:PluginSnapshot={version:'plugins-v1',targetId,targetRevision:(await vault.read(targetId))!.revision,targetKind:'session',speakerId,plugins,references,limits:{steps:8,calls:1,milliseconds:5000,bytes:64000},createdAt:now()}
 if(plugins.length>32||references.length>1000||pluginBytes(canonical(snapshot))>2*1024*1024)throw new ContractError('PLUGIN_SCOPE_LIMIT','插件范围超过限制，请缩小历史或资料')
 signal.throwIfAborted();return {snapshot,preconditions:[...new Map(conditions.map(row=>[row.id,row])).values()]}
}
export async function pluginAuthority(vault:LocalVault,snapshot:PluginSnapshot,pluginId:string,signal:AbortSignal){
 signal.throwIfAborted();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定')
 const frozen=snapshot.plugins.find(row=>row.id===pluginId),row=await vault.read<InstalledPlugin>(pluginId),override=await vault.read<PluginOverrides>('plugin-overrides:'+snapshot.targetId),target=await vault.read<ChatSession>(snapshot.targetId)
 if(!frozen||!row||row.value.deleted||!target||target.kind!=='session'||target.value.archived)throw new ContractError('PLUGIN_REVOKED','插件或会话已停用')
 const entry=override?.value.entries[pluginId],mode=entry?.mode||'inherit';if(mode==='on'&&(entry?.version!==row.value.manifest.version||entry.version!==frozen.value.manifest.version)||mode==='off'||mode==='inherit'&&!row.value.enabled||frozen.value.grants.some(scope=>!row.value.grants.includes(scope)))throw new ContractError('PLUGIN_REVOKED','插件能力已撤销')
 const live=await vault.readMany<ChatMessage>(snapshot.references.filter(ref=>ref.kind==='messages').map(ref=>ref.id));if(live.some(message=>message.value.hidden||message.value.sessionId!==snapshot.targetId))throw new ContractError('PLUGIN_HIDDEN','引用消息已隐藏')
 signal.throwIfAborted()
}
export async function runPluginHook(vault:LocalVault,snapshot:PluginSnapshot,hook:PluginHook,text:string,query:string,speaker:string,signal:AbortSignal,previous:PluginTrace[]=[]):Promise<{outputs:{pluginId:string;output:PluginOutput}[];trace:PluginTrace[]}>{
 const outputs:{pluginId:string;output:PluginOutput}[]=[],trace:PluginTrace[]=[],started=performance.now(),priorMilliseconds=previous.reduce((n,row)=>n+row.milliseconds,0);let currentText=text,used=previous.filter(row=>row.state!=='skipped').reduce((n,row)=>n+(snapshot.plugins.find(plugin=>plugin.id===row.pluginId)?.value.manifest.budget.steps||1),0),bytes=previous.reduce((n,row)=>n+(row.output?pluginBytes(canonical(row.output)):0),0)
 for(const frozen of snapshot.plugins){if(!frozen.value.manifest.hooks.includes(hook))continue;const begin=performance.now(),result:PluginTrace={pluginId:frozen.id,revision:frozen.revision,hook,state:'skipped',reason:'达到本次插件步骤上限',milliseconds:0,modelCalls:0,output:null}
  if(used+frozen.value.manifest.budget.steps>snapshot.limits.steps||priorMilliseconds+performance.now()-started>=snapshot.limits.milliseconds){trace.push(result);continue}used+=frozen.value.manifest.budget.steps
  try{
   const remaining=Math.max(1,Math.ceil(snapshot.limits.milliseconds-priorMilliseconds-(performance.now()-started))),hookSignal=AbortSignal.any([signal,AbortSignal.timeout(remaining)])
   await pluginAuthority(vault,snapshot,frozen.id,hookSignal)
   const input:PluginInput={hook,text:currentText,query,speaker,references:snapshot.references.filter(ref=>frozen.value.grants.includes(ref.kind)).map(copy)}
   const output=frozen.value.builtin?executeBuiltin(frozen.value.builtin,input,frozen.parameters):await runPluginSandbox(frozen.value.source,input,frozen.parameters,Math.max(1,Math.min(frozen.value.manifest.budget.milliseconds,snapshot.limits.milliseconds-priorMilliseconds-(performance.now()-started))),frozen.value.manifest.budget.bytes,hookSignal)
   validatePluginOutput(output,input,frozen.value);await pluginAuthority(vault,snapshot,frozen.id,hookSignal);hookSignal.throwIfAborted();if(priorMilliseconds+performance.now()-started>snapshot.limits.milliseconds)throw new ContractError('PLUGIN_CHAIN_TIMEOUT','本次插件总时限已用完，主回复保留')
   bytes+=pluginBytes(canonical(output));if(bytes>snapshot.limits.bytes)throw new ContractError('PLUGIN_OUTPUT_LIMIT','本次插件结果超过上限')
   result.state='completed';result.reason='';result.output=copy(output);outputs.push({pluginId:frozen.id,output});if(hook==='beforeRender'&&output.displayText!==undefined)currentText=output.displayText
  }catch(error){result.state='failed';result.reason=error instanceof ContractError?error.message:'插件未完成，主回复保留'}
  result.milliseconds=Math.ceil(performance.now()-begin);trace.push(result);if(signal.aborted)break
 }
 return {outputs,trace}
}
