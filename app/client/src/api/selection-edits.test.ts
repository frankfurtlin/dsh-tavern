import {describe,it,expect} from 'vitest'
import {selectionEditScope} from './selection-edits'
import {quoteHash,selectedAnchor} from './anchors'
import type {Block,Anchor} from './types'

const anchor:Anchor={bookId:'book',branchId:'original',revisionId:'v1',chapterId:'chapter',blockId:'paragraph',start:0,end:0,offsetUnit:'utf16',quoteHash:''}
const block:Block={id:'paragraph',text:'林渡😀披着旧雨衣。梅舟e\u0301把信收好。',source:'original',html:null,startAnchor:anchor,endAnchor:{...anchor,start:22,end:22}}
describe('immutable reader selection',()=>{
 it('pins an exact emoji range with two independently verifiable point anchors',async()=>{
  const range=await selectedAnchor(anchor,block.text,2,4),scope=await selectionEditScope(block,'head-v2',range)
  expect(scope.originalText).toBe('😀');expect(scope.revisionId).toBe('v1');expect(scope.headRevisionId).toBe('head-v2')
  expect(scope.startAnchor.start).toBe(2);expect(scope.startAnchor.end).toBe(2);expect(scope.endAnchor.start).toBe(4);expect(scope.endAnchor.end).toBe(4)
  expect(scope.startAnchor.quoteHash).toBe(await quoteHash(block.text,'v1','paragraph',2));expect(scope.endAnchor.quoteHash).toBe(await quoteHash(block.text,'v1','paragraph',4))
  range.start=0;expect(scope.startAnchor.start).toBe(2)
 })
 it('keeps combining marks as literal UTF16 text without normalization',async()=>{
  const start=block.text.indexOf('e'),end=start+2,scope=await selectionEditScope(block,'v1',await selectedAnchor(anchor,block.text,start,end))
  expect(scope.originalText).toBe('e\u0301');expect(scope.endAnchor.end-scope.startAnchor.start).toBe(2)
 })
 it('supports zero-length insertion and the entire exact paragraph',async()=>{
  const point=await selectionEditScope(block,'v1',await selectedAnchor(anchor,block.text,4,4)),whole=await selectionEditScope(block,'v1',null)
  expect(point.originalText).toBe('');expect(point.startAnchor).toEqual(point.endAnchor);expect(whole.originalText).toBe(block.text);expect(whole.endAnchor.end).toBe(block.text.length)
 })
 it.each(['bookId','branchId','revisionId','chapterId','blockId','offsetUnit'] as const)('rejects a range from another %s before submission',async key=>{
  const range=await selectedAnchor(anchor,block.text,2,4)
  await expect(selectionEditScope(block,'v1',{...range,[key]:'other'})).rejects.toThrow('不属于')
 })
 it('rejects stale bytes and a point inside a surrogate pair',async()=>{
  const range=await selectedAnchor(anchor,block.text,2,4)
  await expect(selectionEditScope({...block,text:block.text.replace('😀','🙂')},'v1',range)).rejects.toThrow('校验失败')
  await expect(selectionEditScope(block,'v1',{...range,start:3})).rejects.toThrow('ANCHOR_INVALID')
 })
 it.each([[-1,4],[2,999],[4,2],[2.5,4],[NaN,4]])('rejects invalid bounds %s,%s',async(start,end)=>{
  await expect(selectionEditScope(block,'v1',{...anchor,start,end})).rejects.toThrow('位置无效')
 })
})
