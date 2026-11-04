import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted,ref} from 'vue'
import PersonaControl from './PersonaControl.vue'
import * as cardApi from './api'
import {api} from '../api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({updated:vi.fn()}))
vi.mock('./api',()=>({listPersonas:vi.fn(),createPersona:vi.fn(),updatePersona:vi.fn(),deletePersona:vi.fn()}))
vi.mock('../api',()=>({api:vi.fn(),newKey:()=> 'persona-key'}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('lucide-vue-next',()=>{const stub=defineComponent({setup:()=>()=>null});return{Pencil:stub,Plus:stub,Trash2:stub,UserRound:stub}})
compileRender(PersonaControl,new URL('./PersonaControl.vue',import.meta.url))

const persona=(id:string,name:string,description='')=>({id,name,description})
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,model:ReturnType<typeof ref<string|null>>

function mount(value='p1'){
  model=ref<string|null>(value)
  const host=node('root')
  app=renderer.createApp(defineComponent({setup:()=>()=>h(PersonaControl,{modelValue:model.value,sessionId:'chat-1',version:3,'onUpdate:modelValue':(next:string|null)=>{model.value=next},onUpdated:harness.updated})}))
  provideSfc(app);app.mount(host);root=host
}
function fire(target:Host,key:string,payload?:unknown){const handler=target.props[key];Array.isArray(handler)?handler.forEach(fn=>fn(payload)):handler?.(payload)}
function button(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0]}
function forms(){return descendants(root,item=>item.tag==='form')}

beforeEach(()=>{
  vi.mocked(cardApi.listPersonas).mockReset().mockResolvedValue([persona('p1','旅人','一位雨夜赶路的旅人。'),persona('p2','账房') ] as never)
  vi.mocked(cardApi.createPersona).mockReset().mockResolvedValue(persona('p3','新人设') as never)
  vi.mocked(cardApi.updatePersona).mockReset().mockResolvedValue(persona('p1','旅人改') as never)
  vi.mocked(cardApi.deletePersona).mockReset().mockResolvedValue(undefined as never)
  vi.mocked(api).mockReset().mockResolvedValue({} as never)
  vi.stubGlobal('document',{activeElement:null})
  harness.updated.mockReset()
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

it('lists personas and marks the one bound to this chat',async()=>{
  mount('p1')
  await flush()
  expect(text(root)).toContain('我是谁')
  expect(text(root)).toContain('旅人')
  expect(text(root)).toContain('账房')
  const select=descendants(root,item=>item.tag==='select')[0]
  expect(select.props.value??select.props['onUpdate:modelValue']).toBeTruthy()
  expect(text(root)).toContain('一位雨夜赶路的旅人。')
})

it('binds the chosen persona to the chat session with the expected version',async()=>{
  mount('p1')
  await flush()
  const select=descendants(root,item=>item.tag==='select')[0]
  fire(select,'onChange',{target:{value:'p2'}})
  await flush()
  expect(api).toHaveBeenCalledWith('/play-controls/world-chats/chat-1',{method:'PATCH',key:'persona-key',body:{personaId:'p2',expectedVersion:3}})
  expect(model.value).toBe('p2')
  expect(harness.updated).toHaveBeenCalledTimes(1)
})

it('creates a persona inline and selects it for the current chat',async()=>{
  mount('p1')
  await flush()
  fire(button('新建'),'onClick',new Event('click'))
  await flush()
  const form=forms()[0]
  expect(form).toBeTruthy()
  const inputs=descendants(form,item=>item.tag==='input')
  const areas=descendants(form,item=>item.tag==='textarea')
  fire(inputs[0],'onUpdate:modelValue','新旅伴')
  fire(areas[0],'onUpdate:modelValue','总在雨里遇到的人。')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.createPersona).toHaveBeenCalledWith({name:'新旅伴',description:'总在雨里遇到的人。'})
  expect(api).toHaveBeenCalledWith('/play-controls/world-chats/chat-1',{method:'PATCH',key:'persona-key',body:{personaId:'p3',expectedVersion:3}})
  expect(model.value).toBe('p3')
})

it('asks for confirmation before deleting the selected persona',async()=>{
  mount('p1')
  await flush()
  fire(button('删除'),'onClick',new Event('click'))
  await flush()
  expect(cardApi.deletePersona).not.toHaveBeenCalled()
  fire(button('确认删除'),'onClick',new Event('click'))
  await flush()
  expect(cardApi.deletePersona).toHaveBeenCalledWith('p1')
  expect(model.value).toBeNull()
  expect(text(root)).toContain('人设已删除。')
})
