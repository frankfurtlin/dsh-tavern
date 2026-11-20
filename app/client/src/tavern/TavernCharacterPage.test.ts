import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {onBeforeRouteLeave} from 'vue-router'
import {defineComponent,h,onUnmounted} from 'vue'
import TavernCharacterPage from './TavernCharacterPage.vue'
import {getPrompt,importChats,previewWorldInfo,updatePrompt} from './api'
import LoadState from './LoadState.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({route:{params:{id:'c1'}},push:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>harness.route,useRouter:()=>({push:harness.push}),onBeforeRouteLeave:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>({message:vi.fn()})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../platform/download',()=>({saveBlob:vi.fn(),safeFileName:(name:string,fallback:string)=>name||fallback}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{ArrowLeft:stub,Download:stub,MessageSquarePlus:stub,Plus:stub,Save:stub,Trash2:stub,Upload:stub}})
vi.mock('./api',()=>({
  getCharacter:vi.fn(),
  characterAvatarBlob:vi.fn(async()=>null),
  listCharacterChats:vi.fn(async()=>[{id:'chat-1',cardId:'c1',title:'林渡的对话',lastMessageAt:'2026-01-01T00:00:00'}]),
  listWorldInfo:vi.fn(async()=>[{id:'w1',keys:['雨'],content:'雨夜',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'}]),
  updateCharacter:vi.fn(),
  deleteCharacter:vi.fn(),
  exportCharacterBlob:vi.fn(),
  createCharacterChat:vi.fn(),
  importChats:vi.fn(),
  previewWorldInfo:vi.fn(),
  createWorldInfo:vi.fn(),
  updateWorldInfo:vi.fn(),
  deleteWorldInfo:vi.fn(),
  getPrompt:vi.fn(),
  updatePrompt:vi.fn(),
}))
compileRender(TavernCharacterPage,new URL('./TavernCharacterPage.vue',import.meta.url))
compileRender(LoadState,new URL('./LoadState.vue',import.meta.url))

const card={id:'c1',name:'林渡',description:'雨夜侦探',tags:['悬疑'],hasAvatar:false}

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernCharacterPage)
  provideSfc(app)
  app.mount(host)
}
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}
function aria(label:string){return descendants(host,item=>item.props['aria-label']===label)[0]}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim().includes(label))[0]}

