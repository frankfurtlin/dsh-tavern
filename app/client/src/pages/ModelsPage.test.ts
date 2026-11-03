import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h} from 'vue'
import ModelsPage from './ModelsPage.vue'
import PageHeader from '../workspace/PageHeader.vue'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,deferred,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({api:vi.fn(),valid:true}))
vi.mock('../api',()=>({api:mock.api}));vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>mock.valid})}))
vi.mock('../components/UsageRecords.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
compileRender(ModelsPage,new URL('./ModelsPage.vue',import.meta.url));compileRender(PageHeader,new URL('../workspace/PageHeader.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>,root:Host
function mount(){root=node('root');app=renderer.createApp(ModelsPage);provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
beforeEach(()=>{mock.valid=true;vi.stubGlobal('document',{activeElement:null});mock.api.mockReset().mockImplementation(async path=>path==='/model-profiles'?{items:[{id:'text',modelId:'default-text',isDefault:true,status:'active',hasKey:true,purpose:'text'}]}:path==='/scene-profile'?{enabled:true}:{enabled:true,modelId:'default-vision'})})
afterEach(()=>{app?.unmount();vi.unstubAllGlobals()})
it('shows all beta defaults without a key form, a provider test or advanced requests',async()=>{
 mount();await flush();expect(text(root)).toContain('模型已准备好');expect(text(root)).toContain('default-text');expect(text(root)).toContain('Agnes Image 2.5 Flash');expect(text(root)).toContain('default-vision');expect(descendants(root,n=>n.tag==='input'&&n.props.type==='password')).toHaveLength(0);expect(text(root)).not.toContain('添加通道');expect(mock.api.mock.calls).toHaveLength(3);expect(mock.api.mock.calls.every(c=>!c[1]?.method)).toBe(true)
})
it('waits for model state before reporting availability and offers help rather than requiring a key',async()=>{
 const pending=deferred<any>();mock.api.mockReturnValueOnce(pending.promise);mount();await flush();expect(text(root)).toContain('正在确认模型状态');expect(text(root)).not.toContain('部分模型暂不可用');pending.resolve({items:[]});await flush();expect(text(root)).toContain('部分模型暂不可用');expect(text(root)).toContain('联系管理员');expect(descendants(root,n=>n.tag==='input')).toHaveLength(0)
})
it('does not publish model state from the previous account after scope changes',async()=>{
 const pending=deferred<any>();mock.api.mockReturnValueOnce(pending.promise);mount();await flush();mock.valid=false;pending.resolve({items:[{id:'other',isDefault:true,status:'active',modelId:'wrong-account'}]});await flush();expect(text(root)).not.toContain('wrong-account')
})
