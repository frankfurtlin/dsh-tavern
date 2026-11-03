import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import * as vue from 'vue'
import {createRenderer,defineComponent,h,reactive,ssrContextKey} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import WorldReadPage from './WorldReadPage.vue'
import {worldApi} from './api'
import {api,ApiError} from '../api'
import {cacheGet,cacheSet} from '../platform/bridge'
import {readWorldPackage,downloadedWorld} from '../offline/world-storage'
import SpeechBar from '../components/SpeechBar.vue'
import {compileRender} from '../test/host-renderer'
import {speechEngine} from '../test/speech-engine'

const fixture=vi.hoisted(()=>({route:null as any,epoch:0,owner:'fixture-owner',replace:vi.fn(),push:vi.fn(),cache:new Map<string,unknown>(),packs:new Map<string,any>(),worldDeleted:false,speech:null as any,images:[] as any[],app:{online:true,settings:{fontSize:18,lineHeight:1.9}}}))
vi.mock('vue-router',()=>({useRoute:()=>fixture.route,useRouter:()=>({replace:fixture.replace,push:fixture.push})}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>fixture.epoch,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('./api',()=>({worldApi:vi.fn()}))
vi.mock('../platform/bridge',()=>({accountId:()=>fixture.owner,backendBase:()=> 'https://fixture.invalid',cacheGet:vi.fn(),cacheSet:vi.fn(),native:false}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>false}))
vi.mock('../platform/system-speech',()=>({systemSpeech:()=>fixture.speech.driver}))
vi.mock('../offline/world-storage',()=>({readWorldPackage:vi.fn(),downloadedWorld:vi.fn()}))
vi.mock('../platform/world-cache',()=>({isWorldDeleted:()=>fixture.worldDeleted,isWorldLineDeleted:()=>fixture.worldDeleted}))
vi.mock('../components/DownloadWorldReading.vue',async()=>{const {defineComponent,h}=await import('vue');return {default:defineComponent({props:['worldId','lineId','headId','origin','lineName'],setup:p=>()=>h('div',{'data-download-head':p.headId},'下载控件')})}})
vi.mock('../stores/app',()=>({useApp:()=>fixture.app}))
vi.mock('../platform/request-scope',async()=>{
  const {onUnmounted}=await import('vue')
  return {useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const epoch=fixture.epoch,owner=fixture.owner,path=fixture.route.fullPath;return {auth:epoch,owner,valid:()=>!disposed&&epoch===fixture.epoch&&owner===fixture.owner&&path===fixture.route.fullPath}}}}
})
vi.mock('../components/ReadingExport.vue',()=>({default:defineComponent({props:{scope:Object},setup:p=>()=>h('section',{'data-reading-export-scope':JSON.stringify(p.scope)},'阅读导出')})}))
vi.mock('../components/StoryImages.vue',()=>({default:defineComponent({props:['scope','purpose','side','contentId','paragraphId'],setup:p=>()=>h('section',{'data-image-side':p.side},fixture.images.filter(row=>row.scope.worldId===p.scope?.worldId&&row.scope.lineId===p.scope?.lineId&&row.scope.checkpointId===p.scope?.checkpointId&&row.purpose===p.purpose&&row.contentId===p.contentId&&row.paragraphId===p.paragraphId&&(!p.side||row.side===p.side)).map(row=>h('img',{'data-placement':row.id,alt:row.alt})))})}))
vi.mock('../components/Sheet.vue',async()=>{
  const {defineComponent,h}=await import('vue')
  return {default:defineComponent({props:{title:String},emits:['close'],setup:(p,{emit,slots})=>()=>h('section',{role:'dialog','aria-label':p.title},[h('button',{'aria-label':'关闭',onClick:()=>emit('close')},'关闭'),slots.default?.()])})}
})

