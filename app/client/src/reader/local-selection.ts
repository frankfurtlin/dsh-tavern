import {ContractError,unicodeOffset,type Document,type Paragraph} from '../domain/local-contract'
import type {TextSelection} from '../writing/edits-contract'

/** Exact DOM offsets, including combining marks; never widen a bad selection. */
export function localTextSelection(root:HTMLElement,selection:Selection|null,paragraphs:Document<Paragraph>[]):TextSelection|null{
 if(!selection||selection.isCollapsed||selection.rangeCount===0)return null
 const deny=():never=>{throw new ContractError('WRITING_EDIT_RANGE','选区无法定位，请重新选择完整文字')}
 if(selection.rangeCount!==1)deny()
 const range=selection.getRangeAt(0),point=(node:Node,offset:number)=>{
  const element=(node.nodeType===Node.ELEMENT_NODE?node as Element:node.parentElement)?.closest('.final-reader-paragraph'),row=paragraphs.find(p=>p.id===element?.id)
  if(!element||!root.contains(element)||!row||element.textContent!==row.value.text)return deny()
  const prefix=document.createRange();prefix.selectNodeContents(element);prefix.setEnd(node,offset)
  const length=prefix.toString().length
  if(length>row.value.text.length||unicodeOffset(row.value.text,length)!==length)deny()
  return {paragraphId:row.id,offset:length}
 }
 const start=point(range.startContainer,range.startOffset),end=point(range.endContainer,range.endOffset),a=paragraphs.findIndex(p=>p.id===start.paragraphId),b=paragraphs.findIndex(p=>p.id===end.paragraphId)
 if(a>b||a===b&&start.offset>=end.offset||paragraphs[a]?.value.chapterId!==paragraphs[b]?.value.chapterId||b-a>=1000)deny()
 return {start,end}
}
