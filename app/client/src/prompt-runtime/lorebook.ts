import {canonical,ContractError,type Document} from '../domain/local-contract'
import {selectedText,type ChatMessage} from '../tavern/chat-contract'
import {safeRegexTest} from './safe-regex'
import {expandMacros,seededFraction,type MacroContext} from './macros'
export interface LoreEntry {id:string;keys:string[];secondaryKeys:string[];logic:'any'|'all'|'none'|'not_all';content:string;enabled:boolean;constant:boolean;caseSensitive:boolean;useRegex:boolean;probability:number;priority:number;order:number;position:'before_char'|'after_char'|'after_history';speakerIds:string[];cooldown:number;delay:number;source:Record<string,unknown>}
export interface LocalLorebook {name:string;enabled:boolean;scanDepth:number;tokenBudget:number;recursive:boolean;entries:LoreEntry[];source:Record<string,unknown>}
export interface LoreTrace {id:string;bookId:string;bookRevision:number;entryId:string;included:boolean;reason:string;characters:number;round:number;position:LoreEntry['position']}
export interface LoreResult {before:{id:string;text:string}[];after:{id:string;text:string}[];tail:{id:string;text:string}[];trace:LoreTrace[];activations:{bookId:string;entryId:string;turn:number}[];seed:string;books:{id:string;revision:number}[]}
const record=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value)
const stringArray=(value:unknown)=>Array.isArray(value)?value.filter((item):item is string=>typeof item==='string'):[]
export function validateLorebook(value:unknown):asserts value is LocalLorebook{
  const bad=()=>{throw new ContractError('LOREBOOK_INVALID','世界书字段无效；原数据保留')}
  if(!record(value)||typeof value.name!=='string'||!value.name.trim()||value.name.length>200||typeof value.enabled!=='boolean'||typeof value.recursive!=='boolean'||!Number.isSafeInteger(value.scanDepth)||(value.scanDepth as number)<1||(value.scanDepth as number)>1000||!Number.isSafeInteger(value.tokenBudget)||(value.tokenBudget as number)<0||(value.tokenBudget as number)>1000000||!record(value.source)||!Array.isArray(value.entries)||value.entries.length>2000)bad()
  const book=value as unknown as LocalLorebook,ids=new Set<string>();for(const row of book.entries){if(!record(row)||typeof row.id!=='string'||!row.id||row.id.length>160||ids.has(row.id)||typeof row.content!=='string'||row.content.length>200000||!['keys','secondaryKeys','speakerIds'].every(key=>Array.isArray(row[key])&&(row[key] as unknown[]).length<=32&&(row[key] as unknown[]).every(value=>typeof value==='string'&&value.length<=1000))||!['any','all','none','not_all'].includes(row.logic)||!['before_char','after_char','after_history'].includes(row.position)||!['enabled','constant','caseSensitive','useRegex'].every(key=>typeof row[key]==='boolean')||!Number.isFinite(row.probability)||row.probability<0||row.probability>100||!Number.isFinite(row.priority)||!Number.isFinite(row.order)||!Number.isSafeInteger(row.cooldown)||row.cooldown<0||row.cooldown>10000||!Number.isSafeInteger(row.delay)||row.delay<0||row.delay>10000||!record(row.source))bad();ids.add(row.id)}
}
export function importLorebook(json:string):LocalLorebook{
  if(new TextEncoder().encode(json).length>8*1024*1024)throw new ContractError('LOREBOOK_LIMIT','世界书超过 8 MiB')
  let source:unknown;try{source=JSON.parse(json)}catch{throw new ContractError('LOREBOOK_INVALID','世界书 JSON 无效')}
  if(!record(source))throw new ContractError('LOREBOOK_INVALID','世界书需要 JSON 对象')
  const data=source.spec==='lorebook_v3'?source.data:source;if(!record(data))throw new ContractError('LOREBOOK_INVALID','世界书缺少 data')
  const rawEntries=Array.isArray(data.entries)?data.entries:record(data.entries)?Object.values(data.entries):[]
  const number=(value:unknown,fallback:number)=>typeof value==='number'&&Number.isFinite(value)?value:fallback
  const entries:LoreEntry[]=rawEntries.map((value,index)=>{if(!record(value))throw new ContractError('LOREBOOK_INVALID','世界书条目 '+(index+1)+' 无效');const extension=record(value.extensions)?value.extensions:{},position=value.position==='after_history'||extension.position==='after_history'?'after_history':value.position==='before_char'?'before_char':'after_char',speakerIds=stringArray(extension.speakerIds)
    return {id:String(value.id??index),keys:stringArray(value.keys??value.key),secondaryKeys:stringArray(value.secondary_keys??value.keysecondary),logic:extension.secondaryLogic==='all'?'all':extension.secondaryLogic==='not_all'?'not_all':extension.secondaryLogic==='none'?'none':'any',content:typeof value.content==='string'?value.content:'',enabled:value.enabled!==false&&value.disable!==true,constant:value.constant===true,caseSensitive:value.case_sensitive===true||value.caseSensitive===true,useRegex:value.use_regex===true||value.useRegex===true,probability:number(value.probability??extension.probability,100),priority:number(value.priority,0),order:number(value.insertion_order??value.order,index),position,speakerIds,cooldown:number(value.cooldown??extension.cooldown,0),delay:number(value.delay??extension.delay,0),source:JSON.parse(canonical({...value,extensions:extension,selective:value.selective===true}))}
  })
  const book:LocalLorebook={name:typeof data.name==='string'&&data.name.trim()?data.name:'世界书',enabled:data.enabled!==false,scanDepth:number(data.scan_depth,20),tokenBudget:number(data.token_budget,4096),recursive:data.recursive_scanning===true,entries,source:JSON.parse(canonical(source))};validateLorebook(book);return book
}
export function exportLorebook(book:LocalLorebook){
  validateLorebook(book);const original=book.source.spec==='lorebook_v3'&&record(book.source.data)?book.source.data:book.source
  const data={...original,name:book.name,enabled:book.enabled,scan_depth:book.scanDepth,token_budget:book.tokenBudget,recursive_scanning:book.recursive,entries:book.entries.map(row=>({...row.source,id:row.source.id??row.id,keys:row.keys,secondary_keys:row.secondaryKeys,content:row.content,enabled:row.enabled,disable:!row.enabled,constant:row.constant,case_sensitive:row.caseSensitive,caseSensitive:row.caseSensitive,use_regex:row.useRegex,useRegex:row.useRegex,probability:row.probability,cooldown:row.cooldown,delay:row.delay,priority:row.priority,insertion_order:row.order,position:row.position,selective:row.secondaryKeys.length>0,extensions:{...(record(row.source.extensions)?row.source.extensions:{}),speakerIds:row.speakerIds,secondaryLogic:row.logic,probability:row.probability,cooldown:row.cooldown,delay:row.delay}}))}
  return JSON.stringify(book.source.spec==='lorebook_v3'?{...book.source,data}:data,null,2)
}
/** Privacy filtering precedes scanning, randomness, regex and recursive expansion. */
export async function triggerLorebooks(books:Document<LocalLorebook>[],messages:Document<ChatMessage>[],speakerId:string,budget:number,turn:number,previous:{bookId:string;entryId:string;turn:number}[],context:MacroContext,signal:AbortSignal):Promise<LoreResult>{
  signal.throwIfAborted();if(books.length>16||books.reduce((sum,row)=>sum+row.value.entries.length,0)>5000)throw new ContractError('LOREBOOK_LIMIT','本次世界书超过条目扫描限额；请减少绑定')
  const result:LoreResult={before:[],after:[],tail:[],trace:[],activations:[],seed:context.seed,books:books.map(row=>({id:row.id,revision:row.revision}))},selected=new Set<string>(),base=new Map<string,string>(),bytesByBook=new Map<string,number>(),recursiveText:string[]=[],regexErrors=new Map<LoreEntry,string>();let used=0,regexCalls=0
  const candidates=books.flatMap(book=>{validateLorebook(book.value);base.set(book.id,messages.filter(row=>!row.value.hidden).slice(-book.value.scanDepth).map(row=>selectedText(row.value)).join('\n').slice(-200000));return book.value.entries.map(entry=>({book,entry,key:book.id+':'+entry.id}))}).sort((a,b)=>b.entry.priority-a.entry.priority||a.entry.order-b.entry.order||a.key.localeCompare(b.key))
  const traces=new Map<string,LoreTrace>();const note=(item:typeof candidates[number],reason:string,included=false,round=0)=>{const trace={id:item.key,bookId:item.book.id,bookRevision:item.book.revision,entryId:item.entry.id,included,reason,characters:included?item.entry.content.length:0,round,position:item.entry.position};traces.set(item.key,trace);return trace}
  const matches=async(keys:string[],text:string,entry:LoreEntry)=>{const values:boolean[]=[];for(const rawKey of keys){const key=expandMacros(rawKey,context).text;signal.throwIfAborted();if(entry.useRegex){if(regexCalls++>=64){regexErrors.set(entry,'正则扫描次数达到上限');values.push(false);continue}const match=await safeRegexTest(key,text,entry.caseSensitive,signal);if(match.error)regexErrors.set(entry,'正则未执行：'+match.error);values.push(match.matched)}else values.push(!!key&&(entry.caseSensitive?text.includes(key):text.toLowerCase().includes(key.toLowerCase())))}return values}
  for(let round=0;round<4;round++){
    let added=0
    for(const item of candidates){signal.throwIfAborted();if(selected.has(item.key))continue;const {book,entry}=item
      if(!book.value.enabled||!entry.enabled){note(item,'已停用');continue}
      if(entry.speakerIds.length&&!entry.speakerIds.includes(speakerId)){note(item,'不属于当前角色知识范围');continue}
      if(round&&!book.value.recursive)continue
      const declarations=entry.content.split('\n'),activate=declarations.includes('@@activate'),disabled=declarations.includes('@@dont_activate'),content=declarations.filter(line=>line!=='@@activate'&&line!=='@@dont_activate').join('\n').trim()
      if(!activate&&turn<entry.delay){note(item,'延迟尚未到达');continue}
      const last=previous.filter(row=>row.bookId===book.id&&row.entryId===entry.id).reduce((max,row)=>Math.max(max,row.turn),-1)
      if(!activate&&last>=0&&turn-last<=entry.cooldown){note(item,'仍在冷却');continue}
      if(disabled&&!activate){note(item,'V3 禁止激活');continue}
      if(!content){note(item,'空内容');continue}
      const text=base.get(book.id)!+(round?'\n'+recursiveText.join('\n'):'')
      const primary=entry.constant||activate||(await matches(entry.keys,text,entry)).some(Boolean)
      const secondary=entry.source.selective!==false&&entry.secondaryKeys.length&&!entry.constant&&!activate?await matches(entry.secondaryKeys,text,entry):[]
      const secondaryMatch=entry.logic==='all'?secondary.every(Boolean):entry.logic==='not_all'?secondary.length?!secondary.every(Boolean):true:entry.logic==='none'?!secondary.some(Boolean):secondary.length?secondary.some(Boolean):true
      if(!primary||!secondaryMatch){note(item,regexErrors.get(entry)||'关键词或二级条件未匹配',false,round);continue}
      if(!activate&&seededFraction(context.seed,item.key)*100>=entry.probability){note(item,'本次概率未触发',false,round);continue}
      const expanded=expandMacros(content,context),cost=new TextEncoder().encode(expanded.text).length,bookUsed=bytesByBook.get(book.id)||0
      if(used+cost>budget||bookUsed+cost>book.value.tokenBudget){note(item,'超过世界书保守预算',false,round);continue}
      recursiveText.push(expanded.text+'\n'+[...content.matchAll(/\{\{hidden_key:([^{}]*)\}\}/gi)].map(match=>match[1]).join('\n'));used+=cost;bytesByBook.set(book.id,bookUsed+cost);selected.add(item.key);added++;note(item,'触发并纳入提示',true,round).characters=expanded.text.length;result.activations.push({bookId:book.id,entryId:entry.id,turn});(entry.position==='before_char'?result.before:entry.position==='after_history'?result.tail:result.after).push({id:item.key,text:expanded.text})
    }
    if(!added)break
  }
  for(const region of [result.before,result.after,result.tail])region.sort((a,b)=>candidates.find(item=>item.key===a.id)!.entry.order-candidates.find(item=>item.key===b.id)!.entry.order)
  result.trace=[...traces.values()];return result
}
