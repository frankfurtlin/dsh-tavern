import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import TavernChatPage from './TavernChatPage.vue'
import {deleteMessage,editMessage,exportChatBlob,getChat,listCharacters,selectSwipe,updateChatMembers,updateChatSampling} from './api'
import {saveBlob} from '../platform/download'
import {streamChat} from './sse'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({route:{params:{id:'chat-1'}},push:vi.fn(),replace:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>harness.route,useRouter:()=>({push:harness.push,replace:harness.replace})}))
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
  updateChatMembers:vi.fn(),
  listCharacters:vi.fn(),
  updateChatSampling:vi.fn(),
  exportChatBlob:vi.fn(),
  sendMessage:(chatId:string,content:string,quote?:{text:string}|null,personaId?:string|null)=>{
    const body:{message:string;quote?:{text:string};personaId?:string}={message:content}
    const text=(typeof quote==='string'?quote:quote?.text??'').trim()
    if(text)body.quote={text}
    if(personaId)body.personaId=personaId
    return {path:'/chats/'+chatId+'/messages',body}
  },
  messageText:(message:{swipes?:{content:string}[];activeSwipe?:number})=>message.swipes?.[message.activeSwipe??0]?.content??'',
}))
vi.mock('../platform/download',()=>({saveBlob:vi.fn(),safeFileName:(name:string,fallback:string)=>name||fallback}))
vi.mock('./sse',()=>({streamChat:vi.fn()}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({props:{title:{type:String,default:''}},emits:['close'],setup:(props,{slots})=>()=>h('section',{class:'sheet-stub'},[h('h2',{},props.title),slots.default?.()])})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{ArrowDown:stub,ArrowLeft:stub,Check:stub,ChevronDown:stub,ChevronLeft:stub,ChevronRight:stub,ChevronUp:stub,Copy:stub,Download:stub,HelpCircle:stub,MoreHorizontal:stub,Pencil:stub,Quote:stub,RefreshCw:stub,Search:stub,Send:stub,Square:stub,Trash2:stub,X:stub}})
compileRender(TavernChatPage,new URL('./TavernChatPage.vue',import.meta.url))

