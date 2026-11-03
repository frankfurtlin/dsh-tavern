import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import * as vue from 'vue'
import {createRenderer,defineComponent,h,ssrContextKey} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import WorldCreatePage from './WorldCreatePage.vue'
import {worldApi} from './api'
import type {WorldTask} from './types'

const scope=vi.hoisted(()=>({epoch:0,route:{query:{world:'fixture-world'} as Record<string,string>,fullPath:'/worlds/new?world=fixture-world'}}))
vi.mock('vue-router',()=>({useRoute:()=>scope.route,useRouter:()=>({push:vi.fn()})}))
vi.mock('../api',()=>({newKey:()=> 'fixture-only-key',authGeneration:()=>scope.epoch}))
vi.mock('./api',()=>({worldApi:vi.fn()}))
vi.mock('../components/WorldLookLibrary.vue',()=>({default:defineComponent({setup:()=>()=>h('div',{'aria-label':'角色形象库'})})}))
vi.mock('./mutation-intent',()=>({pendingWorldMutation:async()=>undefined,clearWorldMutation:async()=>{},worldMutation:async(_slot:string,path:string,body:unknown)=>worldApi(path,{method:'POST',key:'fixture-only-key',body})}))
vi.mock('../platform/request-scope',async()=>{
  const {onUnmounted}=await import('vue')
  return {useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const epoch=scope.epoch,path=scope.route.fullPath;return {valid:()=>!disposed&&epoch===scope.epoch&&path===scope.route.fullPath}}}}
})
// Vitest's Node transform supplies SSR setup. Compile the actual client template
// for Vue's in-memory renderer so these tests exercise setup and button handlers.
const filename=new URL('./WorldCreatePage.vue',import.meta.url),descriptor=parse(readFileSync(filename,'utf8')).descriptor
const template=compileTemplate({source:descriptor.template!.content,filename:filename.pathname,id:'prepare-unit',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'prepare-unit'}).bindings}})
if(template.errors.length)throw new Error(String(template.errors[0]))
const compiled=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,templateExports:{render?:Function}={}
new Function('require','exports',compiled)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template dependency: '+name)},templateExports)
Object.assign(WorldCreatePage,{render:templateExports.render})
type Node={tag:string;tagName:string;text:string;value:string;children:Node[];parent:Node|null;props:Record<string,any>;options:Node[];multiple:boolean;selectedIndex:number;addEventListener:EventTarget['addEventListener'];removeEventListener:EventTarget['removeEventListener']}
const node=(tag:string,text=''):Node=>{const events=new EventTarget();return {tag,tagName:tag.toUpperCase(),text,value:'',children:[],parent:null,props:{},get options(){return this.children.flatMap(child=>child.tag==='option'?[child]:child.options)},multiple:false,selectedIndex:-1,addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events)}}
const renderer=createRenderer<Node,Node>({
  createElement:tag=>node(tag),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
  setText:(n,text)=>{n.text=text},setElementText:(n,text)=>{n.text=text;n.children=[]},patchProp:(n,key,_old,value)=>{n.props[key]=value},
  insert:(n,parent,anchor)=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=parent;const i=anchor?parent.children.indexOf(anchor):-1;if(i<0)parent.children.push(n);else parent.children.splice(i,0,n)},
  remove:n=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=null},
  parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null
})
const text=(n:Node):string=>(n.tag==='#comment'?'':n.text)+n.children.map(text).join('')
function find(n:Node,predicate:(n:Node)=>boolean):Node|undefined{if(predicate(n))return n;for(const child of n.children){const found=find(child,predicate);if(found)return found}}
const flush=async()=>{for(let i=0;i<45;i++)await Promise.resolve()}
const world=(status:string)=>({id:'fixture-world',name:'合成恢复世界',status,prepareTaskId:'prepare-task',characters:[{id:'reader',name:'林渡',available:true}],description:'合成夹具'})
const task=(state:string,persistenceState:string):WorldTask=>({id:'prepare-task',type:'world_prepare',state,persistenceState,phase:'preparing_world',errorCode:state==='failed'?'PERSISTENCE_UNAVAILABLE':state==='cancelled'?'TASK_CANCELLED':null,result:{worldId:'fixture-world'},eventSeq:1,providerState:'received',stream:{}})
let app:ReturnType<typeof renderer.createApp>,root:Node
function mount(){root=node('root');app=renderer.createApp(WorldCreatePage);app.provide(ssrContextKey,{modules:new Set()});app.component('RouterLink',defineComponent({setup:(_props,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
beforeEach(()=>{vi.useFakeTimers();vi.stubGlobal('document',{activeElement:null});vi.mocked(worldApi).mockReset();scope.epoch=0;scope.route.query={world:'fixture-world'};scope.route.fullPath='/worlds/new?world=fixture-world'})
afterEach(()=>{app?.unmount();vi.useRealTimers();vi.unstubAllGlobals()})

it('renders recoverable prepare as saving recovery, observes the same task and only then opens identity selection',async()=>{
  const api=vi.mocked(worldApi);let reads=0
  api.mockImplementation(async(path)=>{if(path==='/tasks/prepare-task')return task(++reads===1?'failed':'succeeded',reads===1?'recoverable':'saved') as any;return world(reads===0?'preparing':reads===1?'failed':'active') as any})
  mount();await flush()
  expect(text(root)).toContain('正在恢复保存…');expect(find(root,n=>n.tag==='button'&&text(n)==='重新准备')).toBeUndefined();expect(find(root,n=>n.tag==='button'&&text(n)==='停止准备')).toBeDefined()
  await vi.advanceTimersByTimeAsync(1200);await flush();expect(text(root)).toContain('你想成为谁？');expect(reads).toBe(2);expect(api.mock.calls.some(([,o])=>o?.method==='POST')).toBe(false)
})
it('resumes an existing failed recoverable preparation and stops it through the real cancel button handler without retrying',async()=>{
  const api=vi.mocked(worldApi);let cancelled=false
  api.mockImplementation(async(path,options)=>{if(path==='/tasks/prepare-task/cancel'&&options?.method==='POST'){cancelled=true;return {} as any}if(path==='/tasks/prepare-task')return task(cancelled?'cancelled':'failed',cancelled?'saved':'recoverable') as any;return world('failed') as any})
  mount();await flush();expect(text(root)).toContain('正在恢复保存…')
  const stop=find(root,n=>n.tag==='button'&&text(n)==='停止准备');expect(stop).toBeDefined();await stop!.props.onClick();await vi.advanceTimersByTimeAsync(1200);await flush()
  expect(text(root)).toContain('已停止准备');expect(find(root,n=>n.tag==='button'&&text(n)==='重新准备')).toBeDefined();expect(find(root,n=>n.tag==='button'&&text(n)==='停止准备')).toBeUndefined()
  expect(api.mock.calls.filter(([,o])=>o?.method==='POST').map(([p])=>p)).toEqual(['/tasks/prepare-task/cancel'])
})
it.each(['account','navigation'])('does not issue another prepare poll after the %s scope changes',async(kind)=>{
  const api=vi.mocked(worldApi);api.mockImplementation(async(path)=>path==='/tasks/prepare-task'?task('failed','recoverable') as any:world('preparing') as any)
  mount();await flush();expect(text(root)).toContain('正在恢复保存…');const calls=api.mock.calls.length
  if(kind==='account')scope.epoch++;else scope.route.fullPath='/worlds/new?world=another-world'
  await vi.advanceTimersByTimeAsync(5000);await flush();expect(api).toHaveBeenCalledTimes(calls)
})

it('opens an archived world without identity as a read-only entry without preparing or identity controls',async()=>{
  const api=vi.mocked(worldApi);api.mockResolvedValue(world('archived') as any)
  mount();await flush()
  expect(text(root)).toContain('这个世界已归档');expect(text(root)).toContain('恢复后')
  expect(find(root,n=>n.tag==='form'||n.tag==='input'||n.tag==='textarea')).toBeUndefined()
  for(const label of ['重新准备','进入故事','停止准备'])expect(find(root,n=>n.tag==='button'&&text(n).includes(label))).toBeUndefined()
  expect(find(root,n=>n.tag==='a'&&text(n)==='回到世界列表')).toBeDefined()
  await vi.advanceTimersByTimeAsync(5000);await flush()
  expect(api.mock.calls.map(([path])=>path)).toEqual(['/worlds/fixture-world'])
})
it.each([['failed','recoverable'],['succeeded','saved']])('stops preparation observation when the world is archived after a %s/%s task snapshot',async(state,persistenceState)=>{
  const api=vi.mocked(worldApi);let worldReads=0
  api.mockImplementation(async(path)=>path==='/tasks/prepare-task'?task(state,persistenceState) as any:world(++worldReads===1?'preparing':'archived') as any)
  mount();await flush()
  expect(text(root)).toContain('这个世界已归档')
  for(const label of ['重新准备','进入故事','停止准备'])expect(find(root,n=>n.tag==='button'&&text(n).includes(label))).toBeUndefined()
  const calls=api.mock.calls.length;await vi.advanceTimersByTimeAsync(5000);await flush()
  expect(api).toHaveBeenCalledTimes(calls);expect(calls).toBe(3)
  expect(api.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false)
})
it.each(['retry','begin','cancelPreparation'] as const)('rejects a retained %s handler once its current world is archived',async(handler)=>{
  const api=vi.mocked(worldApi)
  api.mockImplementation(async(path,options)=>options?.method==='POST'?{taskId:'prepare-task',lineId:'unused-line'} as any:path==='/tasks/prepare-task'?task('failed','recoverable') as any:world('archived') as any)
  mount();await flush()
  // Exercise the actual SFC setup method, including a retained task receipt;
  // hiding a button alone must not authorize a stale handler to write.
  const setup=(app as any)._instance.setupState
  expect(setup.world.status).toBe('archived');expect(typeof setup[handler]).toBe('function')
  setup.task=task('failed','recoverable')
  await setup[handler]();await flush()
  expect(api.mock.calls.filter(([,options])=>options?.method==='POST')).toEqual([])
  expect(api.mock.calls.map(([path])=>path)).toEqual(['/worlds/fixture-world'])
})

const templates=[
  {id:'original-mystery-v1',title:'雨夜的留声机',genre:'mystery',description:'一卷来历不明的录音，等待你揭开旧日的秘密。'},
  {id:'original-fantasy-v1',title:'山门第一夜',genre:'fantasy',description:'行至山门时天色已晚，桥边的旅人向你借一盏灯。'},
  {id:'original-romance-v1',title:'迟来的明信片',genre:'romance',description:'多年后重返故乡，你遇见一位等候许久的旧友。'},
]
function newStory(){scope.route.query={};scope.route.fullPath='/worlds/new'}
it('offers original worlds in an empty library without posting or polling any task on entry',async()=>{
  newStory();const api=vi.mocked(worldApi)
  api.mockImplementation(async(path)=>({items:path==='/templates'?templates:[]}) as any)
  mount();await flush()
  expect(text(root)).toContain('雨夜的留声机');expect(text(root)).toContain('迟来的明信片')
  expect(api.mock.calls.map(([path])=>path)).toEqual(['/sources','/templates'])
  expect(find(root,n=>n.tag==='select')).toBeUndefined()
  const setup=(app as any)._instance.setupState
  expect(setup.sourceKind).toBe('standalone');expect(setup.rules.genre).toBe('mystery')
  expect(find(root,n=>n.tag==='button'&&text(n).includes('建立世界'))?.props.disabled).toBe(false)
})
it('creates an explicitly selected template without a book anchor or a fabricated preparation task',async()=>{
  newStory();const api=vi.mocked(worldApi)
  api.mockImplementation(async(path,options)=>{
    if(options?.method==='POST')return {worldId:'fixture-world',taskId:null,status:'active'} as any
    if(path==='/worlds/fixture-world')return {...world('active'),sourceKind:'standalone',sourceAnchor:null,prepareTaskId:null} as any
    return {items:path==='/templates'?templates:[]} as any
  })
  mount();await flush()
  await find(root,n=>n.tag==='button'&&text(n).includes('山门第一夜'))!.props.onClick();await flush()
  const setup=(app as any)._instance.setupState;setup.name='我的山门';setup.fullReference=true
  await setup.create();await flush()
  const posts=api.mock.calls.filter(([,o])=>o?.method==='POST');expect(posts).toHaveLength(1)
  expect(posts[0][1]?.body).toMatchObject({templateId:'original-fantasy-v1',name:'我的山门',rules:{genre:'fantasy'}})
  expect(posts[0][1]?.body).not.toHaveProperty('anchor');expect(posts[0][1]?.body).not.toHaveProperty('fullBookReference')
  expect(text(root)).toContain('你想成为谁？');expect(setup.busy).toBe(false)
  expect(api.mock.calls.some(([path])=>path.startsWith('/tasks/'))).toBe(false)
  await vi.advanceTimersByTimeAsync(5000);expect(posts).toHaveLength(1)
})
it('keeps the original novel path and only loads templates after explicit source selection',async()=>{
  newStory();const api=vi.mocked(worldApi),anchor={bookId:'book-a',branchId:'branch-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:0,end:5,quoteHash:'hash'}
  api.mockImplementation(async(path)=>({items:path==='/templates'?templates:[{id:'book-a',title:'原来的小说',lines:[{id:'branch-a',name:'原文',anchor,chapters:[]}]}]}) as any)
  mount();await flush();const setup=(app as any)._instance.setupState
  expect(setup.sourceKind).toBe('book');expect(setup.sourceAnchor).toEqual(anchor);expect(api.mock.calls.map(([path])=>path)).toEqual(['/sources'])
  await find(root,n=>n.tag==='button'&&text(n)==='建立新故事')!.props.onClick();await flush()
  expect(setup.sourceKind).toBe('standalone');expect(api.mock.calls.map(([path])=>path)).toEqual(['/sources','/templates'])
  setup.busy=true;setup.selectSource('book');expect(setup.sourceKind).toBe('standalone')
  setup.busy=false;setup.selectSource('book');expect(setup.sourceAnchor).toEqual(anchor)
})
it('does not replace template choices with a response that arrived after account change',async()=>{
  newStory();let resolve!:(value:any)=>void
  vi.mocked(worldApi).mockImplementation(async(path)=>path==='/sources'?{items:[]} as any:await new Promise(r=>{resolve=r}) as any)
  mount();await flush();scope.epoch++;resolve({items:templates});await flush()
  expect((app as any)._instance.setupState.templates).toEqual([])
  expect(text(root)).not.toContain('雨夜的留声机')
})
it('reopens a known created world after a detail fetch failure without creating or preparing it twice',async()=>{
  newStory();const api=vi.mocked(worldApi);let details=0
  api.mockImplementation(async(path,options)=>{
    if(options?.method==='POST')return {worldId:'fixture-world',taskId:null,status:'active'} as any
    if(path==='/worlds/fixture-world'){if(++details===1)throw new Error('合成详情读取失败');return {...world('active'),prepareTaskId:null} as any}
    return {items:path==='/templates'?templates:[]} as any
  })
  mount();await flush();const setup=(app as any)._instance.setupState
  await setup.create();await flush();expect(text(root)).toContain('打开已创建的世界');expect(setup.creationReceipt.worldId).toBe('fixture-world')
  setup.selectSource('book');expect(setup.sourceKind).toBe('standalone')
  // The main button submits the actual form; drive the compiled submit handler.
  await find(root,n=>n.tag==='form')!.props.onSubmit({preventDefault(){}});await flush()
  expect(text(root)).toContain('你想成为谁？');expect(details).toBe(2)
  expect(api.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(1)
  expect(api.mock.calls.some(([path])=>path.startsWith('/tasks/'))).toBe(false)
})
