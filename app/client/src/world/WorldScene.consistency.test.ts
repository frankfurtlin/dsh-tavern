import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createRenderer,reactive,ssrContextKey} from 'vue'
import WorldScene from './WorldScene.vue'
import {worldMutation,pendingWorldMutation} from './mutation-intent'
const f=vi.hoisted(()=>({store:null as any,viewers:[] as any[],requests:[] as any[],api:vi.fn()}))
vi.mock('./store',()=>({useWorld:()=>f.store}))
vi.mock('../stores/app',()=>({useApp:()=>({online:true})}))
vi.mock('./api',()=>({worldApi:f.api}))
vi.mock('../api',()=>({authorizationHeaders:()=>({}),newKey:()=> 'fixture'}))
vi.mock('../platform/bridge',()=>({backendBase:()=>'',cacheGet:async()=>null,cacheSet:async()=>{}}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>true})}))
vi.mock('./mutation-intent',()=>({pendingWorldMutation:vi.fn(async()=>null),clearWorldMutation:async()=>{},worldMutation:vi.fn()}))
vi.mock('@photo-sphere-viewer/markers-plugin',()=>({MarkersPlugin:class {}}))
vi.mock('@photo-sphere-viewer/core',()=>({Viewer:class {
  listeners:Record<string,Function>={};destroyed=false;plugin={clearMarkers:vi.fn(),addMarker:vi.fn(),updateMarker:vi.fn()}
  state={size:{width:1600,height:1000}}
  dataHelper={sphericalCoordsToVector3:(p:any)=>p,vector3ToViewerCoords:(p:any)=>({x:this.state.size.width/2+p.yaw*1000,y:400})}
  constructor(public options:unknown){f.viewers.push(this)}getPlugin(){return this.plugin}
  addEventListener(name:string,fn:Function){this.listeners[name]=fn}destroy(){this.destroyed=true}
  ready(){this.listeners.ready?.()}getPosition(){return {yaw:0,pitch:0}}getZoomLevel(){return 35}
  setPanorama=vi.fn(async()=>true);rotate=vi.fn();zoom=vi.fn()
}}))
const deferred=()=>{let resolve!:(v:any)=>void;const promise=new Promise<any>(r=>resolve=r);return {promise,resolve}}
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()}
const scene=(id:string)=>({id,status:'ready',imageUrl:'/image/'+id,sceneHash:id,descriptor:{placeName:'客舍'},characters:[],items:[],points:[],exits:[],layout:{nodes:[],links:[]},characterPresentation:{},conversations:[]})
const node=(tag:string)=>({tag,children:[] as any[],parent:null as any,text:'',props:{} as any})
const renderer=createRenderer<any,any>({createElement:node,createText:t=>({...node('text'),text:t}),createComment:node,
 setText:(n,t)=>n.text=t,setElementText:(n,t)=>n.text=t,patchProp:(n,k,_o,v)=>n.props[k]=v,
 insert:(n,p)=>{n.parent=p;p.children.push(n)},remove:()=>{},parentNode:n=>n.parent,nextSibling:()=>null})
