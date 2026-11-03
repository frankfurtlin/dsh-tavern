import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import BookRemoval from './BookRemoval.vue'
import {api,ApiError,newKey} from '../api'
import {invalidateBookCache} from '../platform/book-cache'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const state=vi.hoisted(()=>({owner:'owner-a',base:'',epoch:0,route:null as any,store:null as any,values:new Map<string,unknown>()}))
vi.mock('vue-router',()=>({useRoute:()=>state.route}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>state.epoch,newKey:vi.fn(),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({accountId:()=>state.owner,backendBase:()=>state.base,cacheGet:async(key:string)=>structuredClone(state.values.get(state.owner+':'+key)),cacheSet:async(key:string,value:unknown)=>{state.values.set(state.owner+':'+key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('../platform/book-cache',()=>({invalidateBookCache:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>state.store}))
compileRender(BookRemoval,new URL('./BookRemoval.vue',import.meta.url))
const receipt={deletionId:'delete-a',bookId:'book-a',deleted:true,cleanupPending:false,cleanupState:'complete',retainedStories:[{bookId:'carrier-a',storyId:'book-carrier-a',branchIds:['publication-line']}]}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,props:{bookId:string;version:number}
let deleted=vi.fn<()=>void>(),conflict=vi.fn<()=>void>()
function mount(){props=reactive({bookId:'book-a',version:6});root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h(BookRemoval,{...props,onDeleted:deleted,onConflict:conflict})}));provideSfc(app);app.mount(root)}
function click(label:string){const button=descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0];expect(button).toBeDefined();expect(button.props.disabled).toBeFalsy();button.props.onClick()}
beforeEach(()=>{state.owner='owner-a';state.base='';state.epoch=0;state.route=reactive({fullPath:'/stories/book-book-a'});state.store=reactive({online:true,message:vi.fn()});state.values.clear();deleted=vi.fn();conflict=vi.fn();vi.mocked(api).mockReset();vi.mocked(newKey).mockReset().mockReturnValue('00000000-a111-b222-c333-000000000006');vi.mocked(invalidateBookCache).mockReset().mockResolvedValue(undefined)})
afterEach(()=>{app?.unmount();app=undefined})
it('requires an explicit delete click and persists the exact CAS request before sending it',async()=>{
  vi.mocked(api).mockImplementation(async()=>{expect(state.values.get('owner-a:book-delete-request:book-a')).toEqual({key:'00000000-a111-b222-c333-000000000006',expectedVersion:6});return receipt as any})
  mount();await flush();expect(api).not.toHaveBeenCalled();click('删除这本小说');await flush()
  expect(api).toHaveBeenCalledWith('/books/book-a',{method:'DELETE',key:'00000000-a111-b222-c333-000000000006',body:{expectedVersion:6}})
  expect(invalidateBookCache).toHaveBeenCalledWith('book-a',receipt.retainedStories);expect(deleted).toHaveBeenCalledTimes(1);expect(state.values.get('owner-a:book-delete-request:book-a')).toBeNull()
})
it('reuses the persisted delete key and original version after an uncertain response',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new ApiError('NETWORK_UNAVAILABLE','offline',0)).mockResolvedValueOnce(receipt)
  mount();await flush();click('删除这本小说');await flush();expect(text(root)).toContain('尚未确认')
  props.version=20;await flush();click('重试同一删除请求');await flush()
  expect(vi.mocked(api).mock.calls.map(([path,options])=>({path,options}))).toEqual(Array(2).fill({path:'/books/book-a',options:{method:'DELETE',key:'00000000-a111-b222-c333-000000000006',body:{expectedVersion:6}}}))
  expect(newKey).toHaveBeenCalledTimes(1);expect(deleted).toHaveBeenCalledTimes(1)
})
it('only checks an existing request on reopen and does not automatically retry DELETE',async()=>{
  state.values.set('owner-a:book-delete-request:book-a',{key:'previous-key',expectedVersion:3})
  vi.mocked(api).mockRejectedValueOnce(new ApiError('NOT_FOUND','unknown',404)).mockResolvedValueOnce(receipt)
  mount();await flush();expect(api).toHaveBeenCalledExactlyOnceWith('/books/book-a/deletion');expect(text(root)).toContain('尚未查到删除回执')
  click('重试同一删除请求');await flush();expect(vi.mocked(api).mock.calls[1]).toEqual(['/books/book-a',{method:'DELETE',key:'previous-key',body:{expectedVersion:3}}]);expect(newKey).not.toHaveBeenCalled()
})
it('keeps a known deletion request when local cleanup fails and confirms it with GET instead of a second DELETE',async()=>{
  vi.mocked(api).mockResolvedValue(receipt);vi.mocked(invalidateBookCache).mockRejectedValueOnce(new Error('迁移暂未完成')).mockResolvedValueOnce(undefined)
  mount();await flush();click('删除这本小说');await flush();expect(text(root)).toContain('迁移暂未完成');expect(deleted).not.toHaveBeenCalled()
  expect(state.values.get('owner-a:book-delete-request:book-a')).toEqual({key:'00000000-a111-b222-c333-000000000006',expectedVersion:6})
  click('查看删除结果');await flush();expect(vi.mocked(api).mock.calls.map(([path,options])=>[path,options?.method||'GET'])).toEqual([['/books/book-a','DELETE'],['/books/book-a/deletion','GET']]);expect(deleted).toHaveBeenCalledTimes(1)
})
it('ignores a late receipt after changing the signed-in owner',async()=>{
  const request=deferred<any>();vi.mocked(api).mockReturnValue(request.promise)
  mount();await flush();click('删除这本小说');await flush();state.owner='owner-b';state.epoch++;request.resolve(receipt);await flush()
  expect(invalidateBookCache).not.toHaveBeenCalled();expect(deleted).not.toHaveBeenCalled();expect(state.values.has('owner-b:book-delete-request:book-a')).toBe(false)
  expect(state.values.get('owner-a:book-delete-request:book-a')).toMatchObject({key:'00000000-a111-b222-c333-000000000006'})
})
it('cleans an authoritative late deletion for the same account after closing the panel without changing the new page',async()=>{
  const request=deferred<any>();vi.mocked(api).mockReturnValue(request.promise)
  mount();await flush();click('删除这本小说');await flush();app!.unmount();app=undefined;state.route.fullPath='/library';request.resolve(receipt);await flush()
  expect(invalidateBookCache).toHaveBeenCalledExactlyOnceWith('book-a',receipt.retainedStories);expect(deleted).not.toHaveBeenCalled();expect(state.store.message).not.toHaveBeenCalled()
})
it('clears a rejected CAS request and refreshes the caller without treating it as deleted',async()=>{
  vi.mocked(api).mockRejectedValue(new ApiError('BOOK_CONFLICT','请刷新',409));mount();await flush();click('删除这本小说');await flush()
  expect(conflict).toHaveBeenCalledTimes(1);expect(deleted).not.toHaveBeenCalled();expect(invalidateBookCache).not.toHaveBeenCalled();expect(state.values.get('owner-a:book-delete-request:book-a')).toBeNull()
})
it('rejects a deletion receipt for another book and preserves the pending request',async()=>{
  vi.mocked(api).mockResolvedValue({...receipt,bookId:'different-book'});mount();await flush();click('删除这本小说');await flush()
  expect(text(root)).toContain('删除回执不匹配');expect(invalidateBookCache).not.toHaveBeenCalled();expect(deleted).not.toHaveBeenCalled();expect(state.values.get('owner-a:book-delete-request:book-a')).toMatchObject({key:'00000000-a111-b222-c333-000000000006'})
})
