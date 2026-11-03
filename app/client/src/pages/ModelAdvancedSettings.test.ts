import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,reactive} from 'vue'
import ModelsPage from './ModelAdvancedSettings.vue'
import PageHeader from '../workspace/PageHeader.vue'
import {mimoPreset} from '../api/profiles'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,deferred,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({api:vi.fn(),allPages:vi.fn(),valid:true}))
vi.mock('../foundation/VisionModelSettings.vue',()=>({default:{render:()=>null}}))
vi.mock('../api',()=>({api:mock.api,allPages:mock.allPages,newKey:()=> 'explicit-only'}));vi.mock('../world/SceneModelSettings.vue',()=>({default:{render:()=>null}}))
vi.mock('../stores/app',()=>({useApp:()=>reactive({online:true,message:vi.fn()})}));vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>mock.valid})}));vi.mock('../api/task-watch',()=>({watchTask:vi.fn(),taskFinished:vi.fn()}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({emits:['close'],setup:(_,{slots,emit})=>()=>h('section',[h('button',{'aria-label':'close-model-sheet',onClick:()=>emit('close')},'关闭'),slots.default?.()])})}))
// Usage has its own actual SFC tests; this suite exercises profile drafts.
vi.mock('../components/UsageRecords.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
compileRender(ModelsPage,new URL('./ModelAdvancedSettings.vue',import.meta.url))
compileRender(PageHeader,new URL('../workspace/PageHeader.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>,root:Host
function mount(){root=node('root');app=renderer.createApp(ModelsPage);provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
function button(label:string){return descendants(root,n=>n.tag==='button'&&text(n).includes(label))[0]}
it('does not report missing models while the first profile request is pending',async()=>{
  const pending=deferred<any>();mock.api.mockReturnValueOnce(pending.promise);mount();await flush()
  expect(text(root)).toContain('正在加载模型通道');expect(text(root)).not.toContain('平台模型暂未就绪')
  pending.resolve({items:[],allowedHosts:['opencode.ai']});await flush()
  expect(text(root)).toContain('平台模型暂未就绪');expect(text(root)).not.toContain('正在加载模型通道')
})
beforeEach(()=>{mock.valid=true;vi.stubGlobal('document',{activeElement:null});mock.api.mockReset().mockResolvedValue({items:[],allowedHosts:['opencode.ai']});mock.allPages.mockReset().mockResolvedValue([])})
afterEach(()=>{app?.unmount();vi.unstubAllGlobals()})
it('shows the MiMo preset with advanced fields collapsed and never tests a provider merely by opening settings',async()=>{
  mount();await flush();button('添加通道').props.onClick();await flush();const advanced=descendants(root,n=>n.tag==='details'&&text(n).includes('高级设置与用量上限'))[0]
  expect(advanced.props.open).toBe(false);expect(text(root)).toContain('OpenCode Go · MiMo');expect(descendants(root,n=>n.tag==='input'&&n.props.type==='password')[0].props.required).toBe(true)
  expect(mock.api).toHaveBeenCalledExactlyOnceWith('/model-profiles');expect(mock.api.mock.calls.every(c=>!c[1]?.method||c[1].method==='GET')).toBe(true)
})
it('posts the existing preset only after an explicit form save and clears the transient key',async()=>{
  mount();await flush();button('添加通道').props.onClick();await flush();const keyInput=descendants(root,n=>n.tag==='input'&&n.props.type==='password')[0];keyInput.props['onUpdate:modelValue']('synthetic-key-only')
  mock.api.mockResolvedValueOnce({id:'new-profile'}).mockResolvedValueOnce({items:[],allowedHosts:[]});descendants(root,n=>n.tag==='form')[0].props.onSubmit({preventDefault(){}});await flush()
  expect(mock.api.mock.calls[1]).toEqual(['/model-profiles',{method:'POST',body:expect.objectContaining({baseUrl:mimoPreset.baseUrl,modelId:mimoPreset.modelId,key:'synthetic-key-only',outputParameter:mimoPreset.outputParameter})}]);expect(descendants(root,n=>n.tag==='input'&&n.props.type==='password')).toHaveLength(0)
})
it('a late save from a closed form does not close or clear a newly opened model draft',async()=>{
  mount();await flush();button('添加通道').props.onClick();await flush();const gate=deferred<unknown>();mock.api.mockReturnValueOnce(gate.promise);descendants(root,n=>n.tag==='form')[0].props.onSubmit({preventDefault(){}});await flush()
  descendants(root,n=>n.props['aria-label']==='close-model-sheet')[0].props.onClick();button('添加通道').props.onClick();await flush();const keyInput=descendants(root,n=>n.tag==='input'&&n.props.type==='password')[0];keyInput.props['onUpdate:modelValue']('new-unsent-synthetic-key');gate.resolve({id:'old-profile'});await flush()
  expect(descendants(root,n=>n.tag==='form')).toHaveLength(1);expect(mock.api).toHaveBeenCalledTimes(2)
  descendants(root,n=>n.tag==='form')[0].props.onSubmit({preventDefault(){}});await flush();expect(mock.api.mock.calls[2][1].body.key).toBe('new-unsent-synthetic-key')
})
