import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,onUnmounted} from 'vue'
import TavernCharactersPage from './TavernCharactersPage.vue'
import {createGroupChat,listCharacters} from './api'
import LoadState from './LoadState.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn(),message:vi.fn()}))
vi.mock('vue-router',()=>({useRouter:()=>({push:harness.push})}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message,user:{id:'u1'}})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{Check:stub,History:stub,MessagesSquare:stub,Search:stub,Upload:stub}})
vi.mock('./api',()=>({
  listCharacters:vi.fn(),
  characterAvatarBlob:vi.fn(async()=>null),
  importCharacter:vi.fn(),
  listCharacterChats:vi.fn(),
  createCharacterChat:vi.fn(),
  createGroupChat:vi.fn(),
}))

compileRender(TavernCharactersPage,new URL('./TavernCharactersPage.vue',import.meta.url))
compileRender(LoadState,new URL('./LoadState.vue',import.meta.url))

const cards=Array.from({length:6},(_,index)=>({
  id:'c'+(index+1),name:'角色'+String.fromCharCode(65+index),description:'简介'+(index+1),tags:[],hasAvatar:false,
}))

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernCharactersPage)
  provideSfc(app)
  app.mount(host)
}
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}
function selectButton(name:string){return descendants(host,item=>item.props['aria-label']==='选择 '+name+' 加入群聊')[0]}
function groupButton(){return descendants(host,item=>item.tag==='button'&&text(item).trim().startsWith('发起群聊'))[0]}

beforeEach(()=>{
  harness.push.mockReset()
  harness.message.mockReset()
  vi.mocked(listCharacters).mockReset().mockResolvedValue(cards as never)
  vi.mocked(createGroupChat).mockReset().mockResolvedValue({id:'group-1',cardId:'',title:'群聊',mode:'group'} as never)
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernCharactersPage group selection',()=>{
  it('creates a group chat from two selected cards and opens it',async()=>{
    mount();await flush()
    selectButton('角色A')!.props.onClick?.()
    await flush()
    selectButton('角色B')!.props.onClick?.()
    await flush()
    const group=groupButton()!
    expect(text(group)).toContain('发起群聊（2）')
    group.props.onClick?.()
    await flush()
    expect(createGroupChat).toHaveBeenCalledWith(['c1','c2'])
    expect(harness.push).toHaveBeenCalledWith('/chats/group-1')
  })

  it('disables the group action until at least two cards are selected',async()=>{
    mount();await flush()
    selectButton('角色A')!.props.onClick?.()
    await flush()
    expect(groupButton()!.props.disabled).toBe(true)
  })

  it('stops at five members and explains the limit',async()=>{
    mount();await flush()
    for(const name of ['角色A','角色B','角色C','角色D','角色E']){
      selectButton(name)!.props.onClick?.()
      await flush()
    }
    selectButton('角色F')!.props.onClick?.()
    await flush()
    expect(harness.message).toHaveBeenCalledWith('群聊最多选择 5 张角色卡。')
    expect((state().selected as string[])).toEqual(['c1','c2','c3','c4','c5'])
  })

  it('clears the selection from the action bar',async()=>{
    mount();await flush()
    selectButton('角色A')!.props.onClick?.()
    selectButton('角色B')!.props.onClick?.()
    await flush()
    const clear=descendants(host,item=>item.tag==='button'&&text(item).trim()==='清空')[0]
    clear!.props.onClick?.()
    await flush()
    expect(state().selected).toEqual([])
    expect(groupButton()).toBeUndefined()
  })

  it('shows a shared empty state with an import call to action',async()=>{
    vi.mocked(listCharacters).mockResolvedValue([])
    mount();await flush()
    const body=text(host)
    expect(body).toContain('还没有角色卡')
    expect(body).toContain('导入第一张角色卡')
    expect(descendants(host,item=>item.tag==='input'&&item.props['aria-label']==='导入第一张角色卡')).toHaveLength(1)
  })

  it('shows the shared retryable error state and reloads on retry',async()=>{
    vi.mocked(listCharacters).mockRejectedValueOnce(new Error('角色卡接口暂不可用'))
    mount();await flush()
    expect(text(host)).toContain('加载失败')
    expect(text(host)).toContain('角色卡接口暂不可用')
    const retry=descendants(host,item=>item.tag==='button'&&text(item).trim()==='重试')[0]
    retry!.props.onClick?.()
    await flush()
    expect(listCharacters).toHaveBeenCalledTimes(2)
    expect(text(host)).toContain('角色A')
  })

describe('TavernCharactersPage preset badge',()=>{
  it('marks builtin cards with a compact preset badge only',async()=>{
    vi.mocked(listCharacters).mockResolvedValue([
      {...cards[0],builtin:true,builtinSlug:'foreverse-rin'},
      {...cards[1],builtin:false},
    ] as never)
    mount();await flush()
    const badges=descendants(host,item=>String(item.props.class||'').includes('tavern-card-preset'))
    expect(badges).toHaveLength(1)
    expect(text(badges[0]).trim()).toBe('预设')
    expect(text(host)).not.toContain('Foreverse')
  })
})
})
