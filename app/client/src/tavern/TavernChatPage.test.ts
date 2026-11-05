import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import TavernChatPage from './TavernChatPage.vue'
import {deleteMessage,editMessage,exportChatBlob,getChat,selectSwipe,updateChatSampling} from './api'
import {saveBlob} from '../platform/download'
import {streamChat} from './sse'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({route:{params:{id:'chat-1'}},push:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>harness.route,useRouter:()=>({push:harness.push})}))
vi.mock('../stores/app',()=>({useApp:()=>({online:true,message:vi.fn()})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../api',()=>({authorizationHeaders:()=>({})}))
vi.mock('./api',()=>({
  getChat:vi.fn(),
  getCharacter:vi.fn(),
  tavernAvatarUrl:(cardId:string)=>'/avatar/'+cardId,
  editMessage:vi.fn(),
  deleteMessage:vi.fn(),
  selectSwipe:vi.fn(),
  updateChat:vi.fn(),
  updateChatSampling:vi.fn(),
  exportChatBlob:vi.fn(),
  messageText:(message:{swipes?:{content:string}[];activeSwipe?:number})=>message.swipes?.[message.activeSwipe??0]?.content??'',
}))
vi.mock('../platform/download',()=>({saveBlob:vi.fn(),safeFileName:(name:string,fallback:string)=>name||fallback}))
vi.mock('./sse',()=>({streamChat:vi.fn()}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({props:{title:{type:String,default:''}},emits:['close'],setup:(props,{slots})=>()=>h('section',{class:'sheet-stub'},[h('h2',{},props.title),slots.default?.()])})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{ArrowLeft:stub,ChevronLeft:stub,ChevronRight:stub,Download:stub,Pencil:stub,RefreshCw:stub,Send:stub,Square:stub,Trash2:stub,X:stub}})
compileRender(TavernChatPage,new URL('./TavernChatPage.vue',import.meta.url))

type Msg={id:string;role:'user'|'assistant';sequence:number;swipes:{content:string}[];activeSwipe:number}
const userMessage:Msg={id:'u1',role:'user',sequence:1,swipes:[{content:'你好'}],activeSwipe:0}
const assistantMessage:Msg={id:'a1',role:'assistant',sequence:2,swipes:[{content:'风起了'}],activeSwipe:0}
const detail=(messages:Msg[]=[userMessage,assistantMessage])=>({chat:{id:'chat-1',cardId:'c1',title:'林渡 · 聊天',characterName:'林渡'},messages})

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernChatPage)
  provideSfc(app)
  app.mount(host)
}
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim()===label)[0]}
function aria(label:string){return descendants(host,item=>item.props['aria-label']===label)[0]}
function submit(){const form=descendants(host,item=>item.tag==='form')[0];form.props.onSubmit?.({preventDefault(){}})}

