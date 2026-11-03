import {sha256,unicodeOffset,ContractError,type Paragraph,type Chapter} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {GraphParagraphProof} from './content-graph'
type Source=Paragraph&{forkOf?:{paragraphId:string;offset:number}}
export async function graphParagraphProofs(vault:LocalVault,chapters:Chapter[],signal:AbortSignal){
 const ids=[...new Set(chapters.flatMap(c=>c.paragraphIds))];if(ids.length>100000)throw new ContractError('GRAPH_LIMIT','图谱段落超过上限，仍可从目录阅读')
 const cache=new Map<string,Promise<Source>>(),proofs=new Map<string,GraphParagraphProof>();let chars=0
 const fail=():never=>{throw new ContractError('GRAPH_SOURCE','分叉来源已变化，正文仍保留')}
 const paragraph=(id:string):Promise<Source>=>{const known=cache.get(id);if(known)return known;const work=(async()=>{signal.throwIfAborted();const p=await vault.read<Source>(id,1);if(p?.kind!=='paragraph'||p.revision!==1||p.value.id!==id||await sha256(p.value.text)!==p.value.textHash)fail();chars+=p!.value.text.length;if(chars>12000000)throw new ContractError('GRAPH_LIMIT','图谱来源超过上限，仍可从目录阅读');return p!.value})();cache.set(id,work);return work}
 for(let from=0;from<ids.length;from+=64){await Promise.all(ids.slice(from,from+64).map(async id=>{const first=await paragraph(id);let p=first;const seen=new Set<string>();while(p.forkOf){signal.throwIfAborted();if(seen.size>=100||seen.has(p.id))fail();seen.add(p.id);const root=await paragraph(p.forkOf.paragraphId),offset=p.forkOf.offset;if(p.chapterId!==root.chapterId||offset<=0||offset>=root.text.length||unicodeOffset(root.text,offset)!==offset||p.text!==root.text.slice(0,offset))fail();p=root}proofs.set(id,{id,rootId:p.id,chapterId:first.chapterId,limit:first.text.length})}))}
 signal.throwIfAborted();return proofs
}
