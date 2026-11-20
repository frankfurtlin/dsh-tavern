import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import PersonaSettings from './PersonaSettings.vue'
import * as cardApi from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

vi.mock('./api',()=>({listPersonas:vi.fn(),createPersona:vi.fn(),updatePersona:vi.fn(),deletePersona:vi.fn()}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{Pencil:stub,Plus:stub,Trash2:stub}})
compileRender(PersonaSettings,new URL('./PersonaSettings.vue',import.meta.url))

const persona=(id:string,name:string,description='')=>({id,name,description})
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
let storage:Map<string,string>

function mount(){
  const host=node('root')
  app=renderer.createApp(PersonaSettings)
  provideSfc(app);app.mount(host);root=host
}
function fire(target:Host,key:string,payload?:unknown){const handler=target.props[key];Array.isArray(handler)?handler.forEach(fn=>fn(payload)):handler?.(payload)}
function button(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0]}
function select(){return descendants(root,item=>item.tag==='select')[0]}
function forms(){return descendants(root,item=>item.tag==='form')}

beforeEach(()=>{
  storage=new Map()
  vi.stubGlobal('document',{activeElement:null})
  vi.stubGlobal('window',{localStorage:{
    getItem:(key:string)=>storage.get(key)??null,
    setItem:(key:string,value:string)=>{storage.set(key,String(value))},
    removeItem:(key:string)=>{storage.delete(key)},
  }})
  vi.mocked(cardApi.listPersonas).mockReset().mockResolvedValue([persona('p1','旅人','一位雨夜赶路的旅人。'),persona('p2','账房')] as never)
  vi.mocked(cardApi.createPersona).mockReset().mockResolvedValue(persona('p3','新人设') as never)
  vi.mocked(cardApi.updatePersona).mockReset().mockResolvedValue(persona('p1','旅人改') as never)
  vi.mocked(cardApi.deletePersona).mockReset().mockResolvedValue(undefined as never)
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

it('lists personas and shows the one saved on this device',async()=>{
  storage.set('tavern:persona','p1')
  mount()
  await flush()
  expect(text(root)).toContain('旅人')
  expect(text(root)).toContain('账房')
  expect(text(root)).toContain('一位雨夜赶路的旅人。')
  expect(select().props.value).toBe('p1')
})

it('persists the chosen persona and explains when it takes effect',async()=>{
  mount()
  await flush()
  fire(select(),'onChange',{target:{value:'p2'}})
  await flush()
  expect(storage.get('tavern:persona')).toBe('p2')
  expect(text(root)).toContain('已切换为「账房」，下一轮消息生效。')
})

it('clears the stored persona when “不使用人设” is chosen',async()=>{
  storage.set('tavern:persona','p1')
  mount()
  await flush()
  fire(select(),'onChange',{target:{value:''}})
  await flush()
  expect(storage.has('tavern:persona')).toBe(false)
  expect(text(root)).toContain('按默认的「用户」称呼你')
})

it('creates a persona inline and uses it right away',async()=>{
  mount()
  await flush()
  fire(button('新建'),'onClick',new Event('click'))
  await flush()
  const form=forms()[0]!
  const inputs=descendants(form,item=>item.tag==='input')
  const areas=descendants(form,item=>item.tag==='textarea')
  fire(inputs[0]!,'onUpdate:modelValue','新旅伴')
  fire(areas[0]!,'onUpdate:modelValue','总在雨里遇到的人。')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.createPersona).toHaveBeenCalledWith({name:'新旅伴',description:'总在雨里遇到的人。'})
  expect(storage.get('tavern:persona')).toBe('p3')
  expect(text(root)).toContain('人设已创建，并作为你的身份。')
})

it('asks for confirmation before deleting the selected persona',async()=>{
  storage.set('tavern:persona','p1')
  mount()
  await flush()
  fire(button('删除'),'onClick',new Event('click'))
  await flush()
  expect(cardApi.deletePersona).not.toHaveBeenCalled()
  fire(button('确认删除'),'onClick',new Event('click'))
  await flush()
  expect(cardApi.deletePersona).toHaveBeenCalledWith('p1')
  expect(storage.has('tavern:persona')).toBe(false)
  expect(text(root)).toContain('人设已删除。')
})

it('forgets a stored persona that no longer exists',async()=>{
  storage.set('tavern:persona','gone')
  mount()
  await flush()
  expect(storage.has('tavern:persona')).toBe(false)
  expect(select().props.value).toBe('')
})

it('shows an empty state without inventing a second selector',async()=>{
  vi.mocked(cardApi.listPersonas).mockResolvedValue([] as never)
  mount()
  await flush()
  expect(descendants(root,item=>item.tag==='select')).toHaveLength(1)
  expect(text(root)).toContain('还没有人设。新建一个，对方就会知道怎么称呼你。')
  expect(text(root)).not.toContain('默认人设')
  expect(text(root)).not.toContain('当前人设')
})
