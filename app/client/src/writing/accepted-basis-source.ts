/** Read only accepted origins reachable from an exact, immutable reading prefix. */
import {canonical,sha256,ContractError,unicodeOffset,type Document,type Paragraph} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {WritingRun} from './contract'
import {acceptedWritingParagraphs} from './accepted-prose'
import {createAcceptedProof,validateAcceptedProof,validateAcceptedProofBytes} from './accepted-proof'
import {deriveVisibleAcceptedEvents,type AcceptedSourceInput,type VisibleAcceptedParagraph} from './accepted-events'
import {foldVisibleAcceptedEvents} from './accepted-event-state'
import {eventCanonical} from './events'
import type {NovelCharacter} from '../novel-characters/contract'
import type {AcceptedBasis,AcceptedIdentityBinding,AcceptedSourceReference,AcceptedOriginReference} from './accepted-basis'

type SourceParagraph=Paragraph&{writingRunId?:string;forkOf?:{paragraphId:string;offset:number}}
type Origin={row:Document<WritingRun>;highestIndex:number}
function fail():never{throw new ContractError('ACCEPTED_EVENTS','已接受事件的来源范围不一致，原文仍保留')}
const clone=<T>(v:T):T=>JSON.parse(canonical(v))

export async function collectAcceptedBasis(vault:LocalVault,run:Pick<WritingRun,'prefixChapters'|'storyId'|'anchor'|'parentRevision'>,inline=new Map<string,Paragraph>()){
 const paragraphs=new Map<string,SourceParagraph>(),origins=new Map<string,Origin>(),prefix:VisibleAcceptedParagraph[]=[],warnings=new Set<string>()
 const readParagraph=async(id:string):Promise<SourceParagraph>=>{
  const cached=paragraphs.get(id);if(cached)return cached
  const provided=inline.get(id) as SourceParagraph|undefined,row=provided?null:await vault.read<SourceParagraph>(id,1),value=provided||row?.value
  if(!value||!provided&&(row?.kind!=='paragraph'||row.revision!==1)||value.id!==id||value.textHash!==await sha256(value.text))fail()
  paragraphs.set(id,value);return value
 }
 const rootOf=async(id:string)=>{
  const first=await readParagraph(id);let p=first
  const seen=new Set<string>()
  for(let depth=0;p.forkOf;depth++){
   if(depth>=100||seen.has(p.id))fail();seen.add(p.id)
   const original=await readParagraph(p.forkOf.paragraphId)
   if(p.chapterId!==original.chapterId||p.forkOf.offset<=0||p.forkOf.offset>=original.text.length||unicodeOffset(original.text,p.forkOf.offset)!==p.forkOf.offset||p.text!==original.text.slice(0,p.forkOf.offset))fail()
   p=original
  }
  if(!p.text.startsWith(first.text))fail()
  return p
 }
 const ids=run.prefixChapters.flatMap(c=>c.paragraphIds)
 for(const id of ids){
  const p=await readParagraph(id),root=await rootOf(id)
  prefix.push({paragraphId:id,paragraphRevision:1,sourceParagraphId:root.id,text:p.text,textHash:p.textHash})
  if(!root.writingRunId)continue
  let origin=origins.get(root.writingRunId)
  if(!origin){const row=await vault.read<WritingRun>(root.writingRunId);if(row?.kind!=='settings'||row.value.assetType!=='writing-run'||row.value.storyId!==run.storyId){warnings.add('SOURCE_EVENTS_UNAVAILABLE');continue}origin={row,highestIndex:0};origins.set(row.id,origin)}
  const index=origin.row.value.chapters.findIndex(c=>c.id===root.chapterId&&c.paragraphIds.includes(root.id))
  if(index<0||index>=origin.row.value.acceptedCount){warnings.add('SOURCE_EVENTS_UNAVAILABLE');continue}
  origin.highestIndex=Math.max(origin.highestIndex,index+1)
 }
 const sources:AcceptedSourceInput[]=[],references:AcceptedSourceReference[]=[],usableOrigins:Origin[]=[],originReferences:AcceptedOriginReference[]=[]
 for(const origin of origins.values()){
  const rows:AcceptedSourceInput[]=[],refs:AcceptedSourceReference[]=[]
   if(!origin.highestIndex)fail()
   const eventsAvailable=!!origin.row.value.eventSchema&&origin.row.value.chapters.slice(0,origin.highestIndex).every(c=>c.status==='complete'&&!!c.eventLedger)
   originReferences.push({runId:origin.row.id,runRevision:origin.row.revision,through:origin.highestIndex,eventsAvailable})
   if(!eventsAvailable){warnings.add('SOURCE_EVENTS_UNAVAILABLE');continue}
   for(const [index,c] of origin.row.value.chapters.slice(0,origin.highestIndex).entries()){
    if(!c.eventLedger||c.status!=='complete')throw new Error('No exact accepted events')
    const parts=acceptedWritingParagraphs(c)
    if(parts.length!==c.paragraphIds.length)fail()
    for(const [i,id] of c.paragraphIds.entries()){const p=await readParagraph(id);if(p.chapterId!==c.id||p.writingRunId!==origin.row.id||p.forkOf||p.text!==parts[i]!.text)fail()}
    const proof=c.acceptedProof||await createAcceptedProof(c.text,c.paragraphIds)
    validateAcceptedProof(proof,c.text,c.paragraphIds);await validateAcceptedProofBytes(proof,c.text)
    if(c.eventLedger.status==='unavailable')warnings.add('SOURCE_EVENTS_UNAVAILABLE')
    rows.push({runId:origin.row.id,runRevision:origin.row.revision,contentId:c.id,chapterIndex:index+1,text:c.text,style:'local-v1',ledger:c.eventLedger,segments:proof.segments})
    refs.push({runId:origin.row.id,runRevision:origin.row.revision,contentId:c.id,chapterIndex:index+1,sourceHash:proof.sourceHash,ledgerHash:await sha256(eventCanonical(c.eventLedger)),projectionHash:await sha256(eventCanonical(proof))})
   }
   sources.push(...rows);references.push(...refs);usableOrigins.push(origin)
 }
 const derived=await deriveVisibleAcceptedEvents(sources,prefix)
 if(derived.dependencyClippedRefs.length)warnings.add('SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE')
 const subjects=new Map<string,AcceptedIdentityBinding>(),conditions:{kind:'settings';id:string;revision:number}[]=[]
 for(const origin of usableOrigins){
  conditions.push({kind:'settings',id:origin.row.id,revision:origin.row.revision})
  const actorIds=new Set<string>()
  for(const view of derived.events.filter(e=>e.ref.runId===origin.row.id)){
   const e=view.entry;if(e.subjectId!==null)actorIds.add(e.subjectId)
   if(e.kind==='item'){if(e.change.fromHolderId!==null)actorIds.add(e.change.fromHolderId);if(e.change.toHolderId!==null)actorIds.add(e.change.toHolderId)}
   else if(e.kind==='knowledge')e.change.learnedByIds.forEach(id=>actorIds.add(id))
   else if(e.kind==='relationship')actorIds.add(e.change.otherId)
  }
  for(const subject of (origin.row.value.eventSubjects||[]).filter(s=>actorIds.has(s.id))){
   const row=await vault.read<NovelCharacter>(subject.id,subject.revision),c=row?.value
   if(row?.kind!=='settings'||row.revision!==subject.revision||c?.assetType!=='novel-character'||c.scope.storyId!==run.storyId||c.name!==subject.name||c.primary.revision!==1||c.primary.quote!==c.name)fail()
   const root=await rootOf(c.primary.id),visible=prefix.find(p=>p.sourceParagraphId===root.id)
   if(!visible||c.primary.offset+c.primary.quote.length>visible.text.length||visible.text.slice(c.primary.offset,c.primary.offset+c.primary.quote.length)!==c.primary.quote)continue
   const primary={paragraphId:root.id,paragraphRevision:1 as const,offset:c.primary.offset,quote:c.primary.quote,visibleParagraphId:visible.paragraphId,visibleHash:visible.textHash}
   const canonicalId='reader-proof:'+await sha256(eventCanonical({storyId:run.storyId,paragraphId:root.id,revision:1,offset:c.primary.offset,quote:c.primary.quote}))
   subjects.set(canonical([origin.row.id,subject.id]),{runId:origin.row.id,subjectId:subject.id,subjectRevision:subject.revision,name:subject.name,canonicalId,primary})
  }
 }
 const bindings=[...subjects.values()],state=foldVisibleAcceptedEvents(derived.events,bindings)
 const value={schema:'accepted-events-basis-v1' as const,scope:{anchor:clone(run.anchor),parentRevision:run.parentRevision},
  prefix:prefix.map(p=>{const lineage:string[]=[];let q=paragraphs.get(p.paragraphId)!;while(q.forkOf){lineage.push(q.forkOf.paragraphId);q=paragraphs.get(q.forkOf.paragraphId)!}return {paragraphId:p.paragraphId,paragraphRevision:1 as const,sourceParagraphId:p.sourceParagraphId,textHash:p.textHash,limit:p.text.length,lineage}}),
  origins:originReferences,sources:references,events:derived.events,subjects:bindings,state,
  status:warnings.size?'partial' as const:'complete' as const,warnings:[...warnings].sort()}
 const basis:AcceptedBasis={...value,hash:await sha256(eventCanonical(value))}
 for(const origin of origins.values())if(!conditions.some(c=>c.id===origin.row.id))conditions.push({kind:'settings',id:origin.row.id,revision:origin.row.revision})
 return {basis,conditions}
}