beforeEach(()=>{
  vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn()})
  vi.stubGlobal('document',{activeElement:null})
  harness.push.mockReset()
  vi.mocked(getChat).mockReset().mockResolvedValue(detail() as never)
  vi.mocked(editMessage).mockReset().mockResolvedValue(null)
  vi.mocked(deleteMessage).mockReset().mockResolvedValue(undefined)
  vi.mocked(selectSwipe).mockReset().mockResolvedValue(null)
  vi.mocked(exportChatBlob).mockReset().mockResolvedValue(new Blob(['{}']))
  vi.mocked(saveBlob).mockReset()
  vi.mocked(streamChat).mockReset()
  vi.mocked(updateChatSampling).mockReset().mockResolvedValue({id:'chat-1',cardId:'c1',title:'林渡 · 聊天'} as never)
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernChatPage',()=>{
  it('loads a chat and renders the character name and messages',async()=>{
    mount();await flush()
    expect(getChat).toHaveBeenCalledWith('chat-1',{limit:200})
    const body=text(host)
    expect(body).toContain('林渡')
    expect(body).toContain('你好')
    expect(body).toContain('风起了')
    expect(body).not.toContain('还没有消息')
  })

  it('shows the empty state for a fresh chat',async()=>{
    vi.mocked(getChat).mockResolvedValue(detail([]) as never)
    mount();await flush()
    expect(text(host)).toContain('还没有消息，说点什么开始吧。')
  })

  it('streams a reply and reloads the saved messages',async()=>{
    vi.mocked(getChat)
      .mockResolvedValueOnce(detail() as never)
      .mockResolvedValueOnce(detail([userMessage,assistantMessage,{id:'a2',role:'assistant',sequence:3,swipes:[{content:'雨停了'}],activeSwipe:0}]) as never)
    vi.mocked(streamChat).mockImplementation(async (_path,_body,handlers)=>{
      handlers.onMessage?.({message:{id:'u2',role:'user',sequence:3,swipes:[{content:'再讲一段'}],activeSwipe:0}})
      handlers.onDelta?.('雨')
      handlers.onDelta?.('停了')
      handlers.onDone?.({})
    })
    mount();await flush()
    state().input='再讲一段'
    submit()
    await flush()
    expect(streamChat).toHaveBeenCalledWith('/chats/chat-1/messages',{text:'再讲一段'},expect.any(Object),expect.objectContaining({signal:expect.anything()}))
    expect(vi.mocked(getChat).mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(text(host)).toContain('雨停了')
  })

  it('aborts the stream when the user stops generation',async()=>{
    let signal:AbortSignal|undefined
    vi.mocked(streamChat).mockImplementation((_path,_body,_handlers,options)=>{
      signal=options?.signal
      return new Promise<void>(()=>{})
    })
    mount();await flush()
    state().input='讲下去'
    submit()
    await flush()
    aria('停止生成').props.onClick?.()
    await flush()
    expect(signal?.aborted).toBe(true)
    expect(text(host)).not.toContain('正在回复…')
  })

  it('regenerates the last assistant message',async()=>{
    vi.mocked(streamChat).mockResolvedValue(undefined)
    mount();await flush()
    button('重新生成').props.onClick?.()
    await flush()
    expect(streamChat).toHaveBeenCalledWith('/chats/chat-1/regenerate',{},expect.any(Object),expect.objectContaining({signal:expect.anything()}))
  })

  it('edits a user message through the inline editor',async()=>{
    mount();await flush()
    aria('编辑这条消息').props.onClick?.()
    await flush()
    const editor=aria('编辑消息')
    editor.props['onUpdate:modelValue']?.('改过的话')
    await flush()
    button('保存').props.onClick?.()
    await flush()
    expect(editMessage).toHaveBeenCalledWith('chat-1','u1','改过的话')
    expect(vi.mocked(getChat).mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('switches between generated swipes',async()=>{
    vi.mocked(getChat).mockResolvedValue(detail([userMessage,{...assistantMessage,swipes:[{content:'版本一'},{content:'版本二'}],activeSwipe:0}]) as never)
    mount();await flush()
    expect(text(host)).toContain('1/2')
    aria('下一个版本').props.onClick?.()
    await flush()
    expect(selectSwipe).toHaveBeenCalledWith('chat-1','a1',1)
  })

  it('requests the first page with limit=200',async()=>{
    mount();await flush()
    expect(getChat).toHaveBeenCalledWith('chat-1',{limit:200})
  })

  it('loads earlier messages with before=first sequence and keeps them in order',async()=>{
    vi.mocked(getChat)
      .mockResolvedValueOnce({chat:{id:'chat-1',cardId:'c1',title:'林渡'},messages:[{...userMessage,sequence:2},{...assistantMessage,sequence:3}],hasMore:true} as never)
      .mockResolvedValueOnce({chat:{id:'chat-1',cardId:'c1',title:'林渡'},messages:[{id:'u0',role:'user',sequence:1,swipes:[{content:'很久以前'}],activeSwipe:0}],hasMore:false} as never)
    mount();await flush()
    button('加载更早消息').props.onClick?.()
    await flush()
    expect(vi.mocked(getChat).mock.calls[1]).toEqual(['chat-1',{limit:200,before:2}])
    const body=text(host)
    expect(body).toContain('很久以前')
    expect(body).toContain('你好')
    expect(body).not.toContain('加载更早消息')
  })

  it('deletes a message only after the inline confirmation',async()=>{
    mount();await flush()
    aria('删除这条消息').props.onClick?.()
    await flush()
    expect(deleteMessage).not.toHaveBeenCalled()
    aria('确认删除消息').props.onClick?.()
    await flush()
    expect(deleteMessage).toHaveBeenCalledWith('chat-1','u1')
    expect(vi.mocked(getChat).mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('exports the chat as JSONL from the header menu',async()=>{
    mount();await flush()
    aria('聊天菜单').props.onClick?.()
    await flush()
    button('导出聊天 (JSONL)').props.onClick?.()
    await flush()
    expect(exportChatBlob).toHaveBeenCalledWith('chat-1','jsonl')
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob),'林渡.jsonl')
  })

  it('edits chat-level sampling from the chat menu',async()=>{
    mount();await flush()
    aria('聊天菜单').props.onClick?.()
    await flush()
    const open=descendants(host,item=>item.tag==='button'&&text(item).trim()==='采样参数')[0]
    open.props.onClick?.()
    await flush()
    const setup=state()
    ;(setup.sampling as {temperature:number;topP:number;maxTokens:number;stopText:string})={temperature:0.5,topP:0.85,maxTokens:640,stopText:'END\nSTOP'}
    const samplingForm=descendants(host,item=>item.tag==='form'&&descendants(item,child=>child.props['aria-label']==='temperature').length>0)[0]
    samplingForm.props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(updateChatSampling).toHaveBeenCalledWith('chat-1',{temperature:0.5,topP:0.85,maxTokens:640,stop:['END','STOP']})
  })

  it('renders group speakers, the member stack and the next speaker',async()=>{
    vi.mocked(getChat).mockResolvedValue({chat:{id:'chat-1',cardId:'',title:'群聊',mode:'group',members:[{cardId:'c1',name:'林渡',order:0},{cardId:'c2',name:'阿青',order:1}]},messages:[userMessage,{...assistantMessage,speakerCardId:'c1',speakerName:'林渡'}]} as never)
    mount();await flush()
    const speakers=descendants(host,item=>String(item.props.class??'').includes('bubble-speaker-name'))
    expect(speakers.length).toBe(1)
    expect(text(speakers[0]!)).toContain('林渡')
    expect(descendants(host,item=>String(item.props.class??'')==='chat-member-avatar').length).toBe(2)
  })
})
