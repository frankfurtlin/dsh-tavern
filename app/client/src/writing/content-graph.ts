import {canonical,ContractError,type Chapter,type Document} from '../domain/local-contract'
import type {LocalBranch} from './contract'
export interface GraphParagraphProof {id:string;rootId:string;chapterId:string;limit:number}
export interface GraphNode {id:string;chapterId:string;title:string;lineId:string|null;index:number;column:number;boundary:string;targets:Record<string,{paragraphId:string;offset:number}>}
/** Segment at exact paragraph/UTF16 fork boundaries, then share the actual content spans. */
export function contentGraph(storyId:string,original:Chapter[],branches:Pick<Document<LocalBranch>,'id'|'value'>[],proofs:Map<string,GraphParagraphProof>){
 const lines=[{id:storyId,lineId:null,chapters:original},...branches.map(b=>({id:b.id,lineId:b.id,chapters:b.value.chapters}))],cuts=new Map<string,Set<number>>()
 for(const line of lines)for(const chapter of line.chapters)for(const id of chapter.paragraphIds){const p=proofs.get(id);if(!p||p.id!==id||p.chapterId!==chapter.id||!Number.isSafeInteger(p.limit)||p.limit<0)throw new ContractError('GRAPH_SOURCE','故事线来源不一致，正文仍保留');if(!cuts.has(p.rootId))cuts.set(p.rootId,new Set([0]));cuts.get(p.rootId)!.add(p.limit)}
 const nodes=new Map<string,GraphNode>(),links=new Map<string,{from:string;to:string;lineId:string|null}>(),paths=new Map<string,string[]>()
 for(const line of lines){const path:string[]=[];let column=0
  for(const [index,chapter] of line.chapters.entries()){
   let spans:{rootId:string;start:number;end:number}[]=[],first:{paragraphId:string;offset:number}|null=null,boundary=''
   const emit=()=>{if(!spans.length||!first)return;const id=chapter.id+':'+canonical(spans);let node=nodes.get(id);if(!node){node={id,chapterId:chapter.id,title:chapter.title,lineId:line.lineId,index,column,boundary,targets:{}};nodes.set(id,node)}node.targets[line.id]=first;path.push(id);column++;spans=[];first=null;boundary=''}
   for(const [paragraphIndex,id] of chapter.paragraphIds.entries()){const p=proofs.get(id)!,ends=[...cuts.get(p.rootId)!].filter(n=>n>0&&n<=p.limit).sort((a,b)=>a-b);let start=0
    if(!p.limit){if(!first)first={paragraphId:id,offset:0};spans.push({rootId:p.rootId,start:0,end:0});continue}
    for(const end of ends){if(!first)first={paragraphId:id,offset:start};spans.push({rootId:p.rootId,start,end});if(end<p.limit||[...cuts.get(p.rootId)!].some(n=>n>end)){boundary='第 '+(index+1)+' 章 · 第 '+(paragraphIndex+1)+' 段'+(end<p.limit?'第 '+end+' 字后':'后');emit()}start=end}
   }
   emit()
  }
  for(let i=1;i<path.length;i++){const from=path[i-1]!,to=path[i]!,key=canonical([from,to]);if(!links.has(key))links.set(key,{from,to,lineId:line.lineId})}paths.set(line.id,path)
 }
 return {nodes:[...nodes.values()],edges:[...links.values()],paths}
}
