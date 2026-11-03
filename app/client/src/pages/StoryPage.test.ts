import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,reactive,onUnmounted} from 'vue'
import StoryPage from './StoryPage.vue'
import {api,ApiError} from '../api'
import {getStory} from '../api/stories'
import type {StoryDetail} from '../api/stories'
import {compileRender,renderer,node,descendants,text,flush,deferred,provideSfc,type Host} from '../test/host-renderer'

const fixture=vi.hoisted(()=>({route:null as any,epoch:0,push:vi.fn(),app:{online:true,worldEnabled:true,message:vi.fn()}}))
vi.mock('vue-router',()=>({useRoute:()=>fixture.route,useRouter:()=>({push:fixture.push,replace:fixture.push})}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>fixture.epoch,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../api/stories',()=>({getStory:vi.fn(),contextRoute:vi.fn(),rememberContext:vi.fn()}))
vi.mock('../world/api',()=>({worldApi:vi.fn()}))
vi.mock('../components/BookRemoval.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
// The launch form has its own API/navigation tests; keep these CAS/export tests
// scoped to the parent's management requests.
vi.mock('../components/WorkEntry.vue',()=>({default:defineComponent({props:{story:Object},setup:p=>()=>h('section',(p.story as StoryDetail).title)})}))
// Existing Story CAS tests isolate the newly added backup child. Its actual
// setup/template and streaming protocol run in PrivateBackup/backups suites.
vi.mock('../components/PrivateBackup.vue',()=>({default:defineComponent({props:{mode:String,storyId:String},setup:p=>()=>h('section',{'data-story-backup':p.storyId},'私人备份')})}))
vi.mock('../stores/app',()=>({useApp:()=>fixture.app}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const path=fixture.route.fullPath,epoch=fixture.epoch;return{valid:()=>!disposed&&path===fixture.route.fullPath&&epoch===fixture.epoch}}}}))
vi.mock('../components/ReadingExport.vue',()=>({default:defineComponent({props:{scope:Object},setup:p=>()=>h('section',{'data-reading-export-scope':JSON.stringify(p.scope)},'阅读导出')})}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({props:{title:String},emits:['close'],setup:(p,{emit,slots})=>()=>h('section',{role:'dialog','aria-label':p.title},[h('button',{'aria-label':'关闭',onClick:()=>emit('close')},'关闭'),slots.default?.()])})}))
vi.mock('../components/StoryImages.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('section',slots.fallback?.())})}))
compileRender(StoryPage,new URL('./StoryPage.vue',import.meta.url))
const a='book-'+'a'.repeat(32),b='book-'+'b'.repeat(32)
const story=(id=a,version=1,title=id===a?'故事甲':'故事乙'):StoryDetail=>({id,kind:'book',version,title,status:'active',source:null,resume:{read:{version:0,context:null,available:false,updatedAt:null},play:{version:0,context:null,available:false,updatedAt:null},chat:{version:0,context:null,available:false,updatedAt:null}},worlds:[],canCreateWorld:true})
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
const find=(match:(element:Host)=>boolean)=>descendants(root,match)[0]
const button=(label:string)=>find(element=>element.tag==='button'&&(element.props['aria-label']===label||text(element)===label))!
const state=()=> (app as any)._instance.setupState
function route(id:string){fixture.route.params.storyId=id;fixture.route.fullPath='/stories/'+id}
function mount(){root=node('root');app=renderer.createApp(StoryPage);provideSfc(app);app.component('RouterLink',defineComponent({props:{to:[String,Object]},setup:(p,{slots})=>()=>h('a',{href:p.to},slots.default?.())}));app.mount(root)}
beforeEach(()=>{vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{activeElement:null});fixture.epoch=0;fixture.app.online=true;fixture.route=reactive({params:{storyId:a},fullPath:'/stories/'+a});fixture.push.mockReset();vi.mocked(api).mockReset();vi.mocked(getStory).mockReset().mockImplementation(async id=>story(id));})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

