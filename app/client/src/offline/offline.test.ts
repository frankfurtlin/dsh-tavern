import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {webcrypto} from 'node:crypto'
import {commitPackage,entries,readPackage,packageKey,packageId,offlineRead,localNotes,noteQueue,queueKey,removeBookDownloads,type OfflinePayload} from './storage'
import {queueNote,syncNotes} from './notes'
import {api,ApiError} from '../api'
// Existing text/note suite stays independent of the separately verified image package protocol.
vi.mock('../api/offline-images',()=>({validateOfflineImages:async()=>{},remapBookImages:()=>undefined}))
const state=vi.hoisted(()=>({owner:'owner-a',auth:0,values:new Map<string,unknown>(),write:Promise.resolve(),failIndex:false}))
vi.mock('../platform/bridge',()=>({accountId:()=>state.owner,backendBase:()=>'',cacheGet:async(key:string)=>structuredClone(state.values.get(state.owner+':'+key)),cacheSet:async(key:string,value:unknown)=>{state.values.set(state.owner+':'+key,structuredClone(value))},cacheUpdate:async(key:string,update:Function,active:Function=()=>true)=>{const owner=state.owner;let result:any;const work=state.write.then(()=>{if(owner!==state.owner||!active())return;if(state.failIndex&&key==='offline-reading-index:v1')throw new Error('storage full');result=update(structuredClone(state.values.get(owner+':'+key)));state.values.set(owner+':'+key,structuredClone(result))});state.write=work.catch(()=>{});await work;return result}}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>state.auth,newKey:()=>crypto.randomUUID(),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
const anchor={bookId:'book-a',branchId:'line-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:0,end:0,quoteHash:'hash',offsetUnit:'utf16'} as const
function payload():OfflinePayload{return{book:{id:'book-a',title:'合成离线故事',originalBranchId:'line-a',originalRevisionId:'revision-a'} as any,branch:{id:'line-a',name:'原文',headRevisionId:'revision-a'} as any,revisionId:'revision-a',directory:[{id:'chapter-a',title:'第一章',observed:true,blockCount:1}],chapters:[{id:'chapter-a',title:'第一章',revisionId:'revision-a',contentHash:'known',blocks:[{id:'block-a',text:'潮水🌊留下最后一封信。',html:null,source:'original',startAnchor:anchor,endAnchor:{...anchor,start:14,end:14}}]}],bookmarks:[],progress:{version:1,anchor,maxObservedAnchor:anchor},assets:{'image-a':'data:image/png;base64,cHVibGlj'},origins:['book-book-a']}}
beforeEach(()=>{state.owner='owner-a';state.auth=0;state.values.clear();state.write=Promise.resolve();state.failIndex=false;vi.mocked(api).mockReset();vi.stubGlobal('crypto',webcrypto);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})})
afterEach(()=>vi.unstubAllGlobals())
it('publishes a complete verified package with exact UTF16 text, directory, progress and public image bytes',async()=>{
  const data=payload(),entry=await commitPackage(data,()=>true)
  expect(await entries()).toEqual([entry]);expect((await readPackage(entry.id))?.payload).toEqual(data)
  expect(await offlineRead('/branches/line-a/chapters/chapter-a?revisionId=revision-a')).toEqual(data.chapters[0])
  expect(await offlineRead('/branches/line-a/chapters?revisionId=another-revision')).toBeUndefined()
})
it('does not mark an incomplete index commit as downloaded',async()=>{
  state.failIndex=true;await expect(commitPackage(payload(),()=>true)).rejects.toThrow('storage full')
  expect(await entries()).toEqual([])
})
it('rejects changed package bytes and never serves another account package',async()=>{
  const entry=await commitPackage(payload(),()=>true),key='owner-a:'+packageKey(entry.id)
  const stored=state.values.get(key) as any;stored.payload.chapters[0].blocks[0].text='corrupt'
  expect(await readPackage(entry.id)).toBeUndefined();expect(await offlineRead('/books/book-a')).toBeUndefined()
  state.owner='owner-b';expect(await entries()).toEqual([]);expect(await readPackage(entry.id)).toBeUndefined()
})
it('keeps two independent offline creations and edits a pending creation without replacing the first',async()=>{
  const first=await queueNote(anchor,'第一份离线笔记'),second=await queueNote(anchor,'第二份离线笔记')
  expect(first.id).not.toBe(second.id);await queueNote(anchor,'修改第二份',second)
  expect((await noteQueue()).map(note=>note.note)).toEqual(['第一份离线笔记','修改第二份'])
})
it('recovers the same queued create key without duplicating it and keeps an unsent offline note editable',async()=>{
  const first=await queueNote(anchor,'本机第一次保存',undefined,'durable-offline-create-key',false)
  const recovered=await queueNote(anchor,'本机第一次保存',undefined,'durable-offline-create-key',false)
  expect(recovered.id).toBe(first.id);expect(await noteQueue()).toMatchObject([{key:'durable-offline-create-key',attempted:false}]);expect(await noteQueue()).toHaveLength(1)
  await queueNote(anchor,'尚未联网时修改',recovered)
  expect((await queueNote(anchor,'本机第一次保存',undefined,'durable-offline-create-key',false)).note).toBe('尚未联网时修改')
  vi.mocked(api).mockResolvedValue({id:'saved-a',anchor,note:'尚未联网时修改',version:1,status:'saved'})
  expect(await syncNotes()).toEqual({synced:1,remaining:0});expect(api).toHaveBeenCalledTimes(1)
  expect(vi.mocked(api).mock.calls[0][1]).toMatchObject({key:'durable-offline-create-key',body:{note:'尚未联网时修改'}})
})
it('retains the original edit version across repeated offline changes',async()=>{
  const remote={id:'remote-a',anchor,note:'旧内容',version:4,status:'saved'}
  const local=await queueNote(anchor,'本机一版',remote);await queueNote(anchor,'本机二版',local)
  expect(await noteQueue()).toMatchObject([{remoteId:'remote-a',baseVersion:4,note:'本机二版'}])
  expect(await localNotes(anchor.bookId,anchor.branchId,anchor.revisionId,[remote])).toMatchObject([{id:'remote-a',note:'本机二版',status:'pending_sync'}])
})
it('creates a separate conflict copy and preserves the remote note without PATCH',async()=>{
  await commitPackage(payload(),()=>true)
  await queueNote(anchor,'本机想法',{id:'remote-a',anchor,note:'base',version:2,status:'saved'})
  vi.mocked(api).mockImplementation(async(path,options)=>options?.method==='POST'?{id:'copy-a',anchor,note:'本机想法',version:1,status:'saved'} as any:{id:'remote-a',anchor,note:'另一设备想法',version:3,status:'saved'} as any)
  expect(await syncNotes()).toEqual({synced:1,remaining:0})
  expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='PATCH')).toHaveLength(0)
  const stored=await readPackage(packageId(anchor.bookId,anchor.branchId,anchor.revisionId))
  expect(stored?.payload.bookmarks.map(note=>note.note)).toEqual(['另一设备想法','本机想法'])
  expect(await localNotes(anchor.bookId,anchor.branchId,anchor.revisionId,stored!.payload.bookmarks)).toMatchObject([{id:'remote-a',note:'另一设备想法'},{id:'copy-a',status:'conflict_copy',note:'本机想法'}])
})
it('confirms an already committed lost PATCH receipt instead of writing again',async()=>{
  await queueNote(anchor,'已经提交的内容',{id:'remote-a',anchor,note:'旧内容',version:1,status:'saved'})
  vi.mocked(api).mockResolvedValue({id:'remote-a',anchor,note:'已经提交的内容',version:2,status:'saved'})
  expect(await syncNotes()).toEqual({synced:1,remaining:0});expect(api).toHaveBeenCalledTimes(1)
  expect(vi.mocked(api).mock.calls[0][1]).toBeUndefined()
})
it('keeps the same create idempotency key after an uncertain network result',async()=>{
  await queueNote(anchor,'同一条笔记');vi.mocked(api).mockRejectedValueOnce(new ApiError('NETWORK_UNAVAILABLE','offline',0))
  expect(await syncNotes()).toEqual({synced:0,remaining:1});const key=vi.mocked(api).mock.calls[0][1]?.key
  await expect(queueNote(anchor,'改变发送中的笔记',{id:(await noteQueue())[0].id,anchor,note:'',status:'pending_sync'})).rejects.toThrow('确认同步')
  vi.mocked(api).mockResolvedValueOnce({id:'saved-a',anchor,note:'同一条笔记',version:1,status:'saved'})
  expect(await syncNotes()).toEqual({synced:1,remaining:0});expect(vi.mocked(api).mock.calls[1][1]?.key).toBe(key)
})
it('ignores late saved notes after an account switch without cleaning the old queue',async()=>{
  await queueNote(anchor,'甲账号笔记')
  let complete!:(value:any)=>void;vi.mocked(api).mockReturnValue(new Promise(resolve=>{complete=resolve}))
  const request=syncNotes();for(let i=0;i<15;i++)await Promise.resolve()
  state.owner='owner-b';state.auth++;complete({id:'saved-a',anchor,note:'甲账号笔记'})
  await request;expect(await noteQueue()).toEqual([]);expect(state.values.get('owner-a:'+queueKey)).toMatchObject([{note:'甲账号笔记'}])
})
function publication():OfflinePayload{
  const data=payload(),change=(a:typeof anchor)=>({...a,branchId:'published-line',revisionId:'published-revision'})
  return {...data,branch:{...data.branch,id:'published-line',name:'公开故事',headRevisionId:'published-revision',parentBranchId:'line-a',forkAnchor:anchor},revisionId:'published-revision',chapters:data.chapters.map(chapter=>({...chapter,revisionId:'published-revision',blocks:chapter.blocks.map(block=>({...block,startAnchor:change(block.startAnchor as typeof anchor),endAnchor:change(block.endAnchor as typeof anchor)}))})),progress:{...data.progress,anchor:change(anchor),maxObservedAnchor:change(anchor)},origins:['book-book-a','world-'+'c'.repeat(32)]}
}
const carrier={bookId:'carrier-book',storyId:'book-carrier-book',branchIds:['published-line'],book:{id:'carrier-book',title:'实际保留的出版故事',originalBranchId:'published-line',originalRevisionId:'published-revision'} as any}
it('removes the source package and migrates only exact retained publication branches without changing text or anchor offsets',async()=>{
  const original=await commitPackage(payload(),()=>true),pub=publication()
  pub.bookmarks=[{id:'published-note',anchor:pub.chapters[0].blocks[0].endAnchor,note:'公开段落笔记',version:2,status:'saved'}]
  const published=await commitPackage(pub,()=>true)
  const other={...payload(),book:{...payload().book,id:'other-book'},branch:{...payload().branch,id:'other-line'}}
  const unrelated=await commitPackage(other,()=>true)
  await removeBookDownloads('book-a',[carrier],()=>true)
  expect(await readPackage(original.id)).toBeUndefined();expect(await readPackage(published.id)).toBeUndefined()
  const moved=await readPackage(packageId(carrier.bookId,'published-line','published-revision'))
  expect(moved?.payload.book).toEqual(carrier.book);expect(moved?.payload.branch.parentBranchId).toBeNull();expect(moved?.payload.branch.forkAnchor).toBeNull()
  expect(moved?.payload.chapters[0].blocks[0].text).toBe(pub.chapters[0].blocks[0].text)
  expect(moved?.payload.chapters[0].blocks[0].endAnchor).toEqual({...pub.chapters[0].blocks[0].endAnchor,bookId:carrier.bookId})
  expect(moved?.payload.bookmarks[0].anchor).toEqual({...pub.bookmarks[0].anchor,bookId:carrier.bookId})
  expect(moved?.payload.progress.anchor).toEqual({...pub.progress.anchor,bookId:carrier.bookId})
  expect(moved?.payload.origins).toEqual([carrier.storyId,'world-'+'c'.repeat(32)])
  expect((await entries()).map(entry=>entry.id).sort()).toEqual([unrelated.id,moved!.id].sort())
  expect((await readPackage(unrelated.id))?.payload).toEqual(other)
})
it.each([undefined,{...carrier.book,id:'wrong-carrier'}])('requires verified carrier metadata before deleting any downloaded source bytes (%j)',async(book)=>{
  const original=await commitPackage(payload(),()=>true),published=await commitPackage(publication(),()=>true)
  await expect(removeBookDownloads('book-a',[{...carrier,book}],()=>true)).rejects.toThrow('资料尚未确认')
  expect((await entries()).map(entry=>entry.id)).toEqual([original.id,published.id]);expect(await readPackage(original.id)).toBeDefined();expect(await readPackage(published.id)).toBeDefined()
})
it('preserves uncertain publication note requests and unrelated queues without resubmitting changed anchors',async()=>{
  const pub=publication(),pubAnchor=pub.chapters[0].blocks[0].startAnchor
  await queueNote(anchor,'删除来源的笔记');await queueNote(pubAnchor,'尚未提交的出版笔记')
  await queueNote({...pubAnchor,start:1,end:1},'已发送且未知的出版笔记',undefined,'same-receipt-key',true)
  await queueNote({...anchor,bookId:'other-book',branchId:'other-line'},'另一本书的笔记')
  await removeBookDownloads('book-a',[carrier],()=>true)
  const queue=await noteQueue();expect(queue.map(note=>note.note)).toEqual(['尚未提交的出版笔记','已发送且未知的出版笔记','另一本书的笔记'])
  expect(queue[0].anchor.bookId).toBe(carrier.bookId);expect(queue[0].blocked).toBeUndefined()
  expect(queue[1]).toMatchObject({key:'same-receipt-key',attempted:true,anchor:{bookId:carrier.bookId},submittedAnchor:{...pubAnchor,start:1,end:1}})
  expect(queue[1].blocked).toContain('待确认');expect(queue[2].anchor.bookId).toBe('other-book');expect(api).not.toHaveBeenCalled()
  // A blocked request remains visible locally and is never resubmitted by sync.
  state.values.set('owner-a:'+queueKey,[queue[1]])
  expect(await syncNotes()).toEqual({synced:0,remaining:1});expect(api).not.toHaveBeenCalled()
})
it('does not remove packages or queues after the active identity changed',async()=>{
  const original=await commitPackage(payload(),()=>true);await queueNote(anchor,'仍属于甲账号')
  await expect(removeBookDownloads('book-a',[],()=>false)).rejects.toThrow('账号或服务器')
  expect(await readPackage(original.id)).toBeDefined();expect(await noteQueue()).toMatchObject([{note:'仍属于甲账号'}])
})
