import {it,expect} from 'vitest'
import {blockHighlights} from './reading-annotations'
import {selectedAnchor} from './anchors'
import type {Anchor,Block,Bookmark} from './types'
const at:Anchor={bookId:'book-a',branchId:'line-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:0,end:0,quoteHash:'',offsetUnit:'utf16'}
const block:Block={id:at.blockId,text:'甲😀乙丙丁戊',html:null,source:'original',startAnchor:at,endAnchor:{...at,start:8,end:8}}
async function mark(start:number,end:number,change:Partial<Anchor>={}):Promise<Bookmark>{return {id:'mark-'+start,note:'',version:1,status:'active',anchor:{...await selectedAnchor(at,block.text,start,end),...change}}}
it('paints the exact persisted UTF-16 emoji selection',async()=>{expect(await blockHighlights(block,at.bookId,[await mark(1,3)])).toEqual([{start:1,end:3}]);expect(block.text.slice(1,3)).toBe('😀')})
it('merges overlapping annotation ranges without duplicating source text',async()=>{expect(await blockHighlights(block,at.bookId,[await mark(4,7),await mark(1,5),await mark(1,3)])).toEqual([{start:1,end:7}])})
it('does not paint annotations from another book, line, revision, chapter or block',async()=>{
  const changes:Partial<Anchor>[]=[{bookId:'book-b'},{branchId:'line-b'},{revisionId:'revision-b'},{chapterId:'chapter-b'},{blockId:'block-b'}]
  expect(await blockHighlights(block,at.bookId,await Promise.all(changes.map(change=>mark(1,3,change))))).toEqual([])
})
it('leaves zero-length bookmarks separate from highlights',async()=>{expect(await blockHighlights(block,at.bookId,[await mark(3,3)])).toEqual([])})
it('rejects stale quote hashes after a source text change',async()=>{expect(await blockHighlights({...block,text:'甲🌧乙丙丁戊'},at.bookId,[await mark(1,3)])).toEqual([])})
it('does not paint malformed, out-of-range or half-surrogate ranges',async()=>{
  const good=await mark(1,3),bad=[{start:2},{end:2},{start:-1},{end:90},{start:1.5}]
  expect(await blockHighlights(block,at.bookId,bad.map(change=>({...good,anchor:{...good.anchor,...change}})))).toEqual([])
})
it('keeps local pending and conflict-copy highlights readable in their exact scope',async()=>{
  expect(await blockHighlights(block,at.bookId,[{...await mark(1,3),status:'pending_sync'},{...await mark(5,7),status:'conflict_copy'}])).toEqual([{start:1,end:3},{start:5,end:7}])
})
