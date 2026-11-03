import {canonical,sha256,unicodeOffset,ContractError,type Paragraph,type ProgressAnchor,type Chapter} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {readingLine,progressKey} from './runtime'
import {charactersAt} from '../novel-characters/runtime'
import {characterChunk} from '../novel-characters/chunking'
import {compareVisibleLines,type ComparisonLine,type ComparisonFact,type ComparisonEvent} from './line-comparison'
import {collectAcceptedBasis} from './accepted-basis-source'
type ProvenParagraph=Paragraph&{forkOf?:{paragraphId:string;offset:number}}
const deny=():never=>{throw new ContractError('LINE_COMPARISON_SOURCE','已读来源已变化，请重新比较')}
/** Read-only, private-vault snapshots. Every paragraph and person is clipped independently. */
export async function readComparisonLine(vault:LocalVault,storyId:string,branchId:string|null,signal:AbortSignal):Promise<ComparisonLine>{
 signal.throwIfAborted();const {story,branch,line}=await readingLine(vault,storyId,branchId),row=branch||story,progress=await vault.read<ProgressAnchor>(progressKey(storyId,branchId)),anchor=progress?.value
 const base={storyId,branchId,revision:row.revision,name:branch?.value.name||'原文',paragraphs:[],facts:[],anchor:null} satisfies ComparisonLine
 if(!anchor)return base
 const end=line.value.paragraphIds.indexOf(anchor.paragraphId)
 if(anchor.storyId!==storyId||anchor.branchId!==branchId||end<0||end>99999)deny()
 const cache=new Map<string,ProvenParagraph>(),roots=new Map<string,{id:string;paragraph:ProvenParagraph}>()
 const paragraph=async(id:string)=>{signal.throwIfAborted();const known=cache.get(id);if(known)return known;const p=await vault.read<ProvenParagraph>(id,1);if(p?.kind!=='paragraph'||p.revision!==1||p.value.id!==id||await sha256(p.value.text)!==p.value.textHash)deny();cache.set(id,p!.value);return p!.value}
 const rootOf=async(id:string)=>{const known=roots.get(id);if(known)return known;let p=await paragraph(id);const seen=new Set<string>();for(let n=0;p.forkOf;n++){if(n>=100||seen.has(p.id))deny();seen.add(p.id);const original=await paragraph(p.forkOf.paragraphId),offset=p.forkOf.offset;if(original.chapterId!==p.chapterId||!Number.isSafeInteger(offset)||offset<=0||offset>=original.text.length||unicodeOffset(original.text,offset)!==offset||p.text!==original.text.slice(0,offset))deny();p=original}const result={id:p.id,paragraph:p};roots.set(id,result);return result}
 const parts:ComparisonLine['paragraphs']=[],visible=new Map<string,number>(),chapterNames=new Map(line.value.chapters.map(c=>[c.id,c.title]));let chars=0
 for(let from=0;from<=end;from+=64){const ids=line.value.paragraphIds.slice(from,Math.min(end+1,from+64));const rows=await Promise.all(ids.map(async id=>({p:await paragraph(id),root:await rootOf(id)})))
  for(const {p,root} of rows){const limit=p.id===anchor.paragraphId?anchor.offset:p.text.length;if(!Number.isSafeInteger(limit)||limit<0||limit>p.text.length||unicodeOffset(p.text,limit)!==limit)deny();const text=p.text.slice(0,limit);chars+=text.length;if(chars>6000000)deny();visible.set(root.id,Math.max(visible.get(root.id)||0,limit));if(!limit)continue;const chapter=chapterNames.get(p.chapterId)||'';parts.push({id:p.id,rootId:root.id,rootRevision:1,start:0,limit,text,chapter})}
 }
 const facts:ComparisonFact[]=[],chunk=await characterChunk(vault,storyId,branchId,anchor)
 if(chunk)for(const person of await charactersAt(vault,chunk.scope)){const c=person.value,root=await rootOf(c.primary.id);if(c.primary.offset+c.primary.quote.length>(visible.get(root.id)||0)||root.paragraph.text.slice(c.primary.offset,c.primary.offset+c.primary.quote.length)!==c.primary.quote)continue;const identityProof=canonical([storyId,root.id,c.primary.revision,c.primary.offset,c.primary.quote])
  for(const f of c.facts){const evidence:ComparisonFact['evidence']=[];for(const e of f.evidence){const r=await rootOf(e.id),limit=visible.get(r.id)||0;if(e.offset+e.quote.length>limit||r.paragraph.text.slice(e.offset,e.offset+e.quote.length)!==e.quote)continue;const target=parts.find(p=>p.rootId===r.id&&p.limit>=e.offset+e.quote.length);if(target)evidence.push({id:target.id,offset:e.offset,quote:e.quote})}if(evidence.length)facts.push({identityProof,name:c.name,kind:f.kind,text:f.text,confidence:f.confidence,evidence})}
 }
 const events:ComparisonEvent[]=[],sourceWarnings:string[]=[],conditions:{id:string;revision:number}[]=[]
 if(parts.length<=10000){
  const inline=new Map<string,Paragraph>(),prefixChapters:Chapter[]=[],targets=new Map<string,string>()
  for(const part of parts){const p=cache.get(part.id)!;let id=p.id;if(part.limit<p.text.length){id='comparison:'+p.id;inline.set(id,{...p,id,text:part.text,textHash:await sha256(part.text),sourceEnd:p.sourceStart+part.limit,forkOf:{paragraphId:p.id,offset:part.limit}} as ProvenParagraph)}targets.set(id,p.id);let chapter=prefixChapters.at(-1);if(chapter?.id!==p.chapterId){chapter={id:p.chapterId,title:chapterNames.get(p.chapterId)||'',paragraphIds:[]};prefixChapters.push(chapter)}chapter.paragraphIds.push(id)}
  const {basis,conditions:pins}=await collectAcceptedBasis(vault,{storyId,anchor,parentRevision:row.revision,prefixChapters},inline);signal.throwIfAborted();conditions.push(...pins);sourceWarnings.push(...basis.warnings)
  for(const e of basis.events){const evidence=e.evidence.map(v=>({id:targets.get(v.paragraphId)!,offset:v.start,quote:v.quote}));if(evidence.some(v=>!v.id))deny();events.push({proof:canonical(e.ref),kind:e.entry.kind,text:evidence.map(v=>v.quote).join('\n'),frame:e.temporalFrame,certainty:e.entry.certainty,evidence})}
 }else sourceWarnings.push('EVENT_COMPARISON_LIMIT')
 signal.throwIfAborted();if(!vault.unlocked||(await vault.read(row.id))?.revision!==row.revision||(await vault.read(storyId))?.revision!==story.revision||(await vault.read(progress!.id))?.revision!==progress!.revision)deny()
 for(const pin of conditions)if((await vault.read(pin.id))?.revision!==pin.revision)deny()
 return {...base,paragraphs:parts,facts,events,sourceWarnings,anchor:{paragraphId:anchor.paragraphId,offset:anchor.offset}}
}
export async function compareReadingLines(vault:LocalVault,storyId:string,leftId:string|null,rightId:string|null,signal:AbortSignal,leftOffset=0,rightOffset=0){
 if(leftId===rightId)throw new ContractError('LINE_COMPARISON_SELECTION','请选择两条不同的故事线')
 const [left,right]=await Promise.all([readComparisonLine(vault,storyId,leftId,signal),readComparisonLine(vault,storyId,rightId,signal)]);signal.throwIfAborted();return compareVisibleLines(left,right,leftOffset,rightOffset)
}
