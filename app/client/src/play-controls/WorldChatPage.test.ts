import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import WorldChatPage from './WorldChatPage.vue'
import {api} from '../api'
import {worldApi} from '../world/api'
import {authorizedBlob,saveBlob} from '../platform/download'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const root='/play-controls/world-chats/chat-1'
const harness=vi.hoisted(()=>({route:{params:{sessionId:'chat-1'},query:{} as Record<string,string>},push:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>harness.route,useRouter:()=>({push:harness.push})}))
vi.mock('../api',()=>({api:vi.fn(),newKey:()=> 'fixture-key'}))
vi.mock('../world/api',()=>({worldApi:vi.fn()}))
vi.mock('../platform/bridge',()=>({cacheGet:async()=>null,cacheSet:async()=>{}}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../stores/app',()=>({useApp:()=>({online:true,user:{username:'reader'},message:vi.fn()})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{ArrowUp:stub,Square:stub,SlidersHorizontal:stub,RefreshCw:stub}})
vi.mock('../workspace/PageHeader.vue',()=>({default:defineComponent({props:{title:String,eyebrow:String,description:String},setup:(props,{slots})=>()=>h('header',[props.title,slots.default?.()])})}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({props:{title:String},setup:(_props,{slots})=>()=>h('section',slots.default?.())})}))
vi.mock('../components/PublicStreamText.vue',()=>({default:defineComponent({props:{text:String},setup:(props)=>()=>h('p',props.text)})}))
vi.mock('../components/WorldAppearanceProvider.vue',()=>({default:defineComponent({setup:(_props,{slots})=>()=>h('div',slots.default?.())})}))
vi.mock('../components/CharacterAvatar.vue',()=>({default:defineComponent({props:{entityId:String,name:String},setup:(props)=>()=>h('span',props.name)})}))
vi.mock('../components/AccountAvatar.vue',()=>({default:defineComponent({setup:()=>()=>h('span','你')})}))
vi.mock('./ModelControlButton.vue',()=>({default:defineComponent({setup:(_props,{expose})=>{expose({refresh:()=>{}});return()=>h('span')}})}))
vi.mock('./PlaySettings.vue',()=>({default:defineComponent({setup:(_props,{expose})=>{expose({requestClose:async()=>true});return()=>h('div')}})}))
vi.mock('../components/PrivateBackup.vue',()=>({default:defineComponent({setup:()=>()=>h('div')})}))
vi.mock('../platform/download',()=>({authorizedBlob:vi.fn(async()=>new Blob(['{}'])),saveBlob:vi.fn(),safeFileName:(name:string,fallback:string)=>name||fallback}))
vi.mock('../experience/TaskProgress.vue',()=>({default:defineComponent({props:{progress:Object},setup:()=>()=>h('div',{'data-progress':'1'})})}))
compileRender(WorldChatPage,new URL('./WorldChatPage.vue',import.meta.url))

const snapshot={worldTitle:'雨夜',lineName:'主线',timeLabel:'第一夜',character:{id:'c1',name:'林渡',description:'旧书摊主',traits:''},knownFacts:[]}
type Msg={id:string;taskId:string;turnId:string;sequence:number;role:string;text:string;status:string;checkpointId:string;modelName:string}
const userMessage:Msg={id:'m1',taskId:'t0',turnId:'turn0',sequence:1,role:'user',text:'你好',status:'saved',checkpointId:'cp1',modelName:''}
const pendingMessage=(text=''):Msg=>({id:'m2',taskId:'t1',turnId:'turn1',sequence:2,role:'assistant',text,status:'pending',checkpointId:'cp1',modelName:''})
const savedMessage=(text='风起了'):Msg=>({id:'m2',taskId:'t1',turnId:'turn1',sequence:2,role:'assistant',text,status:'saved',checkpointId:'cp1',modelName:'模型 A'})
const chat=(over:Record<string,unknown>={})=>({id:'chat-1',worldId:'w1',worldTitle:'雨夜',lineId:'l1',checkpointId:'cp1',characterId:'c1',characterName:'林渡',name:'林渡 · 单独聊聊',version:1,pendingTaskId:null,createdAt:'2026-01-01T00:00:00',status:'active',snapshot,messages:[userMessage],...over})
const task=(state:string,persistenceState='saved')=>({id:'t1',type:'world_chat',state,persistenceState,phase:'writing',errorCode:null,result:{},eventSeq:1,providerState:'received',stream:{}})

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host,current:()=>unknown,postError:Error|null=null,patchError:Error|null=null
function mount(){host=node('root');app=renderer.createApp(WorldChatPage);provideSfc(app);app.component('RouterLink',defineComponent({props:{to:[String,Object]},setup:(props,{slots})=>()=>h('a',{to:props.to},slots.default?.())}));app.mount(host)}
function fire(target:Host,key:string,payload:unknown){const handler=target.props[key];Array.isArray(handler)?handler.forEach(fn=>fn(payload)):handler?.(payload)}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).includes(label))[0]}
beforeEach(()=>{
  vi.useFakeTimers()
  vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn()})
  vi.stubGlobal('document',{activeElement:null})
  postError=null;patchError=null
  harness.push.mockReset().mockResolvedValue(undefined)
  current=()=>chat()
  vi.mocked(api).mockReset().mockImplementation(async (path:string,options?:{method?:string;body?:unknown})=>{
    if(path===root&&!options?.method)return current() as any
    if(path===root+'/regenerate'&&options?.method==='POST'){if(postError)throw postError;return {taskId:'t2'} as any}
    if(path.startsWith(root+'/messages/')&&options?.method==='PATCH'){if(patchError)throw patchError;return {taskId:'t3',sequence:1} as any}
    if(path.startsWith(root+'/messages?'))return {items:[]} as any
    if(path===root+'/source-preview')return {fromTime:'a',toTime:'b',changed:false,addedFacts:[]} as any
    throw new Error('unexpected request '+path)
  })
  vi.mocked(worldApi).mockReset().mockImplementation(async (path:string)=>{if(path==='/tasks/t1'||path==='/tasks/t2')return task('running') as any;return {} as any})
})
afterEach(()=>{app?.unmount();app=undefined;vi.useRealTimers();vi.unstubAllGlobals()})

