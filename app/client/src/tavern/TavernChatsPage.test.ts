import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,onUnmounted} from 'vue'
import TavernChatsPage from './TavernChatsPage.vue'
import {listChats} from './api'
import LoadState from './LoadState.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn()}))
vi.mock('vue-router',()=>({useRouter:()=>({push:harness.push})}))
vi.mock('../stores/app',()=>({useApp:()=>({message:vi.fn()})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../api',()=>({authorizationHeaders:()=>({})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{MessageSquareText:stub}})
vi.mock('./api',()=>({listChats:vi.fn(),tavernAvatarUrl:(cardId:string)=>'/avatar/'+cardId}))

compileRender(TavernChatsPage,new URL('./TavernChatsPage.vue',import.meta.url))
compileRender(LoadState,new URL('./LoadState.vue',import.meta.url))

const chats=[
  {id:'group-1',cardId:'',title:'群聊',mode:'group',members:[{cardId:'c1',name:'林渡'},{cardId:'c2',name:'阿青'}],lastMessage:'雨停了',lastMessageAt:'2026-02-02T10:00:00'},
  {id:'chat-2',cardId:'c3',title:'作家的下午',characterName:'作家',lastMessage:'再写一段',lastMessageAt:'2026-02-01T09:00:00'},
]

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}
function mount(){
  host=node('root')
  app=renderer.createApp(TavernChatsPage)
  provideSfc(app)
  app.mount(host)
}

beforeEach(()=>{
  vi.stubGlobal('document',{activeElement:null})
  harness.push.mockReset()
  vi.mocked(listChats).mockReset().mockResolvedValue(chats as never)
})
afterEach(()=>{vi.useRealTimers();app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernChatsPage',()=>{
  it('lists chats newest first with member names and the last line',async()=>{
    mount();await flush()
    const body=text(host)
    expect(body).toContain('林渡 · 阿青')
    expect(body).toContain('雨停了')
    expect(body).toContain('作家')
    expect(body.indexOf('林渡 · 阿青')).toBeLessThan(body.indexOf('作家'))
  })

  it('opens a chat when the row is clicked',async()=>{
    mount();await flush()
    const row=descendants(host,item=>item.tag==='button'&&String(item.props.class??'').includes('chat-history-row'))[0]!
    row.props.onClick?.()
    expect(harness.push).toHaveBeenCalledWith('/chats/group-1')
  })

  it('shows an empty state when there are no chats',async()=>{
    vi.mocked(listChats).mockResolvedValue([])
    mount();await flush()
    expect(text(host)).toContain('还没有聊天')
  })

  it('shows a retryable error when the list endpoint is missing',async()=>{
    vi.mocked(listChats).mockRejectedValueOnce(new Error('服务端暂不支持聊天列表'))
    mount();await flush()
    expect(text(host)).toContain('服务端暂不支持聊天列表')
    const retry=descendants(host,item=>item.tag==='button'&&text(item).trim()==='重试')[0]!
    retry.props.onClick?.()
    await flush()
    expect(listChats).toHaveBeenCalledTimes(2)
  })

  describe('global chat search',()=>{
    async function searchFor(term:string,results:unknown[]){
      vi.useFakeTimers()
      vi.mocked(listChats).mockReset().mockResolvedValueOnce(chats as never).mockResolvedValueOnce(results as never)
      mount();await flush()
      const input=descendants(host,item=>item.props['aria-label']==='搜索聊天')[0]!
      input.props['onUpdate:modelValue']?.(term)
      await flush()
      vi.advanceTimersByTime(320)
      await flush()
    }

    it('debounces the query, calls the search endpoint and shows the count',async()=>{
      await searchFor('雨',[chats[1]])
      expect(vi.mocked(listChats).mock.calls[0]).toEqual([])
      expect(vi.mocked(listChats)).toHaveBeenLastCalledWith({q:'雨'})
      expect(text(host)).toContain('找到 1 个聊天')
    })

    it('shows the no-match empty state and clears the search',async()=>{
      await searchFor('不存在的词',[])
      expect(text(host)).toContain('没有匹配的聊天')
      vi.mocked(listChats).mockResolvedValueOnce(chats as never)
      const clear=descendants(host,item=>item.tag==='button'&&text(item).trim()==='清空搜索')[0]!
      clear.props.onClick?.()
      await flush()
      expect(vi.mocked(listChats)).toHaveBeenLastCalledWith()
      expect(text(host)).toContain('林渡 · 阿青')
      expect(text(host)).not.toContain('找到 0 个聊天')
    })

    it('highlights the matched keyword in the chat title',async()=>{
      await searchFor('作家',[chats[1]])
      const mark=descendants(host,item=>item.tag==='mark'&&text(item).trim()==='作家')
      expect(mark).toHaveLength(1)
    })
  })
})
