import {beforeEach,describe,expect,it,vi} from 'vitest'
import {contextRoute,rememberContext,storyId,sessionContext} from './stories'
import {api,ApiError} from './index'
import type {StoryContext,StoryDetail} from './stories'
import type {Anchor,RoleSession} from './types'

const scope=vi.hoisted(()=>({epoch:0}))
vi.mock('./index',()=>({api:vi.fn(),authGeneration:()=>scope.epoch,ApiError:class extends Error{
  constructor(public code:string,message:string,public status:number){super(message)}
}}))
const id='a'.repeat(32),branch='b'.repeat(32),revision='c'.repeat(32),world='d'.repeat(32),line='e'.repeat(32),session='f'.repeat(32)
const anchor:Anchor={bookId:id,branchId:branch,revisionId:revision,chapterId:'chapter',blockId:'block',start:3,end:3,offsetUnit:'utf16',quoteHash:'1'.repeat(64)}
const reader:StoryContext={storyId:storyId('book',id),mode:'read',perspective:'reader',entryOrigin:'library',bookId:id,readerBranchId:branch,anchor}
beforeEach(()=>{scope.epoch=0;vi.mocked(api).mockReset()})
describe('story contexts retain mode, branch and logical position',()=>{
  it('preserves the exact UTF-16 anchor in a reading deep link',()=>{
    const route=contextRoute(reader)
    expect(route.path).toBe('/books/'+id+'/read')
    expect(JSON.parse(route.query.anchor)).toEqual(anchor)
    expect(route.query.revision).toBe(revision)
  })
  it('opens chat by session without using the reading or action route',()=>{
    expect(contextRoute({...reader,mode:'chat',sessionId:session,characterId:'person'})).toEqual({path:'/conversations',query:{session,story:reader.storyId}})
  })
  it('world reading retains the displayed checkpoint while play uses its explicit line',()=>{
    const context:StoryContext={storyId:storyId('world',world),mode:'read',perspective:'reader',entryOrigin:'worlds',worldId:world,worldLineId:line,checkpointId:'9'.repeat(32)}
    expect(contextRoute(context)).toEqual({path:'/worlds/'+world+'/read',query:{line,checkpoint:'9'.repeat(32),story:context.storyId}})
    expect(contextRoute({...context,mode:'play',perspective:'actor',characterId:'person'})).toEqual({path:'/worlds/'+world+'/play',query:{line,story:context.storyId}})
  })
  it('rejects a foreign anchor, missing location and mismatched actor perspective',()=>{
    expect(()=>contextRoute({...reader,anchor:{...anchor,branchId:line}})).toThrow()
    expect(()=>contextRoute({...reader,anchor:undefined})).toThrow()
    expect(()=>contextRoute({...reader,perspective:'actor'})).toThrow()
  })
  it('does not accept user-controlled strings as opaque story IDs',()=>{
    expect(()=>storyId('book','../another-account')).toThrow()
  })
  it('uses the independent mode version without writing any body or world head',async()=>{
    vi.mocked(api).mockResolvedValueOnce({resume:{read:{version:7}}} as StoryDetail).mockResolvedValueOnce({version:8})
    await rememberContext(reader)
    expect(vi.mocked(api).mock.calls[1]).toEqual(['/stories/book-'+id+'/resume',{method:'PUT',body:{expectedVersion:7,context:reader}}])
  })
  it('a late response after account change cannot start a resume write',async()=>{
    let finish!:(value:unknown)=>void
    vi.mocked(api).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}) as never)
    const saving=rememberContext(reader);scope.epoch++;finish({resume:{read:{version:0}}})
    await expect(saving).rejects.toMatchObject({code:'SESSION_CHANGED'})
    expect(api).toHaveBeenCalledTimes(1)
  })
  it('a version conflict is surfaced without an automatic overwrite or retry',async()=>{
    vi.mocked(api).mockResolvedValueOnce({resume:{read:{version:7}}}).mockRejectedValueOnce(new ApiError('VERSION_CONFLICT','conflict',409))
    await expect(rememberContext(reader)).rejects.toMatchObject({code:'VERSION_CONFLICT'})
    expect(api).toHaveBeenCalledTimes(2)
  })
  it('a publication role session retains its originating story and exact pinned position',()=>{
    const role={id:session,bookId:id,branchId:branch,pinnedAnchor:anchor,characterId:'person'} as RoleSession
    const context=sessionContext(role,storyId('world',world))
    expect(context).toMatchObject({storyId:storyId('world',world),mode:'chat',perspective:'reader',sessionId:session,readerBranchId:branch,anchor})
    expect(contextRoute(context).query.story).toBe(storyId('world',world))
    expect(sessionContext(role).storyId).toBe(storyId('book',id))
    expect(()=>sessionContext(role,'../another-account')).toThrow('故事标识无效')
  })
})
