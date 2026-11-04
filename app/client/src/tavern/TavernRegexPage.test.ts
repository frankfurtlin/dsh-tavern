import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h} from 'vue'
import TavernRegexPage from './TavernRegexPage.vue'
import {createRegexScript,deleteRegexScript,listRegexScripts,updateRegexScript} from '../characters/api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({message:vi.fn()}))
vi.mock('vue-router',()=>({RouterLink:defineComponent({props:{to:{type:[String,Object]}},setup:(props,{slots})=>()=>h('a',{href:String(props.to)},slots.default?.())})}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{ArrowLeft:stub,Plus:stub,Save:stub,Trash2:stub}})
vi.mock('../characters/api',()=>({
  listRegexScripts:vi.fn(),
  createRegexScript:vi.fn(),
  updateRegexScript:vi.fn(),
  deleteRegexScript:vi.fn(),
}))
compileRender(TavernRegexPage,new URL('./TavernRegexPage.vue',import.meta.url))

const rule={id:'r1',name:'省略号收敛',pattern:'…+',replacement:'……',enabled:true,order:10}
let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernRegexPage)
  provideSfc(app)
  app.mount(host)
}
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim()===label)[0]}
function form(){return descendants(host,item=>item.tag==='form')[0]}

beforeEach(()=>{
  vi.stubGlobal('document',{activeElement:null})
  harness.message.mockReset()
  vi.mocked(listRegexScripts).mockReset().mockResolvedValue([rule])
  vi.mocked(createRegexScript).mockReset().mockResolvedValue({...rule,id:'r2'})
  vi.mocked(updateRegexScript).mockReset().mockResolvedValue(rule)
  vi.mocked(deleteRegexScript).mockReset().mockResolvedValue(undefined)
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernRegexPage',()=>{
  it('renders the saved rules',async()=>{
    mount();await flush()
    const body=text(host)
    expect(body).toContain('省略号收敛')
    expect(body).toContain('已启用')
    expect(listRegexScripts).toHaveBeenCalledTimes(1)
  })

  it('blocks an invalid pattern before calling the server',async()=>{
    mount();await flush()
    const setup=state()
    ;(setup.form as {name:string;pattern:string;replacement:string}).name='坏正则'
    ;(setup.form as {pattern:string}).pattern='('
    form().props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(createRegexScript).not.toHaveBeenCalled()
    expect(text(host)).toContain('正则表达式无效')
  })

  it('creates a rule and refreshes the list',async()=>{
    mount();await flush()
    const setup=state()
    ;(setup.form as {name:string;pattern:string;replacement:string;enabled:boolean;order:number}).name='删掉语气词'
    ;(setup.form as {pattern:string}).pattern='啊|呀'
    ;(setup.form as {replacement:string}).replacement=''
    form().props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(vi.mocked(createRegexScript).mock.calls[0][0]).toMatchObject({name:'删掉语气词',pattern:'啊|呀',replacement:'',enabled:true})
    expect(harness.message).toHaveBeenCalledWith('替换规则已保存。')
    expect(listRegexScripts).toHaveBeenCalledTimes(2)
  })

  it('deletes a rule only after the second click',async()=>{
    mount();await flush()
    button('删除').props.onClick?.()
    await flush()
    expect(deleteRegexScript).not.toHaveBeenCalled()
    const confirm=descendants(host,item=>item.tag==='button'&&text(item).trim()==='确认删除')[0]
    confirm.props.onClick?.()
    await flush()
    expect(deleteRegexScript).toHaveBeenCalledWith('r1')
    expect(harness.message).toHaveBeenCalledWith('替换规则已删除。')
  })
})