type Msg={id:string;role:'user'|'assistant';sequence:number;swipes:{content:string}[];activeSwipe:number;quotedText?:string|null;createdAt?:string;speakerCardId?:string|null;speakerName?:string|null}
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
  vi.mocked(listCharacters).mockReset().mockResolvedValue([
    {id:'c1',name:'林渡'},{id:'c2',name:'阿青'},{id:'c3',name:'角色C'},{id:'c4',name:'角色D'},{id:'c5',name:'角色E'},
  ] as never)
  vi.mocked(updateChatMembers).mockReset().mockResolvedValue({id:'chat-1',cardId:'',title:'群聊',mode:'group'} as never)
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
    expect(streamChat).toHaveBeenCalledWith('/chats/chat-1/messages',{message:'再讲一段'},expect.any(Object),expect.objectContaining({signal:expect.anything()}))
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
    aria('重新生成').props.onClick?.()
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

  it('tucks the per-message actions into a “⋯” menu',async()=>{
    mount();await flush()
    const trigger=aria('更多操作')
    expect(trigger).toBeTruthy()
    expect(trigger.props['aria-expanded']).toBe(false)
    trigger.props.onClick?.()
    await flush()
    expect(trigger.props['aria-expanded']).toBe(true)
    const openMenus=()=>descendants(host,item=>String(item.props.class??'').includes('bubble-actions')&&String(item.props.class??'').includes('open'))
    expect(openMenus()).toHaveLength(1)
    aria('引用这条消息').props.onClick?.()
    await flush()
    expect((state().quote as {text:string}|null)?.text).toBe('你好')
    expect(openMenus()).toHaveLength(0)
  })

  it('closes the action menu on Escape',async()=>{
    const listeners:Record<string,(event:KeyboardEvent)=>void>={}
    vi.stubGlobal('window',{addEventListener:(type:string,fn:(event:KeyboardEvent)=>void)=>{listeners[type]=fn},removeEventListener:vi.fn()})
    mount();await flush()
    aria('更多操作').props.onClick?.()
    await flush()
    const openMenus=()=>descendants(host,item=>String(item.props.class??'').includes('bubble-actions')&&String(item.props.class??'').includes('open'))
    expect(openMenus()).toHaveLength(1)
    listeners.keydown?.({key:'Escape'} as KeyboardEvent)
    await flush()
    expect(openMenus()).toHaveLength(0)
  })

  it('sends the saved persona with every message',async()=>{
    vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn(),localStorage:{getItem:(key:string)=>key==='tavern:persona'?'p1':null,setItem:vi.fn(),removeItem:vi.fn()}})
    vi.mocked(streamChat).mockResolvedValue(undefined)
    mount();await flush()
    state().input='再讲一段'
    submit()
    await flush()
    expect(streamChat).toHaveBeenCalledWith('/chats/chat-1/messages',{message:'再讲一段',personaId:'p1'},expect.any(Object),expect.objectContaining({signal:expect.anything()}))
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

  it('greys out deleted members and marks their speaker name',async()=>{
    vi.mocked(getChat).mockResolvedValue({chat:{id:'chat-1',cardId:'',title:'群聊',mode:'group',aliveMemberCount:1,members:[{cardId:'c1',name:'林渡',order:0},{cardId:'c2',name:'阿青',order:1,deleted:true}]},messages:[userMessage,{...assistantMessage,speakerCardId:'c2',speakerName:'阿青'}]} as never)
    mount();await flush()
    const deletedAvatars=descendants(host,item=>String(item.props.class??'').includes('chat-member-avatar')&&String(item.props.class??'').includes('deleted'))
    expect(deletedAvatars.length).toBe(1)
    expect(deletedAvatars[0]!.props['aria-label']).toBe('阿青（已删除）')
    expect(text(host)).toContain('阿青（已删除）')
  })

  it('locks the composer and regenerate when every member was deleted',async()=>{
    vi.mocked(getChat).mockResolvedValue({chat:{id:'chat-1',cardId:'',title:'群聊',mode:'group',aliveMemberCount:0,members:[{cardId:'c1',name:'林渡',order:0,deleted:true},{cardId:'c2',name:'阿青',order:1,deleted:true}]},messages:[userMessage,assistantMessage]} as never)
    mount();await flush()
    expect(text(host)).toContain('群聊成员已全部删除，无法继续发言')
    expect(aria('聊天输入框').props.disabled).toBe(true)
    expect(aria('发送消息').props.disabled).toBe(true)
    expect(aria('重新生成')).toBeUndefined()
  })

  it('rotates the next speaker over living members only',async()=>{
    vi.mocked(getChat).mockResolvedValue({chat:{id:'chat-1',cardId:'',title:'群聊',mode:'group',aliveMemberCount:2,members:[{cardId:'c1',name:'林渡',order:0},{cardId:'c2',name:'阿青',order:1,deleted:true},{cardId:'c3',name:'角色C',order:2}]},messages:[userMessage,assistantMessage]} as never)
    mount();await flush()
    const next=state().nextSpeaker as {name:string}
    expect(next.name).toBe('角色C')
  })

  it('renders legacy group chats without a members field',async()=>{
    vi.mocked(getChat).mockResolvedValue({chat:{id:'chat-1',cardId:'c1',title:'旧群聊',mode:'group'},messages:[userMessage,assistantMessage]} as never)
    mount();await flush()
    expect(text(host)).toContain('旧群聊')
    expect(aria('聊天输入框').props.disabled).toBe(false)
  })

  it('opens the search bar, counts hits and jumps to a match',async()=>{
    mount();await flush()
    aria('搜索聊天内容').props.onClick?.()
    await flush()
    expect(text(host)).toContain('仅搜索已加载消息')
    state().searchQuery='风'
    await flush()
    expect(text(host)).toContain('1/1')
    aria('下一个匹配').props.onClick?.()
    await flush()
    const hit=descendants(host,item=>String(item.props.class??'').includes('chat-search-hit'))
    expect(hit.length).toBe(1)
  })

  it('shows no-match feedback with the loaded-message hint',async()=>{
    mount();await flush()
    aria('搜索聊天内容').props.onClick?.()
    state().searchQuery='不存在的句子'
    await flush()
    expect(text(host)).toContain('没有匹配')
    expect(text(host)).toContain('仅搜索已加载消息')
  })

  it('cycles through multiple matches and wraps around',async()=>{
    vi.mocked(getChat).mockResolvedValue(detail([{id:'u1',role:'user',sequence:1,swipes:[{content:'下雨了'}],activeSwipe:0},{id:'a1',role:'assistant',sequence:2,swipes:[{content:'雨停了'}],activeSwipe:0}]) as never)
    mount();await flush()
    aria('搜索聊天内容').props.onClick?.()
    state().searchQuery='雨'
    await flush()
    expect(text(host)).toContain('1/2')
    aria('下一个匹配').props.onClick?.()
    await flush()
    expect(text(host)).toContain('2/2')
    aria('下一个匹配').props.onClick?.()
    await flush()
    expect(text(host)).toContain('1/2')
    aria('上一个匹配').props.onClick?.()
    await flush()
    expect(text(host)).toContain('2/2')
  })

  it('closes the search bar on Escape and keeps the composer draft',async()=>{
    mount();await flush()
    aria('搜索聊天内容').props.onClick?.()
    await flush()
    state().input='写了一半的草稿'
    const searchInput=aria('搜索聊天内容输入')
    searchInput.props.onKeydown?.({key:'Escape',stopPropagation(){},preventDefault(){}})
    await flush()
    expect(text(host)).not.toContain('仅搜索已加载消息')
    expect(state().input).toBe('写了一半的草稿')
  })

  it('does not touch composer input while searching',async()=>{
    mount();await flush()
    aria('搜索聊天内容').props.onClick?.()
    state().input='保留草稿'
    state().searchQuery='风'
    await flush()
    expect(state().input).toBe('保留草稿')
    expect(text(host)).toContain('风起了')
  })

  const groupDetail=(members:{cardId:string;name:string;deleted?:boolean}[]=[{cardId:'c1',name:'林渡'},{cardId:'c2',name:'阿青'}])=>({chat:{id:'chat-1',cardId:'',title:'群聊',mode:'group',aliveMemberCount:members.filter(m=>!m.deleted).length,members},messages:[userMessage,assistantMessage]})
  async function openMembersDialog(){
    vi.mocked(getChat).mockResolvedValue(groupDetail() as never)
    mount();await flush()
    aria('聊天菜单').props.onClick?.()
    await flush()
    button('管理成员')!.props.onClick?.()
    await flush()
  }

  it('shows the member manager only for group chats',async()=>{
    mount();await flush()
    aria('聊天菜单').props.onClick?.()
    await flush()
    expect(button('管理成员')).toBeUndefined()
    vi.mocked(getChat).mockResolvedValue(groupDetail() as never)
    app?.unmount()
    mount();await flush()
    aria('聊天菜单').props.onClick?.()
    await flush()
    expect(button('管理成员')).toBeDefined()
  })

  it('preselects living members in the manager dialog',async()=>{
    await openMembersDialog()
    const checked=descendants(host,item=>item.tag==='input'&&item.props.type==='checkbox'&&item.props.checked===true)
    expect(checked.length).toBe(2)
    expect(text(host)).toContain('2/5')
  })

  it('blocks saving when only one member is selected',async()=>{
    await openMembersDialog()
    ;(state().toggleMember as (id:string)=>void)('c2')
    await flush()
    expect(text(host)).toContain('群聊需要 2 至 5 位角色')
    expect(button('保存')!.props.disabled).toBe(true)
  })

  it('prevents selecting more than five members',async()=>{
    vi.mocked(listCharacters).mockResolvedValue([
      {id:'c1',name:'林渡'},{id:'c2',name:'阿青'},{id:'c3',name:'角色C'},{id:'c4',name:'角色D'},{id:'c5',name:'角色E'},{id:'c6',name:'角色F'},
    ] as never)
    await openMembersDialog()
    for(const id of ['c3','c4','c5','c6'])(state().toggleMember as (id:string)=>void)(id)
    await flush()
    expect((state().memberSelection as string[]).length).toBe(5)
    expect(text(host)).toContain('5/5')
  })

  it('saves members with PATCH and reloads the chat detail',async()=>{
    await openMembersDialog()
    ;(state().toggleMember as (id:string)=>void)('c3')
    await flush()
    button('保存')!.props.onClick?.()
    await flush()
    expect(updateChatMembers).toHaveBeenCalledWith('chat-1',['c1','c2','c3'])
    expect(vi.mocked(getChat).mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(text(host)).not.toContain('管理群聊成员')
  })

  it('shows deleted members as non-selectable replacement hints',async()=>{
    vi.mocked(getChat).mockResolvedValue(groupDetail([{cardId:'c1',name:'林渡'},{cardId:'c2',name:'阿青',deleted:true}]) as never)
    vi.mocked(listCharacters).mockResolvedValue([{id:'c1',name:'林渡'},{id:'c3',name:'角色C'}] as never)
    mount();await flush()
    aria('聊天菜单').props.onClick?.()
    await flush()
    button('管理成员')!.props.onClick?.()
    await flush()
    expect(text(host)).toContain('阿青（已删除）')
    expect(descendants(host,item=>String(item.props.class??'').includes('member-deleted-row')).length).toBe(1)
  })

  describe('example chips',()=>{
    it('fills the composer with a starter chip when only the greeting exists',async()=>{
      vi.mocked(getChat).mockResolvedValue(detail([assistantMessage]) as never)
      mount();await flush()
      const chip=button('你好')
      expect(chip).toBeTruthy()
      expect(button('继续')).toBeTruthy()
      chip!.props.onClick?.()
      await flush()
      expect(state().input).toBe('你好')
      expect(button('发送')).toBeTruthy()
    })

    it('hides chips once the user has spoken',async()=>{
      mount();await flush()
      expect(button('你好')).toBeUndefined()
    })
  })

  describe('quote replies',()=>{
    function setInput(value:string){(state() as {input:string}).input=value}

    it('quotes a message and sends it with the quote payload',async()=>{
      mount();await flush()
      aria('引用这条消息')!.props.onClick?.()
      await flush()
      expect((state() as {quote:{text:string}|null}).quote?.text).toBe('你好')
      expect(aria('取消引用')).toBeTruthy()
      setInput('收到')
      await flush()
      submit()
      await flush()
      expect(streamChat).toHaveBeenCalledWith('/chats/chat-1/messages',{message:'收到',quote:{text:'你好'}},expect.any(Object),expect.anything())
    })

    it('cancels the quote before sending',async()=>{
      mount();await flush()
      aria('引用这条消息')!.props.onClick?.()
      await flush()
      aria('取消引用')!.props.onClick?.()
      await flush()
      expect((state() as {quote:unknown}).quote).toBeNull()
      setInput('收到')
      await flush()
      submit()
      await flush()
      expect(streamChat).toHaveBeenCalledWith('/chats/chat-1/messages',{message:'收到'},expect.any(Object),expect.anything())
    })

    it('clears the quote with Escape',async()=>{
      mount();await flush()
      aria('引用这条消息')!.props.onClick?.()
      await flush()
      const listener=vi.mocked(window.addEventListener).mock.calls.find(call=>call[0]==='keydown')![1] as unknown as (event:{key:string})=>void
      listener({key:'Escape'})
      await flush()
      expect((state() as {quote:unknown}).quote).toBeNull()
    })

    it('renders quotedText on saved messages and jumps to the source message',async()=>{
      vi.mocked(getChat).mockResolvedValue(detail([{...userMessage,quotedText:'风起了'},assistantMessage]) as never)
      mount();await flush()
      expect(text(host)).toContain('风起了')
      aria('查看被引用的消息')!.props.onClick?.()
      await flush()
      expect((state() as {searchHitId:string}).searchHitId).toBe('a1')
    })
  })


describe('A2a chat reading',()=>{
  const stamp=(offsetDays=0)=>{
    const date=new Date()
    date.setDate(date.getDate()-offsetDays)
    return date.toISOString()
  }

  it('renders *actions* and **emphasis** as plain text segments',async()=>{
    vi.mocked(getChat).mockResolvedValue(detail([{id:'a1',role:'assistant',sequence:2,swipes:[{content:'*推门而入*，风声**很轻**。'}],activeSwipe:0,createdAt:stamp()}]) as never)
    mount();await flush()
    const acts=descendants(host,item=>String(item.props.class??'')==='act')
    const strongs=descendants(host,item=>String(item.props.class??'')==='strong')
    expect(acts.length).toBe(1)
    expect(text(acts[0])).toBe('推门而入')
    expect(strongs.length).toBe(1)
    expect(text(strongs[0])).toBe('很轻')
    expect(text(host)).not.toContain('*')
  })

  it('separates days and hides per-message time labels',async()=>{
    const today=stamp(0),yesterday=stamp(1)
    vi.mocked(getChat).mockResolvedValue(detail([
      {...userMessage,createdAt:today},
      {...assistantMessage,createdAt:today},
      {id:'u3',role:'user',sequence:3,swipes:[{content:'第二天'}],activeSwipe:0,createdAt:yesterday},
    ]) as never)
    mount();await flush()
    const days=descendants(host,item=>String(item.props.class??'')==='chat-day')
    expect(days.length).toBe(2)
    expect(text(days[0])).toBe('今天')
    expect(text(days[1])).toBe('昨天')
    expect(descendants(host,item=>String(item.props.class??'')==='bubble-time').length).toBe(0)
    const titled=descendants(host,item=>/^\d{2}:\d{2}$/.test(String(item.props.title??'')))
    expect(titled.length).toBeGreaterThan(0)
  })

  it('tags user and assistant bubbles with their role',async()=>{
    mount();await flush()
    const rows=descendants(host,item=>String(item.props.class??'').includes('bubble-row'))
    expect(rows.some(row=>String(row.props.class).includes('user'))).toBe(true)
    expect(rows.some(row=>String(row.props.class).includes('assistant'))).toBe(true)
  })
})
})

describe('不存在的聊天',()=>{
  it('加载 404 时回到角色卡而不是停在错误页',async()=>{
    vi.mocked(getChat).mockRejectedValueOnce(Object.assign(new Error('这段对话不存在或已被删除'),{status:404}))
    mount();await flush()
    expect(harness.replace).toHaveBeenCalledWith('/')
    expect(harness.push).not.toHaveBeenCalled()
  })
})