// Execute the actual SFC setup/template and DOM handlers, with only native
// scrolling/observer primitives isolated. No server, credentials or provider.
const filename=new URL('./WorldReadPage.vue',import.meta.url),descriptor=parse(readFileSync(filename,'utf8')).descriptor
const template=compileTemplate({source:descriptor.template!.content,filename:filename.pathname,id:'world-read-unit',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'world-read-unit'}).bindings}})
if(template.errors.length)throw new Error(String(template.errors[0]))
const compiled=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,exports:{render?:Function}={}
new Function('require','exports',compiled)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template dependency: '+name)},exports)
Object.assign(WorldReadPage,{render:exports.render})
compileRender(SpeechBar,new URL('../components/SpeechBar.vue',import.meta.url))

type Host={tag:string;text:string;children:Host[];parent:Host|null;props:Record<string,any>;scrollTop:number;dataset:Record<string,string>;scrollIntoView:ReturnType<typeof vi.fn>;querySelectorAll:(selector:string)=>Host[]}
function node(tag:string,text=''):Host{const n:Host={tag,text,children:[],parent:null,props:{},scrollTop:0,dataset:{},scrollIntoView:vi.fn(),querySelectorAll:()=>[]};n.querySelectorAll=()=>all(n,c=>!!c.props['data-checkpoint']);return n}
const renderer=createRenderer<Host,Host>({
  createElement:tag=>node(tag),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
  setText:(n,text)=>{n.text=text},setElementText:(n,text)=>{n.text=text;n.children=[]},patchProp:(n,key,_old,value)=>{n.props[key]=value;if(key==='data-checkpoint')n.dataset.checkpoint=value},
  insert:(n,parent,anchor)=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=parent;const i=anchor?parent.children.indexOf(anchor):-1;if(i<0)parent.children.push(n);else parent.children.splice(i,0,n)},
  remove:n=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=null},
  parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null
})
const text=(n:Host):string=>(n.tag==='#comment'?'':n.text)+n.children.map(text).join('')
function all(n:Host,test:(n:Host)=>boolean):Host[]{return [...(test(n)?[n]:[]),...n.children.flatMap(c=>all(c,test))]}
const find=(test:(n:Host)=>boolean)=>all(root,test)[0]
const flush=async()=>{for(let i=0;i<45;i++)await Promise.resolve()}
const deferred=<T>()=>{let resolve!:(v:T)=>void,reject!:(e:Error)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const info=(active:string|null='line-a',status='active',id='world-a')=>({id,status,activeLineId:active,name:'合成阅读世界',characters:[{id:'actor',available:true}]})
const cp=(id:string,lineId='line-a',worldId='world-a')=>({id,lineId,worldId,title:'标题'+id,text:'正文'+id,kind:'turn',sequence:1,options:[],changes:[],action:'',summary:'',createdAt:'fixture',parentCheckpointId:null})
const line=(id='line-a',worldId='world-a')=>({id,worldId,name:'线'+id,status:'active',version:1,headCheckpointId:id+'-cp',head:cp(id+'-cp',id,worldId),readingPosition:{},camera:{},pendingTaskId:null})
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,observers:{callback:Function;disconnect:()=>void}[]
function mount(){root=node('root');app=renderer.createApp(WorldReadPage);app.provide(ssrContextKey,{modules:new Set()});app.component('RouterLink',defineComponent({props:{to:[String,Object]},setup:(p,{slots})=>()=>h('a',{href:p.to},slots.default?.())}));app.mount(root)}
const setup=()=> (app as any)._instance.setupState
function route(worldId='world-a',lineId='line-a',checkpoint?:string){fixture.route.params.worldId=worldId;fixture.route.query={line:lineId,...(checkpoint?{checkpoint}:{})};fixture.route.path='/worlds/'+worldId+'/read';fixture.route.fullPath=fixture.route.path+'?line='+lineId+(checkpoint?'&checkpoint='+checkpoint:'')}
function deletedFallback(target:string|null='line-b',status='active'){
  vi.mocked(worldApi).mockImplementation((path,options)=>{
    if(path==='/worlds/world-a')return Promise.resolve(info(options?.cache? 'line-a':target,status)) as any
    if(path==='/worlds/world-a/lines/line-a')return Promise.reject(new ApiError('WORLD_LINE_DELETED','这条世界线已删除',404))
    if(path==='/worlds/world-a/lines/line-a/history')return Promise.resolve({items:[cp('deleted-only')]}) as any
    if(path==='/worlds/world-a/lines/line-b')return Promise.resolve(line('line-b')) as any
    if(path==='/worlds/world-a/lines/line-b/history')return Promise.resolve({items:[cp('shared'),cp('b-only','line-b')]}) as any
    throw new Error('Unexpected fixture path: '+path)
  })
}
const patches=()=>vi.mocked(worldApi).mock.calls.filter(([,o])=>o?.method==='PATCH')
it('exports the exact public world head from its directory without changing reading position or action head',async()=>{
  mount();await flush();setup().directory=true;await flush();const open=find(n=>n.tag==='button'&&text(n)==='导出阅读文件 · TXT / EPUB')!;expect(open.props.disabled).toBeFalsy();vi.mocked(worldApi).mockClear();open.props.onClick();await flush();expect(find(n=>n.props['data-reading-export-scope'])?.props['data-reading-export-scope']).toBe(JSON.stringify({kind:'world',worldId:'world-a',lineId:'line-a',checkpointId:'line-a-cp'}));expect(patches()).toHaveLength(0);expect(vi.mocked(worldApi).mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0)
})
beforeEach(()=>{
  fixture.images=[]
  fixture.speech=speechEngine();vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}})
  vi.useFakeTimers();fixture.epoch=0;fixture.owner='fixture-owner';fixture.app.online=true;fixture.cache.clear();fixture.packs.clear();fixture.worldDeleted=false;fixture.route=reactive({params:{worldId:'world-a'},query:{line:'line-a'},path:'/worlds/world-a/read',fullPath:'/worlds/world-a/read?line=line-a'});fixture.replace.mockReset();fixture.push.mockReset();vi.mocked(api).mockReset().mockResolvedValue({} as any)
  vi.mocked(readWorldPackage).mockReset().mockImplementation(async id=>fixture.packs.get(id));vi.mocked(downloadedWorld).mockReset().mockImplementation(async(world,id)=>[...fixture.packs.values()].find(item=>item.payload.world.id===world&&(!id||item.payload.line.id===id)))
  vi.stubGlobal('window',new EventTarget());vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})
  fixture.replace.mockImplementation(async(target:any)=>{fixture.route.query=target.query;fixture.route.path=target.path;fixture.route.fullPath=target.path+'?'+new URLSearchParams(target.query).toString()})
  vi.mocked(cacheGet).mockReset().mockImplementation(async key=>fixture.cache.get(key) as any)
  vi.mocked(cacheSet).mockReset().mockImplementation(async(key,value)=>{fixture.cache.set(key,value)})
  vi.mocked(worldApi).mockReset().mockImplementation(async(path,options)=>{
    const worldId=path.includes('world-b')?'world-b':'world-a',id=path.includes('line-b')?'line-b':'line-a'
    if(options?.method==='PATCH')return {...line(id,worldId),readingPosition:(options.body as any).readingPosition,version:2} as any
    if(path.endsWith('/history'))return {items:[cp(id+'-cp',id,worldId)]} as any
    if(path.includes('/lines/'))return line(id,worldId) as any
    return info(id,'active',worldId) as any
  })
  observers=[];vi.stubGlobal('IntersectionObserver',class {record:{callback:Function;disconnect:()=>void};constructor(callback:Function){this.record={callback,disconnect:vi.fn()};observers.push(this.record)}observe(){}disconnect(){this.record.disconnect()}})
  vi.stubGlobal('document',Object.assign(new EventTarget(),{hidden:false,activeElement:null,getElementById:(id:string)=>root?find(n=>n.props.id===id):null}))
  vi.stubGlobal('matchMedia',()=>({matches:true}))
})
afterEach(()=>{app?.unmount();app=undefined;vi.useRealTimers();vi.unstubAllGlobals()})

