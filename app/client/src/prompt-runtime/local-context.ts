import {ContractError,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {selectedText,type ChatMessage,type ChatSession} from '../tavern/chat-contract'
import {importLorebook,triggerLorebooks,type LocalLorebook,type LoreResult} from './lorebook'
import {expandMacros,type MacroContext} from './macros'
import {estimateInput} from '../task-runtime/local-text'
import {chatPrompt} from './chat'
import {applyRegex,type RegexTrace} from './regex'
import {resolveRegex} from './regex-context'
import {resolvePreset} from './preset'
import {freezePlugins,runPluginHook} from '../plugins/runtime'
export async function localLoreContext(vault:LocalVault,sessionId:string,session:ChatSession,messages:Document<ChatMessage>[],speakerId:string,contextLimit:number,outputLimit:number,privateContext:{id:string;text:string}[],signal:AbortSignal){
  signal.throwIfAborted();const member=session.cards.find(row=>row.value.identityId===speakerId);if(!member)throw new ContractError('SPEAKER_INVALID','此角色不在会话中')
  const injections=await vault.read<{sessionId:string;entries:Record<string,string>}>('injections:'+sessionId);if(injections){if(injections.kind!=='settings'||injections.value.sessionId!==sessionId)throw new ContractError('SCRIPT_SCOPE','注入内容不属于此会话');privateContext=[...privateContext,...Object.entries(injections.value.entries).map(([id,text])=>({id:'injection:'+id+':'+injections.revision,text}))]}
  const preset=await resolvePreset(vault,session,speakerId)
  const defaults=await vault.read<{lorebookIds:string[]}>('settings:lorebooks'),ids=[...new Set([...(defaults?.value.lorebookIds||[]),...(session.lorebookIds||[])])],books=await vault.readMany<LocalLorebook>(ids)
  if(books.length!==ids.length||books.some(row=>row.kind!=='lorebook'))throw new ContractError('LOREBOOK_REFERENCE','绑定的世界书缺失，未发送请求')
  const embedded=member.value.data.character_book;if(embedded){const value=importLorebook(JSON.stringify(embedded));books.push({id:'embedded:'+member.id+':'+member.revision,revision:member.revision,kind:'lorebook',value,updatedAt:new Date().toISOString()})}
  const visible=messages.filter(row=>!row.value.hidden),last=(role?:string)=>{const row=[...visible].reverse().find(row=>!role||row.value.role===role);return row?selectedText(row.value):''},now=new Date(),seed=crypto.randomUUID()
  const local=await vault.read<{values:Record<string,string|number>}>('variables:session:'+sessionId),global=await vault.read<{values:Record<string,string|number>}>('settings:global-variables')
  const macros:MacroContext={char:typeof member.value.data.nickname==='string'&&member.value.data.nickname?member.value.data.nickname:member.value.name,user:session.persona.name,persona:session.persona.description,description:member.value.description,personality:member.value.personality,scenario:member.value.scenario,mesExamples:member.value.exampleMessages,input:last('user'),lastMessage:last(),lastUserMessage:last('user'),lastCharMessage:last('assistant'),date:now.toISOString().slice(0,10),time:now.toISOString().slice(11,19),seed,variables:local?.value.values||{},globalVariables:global?.value.values||{}}
  const regex=await resolveRegex(vault,session,speakerId),history=new Map<string,string>(),regexTrace:RegexTrace[]=[],regexSignal=AbortSignal.any([signal,AbortSignal.timeout(15000)])
  if(regex.rows.filter(row=>row.value.rule.enabled&&row.value.rule.prompt).length*visible.length>512)throw new ContractError('REGEX_SCAN_LIMIT','本次历史正则超过 512 次，请缩小规则深度或另开会话；原对话保留')
  for(const [index,row] of visible.entries()){const transformed=await applyRegex(regex.rows,expandMacros(selectedText(row.value),macros).text,{channel:'prompt',placement:row.value.role==='user'?1:row.value.role==='assistant'?2:3,depth:visible.length-1-index,speakerId:row.value.speakerId||speakerId,audienceId:speakerId,edited:!!row.value.variants.find(v=>v.id===row.value.selectedVariantId)?.editedFrom,macros},regexSignal);history.set(row.id,transformed.text);regexTrace.push(...transformed.trace.map(trace=>({...trace,messageId:row.id,variantId:row.value.selectedVariantId})))}
  const emptyLore:LoreResult={before:[],after:[],tail:[],trace:[],activations:[],seed,books:[]},mandatory=chatPrompt(session,[],speakerId,contextLimit,outputLimit,privateContext,{lore:emptyLore,macros,preset:preset.preset}),recentCost=estimateInput([{role:visible.at(-1)?.value.role||'user',content:visible.at(-1)?history.get(visible.at(-1)!.id)!:''}]),budget=Math.max(0,contextLimit-outputLimit-mandatory.estimatedInput-recentCost-256),state=session.loreState||{turn:0,activations:[]}
  const scanned=messages.map(row=>history.has(row.id)?{...row,value:{...row.value,variants:row.value.variants.map(variant=>variant.id===row.value.selectedVariantId?{...variant,text:history.get(row.id)!}:variant)}}:row)
  const lore=await triggerLorebooks(books,scanned,speakerId,budget,state.turn+1,state.activations,macros,signal)
  for(const region of [lore.before,lore.after,lore.tail])for(const row of region){const transformed=await applyRegex(regex.rows,row.text,{channel:'prompt',placement:5,depth:0,speakerId,edited:false,macros},regexSignal);row.text=transformed.text;regexTrace.push(...transformed.trace.map(trace=>({...trace,loreId:row.id})))}
  signal.throwIfAborted();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，上下文未继续')
  const prompt=chatPrompt(session,messages,speakerId,contextLimit,outputLimit,privateContext,{lore,macros,history,preset:preset.preset}),previous=new Map(state.activations.map(row=>[row.bookId+'\0'+row.entryId,row]))
  for(const activation of lore.activations)previous.set(activation.bookId+'\0'+activation.entryId,activation)
  const plugins=await freezePlugins(vault,sessionId,session,messages,speakerId,signal),run=await runPluginHook(vault,plugins.snapshot,'beforePrompt','',last('user'),member.value.name,signal)
  for(const result of run.outputs){const text=result.output.context;if(!text)continue;const next=[prompt.messages[0]!,{role:'system' as const,content:text},...prompt.messages.slice(1)],included=estimateInput(next)+outputLimit<=contextLimit;prompt.sources.push({id:result.pluginId,kind:'plugin',included,reason:included?'获准插件引用':'窗口不足，主对话保留',characters:text.length});if(included){prompt.messages=next;prompt.estimatedInput=estimateInput(next)}}
  signal.throwIfAborted()
  return {prompt,state:{turn:state.turn+1,activations:[...previous.values()].slice(-5000)},trace:{version:'lore-context-v1',seed,preset:preset.snapshot,books:lore.books,sources:prompt.sources,activations:lore.activations,regex:regexTrace,date:macros.date,time:macros.time,...(plugins.snapshot.plugins.length?{plugins:{snapshot:plugins.snapshot,trace:run.trace}}:{})},preconditions:[...books.filter(row=>!row.id.startsWith('embedded:')).map(row=>({id:row.id,revision:row.revision,kind:'lorebook' as const})),...regex.preconditions,...preset.preconditions,...plugins.preconditions,{id:'settings:lorebooks',revision:defaults?.revision||0,kind:'settings' as const},{id:'injections:'+sessionId,revision:injections?.revision||0,kind:'settings' as const}]}
}
