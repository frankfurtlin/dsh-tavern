import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h} from 'vue'
import TavernSettingsPage from './TavernSettingsPage.vue'
import {listPersonas} from '../characters/api'
import LoadState from './LoadState.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({message:vi.fn()}))
vi.mock('vue-router',()=>({RouterLink:defineComponent({props:{to:{type:[String,Object]}},setup:(props,{slots})=>()=>h('a',{href:String(props.to)},slots.default?.())})}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{BookMarked:stub,Brush:stub,Download:stub,HelpCircle:stub,UserRound:stub}})
vi.mock('../characters/PersonaControl.vue',()=>({default:defineComponent({props:{modelValue:{type:String,default:null}},setup:()=>()=>h('section',{'aria-label':'人设'},'人设控件')})}))
vi.mock('./TavernPresetsPanel.vue',()=>({default:defineComponent({setup:()=>()=>h('section',{'aria-label':'预设管理'},'预设面板')})}))
vi.mock('../characters/api',()=>({listPersonas:vi.fn()}))
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

beforeEach(()=>{
  vi.stubGlobal('window',{localStorage:{getItem:vi.fn(()=>null),setItem:vi.fn(),removeItem:vi.fn()}})
  vi.stubGlobal('document',{activeElement:null})
  vi.mocked(listPersonas).mockReset().mockResolvedValue([{id:'p1',name:'旅人',description:'走过很多路'},{id:'p2',name:'读者',description:''}])
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernSettingsPage',()=>{
  it('replaces the placeholder with persona, presets and regex sections',async()=>{
    mount();await flush()
    const body=text(host)
    expect(body).toContain('人设控件')
    expect(body).toContain('预设面板')
    expect(body).toContain('输出替换规则')
    expect(body).toContain('导入与导出')
    expect(body).toContain('旅人')
    expect(body).toContain('读者')
    expect(listPersonas).toHaveBeenCalledTimes(1)
  })

  it('persists the default persona locally',async()=>{
    mount();await flush()
    const select=descendants(host,item=>item.props['aria-label']==='默认人设')[0]
    select.props.onChange?.({target:{value:'p2'}})
    await flush()
    expect(window.localStorage.setItem).toHaveBeenCalledWith('tavern:default-persona','p2')
    expect(text(host)).toContain('默认人设：读者')
  })

  it('shows a persona load error without crashing',async()=>{
    vi.mocked(listPersonas).mockRejectedValueOnce(new Error('502 人设服务暂不可用'))
    mount();await flush()
    expect(text(host)).toContain('502 人设服务暂不可用')
    expect(text(host)).toContain('返回角色卡')
  })
})
