import type {Anchor} from './types'
import type {GenerationSnapshot,GenerationRequest} from './generation-types'
import type {SelectionEditScope} from './selection-edits'
export const selectionIdentity=(scope:SelectionEditScope)=>':selection:'+scope.revisionId+':'+scope.startAnchor.blockId+':'+scope.startAnchor.start+':'+scope.endAnchor.blockId+':'+scope.endAnchor.end
const keys=['bookId','branchId','revisionId','chapterId','blockId','start','end','offsetUnit','quoteHash'] as const
export const sameAnchor=(a:Anchor,b:Anchor)=>keys.every(k=>a[k]===b[k])
export function parseGenerationSelection(input:unknown,scope:Anchor):GenerationSnapshot['selection']{
 if(input===undefined)return undefined
 if(!input||typeof input!=='object')throw new Error('写作记录中的选区无效')
 const value=input as Record<string,unknown>
 if(!['expand','rewrite'].includes(String(value.purpose))||!['requireUnchanged','forkPinned'].includes(String(value.sourcePolicy)))throw new Error('写作记录中的选区无效')
 const point=(input:unknown)=>{
  if(!input||typeof input!=='object')throw new Error('写作记录中的选区无效')
  const a=input as Record<string,unknown>
  if(!keys.every(k=>k==='start'||k==='end'?Number.isSafeInteger(a[k])&&Number(a[k])>=0:typeof a[k]==='string')||a.start!==a.end||a.offsetUnit!=='utf16'||['bookId','branchId','revisionId','chapterId'].some(k=>a[k]!==scope[k as keyof Anchor]))throw new Error('写作记录中的选区无效')
  return Object.fromEntries(keys.map(k=>[k,a[k]])) as unknown as Anchor
 }
 const startAnchor=point(value.startAnchor),endAnchor=point(value.endAnchor)
 if(!sameAnchor(endAnchor,scope))throw new Error('写作记录中的选区终点无效')
 return {purpose:value.purpose as 'expand'|'rewrite',sourcePolicy:value.sourcePolicy as 'requireUnchanged'|'forkPinned',startAnchor,endAnchor}
}
export function selectionRequest(scope:SelectionEditScope,purpose:'expand'|'rewrite',instruction:string,profileId:string):GenerationRequest{
 return {protocol:'reader-prose-v1',originAnchor:{...scope.endAnchor},operation:'continue_from_anchor',chapterCount:1,chapterLength:'short',instruction,profileId,expectedHeadRevisionId:scope.headRevisionId,target:{mode:'new_child_branch',name:purpose==='expand'?'选区扩写':'替换写法'},selection:{purpose,startAnchor:{...scope.startAnchor},endAnchor:{...scope.endAnchor},sourcePolicy:scope.revisionId===scope.headRevisionId?'requireUnchanged':'forkPinned'}}
}
export const matchesSelection=(value:GenerationSnapshot,scope:SelectionEditScope)=>!!value.selection&&sameAnchor(value.selection.startAnchor,scope.startAnchor)&&sameAnchor(value.selection.endAnchor,scope.endAnchor)
