import {describe,it,expect} from 'vitest'
import {parseGenerationSelection,selectionIdentity,selectionRequest,matchesSelection} from './generation-selection'
import type {SelectionEditScope} from './selection-edits'
const anchor={bookId:'book',branchId:'line',revisionId:'old',chapterId:'chapter',blockId:'last',start:4,end:4,offsetUnit:'utf16' as const,quoteHash:'exact-end'}
const scope:SelectionEditScope={bookId:'book',branchId:'line',revisionId:'old',headRevisionId:'head',chapterId:'chapter',originalText:'😀é',startAnchor:{...anchor,blockId:'first',start:2,end:2,quoteHash:'exact-start'},endAnchor:anchor}
describe('durable selected generation boundaries',()=>{
 it('separates identical start points with different end blocks and revisions',()=>{
  expect(selectionIdentity(scope)).not.toBe(selectionIdentity({...scope,endAnchor:{...anchor,blockId:'other'}}))
  expect(selectionIdentity(scope)).not.toBe(selectionIdentity({...scope,revisionId:'another'}))
 })
 it('freezes a one-candidate child request and never invents a continuation from the selection start',()=>{
  const request=selectionRequest(scope,'expand','更细腻','default-model')
  expect(request.originAnchor).toEqual(anchor);expect(request.expectedHeadRevisionId).toBe('head')
  expect(request.chapterCount).toBe(1);expect(request.target.mode).toBe('new_child_branch')
  expect(request.selection?.sourcePolicy).toBe('forkPinned');expect(request.selection?.startAnchor).toEqual(scope.startAnchor)
  scope.startAnchor.quoteHash='changed-live'
  expect(request.selection?.startAnchor.quoteHash).toBe('exact-start');scope.startAnchor.quoteHash='exact-start'
 })
 it('drops undeclared private fields and rejects mismatched end point or owner scope',()=>{
  const input={purpose:'rewrite',sourcePolicy:'forkPinned',startAnchor:scope.startAnchor,endAnchor:anchor,providerKey:'must-not-cache'}
  const parsed=parseGenerationSelection(input,anchor)!
  expect(Object.keys(parsed)).toEqual(['purpose','sourcePolicy','startAnchor','endAnchor'])
  expect(()=>parseGenerationSelection({...input,endAnchor:{...anchor,quoteHash:'wrong'}},anchor)).toThrow()
  expect(()=>parseGenerationSelection({...input,startAnchor:{...scope.startAnchor,bookId:'other'}},anchor)).toThrow()
  expect(()=>parseGenerationSelection({...input,startAnchor:{...scope.startAnchor,end:3}},anchor)).toThrow()
 })
 it('does not attach another selection range to the current editor',()=>{
  const parsed=parseGenerationSelection({purpose:'rewrite',sourcePolicy:'requireUnchanged',startAnchor:scope.startAnchor,endAnchor:anchor},anchor)!
  expect(matchesSelection({selection:parsed} as any,scope)).toBe(true)
  expect(matchesSelection({selection:{...parsed,endAnchor:{...anchor,end:5}}} as any,scope)).toBe(false)
  expect(parseGenerationSelection(undefined,anchor)).toBeUndefined()
 })
})
