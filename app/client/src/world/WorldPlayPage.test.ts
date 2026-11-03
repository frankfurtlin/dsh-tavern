import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import * as vue from 'vue'
import {createRenderer,defineComponent,h,reactive,ssrContextKey} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import WorldPlayPage from './WorldPlayPage.vue'
import {worldApi} from './api'
import {ApiError} from '../api'
import {defaultRules} from './types'
import type {WorldCheckpoint} from './types'
import {cacheUpdate} from '../platform/bridge'

const fixture=vi.hoisted(()=>({store:null as any,route:null as any,epoch:0,push:vi.fn()}))
vi.mock('vue-router',()=>({useRoute:()=>fixture.route,useRouter:()=>({push:fixture.push})}))
vi.mock('./store',()=>({useWorld:()=>fixture.store}))
vi.mock('../stores/app',()=>({useApp:()=>({online:true})}))
vi.mock('../api',()=>({newKey:()=> 'unused-history-key',authGeneration:()=>fixture.epoch,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('./api',()=>({worldApi:vi.fn()}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>null),cacheSet:vi.fn(async()=>{}),cacheUpdate:vi.fn(async(_key:string,update:(value:unknown)=>unknown,valid:()=>boolean)=>valid()?update(null):undefined)}))
vi.mock('../components/WorldAppearanceProvider.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('section',slots.default?.())})}))
vi.mock('./SceneReply.vue',()=>({default:defineComponent({props:{segments:Array},setup:props=>()=>h('div',(props.segments as any[]||[]).map(part=>part.text).join('\n\n'))})}))
vi.mock('../components/CharacterAvatar.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldScene.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldPortraitScene.vue',()=>({default:defineComponent({setup:()=>()=>h('section',{'aria-label':'360度互动场景'})})}))
vi.mock('./WorldDetails.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldWritePanel.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldCorrection.vue',()=>({default:{render:()=>null}}))
vi.mock('../components/Sheet.vue',async()=>{
  const {defineComponent,h}=await import('vue')
  return {default:defineComponent({props:{title:String},emits:['close'],setup:(props,{emit,slots})=>()=>h('section',{role:'dialog','aria-label':props.title},[h('h2',props.title),h('button',{'aria-label':'关闭',onClick:()=>emit('close')},'关闭'),slots.default?.()])})}
})
vi.mock('../platform/request-scope',async()=>{
  const {onUnmounted}=await import('vue')
  return {useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const epoch=fixture.epoch,path=fixture.route.fullPath;return {valid:()=>!disposed&&epoch===fixture.epoch&&path===fixture.route.fullPath}}}}
})
// Run the actual page setup and client template; only child frames/data providers
// are isolated. Sheet emits reach the page's real template close handlers.
vi.mock('../components/StoryImages.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('section',slots.fallback?.())})}))
const filename=new URL('./WorldPlayPage.vue',import.meta.url),descriptor=parse(readFileSync(filename,'utf8')).descriptor
const template=compileTemplate({source:descriptor.template!.content,filename:filename.pathname,id:'history-play-unit',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'history-play-unit'}).bindings}})
if(template.errors.length)throw new Error(String(template.errors[0]))
const compiled=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,templateExports:{render?:Function}={}
new Function('require','exports',compiled)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template dependency: '+name)},templateExports)
Object.assign(WorldPlayPage,{render:templateExports.render})
type HostNode={tag:string;tagName:string;text:string;value:any;children:HostNode[];parent:HostNode|null;props:Record<string,any>;addEventListener:EventTarget['addEventListener'];removeEventListener:EventTarget['removeEventListener'];scrollTop:number;scrollHeight:number;clientHeight:number}
function node(tag:string,text=''):HostNode{const events=new EventTarget();return {tag,tagName:tag.toUpperCase(),text,value:'',children:[],parent:null,props:{},addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events),scrollTop:0,scrollHeight:100,clientHeight:100}}
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
const cp=(id:string):WorldCheckpoint=>({id,worldId:'world-a',lineId:'line-a',parentCheckpointId:'start',sequence:id==='a'?1:2,title:'历史'+id,text:'历史正文'+id,action:'',options:[],changes:[],summary:'',kind:'action',createdAt:'fixture'})
let app:ReturnType<typeof renderer.createApp>,root:HostNode,pending:ReturnType<typeof deferred<WorldCheckpoint>>[]
function mount(){root=node('root');app=renderer.createApp(WorldPlayPage);app.provide(ssrContextKey,{modules:new Set()});app.component('RouterLink',defineComponent({setup:(_props,{slots})=>()=>h('a',slots.default?.())}));app.mount(root)}
const dialog=()=>find(root,n=>n.props.role==='dialog')
function clickHistory(id:string){const button=find(root,n=>n.props['aria-label']==='查看历史'+id+'的历史操作');expect(button).toBeDefined();return button!.props.onClick()}
beforeEach(()=>{
  vi.useFakeTimers();const events=new EventTarget();vi.stubGlobal('window',events);vi.stubGlobal('document',{activeElement:null,querySelector:()=>null});fixture.epoch=0;fixture.push.mockReset()
  fixture.route=reactive({params:{worldId:'world-a'},query:{line:'line-a'},fullPath:'/worlds/world-a/play?line=line-a'})
  fixture.store=reactive({world:{id:'world-a',name:'合成历史世界',status:'active',sourceBookId:null},line:{id:'line-a',name:'合成世界线',status:'active',headCheckpointId:'b',head:cp('b')},state:{playerId:'',minute:0,timeLabel:'fixture',location:'',ended:false,entities:[],lore:[],rules:defaultRules(),definitionVersion:1},history:[cp('a'),cp('b')],drafts:[],input:'',running:false,recovering:false,busy:false,connected:true,error:'',task:null,load:vi.fn().mockResolvedValue(undefined),saveInput:vi.fn().mockResolvedValue(undefined),stopWatching:vi.fn(),act:vi.fn(),cancel:vi.fn()})
  pending=[];vi.mocked(worldApi).mockReset().mockImplementation(()=>{const read=deferred<WorldCheckpoint>();pending.push(read);return read.promise as any})
})
afterEach(()=>{app?.unmount();vi.useRealTimers();vi.unstubAllGlobals()})