it('does not keep the next story busy or replace its draft when a previous save arrives late',async()=>{
  const old=deferred<any>();vi.mocked(api).mockReturnValueOnce(old.promise)
  mount();await flush();button('管理故事').props.onClick();await flush();state().name='旧草稿';button('保存名称').props.onClick();await flush()
  route(b);await flush();button('管理故事').props.onClick();await flush();state().name='新草稿';old.resolve({});await flush()
  expect(state().name).toBe('新草稿');expect(state().menu).toBe(true);expect(state().busy).toBe(false);expect(state().detail.id).toBe(b)
})
it.each(['success','failure'])('an older %s cannot close or corrupt a reopened management sheet',async(result)=>{
  const old=deferred<any>();vi.mocked(api).mockReturnValueOnce(old.promise)
  mount();await flush();button('管理故事').props.onClick();await flush();button('保存名称').props.onClick();await flush();button('关闭').props.onClick();await flush();button('管理故事').props.onClick();await flush();state().name='重新打开后的草稿'
  if(result==='success')old.resolve({});else old.reject(new ApiError('VERSION_CONFLICT','旧冲突',409));await flush()
  expect(state().name).toBe('重新打开后的草稿');expect(state().menu).toBe(true);expect(state().busy).toBe(false);expect(text(root)).not.toContain('旧冲突')
})
it('requires an explicit conflict refresh, keeps the local name and saves using the new version',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new ApiError('VERSION_CONFLICT','另一设备已修改名称',409)).mockResolvedValueOnce(story(a,8,'服务器名称')).mockResolvedValueOnce({})
  mount();await flush();button('管理故事').props.onClick();await flush();state().name='本机保留的名称';button('保存名称').props.onClick();await flush()
  expect(button('载入最新信息')).toBeDefined();expect(button('保存名称').props.disabled).toBe(true);expect(api).toHaveBeenCalledTimes(1)
  button('载入最新信息').props.onClick();await flush();expect(state().name).toBe('本机保留的名称');expect(text(root)).toContain('最新名称：服务器名称')
  button('保存名称').props.onClick();await flush();expect(vi.mocked(api).mock.calls[2]![1]?.body).toEqual({expectedVersion:8,title:'本机保留的名称'});expect(state().menu).toBe(false)
})
it('ignores an older refresh when the editor is closed and reopened',async()=>{
  const old=deferred<any>();vi.mocked(api).mockRejectedValueOnce(new ApiError('VERSION_CONFLICT','冲突',409)).mockReturnValueOnce(old.promise)
  mount();await flush();button('管理故事').props.onClick();await flush();button('保存名称').props.onClick();await flush();button('载入最新信息').props.onClick();await flush()
  button('关闭').props.onClick();await flush();button('管理故事').props.onClick();await flush();state().name='最新本机草稿';old.resolve(story(a,10,'旧刷新结果'));await flush()
  expect(state().name).toBe('最新本机草稿');expect(state().detail.version).toBe(1);expect(state().busy).toBe(false)
})
it('ignores a delayed story response after the account changes',async()=>{
  const old=deferred<StoryDetail>();vi.mocked(getStory).mockReturnValueOnce(old.promise)
  mount();await flush();fixture.epoch++;old.resolve(story());await flush();expect(state().detail).toBeNull();expect(text(root)).not.toContain('故事甲')
})
it('clears an entry deleted in another tab and ignores its late in-flight story response',async()=>{
  const old=deferred<StoryDetail>();vi.mocked(getStory).mockReturnValueOnce(old.promise);mount();await flush()
  const event=new Event('reader-book-deleted');Object.defineProperty(event,'detail',{value:{bookId:a.slice(5)}});window.dispatchEvent(event);await flush()
  expect(fixture.push).toHaveBeenCalledWith('/library');expect(state().detail).toBeNull()
  old.resolve(story());await flush();expect(state().detail).toBeNull();expect(text(root)).not.toContain('故事甲')
})
it('separates reading export from private backup and unmounts the old backup on a story route change',async()=>{
  mount();await flush();button('管理故事').props.onClick();await flush();expect(button('导出阅读文件 · TXT / EPUB').props).toHaveProperty('disabled');button('导出私人完整备份').props.onClick();await flush();expect(find(n=>n.props['data-story-backup']===a)).toBeDefined();expect(api).not.toHaveBeenCalled();route(b);await flush();expect(descendants(root,n=>!!n.props['data-story-backup'])).toHaveLength(0);button('管理故事').props.onClick();await flush();button('导出私人完整备份').props.onClick();await flush();expect(find(n=>n.props['data-story-backup']===b)).toBeDefined()
})
it('opens reading export with the exact validated reader scope and clears it on story navigation',async()=>{
  const value=story(),scope={kind:'book',bookId:a.slice(5),branchId:'c'.repeat(32),revisionId:'d'.repeat(32)}
  value.resume.read={version:1,available:true,updatedAt:null,context:{storyId:a,mode:'read',perspective:'reader',entryOrigin:'library',bookId:scope.bookId,readerBranchId:scope.branchId,anchor:{bookId:scope.bookId,branchId:scope.branchId,revisionId:scope.revisionId,chapterId:'e'.repeat(32),blockId:'f'.repeat(32),start:0,end:0,offsetUnit:'utf16',quoteHash:'hash'}}};vi.mocked(getStory).mockResolvedValueOnce(value)
  mount();await flush();button('管理故事').props.onClick();await flush();expect(button('导出阅读文件 · TXT / EPUB').props.disabled).toBeFalsy();button('导出阅读文件 · TXT / EPUB').props.onClick();await flush();expect(find(n=>n.props['data-reading-export-scope'])?.props['data-reading-export-scope']).toBe(JSON.stringify(scope));expect(api).not.toHaveBeenCalled();route(b);await flush();expect(find(n=>n.props['data-reading-export-scope'])).toBeUndefined()
})
