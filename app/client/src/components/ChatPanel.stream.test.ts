import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,reactive} from 'vue'
import ChatPanel from './ChatPanel.vue'
import PublicStreamText from './PublicStreamText.vue'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({api:vi.fn(),allPages:vi.fn(),watch:vi.fn(),epoch:0,owner:'owner'}))
vi.mock('vue-router',()=>({useRoute:()=>({query:{}})}))
vi.mock('../api',()=>({api:mock.api,allPages:mock.allPages,newKey:()=> 'one-key',authGeneration:()=>mock.epoch,ApiError:class extends Error{code=''}}))
vi.mock('../stores/app',()=>({useApp:()=>reactive({online:true,user:{id:'owner'}})}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>null),cacheSet:vi.fn(async()=>{}),accountId:()=>mock.owner,appForeground:()=>true}))
vi.mock('../api/task-watch',()=>({watchTask:mock.watch,taskFinished:(t:{state:string})=>t.state==='succeeded'}))
compileRender(ChatPanel,new URL('./ChatPanel.vue',import.meta.url))
compileRender(PublicStreamText,new URL('./PublicStreamText.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
const session={id:'session',branchId:'branch',pinnedRevisionId:'revision',knowledgeMode:'read',followProgress:false,pinnedAnchor:{bookId:'book'},characterName:'林渡',branchName:'正文'} as any
const initial={id:'task',taskId:'task',state:'running',eventCursor:1,persistenceState:'pending',result:{},replyStream:{sessionId:'session',turnId:'turn',userMessage:'已发送',text:'你😀',version:1,status:'streaming',contentId:'content',chapterIndex:1,streamVersion:1,snapshotVersion:1,durability:'journaled'}} as any
beforeEach(()=>{mock.api.mockReset().mockResolvedValue({...session,pendingTaskId:'task'});mock.allPages.mockReset().mockResolvedValue([{id:'user',role:'user',turnId:'turn',taskId:'task',text:'已发送'}]);mock.watch.mockReset().mockReturnValue(()=>{});mock.epoch=0;mock.owner='owner';vi.stubGlobal('document',Object.assign(new EventTarget(),{hidden:false,activeElement:null}));vi.stubGlobal('window',new EventTarget());vi.stubGlobal('requestAnimationFrame',()=>1);vi.stubGlobal('cancelAnimationFrame',()=>{})})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
async function mount(){root=node('root');app=renderer.createApp(ChatPanel,{session});provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root);await flush();expect(mock.watch.mock.calls.length,text(root)).toBe(1);await mock.watch.mock.calls[0][1](initial);await flush()}
const delta=(seq=2,payload={})=>({taskId:'task',seq,type:'text_delta',payload:{contentId:'content',chapterIndex:1,streamVersion:1,startUtf16:3,delta:'好',durability:'journaled',...payload}})
it('actual SFC consumes typed text without remounting composer or clearing next draft',async()=>{
  await mount();const textarea=descendants(root,n=>n.tag==='textarea')[0];textarea.props['onUpdate:modelValue']('下一条未提交稿');await flush();Object.defineProperty(document,'activeElement',{value:textarea,configurable:true})
  const event=mock.watch.mock.calls[0][3];expect(event(delta())).toBe(true);await flush();await new Promise(resolve=>setTimeout(resolve,25));await flush()
  expect(text(root)).toContain('你😀好');expect(descendants(root,n=>n.tag==='textarea')[0]).toBe(textarea);expect(textarea.value).toBe('下一条未提交稿');expect(document.activeElement).toBe(textarea)
  expect(mock.api).toHaveBeenCalledTimes(1)
})
it('actual SFC rejects obsolete account callbacks and unknown content without text pollution',async()=>{
  await mount();const event=mock.watch.mock.calls[0][3]
  expect(event(delta(2,{contentId:'other'}))).toBe(false);await flush();expect(text(root)).not.toContain('你😀好')
  mock.epoch++;expect(event(delta())).toBe(false);await flush();expect(text(root)).not.toContain('你😀好')
})

const recovered={id:'old-task',taskId:'old-task',sessionId:'session',turnId:'old-turn',userMessage:'恢复前已发送的问题',text:'恢复的回复片段😀',historical:true,durability:'unconfirmed',createdAt:'2026-10-04T00:00:00Z'}
async function mountHistory(){root=node('root');app=renderer.createApp(ChatPanel,{session});provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root);await flush()}
it('restored partial reply is actually readable without pending task, resend, or replacing saved messages',async()=>{
  mock.api.mockResolvedValue({...session,pendingTaskId:null,receivedHistory:true})
  mock.allPages.mockImplementation(async(path:string)=>path.endsWith('/received-replies')?[recovered]:[{id:'saved',role:'assistant',turnId:'canonical',taskId:'saved-task',text:'已有正式回复保持原文',status:'saved'}])
  await mountHistory();expect(text(root)).toContain('恢复的回复片段😀');expect(text(root)).toContain('恢复前已发送的问题');expect(text(root)).toContain('已有正式回复保持原文');expect(text(root)).toContain('从备份恢复')
  expect(mock.watch).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledOnce();expect(mock.api.mock.calls.every(([,options])=>!options?.method)).toBe(true)
})
it('late restored receipts cannot appear after account change or from a different session',async()=>{
  mock.api.mockResolvedValue({...session,pendingTaskId:null,receivedHistory:true})
  let resolve!: (rows:any[])=>void
  mock.allPages.mockImplementation((path:string)=>path.endsWith('/received-replies')?new Promise(done=>{resolve=done}):Promise.resolve([]))
  await mountHistory();mock.epoch++;resolve([recovered,{...recovered,id:'foreign',sessionId:'foreign-session',text:'跨会话的正文'}]);await flush()
  expect(text(root)).not.toContain('恢复的回复片段😀');expect(text(root)).not.toContain('跨会话的正文');expect(mock.watch).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledOnce()
})
