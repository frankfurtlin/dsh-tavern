/** Derive quoted views without changing accepted source history or making model calls. */
import {sha256,canonical,ContractError,unicodeOffset} from '../domain/local-contract'
import {validateEvents,type NarrativeEvent,type EventLedger} from './events'
import {validateAcceptedProjection,projectionGapIsWhitespace,type AcceptanceStyle,type AcceptedTextSegment} from './accepted-projection'

export interface EventRef {runId:string;contentId:string;localEventId:string}
export interface AcceptedSourceInput {
 runId:string;runRevision:number;contentId:string;chapterIndex:number;text:string;style:AcceptanceStyle;
 ledger:EventLedger;segments:(AcceptedTextSegment&{paragraphId:string;paragraphHash:string})[];
}
export interface VisibleAcceptedParagraph {
 paragraphId:string;paragraphRevision:number;sourceParagraphId:string;text:string;textHash:string;
}
export interface VisibleAcceptedEvent {
 ref:EventRef;sourceRunRevision:number;sourceHash:string;ledgerHash:string;
 entry:NarrativeEvent;dependencies:EventRef[];frameRef:EventRef|null;
 temporalFrame:'past'|'present';
 evidence:{paragraphId:string;paragraphRevision:number;start:number;end:number;quote:string}[];
}
function fail():never{throw new ContractError('ACCEPTED_EVENTS','已接受事件的来源范围不一致，正文仍保留')}
const key=(ref:EventRef)=>canonical(ref)
const clone=<T>(v:T):T=>JSON.parse(canonical(v))