it('shows a growing pending assistant text while polling roughly every 700ms',async()=>{
  vi.mocked(worldApi).mockResolvedValue(task('running') as any)
  current=()=>chat({pendingTaskId:'t1',messages:[userMessage,pendingMessage('')]})
  mount();await flush()
  await vi.advanceTimersByTimeAsync(0);await flush()
  expect(api).toHaveBeenCalledWith(root)
  expect(text(host)).toContain('正在写…')
  const reads=(api as unknown as {mock:{calls:[string,unknown][]}}).mock.calls.filter(([path])=>path===root).length
  current=()=>chat({pendingTaskId:'t1',messages:[userMessage,pendingMessage('风起了')]})
  await vi.advanceTimersByTimeAsync(700);await flush()
  expect(text(host)).toContain('风起了')
  expect((api as unknown as {mock:{calls:[string,unknown][]}}).mock.calls.filter(([path])=>path===root).length).toBeGreaterThan(reads)
})
it('does not poll before the 700ms interval elapses',async()=>{
  current=()=>chat({pendingTaskId:'t1',messages:[userMessage,pendingMessage('风')]})
  mount();await flush();await vi.advanceTimersByTimeAsync(0);await flush()
  const reads=()=>(api as unknown as {mock:{calls:[string,unknown][]}}).mock.calls.filter(([path])=>path===root).length
  const before=reads();await vi.advanceTimersByTimeAsync(650);await flush();expect(reads()).toBe(before)
})
it('renders the final saved reply without a pending placeholder',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('雨停了')]})
  mount();await flush()
  expect(text(host)).toContain('雨停了');expect(text(host)).not.toContain('正在写…')
  expect(button('重新生成')).toBeDefined();expect(button('编辑')).toBeDefined()
})
it('regenerates the last assistant reply through the regenerate endpoint',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('旧回复')]})
  mount();await flush()
  await button('重新生成')!.props.onClick();await flush()
  const request=(api as unknown as {mock:{calls:[string,{method?:string;key?:string}][]}}).mock.calls.find(([path])=>path===root+'/regenerate')
  expect(request).toBeDefined();expect(request![1].method).toBe('POST');expect(request![1].key).toBe('fixture-key')
})
it('edits a user message inline and resends it with PATCH messages/{sequence}',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('前面的话')]})
  mount();await flush()
  await button('编辑')!.props.onClick();await flush()
  const textarea=descendants(host,item=>item.tag==='textarea')[0]
  expect(textarea).toBeDefined();fire(textarea,'onUpdate:modelValue','改过的一句话');await flush()
  const form=descendants(host,item=>item.tag==='form'&&descendants(item,node=>node.tag==='textarea').length>0)[0]
  await form.props.onSubmit({preventDefault(){}});await flush()
  const request=(api as unknown as {mock:{calls:[string,{method?:string;body?:{text?:string}}][]}}).mock.calls.find(([path])=>path===root+'/messages/1')
  expect(request).toBeDefined();expect(request![1].method).toBe('PATCH');expect(request![1].body?.text).toBe('改过的一句话')
  expect(descendants(host,item=>item.tag==='textarea'&&item.props['aria-label']==='编辑这条消息')).toHaveLength(0)
})
it('hides regenerate and edit actions while a reply is running',async()=>{
  current=()=>chat({pendingTaskId:'t1',messages:[userMessage,savedMessage('旧回复')]})
  mount();await flush()
  expect(button('重新生成')).toBeUndefined();expect(button('编辑')).toBeUndefined()
})
it('shows the backend CHAT_PENDING message when regenerate is rejected',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('旧回复')]})
  postError=new Error('当前回复还在处理中')
  mount();await flush()
  await button('重新生成')!.props.onClick();await flush()
  expect(text(host)).toContain('当前回复还在处理中')
})

