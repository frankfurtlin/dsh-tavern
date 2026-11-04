import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted,reactive} from 'vue'
import GroupChatPage from './GroupChatPage.vue'
import * as groupApi from './api'
import type {GroupCard,GroupMessage} from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn(),route:null as any}))
vi.mock('./api',()=>({getGroupChat:vi.fn(),sendGroupMessage:vi.fn(),cardAvatarBlob:vi.fn()}))
vi.mock('vue-router',()=>({
  useRoute:()=>harness.route,
  useRouter:()=>({push:harness.push}),
  RouterLink:defineComponent({props:{to:[String,Object]},setup:(props,{slots})=>()=>h('a',{href:props.to},slots.default?.())}),
}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{Send:stub,Users:stub}})
vi.mock('../workspace/PageHeader.vue',()=>({default:defineComponent({props:{title:String,description:String},setup:(props,{slots})=>()=>h('header',[props.title,props.description||'',slots.default?.()])})}))
compileRender(GroupChatPage,new URL('./GroupChatPage.vue',import.meta.url))

const cards:GroupCard[]=[{id:'c1',name:'林渡'},{id:'c2',name:'沈青'}]
function detail(messages:GroupMessage[]):Awaited<ReturnType<typeof groupApi.getGroupChat>>{
  return {chat:{id:'g1',title:'林渡、沈青的群聊',cardIds:['c1','c2']},cards,messages}
}
const userMessage:GroupMessage={id:'m1',role:'user',text:'你们好'}
const assistantMessage:GroupMessage={id:'m2',role:'assistant',speakerCardId:'c1',speakerName:'林渡',text:'你好呀，今天想聊点什么？',status:'saved'}
const sentResult:Awaited<ReturnType<typeof groupApi.sendGroupMessage>>={taskId:'t1',chatId:'g1',speakerCardId:'c1',speakerName:'林渡'}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){root=node('root');app=renderer.createApp(GroupChatPage);provideSfc(app);app.mount(root)}
function button(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0]}
function form(){return descendants(root,item=>item.tag==='form')[0]}
function typeDraft(value:string){const area=descendants(root,item=>item.tag==='textarea')[0];area.props['onUpdate:modelValue'](value)}
async function submit(){form().props.onSubmit({preventDefault(){}});await flush()}

beforeEach(()=>{
  vi.useFakeTimers()
  vi.stubGlobal('document',{activeElement:null})
  harness.route=reactive({params:{id:'g1'}})
  harness.push.mockReset().mockResolvedValue(undefined)
  vi.mocked(groupApi.getGroupChat).mockReset()
  vi.mocked(groupApi.sendGroupMessage).mockReset()
  vi.mocked(groupApi.cardAvatarBlob).mockReset().mockResolvedValue(null)
})
afterEach(()=>{app?.unmount();app=undefined;vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()})

it('renders participants, speaker bubbles and the next speaker on load',async()=>{
  vi.mocked(groupApi.getGroupChat).mockResolvedValueOnce(detail([userMessage,assistantMessage]))
  mount();await flush()
  expect(text(root)).toContain('林渡、沈青的群聊')
  expect(text(root)).toContain('参与者：林渡、沈青')
  expect(text(root)).toContain('你们好')
  expect(text(root)).toContain('你好呀，今天想聊点什么？')
  expect(text(root)).toContain('下一位：沈青')
  expect(groupApi.cardAvatarBlob).toHaveBeenCalledWith('c1')
})

it('shows an empty conversation state before the first message',async()=>{
  vi.mocked(groupApi.getGroupChat).mockResolvedValueOnce(detail([]))
  mount();await flush()
  expect(text(root)).toContain('还没有人说话')
  expect(text(root)).toContain('林渡 会先回应')
  expect(messagesCount()).toBe(0)
})

function messagesCount(){return descendants(root,item=>item.tag==='article'&&String(item.props.class||'').includes('group-message')).length}

it('sends a message, polls and reveals the new assistant reply',async()=>{
  vi.mocked(groupApi.getGroupChat)
    .mockResolvedValueOnce(detail([]))
    .mockResolvedValueOnce(detail([userMessage]))
    .mockResolvedValueOnce(detail([userMessage,assistantMessage]))
  vi.mocked(groupApi.sendGroupMessage).mockResolvedValueOnce(sentResult)
  mount();await flush()
  typeDraft('你们好');await flush()
  await submit()
  expect(groupApi.sendGroupMessage).toHaveBeenCalledWith('g1','你们好')
  expect(text(root)).toContain('林渡 正在回应')
  await vi.advanceTimersByTimeAsync(1000)
  await flush()
  expect(groupApi.getGroupChat).toHaveBeenCalledTimes(3)
  expect(text(root)).toContain('你好呀，今天想聊点什么？')
  expect(text(root)).toContain('下一位：沈青')
  expect(text(root)).not.toContain('正在回应')
})

it('stops polling and reports a failed generation status',async()=>{
  const failed:GroupMessage={id:'m3',role:'assistant',speakerCardId:'c1',speakerName:'林渡',text:'',status:'failed'}
  vi.mocked(groupApi.getGroupChat)
    .mockResolvedValueOnce(detail([]))
    .mockResolvedValueOnce(detail([userMessage]))
    .mockResolvedValueOnce(detail([userMessage,failed]))
  vi.mocked(groupApi.sendGroupMessage).mockResolvedValueOnce(sentResult)
  mount();await flush()
  typeDraft('你们好');await flush()
  await submit()
  await vi.advanceTimersByTimeAsync(1000)
  await flush()
  expect(text(root)).toContain('这条回复生成失败')
  expect(text(root)).toContain('这条回复生成失败，可以再发一条试试。')
  expect(text(root)).not.toContain('正在回应')
})

it('surfaces a 409 pending-generation error when sending is rejected',async()=>{
  vi.mocked(groupApi.getGroupChat).mockResolvedValueOnce(detail([userMessage,assistantMessage]))
  vi.mocked(groupApi.sendGroupMessage).mockRejectedValueOnce(Object.assign(new Error('当前还有一条回复正在生成，请稍后再发。'),{status:409}))
  mount();await flush()
  typeDraft('再说一句');await flush()
  await submit()
  expect(text(root)).toContain('发送失败：当前还有一条回复正在生成，请稍后再发。')
})

it('shows a load error and recovers through the retry button',async()=>{
  vi.mocked(groupApi.getGroupChat).mockRejectedValueOnce(new Error('群聊不存在')).mockResolvedValueOnce(detail([userMessage]))
  mount();await flush()
  expect(text(root)).toContain('群聊不存在')
  button('重新载入群聊').props.onClick()
  await flush()
  expect(text(root)).toContain('你们好')
})
