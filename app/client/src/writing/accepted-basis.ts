/** Immutable accepted-story context. Original ledgers and narrative text stay literal. */
import {canonical,sha256,ContractError,unicodeOffset,type DomainKind,type Paragraph,type ProgressAnchor} from '../domain/local-contract'
import type {WritingRun} from './contract'
import type {NovelCharacter} from '../novel-characters/contract'
import {acceptedWritingParagraphs} from './accepted-prose'
import {projectAcceptedText} from './accepted-projection'
import {validateAcceptedProof,validateAcceptedProofBytes,type AcceptedChapterProof} from './accepted-proof'
import {deriveAcceptedEventsFromSources,type AcceptedSourceInput,type VisibleAcceptedParagraph,type VisibleAcceptedEvent} from './accepted-events'
import {foldVisibleAcceptedEvents,type SubjectBinding} from './accepted-event-state'
import {eventCanonical} from './events'

export interface AcceptedIdentityBinding extends SubjectBinding {
 subjectRevision:number;name:string;
 primary:{paragraphId:string;paragraphRevision:1;offset:number;quote:string;visibleParagraphId:string;visibleHash:string}
}
export interface AcceptedSourceReference {
 runId:string;runRevision:number;contentId:string;chapterIndex:number;sourceHash:string;ledgerHash:string;projectionHash:string
}
export interface AcceptedOriginReference {runId:string;runRevision:number;through:number;eventsAvailable:boolean}
export interface AcceptedBasis {
 schema:'accepted-events-basis-v1';scope:{anchor:ProgressAnchor;parentRevision:number};
 prefix:{paragraphId:string;paragraphRevision:1;sourceParagraphId:string;textHash:string;limit:number;lineage:string[]}[];
 origins:AcceptedOriginReference[];sources:AcceptedSourceReference[];events:VisibleAcceptedEvent[];subjects:AcceptedIdentityBinding[];
 state:ReturnType<typeof foldVisibleAcceptedEvents>;status:'complete'|'partial';warnings:string[];hash:string;
}
export type BasisLookup=(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined
type SourceParagraph=Paragraph&{writingRunId?:string;forkOf?:{paragraphId:string;offset:number}}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const exact=(v:unknown,fields:string)=>record(v)&&Object.keys(v).sort().join(' ')===fields.split(' ').sort().join(' ')
const id=(v:unknown)=>typeof v==='string'&&!!v&&v.length<=160
const positive=(v:unknown)=>Number.isSafeInteger(v)&&Number(v)>0
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)
function fail():never{throw new ContractError('ACCEPTED_BASIS','已读事件快照与确切来源不一致，原文仍保留')}
export function acceptedBasisValue(basis:AcceptedBasis){const {hash:_,...value}=basis;return value}
export function validateAcceptedBasis(value:unknown,run:WritingRun):asserts value is AcceptedBasis {
 if(!exact(value,'schema scope prefix origins sources events subjects state status warnings hash'))fail()
 const b=value as unknown as AcceptedBasis
 if(b.schema!=='accepted-events-basis-v1'||!run.eventSchema||!exact(b.scope,'anchor parentRevision')||canonical(b.scope.anchor)!==canonical(run.anchor)||b.scope.parentRevision!==run.parentRevision||!hash(b.hash)||!['complete','partial'].includes(b.status))fail()
 for(const [list,max] of [[b.prefix,10000],[b.origins,1000],[b.sources,1000],[b.events,12000],[b.subjects,2000],[b.warnings,2]] as const)if(!Array.isArray(list)||list.length>max)fail()
 if(canonical(b.prefix.map(p=>p.paragraphId))!==canonical(run.prefixChapters.flatMap(c=>c.paragraphIds))||b.prefix.some(p=>!exact(p,'paragraphId paragraphRevision sourceParagraphId textHash limit lineage')||!id(p.paragraphId)||p.paragraphRevision!==1||!id(p.sourceParagraphId)||!hash(p.textHash)||!positive(p.limit)||!Array.isArray(p.lineage)||p.lineage.length>100||p.lineage.some(x=>!id(x))))fail()
 if(b.origins.some(o=>!exact(o,'runId runRevision through eventsAvailable')||!id(o.runId)||!positive(o.runRevision)||!positive(o.through)||o.through>10||typeof o.eventsAvailable!=='boolean')||new Set(b.origins.map(o=>o.runId)).size!==b.origins.length)fail()
 if(b.sources.some(s=>!exact(s,'runId runRevision contentId chapterIndex sourceHash ledgerHash projectionHash')||!id(s.runId)||!positive(s.runRevision)||!id(s.contentId)||!positive(s.chapterIndex)||s.chapterIndex>10||![s.sourceHash,s.ledgerHash,s.projectionHash].every(hash)))fail()
 if(!record(b.state)||b.state.schema!=='accepted-event-state-v1'||new Set(b.warnings).size!==b.warnings.length||b.warnings.some(w=>!['SOURCE_EVENTS_UNAVAILABLE','SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE'].includes(w))||canonical([...b.warnings].sort())!==canonical(b.warnings)||b.status!==(b.warnings.length?'partial':'complete'))fail()
 for(const s of b.subjects){if(!exact(s,'runId subjectId canonicalId subjectRevision name primary')||!id(s.runId)||!id(s.subjectId)||!positive(s.subjectRevision)||typeof s.name!=='string'||!s.name.trim()||s.name.length>160||!/^reader-proof:[a-f0-9]{64}$/.test(s.canonicalId)||!exact(s.primary,'paragraphId paragraphRevision offset quote visibleParagraphId visibleHash')||!id(s.primary.paragraphId)||s.primary.paragraphRevision!==1||!Number.isSafeInteger(s.primary.offset)||s.primary.offset<0||s.primary.quote!==s.name||!id(s.primary.visibleParagraphId)||!hash(s.primary.visibleHash))fail()}
 // Derived views are bounded here, then replayed against exact source objects below.
 if(eventCanonical(b).length>12_000_000)fail()
}
export function acceptedBasisReferences(run:WritingRun){
 const b=run.acceptedBasis;if(!b)return []
 const refs:{id:string;kind:DomainKind;revision?:number}[]=[]
 for(const o of b.origins)refs.push({id:o.runId,kind:'settings',revision:o.runRevision})
 for(const p of b.prefix)for(const id of [p.paragraphId,p.sourceParagraphId,...p.lineage])refs.push({id,kind:'paragraph',revision:1})
 for(const s of b.subjects){refs.push({id:s.subjectId,kind:'settings',revision:s.subjectRevision},{id:s.primary.paragraphId,kind:'paragraph',revision:1})}
 return refs
}
function paragraph(lookup:BasisLookup,id:string):SourceParagraph {
 const row=lookup(id,1),p=row?.value as SourceParagraph|undefined
 if(row?.kind!=='paragraph'||row.revision!==1||p?.id!==id)fail();return p
}
function rootParagraph(lookup:BasisLookup,id:string){
 let p=paragraph(lookup,id);const first=p,lineage:string[]=[],seen=new Set([id])
 while(p.forkOf){if(lineage.length>=100||seen.has(p.forkOf.paragraphId))fail();const q=paragraph(lookup,p.forkOf.paragraphId),offset=p.forkOf.offset;if(p.chapterId!==q.chapterId||offset<=0||offset>=q.text.length||unicodeOffset(q.text,offset)!==offset||p.text!==q.text.slice(0,offset))fail();lineage.push(q.id);seen.add(q.id);p=q}
 if(!p.text.startsWith(first.text))fail();return {first,root:p,lineage}
}
/** The sources are pinned revisions, including unavailable legacy origins. Never scan book tasks. */
export function replayAcceptedBasis(run:WritingRun,lookup:BasisLookup,rebuildingMigration=false){
 const b=run.acceptedBasis!;validateAcceptedBasis(b,run)
 const prefix:VisibleAcceptedParagraph[]=[],origins=new Map<string,{run:WritingRun;revision:number;through:number}>()
 for(const ref of b.prefix){const {first,root,lineage}=rootParagraph(lookup,ref.paragraphId)
  if(ref.sourceParagraphId!==root.id||ref.textHash!==first.textHash||ref.limit!==first.text.length||canonical(ref.lineage)!==canonical(lineage))fail()
  prefix.push({paragraphId:first.id,paragraphRevision:1,sourceParagraphId:root.id,text:first.text,textHash:first.textHash})
  if(!root.writingRunId)continue
  const pin=b.origins.find(o=>o.runId===root.writingRunId);if(!pin)fail()
  let o=origins.get(pin.runId)
  if(!o){const row=lookup(pin.runId,pin.runRevision),source=row?.value as WritingRun|undefined;if(row?.kind!=='settings'||row.revision!==pin.runRevision||source?.assetType!=='writing-run'||source.storyId!==run.storyId)fail();o={run:source,revision:pin.runRevision,through:0};origins.set(pin.runId,o)}
  const index=o.run.chapters.findIndex(c=>c.id===root.chapterId&&c.paragraphIds.includes(root.id));if(index<0||index>=o.run.acceptedCount)fail();o.through=Math.max(o.through,index+1)
 }
 if(canonical([...origins].map(([runId,o])=>({runId,runRevision:o.revision,through:o.through,eventsAvailable:!!o.run.eventSchema&&o.run.chapters.slice(0,o.through).every(c=>c.status==='complete'&&!!c.eventLedger)})))!==canonical(b.origins))fail()
 const inputs:(AcceptedSourceInput&{ledgerHash:string})[]=[],proofs:AcceptedChapterProof[]=[],references:AcceptedSourceReference[]=[]
 for(const [runId,o] of origins){if(!b.origins.find(r=>r.runId===runId)!.eventsAvailable)continue
  for(const [index,c] of o.run.chapters.slice(0,o.through).entries()){
   const parts=acceptedWritingParagraphs(c);if(parts.length!==c.paragraphIds.length)fail()
   for(const [i,id] of c.paragraphIds.entries()){const p=paragraph(lookup,id);if(p.chapterId!==c.id||p.writingRunId!==runId||p.forkOf||p.text!==parts[i]!.text)fail()}
   const proof=c.acceptedProof||{schema:'accepted-chapter-proof-v1' as const,style:'local-v1' as const,sourceHash:c.eventLedger!.sourceHash,segments:projectAcceptedText(c.text,'local-v1').segments.map((part,i)=>({...part,paragraphId:c.paragraphIds[i]!,paragraphRevision:1 as const,paragraphHash:paragraph(lookup,c.paragraphIds[i]!).textHash}))}
   validateAcceptedProof(proof,c.text,c.paragraphIds);proofs.push(proof)
   const ref=b.sources[references.length];if(!ref||ref.runId!==runId||ref.runRevision!==o.revision||ref.contentId!==c.id||ref.chapterIndex!==index+1||ref.sourceHash!==proof.sourceHash)fail()
   references.push(ref);inputs.push({runId,runRevision:o.revision,contentId:c.id,chapterIndex:index+1,text:c.text,style:'local-v1',ledger:c.eventLedger!,segments:proof.segments,ledgerHash:ref.ledgerHash})
  }
 }
 if(references.length!==b.sources.length)fail()
 const derived=deriveAcceptedEventsFromSources(inputs,prefix),warnings=new Set<string>()
 if(b.origins.some(o=>!o.eventsAvailable)||inputs.some(s=>s.ledger.status==='unavailable'))warnings.add('SOURCE_EVENTS_UNAVAILABLE')
 if(derived.dependencyClippedRefs.length)warnings.add('SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE')
 if(!rebuildingMigration&&(canonical(derived.events)!==canonical(b.events)||canonical([...warnings].sort())!==canonical(b.warnings)))fail()
 const identities:{binding:AcceptedIdentityBinding;storyId:string}[]=[]
 for(const binding of b.subjects){const o=origins.get(binding.runId),declared=o?.run.eventSubjects?.find(s=>s.id===binding.subjectId&&s.revision===binding.subjectRevision&&s.name===binding.name),row=lookup(binding.subjectId,binding.subjectRevision),character=row?.value as NovelCharacter|undefined
  if(!declared||row?.kind!=='settings'||row.revision!==binding.subjectRevision||character?.assetType!=='novel-character'||character.scope.storyId!==run.storyId||character.name!==binding.name||character.primary.revision!==1||character.primary.quote!==character.name)fail()
  const {root}=rootParagraph(lookup,character.primary.id),visible=prefix.find(p=>p.sourceParagraphId===root.id),primary=binding.primary
  if(!visible||canonical(primary)!==canonical({paragraphId:root.id,paragraphRevision:1,offset:character.primary.offset,quote:character.primary.quote,visibleParagraphId:visible.paragraphId,visibleHash:visible.textHash})||visible.text.slice(primary.offset,primary.offset+primary.quote.length)!==primary.quote)fail()
  identities.push({binding,storyId:run.storyId})
 }
 if(!rebuildingMigration&&canonical(foldVisibleAcceptedEvents(b.events,b.subjects))!==canonical(b.state))fail()
 return {prefix,inputs,proofs,identities,warnings:[...warnings].sort()}
}
/** After typed ID remapping, rederive only hashes/state; narrative strings never change. */
export async function rebaseAcceptedBasis(run:WritingRun,lookup:BasisLookup){
 const b=run.acceptedBasis;if(!b)return
 const {prefix,inputs,proofs,identities,warnings}=replayAcceptedBasis(run,lookup,true)
 for(const {binding,storyId} of identities){const p=binding.primary;binding.canonicalId='reader-proof:'+await sha256(eventCanonical({storyId,paragraphId:p.paragraphId,revision:1,offset:p.offset,quote:p.quote}))}
 for(const [index,s] of inputs.entries()){const ref=b.sources[index]!;ref.ledgerHash=await sha256(eventCanonical(s.ledger));ref.projectionHash=await sha256(eventCanonical(proofs[index]!));s.ledgerHash=ref.ledgerHash}
 b.events=deriveAcceptedEventsFromSources(inputs,prefix).events;b.state=foldVisibleAcceptedEvents(b.events,b.subjects);b.warnings=warnings;b.status=warnings.length?'partial':'complete';b.hash=await sha256(eventCanonical(acceptedBasisValue(b)))
}
export async function acceptedBasisSourceBytes(run:WritingRun,lookup:BasisLookup){
 if(!run.acceptedBasis)return
 const b=run.acceptedBasis,{prefix,inputs,proofs,identities}=replayAcceptedBasis(run,lookup)
 if(b.hash!==await sha256(eventCanonical(acceptedBasisValue(b))))fail()
 for(const p of prefix)if(p.textHash!==await sha256(p.text))fail()
 for(const [index,s] of inputs.entries()){const ref=b.sources[index]!,proof=proofs[index]!;await validateAcceptedProofBytes(proof,s.text);if(s.ledger.sourceHash!==await sha256(s.text)||ref.ledgerHash!==await sha256(eventCanonical(s.ledger))||ref.projectionHash!==await sha256(eventCanonical(proof)))fail();for(const part of proof.segments)if(paragraph(lookup,part.paragraphId).textHash!==part.paragraphHash)fail()}
 for(const {binding,storyId} of identities){const p=binding.primary;if(binding.canonicalId!=='reader-proof:'+await sha256(eventCanonical({storyId,paragraphId:p.paragraphId,revision:1,offset:p.offset,quote:p.quote})))fail()}
}
