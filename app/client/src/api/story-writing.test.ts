import {describe,it,expect,vi} from 'vitest'
vi.mock('./index',()=>({ApiError:class extends Error {constructor(public code:string,message:string,public status:number){super(message)}}}))
import {writingChapterCount,writingOperation,writingError,writingReviewMessages} from './story-writing'
import {ApiError} from './index'
import type {Anchor,Branch} from './types'

const anchor={bookId:'book',branchId:'line',revisionId:'revision',chapterId:'chapter',blockId:'block',start:0,end:5,offsetUnit:'utf16',quoteHash:'a'.repeat(64)} as Anchor
const branch={id:'line',name:'已有故事线',parentBranchId:'original'} as Branch
describe('reader writing choices',()=>{
  it('uses one writing entry: empty wishes continue, explicit wishes fork even at a child tail',()=>{
    const natural=writingOperation(anchor,branch,'continue_at_tail','default','   ',1)
    expect(natural.operation).toBe('continue_at_tail');expect(natural.instruction).toBe('');expect(natural.target?.mode).toBe('append_existing')
    const alternative=writingOperation(anchor,branch,'continue_at_tail','default','留下来救人',1)
    expect(alternative.operation).toBe('counterfactual');expect(alternative.target?.mode).toBe('new_child_branch');expect(alternative.instruction).toBe('留下来救人')
    const middle=writingOperation(anchor,branch,'continue_from_anchor','default','',1)
    expect(middle.operation).toBe('continue_from_anchor');expect(middle.target?.mode).toBe('new_child_branch')
  })
  it('accepts only finite whole chapter counts within the advertised range',()=>{
    for(const count of [1,3,5,10,'2'])expect(writingChapterCount(count)).toBe(Number(count))
    for(const count of [0,11,-1,1.5,'',null,true,false,[],{},NaN,Infinity,'3章'])expect(writingChapterCount(count)).toBeNull()
  })
  it('keeps a Unicode character intact at the branch-name limit without truncating the wish',()=>{
    const wish='a'.repeat(39)+'😀继续写'
    const result=writingOperation(anchor,branch,'continue_at_tail','default',wish,1)
    expect(result.target?.name).toBe('a'.repeat(39)+'😀')
    expect(result.instruction).toBe(wish)
    expect(JSON.parse(JSON.stringify(result)).target.name).toBe('a'.repeat(39)+'😀')
  })
  it('appends at a child tail and creates a named branch at any other origin',()=>{
    const append=writingOperation(anchor,branch,'continue_at_tail','default','',3)
    expect(append.target).toEqual({mode:'append_existing',name:branch.name,branchId:branch.id})
    expect(append.chapterCount).toBe(3)
    const fork=writingOperation(anchor,branch,'counterfactual','default','留下\n救人',5)
    expect(fork.target?.mode).toBe('new_child_branch');expect(fork.target?.name).toBe('留下 救人')
    expect(fork.anchor).not.toBe(anchor);expect(fork.expectedHeadRevisionId).toBe(anchor.revisionId)
    expect(writingOperation(anchor,{...branch,parentBranchId:null},'continue_at_tail','default','',1).target?.name).toBe('AI 续写')
  })
  it('gives optional alternative wishes a usable instruction without exposing internal codes',()=>{
    expect(writingOperation(anchor,branch,'counterfactual','default','',1).instruction).toBeTruthy()
    for(const error of ['ANALYSIS_INVALID','PROVIDER_TIMEOUT','VALIDATION_FAILED',new ApiError('BUDGET_NOT_AUTHORIZED','input token limit',403)]){
      expect(writingError(error)).not.toMatch(/token|ANALYSIS|PROVIDER|VALIDATION|预算授权/)
    }
  })
  it('distinguishes plot checks from incomplete prose and terminal connection failures',()=>{
    expect(writingError('GENERATION_SPOILER')).toContain('原作后续情节')
    expect(writingError('GENERATION_HISTORY_CONFLICT')).toContain('起点之前')
    expect(writingError('VALIDATION_UNCERTAIN')).toContain('可信结果')
    expect(writingError('GENERATION_INCOMPLETE')).toContain('篇幅和结构')
    expect(writingError('PROVIDER_STATUS_UNKNOWN')).toContain('已停止')
    expect(writingError('PROVIDER_STATUS_UNKNOWN')).not.toContain('稍后查看')
  })
  it('uses nonblocking, deduplicated content notes and never renders arbitrary model reasons',()=>{
    const notes=writingReviewMessages([{code:'SPOILER',chapterIndex:1},{code:'SPOILER',chapterIndex:2},{code:'HISTORY_CONFLICT',chapterIndex:3},{code:'PRIVATE-SUPPLIER-REASON',chapterIndex:4}])
    expect(notes).toHaveLength(3);expect(notes[0]).toContain('原作后续情节');expect(notes.join('')).not.toMatch(/未加入|不能|PRIVATE/)
  })
})
