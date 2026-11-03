import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import BookmarkRemoval from './BookmarkRemoval.vue'
import {api,ApiError,newKey} from '../api'
import {markBookmarkDeleted} from '../platform/bookmark-cache'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const state=vi.hoisted(()=>({owner:'owner-a',base:'https://fixture.invalid',epoch:0,route:null as any,store:null as any,values:new Map<string,unknown>(),failClear:false}))
vi.mock('vue-router',()=>({useRoute:()=>state.route}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>state.epoch,newKey:vi.fn(),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({accountId:()=>state.owner,backendBase:()=>state.base}))
vi.mock('../platform/bookmark-cache',()=>({bookmarkScope:()=>{const owner=state.owner,base=state.base;return()=>owner===state.owner&&base===state.base},bookmarkDeletionRequest:async(book:string,id:string)=>structuredClone(state.values.get(state.owner+':'+book+':'+id)),saveBookmarkDeletionRequest:async(value:unknown,book:string,id:string)=>{if(value===null&&state.failClear)throw new Error('disk unavailable');state.values.set(state.owner+':'+book+':'+id,JSON.parse(JSON.stringify(value)))},markBookmarkDeleted:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>state.store}))
compileRender(BookmarkRemoval,new URL('./BookmarkRemoval.vue',import.meta.url))
const anchor={bookId:'book-a',branchId:'line-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:1,end:3,quoteHash:'range-hash',offsetUnit:'utf16' as const}
const note={id:'note-a',anchor,note:'我的原笔记',status:'valid',version:4}
const receipt={deleted:true as const,bookId:'book-a',bookmarkId:'note-a',version:5,receiptId:'receipt-a',deletedAt:'2026-10-05T01:00:00Z'}
const pending={bookId:'book-a',bookmarkId:'note-a',expectedVersion:4,key:'00000000-0000-4000-8000-000000000009',attempted:true}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,deleted=vi.fn(),cancel=vi.fn()
function mount(value=note){root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h(BookmarkRemoval,{note:value,onDeleted:deleted,onCancel:cancel})}));provideSfc(app);app.mount(root)}
function click(label:string){const button=descendants(root,item=>item.tag==='button'&&text(item)===label)[0];expect(button).toBeDefined();expect(button.props.disabled).toBeFalsy();return button.props.onClick()}
beforeEach(()=>{state.owner='owner-a';state.base='https://fixture.invalid';state.epoch=0;state.route=reactive({fullPath:'/notebook'});state.store=reactive({online:true,message:vi.fn()});state.values.clear();state.failClear=false;deleted=vi.fn();cancel=vi.fn();vi.mocked(api).mockReset();vi.mocked(newKey).mockReset().mockReturnValue('00000000-0000-4000-8000-000000000009');vi.mocked(markBookmarkDeleted).mockReset()})
afterEach(()=>{app?.unmount();app=undefined})
it('persists CAS identity before lookup and explicit DELETE, without writing progress',async()=>{
  vi.mocked(api).mockImplementation(async(path,options)=>{expect(state.values.get('owner-a:book-a:note-a')).toMatchObject({...pending,attempted:options?.method==='DELETE'});return path.endsWith('requestId=00000000-0000-4000-8000-000000000009')?{found:false}:receipt as any})
  mount();await flush();expect(api).not.toHaveBeenCalled();await click('确认删除笔记');await flush()
  expect(vi.mocked(api).mock.calls).toEqual([['/books/book-a/bookmarks/note-a/deletion?requestId=00000000-0000-4000-8000-000000000009'],['/books/book-a/bookmarks/note-a',{method:'DELETE',key:'00000000-0000-4000-8000-000000000009',body:{expectedVersion:4}}]])
  expect(markBookmarkDeleted).toHaveBeenCalledExactlyOnceWith('book-a','note-a',receipt);expect(deleted).toHaveBeenCalledExactlyOnceWith('note-a');expect(state.values.get('owner-a:book-a:note-a')).toBeNull()
})
it('blocks deletion on an old server with no receipt contract',async()=>{
  vi.mocked(api).mockRejectedValue(new ApiError('NOT_FOUND','确认服务尚未可用',404));mount();await flush();await click('确认删除笔记');await flush()
  expect(vi.mocked(api).mock.calls).toHaveLength(1);expect(vi.mocked(api).mock.calls[0][1]).toBeUndefined();expect(markBookmarkDeleted).not.toHaveBeenCalled();expect(deleted).not.toHaveBeenCalled()
})
it('shows CAS conflict inside the panel and only deletes the refreshed version after another explicit click',async()=>{
  vi.mocked(newKey).mockReturnValueOnce('00000000-0000-4000-8000-000000000009').mockReturnValueOnce('00000000-0000-4000-8000-000000000019')
  vi.mocked(api).mockResolvedValueOnce({found:false}).mockRejectedValueOnce(new ApiError('NOTE_CONFLICT','另一设备已更新这条笔记',409)).mockResolvedValueOnce({...note,note:'另一设备的新笔记',version:9}).mockResolvedValueOnce({found:false}).mockResolvedValueOnce({...receipt,version:10})
  mount();await flush();await click('确认删除笔记');await flush();expect(text(root)).toContain('另一设备已更新');expect(markBookmarkDeleted).not.toHaveBeenCalled();expect(state.values.get('owner-a:book-a:note-a')).toBeNull()
  await click('载入最新信息');await flush();expect(text(root)).toContain('另一设备的新笔记');expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='DELETE')).toHaveLength(1)
  await click('确认删除笔记');await flush();expect(vi.mocked(api).mock.calls[4][1]).toMatchObject({key:'00000000-0000-4000-8000-000000000019',body:{expectedVersion:9}})
})
it('keeps unknown response pending and reopens with a pure lookup of the same key',async()=>{
  vi.mocked(api).mockResolvedValueOnce({found:false}).mockRejectedValueOnce(new ApiError('NETWORK_UNAVAILABLE','lost response',0))
  mount();await flush();await click('确认删除笔记');await flush();expect(text(root)).toContain('删除结果尚未确认');expect(state.values.get('owner-a:book-a:note-a')).toEqual(pending)
  app!.unmount();app=undefined;vi.mocked(api).mockResolvedValueOnce({found:true,receipt});mount({...note,version:100});await flush()
  expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='DELETE')).toHaveLength(1);expect(vi.mocked(api).mock.calls[2]).toEqual(['/books/book-a/bookmarks/note-a/deletion?requestId=00000000-0000-4000-8000-000000000009']);expect(deleted).toHaveBeenCalledTimes(1)
})
it('does not automatically delete when a reopened lookup returns found false',async()=>{
  state.values.set('owner-a:book-a:note-a',pending);vi.mocked(api).mockResolvedValue({found:false});mount({...note,version:100});await flush()
  expect(api).toHaveBeenCalledTimes(1);expect(text(root)).toContain('尚未查到删除回执');expect(markBookmarkDeleted).not.toHaveBeenCalled()
  vi.mocked(api).mockResolvedValueOnce({found:false}).mockResolvedValueOnce(receipt);await click('重试同一删除请求');await flush()
  expect(vi.mocked(api).mock.calls[2][1]).toEqual({method:'DELETE',key:'00000000-0000-4000-8000-000000000009',body:{expectedVersion:4}});expect(newKey).not.toHaveBeenCalled()
})
it('does not repeat DELETE when the committed receipt only needs local cleanup',async()=>{
  state.failClear=true;vi.mocked(api).mockResolvedValueOnce({found:false}).mockResolvedValueOnce(receipt);mount();await flush();await click('确认删除笔记');await flush();expect(deleted).not.toHaveBeenCalled();expect(text(root)).toContain('disk unavailable')
  state.failClear=false;vi.mocked(api).mockResolvedValueOnce({found:true,receipt});await click('查看删除结果');await flush();expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='DELETE')).toHaveLength(1);expect(deleted).toHaveBeenCalledTimes(1)
})
it.each(['owner','backend'])('ignores a late delete receipt after %s changes',async change=>{
  const result=deferred<any>();vi.mocked(api).mockResolvedValueOnce({found:false}).mockReturnValueOnce(result.promise);mount();await flush();const action=click('确认删除笔记');await flush()
  if(change==='owner'){state.owner='owner-b';state.epoch++}else state.base='https://another.invalid';result.resolve(receipt);await action;await flush();expect(markBookmarkDeleted).not.toHaveBeenCalled();expect(deleted).not.toHaveBeenCalled()
})
it('records a same-owner late successful deletion after closing without touching the new panel',async()=>{
  const result=deferred<any>();vi.mocked(api).mockResolvedValueOnce({found:false}).mockReturnValueOnce(result.promise);mount();await flush();const action=click('确认删除笔记');await flush();app!.unmount();app=undefined;state.route.fullPath='/library';result.resolve(receipt);await action;await flush()
  expect(markBookmarkDeleted).toHaveBeenCalledTimes(1);expect(deleted).not.toHaveBeenCalled();expect(state.store.message).not.toHaveBeenCalled()
})
it('rejects a receipt for another bookmark without erasing the saved original request',async()=>{
  state.values.set('owner-a:book-a:note-a',pending);vi.mocked(api).mockResolvedValue({found:true,receipt:{...receipt,bookmarkId:'note-b'}});mount();await flush();expect(text(root)).toContain('回执不匹配');expect(markBookmarkDeleted).not.toHaveBeenCalled();expect(state.values.get('owner-a:book-a:note-a')).toEqual(pending)
})
it('does not send DELETE when the pure lookup returns after its panel was closed',async()=>{
  const response=deferred<any>();vi.mocked(api).mockReturnValueOnce(response.promise);mount();await flush();const action=click('确认删除笔记');await flush();app!.unmount();app=undefined;response.resolve({found:false});await action;await flush()
  expect(api).toHaveBeenCalledTimes(1);expect(state.values.get('owner-a:book-a:note-a')).toMatchObject({expectedVersion:4,attempted:false});expect(deleted).not.toHaveBeenCalled()
})
it('retains a rejected request if local clearing failed, then explicitly clears and refreshes before the next CAS',async()=>{
  state.failClear=true;vi.mocked(api).mockResolvedValueOnce({found:false}).mockRejectedValueOnce(new ApiError('NOTE_CONFLICT','changed',409));mount();await flush();await click('确认删除笔记');await flush()
  expect(text(root)).toContain('本机请求清理失败');expect(state.values.get('owner-a:book-a:note-a')).toEqual(pending);expect(markBookmarkDeleted).not.toHaveBeenCalled()
  state.failClear=false;vi.mocked(api).mockResolvedValueOnce({...note,version:7});await click('载入最新信息');await flush();expect(state.values.get('owner-a:book-a:note-a')).toBeNull();expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='DELETE')).toHaveLength(1)
})