it('exports the chat history through the authorized JSONL download',async()=>{
  mount();await flush()
  const exportButton=descendants(host,item=>item.tag==='button'&&text(item).trim()==='导出聊天')[0]
  exportButton.props.onClick()
  await flush()
  expect(authorizedBlob).toHaveBeenCalledWith(root+'/export?format=jsonl')
  expect(saveBlob).toHaveBeenCalledTimes(1)
  expect((saveBlob as unknown as {mock:{calls:unknown[][]}}).mock.calls[0][1]).toBe('林渡 · 单独聊聊.jsonl')
  expect(text(host)).toContain('聊天记录已导出为 JSONL。')
})

it('shows the lore, memories and persona injected before the last reply',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('风起了')]})
  vi.mocked(api).mockImplementation(async (path:string,options?:{method?:string})=>{
    if(path===root&&!options?.method)return current() as any
    if(path===root+'/injection?before=2')return {lore:[{id:'l1',title:'雨夜'}],memories:[{id:'m1',kind:'fact',title:'读者养猫'}],persona:{id:'p1',name:'旅人'}} as any
    throw new Error('unexpected request '+path)
  })
  mount();await flush()
  button('本轮记忆').props.onClick()
  await flush()
  expect(api).toHaveBeenCalledWith(root+'/injection?before=2')
  expect(text(host)).toContain('你的身份：旅人')
  expect(text(host)).toContain('雨夜')
  expect(text(host)).toContain('读者养猫')
})

it('keeps the memory drawer usable when the injection endpoint is missing',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('风起了')]})
  vi.mocked(api).mockImplementation(async (path:string,options?:{method?:string})=>{
    if(path===root&&!options?.method)return current() as any
    if(path.startsWith(root+'/injection'))throw new Error('服务端暂不支持本轮记忆查询，请稍后重试。')
    throw new Error('unexpected request '+path)
  })
  mount();await flush()
  button('本轮记忆').props.onClick()
  await flush()
  expect(text(host)).toContain('服务端暂不支持本轮记忆查询，请稍后重试。')
})

it('lets the reader inspect the injection before any earlier message',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('风起了')]})
  vi.mocked(api).mockImplementation(async (path:string,options?:{method?:string})=>{
    if(path===root&&!options?.method)return current() as any
    if(path.startsWith(root+'/injection'))return {lore:[{id:'l1',title:'雨夜'}],memories:[],persona:null} as any
    throw new Error('unexpected request '+path)
  })
  mount();await flush()
  const memoryButton=descendants(host,item=>item.tag==='button'&&text(item).trim()==='本轮记忆')[0]
  memoryButton.props.onClick()
  await flush()
  const picker=descendants(host,item=>item.tag==='select'&&String(item.props['aria-label']||'')===''&&item.props.onChange)[0]
  picker.props.onChange({target:{value:'1'}})
  await flush()
  expect(api).toHaveBeenCalledWith(root+'/injection?before=1')
})

it('forks the conversation from the chosen message and opens the new session',async()=>{
  current=()=>chat({messages:[userMessage,savedMessage('前面的话')]})
  vi.mocked(api).mockImplementation(async (path:string,options?:{method?:string;body?:unknown})=>{
    if(path===root&&!options?.method)return current() as any
    if(path===root+'/fork'&&options?.method==='POST')return {sessionId:'new-1',name:'林渡 · 单独聊聊（分支）'} as any
    throw new Error('unexpected request '+path)
  })
  mount();await flush()
  await button('从这里分支')!.props.onClick();await flush()
  expect(text(host)).toContain('确认分支')
  await button('确认分支')!.props.onClick();await flush()
  const request=(api as unknown as {mock:{calls:[string,{method?:string;key?:string;body?:{sequence?:number}}][]}}).mock.calls.find(([path])=>path===root+'/fork')
  expect(request).toBeDefined();expect(request![1].method).toBe('POST');expect(request![1].key).toBe('fixture-key')
  expect(request![1].body).toEqual({sequence:1})
  expect(harness.push).toHaveBeenCalledWith('/conversations/world/new-1')
})