it('renders each public world paragraph illustration on its declared side and never exposes another line placement',async()=>{
  const scope={worldId:'world-a',lineId:'line-a',checkpointId:'line-a-cp'},row={scope,purpose:'illustration',contentId:'line-a-cp',paragraphId:'0'}
  fixture.images=[{...row,id:'world-before',side:'before',alt:'前景图'},{...row,id:'world-after',side:'after',alt:'后景图'},{...row,id:'other-line',scope:{...scope,lineId:'line-b'},side:'before',alt:'别线图'}]
  mount();await flush();const prose=find(n=>n.props.id==='world-speech-line-a-cp:0')!,before=all(root,n=>n.props['data-placement']==='world-before'),after=all(root,n=>n.props['data-placement']==='world-after')
  expect(before).toHaveLength(1);expect(after).toHaveLength(1);expect(find(n=>n.props['data-placement']==='other-line')).toBeUndefined()
  expect(prose.parent!.children.indexOf(before[0].parent!)).toBeLessThan(prose.parent!.children.indexOf(prose));expect(prose.parent!.children.indexOf(after[0].parent!)).toBeGreaterThan(prose.parent!.children.indexOf(prose));expect(text(prose)).toBe('正文line-a-cp');expect(patches()).toEqual([])
  route('world-a','line-b');await flush();expect(find(n=>n.props['data-placement']==='world-before')).toBeUndefined();expect(find(n=>n.props['data-placement']==='world-after')).toBeUndefined();expect(text(root)).toContain('正文line-b-cp')
})

