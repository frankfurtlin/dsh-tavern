import {canonical,ContractError,unicodeOffset,type Chapter,type ProgressAnchor,type PromptMessage,type ProviderSnapshot} from '../domain/local-contract'
import type {Timeouts} from '../provider-adapters/text'
import type {ProsePresentation} from './prose'
import {writingPresentation,validateWritingPurpose,type WritingPurpose} from './purpose'
import {assessWritingQuality,type WritingQuality} from './quality'
import {validatePreferenceSnapshot,type PreferenceSnapshot} from './planning-contract'
import {eventSchema,validateEvents,foldEvents,type EventLedger} from './events'
import {validateAcceptedProof,type AcceptedChapterProof} from './accepted-proof'
import {validateAcceptedBasis,type AcceptedBasis} from './accepted-basis'
export interface LocalBranch {
  storyId:string;name:string;archived:boolean;parentBranchId:string|null;parentRevision:number;storyRevision:number;
  fork:ProgressAnchor;prefixChapters:Chapter[];chapters:Chapter[];paragraphIds:string[];createdAt:string;
  edition?:{editId:string;editRevision:number;variantId:string;paragraphId:string|null};
}
export interface WritingChapter {
  id:string;title:string;paragraphIds:string[];text:string;status:'queued'|'running'|'complete'|'partial'|'failed'|'unknown';
  attemptId:string|null;request:PromptMessage[];providerRequestId:string|null;finishReason:string|null;
  usage:{input:number|null;output:number|null};warnings:string[];
  raw?:string;presentation?:Omit<ProsePresentation,'text'>;quality?:WritingQuality;
  eventLedger?:EventLedger;
  acceptedProof?:AcceptedChapterProof;
  originalStatus?:WritingChapter['status'];
}
export interface WritingRun {
  assetType:'writing-run';storyId:string;storyRevision:number;parentBranchId:string|null;parentRevision:number;anchor:ProgressAnchor;
  prefixChapters:Chapter[];prefixParagraphId:string|null;targetBranchId:string;targetRevision:number;append:boolean;
  direction:string;chapterCount:number;length:number;style:string;forbidden:string;authorNote:string;
  configSnapshot:ProviderSnapshot;timeouts:Timeouts;dailyCalls:number;chapters:WritingChapter[];
  status:'queued'|'running'|'completed'|'failed'|'cancelled'|'unknown';ownerId:string|null;leaseUntil:string|null;
  acceptedCount:number;acceptedRevision:number;historical:boolean;createdAt:string;updatedAt:string;error:string|null;
  purpose?:WritingPurpose;
  template?:string;preferenceSnapshot?:PreferenceSnapshot;
  originalStatus?:WritingRun['status'];
  eventSchema?:typeof eventSchema;eventPrompt?:string;eventSubjects?:{id:string;revision:number;name:string}[];
  acceptedBasis?:AcceptedBasis;
  intent?:{schema:'writing-intent-v2';id:string;requestHash:string};
}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const str=(v:unknown,max:number)=>typeof v==='string'&&v.length<=max
const id=(v:unknown)=>str(v,160)&&!!v
const int=(v:unknown)=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0
const ids=(v:unknown)=>Array.isArray(v)&&v.every(id)&&new Set(v).size===v.length
const date=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v))
function anchor(v:unknown){return record(v)&&id(v.storyId)&&(v.branchId===null||id(v.branchId))&&id(v.paragraphId)&&int(v.offset)}
export function validateChapters(v:unknown,allowEmpty=false):asserts v is Chapter[]{
  if(!Array.isArray(v)||!allowEmpty&&!v.length||v.length>10000||v.some(c=>!record(c)||!id(c.id)||!str(c.title,1000)||!ids(c.paragraphIds))||new Set(v.map(c=>c.id)).size!==v.length||new Set(v.flatMap(c=>c.paragraphIds)).size!==v.flatMap(c=>c.paragraphIds).length)throw new ContractError('WRITING_CHAPTERS','故事线目录格式无效')
}
export function validateBranch(v:unknown){
  if(!record(v)||!id(v.storyId)||!str(v.name,200)||!(v.name as string).trim()||typeof v.archived!=='boolean'||!(v.parentBranchId===null||id(v.parentBranchId))||!int(v.parentRevision)||!int(v.storyRevision)||!v.storyRevision||!anchor(v.fork)||!date(v.createdAt))throw new ContractError('BRANCH_INVALID','故事线格式无效')
  if((v.fork as ProgressAnchor).storyId!==v.storyId||(v.fork as ProgressAnchor).branchId!==v.parentBranchId||v.parentBranchId===null&&v.parentRevision!==v.storyRevision||v.parentBranchId!==null&&!v.parentRevision)throw new ContractError('BRANCH_INVALID','故事线来源格式无效')
  validateChapters(v.prefixChapters,true);validateChapters(v.chapters)
  if(v.edition!==undefined&&(!record(v.edition)||!id(v.edition.editId)||!String(v.edition.editId).startsWith('writing-edit:')||!int(v.edition.editRevision)||!v.edition.editRevision||!id(v.edition.variantId)||!(v.edition.paragraphId===null||id(v.edition.paragraphId))))throw new ContractError('BRANCH_INVALID','编辑故事线来源格式无效')
  if(!ids(v.paragraphIds)||canonical(v.chapters.flatMap(c=>c.paragraphIds))!==canonical(v.paragraphIds)||canonical(v.chapters.slice(0,v.prefixChapters.length))!==canonical(v.prefixChapters))throw new ContractError('BRANCH_INVALID','共享前文和目录不一致')
}
export function validateWritingRun(v:unknown,documentId?:string){
  const bad=()=>{throw new ContractError('WRITING_INVALID','写作记录格式无效，原数据保留')}
  if(!record(v)||v.assetType!=='writing-run'||documentId!==undefined&&!documentId.startsWith('writing-run:')||!id(v.storyId)||!int(v.storyRevision)||!v.storyRevision||!(v.parentBranchId===null||id(v.parentBranchId))||!int(v.parentRevision)||!v.parentRevision||!anchor(v.anchor)||!id(v.targetBranchId)||!int(v.targetRevision)||typeof v.append!=='boolean'||!str(v.direction,20000)||!int(v.chapterCount)||(v.chapterCount as number)<1||(v.chapterCount as number)>10||!int(v.length)||(v.length as number)<100||(v.length as number)>10000||!str(v.style,10000)||!str(v.forbidden,10000)||!str(v.authorNote,20000)||!record(v.configSnapshot)||!record(v.timeouts)||!int(v.dailyCalls)||(v.dailyCalls as number)<1||(v.dailyCalls as number)>10000||!Array.isArray(v.chapters)||v.chapters.length!==v.chapterCount||!['queued','running','completed','failed','cancelled','unknown'].includes(String(v.status))||!(v.ownerId===null||id(v.ownerId))||!(v.leaseUntil===null||date(v.leaseUntil))||!int(v.acceptedCount)||(v.acceptedCount as number)>v.chapters.length||!int(v.acceptedRevision)||typeof v.historical!=='boolean'||!date(v.createdAt)||!date(v.updatedAt)||!(v.error===null||str(v.error,2000))||!(v.prefixParagraphId===null||id(v.prefixParagraphId)))bad()
  const run=v as unknown as WritingRun,config=run.configSnapshot
  if(run.intent!==undefined&&(!record(run.intent)||Object.keys(run.intent).length!==3||run.intent.schema!=='writing-intent-v2'||!str(run.intent.id,500)||!run.intent.id||!/^[a-f0-9]{64}$/.test(run.intent.requestHash)))bad()
  if(run.originalStatus!==undefined&&(!run.historical||!['queued','running','completed','failed','cancelled','unknown'].includes(run.originalStatus)||run.status!==(run.originalStatus==='queued'||run.originalStatus==='running'?'unknown':run.originalStatus)))bad()
  for(const c of run.chapters)if(c.originalStatus!==undefined&&(!run.historical||!['queued','running','complete','partial','failed','unknown'].includes(c.originalStatus)||c.status!==(c.originalStatus==='running'?(c.text?'partial':'unknown'):c.originalStatus)))bad()
  if(run.template!==undefined&&!str(run.template,20000))bad()
  if(run.eventSchema!==undefined){if(run.eventSchema!==eventSchema||!str(run.eventPrompt,10000)||!run.eventPrompt?.startsWith('events-contract-v1\n')||run.purpose||!Array.isArray(run.eventSubjects)||run.eventSubjects.length>200||run.eventSubjects.some(s=>!record(s)||Object.keys(s).length!==3||!id(s.id)||!int(s.revision)||!s.revision||!str(s.name,160)||!s.name.trim())||new Set(run.eventSubjects.map(s=>s.id)).size!==run.eventSubjects.length)bad()}else if(run.eventSubjects!==undefined||run.eventPrompt!==undefined||run.chapters.some(c=>c.eventLedger!==undefined))bad()
  const priorIds=new Set<string>(),subjects=new Set(run.eventSubjects?.map(s=>s.id))
  if(run.acceptedBasis!==undefined)validateAcceptedBasis(run.acceptedBasis,run)
  for(const [index,c] of run.chapters.entries())if(c.eventLedger){if(c.status!=='complete'&&c.status!=='partial')bad();validateEvents(c.eventLedger,c.text,{subjects,priorIds,chapterIndex:index+1});for(const e of c.eventLedger.events)priorIds.add(e.id)}
  foldEvents(run.chapters.filter(c=>c.eventLedger).map(c=>c.eventLedger!))
  if(run.preferenceSnapshot!==undefined)validatePreferenceSnapshot(run.preferenceSnapshot,run.storyId,{length:run.length,style:run.style,forbidden:run.forbidden,authorNote:run.authorNote,template:run.template??''})
  if(run.purpose!==undefined){validateWritingPurpose(run.purpose);if(run.chapterCount!==1||run.append||run.acceptedCount)bad()}
  if(run.anchor.storyId!==run.storyId||run.anchor.branchId!==run.parentBranchId||run.parentBranchId===null&&run.parentRevision!==run.storyRevision||run.append&&(run.parentBranchId!==run.targetBranchId||run.targetRevision!==run.parentRevision||!!run.direction.trim())||!run.append&&run.targetRevision!==0)bad()
  if(!id(config.id)||!int(config.revision)||!config.revision||!['chat-completions','responses','anthropic','gemini'].includes(config.protocol)||!str(config.baseUrl,2000)||!id(config.modelId)||!str(config.key,16000)||!int(config.contextLimit)||!int(config.outputLimit)||config.outputLimit<1||config.contextLimit<=config.outputLimit||Object.values(run.timeouts).length!==3||['firstPacketMs','idleMs','totalMs'].some(k=>{const n=run.timeouts[k as keyof Timeouts];return !int(n)||n<100||n>3600000}))bad()
  validateChapters(run.prefixChapters,true)
  if(new Set(run.chapters.map(c=>c.id)).size!==run.chapters.length)bad()
  for(const c of run.chapters){if(!id(c.id)||!str(c.title,1000)||!ids(c.paragraphIds)||!str(c.text,1_000_000)||!['queued','running','complete','partial','failed','unknown'].includes(c.status)||!(c.attemptId===null||id(c.attemptId))||!(c.providerRequestId===null||str(c.providerRequestId,1000))||!(c.finishReason===null||str(c.finishReason,100))||!Array.isArray(c.request)||c.request.some(m=>!record(m)||!['system','user','assistant'].includes(m.role)||!str(m.content,1_000_000))||!record(c.usage)||[c.usage.input,c.usage.output].some(n=>n!==null&&!int(n))||!Array.isArray(c.warnings)||c.warnings.length>16||c.warnings.some(w=>!str(w,1000)))bad();if(c.status==='queued'&&(c.attemptId||c.text||c.request.length)||c.status!=='queued'&&!c.attemptId||c.status==='complete'&&(!c.text.trim()||!['stop','end_turn'].includes(c.finishReason||'')))bad()}
  for(const c of run.chapters){if(c.raw!==undefined){if(!str(c.raw,1_000_000))bad();const {text,...presentation}=writingPresentation(c.raw,run.purpose);if(text!==c.text||canonical(presentation)!==canonical(c.presentation)||c.status==='complete'&&presentation.pending)bad()}else if(c.presentation!==undefined||c.quality!==undefined)bad();if(c.quality!==undefined&&canonical(c.quality)!==canonical(assessWritingQuality(c.text,c.raw!,c.finishReason,run.length)))bad()}
  if(config.key&&run.chapters.some(c=>[c.raw??'',c.text,c.title,c.providerRequestId??'',c.finishReason??''].some(text=>text.includes(config.key))))throw new ContractError('WRITING_SECRET_ECHO','供应商回显凭据不能进入正文或请求历史，原数据保留')
  if(run.acceptedCount>completePrefix(run)||run.acceptedCount>0&&!run.acceptedRevision)bad()
  for(const [index,c] of run.chapters.entries())if(c.acceptedProof!==undefined){if(!run.eventSchema||index>=run.acceptedCount||c.status!=='complete')bad();validateAcceptedProof(c.acceptedProof,c.text,c.paragraphIds)}
}
export function completePrefix(run:WritingRun){let n=0;for(const chapter of run.chapters){if(chapter.status!=='complete')break;n++}return n}
/** Returns the exact inherited prefix; a cut inside a paragraph uses a new immutable fragment ID. */
export function prefixAt(chapters:Chapter[],paragraphId:string,offset:number,text:string,fragmentId:string|null):Chapter[]{
  const result:Chapter[]=[];let found=false
  const cut=unicodeOffset(text,offset)
  if(cut!==offset||offset<0||offset>text.length)throw new ContractError('WRITING_ANCHOR','写作位置必须落在完整字符之间')
  for(const chapter of chapters){const index=chapter.paragraphIds.indexOf(paragraphId);if(index<0){if(!found)result.push({...chapter,paragraphIds:[...chapter.paragraphIds]});continue}found=true;const tail=cut===text.length?[paragraphId]:cut===0?[]:fragmentId?[fragmentId]:(()=>{throw new ContractError('WRITING_ANCHOR','段内分叉缺少前文片段')})();const paragraphIds=[...chapter.paragraphIds.slice(0,index),...tail];if(paragraphIds.length)result.push({...chapter,paragraphIds});break}
  if(!found)throw new ContractError('WRITING_ANCHOR','写作位置不属于这条故事线')
  return result
}
/** The graph draws shared chapter nodes once. Branches only add chapters after their inherited prefix. */
export function branchGraph(storyId:string,chapters:Chapter[],branches:{id:string;value:LocalBranch}[]){
  const nodes=new Map<string,{id:string;title:string;chapterId:string;lineId:string|null;index:number}>(),edges:{from:string;to:string;lineId:string|null}[]=[],keys=new Map<string,string[]>()
  const linked=new Set<string>()
  const add=(lineId:string|null,all:Chapter[],prefix:number)=>{const path:string[]=[];for(let i=0;i<all.length;i++){const chapter=all[i]!,key=chapter.id+':'+chapter.paragraphIds.join(',');let node=nodes.get(key);if(!node){node={id:key,title:chapter.title,chapterId:chapter.id,lineId,index:i};nodes.set(key,node)}path.push(node.id);const edge=i>0?path[i-1]+'>'+node.id:null;if(edge&&!linked.has(edge)){linked.add(edge);edges.push({from:path[i-1]!,to:node.id,lineId:i>=prefix?lineId:null})}}keys.set(lineId||storyId,path)}
  add(null,chapters,0);for(const branch of branches)add(branch.id,branch.value.chapters,branch.value.prefixChapters.length)
  return {nodes:[...nodes.values()],edges,paths:keys}
}
