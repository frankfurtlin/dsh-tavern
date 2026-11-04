import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted,reactive} from 'vue'
import RegexPage from './RegexPage.vue'
import * as cardApi from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

vi.mock('vue-router',()=>({RouterLink:defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())})}))
vi.mock('./api',()=>({listRegexScripts:vi.fn(),createRegexScript:vi.fn(),updateRegexScript:vi.fn(),deleteRegexScript:vi.fn()}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../workspace/PageHeader.vue',()=>({default:defineComponent({props:{title:String,description:String,eyebrow:String},setup:(p,{slots})=>()=>h('header',[p.title,slots.default?.()])})}))
vi.mock('lucide-vue-next',()=>{
  const stub=defineComponent({setup:()=>()=>null})
  return{ArrowDown:stub,ArrowLeft:stub,ArrowUp:stub,Pencil:stub,Plus:stub,Replace:stub,Trash2:stub}
})
compileRender(RegexPage,new URL('./RegexPage.vue',import.meta.url))

const rules=[
  {id:'r1',name:'句号改感叹号',pattern:'。$',replacement:'！',enabled:true,order:10},
  {id:'r2',name:'删除括号注',pattern:'（.*?）',replacement:'',enabled:false,order:20},
]
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host

function fire(target:Host,key:string,payload?:unknown){
  const handler=target.props[key]
  if(Array.isArray(handler)){for(const fn of handler)fn(payload)}
  else handler?.(payload)
}
function button(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0]}
function mount(){root=node('root');app=renderer.createApp(RegexPage);provideSfc(app);app.mount(root)}

beforeEach(()=>{
  // Vue's native v-model directive reads document.activeElement on updates.
  vi.stubGlobal('document',{activeElement:null})
  vi.mocked(cardApi.listRegexScripts).mockReset().mockResolvedValue(rules.map(rule=>({...rule})))
  vi.mocked(cardApi.createRegexScript).mockReset()
  vi.mocked(cardApi.updateRegexScript).mockReset()
  vi.mocked(cardApi.deleteRegexScript).mockReset().mockResolvedValue(undefined)
})
afterEach(()=>{app?.unmount();app=undefined;vi.restoreAllMocks();vi.unstubAllGlobals()})

it('lists the rules in order with enable state and pattern preview',async()=>{
  mount();await flush()
  expect(text(root)).toContain('输出替换规则')
  expect(text(root)).toContain('句号改感叹号')
  expect(text(root)).toContain('。$')
  expect(text(root)).toContain('！')
  expect(text(root)).toContain('删除括号注')
  const names=descendants(root,item=>item.tag==='strong').map(item=>text(item))
  expect(names[0]).toContain('句号改感叹号')
})

it('shows an empty state before the first rule exists',async()=>{
  vi.mocked(cardApi.listRegexScripts).mockResolvedValueOnce([])
  mount();await flush()
  expect(text(root)).toContain('还没有替换规则')
  expect(text(root)).toContain('新增第一条规则')
})

it('creates a replacement rule from the inline form',async()=>{
  const created={id:'r3',name:'省略号收敛',pattern:'…+',replacement:'……',enabled:true,order:30}
  vi.mocked(cardApi.listRegexScripts).mockResolvedValueOnce(rules.map(rule=>({...rule}))).mockResolvedValueOnce([...rules,created])
  vi.mocked(cardApi.createRegexScript).mockResolvedValueOnce(created)
  mount();await flush()
  button('新增规则').props.onClick()
  await flush()
  const form=descendants(root,item=>item.tag==='form')[0]
  const inputs=descendants(form,item=>item.tag==='input')
  fire(inputs[0],'onUpdate:modelValue','省略号收敛')
  fire(inputs[1],'onUpdate:modelValue','…+')
  fire(inputs[2],'onUpdate:modelValue','……')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.createRegexScript).toHaveBeenCalledWith({name:'省略号收敛',pattern:'…+',replacement:'……',enabled:true,order:30})
  expect(text(root)).toContain('省略号收敛')
  expect(text(root)).toContain('替换规则已保存')
})

it('rejects an invalid regular expression before calling the server',async()=>{
  mount();await flush()
  button('新增规则').props.onClick()
  await flush()
  const form=descendants(root,item=>item.tag==='form')[0]
  const inputs=descendants(form,item=>item.tag==='input')
  fire(inputs[0],'onUpdate:modelValue','坏正则')
  fire(inputs[1],'onUpdate:modelValue','(')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(text(root)).toContain('正则表达式无效')
  expect(cardApi.createRegexScript).not.toHaveBeenCalled()
})

it('surfaces the server REGEX_INVALID message when it still rejects',async()=>{
  vi.mocked(cardApi.createRegexScript).mockRejectedValueOnce(new Error('正则表达式无效：未闭合的分组'))
  mount();await flush()
  button('新增规则').props.onClick()
  await flush()
  const form=descendants(root,item=>item.tag==='form')[0]
  const inputs=descendants(form,item=>item.tag==='input')
  fire(inputs[0],'onUpdate:modelValue','坏正则')
  fire(inputs[1],'onUpdate:modelValue','。$')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(text(root)).toContain('正则表达式无效：未闭合的分组')
})

it('toggles a rule without losing the list',async()=>{
  vi.mocked(cardApi.updateRegexScript).mockResolvedValueOnce({...rules[0],enabled:false})
  mount();await flush()
  button('停用').props.onClick()
  await flush()
  expect(cardApi.updateRegexScript).toHaveBeenCalledWith('r1',{enabled:false})
  expect(text(root)).toContain('句号改感叹号')
})

it('deletes a rule after an explicit confirmation',async()=>{
  mount();await flush()
  fire(descendants(root,item=>item.props['aria-label']==='删除规则')[0],'onClick')
  await flush()
  button('确认删除').props.onClick()
  await flush()
  expect(cardApi.deleteRegexScript).toHaveBeenCalledWith('r1')
  expect(text(root)).not.toContain('句号改感叹号')
  expect(text(root)).toContain('替换规则已删除')
})

it('moves a rule up or down by rewriting the two affected orders',async()=>{
  vi.mocked(cardApi.updateRegexScript).mockResolvedValue({} as any)
  mount();await flush()
  fire(descendants(root,item=>item.props['aria-label']==='下移规则')[0],'onClick')
  await flush()
  expect(cardApi.updateRegexScript).toHaveBeenNthCalledWith(1,'r1',{order:20})
  expect(cardApi.updateRegexScript).toHaveBeenNthCalledWith(2,'r2',{order:10})
  expect(cardApi.listRegexScripts).toHaveBeenCalledTimes(2)
})
