import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import * as vue from 'vue'
import {createRenderer,defineComponent,h,reactive,ssrContextKey} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import WorldLinesPage from './WorldLinesPage.vue'
import {worldApi} from './api'
import {ApiError,api} from '../api'
import type {WorldCheckpoint} from './types'

const fixture=vi.hoisted(()=>({route:null as any,epoch:0,push:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>fixture.route,useRouter:()=>({push:fixture.push,replace:vi.fn()})}))
vi.mock('../api',()=>({api:vi.fn(),newKey:()=> 'unused-history-key',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('./api',()=>({worldApi:vi.fn()}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>null),cacheUpdate:vi.fn(async(_key:string,update:(value:unknown)=>unknown,valid:()=>boolean)=>valid()?update(null):undefined)}))
// The comparison child has its own actual-SFC and HTTP suite. Keep these
// history/management tests bound to the page's existing handlers and fake host.
vi.mock('../components/ComparisonLauncher.vue',()=>({default:{render:()=>null}}))
vi.mock('../components/Sheet.vue',async()=>{
  const {defineComponent,h}=await import('vue')
  return {default:defineComponent({props:{title:String},emits:['close'],setup:(props,{emit,slots})=>()=>h('section',{role:'dialog','aria-label':props.title},[h('h2',props.title),h('button',{'aria-label':'关闭',onClick:()=>emit('close')},'关闭'),slots.default?.()])})}
})
vi.mock('../platform/request-scope',async()=>{
  const {onUnmounted}=await import('vue')
  return {useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const epoch=fixture.epoch,path=fixture.route.fullPath;return {valid:()=>!disposed&&epoch===fixture.epoch&&path===fixture.route.fullPath}}}}
})
// Exercise the real graph layout, page setup/template, and fork button handler.
// The Sheet frame is isolated while its close emit uses the real page binding.
const filename=new URL('./WorldLinesPage.vue',import.meta.url),descriptor=parse(readFileSync(filename,'utf8')).descriptor
const template=compileTemplate({source:descriptor.template!.content,filename:filename.pathname,id:'history-lines-unit',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'history-lines-unit'}).bindings}})
if(template.errors.length)throw new Error(String(template.errors[0]))
const compiled=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,templateExports:{render?:Function}={}
new Function('require','exports',compiled)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template dependency: '+name)},templateExports)
Object.assign(WorldLinesPage,{render:templateExports.render})
type HostNode={tag:string;tagName:string;text:string;value:any;children:HostNode[];parent:HostNode|null;props:Record<string,any>;addEventListener:EventTarget['addEventListener'];removeEventListener:EventTarget['removeEventListener'];clientHeight:number;clientWidth:number}
function node(tag:string,text=''):HostNode{const events=new EventTarget(),host={tag,tagName:tag.toUpperCase(),text,value:'',children:[],parent:null,props:{},addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events),clientHeight:500,clientWidth:600} as HostNode;Object.defineProperty(host,'options',{get:()=>host.children.filter(n=>n.tag==='option')});return host}
const renderer=createRenderer<HostNode,HostNode>({
  createElement:tag=>node(tag),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
  setText:(n,text)=>{n.text=text},setElementText:(n,text)=>{n.text=text;n.children=[]},patchProp:(n,key,_old,value)=>{n.props[key]=value;if(key==='value')n.value=value},
  insert:(n,parent,anchor)=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=parent;const i=anchor?parent.children.indexOf(anchor):-1;if(i<0)parent.children.push(n);else parent.children.splice(i,0,n)},
  remove:n=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=null},
  parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null
})
const text=(n:HostNode):string=>(n.tag==='#comment'?'':n.text)+n.children.map(text).join('')
function find(n:HostNode,predicate:(n:HostNode)=>boolean):HostNode|undefined{if(predicate(n))return n;for(const child of n.children){const found=find(child,predicate);if(found)return found}}
const flush=async()=>{for(let i=0;i<24;i++)await Promise.resolve()}
const deferred=<T>()=>{let resolve!:(value:T)=>void,reject!:(error:Error)=>void;const promise=new Promise<T>((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const cp=(id:string):WorldCheckpoint=>({id,worldId:'world-a',lineId:'line-a',parentCheckpointId:id==='b'?'a':null,sequence:id==='a'?1:2,title:'历史'+id,text:'历史正文'+id,action:'',options:[],changes:[],summary:'',kind:'action',createdAt:'fixture'})
const line=()=>({id:'line-a',name:'合成世界线',headCheckpointId:'b',status:'active',version:1,camera:{x:0,y:0,zoom:1}})
let app:ReturnType<typeof renderer.createApp>,root:HostNode,pending:ReturnType<typeof deferred<WorldCheckpoint>>[]
function mount(){root=node('root');app=renderer.createApp(WorldLinesPage);app.provide(ssrContextKey,{modules:new Set()});app.component('RouterLink',defineComponent({setup:(_props,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
const dialog=()=>find(root,n=>n.props.role==='dialog')
function clickHistory(id:string){const button=find(root,n=>n.props['aria-label']==='历史'+id+'，查看历史节点');expect(button).toBeDefined();return button!.props.onClick()}
beforeEach(()=>{
  vi.useFakeTimers();vi.stubGlobal('document',{activeElement:null});vi.stubGlobal('requestAnimationFrame',vi.fn());fixture.epoch=0;fixture.push.mockReset()
  fixture.route=reactive({params:{worldId:'world-a'},query:{line:'line-a'},fullPath:'/worlds/world-a/lines?line=line-a'})
  vi.mocked(api).mockReset().mockResolvedValue({})
  pending=[];vi.mocked(worldApi).mockReset().mockImplementation((path,options)=>{
    if(options?.method==='POST')return Promise.resolve({...line(),id:'forked-line'}) as any
    if(path==='/worlds/world-a/graph')return Promise.resolve({lines:[line()],nodes:[cp('a'),cp('b')]}) as any
    if(path==='/worlds/world-a')return Promise.resolve({id:'world-a',status:'active',activeLineId:'line-a',name:'合成历史世界'}) as any
    const read=deferred<WorldCheckpoint>();pending.push(read);return read.promise as any
  })
})
afterEach(()=>{app?.unmount();vi.useRealTimers();vi.unstubAllGlobals()})

it('keeps only the server-validated story origin in graph reading, play, and fork links',async()=>{
  const origin='book-'+'a'.repeat(32);fixture.route.query.story=origin
  mount();await flush();const setup=(app as any)._instance.setupState
  expect(api).toHaveBeenCalledWith('/stories/'+origin+'/context/validate',{method:'POST',body:{storyId:origin,mode:'read',perspective:'reader',entryOrigin:'worlds',worldId:'world-a',worldLineId:'line-a',checkpointId:'b'}})
  expect(setup.backTo).toEqual({path:'/worlds/world-a/play',query:{line:'line-a',story:origin}})
  void clickHistory('b');pending[0].resolve(cp('b'));await flush()
  expect(setup.viewLink('read','line-a','b')).toEqual({path:'/worlds/world-a/read',query:{line:'line-a',checkpoint:'b',story:origin}})
  await find(dialog()!,n=>n.tag==='button'&&text(n)==='从这里继续')!.props.onClick();await flush()
  expect(fixture.push).toHaveBeenCalledWith({path:'/worlds/world-a/play',query:{line:'forked-line',story:origin}})
})
it('drops a foreign story origin rejected by the owned context validator',async()=>{
  fixture.route.query.story='book-'+'b'.repeat(32);vi.mocked(api).mockRejectedValueOnce(new Error('unavailable'))
  mount();await flush();expect((app as any)._instance.setupState.backTo).toBe('/worlds/world-a/play?line=line-a')
})
it('does not apply a late origin validation after the account changes',async()=>{
  const pendingOrigin=deferred<any>();fixture.route.query.story='book-'+'a'.repeat(32);vi.mocked(api).mockReturnValueOnce(pendingOrigin.promise)
  mount();await flush();fixture.epoch++;pendingOrigin.resolve({});await flush()
  expect((app as any)._instance.setupState.returnStory).toBe('')
})
it('keeps the selected history while a delayed successful origin validation finishes',async()=>{
  const origin='book-'+'a'.repeat(32),pendingOrigin=deferred<any>()
  fixture.route.query.story=origin;vi.mocked(api).mockReturnValueOnce(pendingOrigin.promise)
  mount();await flush();void clickHistory('b');pending[0].resolve(cp('b'));await flush()
  expect(dialog()?.props['aria-label']).toBe('历史b')
  const setup=(app as any)._instance.setupState;setup.pan(60,30)
  const camera={...setup.camera}
  pendingOrigin.resolve({});await flush()
  expect(dialog()?.props['aria-label']).toBe('历史b')
  expect(setup.camera).toEqual(camera)
  expect((app as any)._instance.setupState.viewLink('read','line-a','b')).toEqual({path:'/worlds/world-a/read',query:{line:'line-a',checkpoint:'b',story:origin}})
})

it('forks from the later selected checkpoint after an older history success arrives last',async()=>{
  mount();await flush();void clickHistory('a');void clickHistory('b');expect(pending).toHaveLength(2)
  pending[1].resolve(cp('b'));await flush();expect(dialog()?.props['aria-label']).toBe('历史b')
  pending[0].resolve(cp('a'));await flush()
  const fork=find(dialog()!,n=>n.tag==='button'&&text(n)==='从这里继续');expect(fork).toBeDefined();await fork!.props.onClick();await flush()
  const posts=vi.mocked(worldApi).mock.calls.filter(([,options])=>options?.method==='POST')
  expect(posts).toHaveLength(1);expect(posts[0]![0]).toBe('/worlds/world-a/fork');expect(posts[0]![1]?.body).toMatchObject({checkpointId:'b',mode:'continue'})
  expect(dialog()?.props['aria-label']).toBe('历史b');expect(fixture.push).toHaveBeenCalledWith('/worlds/world-a/play?line=forked-line')
})
it('ignores an older checkpoint error once the later checkpoint is visible',async()=>{
  mount();await flush();void clickHistory('a');void clickHistory('b');pending[1].resolve(cp('b'));await flush();pending[0].reject(new Error('stale-node-error'));await flush()
  expect(dialog()?.props['aria-label']).toBe('历史b');expect(text(root)).not.toContain('stale-node-error');expect(find(root,n=>n.props.role==='alert')).toBeUndefined()
})
it.each(['success','error'])('does not reopen or show an old error after closing a history panel with pending %s',async(outcome)=>{
  mount();await flush();void clickHistory('b');pending[0].resolve(cp('b'));await flush();void clickHistory('a')
  find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await flush();expect(dialog()).toBeUndefined()
  if(outcome==='success')pending[1].resolve(cp('a'));else pending[1].reject(new Error('closed-node-error'));await flush()
  expect(dialog()).toBeUndefined();expect((app as any)._instance.setupState.selected).toBeNull();expect(text(root)).not.toContain('closed-node-error')
})
it('keeps line management visible and cancels a pending history selection when management opens',async()=>{
  mount();await flush();void clickHistory('a');const manage=find(root,n=>n.tag==='button'&&text(n)==='合成世界线');expect(manage).toBeDefined();manage!.props.onClick();await flush();expect(dialog()?.props['aria-label']).toBe('管理世界线')
  pending[0].resolve(cp('a'));await flush();expect((app as any)._instance.setupState.selected).toBeNull();expect(dialog()?.props['aria-label']).toBe('管理世界线')
})
it.each([['account','success'],['account','error'],['navigation','success'],['navigation','error']])('ignores checkpoint reads after %s changes with a late %s',async(kind,outcome)=>{
  mount();await flush();void clickHistory('a')
  if(kind==='account')fixture.epoch++;else fixture.route.fullPath='/worlds/world-b/lines?line=line-b'
  if(outcome==='success')pending[0].resolve(cp('a'));else pending[0].reject(new Error('old-scope-node-error'));await flush()
  expect(dialog()).toBeUndefined();expect((app as any)._instance.setupState.selected).toBeNull();expect(text(root)).not.toContain('old-scope-node-error')
})

function lineManagement(){
  const other={...line(),id:'line-b',name:'另一条世界线',version:4}
  let server={...line(),name:'另一设备名称',status:'archived',version:2},graphReads=0
  vi.mocked(worldApi).mockImplementation((path,options)=>{
    if(path==='/worlds/world-a')return Promise.resolve({id:'world-a',status:'active',activeLineId:'line-a',name:'合成历史世界'}) as any
    if(path==='/worlds/world-a/graph')return Promise.resolve({lines:[graphReads++?{...server}:line(),other],nodes:[cp('a'),cp('b')]}) as any
    if(path==='/worlds/world-a/lines/line-a'){
      if(!options?.method)return Promise.resolve({...server}) as any
      const body=options.body as {expectedVersion:number;name?:string;status?:string}
      if(body?.expectedVersion!==server.version)return Promise.reject(new ApiError('VERSION_CONFLICT','世界线已更新，请刷新后重试',409))
      server={...server,...body,version:server.version+1};return Promise.resolve({...server}) as any
    }
    throw new Error('Unexpected management request: '+path)
  })
  return {other,server:()=>server}
}
function managementButton(label:string){const button=find(dialog()!,n=>n.tag==='button'&&text(n)===label);expect(button).toBeDefined();return button!}
function openLine(label='合成世界线'){const button=find(root,n=>n.tag==='button'&&text(n)===label);expect(button).toBeDefined();button!.props.onClick()}
function draft(value:string){const input=find(dialog()!,n=>n.tag==='input')!;input.props['onUpdate:modelValue'](value)}
const writes=()=>vi.mocked(worldApi).mock.calls.filter(([,options])=>['PATCH','DELETE'].includes(options?.method||''))
async function conflict(){openLine();await flush();draft('我的名称草稿');await flush();await managementButton('保存名称').props.onClick();await flush()}

it('retains a name draft through a natural version conflict, explicitly reads current status and saves with the refreshed version',async()=>{
  const server=lineManagement();mount();await flush();await conflict()
  const alert=find(dialog()!,n=>n.props.role==='alert');expect(alert).toBeDefined();expect(text(alert!)).toContain('世界线已更新')
  expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('我的名称草稿')
  for(const label of ['保存名称','归档','确认删除'])expect(managementButton(label).props.disabled).toBe(true)
  const before=writes().length;await managementButton('确认删除').props.onClick();expect(writes()).toHaveLength(before)
  await managementButton('载入最新信息').props.onClick();await flush()
  expect(writes()).toHaveLength(1);expect(server.server().name).toBe('另一设备名称')
  expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('我的名称草稿')
  expect(text(dialog()!)).toContain('最新名称：另一设备名称');expect(managementButton('恢复').props.disabled).toBe(false)
  await managementButton('保存名称').props.onClick();await flush()
  expect(writes()).toHaveLength(2);expect(writes()[1]![1]?.body).toMatchObject({expectedVersion:2,name:'我的名称草稿'})
  expect(server.server()).toMatchObject({name:'我的名称草稿',status:'archived',version:3});expect(dialog()).toBeUndefined()
})

it.each(['rename','archive','delete'].flatMap(action=>['success','error'].map(outcome=>[action,outcome])))('isolates late %s %s from another line management draft',async(action,outcome)=>{
  lineManagement();mount();await flush();openLine();await flush();draft('A草稿');await flush()
  const old=deferred<any>(),base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>options?.method?old.promise:base(path,options))
  void managementButton(action==='rename'?'保存名称':action==='archive'?'归档':'确认删除').props.onClick()
  find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();openLine('另一条世界线');await flush();draft('B草稿');await flush()
  if(outcome==='success')old.resolve({...line(),name:'A已保存'});else old.reject(new Error('A迟到错误'));await flush()
  expect(dialog()?.props['aria-label']).toBe('管理世界线');expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('B草稿')
  expect(text(root)).not.toContain('A迟到错误');expect(managementButton('保存名称').props.disabled).toBe(false)
})

it('does not release a newer line save busy state when an older save completes',async()=>{
  lineManagement();mount();await flush();openLine();await flush()
  const old=deferred<any>(),current=deferred<any>(),base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>options?.method?(path.endsWith('/line-a')?old.promise:current.promise):base(path,options))
  void managementButton('保存名称').props.onClick();find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();openLine('另一条世界线');await flush();draft('B草稿');await flush();void managementButton('保存名称').props.onClick()
  old.resolve(line());await flush();expect(dialog()).toBeDefined();expect(managementButton('保存名称').props.disabled).toBe(true)
  current.resolve({...line(),id:'line-b',name:'B草稿'});await flush();expect(dialog()).toBeUndefined()
})

it.each(['success','error'])('discards a late refresh %s after selecting another line',async(outcome)=>{
  lineManagement();mount();await flush();await conflict()
  const old=deferred<any>(),base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>path==='/worlds/world-a/lines/line-a'&&!options?.method?old.promise:base(path,options))
  void managementButton('载入最新信息').props.onClick();find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();openLine('另一条世界线');await flush();draft('B草稿');await flush()
  if(outcome==='success')old.resolve({...line(),name:'迟到名称',version:99,status:'archived'});else old.reject(new Error('迟到刷新错误'));await flush()
  expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('B草稿');expect(text(root)).not.toContain('迟到刷新错误');expect(text(root)).not.toContain('迟到名称')
  expect(managementButton('归档').props.disabled).toBe(false)
})

it.each(['account','navigation'].flatMap(scope=>['success','error'].map(outcome=>[scope,outcome])))('discards management response after %s changes with late %s',async(kind,outcome)=>{
  lineManagement();mount();await flush();openLine();await flush();const old=deferred<any>(),base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>options?.method?old.promise:base(path,options));void managementButton('保存名称').props.onClick()
  if(kind==='account')fixture.epoch++;else fixture.route.fullPath='/worlds/world-b/lines?line=line-b'
  if(outcome==='success')old.resolve(line());else old.reject(new Error('旧会话管理错误'));await flush()
  expect(text(root)).not.toContain('旧会话管理错误');expect(writes()).toHaveLength(1)
})

it.each(['success','error'])('closing management releases its busy state and ignores late %s',async(outcome)=>{
  lineManagement();mount();await flush();openLine();await flush();const old=deferred<any>(),base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>options?.method?old.promise:base(path,options));void managementButton('保存名称').props.onClick()
  find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await flush();expect((app as any)._instance.setupState.busy).toBe(false)
  if(outcome==='success')old.resolve(line());else old.reject(new Error('关闭后管理错误'));await flush()
  expect(dialog()).toBeUndefined();expect(text(root)).not.toContain('关闭后管理错误')
})

function cameraManagement(){
  let server={...line()},cameraResponse=deferred<any>()
  vi.mocked(worldApi).mockImplementation((path,options)=>{
    if(path==='/worlds/world-a')return Promise.resolve({id:'world-a',status:'active',activeLineId:'line-a',name:'合成历史世界'}) as any
    if(path==='/worlds/world-a/graph')return Promise.resolve({lines:[{...server}],nodes:[cp('a'),cp('b')]}) as any
    if(path==='/worlds/world-a/lines/line-a'&&options?.method==='PATCH'){
      const body=options.body as {expectedVersion:number;camera?:any;name?:string}
      if(body.expectedVersion!==server.version)return Promise.reject(new ApiError('VERSION_CONFLICT','世界线已更新，请刷新后重试',409))
      server={...server,...body,version:server.version+1}
      return (body.camera?cameraResponse.promise:Promise.resolve({...server})) as any
    }
    throw new Error('Unexpected camera request: '+path)
  })
  return {server:()=>server,response:()=>cameraResponse,respond:()=>cameraResponse.resolve({...server}),next:()=>{cameraResponse=deferred<any>()}}
}
const setup=()=> (app as any)._instance.setupState
async function startCamera(){setup().zoom(1.1);vi.advanceTimersByTime(700);await flush()}

it('opens a dense overview locally, reads the chosen checkpoint and ignores its response after closing history',async()=>{
  mount();await flush();setup().camera.zoom=.12;await flush()
  const target=setup().targets.find((target:any)=>target.nodes.length>1)
  expect(target).toBeDefined();const before=vi.mocked(worldApi).mock.calls.length
  setup().openTarget(target);await flush();expect(dialog()?.props['aria-label']).toBe('选择历史节点')
  expect(vi.mocked(worldApi).mock.calls).toHaveLength(before)
  const choice=find(dialog()!,n=>n.tag==='button'&&n.props['aria-label']==='历史a，查看历史节点')!
  choice.props.onClick();await flush();expect(setup().overviewSelection).toBeNull();expect(pending).toHaveLength(1)
  setup().closeHistory();pending[0].resolve(cp('a'));await flush();expect(dialog()).toBeUndefined()
  expect(writes()).toHaveLength(0)
})

it('expands a packed history from the overview without reading a checkpoint or generating a turn',async()=>{
  mount();await flush();const before=vi.mocked(worldApi).mock.calls.length
  const group={...setup().nodes[0],id:'group:line-a:a',kind:'group',members:['a','b']}
  setup().overviewSelection={id:'overview:0:0',nodes:[group],overview:true};setup().chooseNode(group);await flush()
  expect(setup().expanded.has(group.id)).toBe(true);expect(dialog()).toBeUndefined()
  expect(vi.mocked(worldApi).mock.calls).toHaveLength(before);expect(writes()).toHaveLength(0)
})

it('cancels a pending history read when a dense picker or line management replaces it',async()=>{
  mount();await flush();void clickHistory('a');setup().camera.zoom=.12;await flush()
  setup().openTarget(setup().targets.find((target:any)=>target.nodes.length>1));await flush()
  pending[0].resolve(cp('a'));await flush();expect(dialog()?.props['aria-label']).toBe('选择历史节点')
  openLine();await flush();expect(setup().overviewSelection).toBeNull();expect(dialog()?.props['aria-label']).toBe('管理世界线')
})

it('pans an offscreen focused target into view without changing the selected line or head',async()=>{
  mount();await flush();const before={...setup().graph.lines[0]},zoom=setup().camera.zoom
  setup().focusTarget({x:1000,y:700,width:44,height:44});await flush()
  expect(setup().camera).toMatchObject({x:-434,y:-234,zoom})
  expect(setup().graph.lines[0]).toEqual(before);expect(writes()).toHaveLength(0)
})

it('waits for a committed local camera response and uses its new version for renaming',async()=>{
  const server=cameraManagement();mount();await flush();await startCamera();expect(server.server().version).toBe(2)
  openLine();await flush();draft('保留我的新名称');await flush();const saving=managementButton('保存名称').props.onClick();await flush()
  expect(writes()).toHaveLength(1);expect(managementButton('保存名称').props.disabled).toBe(true)
  server.respond();await saving;await flush()
  expect(writes()).toHaveLength(2);expect(writes()[1]![1]?.body).toMatchObject({expectedVersion:2,name:'保留我的新名称'})
  expect(server.server()).toMatchObject({version:3,name:'保留我的新名称'});expect(dialog()).toBeUndefined();expect(text(root)).not.toContain('世界线已更新')
})

it('cancels a pending camera debounce when line management opens',async()=>{
  cameraManagement();mount();await flush();setup().zoom(1.1);openLine();await flush();vi.advanceTimersByTime(700);await flush()
  expect(writes()).toHaveLength(0);draft('直接改名');await flush();await managementButton('保存名称').props.onClick();await flush()
  expect(writes()).toHaveLength(1);expect(writes()[0]![1]?.body).toMatchObject({expectedVersion:1,name:'直接改名'});expect(dialog()).toBeUndefined()
})

it('serializes repeated camera saves using the previous returned line version',async()=>{
  const server=cameraManagement();mount();await flush();await startCamera();setup().zoom(1.2);vi.advanceTimersByTime(700);await flush()
  expect(writes()).toHaveLength(1);server.respond();server.next();await flush()
  expect(writes()).toHaveLength(2);expect(writes()[1]![1]?.body).toMatchObject({expectedVersion:2});expect(server.server().version).toBe(3)
  server.respond();await flush();expect(setup().graph.lines[0].version).toBe(3)
})

it('closing management while awaiting a camera response cancels the pending rename',async()=>{
  const server=cameraManagement();mount();await flush();await startCamera();openLine();await flush();draft('关闭前草稿');await flush()
  const saving=managementButton('保存名称').props.onClick();find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await flush()
  server.respond();await saving;await flush();expect(writes()).toHaveLength(1);expect(server.server().name).toBe('合成世界线');expect(dialog()).toBeUndefined()
})

it.each(['account','navigation'])('ignores an in-flight camera response and pending management write after %s changes',async(kind)=>{
  const server=cameraManagement();mount();await flush();await startCamera();openLine();await flush()
  const saving=managementButton('保存名称').props.onClick();await flush()
  if(kind==='account')fixture.epoch++;else fixture.route.fullPath='/worlds/world-b/lines?line=line-b'
  server.respond();await saving;await flush()
  expect(writes()).toHaveLength(1);expect(setup().graph.lines[0].version).toBe(1);expect(text(root)).not.toContain('世界线已更新')
})

it('paginates every raw checkpoint and reads a packed member without creating a task',async()=>{
  const base=vi.mocked(worldApi).getMockImplementation()!,raw=Array.from({length:123},(_,i)=>({...cp('raw'+i),parentCheckpointId:i?'raw'+(i-1):null,sequence:i,title:'原始节点'+i}))
  vi.mocked(worldApi).mockImplementation((path,options)=>path.endsWith('/graph')?Promise.resolve({lines:[line()],nodes:raw}) as any:base(path,options))
  mount();await flush();const control=find(root,n=>n.tag==='button'&&text(n)==='节点列表')
  expect(control).toBeDefined();control!.props.onClick();await flush()
  expect(setup().listedCheckpoints).toHaveLength(50)
  find(dialog()!,n=>n.tag==='button'&&text(n)==='下一页')!.props.onClick();await flush()
  find(dialog()!,n=>n.tag==='button'&&text(n)==='下一页')!.props.onClick();await flush();expect(setup().listedCheckpoints).toHaveLength(23)
  const chosen=find(dialog()!,n=>n.props['aria-label']==='原始节点122，查看历史节点');expect(chosen,JSON.stringify(setup().listedCheckpoints.map((cp:any)=>cp.title))+' '+text(dialog()!)).toBeDefined()
  chosen!.props.onClick();await flush();expect(vi.mocked(worldApi).mock.calls.at(-1)?.[0]).toBe('/worlds/world-a/checkpoints/raw122')
  pending[0].resolve(raw[122]);await flush();expect(dialog()?.props['aria-label']).toBe('原始节点122');expect(writes()).toHaveLength(0)
})

it('uses two real pointer positions to zoom, suppresses a release click and keeps keyboard selection available',async()=>{
  mount();await flush();const view=find(root,n=>n.props['aria-label']==='可拖动和缩放的世界线图谱')!
  const target={closest:()=>null},point=(id:number,x:number,y:number)=>({button:0,pointerId:id,clientX:x,clientY:y,target,preventDefault:vi.fn()})
  setup().down(point(1,100,100));setup().down(point(2,200,100));setup().move(point(2,300,100));await flush()
  expect(setup().camera.zoom).toBe(1.8)
  setup().up(point(2,300,100));setup().up(point(1,100,100));await flush()
  const release={detail:1,preventDefault:vi.fn(),stopImmediatePropagation:vi.fn()}
  expect(view.props.onClickCapture).toBeDefined();view.props.onClickCapture(release)
  expect(release.preventDefault).toHaveBeenCalledOnce();expect(release.stopImmediatePropagation).toHaveBeenCalledOnce()
  const keyboard={detail:0,preventDefault:vi.fn(),stopImmediatePropagation:vi.fn()};view.props.onClickCapture(keyboard)
  expect(keyboard.preventDefault).not.toHaveBeenCalled()
  setup().locate();await flush();expect(setup().visibleGraph.targets.some((t:any)=>t.nodes.some((n:any)=>n.id==='b')),JSON.stringify({camera:setup().camera,size:setup().viewportSize,visible:setup().visibleGraph.targets})).toBe(true)
  setup().openList();await flush();const b=find(dialog()!,n=>n.props['aria-label']==='历史b，查看历史节点');expect(b).toBeDefined();b!.props.onClick();pending[0].resolve(cp('b'));await flush();expect(dialog()?.props['aria-label']).toBe('历史b')
  expect(vi.mocked(worldApi).mock.calls.some(([,o])=>o?.method==='POST')).toBe(false)
})

it('accepts a fresh stationary pointer tap immediately after suppressing a drag release',async()=>{
  mount();await flush();const view=find(root,n=>n.props['aria-label']==='可拖动和缩放的世界线图谱')!,target={closest:()=>null}
  const point=(x:number)=>({button:0,pointerId:1,clientX:x,clientY:100,target,preventDefault:vi.fn()})
  setup().down(point(100));setup().move(point(140));setup().up(point(140));await flush()
  const ghost={detail:1,preventDefault:vi.fn(),stopImmediatePropagation:vi.fn()};view.props.onClickCapture(ghost);expect(ghost.preventDefault).toHaveBeenCalledOnce()
  setup().down(point(140));setup().up(point(140));const tap={detail:1,preventDefault:vi.fn(),stopImmediatePropagation:vi.fn()};view.props.onClickCapture(tap);expect(tap.preventDefault).not.toHaveBeenCalled()
  void clickHistory('b');pending[0].resolve(cp('b'));await flush();expect(dialog()?.props['aria-label']).toBe('历史b');expect(writes()).toHaveLength(0)
})