it('normalizes a deleted line URL to the retained line and keeps its shared historical checkpoint',async()=>{
  route('world-a','line-a','shared');deletedFallback();mount();await flush()
  expect(fixture.replace).toHaveBeenCalledWith({path:'/worlds/world-a/read',query:{line:'line-b',checkpoint:'shared'}})
  expect(setup().line.id).toBe('line-b');expect(setup().current).toBe('shared');expect(text(root)).toContain('正文shared');expect(text(root)).not.toContain('正文deleted-only')
  expect(fixture.cache.get('world-line-deleted:world-a:line-a')).toBe(true);expect(patches()).toEqual([])
})
it.each(['active','archived'])('clears deleted prose and offers a safe empty entry for a %s world with no remaining line',async(status)=>{
  deletedFallback(null,status);mount();await flush()
  expect(setup().line).toBeNull();expect(setup().history).toEqual([]);expect(setup().current).toBe('');expect(setup().directory).toBe(false)
  expect(find(n=>n.tag==='a'&&n.props.href==='/worlds')).toBeDefined()
  const identity=find(n=>n.tag==='a'&&n.props.href==='/worlds/new?world=world-a')
  if(status==='active')expect(identity).toBeDefined();else expect(identity).toBeUndefined()
  await vi.advanceTimersByTimeAsync(1000);expect(patches()).toEqual([])
})
it.each([['RESOURCE_NOT_FOUND',404],['NETWORK_UNAVAILABLE',0],['WORLD_LINE_DELETED',403]])('does not turn %s/%s into a deletion fallback or marker',async(code,status)=>{
  vi.mocked(worldApi).mockImplementation(async path=>{if(path.includes('/lines/'))throw new ApiError(code,'原始读取错误',status as number);return info() as any})
  mount();await flush();expect(text(root)).toContain('原始读取错误');expect(fixture.replace).not.toHaveBeenCalled();expect(fixture.cache.has('world-line-deleted:world-a:line-a')).toBe(false);expect(patches()).toEqual([])
})
it('keeps an authoritative deletion marker and cleared view when the fresh world request fails',async()=>{
  deletedFallback();const base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>path==='/worlds/world-a'&&!options?.cache?Promise.reject(new ApiError('NETWORK_UNAVAILABLE','刷新网络失败',0)):base(path,options))
  mount();await flush();expect(fixture.cache.get('world-line-deleted:world-a:line-a')).toBe(true);expect(text(root)).toContain('刷新网络失败');expect(setup().line).toBeNull();expect(setup().history).toEqual([]);expect(fixture.replace).not.toHaveBeenCalled()
})
it('refuses old cached line prose after an authoritative deletion while retained lines still read offline normally',async()=>{
  fixture.cache.set('world-line-deleted:world-a:line-a',true)
  const base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>path==='/worlds/world-a'&&!options?.cache?Promise.reject(new ApiError('NETWORK_UNAVAILABLE','无法查找保留线',0)):base(path,options))
  mount();await flush();expect(text(root)).not.toContain('正文line-a-cp');expect(vi.mocked(worldApi).mock.calls.some(([p])=>p.includes('/lines/line-a'))).toBe(false)
  route('world-a','line-b');await flush();expect(text(root)).toContain('正文line-b-cp');expect(setup().line.id).toBe('line-b')
})
it('clears displayed text, the open directory and queued position writes when a same-route reload detects deletion',async()=>{
  mount();await flush();find(n=>n.props['aria-label']==='世界正文目录'&&n.tag==='button')!.props.onClick();await flush();expect(find(n=>n.props.role==='dialog')).toBeDefined()
  setup().scrolling();deletedFallback(null);await setup().load();await flush();await vi.advanceTimersByTimeAsync(1000)
  expect(text(root)).not.toContain('正文line-a-cp');expect(find(n=>n.props.role==='dialog')).toBeUndefined();expect(setup().current).toBe('');expect(patches()).toEqual([])
})
it.each(['query','local','server'])('never saves a nonexistent %s checkpoint and selects readable content',async(source)=>{
  if(source==='query')route('world-a','line-a','missing')
  if(source==='local')fixture.cache.set('world-reading:world-a:line-a',{checkpointId:'missing',scroll:44})
  const base=vi.mocked(worldApi).getMockImplementation()!
  if(source==='server')vi.mocked(worldApi).mockImplementation((path,options)=>path==='/worlds/world-a/lines/line-a'&&!options?.method?Promise.resolve({...line(),readingPosition:{checkpointId:'missing',scroll:99}}) as any:base(path,options))
  mount();await flush();expect(setup().current).toBe('line-a-cp');await setup().save();await flush()
  expect(patches()).toHaveLength(1);expect(patches()[0]![1]?.body).toMatchObject({readingPosition:{checkpointId:'line-a-cp'}})
})
it('ignores a stale directory handler and disconnected observer after loading another line',async()=>{
  mount();await flush();const oldJump=()=>setup().jump('line-a-cp'),observer=observers[0]!
  route('world-a','line-b');await flush();await oldJump();observer.callback([{isIntersecting:true,target:{dataset:{checkpoint:'line-a-cp'}}}]);await flush()
  expect(setup().current).toBe('line-b-cp');await setup().save();expect(patches()[0]![0]).toContain('/lines/line-b');expect(patches()[0]![1]?.body).toMatchObject({readingPosition:{checkpointId:'line-b-cp'}})
})
it.each(['success','error'])('keeps the latest same-route load when an older history %s arrives last',async(outcome)=>{
  const old=deferred<any>();let histories=0;const base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>path.endsWith('/history')?(++histories===1?old.promise:Promise.resolve({items:[cp('latest')]})) as any:base(path,options))
  mount();await flush();await setup().load();await flush();expect(text(root)).toContain('正文latest')
  if(outcome==='success')old.resolve({items:[cp('older')]});else old.reject(new Error('旧错误'));await flush()
  expect(text(root)).toContain('正文latest');expect(text(root)).not.toContain('正文older');expect(text(root)).not.toContain('旧错误')
})
it.each(['account','navigation','unmount','new-world'])('ignores an old deletion reply after %s changes',async(kind)=>{
  const old=deferred<any>(),base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>path==='/worlds/world-a/lines/line-a'?old.promise:base(path,options))
  mount();await flush()
  if(kind==='account')fixture.epoch++
  else if(kind==='unmount'){app!.unmount();app=undefined}
  else if(kind==='new-world'){route('world-b','line-b');await flush()}
  else fixture.route.fullPath='/worlds'
  old.reject(new ApiError('WORLD_LINE_DELETED','旧线已删除',404));await flush()
  expect(fixture.cache.has('world-line-deleted:world-a:line-a')).toBe(false);expect(fixture.replace).not.toHaveBeenCalled();expect(text(root)).not.toContain('旧线已删除')
  if(kind==='new-world')expect(text(root)).toContain('正文line-b-cp')
})
it('does not PATCH or replace the new reader after an old position cache write resolves late',async()=>{
  mount();await flush();const waiting=deferred<void>()
  vi.mocked(cacheSet).mockImplementation(()=>waiting.promise)
  void setup().save();await flush();route('world-b','line-b');await flush();waiting.resolve();await flush()
  expect(patches()).toEqual([]);expect(setup().line.worldId).toBe('world-b');expect(text(root)).toContain('正文line-b-cp')
})