beforeEach(()=>{
  vi.stubGlobal('window',{confirm:vi.fn(()=>false)})
  vi.stubGlobal('document',{activeElement:null})
  harness.push.mockReset()
  vi.mocked(previewWorldInfo).mockReset().mockResolvedValue({matched:[],injection:''})
  vi.mocked(getPrompt).mockReset().mockResolvedValue({systemPrompt:'',mainPrompt:'',jailbreakPrompt:'',temperature:0.8,topP:0.95,maxTokens:1024,stop:[],worldInfoPosition:'before_char'})
  vi.mocked(updatePrompt).mockReset().mockResolvedValue({systemPrompt:'',mainPrompt:'',jailbreakPrompt:'',temperature:0.8,topP:0.95,maxTokens:1024,stop:[],worldInfoPosition:'before_char'})
  vi.mocked(importChats).mockReset().mockResolvedValue({id:'imported-1',cardId:'c1',title:'导入的聊天'} as never)
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernCharacterPage',()=>{
  it('renders the card, world info and chat history',async()=>{
    const {getCharacter}=await import('./api')
    vi.mocked(getCharacter).mockResolvedValue(card as never)
    mount();await flush()
    const body=text(host)
    expect(body).toContain('林渡')
    expect(body).toContain('雨夜')
    expect(body).toContain('林渡的对话')
    expect(body).toContain('世界书')
    const open=button('查看全部世界书')
    open.props.onClick?.()
    await flush()
    expect(text(host)).toContain('雨夜')
    expect(text(host)).toContain('注入预览')
  })

  it('previews world-info injection for a sample message',async()=>{
    const {getCharacter}=await import('./api')
    vi.mocked(getCharacter).mockResolvedValue(card as never)
    vi.mocked(previewWorldInfo).mockResolvedValueOnce({
      matched:[{id:'w1',keys:['雨'],content:'雨夜，街上没人。',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'}],
      injection:'[世界书 · 角色设定之前]\n雨夜，街上没人。',
    })
    mount();await flush()
    button('查看全部世界书').props.onClick?.()
    await flush()
    state().previewMessage='外面下雨了'
    const input=aria('注入预览示例消息')
    const form=descendants(host,item=>item.children.some(child=>child===input))[0]
    form.props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(previewWorldInfo).toHaveBeenCalledWith('c1','外面下雨了')
    const body=text(host)
    expect(body).toContain('命中 1 条世界书')
    expect(body).toContain('雨夜，街上没人。')
  })

  it('imports a tavern chat JSONL and opens the new chat',async()=>{
    const {getCharacter}=await import('./api')
    vi.mocked(getCharacter).mockResolvedValue(card as never)
    mount();await flush()
    const file=new File(['{"role":"user","text":"你好"}\n'],'chat.jsonl',{type:'application/jsonl'})
    const input=aria('导入酒馆聊天 JSONL')
    input.props.onChange?.({target:{files:[file],value:''}})
    await flush()
    expect(importChats).toHaveBeenCalledWith('c1',file)
    expect(harness.push).toHaveBeenCalledWith('/chats/imported-1')
  })

  it('saves prompt overrides and reports the jailbreak fallback',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue(card as never)
    vi.mocked(getPrompt).mockResolvedValue({systemPrompt:'旧系统',mainPrompt:'主线',jailbreakPrompt:'旧越狱',temperature:0.6,topP:0.9,maxTokens:800,stop:['END'],worldInfoPosition:'after_char'})
    vi.mocked(updatePrompt).mockResolvedValueOnce({systemPrompt:'旧系统',mainPrompt:'主线',jailbreakPrompt:'旧越狱',temperature:0.6,topP:0.9,maxTokens:800,stop:['END'],worldInfoPosition:'after_char'})
    vi.mocked(updatePrompt).mockRejectedValueOnce(new Error('422'))
    mount();await flush()
    const setup=state()
    const prompt=setup.prompt as {mainPrompt:string;jailbreakPrompt:string;temperature:number}
    prompt.mainPrompt='新的主线'
    prompt.temperature=1.1
    await (setup.savePrompt as ()=>Promise<void>)()
    await flush()
    expect(vi.mocked(updatePrompt).mock.calls[0][1]).toMatchObject({mainPrompt:'新的主线',temperature:1.1,topP:0.9,maxTokens:800,stop:['END']})
    expect(vi.mocked(updatePrompt).mock.calls[1][1]).toEqual({jailbreakPrompt:'旧越狱'})
    expect(text(host)).toContain('越狱提示词需要服务端支持')
  })

  it('edits world-info probability on a 0..1 scale and saves an integer percent',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue(card as never)
    vi.mocked(apiModule.updateWorldInfo).mockReset().mockResolvedValue({} as never)
    mount();await flush()
    button('查看全部世界书').props.onClick?.()
    await flush()
    const edit=descendants(host,item=>item.tag==='button'&&String(item.props['aria-label']||'').startsWith('编辑世界书 '))[0]
    edit.props.onClick?.()
    await flush()
    expect((state().infoEditor as {probability:number}).probability).toBe(1)
    const setup=state()
    ;(setup.infoEditor as {probability:number}).probability=0.45
    const editor=descendants(host,item=>item.tag==='form'&&descendants(item,child=>child.props['aria-label']==='世界书内容').length>0)[0]
    editor.props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(apiModule.updateWorldInfo).toHaveBeenCalledWith('c1','w1',expect.objectContaining({probability:45}))
  })

  it('shows why each world-info entry matched in the injection preview',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue(card as never)
    vi.mocked(previewWorldInfo).mockResolvedValueOnce({
      matched:[
        {id:'w1',keys:['雨'],content:'雨夜。',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'},
        {id:'w2',keys:[],content:'常驻设定。',position:'before_char',depth:4,constant:true,probability:100,order:1,enabled:true,source:'manual'},
      ],
      injection:'[世界书]',
    })
    mount();await flush()
    button('查看全部世界书').props.onClick?.()
    await flush()
    state().previewMessage='下雨了'
    const form=descendants(host,item=>item.children.some(child=>child.props['aria-label']==='注入预览示例消息'))[0]
    form.props.onSubmit?.({preventDefault(){}})
    await flush()
    const body=text(host)
    expect(body).toContain('关键词「雨」')
    expect(body).toContain('常驻')
  })

describe('TavernCharacterPage preset card',()=>{
  it('labels builtin cards with the preset attribution line',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue({...card,builtin:true,builtinSlug:'foreverse-rin'} as never)
    mount();await flush()
    expect(text(host)).toContain('预设 · Foreverse Team（CC BY 4.0）')
  })

  it('shows the original creator for non-builtin cards',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue({...card,creator:'Alice'} as never)
    mount();await flush()
    expect(text(host)).toContain('Alice')
    expect(text(host)).not.toContain('Foreverse')
  })

  it('warns that deleted preset cards are not restored',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue({...card,builtin:true} as never)
    mount();await flush()
    button('编辑').props.onClick?.()
    await flush()
    const del=descendants(host,item=>item.tag==='button'&&text(item).trim().includes('删除角色卡'))[0]
    del!.props.onClick?.()
    await flush()
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(String(vi.mocked(window.confirm).mock.calls[0][0])).toContain('预设角色删除后不会自动恢复。')
    expect(apiModule.deleteCharacter).not.toHaveBeenCalled()
  })

  it('does not add the preset warning for ordinary cards',async()=>{
    const apiModule=await import('./api')
    vi.mocked(apiModule.getCharacter).mockResolvedValue(card as never)
    mount();await flush()
    button('编辑').props.onClick?.()
    await flush()
    const del=descendants(host,item=>item.tag==='button'&&text(item).trim().includes('删除角色卡'))[0]
    del!.props.onClick?.()
    await flush()
    expect(String(vi.mocked(window.confirm).mock.calls[0][0])).not.toContain('预设角色删除后不会自动恢复。')
  })
})
})

