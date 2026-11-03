import {canonical,ContractError,unicodeOffset} from '../domain/local-contract'
export interface VisibleComparisonParagraph {id:string;rootId:string;rootRevision:number;start:number;limit:number;text:string;chapter:string}
export interface ComparisonFact {identityProof:string;name:string;kind:string;text:string;confidence:'explicit'|'inferred';evidence:{id:string;offset:number;quote:string}[]}
export interface ComparisonEvent {proof:string;kind:string;text:string;frame:'past'|'present';certainty:'narrated'|'inferred';evidence:{id:string;offset:number;quote:string}[]}
export interface ComparisonLine {storyId:string;branchId:string|null;revision:number;name:string;paragraphs:VisibleComparisonParagraph[];facts:ComparisonFact[];events?:ComparisonEvent[];sourceWarnings?:string[];anchor:{paragraphId:string;offset:number}|null}
export interface ComparisonRow {kind:'same'|'different'|'left'|'right';left:VisibleComparisonParagraph|null;right:VisibleComparisonParagraph|null}
const deny=():never=>{throw new ContractError('LINE_COMPARISON','比较范围与已读原文不一致')}
function validate(line:ComparisonLine){
 if(!line.storyId||!Number.isSafeInteger(line.revision)||line.revision<1||!Array.isArray(line.paragraphs)||line.paragraphs.length>100000||!Array.isArray(line.facts)||line.facts.length>20000)deny()
 const ids=new Set<string>();let chars=0
 for(const p of line.paragraphs){if(!p.id||!p.rootId||!Number.isSafeInteger(p.rootRevision)||p.rootRevision<1||!Number.isSafeInteger(p.start)||!Number.isSafeInteger(p.limit)||p.start<0||p.limit<p.start||typeof p.text!=='string'||p.text.length!==p.limit-p.start||unicodeOffset(p.text,p.text.length)!==p.text.length||ids.has(p.id))deny();ids.add(p.id);chars+=p.text.length}
 if(chars>6000000||line.anchor===null&&line.paragraphs.length)deny()
 for(const f of line.facts)if(!f.identityProof||!f.name||!f.text||!['explicit','inferred'].includes(f.confidence)||!Array.isArray(f.evidence)||!f.evidence.length)deny()
 if(line.events!==undefined&&(!Array.isArray(line.events)||line.events.length>12000))deny()
 for(const e of line.events||[])if(!e.proof||!e.text||!['past','present'].includes(e.frame)||!['narrated','inferred'].includes(e.certainty)||!Array.isArray(e.evidence)||!e.evidence.length)deny()
}
const sameRoot=(a:VisibleComparisonParagraph,b:VisibleComparisonParagraph)=>a.rootId===b.rootId&&a.rootRevision===b.rootRevision
const same=(a:VisibleComparisonParagraph,b:VisibleComparisonParagraph)=>sameRoot(a,b)&&a.start===b.start&&a.limit===b.limit&&a.text===b.text
function tail(p:VisibleComparisonParagraph,start:number){return {...p,start,text:p.text.slice(start-p.start)}}
/** Both inputs are already clipped to their own saved reading anchors. No model or mutation. */
export function compareVisibleLines(left:ComparisonLine,right:ComparisonLine,leftOffset=0,rightOffset=0,pageSize=40){
 validate(left);validate(right)
 if(left.storyId!==right.storyId||left.branchId===right.branchId||![leftOffset,rightOffset].every(n=>Number.isSafeInteger(n)&&n>=0)||!Number.isSafeInteger(pageSize)||pageSize<1||pageSize>80)deny()
 let commonCount=0,lastCommon:{leftId:string;rightId:string;rootId:string;offset:number}|null=null,partial=false
 while(commonCount<left.paragraphs.length&&commonCount<right.paragraphs.length){const a=left.paragraphs[commonCount]!,b=right.paragraphs[commonCount]!
  if(!sameRoot(a,b)||a.start!==b.start)break
  const limit=Math.min(a.limit,b.limit),count=limit-a.start
  if(a.text.slice(0,count)!==b.text.slice(0,count))deny()
  lastCommon={leftId:a.id,rightId:b.id,rootId:a.rootId,offset:limit}
  if(a.limit!==b.limit){partial=true;break}commonCount++
 }
 let a=left.paragraphs.slice(commonCount),b=right.paragraphs.slice(commonCount)
 if(partial&&lastCommon){a=a[0]!.limit===lastCommon.offset?a.slice(1):[tail(a[0]!,lastCommon.offset),...a.slice(1)];b=b[0]!.limit===lastCommon.offset?b.slice(1):[tail(b[0]!,lastCommon.offset),...b.slice(1)]}
 if(leftOffset>a.length||rightOffset>b.length)deny()
 const aa=a.slice(leftOffset,leftOffset+pageSize),bb=b.slice(rightOffset,rightOffset+pageSize),stride=bb.length+1,grid=new Uint16Array((aa.length+1)*stride)
 for(let i=aa.length-1;i>=0;i--)for(let j=bb.length-1;j>=0;j--)grid[i*stride+j]=same(aa[i]!,bb[j]!)?grid[(i+1)*stride+j+1]!+1:Math.max(grid[(i+1)*stride+j]!,grid[i*stride+j+1]!)
 const rows:ComparisonRow[]=[],pendingA:VisibleComparisonParagraph[]=[],pendingB:VisibleComparisonParagraph[]=[]
 const flush=()=>{while(pendingA.length||pendingB.length){const l=pendingA.shift()||null,r=pendingB.shift()||null;rows.push({kind:l&&r?'different':l?'left':'right',left:l,right:r})}}
 let i=0,j=0;while(i<aa.length||j<bb.length){if(i<aa.length&&j<bb.length&&same(aa[i]!,bb[j]!)){flush();rows.push({kind:'same',left:aa[i++]!,right:bb[j++]!})}else if(i<aa.length&&(j===bb.length||grid[(i+1)*stride+j]!>=grid[i*stride+j+1]!))pendingA.push(aa[i++]!);else pendingB.push(bb[j++]!)}flush()
 const factKey=(f:ComparisonFact)=>canonical([f.identityProof,f.kind,f.text,f.confidence]),leftFacts=new Set(left.facts.map(factKey)),rightFacts=new Set(right.facts.map(factKey))
 const leftEvents=left.events||[],rightEvents=right.events||[],eventKey=(e:ComparisonEvent)=>canonical([e.proof,e.kind,e.text,e.frame,e.certainty]),le=new Set(leftEvents.map(eventKey)),re=new Set(rightEvents.map(eventKey))
 return {schema:'read-line-comparison-v1' as const,storyId:left.storyId,left:{name:left.name,branchId:left.branchId,revision:left.revision,anchor:left.anchor},right:{name:right.name,branchId:right.branchId,revision:right.revision,anchor:right.anchor},common:{paragraphCount:commonCount,last:lastCommon},rows,facts:{left:left.facts.filter(f=>!rightFacts.has(factKey(f))),right:right.facts.filter(f=>!leftFacts.has(factKey(f)))},events:{left:leftEvents.filter(e=>!re.has(eventKey(e))),right:rightEvents.filter(e=>!le.has(eventKey(e)))},timeline:{left:leftEvents,right:rightEvents},sourceWarnings:{left:left.sourceWarnings||[],right:right.sourceWarnings||[]},page:{leftOffset,rightOffset,pageSize,nextLeft:Math.min(a.length,leftOffset+aa.length),nextRight:Math.min(b.length,rightOffset+bb.length),hasMore:leftOffset+aa.length<a.length||rightOffset+bb.length<b.length}}
}