// Leaving before the debounce must keep only the last valid local position.
// No network request may start during component teardown.
it('keeps the real last scroll locally when normal navigation unmounts before the debounce',async()=>{
  const base=vi.mocked(worldApi).getMockImplementation()!
  vi.mocked(worldApi).mockImplementation((path,options)=>path.endsWith('/history')?Promise.resolve({items:[cp('first'),cp('last')]}) as any:base(path,options))
  mount();await flush()
  observers[0]!.callback([{isIntersecting:true,target:{dataset:{checkpoint:'last'}}}])
  setup().viewport.scrollTop=321;setup().scrolling();await vi.advanceTimersByTimeAsync(699)
  expect(vi.mocked(cacheSet).mock.calls).toEqual([])
  fixture.route.fullPath='/worlds'
  app!.unmount();app=undefined;await flush()
  expect(fixture.cache.get('world-reading:world-a:line-a')).toEqual({checkpointId:'last',scroll:321})
  expect(patches()).toEqual([])
})
it.each(['auth','owner'])('does not save exit positions after the loaded %s identity changes',async(kind)=>{
  mount();await flush();setup().viewport.scrollTop=321;setup().scrolling()
  if(kind==='auth')fixture.epoch++;else fixture.owner='different-fixture-owner'
  app!.unmount();app=undefined;await flush()
  expect(vi.mocked(cacheSet).mock.calls).toEqual([]);expect(patches()).toEqual([])
})
it('does not save a position on exit after authoritative deletion has cleared the reading state',async()=>{
  mount();await flush();setup().viewport.scrollTop=321;setup().scrolling();deletedFallback(null)
  await setup().load();await flush();expect(setup().line).toBeNull()
  app!.unmount();app=undefined;await flush()
  expect(vi.mocked(cacheSet).mock.calls.filter(([key])=>key.startsWith('world-reading:'))).toEqual([])
  expect(fixture.cache.get('world-line-deleted:world-a:line-a')).toBe(true);expect(patches()).toEqual([])
})
it('does not save an invalid checkpoint when exiting before the debounce',async()=>{
  mount();await flush();setup().current='missing';setup().viewport.scrollTop=321;setup().scrolling()
  app!.unmount();app=undefined;await flush()
  expect(vi.mocked(cacheSet).mock.calls).toEqual([]);expect(patches()).toEqual([])
})

