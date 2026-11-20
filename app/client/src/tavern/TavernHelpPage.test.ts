import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import TavernHelpPage from './TavernHelpPage.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn()}))
vi.mock('vue-router',()=>({useRouter:()=>({push:harness.push})}))

compileRender(TavernHelpPage,new URL('./TavernHelpPage.vue',import.meta.url))

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernHelpPage)
  provideSfc(app)
  app.mount(host)
}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim()===label)[0]}

beforeEach(()=>{
  harness.push.mockReset()
  vi.stubGlobal('window',{localStorage:{getItem:vi.fn(()=>null),setItem:vi.fn(),removeItem:vi.fn()}})
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernHelpPage',()=>{
  it('renders every help section',async()=>{
    mount();await flush()
    const body=text(host)
    for(const title of ['开始聊天','DSH 工具条','导入角色卡','预设卡','导出与备份','正则替换','世界书注入'])expect(body).toContain(title)
    expect(body).toContain('最近 12 条消息')
  })

  it('lists every section in the table of contents',async()=>{
    mount();await flush()
    const links=descendants(host,item=>item.tag==='a'&&String(item.props.href||'').startsWith('#help-'))
    expect(links.map(link=>link.props.href)).toEqual(['#help-start','#help-dsh','#help-import','#help-presets','#help-backup','#help-regex','#help-lore'])
    const ids=descendants(host,item=>item.tag==='section'&&String(item.props.id||'').startsWith('help-')).length
    expect(ids).toBe(7)
    expect(descendants(host,item=>String(item.props.class||'')==='help-body').length).toBe(7)
  })

  it('offers a back button to settings',async()=>{
    mount();await flush()
    button('返回设置')!.props.onClick?.()
    expect(harness.push).toHaveBeenCalledWith('/settings')
  })

  it('reopens the onboarding guide from the help page',async()=>{
    mount();await flush()
    button('重新查看引导')!.props.onClick?.()
    await flush()
    expect(window.localStorage.removeItem).toHaveBeenCalledWith('tavern:guide-seen')
    expect(harness.push).toHaveBeenCalledWith({path:'/',query:{guide:'1'}})
  })
})

describe('TavernHelpPage A1 layout',()=>{
  it('renders without a duplicate page heading',async()=>{
    mount();await flush()
    expect(descendants(host,item=>item.tag==='h1')).toHaveLength(0)
  })
})
