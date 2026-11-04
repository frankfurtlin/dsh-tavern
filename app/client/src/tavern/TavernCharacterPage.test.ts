import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import TavernCharacterPage from './TavernCharacterPage.vue'
import {importChats,previewWorldInfo} from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({route:{params:{id:'c1'}},push:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>harness.route,useRouter:()=>({push:harness.push})}))
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
}))
compileRender(TavernCharacterPage,new URL('./TavernCharacterPage.vue',import.meta.url))

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
    expect(body).toContain('注入预览')
  })

  it('previews world-info injection for a sample message',async()=>{
    const {getCharacter}=await import('./api')
    vi.mocked(getCharacter).mockResolvedValue(card as never)
    vi.mocked(previewWorldInfo).mockResolvedValueOnce({
      matched:[{id:'w1',keys:['雨'],content:'雨夜，街上没人。',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'}],
      injection:'[世界书 · 角色设定之前]\n雨夜，街上没人。',
    })
    mount();await flush()
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
})