it('validates an originating story and returns to it after saving only the reading position',async()=>{
  const origin='book-'+'a'.repeat(32)
  fixture.route.query.story=origin;fixture.route.fullPath+='&story='+origin
  mount();await flush()
  expect(vi.mocked(api).mock.calls[0]).toEqual(['/stories/'+origin+'/context/validate',{method:'POST',body:{storyId:origin,mode:'read',perspective:'reader',entryOrigin:'worlds',worldId:'world-a',worldLineId:'line-a',checkpointId:'line-a-cp'}}])
  expect(setup().playLink.query.story).toBe(origin)
  await setup().leave();await flush();expect(fixture.push).toHaveBeenCalledWith('/stories/'+origin)
  expect(patches()).toHaveLength(1);expect(patches()[0]![1]?.body).toEqual({expectedVersion:1,readingPosition:{checkpointId:'line-a-cp',scroll:0}})
})
it('does not preserve a denied story origin or advance a world head',async()=>{
  const worldId='b'.repeat(32),origin='book-'+'a'.repeat(32)
  route(worldId,'line-a');fixture.route.query.story=origin;fixture.route.fullPath+='&story='+origin
  vi.mocked(api).mockRejectedValueOnce(new ApiError('NOT_FOUND','故事不存在',404))
  vi.mocked(worldApi).mockImplementation(async(path,options)=>options?.method==='PATCH'?{...line('line-a',worldId),version:2}:path.endsWith('/history')?{items:[cp('line-a-cp','line-a',worldId)]}:path.includes('/lines/')?line('line-a',worldId):info('line-a','active',worldId) as any)
  mount();await flush();expect(setup().returnStory).toBe('');expect(setup().playLink.query.story).toBeUndefined()
  await setup().leave();expect(fixture.push).toHaveBeenCalledWith('/stories/world-'+worldId)
  expect(patches().every(([,options])=>Object.keys(options!.body as object).every(key=>['expectedVersion','readingPosition'].includes(key)))).toBe(true)
})
it('ignores a late origin validation after a different world starts loading',async()=>{
  const old=deferred<any>();vi.mocked(api).mockReturnValueOnce(old.promise)
  fixture.route.query.story='book-'+'a'.repeat(32);fixture.route.fullPath+='&story='+fixture.route.query.story
  mount();await flush();route('world-b','line-b');await flush();old.resolve({});await flush()
  expect(setup().world.id).toBe('world-b');expect(setup().returnStory).toBe('');expect(text(root)).toContain('正文line-b-cp');expect(patches()).toEqual([])
})