it('opens sprite conversations inside the normal 360 world without a separate mode entry',async()=>{
  fixture.route.fullPath='/worlds/world-a/explore?line=line-a'
  fixture.store.world.portraitDialogue=true
  mount();await flush()
  const setup=(app as any)._instance.setupState
  expect(setup.portraitMode).toBe(true)
  expect(find(root,n=>n.props['aria-label']==='360度互动场景')).toBeDefined()
  const modes=find(root,n=>n.props['aria-label']==='游玩模式')!
  expect(modes.children.filter(n=>n.tag==='button').map(text)).toEqual(['360° 世界','纯故事'])
  setup.setMode(true)
  expect(fixture.push).toHaveBeenLastCalledWith({path:'/worlds/world-a/explore',query:{line:'line-a'}})
  setup.setMode(false)
  expect(fixture.push).toHaveBeenLastCalledWith({path:'/worlds/world-a/play',query:{line:'line-a'}})
})

it('opens a publication at its exact anchor while preserving the validated story entry origin',async()=>{
  mount();await flush();const setup=(app as any)._instance.setupState,origin='world-'+'a'.repeat(32)
  setup.returnStory=origin
  const anchor={bookId:'published-book',branchId:'published-line',revisionId:'published-revision',chapterId:'published-chapter',blockId:'published-block',start:0,end:0,quoteHash:'verified-hash',offsetUnit:'utf16'}
  vi.mocked(worldApi).mockResolvedValueOnce({bookId:anchor.bookId,branchId:anchor.branchId,revisionId:anchor.revisionId,readingAnchor:anchor})
  await setup.publish();await flush()
  expect(fixture.push).toHaveBeenCalledWith({path:'/books/published-book/read',query:{branch:anchor.branchId,revision:anchor.revisionId,chapter:anchor.chapterId,anchor:JSON.stringify(anchor),story:origin}})
  expect(fixture.store.line.headCheckpointId).toBe('b')
})
it('shows shared prose once when a state correction retains the preceding scene',async()=>{
  const first=cp('a'),correction={...cp('b'),kind:'correction',text:first.text}
  fixture.store.history=[first,correction];fixture.store.line.head=correction
  mount();await flush()
  expect((app as any)._instance.setupState.scenes.map((c:WorldCheckpoint)=>c.id)).toEqual(['a'])
  expect(text(root).split(first.text)).toHaveLength(2)
  expect((app as any)._instance.setupState.head.id).toBe('b')
})
it('keeps distinct correction prose visible in story mode',async()=>{
  fixture.store.history[1].kind='correction'
  mount();await flush()
  expect(text(root)).toContain('历史正文a');expect(text(root)).toContain('历史正文b')
})
it('marks a removed source without leaving a link back to deleted prose',async()=>{
  fixture.store.world.sourceBookId='source-book';fixture.store.world.sourceAnchor={branchId:'source-line'};fixture.store.world.sourceRemoved=false
  mount();await flush();await menu()
  expect(text(dialog()!)).toContain('回到来源小说');const setup=(app as any)._instance.setupState;setup.returnStory='book-source-book'
  const event=new Event('reader-book-deleted');Object.defineProperty(event,'detail',{value:{bookId:'source-book'}});window.dispatchEvent(event);await flush()
  expect(text(root)).toContain('来源已移除');expect(text(dialog()!)).not.toContain('回到来源小说');expect(fixture.store.line.headCheckpointId).toBe('b');expect(setup.returnLink).toBe('/stories/world-world-a')
})
it('keeps the validated story origin in play-to-read and play-to-graph links',async()=>{
  mount();await flush();const setup=(app as any)._instance.setupState,origin='book-'+'a'.repeat(32)
  setup.returnStory=origin
  expect(setup.viewLink('read')).toEqual({path:'/worlds/world-a/read',query:{line:'line-a',story:origin}})
  expect(setup.viewLink('lines')).toEqual({path:'/worlds/world-a/lines',query:{line:'line-a',story:origin}})
})

