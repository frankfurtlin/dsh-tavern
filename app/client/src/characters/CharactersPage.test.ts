import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import CharactersPage from './CharactersPage.vue'
import * as cardApi from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn(),message:vi.fn()}))
vi.mock('./api',()=>({listCards:vi.fn(),importCard:vi.fn(),startCardChat:vi.fn(),deleteCard:vi.fn(),cardAvatarBlob:vi.fn()}))
vi.mock('vue-router',()=>({useRouter:()=>({push:harness.push})}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{Upload:stub,Users:stub,MessageCircle:stub,Pencil:stub,Replace:stub,Trash2:stub,Heart:stub}})
vi.mock('../workspace/PageHeader.vue',()=>({default:defineComponent({props:{title:String},setup:(p,{slots})=>()=>h('header',[p.title,slots.default?.()])})}))
compileRender(CharactersPage,new URL('./CharactersPage.vue',import.meta.url))

const card={id:'c1',name:'林渡',description:'热爱相声的旧书摊主，说话总带着包袱。',personality:'机智',scenario:'书摊',tags:['相声','旧书','北平'],creator:'佚名',source:'png' as const,hasAvatar:true}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){root=node('root');app=renderer.createApp(CharactersPage);provideSfc(app);app.mount(root)}
function button(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0]}

beforeEach(()=>{
  vi.mocked(cardApi.listCards).mockReset().mockResolvedValue([])
  vi.mocked(cardApi.importCard).mockReset()
  vi.mocked(cardApi.startCardChat).mockReset()
  vi.mocked(cardApi.deleteCard).mockReset().mockResolvedValue(undefined)
  vi.mocked(cardApi.cardAvatarBlob).mockReset().mockResolvedValue(null)
  harness.push.mockReset().mockResolvedValue(undefined)
  harness.message.mockReset()
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:card-c1')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
})
afterEach(()=>{app?.unmount();app=undefined;vi.restoreAllMocks()})

it('shows the empty state until a SillyTavern card exists',async()=>{
  mount();await flush()
  expect(text(root)).toContain('角色卡库')
  expect(text(root)).toContain('还没有角色卡')
  expect(text(root)).toContain('选择 PNG 或 JSON 文件')
})

it('renders the card grid with description, tags and a bearer-fetched avatar',async()=>{
  vi.mocked(cardApi.listCards).mockResolvedValueOnce([card])
  const blob=new Blob(['png'],{type:'image/png'})
  vi.mocked(cardApi.cardAvatarBlob).mockResolvedValueOnce(blob)
  mount();await flush()
  expect(text(root)).toContain('林渡')
  expect(text(root)).toContain('热爱相声的旧书摊主')
  expect(text(root)).toContain('旧书')
  expect(cardApi.cardAvatarBlob).toHaveBeenCalledWith('c1')
  const image=descendants(root,item=>item.tag==='img')[0]
  expect(image.props.src).toBe('blob:card-c1')
  expect(image.props.alt).toBe('林渡头像')
})

it('uploads the picked png and reloads the library',async()=>{
  vi.mocked(cardApi.listCards).mockResolvedValueOnce([]).mockResolvedValueOnce([card])
  vi.mocked(cardApi.importCard).mockResolvedValueOnce({card})
  mount();await flush()
  const input=descendants(root,item=>item.tag==='input')[0]
  const file=new File([new Uint8Array([1])],'card.png',{type:'image/png'})
  input.props.onChange({target:{files:[file],value:'C:\\fakepath\\card.png'}})
  await flush()
  expect(cardApi.importCard).toHaveBeenCalledTimes(1)
  expect(vi.mocked(cardApi.importCard).mock.calls[0][0]).toBe(file)
  expect(cardApi.listCards).toHaveBeenCalledTimes(2)
  expect(harness.message).toHaveBeenCalledWith(expect.stringContaining('林渡'))
  expect(text(root)).toContain('林渡')
})

it('surfaces the server message when an import is rejected',async()=>{
  vi.mocked(cardApi.importCard).mockRejectedValueOnce(new Error('无法识别这张角色卡，请确认文件内容。'))
  mount();await flush()
  const input=descendants(root,item=>item.tag==='input')[0]
  input.props.onChange({target:{files:[new File(['x'],'bad.png',{type:'image/png'})],value:''}})
  await flush()
  expect(text(root)).toContain('无法识别这张角色卡，请确认文件内容。')
})

it('opens the created session route when chatting with a card',async()=>{
  vi.mocked(cardApi.listCards).mockResolvedValueOnce([card])
  vi.mocked(cardApi.startCardChat).mockResolvedValueOnce({sessionId:'session-9',cardId:'c1'})
  mount();await flush()
  button('开聊').props.onClick()
  await flush()
  expect(cardApi.startCardChat).toHaveBeenCalledWith('c1')
  expect(harness.push).toHaveBeenCalledWith('/conversations/world/session-9')
})

it('deletes a card and reloads the remaining library',async()=>{
  vi.mocked(cardApi.listCards).mockResolvedValueOnce([card]).mockResolvedValueOnce([])
  mount();await flush()
  button('删除').props.onClick()
  await flush()
  expect(cardApi.deleteCard).toHaveBeenCalledWith('c1')
  expect(text(root)).toContain('还没有角色卡')
})

it('opens the card detail editor and the replacement-rule page',async()=>{
  vi.mocked(cardApi.listCards).mockResolvedValueOnce([card])
  mount();await flush()
  button('编辑').props.onClick()
  await flush()
  expect(harness.push).toHaveBeenCalledWith('/characters/c1')
  const rules=descendants(root,item=>item.tag==='button'&&text(item).includes('输出替换规则'))[0]
  rules.props.onClick()
  await flush()
  expect(harness.push).toHaveBeenCalledWith('/characters/regex')
})

it('groups long-term companions in a pinned section above the rest',async()=>{
  const companion={...card,id:'c2',name:'沈青',companion:true,hasAvatar:false}
  vi.mocked(cardApi.listCards).mockResolvedValueOnce([card,companion])
  mount();await flush()
  const body=text(root)
  expect(body).toContain('长期伙伴')
  expect(body).toContain('全部角色卡')
  expect(body.indexOf('沈青')).toBeLessThan(body.indexOf('林渡'))
  expect(descendants(root,item=>Boolean(item.props['data-card-id'])).length).toBe(2)
})
