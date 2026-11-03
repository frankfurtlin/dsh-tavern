import type {Anchor,Block,Bookmark} from './types'
import {quoteHash,selectedAnchor} from './anchors'
import {canonicalOffset,renderedOffset} from './reading-layout'

export interface HighlightRange {start:number;end:number}
export interface ReadingSelectionPoints {startAnchor:Anchor;endAnchor:Anchor;originalText:string}

// Only verified ranges from this exact source and revision may paint the text.
export async function blockHighlights(block:Block,bookId:string,marks:Bookmark[]):Promise<HighlightRange[]>{
  const scope=block.endAnchor,ranges:HighlightRange[]=[]
  for(const mark of marks){
    if(mark.status==='deleted_original')continue
    const at=mark.anchor
    if(at.bookId!==bookId||at.branchId!==scope.branchId||at.revisionId!==scope.revisionId||at.chapterId!==scope.chapterId||at.blockId!==block.id||at.offsetUnit!=='utf16'||at.start>=at.end||!Number.isInteger(at.start)||!Number.isInteger(at.end))continue
    try{if(await quoteHash(block.text,at.revisionId,block.id,at.start,at.end)===at.quoteHash)ranges.push({start:at.start,end:at.end})}catch{/* A stale or malformed mark cannot change the source text. */}
  }
  const merged:HighlightRange[]=[]
  for(const range of ranges.sort((a,b)=>a.start-b.start||a.end-b.end)){
    const last=merged.at(-1)
    if(last&&range.start<=last.end)last.end=Math.max(last.end,range.end)
    else merged.push({...range})
  }
  return merged
}

export function paintHighlights(element:HTMLElement,canonical:string,ranges:HighlightRange[]){
  const document=element.ownerDocument,nodes:Text[]=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT)
  while(walker.nextNode())nodes.push(walker.currentNode as Text)
  const text=nodes.map(node=>node.data).join('')
  // Validate all offsets before mutating any HTML; never approximate a different text.
  const rendered=ranges.map(range=>({start:renderedOffset(canonical,text,range.start),end:renderedOffset(canonical,text,range.end)}))
  let offset=0
  for(const node of nodes){
    const start=offset,end=start+node.length;offset=end
    const local=rendered.filter(range=>range.start<end&&range.end>start)
    if(!local.length)continue
    const fragment=document.createDocumentFragment();let cursor=0
    for(const range of local){
      const left=Math.max(0,range.start-start),right=Math.min(node.length,range.end-start)
      if(left>cursor)fragment.append(document.createTextNode(node.data.slice(cursor,left)))
      const mark=document.createElement('mark');mark.className='reading-highlight';mark.setAttribute('data-reader-highlight','true');mark.textContent=node.data.slice(left,right);fragment.append(mark);cursor=right
    }
    if(cursor<node.length)fragment.append(document.createTextNode(node.data.slice(cursor)))
    node.replaceWith(fragment)
  }
}

export async function readingSelection(root:HTMLElement,selection:Selection|null,blocks:Block[]):Promise<Anchor|null>{
  if(!selection||selection.isCollapsed||selection.rangeCount!==1)return null
  const range=selection.getRangeAt(0)
  const content=(node:Node)=>{const element=node.nodeType===1?node as Element:node.parentElement;return element?.closest<HTMLElement>('[data-reader-content]')}
  const first=content(range.startContainer),last=content(range.endContainer)
  if(!first||first!==last||!root.contains(first)||!first.contains(range.startContainer)||!first.contains(range.endContainer))throw new Error('请在同一段正文里选择文字')
  const paragraph=first.closest<HTMLElement>('[data-reading-block]'),block=blocks.find(block=>block.id===paragraph?.dataset.readingBlock)
  if(!block)throw new Error('所选文字不属于当前正文')
  const text=first.textContent||'',before=range.cloneRange();before.selectNodeContents(first);before.setEnd(range.startContainer,range.startOffset)
  const start=canonicalOffset(block.text,text,before.toString().length)
  before.setEnd(range.endContainer,range.endOffset)
  const end=canonicalOffset(block.text,text,before.toString().length)
  if(start>=end)return null
  return selectedAnchor(block.endAnchor,block.text,start,end)
}

export async function readingSelectionPoints(root:HTMLElement,selection:Selection|null,blocks:Block[]):Promise<ReadingSelectionPoints|null>{
  if(!selection||selection.isCollapsed||selection.rangeCount!==1)return null
  const range=selection.getRangeAt(0)
  function endpoint(node:Node,offset:number){
    const element=node.nodeType===1?node as Element:node.parentElement,content=element?.closest<HTMLElement>('[data-reader-content]'),paragraph=content?.closest<HTMLElement>('[data-reading-block]'),index=blocks.findIndex(block=>block.id===paragraph?.dataset.readingBlock)
    if(!content||!root.contains(content)||!content.contains(node)||index<0)throw new Error('请在当前正文中选择文字')
    const before=range.cloneRange();before.selectNodeContents(content);before.setEnd(node,offset)
    return {index,offset:canonicalOffset(blocks[index].text,content.textContent||'',before.toString().length)}
  }
  const first=endpoint(range.startContainer,range.startOffset),last=endpoint(range.endContainer,range.endOffset),a=blocks[first.index].endAnchor,b=blocks[last.index].endAnchor
  if(first.index>last.index||last.index-first.index>=1000||first.index===last.index&&first.offset>last.offset||(['bookId','branchId','revisionId','chapterId','offsetUnit'] as const).some(k=>a[k]!==b[k]))throw new Error('选区两端须属于同一正文版本和章节')
  const startAnchor={...a,start:first.offset,end:first.offset,quoteHash:await quoteHash(blocks[first.index].text,a.revisionId,a.blockId,first.offset)},endAnchor={...b,start:last.offset,end:last.offset,quoteHash:await quoteHash(blocks[last.index].text,b.revisionId,b.blockId,last.offset)}
  const pieces=blocks.slice(first.index,last.index+1).map((block,index)=>block.text.slice(index===0?first.offset:0,first.index+index===last.index?last.offset:block.text.length))
  return {startAnchor,endAnchor,originalText:pieces.join('\n\n')}
}