it.each(['fork','rewrite'] as const)('allows another %s after confirmed navigation without leaving an old intent',async operation=>{
  mount();await flush()
  const intents=new Map<string,unknown>(),mock=vi.mocked(cacheUpdate),original=mock.getMockImplementation()
  mock.mockImplementation(async(key,update,valid)=>{
    if(valid&&!valid())return undefined
    const result=update(intents.get(key) as any);intents.set(key,result);return result as any
  })
  const setup=(app as any)._instance.setupState
  fixture.push.mockImplementation(async()=>{fixture.route.fullPath+='#new-line'})
  vi.mocked(worldApi).mockResolvedValue(operation==='fork'?{id:'line-b'}:{lineId:'line-b'})
  try{
    setup.selected=cp('a')
    if(operation==='fork')await setup.fork('continue',cp('a'));else await setup.rewrite()
    setup.selected=cp('b')
    if(operation==='fork')await setup.fork('continue',cp('b'));else await setup.rewrite()
    expect(fixture.push).toHaveBeenCalledTimes(2)
    expect(intents.get('world-mutation:'+operation+':world-a')).toBeNull()
  }finally{mock.mockImplementation(original!)}
})

it('keeps the later selected history and its actual dialog when an older success arrives last',async()=>{
  mount();await flush();void clickHistory('a');void clickHistory('b');expect(pending).toHaveLength(2)
  pending[1].resolve(cp('b'));await flush();expect(dialog()?.props['aria-label']).toBe('历史b')
  pending[0].resolve(cp('a'));await flush();expect(dialog()?.props['aria-label']).toBe('历史b');expect(text(dialog()!)).toContain('历史正文b')
  expect((app as any)._instance.setupState.selected.id).toBe('b')
})
it('ignores an older history error after a later history dialog is visible',async()=>{
  mount();await flush();void clickHistory('a');void clickHistory('b');pending[1].resolve(cp('b'));await flush();pending[0].reject(new Error('stale-history-error'));await flush()
  expect(dialog()?.props['aria-label']).toBe('历史b');expect(text(root)).not.toContain('stale-history-error');expect(find(root,n=>n.props.role==='alert')).toBeUndefined()
})
it.each(['success','error'])('does not reopen or show an old error after closing the existing dialog with a pending %s',async(outcome)=>{
  mount();await flush();void clickHistory('b');pending[0].resolve(cp('b'));await flush();void clickHistory('a')
  find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await flush();expect(dialog()).toBeUndefined()
  if(outcome==='success')pending[1].resolve(cp('a'));else pending[1].reject(new Error('closed-history-error'));await flush()
  expect(dialog()).toBeUndefined();expect(text(root)).not.toContain('closed-history-error');expect((app as any)._instance.setupState.selected).toBeNull()
})
it('preserves a deliberately opened operation sheet instead of replacing it with pending history',async()=>{
  mount();await flush();void clickHistory('a');find(root,n=>n.props['aria-label']==='世界操作')!.props.onClick();await flush();expect(dialog()?.props['aria-label']).toBe('这条世界线')
  pending[0].resolve(cp('a'));await flush();expect(dialog()?.props['aria-label']).toBe('这条世界线');expect((app as any)._instance.setupState.selected).toBeNull()
})
it.each([['account','success'],['account','error'],['navigation','success'],['navigation','error']])('ignores pending history after %s changes with a late %s',async(kind,outcome)=>{
  mount();await flush();void clickHistory('a')
  if(kind==='account')fixture.epoch++;else fixture.route.fullPath='/worlds/world-b/play?line=line-b'
  if(outcome==='success')pending[0].resolve(cp('a'));else pending[0].reject(new Error('old-scope-history-error'));await flush()
  expect(dialog()).toBeUndefined();expect((app as any)._instance.setupState.selected).toBeNull();expect(text(root)).not.toContain('old-scope-history-error')
})

