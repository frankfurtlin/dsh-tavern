/** Public server display projection. Full source verification remains at storage. */
import type {Anchor} from '../api/types'
import {ContractError,canonical} from '../domain/local-contract'
import {scalarText,validateNarrativeEvent} from './events'
import type {EventRef,VisibleAcceptedEvent} from './accepted-events'
export interface AcceptedEventContext {
 schema:'accepted-event-context-v1';basisHash:string;scope:Anchor;
 events:Pick<VisibleAcceptedEvent,'ref'|'entry'|'dependencies'|'frameRef'|'temporalFrame'|'evidence'>[];
 subjects:{runId:string;subjectId:string;canonicalId:string;name:string;primary:{blockId:string;start:number;quote:string}}[];
 status:'complete'|'partial';warnings:string[];sourceCount:number;originCount:number;prefixCount:number;
 paragraphChapters:{paragraphId:string;chapterId:string}[];
}
const exact=(v:unknown,keys:string):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(' ')===keys.split(' ').sort().join(' ')
const integer=(v:unknown,max=6_000_000):v is number=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<=max
const id=(v:unknown):v is string=>scalarText(v,160)
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)
function fail():never{throw new ContractError('WRITING_ACCEPTED_CONTEXT','续写依据暂时无法核对，正文仍保留')}
function ref(v:unknown):asserts v is EventRef{if(!exact(v,'runId contentId localEventId')||!id(v.runId)||!id(v.contentId)||typeof v.localEventId!=='string'||!/^c([1-9][0-9]{0,2}):e([1-9][0-9]?)$/.test(v.localEventId))fail()}
export function parseAcceptedEventContext(value:unknown):AcceptedEventContext{
 if(!exact(value,'schema basisHash scope events subjects status warnings sourceCount originCount prefixCount paragraphChapters')||value.schema!=='accepted-event-context-v1'||!hash(value.basisHash)||!['complete','partial'].includes(String(value.status)))fail()
 const scope=value.scope
 if(!exact(scope,'bookId branchId revisionId chapterId blockId start end offsetUnit quoteHash')||!['bookId','branchId','revisionId','chapterId','blockId'].every(k=>id(scope[k]))||scope.offsetUnit!=='utf16'||!hash(scope.quoteHash)||!integer(scope.start)||!integer(scope.end)||scope.start>scope.end)fail()
 for(const [key,max] of [['sourceCount',1000],['originCount',1000],['prefixCount',10000]] as const)if(!integer(value[key],max))fail()
 if(Number(value.originCount)>Number(value.sourceCount)||!Array.isArray(value.events)||value.events.length>12000||!Array.isArray(value.subjects)||value.subjects.length>2000||!Array.isArray(value.warnings)||value.warnings.length>3)fail()
 if(value.warnings.some(w=>!['SOURCE_EVENTS_UNAVAILABLE','SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE','SOURCE_IDENTITY_UNPROVEN'].includes(w))||canonical(value.warnings)!==canonical([...new Set(value.warnings)].sort())||value.status!==(value.warnings.length?'partial':'complete'))fail()
 const known=new Set<string>()
 for(const event of value.events){
  if(!exact(event,'ref entry dependencies frameRef temporalFrame evidence'))fail();ref(event.ref);validateNarrativeEvent(event.entry)
  if(event.ref.localEventId!==event.entry.id||!['past','present'].includes(String(event.temporalFrame))||!Array.isArray(event.dependencies)||event.dependencies.length>12||!Array.isArray(event.evidence)||!event.evidence.length||event.evidence.length>10000)fail()
  const key=canonical(event.ref);if(known.has(key))fail()
  for(const dependency of event.dependencies){ref(dependency);if(!known.has(canonical(dependency)))fail()}
  if(new Set(event.dependencies.map(d=>canonical(d))).size!==event.dependencies.length)fail()
  if(event.frameRef!==null){ref(event.frameRef);if(!known.has(canonical(event.frameRef)))fail()}
  for(const evidence of event.evidence)if(!exact(evidence,'paragraphId paragraphRevision start end quote')||!id(evidence.paragraphId)||evidence.paragraphRevision!==1||!integer(evidence.start)||!integer(evidence.end)||evidence.end<=evidence.start||!scalarText(evidence.quote,1200)||evidence.end-evidence.start!==evidence.quote.length)fail()
  known.add(key)
 }
 for(const subject of value.subjects){if(!exact(subject,'runId subjectId canonicalId name primary')||!id(subject.runId)||!id(subject.subjectId)||!id(subject.name)||typeof subject.canonicalId!=='string'||!/^reader-proof:[a-f0-9]{64}$/.test(subject.canonicalId)||!exact(subject.primary,'blockId start quote')||!id(subject.primary.blockId)||!integer(subject.primary.start)||!id(subject.primary.quote))fail()}
 if(!Array.isArray(value.paragraphChapters)||value.paragraphChapters.length>10000||value.paragraphChapters.some(p=>!exact(p,'paragraphId chapterId')||!id(p.paragraphId)||!id(p.chapterId))||new Set(value.paragraphChapters.map(p=>p.paragraphId)).size!==value.paragraphChapters.length)fail()
 const chapters=new Map(value.paragraphChapters.map(p=>[p.paragraphId,p.chapterId]))
 if(chapters.get(scope.blockId)!==scope.chapterId||value.events.some(event=>event.evidence.some((proof:{paragraphId:string})=>!chapters.has(proof.paragraphId))))fail()
 if(canonical(value).length>12_000_000)fail()
 return JSON.parse(canonical(value)) as AcceptedEventContext
}