let app:any,setup:any
beforeEach(async()=>{
 vi.mocked(worldMutation).mockReset();vi.mocked(pendingWorldMutation).mockReset().mockResolvedValue(undefined)
 f.viewers=[];f.requests=[];f.api.mockReset().mockImplementation(async(p:string)=>p==='/scene-profile'?{enabled:true}:scene('A'))
 f.store=reactive({world:{id:'w',status:'active'},line:{id:'l',status:'active',head:{id:'cp',text:'',state:{}}},drafts:[],task:null,running:false,busy:false,input:'',error:''})
 vi.stubGlobal('fetch',vi.fn(()=>{const d=deferred();f.requests.push(d);return d.promise}))
 vi.stubGlobal('window',{matchMedia:()=>({matches:false})});vi.stubGlobal('URL',{createObjectURL:()=> 'blob:fixture',revokeObjectURL:vi.fn()})
 app=renderer.createApp({...WorldScene,render:()=>null});app.provide(ssrContextKey,{modules:new Set()});app.mount(node('root'))
 setup=app._instance.setupState;setup.canvas={dataset:{}};await flush()
})
afterEach(()=>{app.unmount();vi.unstubAllGlobals()})
it('an old ready callback cannot show image A with the corrected image B hotspots',async()=>{
 f.requests[0].resolve({ok:true,blob:async()=>new Blob(['A'])});await flush()
 expect(f.viewers).toHaveLength(1);const a=f.viewers[0]
 setup.acceptScene(scene('B'));const loading=setup.display(scene('B'),()=>true);await flush()
 expect(a.destroyed).toBe(true);a.ready();await flush();expect(setup.viewReady).toBe(false)
 expect(a.plugin.addMarker).not.toHaveBeenCalled()
 f.requests[1].resolve({ok:true,blob:async()=>new Blob(['B'])});await loading;await flush()
 const b=f.viewers[1];b.ready();await flush();expect(setup.viewReady).toBe(true)
 a.ready();expect(setup.viewReady).toBe(true)
})
it('a replaced image response is discarded before creating its viewer',async()=>{
 setup.acceptScene(scene('B'));const loading=setup.display(scene('B'),()=>true);await flush()
 f.requests[0].resolve({ok:true,blob:async()=>new Blob(['A'])});await flush();expect(f.viewers).toHaveLength(0)
 f.requests[1].resolve({ok:true,blob:async()=>new Blob(['B'])});await loading;expect(f.viewers).toHaveLength(1)
})
it('recovery submits one durable image-only intent and ignores its result after a checkpoint change',async()=>{
 const d=deferred();vi.mocked(worldMutation).mockReturnValue(d.promise)
 setup.acceptScene({...scene('saved'),status:'unknown',recovery:{stage:'inspection',imageRetained:true,canInspect:true}})
 setup.loading=false
 const first=setup.inspectReceived();await flush();await setup.inspectReceived()
 expect(worldMutation).toHaveBeenCalledTimes(1)
 expect(vi.mocked(worldMutation).mock.calls[0].slice(0,3)).toEqual(['scene-inspect:l','/worlds/w/lines/l/scene/inspect',{sceneId:'saved',expectedCheckpointId:'cp',independent:true}])
 f.store.line.head.id='new-cp';await flush()
 d.resolve({...scene('old-recovery'),status:'queued'});await first;await flush()
 expect(setup.scene.id).not.toBe('old-recovery')
})
it('an uncertain recovery uses the saved payload instead of creating a second image request',async()=>{
 const body={sceneId:'saved-before-refresh',expectedCheckpointId:'cp',independent:true}
 vi.mocked(pendingWorldMutation).mockResolvedValue({key:'same-intent',path:'/worlds/w/lines/l/scene/inspect',body})
 vi.mocked(worldMutation).mockRejectedValue(new Error('connection lost'))
 setup.loading=false
 await setup.inspectReceived();await flush()
 expect(setup.pendingInspection).toBe(true)
 expect(vi.mocked(worldMutation).mock.calls[0][2]).toEqual(body)
 expect(f.api.mock.calls.some(c=>c[1]?.body?.regenerate===true)).toBe(false)
})
it('a completed multiline speech keeps its saved speaker channel in the main scene',async()=>{
 const speech='先从这顶帽子入手。\n\n至于蓝宝石，我们分头调查。'
 f.store.line.head.text='福尔摩斯说：“'+speech+'”'
 const s={...scene('A'),responses:[{id:'cp',text:f.store.line.head.text,segments:[{kind:'speech',text:speech,speakerId:'holmes',speakerName:'福尔摩斯'}]}]}
 setup.acceptScene(s);await flush()
 expect(setup.narrativeSegments).toEqual(s.responses[0].segments)
})
it('a long name flips outside the measured face using its actual width and follows viewport resizing',async()=>{
 const button={classList:{add:vi.fn(),toggle:vi.fn()},dataset:{},addEventListener:vi.fn(),setAttribute:vi.fn(),offsetWidth:190}
 vi.stubGlobal('document',{createElement:()=>button})
 f.requests[0].resolve({ok:true,blob:async()=>new Blob(['A'])});await flush()
 const viewer=f.viewers[0];viewer.state.size.width=900
 setup.acceptScene({...scene('A'),characters:[{id:'h',name:'歇洛克·福尔摩斯',type:'character'}],
  characterPresentation:{h:{mode:'embedded',yaw:.37,pitch:.2,labelYaw:.4,labelLeftYaw:.34,labelPitch:.15}}})
 viewer.ready();await flush()
 expect(viewer.plugin.updateMarker).toHaveBeenLastCalledWith({id:'person-h',position:{yaw:.34,pitch:.15},anchor:'center right',size:{width:190,height:44}})
 const left=790-190-14,right=left+190
 expect(left).toBeGreaterThan(8);expect(right).toBeLessThan(790)
 viewer.state.size.width=1800;viewer.listeners['size-updated']()
 expect(viewer.plugin.updateMarker).toHaveBeenLastCalledWith({id:'person-h',position:{yaw:.4,pitch:.15},anchor:'center left',size:{width:190,height:44}})
})
it('a reviewed environment is visible read-only, then upgrades on the same viewer before enabling actions',async()=>{
 const preview={...scene('A'),status:'queued',imageUrl:null,preview:{url:'/preview/A',sourceHash:'source-A',revision:'preview-A',phase:'environment',readOnly:true,width:1024,height:512}}
 setup.acceptScene(preview)
 const first=setup.display(preview,()=>true);await flush()
 f.requests[1].resolve({ok:true,blob:async()=>new Blob(['preview'])});await first;await flush()
 const viewer=f.viewers[0];viewer.ready();await flush()
 expect(setup.viewReady).toBe(true);expect(setup.interactionsReady).toBe(false)
 f.store.act=vi.fn();f.store.input='观察四周';setup.send();expect(f.store.act).not.toHaveBeenCalled()
 const complete={...preview,status:'ready',imageUrl:'/image/A-final',preview:undefined,image:{sha256:'final-hash'}}
 setup.acceptScene(complete)
 const upgrade=setup.display(complete,()=>true);await flush()
 expect(setup.interactionsReady).toBe(false)
 f.requests[2].resolve({ok:true,blob:async()=>new Blob(['final'])});await upgrade;await flush()
 expect(f.viewers).toHaveLength(1);expect(viewer.setPanorama).toHaveBeenCalledTimes(1)
 expect(setup.viewReady).toBe(true);expect(setup.interactionsReady).toBe(true)
 setup.send();expect(f.store.act).toHaveBeenCalledTimes(1)
 f.requests[0].resolve({ok:true,blob:async()=>new Blob(['late-original'])});await flush()
 expect(f.viewers).toHaveLength(1)
})
it('keeps the last rendered texture with interactions disabled when the final image download fails',async()=>{
 f.requests[0].resolve({ok:true,blob:async()=>new Blob(['old'])});await flush();f.viewers[0].ready();await flush()
 const latest={...scene('A'),imageUrl:'/image/new',image:{sha256:'new-version'}}
 setup.acceptScene(latest);const upgrade=setup.display(latest,()=>true);await flush()
 f.requests[1].resolve({ok:false});await upgrade;await flush()
 expect(setup.viewReady).toBe(true);expect(setup.interactionsReady).toBe(false)
 expect(setup.mediaError).toContain('已保留原图')
 expect(f.viewers[0].destroyed).toBe(false)
})
it('withdraws a contradicted preview immediately even while a different safe fallback is downloading',async()=>{
 const preview={...scene('A'),status:'queued',imageUrl:null,preview:{url:'/preview/A',sourceHash:'unsafe',revision:'unsafe:preview1024-v1',phase:'environment',readOnly:true,width:1024,height:512}}
 setup.acceptScene(preview);const first=setup.display(preview,()=>true);await flush()
 f.requests[1].resolve({ok:true,blob:async()=>new Blob(['preview'])});await first;await flush()
 const viewer=f.viewers[0];viewer.ready();await flush();expect(setup.viewReady).toBe(true)
 const withdrawn={...preview,invalidPreviewHashes:['unsafe'],preview:{...preview.preview,url:'/preview/safe',sourceHash:'safe',revision:'safe:preview1024-v1'}}
 setup.acceptScene(withdrawn)
 expect(viewer.destroyed).toBe(true);expect(setup.viewReady).toBe(false);expect(setup.interactionsReady).toBe(false)
 const fallback=setup.display(withdrawn,()=>true);await flush()
 expect(setup.viewReady).toBe(false)
 f.requests[2].resolve({ok:true,blob:async()=>new Blob(['safe'])});await fallback;await flush()
 f.viewers[1].ready();await flush();expect(setup.viewReady).toBe(true);expect(setup.interactionsReady).toBe(false)
 expect(worldMutation).not.toHaveBeenCalled();expect(f.store.line.head.id).toBe('cp')
})
it('keeps a ready image read-only while its binding proof is pending, then enables the same pixels without another fetch',async()=>{
 const pending={...scene('A'),image:{sha256:'same-hash',anchorStatus:'queued'}}
 setup.acceptScene(pending);const draw=setup.display(pending,()=>true);await flush()
 f.requests[1].resolve({ok:true,blob:async()=>new Blob(['same-pixels'])});await draw;f.viewers[0].ready();await flush()
 expect(setup.viewReady).toBe(true);expect(setup.interactionsReady).toBe(false)
 const complete={...pending,image:{...pending.image,anchorStatus:'complete'}}
 setup.acceptScene(complete);await setup.display(complete,()=>true);await flush()
 expect(setup.interactionsReady).toBe(true);expect(f.requests).toHaveLength(2)
})

it('blocks a new visual recovery during supplier cooldown and enables only a manual retry after expiry',async()=>{
 const until=Date.now()+6512_000
 setup.acceptScene({...scene('saved'),status:'failed',recovery:{stage:'inspection',imageRetained:true,canInspect:true,httpStatus:429,retryAt:new Date(until).toISOString()}})
 setup.loading=false;await flush()
 expect(setup.cooldownRemaining).toBeGreaterThan(6500)
 await setup.inspectReceived();expect(worldMutation).not.toHaveBeenCalled()
 setup.progressClock=until+1000;await flush()
 expect(setup.cooldownRemaining).toBe(0)
 expect(worldMutation).not.toHaveBeenCalled()
 vi.mocked(worldMutation).mockResolvedValue({...scene('saved'),status:'queued'})
 await setup.inspectReceived();await flush()
 expect(worldMutation).toHaveBeenCalledTimes(1)
 expect(vi.mocked(worldMutation).mock.calls[0][2]).toEqual({sceneId:'saved',expectedCheckpointId:'cp',independent:true})
 expect(f.store.line.head.id).toBe('cp')
})
