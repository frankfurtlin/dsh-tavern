import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h} from 'vue'
import TavernSettingsPage from './TavernSettingsPage.vue'
import {listPresets,updatePreset} from './api'
import LoadState from './LoadState.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({message:vi.fn()}))
vi.mock('vue-router',()=>({RouterLink:defineComponent({props:{to:{type:[String,Object]}},setup:(props,{slots})=>()=>h('a',{href:String(props.to)},slots.default?.())})}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{BookMarked:stub,Brush:stub,Download:stub,HelpCircle:stub,UserRound:stub}})
vi.mock('../characters/PersonaSettings.vue',()=>({default:defineComponent({setup:()=>()=>h('div',{'aria-label':'人设设置'},'人设设置')})}))
vi.mock('./TavernPresetsPanel.vue',()=>({default:defineComponent({setup:()=>()=>h('section',{'aria-label':'预设管理'},'预设面板')})}))
vi.mock('./api',()=>({listPresets:vi.fn(),updatePreset:vi.fn()}))
compileRender(TavernSettingsPage,new URL('./TavernSettingsPage.vue',import.meta.url))
compileRender(LoadState,new URL('./LoadState.vue',import.meta.url))

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernSettingsPage)
  provideSfc(app)
  app.mount(host)
}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim()===label)[0]}
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}

beforeEach(()=>{
  vi.stubGlobal('window',{localStorage:{getItem:vi.fn(()=>null),setItem:vi.fn(),removeItem:vi.fn()}})
  vi.stubGlobal('document',{activeElement:null})
  vi.mocked(listPresets).mockReset().mockResolvedValue([{id:'pre-1',name:'默认',isDefault:true,temperature:0.7,topP:0.9,maxTokens:768}])
  vi.mocked(updatePreset).mockReset().mockImplementation(async(_id,patch)=>({id:'pre-1',name:'默认',isDefault:true,...patch}))
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernSettingsPage',()=>{
  it('replaces the placeholder with persona, presets and regex sections',async()=>{
    mount();await flush()
    const body=text(host)
    expect(body).toContain('人设设置')
    expect(body).toContain('预设面板')
    expect(body).toContain('输出替换规则')
    expect(body).toContain('角色卡库')
    for(const title of ['我是谁','采样参数','预设','本地数据'])expect(body).toContain(title)
    expect(body).toContain('温度')
    expect(body).toContain('核采样')
    expect(body).toContain('最大长度')
    expect(body).toContain('重复惩罚')
    expect(body).toContain('高级')
    expect(body).not.toContain('默认人设')
    expect(body).not.toContain('当前人设')
  })

  it('saves the default preset sampling without leaving the page',async()=>{
    mount();await flush()
    ;(state().sampling as {temperature:number}).temperature=1.1
    await flush()
    button('保存采样参数')!.props.onClick?.()
    await flush()
    expect(updatePreset).toHaveBeenCalledWith('pre-1',{temperature:1.1,topP:0.9,maxTokens:768})
    expect(harness.message).toHaveBeenCalledWith('采样参数已保存。')
    expect(text(host)).toContain('采样参数已保存。')
  })

  it('keeps the persona block inside a single settings card',async()=>{
    mount();await flush()
    const blocks=descendants(host,item=>String(item.props.class??'').includes('settings-card'))
    expect(blocks.length).toBeGreaterThanOrEqual(1)
    const personaCard=descendants(host,item=>String(item.props.class??'')==='settings-card')[0]!
    expect(descendants(personaCard,item=>item.props['aria-label']==='人设设置')).toHaveLength(1)
    expect(text(host)).toContain('返回角色卡')
  })
})

describe('TavernSettingsPage A1 layout',()=>{
  it('renders without a duplicate page heading',async()=>{
    mount();await flush()
    expect(descendants(host,item=>item.tag==='h1')).toHaveLength(0)
  })
})
