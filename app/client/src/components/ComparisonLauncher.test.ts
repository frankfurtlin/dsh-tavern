import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import ComparisonLauncher from './ComparisonLauncher.vue'
import {api,ApiError} from '../api'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const state=vi.hoisted(()=>({owner:'owner-a',epoch:0,route:null as any,store:null as any,values:new Map<string,unknown>()}))
vi.mock('vue-router',()=>({useRoute:()=>state.route}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>state.epoch,newKey:()=> 'same-original-request',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({accountId:()=>state.owner,cacheGet:async(key:string)=>state.values.get(state.owner+':'+key),cacheSet:async(key:string,value:unknown)=>{state.values.set(state.owner+':'+key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('../stores/app',()=>({useApp:()=>state.store}))
vi.mock('./Sheet.vue',async()=>{const{defineComponent,h}=await import('vue');return{default:defineComponent({props:{title:String},emits:['close'],setup:(props,{emit,slots})=>()=>h('section',{role:'dialog','aria-label':props.title},[h('button',{'aria-label':'关闭',onClick:()=>emit('close')},'关闭'),slots.default?.()])})}})
compileRender(ComparisonLauncher,new URL('./ComparisonLauncher.vue',import.meta.url))
const targets=[{id:'a',name:'同名',nodeId:'cp-a'},{id:'b',name:'同名',nodeId:'cp-b'},{id:'c',name:'第三线',nodeId:'cp-c'}]
const capabilities={protocol:'reader-compare-v1',ready:true,revealReady:true,alignments:{book:['latest'],world:['latest','same-time']},maxPageSize:20}
function result(right='b',seen=false){return{protocol:'reader-compare-v1',storyId:'world-w',kind:'world',alignment:'latest',atMinute:null,common:{nodeId:'start',visible:true,title:'共同起点'},left:{lineId:'a',name:'同名',nodeId:'cp-a',requestedNodeId:'cp-a',observedNodeId:'start',viewVersion:0,hasUnseen:false,items:[{id:'start',title:'开场',text:'已读公开正文'}],nextOffset:null,state:{}},right:{lineId:right,name:'同名',nodeId:'cp-'+right,requestedNodeId:'cp-'+right,observedNodeId:seen?'cp-'+right:'start',viewVersion:seen?1:0,hasUnseen:!seen,items:[{id:'start',title:'开场',text:seen?'明确查看后的正文':'右侧已读公开正文'}],nextOffset:null,state:{}},differences:[],differencesTruncated:false}}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,parent:{width:number;targets:typeof targets}
function mount(){parent=reactive({width:1440,targets:targets.map(target=>({...target}))});root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h('div',{'data-viewport-width':parent.width},[h(ComparisonLauncher,{storyId:'world-w',kind:'world',resourceId:'w',targets:parent.targets.map(target=>({...target})),currentLineId:'a'})])}));provideSfc(app);app.mount(root)}
function click(label:string){const button=descendants(root,item=>item.tag==='button'&&(item.props['aria-label']===label||text(item)===label))[0];expect(button).toBeDefined();expect(button.props.disabled).toBeFalsy();return button.props.onClick()}
beforeEach(()=>{state.owner='owner-a';state.epoch=0;state.route=reactive({fullPath:'/worlds/w/lines'});state.store=reactive({online:true,user:{id:state.owner}});state.values.clear();vi.mocked(api).mockReset().mockImplementation(async(path)=>path==='/comparison/capabilities'?capabilities as any:result() as any)})
afterEach(()=>{app?.unmount();app=undefined})
it('does not expose a comparison action or POST when the server is not ready',async()=>{vi.mocked(api).mockRejectedValue(new ApiError('RESOURCE_NOT_FOUND','not installed',404));mount();await flush();expect(text(root)).not.toContain('比较路径');expect(api).toHaveBeenCalledExactlyOnceWith('/comparison/capabilities')})
it('only loads the selected authorized IDs with GET and waits for explicit unseen confirmation',async()=>{mount();await flush();click('比较路径');await flush();expect(text(root)).toContain('已读公开正文');expect(vi.mocked(api).mock.calls.every(([,options])=>!options?.method)).toBe(true);click('查看这条线后续');await flush();const submit=descendants(root,n=>n.tag==='button'&&text(n)==='确认查看')[0];expect(submit.props.disabled).toBe(true);expect(vi.mocked(api).mock.calls.every(([,options])=>options?.method!=='POST')).toBe(true)})
it('keeps comparison open when the parent resizes and recreates equivalent path targets',async()=>{
 mount();await flush();click('比较路径');await flush();vi.mocked(api).mockClear()
 for(const width of [320,1440]){parent.width=width;await flush();expect(descendants(root,n=>n.props.role==='dialog')).toHaveLength(1);expect(text(root)).toContain('已读公开正文')}
 expect(api).not.toHaveBeenCalled()
})
it('closes comparison when the parent changes the selected checkpoint',async()=>{
 mount();await flush();click('比较路径');await flush();parent.targets[0]={...parent.targets[0]!,nodeId:'cp-a-new'};await flush()
 expect(descendants(root,n=>n.props.role==='dialog')).toHaveLength(0);expect(text(root)).not.toContain('已读公开正文')
})
it('persists an unknown reveal and reopens with original pure GET lookup rather than another POST',async()=>{
 let committed=false;vi.mocked(api).mockImplementation(async(path,options)=>{if(path==='/comparison/capabilities')return capabilities as any;if(options?.method==='POST'){committed=true;throw new ApiError('NETWORK_UNAVAILABLE','response lost',0)}if(path.includes('/reveal/requests'))return{found:true,result:{protocol:'reader-compare-v1',kind:'world',resourceId:'w',lineId:'b',nodeId:'cp-b',version:1,anchor:{checkpointId:'cp-b'}}} as any;return result('b',committed) as any})
 mount();await flush();click('比较路径');await flush();click('查看这条线后续');await flush();const checkbox=descendants(root,n=>n.tag==='input'&&n.props.type==='checkbox')[0];checkbox.props['onUpdate:modelValue'](true);await flush();click('确认查看');await flush();expect(text(root)).toContain('查询结果');expect(state.values.get('owner-a:comparison-reveal:world-w:world:w')).toMatchObject({requestId:'same-original-request',lineId:'b',nodeId:'cp-b',expectedVersion:0});click('关闭');await flush();click('比较路径');await flush();expect(text(root)).toContain('明确查看后的正文');expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1);expect(api).toHaveBeenCalledWith('/stories/world-w/compare/reveal/requests?requestId=same-original-request');expect(state.values.get('owner-a:comparison-reveal:world-w:world:w')).toBeNull()
})
it('ignores a late comparison after close or account/route change',async()=>{
 const late=deferred<any>();vi.mocked(api).mockImplementation(async(path)=>path==='/comparison/capabilities'?capabilities:late.promise);mount();await flush();click('比较路径');await flush();click('关闭');await flush();late.resolve(result());await flush();expect(text(root)).not.toContain('已读公开正文');expect(descendants(root,n=>n.props.role==='dialog')).toHaveLength(0)
 const next=deferred<any>();vi.mocked(api).mockImplementation(async(path)=>path==='/comparison/capabilities'?capabilities:next.promise);click('比较路径');await flush();state.epoch++;state.owner='owner-b';state.store.user.id='owner-b';state.route.fullPath='/worlds/other/lines';await flush();next.resolve(result());await flush();expect(text(root)).not.toContain('已读公开正文');expect(descendants(root,n=>n.props.role==='dialog')).toHaveLength(0)
})
it('reveals only the actual time-aligned node using that node view version',async()=>{
 vi.mocked(api).mockImplementation(async(path,options)=>{if(path==='/comparison/capabilities')return capabilities as any;if(options?.method==='POST')return{protocol:'reader-compare-v1',kind:'world',resourceId:'w',lineId:'b',nodeId:'cp-b-time',version:8,anchor:{checkpointId:'cp-b-time'}} as any;const value=result();if(path.includes('alignment=same-time')){value.alignment='same-time';value.atMinute=0 as any;value.right.nodeId='cp-b-time';value.right.viewVersion=7}return value as any})
 mount();await flush();click('比较路径');await flush();const selects=descendants(root,n=>n.tag==='select');selects[2].props['onUpdate:modelValue']('same-time');selects[2].props.onChange();await flush();click('查看这条线后续');await flush();const checkbox=descendants(root,n=>n.tag==='input'&&n.props.type==='checkbox')[0];checkbox.props['onUpdate:modelValue'](true);await flush();click('确认查看');await flush();const post=vi.mocked(api).mock.calls.find(([,options])=>options?.method==='POST');expect(post?.[1]?.body).toMatchObject({nodeId:'cp-b-time',expectedVersion:7});expect(post?.[1]?.body).not.toMatchObject({nodeId:'cp-b'})
})
