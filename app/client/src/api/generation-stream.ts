import {parseGenerationSelection} from './generation-selection'
import {proseProtocol,type GenerationSnapshot,type GenerationEvent,type GenerationChapter,type GenerationHint,type GenerationDurability,type GenerationRawPresentation,type GenerationReceivedRaw} from './generation-types'
import {validateEvents,foldEvents,type EventLedger} from '../writing/events'
import {parseAcceptedEventContext,type AcceptedEventContext} from '../writing/accepted-context'
export type GenerationReduction={kind:'applied'|'duplicate'|'resync'|'ignored';value:GenerationSnapshot;reason?:string}
const durability=new Set(['journaled','saving','saved','unconfirmed'])
const integer=(n:unknown,min=0)=>Number.isSafeInteger(n)&&Number(n)>=min
const record=(n:unknown):n is Record<string,unknown>=>!!n&&typeof n==='object'&&!Array.isArray(n)
const string=(n:unknown):n is string=>typeof n==='string'
export function wellFormedUtf16(text:string){
  for(let i=0;i<text.length;i++){const c=text.charCodeAt(i);if(c>=0xd800&&c<=0xdbff){const d=text.charCodeAt(++i);if(!(d>=0xdc00&&d<=0xdfff))return false}else if(c>=0xdc00&&c<=0xdfff)return false}return true
}
function invalid():never{throw new Error('写作记录不完整，请重新载入已保存的结果')}
const exactKeys=(value:Record<string,unknown>,keys:string[])=>Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key))
function rawPresentation(input:unknown,chapter:Record<string,unknown>):GenerationRawPresentation|undefined{
  if(input===undefined||input===null)return undefined
  if(!record(input)||!exactKeys(input,['schema','chapterIndex','selected','attempts','presentation'])||input.schema!=='prose-presentation-v1'||input.chapterIndex!==chapter.index||!['original','repair'].includes(String(input.selected))||!Array.isArray(input.attempts)||input.attempts.length<1||input.attempts.length>2||!record(input.presentation))return invalid()
  const p=input.presentation
  if(!exactKeys(p,['text','title','format','thoughtOmitted','pending'])||p.text!==chapter.text||p.title!==chapter.title||!['prose','json','unrecognized'].includes(String(p.format))||typeof p.thoughtOmitted!=='boolean'||typeof p.pending!=='boolean')return invalid()
  const attempts=input.attempts.map(a=>{
    if(!record(a)||!exactKeys(a,['kind','raw','rawHash','finishReason'])||!['original','repair'].includes(String(a.kind))||!string(a.raw)||a.raw.length>2_000_000||!wellFormedUtf16(a.raw)||!string(a.rawHash)||!/^[a-f0-9]{64}$/.test(a.rawHash)||!(a.finishReason===null||string(a.finishReason)&&a.finishReason.length<=80))return invalid()
    return {kind:a.kind as 'original'|'repair',raw:a.raw,rawHash:a.rawHash,finishReason:a.finishReason as string|null}
  })
  const selected=attempts.find(a=>a.kind===input.selected)
  if(new Set(attempts.map(a=>a.kind)).size!==attempts.length||!selected)return invalid()
  if(chapter.completeness==='complete'&&(p.pending||p.format==='unrecognized'||!String(chapter.text).trim()||!['stop','completed'].includes(selected.finishReason||'')))return invalid()
  return {schema:'prose-presentation-v1',chapterIndex:Number(input.chapterIndex),selected:input.selected as 'original'|'repair',attempts,presentation:{text:String(p.text),title:String(p.title),format:p.format as 'prose'|'json'|'unrecognized',thoughtOmitted:p.thoughtOmitted,pending:p.pending}}
}
export function parseReceivedRaw(input:unknown):GenerationReceivedRaw{
  if(!record(input)||!exactKeys(input,['generationId','candidateVersion','contentId','streamVersion','snapshotVersion','rawPresentation'])||!string(input.generationId)||!input.generationId||input.generationId.length>160||!string(input.contentId)||!input.contentId||input.contentId.length>160||!integer(input.candidateVersion,1)||!integer(input.streamVersion,1)||!integer(input.snapshotVersion)||!record(input.rawPresentation)||!record(input.rawPresentation.presentation))return invalid()
  const p=input.rawPresentation.presentation,index=input.rawPresentation.chapterIndex
  if(!integer(index,1)||Number(index)>10||!string(p.text)||p.text.length>2_000_000||!wellFormedUtf16(p.text)||!string(p.title)||p.title.length>1000||!wellFormedUtf16(p.title))return invalid()
  const raw=rawPresentation(input.rawPresentation,{index,text:p.text,title:p.title,completeness:'partial'})
  if(!raw)return invalid()
  return {generationId:input.generationId,candidateVersion:Number(input.candidateVersion),contentId:input.contentId,streamVersion:Number(input.streamVersion),snapshotVersion:Number(input.snapshotVersion),rawPresentation:raw}
}
// Copy only the public contract: a checkpoint/provider prompt cannot leak through
// an object spread into the draft or its persistent cache.
export function parseGenerationSnapshot(input:unknown):GenerationSnapshot{
  if(!record(input)||input.protocol!==proseProtocol||!string(input.id)||!string(input.taskId)||!string(input.bookId)||!record(input.scopeAnchor)||!Array.isArray(input.chapters))return invalid()
  const a=input.scopeAnchor
  if(!['bookId','branchId','revisionId','chapterId','blockId','quoteHash'].every(k=>string(a[k]))||a.bookId!==input.bookId||a.offsetUnit!=='utf16'||!integer(a.start)||!integer(a.end)||Number(a.start)>Number(a.end))return invalid()
  if(!integer(input.candidateVersion,1)||!integer(input.eventCursor)||!integer(input.requestedChapters,1)||Number(input.requestedChapters)>10||!integer(input.acceptedPrefix)||Number(input.acceptedPrefix)>Number(input.requestedChapters)||!integer(input.completedChapters)||Number(input.completedChapters)>Number(input.requestedChapters))return invalid()
  if(!['state','phase','providerState','persistenceState','usageState','createdAt'].every(k=>string(input[k]))||typeof input.cancelRequested!=='boolean')return invalid()
  if(['importedHistory','readOnly'].some(k=>input[k]!==undefined&&typeof input[k]!=='boolean'))return invalid()
  const chapters:GenerationChapter[]=input.chapters.map((c,i)=>{
    if(!record(c)||c.index!==i+1||c.index>Number(input.requestedChapters)||!string(c.contentId)||!c.contentId||!string(c.title)||!wellFormedUtf16(c.title)||!string(c.text)||!wellFormedUtf16(c.text)||!integer(c.streamVersion,1)||!integer(c.snapshotVersion)||!['partial','complete'].includes(String(c.completeness))||!durability.has(String(c.durability))||typeof c.canAccept!=='boolean'||!(c.contentHash===null||string(c.contentHash)))return invalid()
    if(c.canAccept&&(c.completeness!=='complete'||c.durability!=='saved'||!c.contentHash))return invalid()
    const hints:GenerationHint[]=Array.isArray(c.hints)?c.hints.filter(record).filter(h=>string(h.code)).map(h=>({code:String(h.code),...(string(h.message)?{message:h.message}:{})})):[]
    const raw=rawPresentation(c.rawPresentation,c)
    let events:EventLedger|undefined
    if(c.eventLedger!==undefined&&c.eventLedger!==null)try{validateEvents(c.eventLedger,c.text,{chapterIndex:i+1,...(c.contentHash?{sourceHash:String(c.contentHash)}:{})});events=JSON.parse(JSON.stringify(c.eventLedger)) as EventLedger}catch{if(!hints.some(h=>h.code==='CHECK_UNAVAILABLE'))hints.push({code:'CHECK_UNAVAILABLE'})}
    return {index:c.index as number,contentId:c.contentId,title:c.title,text:c.text,contentHash:c.contentHash as string|null,streamVersion:Number(c.streamVersion),snapshotVersion:Number(c.snapshotVersion),completeness:c.completeness as 'partial'|'complete',durability:c.durability as GenerationDurability,canAccept:c.canAccept,hints,...(raw?{rawPresentation:raw}:{}),...(events?{eventLedger:events}:{})}
  })
  if(new Set(chapters.map(c=>c.contentId)).size!==chapters.length||chapters.filter(c=>c.completeness==='complete'&&c.durability==='saved').length<Number(input.completedChapters))return invalid()
  let eventStateStatus:'complete'|'unavailable'=input.eventStateStatus==='unavailable'||chapters.some(c=>c.hints.some(h=>h.code==='CHECK_UNAVAILABLE'))?'unavailable':'complete'
  try{foldEvents(chapters.filter(c=>c.eventLedger).map(c=>c.eventLedger!))}catch{eventStateStatus='unavailable';for(const c of chapters)if(!c.hints.some(h=>h.code==='CHECK_UNAVAILABLE'))c.hints.push({code:'CHECK_UNAVAILABLE'})}
  let acceptedEventBasis:AcceptedEventContext|undefined,acceptedBasisUnavailable=input.acceptedBasisUnavailable===true
  if(input.acceptedEventBasis!==undefined)try{acceptedEventBasis=parseAcceptedEventContext(input.acceptedEventBasis);if(['bookId','branchId','revisionId','chapterId','blockId','start','end','offsetUnit','quoteHash'].some(k=>acceptedEventBasis!.scope[k as keyof AcceptedEventContext['scope']]!==a[k]))throw new Error('basis scope')}catch{acceptedEventBasis=undefined;acceptedBasisUnavailable=true}
  for(let i=0;i<Number(input.acceptedPrefix);i++)if(!chapters[i]||chapters[i].completeness!=='complete'||chapters[i].durability!=='saved'||!chapters[i].contentHash)return invalid()
  const selection=parseGenerationSelection(input.selection,a as unknown as import('./types').Anchor)
  return {...(selection?{selection}:{}),id:input.id,taskId:input.taskId,bookId:input.bookId,protocol:proseProtocol,importedHistory:input.importedHistory===true,readOnly:input.readOnly===true,scopeAnchor:{bookId:String(a.bookId),branchId:String(a.branchId),revisionId:String(a.revisionId),chapterId:String(a.chapterId),blockId:String(a.blockId),start:Number(a.start),end:Number(a.end),offsetUnit:'utf16',quoteHash:String(a.quoteHash)},createdAt:String(input.createdAt),candidateVersion:Number(input.candidateVersion),requestedChapters:Number(input.requestedChapters),completedChapters:Number(input.completedChapters),acceptedPrefix:Number(input.acceptedPrefix),targetBranchId:string(input.targetBranchId)?input.targetBranchId:null,lastAcceptedRevisionId:string(input.lastAcceptedRevisionId)?input.lastAcceptedRevisionId:null,eventCursor:Number(input.eventCursor),state:String(input.state),phase:String(input.phase),providerState:String(input.providerState),persistenceState:String(input.persistenceState),usageState:String(input.usageState),cancelRequested:input.cancelRequested,errorCode:string(input.errorCode)?input.errorCode:null,chapters,eventStateStatus,...(acceptedEventBasis?{acceptedEventBasis}:{}),...(acceptedBasisUnavailable?{acceptedBasisUnavailable:true}:{}),...(record(input.fee)?{fee:{amount:typeof input.fee.amount==='number'&&Number.isFinite(input.fee.amount)?input.fee.amount:null,currency:string(input.fee.currency)?input.fee.currency:null,certainty:input.fee.certainty==='known'?'known' as const:'unknown' as const}}:{})}
}
export function mergeGenerationSnapshot(old:GenerationSnapshot|null,input:GenerationSnapshot):GenerationSnapshot{
  if(old&&(input.id!==old.id||input.taskId!==old.taskId||input.bookId!==old.bookId))throw new Error('写作记录属于其他任务')
  if(old&&(input.eventCursor<old.eventCursor||input.candidateVersion<old.candidateVersion))return old
  if(old&&input.acceptedPrefix<old.acceptedPrefix)throw new Error('已保存的章节不能撤回')
  if(old)for(let i=0;i<old.acceptedPrefix;i++){
    const a=old.chapters[i],b=input.chapters[i]
    if(!a||!b||a.contentId!==b.contentId||a.text!==b.text||a.contentHash!==b.contentHash||a.title!==b.title)throw new Error('已保存的正文不能被候选版本覆盖')
  }
  const next=parseGenerationSnapshot(input)
  if(old?.acceptedEventBasis){
    if(next.acceptedEventBasis&&JSON.stringify(old.acceptedEventBasis)!==JSON.stringify(next.acceptedEventBasis))throw new Error('本次续写依据不能被后续结果覆盖')
    if(!next.acceptedEventBasis)next.acceptedEventBasis=old.acceptedEventBasis
  }
  if(old)for(let i=0;i<old.acceptedPrefix;i++){
    const known=old.chapters[i]?.rawPresentation,current=next.chapters[i]?.rawPresentation
    if(known&&JSON.stringify(known)!==JSON.stringify(current))throw new Error('已保存的原返回不能被候选版本覆盖')
    const oldEvents=old.chapters[i]?.eventLedger
    if(oldEvents&&JSON.stringify(oldEvents)!==JSON.stringify(next.chapters[i]?.eventLedger))throw new Error('已保存的事件记录不能被候选版本覆盖')
  }
  if(old&&next.candidateVersion===old.candidateVersion)for(let i=0;i<old.chapters.length;i++){
    const a=old.chapters[i],b=next.chapters[i]
    if(!b||b.streamVersion<a.streamVersion||b.streamVersion===a.streamVersion&&(!b.text.startsWith(a.text)||b.snapshotVersion<a.snapshotVersion))return old
  }
  return next
}
export function reduceGeneration(value:GenerationSnapshot,event:GenerationEvent):GenerationReduction{
  if(generationReadOnly(value))return {kind:'ignored',value}
  const resync=(reason:string):GenerationReduction=>({kind:'resync',value,reason})
  if(event.taskId!==value.taskId)return {kind:'ignored',value}
  if(!integer(event.seq,1))return resync('invalid-sequence')
  if(event.seq<=value.eventCursor)return {kind:'duplicate',value}
  if(event.seq!==value.eventCursor+1)return resync('sequence-gap')
  if(!record(event.payload))return resync('invalid-payload')
  const p=event.payload
  if(p.candidateVersion!==undefined&&p.candidateVersion!==value.candidateVersion)return resync('candidate-version')
  if(event.type==='text_delta'){
    const chapter=value.chapters.find(c=>c.index===p.chapterIndex)
    if(!chapter||chapter.contentId!==p.contentId||chapter.streamVersion!==p.streamVersion)return resync('chapter-version')
    if(chapter.index<=value.acceptedPrefix||chapter.completeness==='complete')return resync('immutable-chapter')
    if(p.startUtf16!==chapter.text.length||!string(p.delta)||!wellFormedUtf16(p.delta)||p.durability!=='journaled')return resync('delta-boundary-or-durability')
    // A public delta contains reading bytes only. Its prior raw receipt is a
    // different snapshot; fetch the new owner-only receipt on the next GET.
    return {kind:'applied',value:{...value,eventCursor:event.seq,chapters:value.chapters.map(c=>{if(c!==chapter)return c;const {rawPresentation:previousRaw,eventLedger:previousEvents,...reading}=c;return {...reading,text:c.text+p.delta as string,durability:'journaled' as const}})}}
  }
  // Saved/terminal/usage are confirmed by a consistent snapshot; never mark a
  // chapter accepted or a task finished from a disconnected notification alone.
  return resync('authoritative-'+event.type)
}
export function generationReadOnly(value:GenerationSnapshot):boolean{return value.importedHistory===true||value.readOnly===true||value.state==='historical-unknown'}
export function generationFinished(value:GenerationSnapshot):boolean{return generationReadOnly(value)||['succeeded','completed','failed','cancelled','result-unknown'].includes(value.state)&&!['recoverable','saving','unconfirmed'].includes(value.persistenceState)}
export function acceptablePrefix(value:GenerationSnapshot):number{
  let prefix=value.acceptedPrefix
  for(let i=prefix;i<value.requestedChapters;i++){
    const c=value.chapters[i];if(!c||c.index!==i+1||c.completeness!=='complete'||c.durability!=='saved'||!c.canAccept||!c.contentHash)break
    prefix=i+1
  }
  return prefix
}
