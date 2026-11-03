import type {Anchor,Block} from './types'
import {quoteHash} from './anchors'
export interface SelectionEditScope {bookId:string;branchId:string;revisionId:string;headRevisionId:string;startAnchor:Anchor;endAnchor:Anchor;originalText:string;chapterId:string}
export interface SelectionEditResult {branchId:string;revisionId:string;version:number;sequence:number;baseRevisionId:string;restored:boolean}
export interface RevisionHistory {branchId:string;headRevisionId:string;items:{revisionId:string;sequence:number;createdAt:string;kind:string|null;restored:boolean;startAnchor:Anchor|null;endAnchor:Anchor|null}[];nextBeforeSequence:number|null}
export interface SelectionDetail {bookId:string;branchId:string;revisionId:string;sequence:number;sourceHeadRevisionId:string;targetHeadRevisionId:string;baseRevisionId:string;startAnchor:Anchor;endAnchor:Anchor;originalText:string;replacementText:string|null}
export interface SelectionRevisionAction {action:'select_candidate'|'restore_original';candidateRevisionId?:string;expectedSourceHeadRevisionId:string;expectedTargetHeadRevisionId:string;sourcePolicy:'requireUnchanged'|'forkPinned'}
export async function selectionEditScope(block:Block,headRevisionId:string,selection:Anchor|null):Promise<SelectionEditScope>{
 const source=block.endAnchor
 if(selection&&(['bookId','branchId','revisionId','chapterId','blockId','offsetUnit'] as const).some(k=>selection[k]!==source[k]))throw new Error('所选文字不属于这个版本')
 const start=selection?.start??0,end=selection?.end??block.text.length
 if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<start||end>block.text.length)throw new Error('选区位置无效')
 const [left,right]=await Promise.all([quoteHash(block.text,source.revisionId,block.id,start),quoteHash(block.text,source.revisionId,block.id,end)])
 if(selection&&await quoteHash(block.text,source.revisionId,block.id,start,end)!==selection.quoteHash)throw new Error('选区内容校验失败')
 return {bookId:source.bookId,branchId:source.branchId,revisionId:source.revisionId,headRevisionId,chapterId:source.chapterId,startAnchor:{...source,start,end:start,quoteHash:left},endAnchor:{...source,start:end,end,quoteHash:right},originalText:block.text.slice(start,end)}
}