function offlinePack(){return {id:'world-a:line-a:last',payload:{world:info(),line:{...line(),headCheckpointId:'last'},history:[cp('first'),cp('unvisited'),cp('last')],origins:['world-world-a']}}}
it('renders every downloaded chapter offline without fetching visited world caches or sending a position PATCH',async()=>{
  fixture.app.online=false;const pack=offlinePack();fixture.packs.set(pack.id,pack);fixture.route.query.download=pack.id;fixture.route.fullPath+='&download='+pack.id
  mount();await flush();expect(text(root)).toContain('正文first');expect(text(root)).toContain('正文unvisited');expect(text(root)).toContain('正文last');expect(setup().snapshot).toBe(true)
  expect(worldApi).not.toHaveBeenCalled();expect(api).not.toHaveBeenCalled();await setup().jump('unvisited');await setup().save();await flush()
  expect(fixture.cache.get('world-reading:world-a:line-a')).toMatchObject({checkpointId:'unvisited'});expect(patches()).toEqual([]);expect(pack.payload.line.headCheckpointId).toBe('last')
})
it('accepts only a package-validated origin while reading an archived snapshot, without context POSTs',async()=>{
  const worldId='b'.repeat(32);route(worldId);fixture.app.online=false;const pack=offlinePack();pack.payload.world.id=worldId;pack.payload.line.worldId=worldId;pack.payload.origins=['world-'+worldId];pack.payload.line.status='archived';fixture.packs.set(pack.id,pack);fixture.route.query.story='book-'+'a'.repeat(32);fixture.route.fullPath+='&story='+fixture.route.query.story
  mount();await flush();expect(text(root)).toContain('正文unvisited');expect(setup().returnStory).toBe('');await setup().leave();expect(fixture.push).toHaveBeenCalledWith('/stories/world-'+worldId);expect(api).not.toHaveBeenCalled();expect(patches()).toEqual([])
})
it('refuses partial visited prose offline when the requested world line was never explicitly downloaded',async()=>{
  fixture.app.online=false;fixture.cache.set('world-api:/worlds/world-a/lines/line-a/history',{items:[cp('visited')]})
  mount();await flush();expect(text(root)).toContain('尚未下载');expect(text(root)).not.toContain('正文visited');expect(worldApi).not.toHaveBeenCalled();expect(patches()).toEqual([])
})
it('opens the actual download sheet only from the existing directory and passes the exact current head',async()=>{
  mount();await flush();expect(find(n=>n.props['data-download-head'])).toBeUndefined()
  find(n=>n.props['aria-label']==='世界正文目录'&&n.tag==='button')!.props.onClick();await flush()
  find(n=>n.tag==='button'&&text(n).includes('下载这条世界线'))!.props.onClick();await flush()
  expect(find(n=>n.props['aria-label']==='下载世界线')).toBeDefined();expect(find(n=>n.props['data-download-head']==='line-a-cp')).toBeDefined();expect(patches()).toEqual([])
})
it('immediately clears downloaded prose and its directory after an authoritative world-deleted event',async()=>{
  fixture.app.online=false;const pack=offlinePack();fixture.packs.set(pack.id,pack);mount();await flush()
  find(n=>n.props['aria-label']==='世界正文目录'&&n.tag==='button')!.props.onClick();await flush();setup().scrolling()
  fixture.worldDeleted=true;window.dispatchEvent(new CustomEvent('reader-world-deleted',{detail:{worldId:'world-a'}}));await flush();await vi.advanceTimersByTimeAsync(1000)
  expect(text(root)).not.toContain('正文first');expect(text(root)).toContain('这个世界已删除');expect(find(n=>n.props.role==='dialog')).toBeUndefined();expect(patches()).toEqual([])
})
it.each(['account','route'])('ignores a late verified package after %s changes',async(kind)=>{
  fixture.app.online=false;const pack=offlinePack(),held=deferred<any>();fixture.route.query.download=pack.id;fixture.route.fullPath+='&download='+pack.id;vi.mocked(readWorldPackage).mockReturnValueOnce(held.promise)
  mount();await flush();if(kind==='account')fixture.epoch++;else fixture.route.fullPath='/worlds';held.resolve(pack);await flush()
  expect(text(root)).not.toContain('正文unvisited');expect(patches()).toEqual([])
})
it('narrates only the loaded public history without any action, position PATCH or world head change',async()=>{
  const worldId='a'.repeat(32);route(worldId);vi.mocked(worldApi).mockImplementation(async path=>path.endsWith('/history')?{items:[cp('line-a-cp','line-a',worldId)]}:path.includes('/lines/')?line('line-a',worldId):info('line-a','active',worldId) as any)
  mount();await flush();find(n=>n.props['aria-label']==='世界正文目录'&&n.tag==='button')!.props.onClick();await flush();await find(n=>n.tag==='button'&&text(n).includes('播放公开正文'))!.props.onClick();await flush();expect(fixture.speech.driver.loadQueue).toHaveBeenCalledOnce();const queue=fixture.speech.driver.loadQueue.mock.calls[0][0];expect(queue.segments[0].text).toBe('正文line-a-cp');expect(queue.segments[0].anchor).toMatchObject({worldId,worldLineId:'line-a',checkpointId:'line-a-cp',offsetUnit:'utf16'});expect(JSON.stringify(queue)).not.toContain('options');expect(JSON.stringify(queue)).not.toContain('changes')
  fixture.speech.emit({status:'ended',lastCompletedId:'line-a-cp:0',eventVersion:4});await flush();expect(find(n=>n.props.id==='world-speech-line-a-cp:0')!.props.class).toContain('speech-completed');expect(setup().line.headCheckpointId).toBe('line-a-cp');expect(patches()).toEqual([]);expect(vi.mocked(worldApi).mock.calls.every(([,options])=>!options?.method)).toBe(true)
})