/** Inputs are exact accepted sources, never arbitrary same-book tasks. No model calls. */
export async function deriveVisibleAcceptedEvents(sources:AcceptedSourceInput[],prefix:VisibleAcceptedParagraph[]){
 for(const p of prefix)if(await sha256(p.text)!==p.textHash)fail()
 for(const source of sources){if(source.ledger.sourceHash!==await sha256(source.text))fail();for(const part of source.segments)if(await sha256(part.text)!==part.paragraphHash)fail()}
 const checked=await Promise.all(sources.map(async source=>({...source,ledgerHash:await sha256(canonical(source.ledger))})))
 return deriveAcceptedEventsFromSources(checked,prefix)
}
/** The storage boundary verifies bytes; relations replay exact pinned objects synchronously. */
export function deriveAcceptedEventsFromSources(sources:(AcceptedSourceInput&{ledgerHash:string})[],prefix:VisibleAcceptedParagraph[]){
 if(sources.length>1000||prefix.length>10000)fail()
 const visible=new Map<string,{value:VisibleAcceptedParagraph;position:number}>()
 for(const [position,p] of prefix.entries()){
  if(visible.has(p.sourceParagraphId)||p.paragraphRevision!==1)fail()
  visible.set(p.sourceParagraphId,{value:p,position})
 }
 const origin=new Map<string,Map<string,EventRef>>(),seen=new Set<string>()
 for(const source of sources){
  validateAcceptedProjection({schema:'accepted-projection-v1',style:source.style,segments:source.segments.map(p=>({text:p.text,sourceStart:p.sourceStart,sourceEnd:p.sourceEnd,targetStart:p.targetStart,targetEnd:p.targetEnd}))},source.text,source.style)
  const unique=canonical([source.runId,source.contentId]);if(seen.has(unique))fail();seen.add(unique)
  if(!Number.isSafeInteger(source.runRevision)||source.runRevision<1||source.chapterIndex!==source.ledger.chapterIndex||!/^[a-f0-9]{64}$/.test(source.ledgerHash))fail()
  validateEvents(source.ledger,source.text)
  const refs=origin.get(source.runId)||new Map<string,EventRef>();origin.set(source.runId,refs)
  for(const e of source.ledger.events){if(refs.has(e.id))fail();refs.set(e.id,{runId:source.runId,contentId:source.contentId,localEventId:e.id})}
  let last=0
  for(const segment of source.segments){if(segment.sourceStart<last||source.text.slice(segment.sourceStart,segment.sourceEnd)!==segment.text||segment.sourceEnd-segment.sourceStart!==segment.text.length||segment.targetStart!==0||segment.targetEnd!==segment.text.length||unicodeOffset(source.text,segment.sourceStart)!==segment.sourceStart||unicodeOffset(source.text,segment.sourceEnd)!==segment.sourceEnd)fail();last=segment.sourceEnd}
 }
 const frames=new Map<string,boolean>(),proposed:VisibleAcceptedEvent[]=[],clippedRefs:EventRef[]=[],dependencyClippedRefs:EventRef[]=[]
 const visibleRefs=new Set<string>()
 for(const source of sources){
  const refs=origin.get(source.runId)!,ledgerHash=source.ledgerHash
  let past=false,frameRef:EventRef|null=null
  for(const e of source.ledger.events){
   const ref=refs.get(e.id)!,deps:EventRef[]=[]
   const resolve=(id:string)=>{const value=refs.get(id);if(!value)fail();return value}
   if(e.kind==='time'&&e.change.relativeTo!==null)deps.push(resolve(e.change.relativeTo))
   if(e.kind==='event')deps.push(...e.change.causedBy.map(resolve))
   let currentFrame=frameRef
   if(e.certainty==='narrated'){
    if(e.kind==='time'){
     if(e.change.relation!=='unknown'){
      const relative=e.change.relativeTo===null?null:resolve(e.change.relativeTo)
      past=e.change.relation==='before'||(relative!==null&&frames.get(key(relative))===true)
      currentFrame=null;frameRef=ref
     }
    }
    frames.set(key(ref),past)
   }
   if(currentFrame!==null)deps.push(currentFrame)
   const uniqueDeps=[...new Map(deps.map(d=>[key(d),d])).values()]
   const evidence:VisibleAcceptedEvent['evidence']=[]
   let covered=e.start,lastPosition=-1,valid=true
   for(const part of source.segments){
    const start=Math.max(e.start,part.sourceStart),end=Math.min(e.end,part.sourceEnd)
    if(end<=start)continue
    if(!projectionGapIsWhitespace(source.text.slice(covered,start),source.style))valid=false
    const visiblePart=visible.get(part.paragraphId)
    const targetStart=start-part.sourceStart,targetEnd=end-part.sourceStart
    if(!visiblePart||lastPosition>=0&&visiblePart.position!==lastPosition+1||targetEnd>visiblePart.value.text.length||!part.text.startsWith(visiblePart.value.text))valid=false
    else {const p=visiblePart.value;if(unicodeOffset(p.text,targetStart)!==targetStart||unicodeOffset(p.text,targetEnd)!==targetEnd)fail();evidence.push({paragraphId:p.paragraphId,paragraphRevision:p.paragraphRevision,start:targetStart,end:targetEnd,quote:p.text.slice(targetStart,targetEnd)});lastPosition=visiblePart.position}
    covered=end
   }
   if(!evidence.length||!projectionGapIsWhitespace(source.text.slice(covered,e.end),source.style))valid=false
   if(!valid){clippedRefs.push(clone(ref));continue}
   // Every backward cause, time reference and implicit frame must itself be visible.
   if(uniqueDeps.some(d=>!visibleRefs.has(key(d)))){dependencyClippedRefs.push(clone(ref));continue}
   visibleRefs.add(key(ref))
   proposed.push({ref:clone(ref),sourceRunRevision:source.runRevision,sourceHash:source.ledger.sourceHash,ledgerHash,entry:clone(e),dependencies:clone(uniqueDeps),frameRef:clone(currentFrame),temporalFrame:past?'past':'present',evidence})
  }
 }
 return {schema:'accepted-event-view-v1' as const,events:proposed,clippedRefs,dependencyClippedRefs}
}
