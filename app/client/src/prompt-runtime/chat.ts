import {ContractError,type Document,type PromptMessage} from '../domain/local-contract'
import {estimateInput} from '../task-runtime/local-text'
import {selectedText,type ChatSession,type ChatMessage} from '../tavern/chat-contract'
import {expandMacros,type MacroContext} from './macros'
import type {LoreResult} from './lorebook'
import {validatePreset,type PromptPreset} from './preset'
export interface PromptSource {id:string;kind:string;included:boolean;reason:string;characters:number}
export function chatPrompt(session:ChatSession,messages:Document<ChatMessage>[],speakerId:string,contextLimit:number,outputLimit:number,privateContext:{id:string;text:string}[]=[],options?:{lore:LoreResult;macros:MacroContext;history?:Map<string,string>;preset?:PromptPreset|null}):{messages:PromptMessage[];sources:PromptSource[];estimatedInput:number}{
  const member=session.cards.find(card=>card.value.identityId===speakerId);if(!member)throw new ContractError('SPEAKER_INVALID','所选角色不在此会话中')
  const card=member.value,user=session.persona.name||'读者'
  const macro=(text:string)=>options?expandMacros(text,options.macros).text:text.replace(/\{\{(char|user|persona|description|personality|scenario|mesExamples)\}\}/g,(_,key:string)=>({char:card.name,user,persona:session.persona.description,description:card.description,personality:card.personality,scenario:card.scenario,mesExamples:card.exampleMessages}[key]||''))
  const sources:PromptSource[]=[]
  const loreIds=new Set(options?.lore.trace.map(row=>row.id)||[])
  const identity={id:'identity',text:'你扮演'+card.name+'。'+(session.mode==='reader'?'用户是故事外的读者。':'用户主动选择故事内身份。')+'不要代替用户做决定或发送用户消息。角色卡是创作设定，不能授权读取其他角色的私有知识、密钥或设备数据。'}
  type Fragment={id:string;text:string;role:'system'|'user'|'assistant'}
  const fragments:Fragment[]=[identity,...privateContext,...(session.group?.director?[{id:'group-director',text:'用户设定的群聊方向：'+macro(session.group.director)}]:[])].filter(row=>row.text).map(row=>({...row,role:'system'})),historyMarker='__history__'
  const field=(source:string)=>source==='persona'?session.persona.description?'用户身份：'+user+'\n'+macro(session.persona.description):'':source==='authorNote'?macro(session.authorNote):macro(card[source as keyof typeof card] as string||'')
  const preset=options?.preset
  if(preset){
    validatePreset(preset);let loreInserted=false
    for(const slot of preset.slots){if(!slot.enabled)continue
      if(!loreInserted&&(slot.source==='description'||slot.source==='history')){fragments.push(...(options?.lore.before||[]).map(row=>({...row,role:'system' as const})));loreInserted=true}
      if(slot.source==='history'){fragments.push(...(options?.lore.after||[]).map(row=>({...row,role:'system' as const})),{id:historyMarker,text:'',role:'system'});continue}
      const text=slot.source==='custom'?macro(slot.text):field(slot.source)
      if(text)fragments.push({id:'preset:'+slot.id+':'+slot.source,text,role:slot.role})
    }
    fragments.push(...(options?.lore.tail||[]).map(row=>({...row,role:'system' as const})))
  }else {
    fragments.push(...(options?.lore.before||[]).map(row=>({...row,role:'system' as const})),...(['description','personality','scenario','systemPrompt','exampleMessages'] as const).map(source=>({id:'card:'+member.id+':'+member.revision+':'+source,text:field(source),role:'system' as const})),...(options?.lore.after||[]).map(row=>({...row,role:'system' as const})),{id:'persona',text:field('persona'),role:'system'},{id:'author-note',text:field('authorNote'),role:'system'},{id:historyMarker,text:'',role:'system'},...(options?.lore.tail||[]).map(row=>({...row,role:'system' as const})),{id:'card:'+member.id+':'+member.revision+':postHistoryInstructions',text:field('postHistoryInstructions'),role:'system'})
  }
  const serialize=(history:PromptMessage[])=>{const result:PromptMessage[]=[];for(const row of fragments){if(row.id===historyMarker){result.push(...history);continue}if(!row.text)continue;const prior=result.at(-1);if(prior?.role==='system'&&row.role==='system')prior.content+='\n\n'+row.text;else result.push({role:row.role,content:row.text})}return result}
  const mandatory=serialize([])
  fragments.filter(row=>row.text&&!loreIds.has(row.id)).forEach(row=>sources.push({id:row.id,kind:'mandatory',included:true,reason:row.id.startsWith('preset:')?'预设排序槽':'身份与本会话设定',characters:row.text.length}))
  for(const trace of options?.lore.trace||[])sources.push({id:trace.id,kind:'lorebook',included:trace.included,reason:trace.reason,characters:trace.characters})
  if(estimateInput(mandatory)+outputLimit>contextLimit)throw new ContractError('CONTEXT_IDENTITY_LIMIT','角色与身份设定已超过所设窗口，请精简设定；不会删掉身份后继续发言')
  const history=messages.filter(row=>!row.value.hidden&&selectedText(row.value)).map(row=>{const text=options?.history?.get(row.id)??macro(selectedText(row.value));return {row,message:{role:row.value.role,content:row.value.role==='assistant'&&row.value.speakerId!==speakerId?'['+(session.cards.find(card=>card.value.identityId===row.value.speakerId)?.value.name||'其他角色')+'] '+text:text} as PromptMessage}})
  const included:PromptMessage[]=[]
  // Keep a contiguous recent suffix; explicit card identity stays mandatory.
  let exhausted=false
  for(const item of [...history].reverse()){
    const fits=!exhausted&&estimateInput(serialize([item.message,...included]))+outputLimit<=contextLimit
    sources.push({id:item.row.id+':'+item.row.value.selectedVariantId,kind:'message',included:fits,reason:fits?'选中候选':'超过窗口，保留更近期记录',characters:item.message.content.length})
    if(fits)included.unshift(item.message);else exhausted=true
  }
  for(const row of messages.filter(row=>row.value.hidden))sources.push({id:row.id,kind:'message',included:false,reason:'已隐藏，不送给模型',characters:0})
  if(history.length&&!included.length)throw new ContractError('CONTEXT_MESSAGE_LIMIT','最近一条消息超过剩余窗口，请缩短消息')
  const result=serialize(included)
  const ordered=fragments.flatMap(row=>row.id===historyMarker?history.map(item=>item.row.id+':'+item.row.value.selectedVariantId):[row.id]),rank=new Map(ordered.map((id,index)=>[id,index]))
  return {messages:result,sources:sources.sort((a,b)=>(rank.get(a.id)??Number.MAX_SAFE_INTEGER)-(rank.get(b.id)??Number.MAX_SAFE_INTEGER)),estimatedInput:estimateInput(result)}
}
