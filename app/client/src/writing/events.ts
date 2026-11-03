import {ContractError,sha256} from '../domain/local-contract'
export const eventSchema='prose-events-v1' as const
type Changes={
  character:{name:string;aliases:string[]};item:{item:string;fromHolderId:string|null;toHolderId:string|null;location:string|null}
  location:{from:string|null;to:string};time:{label:string;relation:'before'|'after'|'same_time'|'unknown';relativeTo:string|null}
  motivation:{goal:string;state:'adopted'|'pursued'|'abandoned'|'uncertain'};knowledge:{fact:string;learnedByIds:string[]}
  relationship:{otherId:string;relation:string};unresolved:{threadKey:string;state:'opened'|'advanced'|'resolved';detail:string}
  event:{effect:string;causedBy:string[]}
}
type EntryBase={id:string;subjectId:string|null;certainty:'narrated'|'inferred';confidence:number;statement:string;quote:string;start:number;end:number;offsetUnit:'utf16'}
export type NarrativeEvent={[K in keyof Changes]:EntryBase&{kind:K;change:Changes[K]}}[keyof Changes]
export interface EventLedger {schema:typeof eventSchema;sourceHash:string;chapterIndex:number;status:'model-proposed'|'unavailable';events:NarrativeEvent[]}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const exact=(v:unknown,fields:string):v is Record<string,unknown>=>object(v)&&Object.keys(v).length===fields.split(' ').length&&fields.split(' ').every(key=>Object.hasOwn(v,key))
const int=(v:unknown,min:number,max:number):v is number=>Number.isSafeInteger(v)&&Number(v)>=min&&Number(v)<=max
export function scalarText(v:unknown,max:number):v is string{
  if(typeof v!=='string'||!v.trim()||v.includes('\0')||Array.from(v).length>max)return false
  for(let i=0;i<v.length;i++){const c=v.charCodeAt(i);if(c>=0xd800&&c<=0xdbff){const d=v.charCodeAt(++i);if(!(d>=0xdc00&&d<=0xdfff))return false}else if(c>=0xdc00&&c<=0xdfff)return false}return true
}
const nullableText=(v:unknown,max:number)=>v===null||scalarText(v,max)
const enumValue=(v:unknown,values:string[])=>typeof v==='string'&&values.includes(v)
const identity=(v:unknown,subjects?:Set<string>,nullable=false)=>v===null&&nullable||scalarText(v,160)&&(!subjects||subjects.has(v))
const idParts=(v:unknown)=>typeof v==='string'?/^c([1-9][0-9]{0,2}):e([1-9][0-9]?)$/.exec(v):null
function eventId(v:unknown):v is string{const m=idParts(v);return !!m&&Number(m[1])<=200&&Number(m[2])<=12}
const unique=(v:unknown,max:number,check:(x:unknown)=>boolean,min=0):v is unknown[]=>Array.isArray(v)&&v.length>=min&&v.length<=max&&v.every(check)&&new Set(v).size===v.length
function validChange(kind:unknown,c:unknown,subjects?:Set<string>,known?:Set<string>):boolean{
  switch(kind){
    case 'character':return exact(c,'name aliases')&&scalarText(c.name,160)&&unique(c.aliases,8,x=>scalarText(x,160))
    case 'item':return exact(c,'item fromHolderId toHolderId location')&&scalarText(c.item,200)&&identity(c.fromHolderId,subjects,true)&&identity(c.toHolderId,subjects,true)&&nullableText(c.location,200)
    case 'location':return exact(c,'from to')&&nullableText(c.from,200)&&scalarText(c.to,200)
    case 'time':return exact(c,'label relation relativeTo')&&scalarText(c.label,200)&&enumValue(c.relation,['before','after','same_time','unknown'])&&(c.relativeTo===null&&c.relation==='unknown'||eventId(c.relativeTo)&&c.relation!=='unknown'&&(!known||known.has(c.relativeTo)))
    case 'motivation':return exact(c,'goal state')&&scalarText(c.goal,400)&&enumValue(c.state,['adopted','pursued','abandoned','uncertain'])
    case 'knowledge':return exact(c,'fact learnedByIds')&&scalarText(c.fact,600)&&unique(c.learnedByIds,16,x=>identity(x,subjects),1)
    case 'relationship':return exact(c,'otherId relation')&&identity(c.otherId,subjects)&&scalarText(c.relation,400)
    case 'unresolved':return exact(c,'threadKey state detail')&&scalarText(c.threadKey,200)&&enumValue(c.state,['opened','advanced','resolved'])&&scalarText(c.detail,600)
    case 'event':return exact(c,'effect causedBy')&&scalarText(c.effect,600)&&unique(c.causedBy,12,x=>eventId(x)&&(!known||known.has(x)))
    default:return false
  }
}
function bad():never{throw new ContractError('WRITING_EVENTS','事件记录与来源不一致，正文仍保留')}
/** A clipped view retains the original event ID, including gaps in its sequence. */
export function validateNarrativeEvent(entry:unknown):asserts entry is NarrativeEvent{
 if(!exact(entry,'id kind subjectId certainty confidence statement quote start end offsetUnit change')||!eventId(entry.id)||!identity(entry.subjectId,undefined,true)||!enumValue(entry.certainty,['narrated','inferred'])||!int(entry.confidence,0,1000)||!scalarText(entry.statement,600)||!scalarText(entry.quote,1200)||entry.offsetUnit!=='utf16'||!int(entry.start,0,6_000_000)||!int(entry.end,1,6_000_000)||entry.end<=entry.start||entry.end-entry.start!==entry.quote.length||!validChange(entry.kind,entry.change))bad()
 const current=idParts(entry.id)!,c=entry.change as Record<string,unknown>,refs=entry.kind==='time'&&c.relativeTo!==null?[c.relativeTo]:entry.kind==='event'?c.causedBy as string[]:[]
 for(const ref of refs){const m=idParts(ref)!;if(Number(m[1])>Number(current[1])||Number(m[1])===Number(current[1])&&Number(m[2])>=Number(current[2]))bad()}
}
/** Structural/quoted closure is synchronous; source hashing is verified at durable boundaries. */
export function validateEvents(v:unknown,text?:string,options:{subjects?:Set<string>;priorIds?:Iterable<string>;chapterIndex?:number;sourceHash?:string}={}):asserts v is EventLedger{
  if(!exact(v,'schema sourceHash chapterIndex status events')||v.schema!==eventSchema||typeof v.sourceHash!=='string'||!/^[a-f0-9]{64}$/.test(v.sourceHash)||!int(v.chapterIndex,1,200)||!enumValue(v.status,['model-proposed','unavailable'])||!Array.isArray(v.events)||v.events.length>12||v.status==='unavailable'&&v.events.length||options.chapterIndex!==undefined&&v.chapterIndex!==options.chapterIndex||options.sourceHash!==undefined&&v.sourceHash!==options.sourceHash)bad()
  const known=options.priorIds?new Set(options.priorIds):undefined
  for(const [index,entry] of (v.events as unknown[]).entries()){
    if(!exact(entry,'id kind subjectId certainty confidence statement quote start end offsetUnit change')||entry.id!==`c${v.chapterIndex}:e${index+1}`||!identity(entry.subjectId,options.subjects,true)||!enumValue(entry.certainty,['narrated','inferred'])||!int(entry.confidence,0,1000)||!scalarText(entry.statement,600)||!scalarText(entry.quote,1200)||entry.offsetUnit!=='utf16'||!int(entry.start,0,6_000_000)||!int(entry.end,1,6_000_000)||entry.end<=entry.start||entry.end-entry.start!==entry.quote.length||!validChange(entry.kind,entry.change,options.subjects,known))bad()
    if(text!==undefined&&text.slice(entry.start as number,entry.end as number)!==entry.quote)bad()
    const c=entry.change as Record<string,unknown>,refs=entry.kind==='time'&&c.relativeTo!==null?[c.relativeTo]:entry.kind==='event'?c.causedBy as string[]:[]
    for(const ref of refs){const m=idParts(ref)!;if(Number(m[1])>Number(v.chapterIndex)||Number(m[1])===v.chapterIndex&&Number(m[2])>=index+1)bad()}
    known?.add(String(entry.id))
  }
}
function ordered(a:string,b:string){const aa=Array.from(a,c=>c.codePointAt(0)!),bb=Array.from(b,c=>c.codePointAt(0)!);for(let i=0;i<Math.min(aa.length,bb.length);i++)if(aa[i]!==bb[i])return aa[i]!-bb[i]!;return aa.length-bb.length}
/** Python canonical sorts by Unicode scalar, unlike JavaScript's UTF16 sort. */
export function eventCanonical(v:unknown):string{if(Array.isArray(v))return '['+v.map(eventCanonical).join(',')+']';if(object(v))return '{'+Object.keys(v).sort(ordered).map(key=>JSON.stringify(key)+':'+eventCanonical(v[key])).join(',')+'}';return JSON.stringify(v)}
export function foldEvents(ledgers:EventLedger[]){return foldEventGroups([{scope:null,ledgers}])}
export function foldScopedEvents(groups:{scope:string;ledgers:EventLedger[]}[]){
 if(!Array.isArray(groups)||groups.length>1000||groups.some(group=>!exact(group,'scope ledgers')||!scalarText(group.scope,300)||!Array.isArray(group.ledgers))||new Set(groups.map(group=>group.scope)).size!==groups.length)bad()
 return foldEventGroups(groups)
}
function foldEventGroups(groups:{scope:string|null;ledgers:EventLedger[]}[]){
  const dict=<T>()=>Object.create(null) as Record<string,T>
  const items=dict<{holderId:string|null;location:string|null;eventId:string}>(),locations=dict<{place:string;eventId:string}>(),motivations=dict<Changes['motivation']&{eventId:string}>(),threads=dict<{state:string;detail:string;eventId:string}>(),knowledge=dict<{fact:string;eventId:string}[]>(),characters=dict<Changes['character']&{subjectId:string|null;eventId:string}>()
  const timeRelations:(Changes['time']&{eventId:string})[]=[],hints:{code:string;eventId:string;previousEventId:string;severity:'soft'}[]=[],eventOrder:string[]=[],historicalEvents:string[]=[],frames=dict<boolean>(),relationships=dict<Record<string,{relation:string;eventId:string}>>(),causalEvents:{eventId:string;effect:string;causedBy:string[];temporalFrame:'past'|'present'}[]=[]
  for(const {scope,ledgers} of groups){const prior=new Set<string>();let previousChapter=0;const keyFor=(id:string)=>scope===null?id:scope+'/'+id
  for(const ledger of ledgers){validateEvents(ledger,undefined,{priorIds:prior});if(ledger.chapterIndex<=previousChapter)bad();previousChapter=ledger.chapterIndex;let earlier=false
    for(const e of ledger.events){prior.add(e.id);const key=keyFor(e.id);eventOrder.push(key);if(e.certainty!=='narrated')continue
      const hint=(code:string,previousEventId:string)=>hints.push({code,eventId:key,previousEventId,severity:'soft'})
      if(e.kind==='time'){timeRelations.push({eventId:key,...e.change,relativeTo:e.change.relativeTo===null?null:keyFor(e.change.relativeTo)});if(e.change.relation!=='unknown')earlier=e.change.relation==='before'||frames[keyFor(e.change.relativeTo!)]===true;frames[key]=earlier;continue}
      frames[key]=earlier;if(e.kind==='event')causalEvents.push({eventId:key,effect:e.change.effect,causedBy:e.change.causedBy.map(keyFor),temporalFrame:earlier?'past':'present'});if(earlier){historicalEvents.push(key);continue}
      if(e.kind==='item'){const c=e.change,old=items[c.item];if(old&&old.holderId!==null&&c.fromHolderId!==null&&old.holderId!==c.fromHolderId)hint('ITEM_TRANSFER_GAP',old.eventId);items[c.item]={holderId:c.toHolderId,location:c.location,eventId:key}}
      else if(e.kind==='location'&&e.subjectId!==null){const old=locations[e.subjectId];if(old&&e.change.from!==null&&old.place!==e.change.from)hint('LOCATION_TRANSITION_GAP',old.eventId);locations[e.subjectId]={place:e.change.to,eventId:key}}
      else if(e.kind==='motivation'&&e.subjectId!==null)motivations[e.subjectId]={...e.change,eventId:key}
      else if(e.kind==='knowledge')for(const actor of e.change.learnedByIds)(knowledge[actor]??=[]).push({fact:e.change.fact,eventId:key})
      else if(e.kind==='unresolved'){const old=threads[e.change.threadKey];if(old&&old.state==='resolved'&&e.change.state==='advanced')hint('THREAD_REOPENING_UNCLEAR',old.eventId);threads[e.change.threadKey]={state:e.change.state,detail:e.change.detail,eventId:key}}
      else if(e.kind==='relationship'&&e.subjectId!==null)(relationships[e.subjectId]??=dict())[e.change.otherId]={relation:e.change.relation,eventId:key}
      else if(e.kind==='character')characters[e.subjectId??key]={...e.change,subjectId:e.subjectId,eventId:key}
    }
  }
  }
  const plain=<T>(value:Record<string,T>):Record<string,T>=>Object.fromEntries(Object.entries(value))
  return {schema:'prose-event-state-v2' as const,eventOrder,items:plain(items),locations:plain(locations),motivations:plain(motivations),threads:plain(threads),knowledge:plain(knowledge),characters:plain(characters),timeRelations,historicalEvents,relationships:plain(Object.fromEntries(Object.entries(relationships).map(([id,peers])=>[id,plain(peers)]))),causalEvents,hints}
}
/** Finite safe extraction from the already received generation/review envelope. */
export async function eventsFromProposals(proposals:unknown,text:string,chapterIndex:number,subjects?:Set<string>,priorIds:Iterable<string>=[]):Promise<EventLedger>{
  const empty:EventLedger={schema:eventSchema,sourceHash:await sha256(text),chapterIndex,status:'unavailable',events:[]}
  try{
    if(!Array.isArray(proposals)||proposals.length>12)return empty
    const entries=proposals.map((p,index)=>{if(!exact(p,'kind subjectId certainty confidence statement quote change')||typeof p.quote!=='string')bad();const start=text.indexOf(p.quote);if(start<0||text.indexOf(p.quote,start+1)>=0)bad();return {...p,id:`c${chapterIndex}:e${index+1}`,start,end:start+p.quote.length,offsetUnit:'utf16'}})
    const ledger={...empty,status:'model-proposed',events:entries};validateEvents(ledger,text,{subjects,priorIds});return ledger
  }catch{return empty}
}