function renameFixture(){
  fixture.store.line.version=1
  let server={...fixture.store.line,name:'另一设备名称',version:2,status:'archived'}
  fixture.store.reloadHead=vi.fn(async()=>{fixture.store.line={...server}})
  vi.mocked(worldApi).mockImplementation((path,options)=>{
    if(path!=='/worlds/world-a/lines/line-a')throw new Error('Unexpected rename request: '+path)
    if(!options?.method)return Promise.resolve({...server}) as any
    const body=options.body as {expectedVersion:number;name:string}
    if(body.expectedVersion!==server.version)return Promise.reject(new ApiError('VERSION_CONFLICT','世界线已更新，请刷新后重试',409))
    server={...server,name:body.name,version:server.version+1};return Promise.resolve({...server}) as any
  })
  return {server:()=>server}
}
async function menu(){find(root,n=>n.props['aria-label']==='世界操作')!.props.onClick();await flush()}
function menuButton(label:string){const button=find(dialog()!,n=>n.tag==='button'&&text(n)===label);expect(button).toBeDefined();return button!}
function nameDraft(value:string){find(dialog()!,n=>n.tag==='input')!.props['onUpdate:modelValue'](value)}
const renameWrites=()=>vi.mocked(worldApi).mock.calls.filter(([,options])=>options?.method==='PATCH')
async function renameConflict(){await menu();nameDraft('我的名称草稿');await flush();await menuButton('保存名称').props.onClick();await flush()}

