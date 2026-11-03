import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import NotebookPage from './NotebookPage.vue'
import BookmarkRemoval from '../components/BookmarkRemoval.vue'
import {api,allPages} from '../api'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const state=vi.hoisted(()=>({owner:'owner-a',epoch:0,route:null as any,store:null as any,values:new Map<string,unknown>(),deleted:new Set<string>()}))
vi.mock('vue-router',()=>({useRoute:()=>state.route,useRouter:()=>({push:vi.fn()})}))
vi.mock('../api',()=>({api:vi.fn(),allPages:vi.fn(),authGeneration:()=>state.epoch,newKey:()=> '00000000-0000-4000-8000-000000000009',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({accountId:()=>state.owner,backendBase:()=> 'https://fixture.invalid'}))
vi.mock('../platform/bookmark-cache',()=>({isBookmarkDeleted:(book:string,id:string)=>state.deleted.has(book+':'+id),bookmarkScope:()=>()=>true,bookmarkDeletionRequest:async(book:string,id:string)=>state.values.get(book+':'+id),saveBookmarkDeletionRequest:async(value:unknown,book:string,id:string)=>{state.values.set(book+':'+id,value)},markBookmarkDeleted:(book:string,id:string)=>{state.deleted.add(book+':'+id);window.dispatchEvent(new CustomEvent('reader-bookmark-deleted',{detail:{bookId:book,bookmarkId:id}}))}}))
vi.mock('../stores/app',()=>({useApp:()=>state.store}))
vi.mock('../components/NoteEditor.vue',()=>({default:defineComponent({setup:()=>()=>h('div')})}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({emits:['close'],setup:(_,{slots,emit})=>()=>h('section',[h('button',{onClick:()=>emit('close')},'关闭面板'),slots.default?.()])})}))
compileRender(NotebookPage,new URL('./NotebookPage.vue',import.meta.url));compileRender(BookmarkRemoval,new URL('../components/BookmarkRemoval.vue',import.meta.url))
const anchor={bookId:'book-a',branchId:'line-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:1,end:3,quoteHash:'range-hash',offsetUnit:'utf16' as const}
const notes=['a','b'].map((id,i)=>({id:'note-'+id,anchor,note:'独立笔记'+id,status:'valid',version:4+i,bookTitle:'合成书',branchName:'原文',location:'第一章',createdAt:''}))
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){root=node('root');app=renderer.createApp(NotebookPage);provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
function click(label:string){const button=descendants(root,item=>item.tag==='button'&&text(item)===label)[0];expect(button).toBeDefined();return button.props.onClick()}
function choose(id:string){const card=descendants(root,item=>item.tag==='article'&&text(item).includes('独立笔记'+id))[0];descendants(card,item=>item.tag==='button'&&item.props['aria-label']==='删除笔记')[0].props.onClick()}
beforeEach(()=>{state.owner='owner-a';state.epoch=0;state.values.clear();state.deleted.clear();state.route=reactive({fullPath:'/notebook'});state.store=reactive({online:true,message:vi.fn()});vi.mocked(api).mockReset();vi.mocked(allPages).mockReset().mockResolvedValue(notes);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
it('uses the selected note version only after the actual notebook confirmation and retains other notes',async()=>{
  vi.mocked(api).mockResolvedValueOnce({found:false}).mockResolvedValueOnce({deleted:true,bookId:'book-a',bookmarkId:'note-a',version:5,receiptId:'receipt-a',deletedAt:'2026-10-05T01:00:00Z'})
  mount();await flush();choose('a');await flush();expect(api).not.toHaveBeenCalled();await click('确认删除笔记');await flush()
  expect(vi.mocked(api).mock.calls[1]).toEqual(['/books/book-a/bookmarks/note-a',{method:'DELETE',key:'00000000-0000-4000-8000-000000000009',body:{expectedVersion:4}}]);expect(text(root)).not.toContain('独立笔记a');expect(text(root)).toContain('独立笔记b')
})
it('does not close the newly selected note panel when the previous deletion arrives late',async()=>{
  const response=deferred<any>();vi.mocked(api).mockResolvedValueOnce({found:false}).mockReturnValueOnce(response.promise)
  mount();await flush();choose('a');await flush();const action=click('确认删除笔记');await flush();click('关闭面板');await flush();choose('b');await flush()
  response.resolve({deleted:true,bookId:'book-a',bookmarkId:'note-a',version:5,receiptId:'receipt-a',deletedAt:'2026-10-05T01:00:00Z'});await action;await flush()
  expect(descendants(root,item=>item.tag==='button'&&text(item)==='确认删除笔记')).toHaveLength(1);expect(text(root)).toContain('独立笔记b');expect(text(root)).not.toContain('独立笔记a');expect(state.store.message).not.toHaveBeenCalled()
})
