import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import * as vue from 'vue'
import {createRenderer,defineComponent,h,reactive,ssrContextKey} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import WorldWritePanel from './WorldWritePanel.vue'
import {worldApi} from './api'
import type {WorldTurn} from './types'

const fixture=vi.hoisted(()=>({store:null as any,epoch:0}))
vi.mock('./store',()=>({useWorld:()=>fixture.store}))
vi.mock('./api',()=>({worldApi:vi.fn()}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>null),cacheUpdate:vi.fn(async(_key:string,update:(value:unknown)=>unknown,valid:()=>boolean)=>valid()?update(null):undefined)}))
vi.mock('../api',()=>({newKey:()=> 'unused-unit-key'}))
vi.mock('../platform/request-scope',async()=>{
  const {onUnmounted}=await import('vue')
  return {useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const epoch=fixture.epoch;return {valid:()=>!disposed&&epoch===fixture.epoch}}}}
})
// Render the actual client template with the real SFC setup in memory. Node's
// Vitest transform supplies SSR setup, so compile only its client render here.
const filename=new URL('./WorldWritePanel.vue',import.meta.url),descriptor=parse(readFileSync(filename,'utf8')).descriptor
const template=compileTemplate({source:descriptor.template!.content,filename:filename.pathname,id:'write-unit',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'write-unit'}).bindings}})
if(template.errors.length)throw new Error(String(template.errors[0]))
const compiled=ts.transpileModule(template.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,templateExports:{render?:Function}={}
new Function('require','exports',compiled)((name:string)=>{if(name==='vue')return vue;throw new Error('Unexpected template dependency: '+name)},templateExports)
Object.assign(WorldWritePanel,{render:templateExports.render})

type HostNode={tag:string;tagName:string;text:string;value:any;children:HostNode[];parent:HostNode|null;props:Record<string,any>;options:HostNode[];addEventListener:EventTarget['addEventListener'];removeEventListener:EventTarget['removeEventListener'];[key:string]:any}
function node(tag:string,text=''):HostNode{const events=new EventTarget();return {tag,tagName:tag.toUpperCase(),text,value:'',children:[],parent:null,props:{},get options(){return this.children.filter(n=>n.tag==='option')},addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events)}}
const renderer=createRenderer<HostNode,HostNode>({
  createElement:tag=>node(tag),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
  setText:(n,text)=>{n.text=text},setElementText:(n,text)=>{n.text=text;n.children=[]},patchProp:(n,key,_old,value)=>{n.props[key]=value;if(key==='value'){n.value=value;n._value=value}},
  insert:(n,parent,anchor)=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=parent;const i=anchor?parent.children.indexOf(anchor):-1;if(i<0)parent.children.push(n);else parent.children.splice(i,0,n)},
  remove:n=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=null},
  parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null
})
const text=(n:HostNode):string=>(n.tag==='#comment'?'':n.text)+n.children.map(text).join('')
function find(n:HostNode,predicate:(n:HostNode)=>boolean):HostNode|undefined{if(predicate(n))return n;for(const child of n.children){const found=find(child,predicate);if(found)return found}}
const flush=async()=>{for(let i=0;i<24;i++)await Promise.resolve()}
const deferred=<T>()=>{let resolve!:(value:T)=>void,reject!:(error:Error)=>void;const promise=new Promise<T>((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
function candidate(id='turn-a',title='最新完整章节'):WorldTurn{return {id,taskId:'write-task',lineId:'line-a',status:'candidate',parentCheckpointId:'head',checkpointId:null,action:'',draftText:'',warnings:[],acceptedCount:0,chapters:[{index:1,title,text:'这段完整正文来自较新的读取。',changes:[],options:[]}]}}
let app:ReturnType<typeof renderer.createApp>,root:HostNode,props:{turnId:string|undefined},pending:ReturnType<typeof deferred<WorldTurn>>[]
function mount(){root=node('root');props=reactive({turnId:'turn-a' as string|undefined});app=renderer.createApp(defineComponent({setup:()=>()=>h(WorldWritePanel,{turnId:props.turnId})}));app.provide(ssrContextKey,{modules:new Set()});app.mount(root)}
beforeEach(()=>{
  vi.useFakeTimers();vi.stubGlobal('document',{activeElement:null});fixture.epoch=0
  fixture.store=reactive({world:{id:'world-a'},line:{id:'line-a',headCheckpointId:'head'},task:{state:'succeeded',persistenceState:'saved',result:{chapterIndex:1}},running:false,recovering:false})
  pending=[];vi.mocked(worldApi).mockReset().mockImplementation(()=>{const next=deferred<WorldTurn>();pending.push(next);return next.promise as any})
})
afterEach(()=>{app?.unmount();vi.useRealTimers();vi.unstubAllGlobals()})
async function nextRead(){fixture.store.task.result.chapterIndex++;await flush();expect(pending).toHaveLength(2)}

it('keeps the newer rendered chapter and accept control when an older same-turn success arrives last',async()=>{
  mount();await flush();await nextRead();pending[1].resolve(candidate());await flush()
  expect(text(root)).toContain('最新完整章节');expect(find(root,n=>n.tag==='button'&&text(n)==='保存并继续游玩')).toBeDefined()
  pending[0].resolve({...candidate(),status:'writing',chapters:[]});await flush()
  expect(text(root)).toContain('最新完整章节');expect(find(root,n=>n.tag==='button'&&text(n)==='保存并继续游玩')).toBeDefined()
  expect(vi.mocked(worldApi).mock.calls.every(([,options])=>!options?.method)).toBe(true)
})
it('ignores an older same-turn error after a newer chapter has rendered',async()=>{
  mount();await flush();await nextRead();pending[1].resolve(candidate());await flush();pending[0].reject(new Error('旧请求临时断开'));await flush()
  expect(text(root)).toContain('最新完整章节');expect(find(root,n=>n.props.role==='alert')).toBeUndefined()
})
it('loads a changed turn prop and ignores the old turn response after the new turn is displayed',async()=>{
  mount();await flush();props.turnId='turn-b';await flush();expect(pending).toHaveLength(2)
  expect(vi.mocked(worldApi).mock.calls.map(([path])=>path)).toEqual(['/worlds/world-a/turns/turn-a','/worlds/world-a/turns/turn-b'])
  pending[1].resolve(candidate('turn-b','另一次写作'));await flush();pending[0].resolve(candidate('turn-a','上一候选'));await flush()
  expect(text(root)).toContain('另一次写作');expect(text(root)).not.toContain('上一候选')
})
it('keeps a new empty writing form when a removed turn prop has an outstanding response',async()=>{
  mount();await flush();props.turnId=undefined;await flush();pending[0].resolve(candidate('turn-a','上一候选'));await flush()
  expect(text(root)).toContain('接下来，写几章？');expect(text(root)).not.toContain('上一候选');expect(find(root,n=>n.tag==='button'&&text(n)==='保存并继续游玩')).toBeUndefined()
  expect(vi.mocked(worldApi)).toHaveBeenCalledTimes(1)
})
it('discards an old world success after the world identity changes without another turn request',async()=>{
  mount();await flush();fixture.store.world.id='world-b';pending[0].resolve(candidate('turn-a','旧世界章节'));await flush()
  expect(text(root)).not.toContain('旧世界章节');expect(find(root,n=>n.tag==='button'&&text(n)==='保存并继续游玩')).toBeUndefined()
})
it('does not show an old world error after the world identity changes',async()=>{
  mount();await flush();fixture.store.world.id='world-b';pending[0].reject(new Error('旧世界错误'));await flush()
  expect(find(root,n=>n.props.role==='alert')).toBeUndefined()
})
