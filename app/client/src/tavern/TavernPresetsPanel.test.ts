import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import TavernPresetsPanel from './TavernPresetsPanel.vue'
import {createPreset,deletePreset,listPresets,setDefaultPreset,updatePreset} from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({message:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>({message:harness.message})}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{Plus:stub,Save:stub,Star:stub,Trash2:stub}})
vi.mock('./api',()=>({
  listPresets:vi.fn(),
  createPreset:vi.fn(),
  updatePreset:vi.fn(),
  deletePreset:vi.fn(),
  setDefaultPreset:vi.fn(),
}))
compileRender(TavernPresetsPanel,new URL('./TavernPresetsPanel.vue',import.meta.url))

const preset={id:'p1',name:'细腻',temperature:0.8,topP:0.95,maxTokens:1024,stop:[],isDefault:true}
let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(){
  host=node('root')
  app=renderer.createApp(TavernPresetsPanel)
  provideSfc(app)
  app.mount(host)
}
function state(){return (app as unknown as {_instance:{setupState:Record<string,unknown>}})._instance.setupState}
function button(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim()===label)[0]}
function form(){return descendants(host,item=>item.tag==='form')[0]}

beforeEach(()=>{
  vi.stubGlobal('document',{activeElement:null})
  harness.message.mockReset()
  vi.mocked(listPresets).mockReset().mockResolvedValue([preset,{id:'p2',name:'冷硬',temperature:0.4,topP:0.9,maxTokens:512,stop:[]}])
  vi.mocked(createPreset).mockReset().mockResolvedValue({...preset,id:'p2',name:'冷硬'})
  vi.mocked(updatePreset).mockReset().mockResolvedValue(preset)
  vi.mocked(deletePreset).mockReset().mockResolvedValue(undefined)
  vi.mocked(setDefaultPreset).mockReset().mockResolvedValue(preset)
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('TavernPresetsPanel',()=>{
  it('lists presets with their ranges and default badge',async()=>{
    mount();await flush()
    const body=text(host)
    expect(body).toContain('细腻')
    expect(body).toContain('默认')
    expect(body).toContain('temperature 0.8')
    expect(descendants(host,item=>item.props['aria-label']==='预设管理')).toHaveLength(1)
  })

  it('explains a missing presets endpoint instead of crashing',async()=>{
    vi.mocked(listPresets).mockRejectedValueOnce(new Error('Not Found'))
    mount();await flush()
    expect(text(host)).toContain('Not Found')
  })

  it('validates ranges and creates a preset',async()=>{
    mount();await flush()
    button('新建预设').props.onClick?.()
    await flush()
    const setup=state()
    setup.formError as unknown
    // empty name blocks the request
    ;(setup.form as {name:string}).name=''
    form().props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(createPreset).not.toHaveBeenCalled()
    expect(text(host)).toContain('请填写预设名称')

    ;(setup.form as {name:string;temperature:number}).name='冷硬'
    ;(setup.form as {name:string;temperature:number}).temperature=2.5
    form().props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(createPreset).not.toHaveBeenCalled()
    expect(text(host)).toContain('temperature 需要在 0–2 之间')

    ;(setup.form as {temperature:number}).temperature=0.4
    form().props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(vi.mocked(createPreset).mock.calls[0][0]).toMatchObject({name:'冷硬',temperature:0.4,topP:0.95,maxTokens:1024})
    expect(harness.message).toHaveBeenCalledWith('预设已保存。')
    expect(listPresets).toHaveBeenCalledTimes(2)
  })

  it('backfills the edit form from the mapped prompt fields',async()=>{
    vi.mocked(listPresets).mockReset().mockResolvedValueOnce([{...preset,systemPrompt:'旧系统',mainPrompt:'主线',jailbreakPrompt:'越狱',postHistoryPrompt:'后置'}])
    mount();await flush()
    const edit=descendants(host,item=>item.tag==='button'&&text(item).trim()==='编辑')[0]
    edit.props.onClick?.()
    await flush()
    const form=state().form as {systemPrompt:string;mainPrompt:string;jailbreakPrompt:string;postHistoryPrompt:string;name:string}
    expect(form.name).toBe('细腻')
    expect(form.systemPrompt).toBe('旧系统')
    expect(form.mainPrompt).toBe('主线')
    expect(form.jailbreakPrompt).toBe('越狱')
    expect(form.postHistoryPrompt).toBe('后置')
  })

  it('new defaults are applied through the dedicated default endpoint',async()=>{
    mount();await flush()
    button('新建预设').props.onClick?.()
    await flush()
    const setup=state()
    ;(setup.form as {name:string;isDefault:boolean}).name='冷硬'
    ;(setup.form as {isDefault:boolean}).isDefault=true
    form().props.onSubmit?.({preventDefault(){}})
    await flush()
    expect(createPreset).toHaveBeenCalledTimes(1)
    expect((vi.mocked(createPreset).mock.calls[0][0] as {isDefault?:boolean}).isDefault).toBe(true)
    expect(setDefaultPreset).toHaveBeenCalledWith('p2')
    expect(harness.message).toHaveBeenCalledWith('预设已保存并设为默认。')
  })

  it('sets the default preset and deletes after confirmation',async()=>{
    mount();await flush()
    button('设为默认').props.onClick?.()
    await flush()
    expect(setDefaultPreset).toHaveBeenCalledWith('p2')
    expect(harness.message).toHaveBeenCalledWith('已设为默认预设。')

    button('删除').props.onClick?.()
    await flush()
    expect(deletePreset).not.toHaveBeenCalled()
    const confirm=descendants(host,item=>item.tag==='button'&&text(item).trim()==='确认删除')[0]
    confirm.props.onClick?.()
    await flush()
    expect(deletePreset).toHaveBeenCalledWith('p1')
    expect(harness.message).toHaveBeenCalledWith('预设已删除。')
  })
})