function hasClass(item:Host,name:string){
  const value=item.props.class
  if(typeof value==='string')return value.split(/\s+/).includes(name)
  if(Array.isArray(value))return value.includes(name)
  return !!value&&typeof value==='object'&&!!(value as Record<string,boolean>)[name]
}

describe('TavernCharacterPage A2b reading layout',()=>{
  it('renders the hero with an avatar, serif name and 44px actions',async()=>{
    mount();await flush()
    expect(descendants(host,item=>hasClass(item,'character-hero'))).toHaveLength(1)
    expect(descendants(host,item=>hasClass(item,'character-avatar'))).toHaveLength(1)
    const name=descendants(host,item=>item.tag==='h1'&&text(item).includes('林渡'))[0]
    expect(name).toBeTruthy()
    for(const label of ['开新聊天','编辑'])expect(button(label)).toBeTruthy()
  })

  it('uses the shared reading size for read-only sections',async()=>{
    mount();await flush()
    const bodies=descendants(host,item=>hasClass(item,'read-body'))
    expect(bodies.length).toBeGreaterThanOrEqual(1)
    for(const body of bodies){
      expect(body.props.style?.fontSize).toBe('var(--reading-size)')
      expect(body.props.style?.lineHeight).toBe('var(--reading-leading)')
    }
  })

  it('switches to the edit form and cancels back to reading mode',async()=>{
    mount();await flush()
    expect(aria('编辑角色设定')).toBeUndefined()
    button('编辑').props.onClick?.()
    await flush()
    expect(aria('编辑角色设定')).toBeTruthy()
    expect(descendants(host,item=>hasClass(item,'edit-area')).length).toBeGreaterThan(0)
    vi.mocked(window.confirm).mockReturnValueOnce(true)
    button('取消').props.onClick?.()
    await flush()
    expect(aria('编辑角色设定')).toBeUndefined()
    expect(descendants(host,item=>hasClass(item,'character-hero'))).toHaveLength(1)
  })

  it('keeps unsaved edits when the leave confirmation is dismissed',async()=>{
    mount();await flush()
    button('编辑').props.onClick?.()
    await flush()
    ;(state().form as {description:string}).description='改过的设定'
    button('取消').props.onClick?.()
    await flush()
    expect(window.confirm).toHaveBeenCalled()
    expect(aria('编辑角色设定')).toBeTruthy()
    vi.mocked(window.confirm).mockReturnValueOnce(true)
    button('取消').props.onClick?.()
    await flush()
    expect(aria('编辑角色设定')).toBeUndefined()
  })

  it('asks before leaving the page with unsaved edits',async()=>{
    mount();await flush()
    const guard=vi.mocked(onBeforeRouteLeave).mock.calls[0][0] as ()=>boolean
    expect(guard()).toBe(true)
    button('编辑').props.onClick?.()
    await flush()
    ;(state().form as {description:string}).description='改过的设定'
    await flush()
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    expect(guard()).toBe(false)
    vi.mocked(window.confirm).mockReturnValueOnce(true)
    expect(guard()).toBe(true)
  })

  it('collapses world info into a sheet with count and first summary',async()=>{
    mount();await flush()
    expect(text(host)).toContain('共 1 条')
    expect(text(host)).toContain('首条')
    expect(text(host)).not.toContain('注入预览')
    button('查看全部世界书').props.onClick?.()
    await flush()
    const sheet=descendants(host,item=>item.props.role==='dialog')[0]
    expect(sheet).toBeTruthy()
    expect(text(sheet)).toContain('雨夜')
    expect(text(sheet)).toContain('注入预览')
  })
})
