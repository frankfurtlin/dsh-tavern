import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,reactive,onUnmounted} from 'vue'
import ConversationsPage from './ConversationsPage.vue'
import {allPages,api} from '../api'
import {rememberContext} from '../api/stories'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'

const fixture=vi.hoisted(()=>({route:null as any,epoch:0}))
vi.mock('vue-router',()=>({useRoute:()=>fixture.route,useRouter:()=>({currentRoute:{value:fixture.route},replace:vi.fn()})}))
vi.mock('../api',()=>({allPages:vi.fn(),api:vi.fn()}))
vi.mock('../api/stories',()=>({rememberContext:vi.fn(),sessionContext:(session:{id:string},storyId:string)=>({sessionId:session.id,storyId})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const path=fixture.route.fullPath,epoch=fixture.epoch;return{valid:()=>!disposed&&path===fixture.route.fullPath&&epoch===fixture.epoch}}}}))
vi.mock('../components/ChatPanel.vue',()=>({default:defineComponent({props:{session:Object},setup:p=>()=>h('div',p.session?.id)})}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({props:{title:String},setup:(p,{slots})=>()=>h('section',{role:'dialog'},[p.title,slots.default?.()])})}))
compileRender(ConversationsPage,new URL('./ConversationsPage.vue',import.meta.url))
const book='book-'+'a'.repeat(32),world='world-'+'b'.repeat(32)
const sessions=[{id:'session-a',characterName:'林渡',bookTitle:'原文',branchName:'原文',pinnedLocation:'第一章',pendingTaskId:null}] as any
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){root=node('root');app=renderer.createApp(ConversationsPage);provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
function route(story:string){fixture.route.query.story=story;fixture.route.fullPath='/conversations?session=session-a&story='+story}
beforeEach(()=>{
  vi.mocked(api).mockReset().mockResolvedValue({items:[],nextCursor:null});
  fixture.epoch=0;fixture.route=reactive({query:{session:'session-a',story:book},fullPath:'/conversations?session=session-a&story='+book})
  vi.mocked(allPages).mockReset().mockResolvedValue(sessions);vi.mocked(rememberContext).mockReset().mockResolvedValue({version:1,context:null,updatedAt:null,available:false})
  vi.stubGlobal('innerWidth',375);vi.stubGlobal('window',new EventTarget())
  vi.stubGlobal('document',{activeElement:null,getElementById:()=>null})
  vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}})
  vi.stubGlobal('requestAnimationFrame',()=>1);vi.stubGlobal('cancelAnimationFrame',()=>{})
})
it('removes deleted source sessions immediately and keeps an unrelated active conversation through a late refresh',async()=>{
  const old={...sessions[0],bookId:'source-a'},other={...sessions[0],id:'session-b',bookId:'source-b',characterName:'梅舟'}
  vi.mocked(allPages).mockResolvedValueOnce([old,other]);mount();await flush()
  descendants(root,item=>item.tag==='button'&&text(item).includes('梅舟'))[0].props.onClick();await flush()
  const pending=deferred<any>();vi.mocked(allPages).mockReturnValueOnce(pending.promise)
  const event=new Event('reader-book-deleted');Object.defineProperty(event,'detail',{value:{bookId:'source-a'}});window.dispatchEvent(event);await flush()
  expect(text(root)).toContain('session-b');expect(text(root)).not.toContain('林渡')
  pending.resolve([other]);await flush();expect(text(root)).toContain('session-b');expect(text(root)).not.toContain('林渡')
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

it('revalidates the same conversation when only its story origin changes on the mounted page',async()=>{
  mount();await flush();expect(text(root)).toContain('session-a');expect(rememberContext).toHaveBeenCalledTimes(1)
  route(world);await flush()
  expect(allPages).toHaveBeenCalledTimes(2);expect(rememberContext).toHaveBeenCalledTimes(2)
  expect(vi.mocked(rememberContext).mock.calls.map(([context])=>context)).toEqual([{sessionId:'session-a',storyId:book},{sessionId:'session-a',storyId:world}])
})
it('ignores the old list after changing the story origin while it is still loading',async()=>{
  const old=deferred<any>();vi.mocked(allPages).mockReturnValueOnce(old.promise)
  mount();await flush();route(world);await flush();expect(text(root)).toContain('session-a')
  old.resolve([{...sessions[0],id:'wrong-old-session'}]);await flush()
  expect(text(root)).not.toContain('wrong-old-session');expect(rememberContext).toHaveBeenCalledTimes(1)
  expect(vi.mocked(rememberContext).mock.calls[0][0]).toEqual({sessionId:'session-a',storyId:world})
})
it('keeps a newer manual selection when the current list request resolves with a different query session',async()=>{
  const other={...sessions[0],id:'session-b',characterName:'梅舟'},items=[...sessions,other]
  vi.mocked(allPages).mockResolvedValueOnce(items)
  mount();await flush()
  const pending=deferred<any>();vi.mocked(allPages).mockReturnValueOnce(pending.promise)
  route(world);await flush()
  descendants(root,item=>item.tag==='button'&&text(item).includes('梅舟'))[0].props.onClick()
  await flush();expect(text(root)).toContain('session-b')
  const before=vi.mocked(rememberContext).mock.calls.length
  pending.resolve(items);await flush()
  expect(text(root)).toContain('session-b');expect(text(root)).not.toContain('session-a')
  expect(rememberContext).toHaveBeenCalledTimes(before)
  expect(vi.mocked(rememberContext).mock.calls.at(-1)?.[0]).toEqual({sessionId:'session-b',storyId:world})
})

it('opens world character conversations separately from book sessions',async()=>{
  vi.mocked(api).mockResolvedValue({items:[{id:'world-chat',characterName:'福尔摩斯',worldTitle:'蓝宝石案',snapshot:{lineName:'善后',timeLabel:'案后'},pendingTaskId:null}],nextCursor:null})
  mount();await flush();expect(text(root)).toContain('福尔摩斯');expect(text(root)).toContain('独立对话');expect(text(root)).toContain('session-a')
  expect(api).toHaveBeenCalledWith('/play-controls/world-chats?status=active&q=')
})

it('keeps book conversations usable when the independent world list fails',async()=>{
  vi.mocked(api).mockRejectedValue(new Error('世界对话暂时不可用'))
  mount();await flush();expect(text(root)).toContain('session-a');expect(text(root)).toContain('世界对话暂时不可用')
  expect(rememberContext).toHaveBeenCalledTimes(1)
})
