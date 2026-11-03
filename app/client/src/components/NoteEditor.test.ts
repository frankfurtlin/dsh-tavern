import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import NoteEditor from './NoteEditor.vue'
import {api,ApiError} from '../api'
import {cacheGet} from '../platform/bridge'
import {queueNote} from '../offline/notes'
import type {Anchor,Bookmark} from '../api/types'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const state=vi.hoisted(()=>({owner:'owner-a',epoch:0,route:null as any,store:null as any,values:new Map<string,unknown>(),clearFails:false}))
vi.mock('vue-router',()=>({useRoute:()=>state.route}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>state.epoch,newKey:()=> '00000000-a111-b222-c333-000000000006',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({accountId:()=>state.owner,cacheGet:vi.fn(async(key:string)=>structuredClone(state.values.get(key))),cacheSet:async(key:string,value:unknown)=>{if(value===null&&state.clearFails)throw new Error('disk unavailable');state.values.set(key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('../stores/app',()=>({useApp:()=>state.store}))
vi.mock('../offline/notes',()=>({queueNote:vi.fn()}))
compileRender(NoteEditor,new URL('./NoteEditor.vue',import.meta.url))
const anchor:Anchor={bookId:'source-a',branchId:'line-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:1,end:3,quoteHash:'emoji-hash',offsetUnit:'utf16'}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,saved=vi.fn()
function mount(at=anchor,note?:Bookmark){root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h(NoteEditor,{anchor:at,note,onSaved:saved})}));provideSfc(app);app.mount(root)}
function input(value:string){descendants(root,item=>item.tag==='textarea')[0].props['onUpdate:modelValue'](value)}
function submit(){descendants(root,item=>item.tag==='form')[0].props.onSubmit({preventDefault(){}})}
beforeEach(()=>{state.owner='owner-a';state.epoch=0;state.clearFails=false;state.values.clear();state.route=reactive({fullPath:'/read'});state.store=reactive({online:true,message:vi.fn()});vi.mocked(api).mockReset();vi.mocked(queueNote).mockReset();vi.mocked(cacheGet).mockReset().mockImplementation(async(key:string)=>structuredClone(state.values.get(key)) as any);saved=vi.fn();vi.stubGlobal('document',{activeElement:null});vi.stubGlobal('window',new EventTarget());vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
it('keeps two selected ranges in the same paragraph in independent drafts',async()=>{
  mount();await flush();input('这一个选区的想法');await flush();app!.unmount();app=undefined
  mount({...anchor,start:4,end:6,quoteHash:'different-range-hash'});await flush();expect(descendants(root,item=>item.tag==='textarea')[0].value).toBe('');input('另一个选区的想法');await flush();app!.unmount();app=undefined
  mount();await flush();expect(descendants(root,item=>item.tag==='textarea')[0].value).toBe('这一个选区的想法');expect(state.values.size).toBe(2)
})
it('retries the exact persisted create and selected anchor after an unknown response',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new ApiError('NETWORK_UNAVAILABLE','unknown',0)).mockResolvedValueOnce({id:'remote-note',anchor,note:'保留选区',version:1,status:'valid'})
  mount();await flush();input('保留选区');await flush();submit();await flush();expect(saved).not.toHaveBeenCalled();expect(text(root)).toContain('重试保存同一条笔记');submit();await flush()
  expect(vi.mocked(api).mock.calls).toEqual(Array(2).fill(['/books/source-a/bookmarks',{method:'POST',key:'00000000-a111-b222-c333-000000000006',body:{anchor,note:'保留选区'}}]));expect(saved).toHaveBeenCalledTimes(1)
})
it('does not issue another create when only clearing the local draft failed',async()=>{
  vi.mocked(api).mockResolvedValue({id:'remote-note',anchor,note:'已保存正文选区',version:1,status:'valid'});state.clearFails=true
  mount();await flush();input('已保存正文选区');await flush();submit();await flush();expect(text(root)).toContain('完成草稿清理');expect(saved).not.toHaveBeenCalled()
  state.clearFails=false;submit();await flush();expect(api).toHaveBeenCalledTimes(1);expect(saved).toHaveBeenCalledTimes(1)
})
it('preserves the original request key when an unknown online save is retried offline',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new ApiError('NETWORK_UNAVAILABLE','response lost',0))
  vi.mocked(queueNote).mockResolvedValue({id:'local-note',anchor,note:'不能重复的想法',version:1,status:'pending_sync'})
  mount();await flush();input('不能重复的想法');await flush();submit();await flush()
  state.store.online=false;submit();await flush()
  expect(queueNote).toHaveBeenCalledExactlyOnceWith(anchor,'不能重复的想法',undefined,'00000000-a111-b222-c333-000000000006',true)
  expect(api).toHaveBeenCalledTimes(1);expect(saved).toHaveBeenCalledTimes(1)
})
it('only clears the confirmed draft when cleanup is retried after going offline',async()=>{
  vi.mocked(api).mockResolvedValue({id:'remote-note',anchor,note:'已保存的想法',version:1,status:'valid'});state.clearFails=true
  mount();await flush();input('已保存的想法');await flush();submit();await flush()
  expect(text(root)).toContain('完成草稿清理');state.store.online=false;state.clearFails=false;submit();await flush()
  expect(queueNote).not.toHaveBeenCalled();expect(api).toHaveBeenCalledTimes(1);expect(saved).toHaveBeenCalledTimes(1)
})
it('restores an already queued offline save after draft cleanup failed and the editor reopened',async()=>{
  state.store.online=false;state.clearFails=true
  vi.mocked(queueNote).mockResolvedValue({id:'local-note',anchor,note:'可靠的本机笔记',version:1,status:'pending_sync'})
  mount();await flush();input('可靠的本机笔记');await flush();submit();await flush()
  expect(text(root)).toContain('完成草稿清理');expect([...state.values.values()]).toMatchObject([{confirmed:{id:'local-note',status:'pending_sync'},pending:{attempted:false}}])
  app!.unmount();app=undefined;mount();await flush();state.clearFails=false;submit();await flush()
  expect(queueNote).toHaveBeenCalledTimes(1);expect(api).not.toHaveBeenCalled();expect(saved).toHaveBeenCalledTimes(1)
})
it('preserves local text after a remote CAS conflict until an explicit choice',async()=>{
  const note={id:'remote-note',anchor,note:'初始想法',status:'valid',version:1}
  vi.mocked(api).mockRejectedValueOnce(new ApiError('NOTE_CONFLICT','another device',409)).mockResolvedValueOnce({...note,note:'另一设备的想法',version:2})
  mount(anchor,note);await flush();input('我的未保存想法');await flush();submit();await flush()
  expect(descendants(root,item=>item.tag==='textarea')[0].value).toBe('我的未保存想法');expect(text(root)).toContain('另一设备的想法');expect(saved).not.toHaveBeenCalled()
  expect([...state.values.values()]).toMatchObject([{text:'我的未保存想法',baseVersion:1}])
})

it('keeps the input disabled until an existing draft is restored',async()=>{
  const draft=deferred<any>();vi.mocked(cacheGet).mockReturnValueOnce(draft.promise)
  mount();await flush();expect(descendants(root,item=>item.tag==='textarea')[0].props.disabled).toBe(true);expect(api).not.toHaveBeenCalled()
  draft.resolve({text:'此前可靠保存的想法',pending:null,baseVersion:1});await flush()
  expect(descendants(root,item=>item.tag==='textarea')[0].value).toBe('此前可靠保存的想法');expect(descendants(root,item=>item.tag==='textarea')[0].props.disabled).toBe(false)
  input('恢复后输入的新想法');await flush();expect([...state.values.values()]).toMatchObject([{text:'恢复后输入的新想法'}])
})
it('preserves text and never resubmits a create whose bookmark was authoritatively deleted',async()=>{
  vi.mocked(api).mockRejectedValue(new ApiError('BOOKMARK_DELETED','这条笔记已删除',409))
  mount();await flush();input('尚未确认的想法仍需保留');await flush();submit();await flush()
  expect(descendants(root,item=>item.tag==='textarea')[0].value).toBe('尚未确认的想法仍需保留')
  expect(text(root)).toContain('复制本机文字')
  submit();await flush();expect(api).toHaveBeenCalledTimes(1)
  expect([...state.values.values()]).toMatchObject([{text:'尚未确认的想法仍需保留'}])
  app!.unmount();app=undefined;mount();await flush();expect(text(root)).toContain('复制本机文字');submit();await flush();expect(api).toHaveBeenCalledTimes(1)
})
it('preserves a pending local edit when deletion arrives while its draft is still loading',async()=>{
  const restore=deferred<any>();vi.mocked(cacheGet).mockReturnValueOnce(restore.promise);const note={id:'remote-note',anchor,note:'原账号文字',status:'valid',version:1}
  mount(anchor,note);await flush();window.dispatchEvent(new CustomEvent('reader-bookmark-deleted',{detail:{bookId:anchor.bookId,bookmarkId:note.id}}))
  restore.resolve({text:'删除到来之前的本机文字',pending:null,baseVersion:1});await flush()
  expect(descendants(root,item=>item.tag==='textarea')[0].value).toBe('删除到来之前的本机文字');expect(text(root)).toContain('复制本机文字');submit();await flush();expect(api).not.toHaveBeenCalled()
})