it('recovers menu rename through explicit fresh metadata without replacing the draft or automatically saving',async()=>{
  mount();await flush();const server=renameFixture();await renameConflict()
  const alert=find(dialog()!,n=>n.props.role==='alert');expect(alert).toBeDefined();expect(text(alert!)).toContain('世界线已更新');expect(menuButton('保存名称').props.disabled).toBe(true)
  await menuButton('载入最新信息').props.onClick();await flush()
  expect(renameWrites()).toHaveLength(1);expect(server.server().name).toBe('另一设备名称')
  expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('我的名称草稿');expect(text(dialog()!)).toContain('最新名称：另一设备名称')
  expect(fixture.store.line).toMatchObject({version:2,status:'archived'});expect(find(root,n=>n.props.class==='world-archived-note')).toBeDefined()
  await menuButton('保存名称').props.onClick();await flush()
  expect(renameWrites()[1]![1]?.body).toMatchObject({expectedVersion:2,name:'我的名称草稿'})
  expect(server.server()).toMatchObject({version:3,status:'archived',name:'我的名称草稿'});expect(dialog()).toBeUndefined()
})

it.each(['success','error'])('does not close or report into a reopened menu after an older rename %s',async(outcome)=>{
  mount();await flush();renameFixture();await menu();nameDraft('旧草稿');await flush()
  const old=deferred<any>();vi.mocked(worldApi).mockImplementation(()=>old.promise);void menuButton('保存名称').props.onClick()
  find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await menu();nameDraft('新菜单草稿');await flush()
  if(outcome==='success')old.resolve({...fixture.store.line,name:'旧请求保存'});else old.reject(new Error('旧改名错误'));await flush()
  expect(dialog()?.props['aria-label']).toBe('这条世界线');expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('新菜单草稿')
  expect(text(root)).not.toContain('旧改名错误');expect(menuButton('保存名称').props.disabled).toBe(false);expect(fixture.store.reloadHead).not.toHaveBeenCalled()
})

it.each(['success','error'])('does not apply an older explicit refresh %s after reopening the menu',async(outcome)=>{
  mount();await flush();renameFixture();await renameConflict();const old=deferred<any>();vi.mocked(worldApi).mockImplementation(()=>old.promise)
  void menuButton('载入最新信息').props.onClick();find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await menu();nameDraft('新菜单草稿');await flush()
  if(outcome==='success')old.resolve({...fixture.store.line,name:'迟到名称',version:99});else old.reject(new Error('旧刷新错误'));await flush()
  expect(fixture.store.line.version).toBe(1);expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('新菜单草稿')
  expect(text(root)).not.toContain('旧刷新错误');expect(text(dialog()!)).not.toContain('迟到名称');expect(menuButton('保存名称').props.disabled).toBe(false)
})

it('does not close a newer sheet if rename reload finishes after the old menu was closed',async()=>{
  mount();await flush();renameFixture();await menu();const old=deferred<any>();vi.mocked(worldApi).mockResolvedValue(fixture.store.line);fixture.store.reloadHead=vi.fn(()=>old.promise)
  void menuButton('保存名称').props.onClick();await flush();find(dialog()!,n=>n.props['aria-label']==='关闭')!.props.onClick();await menu();nameDraft('新菜单草稿');await flush()
  old.resolve(undefined);await flush();expect(dialog()?.props['aria-label']).toBe('这条世界线');expect(find(dialog()!,n=>n.tag==='input')!.value).toBe('新菜单草稿')
})

it.each(['account','navigation'].flatMap(scope=>['success','error'].map(outcome=>[scope,outcome])))('ignores pending rename after %s changes with late %s',async(kind,outcome)=>{
  mount();await flush();renameFixture();await menu();const old=deferred<any>();vi.mocked(worldApi).mockImplementation(()=>old.promise);void menuButton('保存名称').props.onClick()
  if(kind==='account')fixture.epoch++;else fixture.route.fullPath='/worlds/world-b/play?line=line-b'
  if(outcome==='success')old.resolve(fixture.store.line);else old.reject(new Error('旧会话改名错误'));await flush()
  expect(text(root)).not.toContain('旧会话改名错误');expect(fixture.store.reloadHead).not.toHaveBeenCalled();expect(renameWrites()).toHaveLength(1)
})
