import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import GroupListPage from './GroupListPage.vue'
import * as groupApi from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn(),message:vi.fn()}))
vi.mock('./api',()=>({listGroupChats:vi.fn(),deleteGroupChat:vi.fn()}))
vi.mock('vue-router',()=>({
  RouterLink:defineComponent({props:{to:[String,Object]},setup:(props,{slots})=>()=>h('a',{href:props.to},slots.default?.())}),
  useRouter:()=>({push:harness.push}),
}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{MessagesSquare:stub,Plus:stub,Trash2:stub}})
vi.mock('../workspace/PageHeader.vue',()=>({default:defineComponent({props:{title:String,description:String},setup:(props,{slots})=>()=>h('header',[props.title,props.description||'',slots.default?.()])})}))
compileRender(GroupListPage,new URL('./GroupListPage.vue',import.meta.url))

const chats:Awaited<ReturnType<typeof groupApi.listGroupChats>>=[
  {id:'g1',title:'林渡、沈青的群聊',cards:[{id:'c1',name:'林渡'},{id:'c2',name:'沈青'}],lastText:'你好呀，今天想聊点什么？',updatedAt:'2026-11-04T02:00:00Z'},
  {id:'g2',title:'夜谈',cards:[{id:'c3',name:'白露'}],lastText:'',updatedAt:''},
]
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){root=node('root');app=renderer.createApp(GroupListPage);provideSfc(app);app.mount(root)}
function buttons(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))}

beforeEach(()=>{
  vi.mocked(groupApi.listGroupChats).mockReset().mockResolvedValue([])
  vi.mocked(groupApi.deleteGroupChat).mockReset().mockResolvedValue(undefined)
  harness.push.mockReset().mockResolvedValue(undefined)
  harness.message.mockReset()
})
afterEach(()=>{app?.unmount();app=undefined;vi.restoreAllMocks()})

it('lists group chats with participants, last line and a link into the room',async()=>{
  vi.mocked(groupApi.listGroupChats).mockResolvedValueOnce(chats)
  mount();await flush()
  expect(text(root)).toContain('林渡、沈青的群聊')
  expect(text(root)).toContain('林渡、沈青')
  expect(text(root)).toContain('你好呀，今天想聊点什么？')
  expect(text(root)).toContain('还没有消息，进去打个招呼吧。')
  const link=descendants(root,item=>item.tag==='a'&&item.props.href==='/characters/groups/g1')[0]
  expect(link).toBeTruthy()
})

it('shows the empty state with a link back to the card library',async()=>{
  mount();await flush()
  expect(text(root)).toContain('还没有群聊')
  expect(text(root)).toContain('去角色卡库挑选角色')
  const link=descendants(root,item=>item.tag==='a'&&item.props.href==='/characters')[0]
  expect(link).toBeTruthy()
})

it('deletes a group chat only after the two-step confirmation',async()=>{
  vi.mocked(groupApi.listGroupChats).mockResolvedValueOnce(chats).mockResolvedValueOnce([chats[1]])
  mount();await flush()
  buttons('删除')[0].props.onClick()
  await flush()
  expect(groupApi.deleteGroupChat).not.toHaveBeenCalled()
  expect(text(root)).toContain('确认删除')
  buttons('确认删除')[0].props.onClick()
  await flush()
  expect(groupApi.deleteGroupChat).toHaveBeenCalledWith('g1')
  expect(harness.message).toHaveBeenCalledWith(expect.stringContaining('林渡、沈青的群聊'))
  expect(text(root)).not.toContain('林渡、沈青的群聊')
  expect(text(root)).toContain('夜谈')
})

it('surfaces a list error and reloads on demand',async()=>{
  vi.mocked(groupApi.listGroupChats).mockRejectedValueOnce(new Error('群聊列表暂时不可用')).mockResolvedValueOnce(chats)
  mount();await flush()
  expect(text(root)).toContain('群聊列表暂时不可用')
  buttons('重新载入群聊')[0].props.onClick()
  await flush()
  expect(text(root)).toContain('林渡、沈青的群聊')
})
