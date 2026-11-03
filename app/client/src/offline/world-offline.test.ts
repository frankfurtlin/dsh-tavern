import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {webcrypto} from 'node:crypto'
import {worldApi} from '../world/api'

// Legacy public-history consistency cases do not emulate the new image service.
vi.mock('../api/assets',()=>({downloadImages:async()=>undefined}))
vi.mock('../api/offline-images',()=>({publicImages:(value:unknown)=>value,validateOfflineImages:async()=>{}}))
const fixture=vi.hoisted(()=>({native:false,values:new Map<string,unknown>(),files:new Map<string,string>(),pause:undefined as undefined|((path:string)=>Promise<void>),failIndex:false}))
vi.mock('../world/api',()=>({worldApi:vi.fn()}))
vi.mock('@capacitor/core',()=>({Capacitor:{isNativePlatform:()=>fixture.native,getPlatform:()=>fixture.native?'android':'web'},registerPlugin:()=>({})}))
vi.mock('@capacitor/app',()=>({App:{}}));vi.mock('@capacitor/network',()=>({Network:{}}));vi.mock('@capacitor/keyboard',()=>({Keyboard:{}}))
vi.mock('@capawesome/capacitor-file-picker',()=>({FilePicker:{}}));vi.mock('capacitor-secure-storage-plugin',()=>({SecureStoragePlugin:{}}))
vi.mock('@capacitor/filesystem',()=>({Directory:{Data:'DATA'},Encoding:{UTF8:'utf8'},Filesystem:{
  readFile:async({path}:{path:string})=>{if(!fixture.files.has(path))throw new Error('ENOENT');return {data:fixture.files.get(path)}},
  writeFile:async({path,data}:{path:string;data:string})=>{if(fixture.pause)await fixture.pause(path);fixture.files.set(path,data)},
  deleteFile:async({path}:{path:string})=>{fixture.files.delete(path)},rmdir:async()=>{}
}}))
vi.mock('idb',()=>({openDB:async()=>({
  get:async(_store:string,key:string)=>structuredClone(fixture.values.get(key)),put:async(_store:string,value:unknown,key:string)=>{fixture.values.set(key,structuredClone(value))},delete:async(_store:string,key:string)=>{fixture.values.delete(key)},getAllKeys:async()=>[...fixture.values.keys()],
  transaction:()=>{const working=new Map(fixture.values);let aborted=false;return {store:{get:async(key:string)=>structuredClone(working.get(key)),put:async(value:unknown,key:string)=>{if(fixture.failIndex&&key==='owner-a:world-offline-index:v1')throw new Error('storage full');working.set(key,structuredClone(value))},getAllKeys:async()=>[...working.keys()]},abort:()=>{aborted=true},get done(){if(aborted)return Promise.reject(new Error('aborted'));fixture.values.clear();for(const [k,v] of working)fixture.values.set(k,v);return Promise.resolve()}}}
})}))
const local=new Map<string,string>()
beforeEach(()=>{
  vi.resetModules();fixture.native=false;fixture.values.clear();fixture.files.clear();fixture.pause=undefined;fixture.failIndex=false;local.clear();vi.mocked(worldApi).mockReset()
  vi.stubGlobal('crypto',webcrypto);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{hidden:false});vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')))
  vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})
  vi.stubGlobal('localStorage',{getItem:(key:string)=>local.get(key)||null,setItem:(key:string,value:string)=>local.set(key,value),removeItem:(key:string)=>local.delete(key),key:(i:number)=>[...local.keys()][i]||null,get length(){return local.size}})
  vi.stubEnv('VITE_API_BASE_URL','');vi.stubEnv('VITE_NATIVE_ALLOWED_SERVERS','');vi.stubEnv('VITE_NATIVE_DEV_SERVER','')
})
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs()})
const deferred=<T>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve}}
async function modules(){const bridge=await import('../platform/bridge');bridge.setAccount('owner-a');return {bridge,storage:await import('./world-storage'),cache:await import('../platform/world-cache'),reader:await import('../api'),download:await import('./download-world')}}
function data(lineId='line-a'){
  const cp=(id:string,parent:string|null,sequence:number)=>({id,worldId:'world-a',lineId:sequence<2?'ancestor-line':lineId,parentCheckpointId:parent,sequence,title:'合成标题'+id,text:sequence?'潮水🌊与甲😀乙'+id:'',kind:sequence?'turn':'start',createdAt:'fixture',state:{goal:'SECRET-GOAL',GM:'SECRET-GM'},definition:{secret:'SECRET-DEFINITION'},changes:[{private:'SECRET-CHANGE'}],summary:'SECRET-SUMMARY',options:[]})
  const history=[cp('root',null,0),cp('one','root',1),cp('two','one',2)]
  const world={id:'world-a',name:'合成公开故事',status:'active',version:2,sourceRemoved:false,activeLineId:lineId,sourceBookId:'source-book',sourceAnchor:{secret:'SECRET-ANCHOR'},fullBookReference:'SECRET-BOOK',definition:{private:'SECRET-DEFINITION'},rules:{deathPolicy:'rescue'},prepareTaskId:'private-task'}
  const line={id:lineId,worldId:world.id,name:'合成线'+lineId,status:'archived',version:3,headCheckpointId:'two',head:history[2],readingPosition:{checkpointId:'one',scroll:21,updatedAt:'fixture'},pendingTaskId:'private-task',camera:{zoom:1}}
  return {world,line,history,origins:[]}
}
function provider(value=data()){
  vi.mocked(worldApi).mockImplementation(async path=>path.endsWith('/history')?{items:value.history,headCheckpointId:value.line.headCheckpointId}:path.includes('/lines/')?value.line:value.world as any)
}
it('downloads the whole shared ancestry at one exact head and stores only whitelisted public reading data',async()=>{
  const m=await modules(),source=data();provider(source)
  const entry=await m.download.downloadWorldReading('world-a','line-a','two','')
  const stored=await m.storage.readWorldPackage(entry.id)
  expect(stored?.payload.history.map(cp=>cp.id)).toEqual(['root','one','two']);expect(stored?.payload.history[1]?.text).toBe(source.history[1]!.text)
  expect(stored?.payload.history[1]?.lineId).toBe('ancestor-line');expect(stored?.payload.line.headCheckpointId).toBe('two');expect(entry.chapterCount).toBe(2)
  expect(JSON.stringify(stored)).not.toMatch(/SECRET|state|definition|rules|sourceBookId|sourceAnchor|fullBookReference|pendingTaskId|camera|"options"|"changes"|"summary"/)
  expect(stored?.payload.origins).toEqual(['world-world-a']);expect(await m.storage.worldEntries()).toEqual([entry]);expect(worldApi).toHaveBeenCalledTimes(5);expect(fetch).not.toHaveBeenCalled()
})
it.each(['history-head','late-line-head','missing-ancestor'])('never registers a package after a %s consistency failure',async(kind)=>{
  const m=await modules(),value=data();let reads=0
  vi.mocked(worldApi).mockImplementation(async path=>{
    if(path.endsWith('/history'))return {items:kind==='missing-ancestor'?value.history.slice(1):value.history,headCheckpointId:kind==='history-head'?'new-head':'two'} as any
    if(path.includes('/lines/'))return {...value.line,...(kind==='late-line-head'&&++reads===2?{headCheckpointId:'new-head'}:{})} as any
    return value.world as any
  })
  await expect(m.download.downloadWorldReading('world-a','line-a','two','')).rejects.toThrow()
  expect(await m.storage.worldEntries()).toEqual([]);expect([...fixture.values.keys()].some(key=>key.includes('world-offline-package:'))).toBe(false)
})
it.each(['owner','auth','cancel'])('does not continue a held download after %s changes',async(kind)=>{
  const m=await modules(),waiting=deferred<any>();let active=true;vi.mocked(worldApi).mockReturnValueOnce(waiting.promise)
  const request=m.download.downloadWorldReading('world-a','line-a','two','',()=>active),failed=expect(request).rejects.toThrow('已变化')
  if(kind==='owner')m.bridge.setAccount('owner-b');else if(kind==='auth')m.reader.beginAuthentication();else active=false
  waiting.resolve(data().world);await failed;expect(worldApi).toHaveBeenCalledTimes(1);expect(fixture.values.size).toBe(0)
})
it('does not publish an incomplete index and rejects changed bytes or extra fields even with a recomputed hash',async()=>{
  const m=await modules();fixture.failIndex=true;await expect(m.storage.commitWorldPackage(data(),()=>true)).rejects.toThrow('storage full');expect(await m.storage.worldEntries()).toEqual([])
  fixture.failIndex=false;const entry=await m.storage.commitWorldPackage(data(),()=>true),name='owner-a:'+m.storage.worldPackageKey(entry.id),stored=fixture.values.get(name) as any
  stored.payload.history[1].text='changed';expect(await m.storage.readWorldPackage(entry.id)).toBeUndefined()
  stored.payload.definition={private:'must not be served'};stored.sha256=await(await import('./storage')).digest(stored.payload);expect(await m.storage.readWorldPackage(entry.id)).toBeUndefined()
  expect(await m.storage.offlineWorldRead('/worlds/world-a/lines/line-a/history')).toBeUndefined()
})
it('reads a downloaded public history through the real world namespace but never a cached POST or a Play state',async()=>{
  const m=await modules(),entry=await m.storage.commitWorldPackage(data(),()=>true)
  const reply=await m.reader.api<any>('/worlds/world-a/lines/line-a/history',{namespace:'world',cache:true,readOnlyWorld:true})
  expect(reply.items.map((cp:any)=>cp.id)).toEqual(['root','one','two']);expect(reply.offlinePackageId).toBe(entry.id)
  await expect(m.reader.api('/worlds/world-a/lines/line-a',{namespace:'world',cache:true})).rejects.toMatchObject({code:'NETWORK_UNAVAILABLE'})
  await m.bridge.cacheSet('world-api:/worlds/world-a/lines/line-a/actions',{taskId:'old-receipt'})
  await expect(m.reader.api('/worlds/world-a/lines/line-a/actions',{namespace:'world',method:'POST',body:{message:'must not succeed'},cache:true,readOnlyWorld:true})).rejects.toMatchObject({code:'NETWORK_UNAVAILABLE'})
})
it('does not use partial visited caches as a substitute for an explicit public download',async()=>{
  const m=await modules();await m.bridge.cacheSet('world-api:/worlds/world-a',data().world)
  await expect(m.reader.api('/worlds/world-a',{namespace:'world',cache:true,readOnlyWorld:true})).rejects.toMatchObject({code:'NETWORK_UNAVAILABLE'})
  expect(await m.reader.api<any>('/worlds/world-a',{namespace:'world',cache:true})).toMatchObject({id:'world-a'})
})
it('retains the independent world package when its source book is deleted',async()=>{
  const m=await modules(),entry=await m.storage.commitWorldPackage(data(),()=>true),book=await import('../platform/book-cache')
  await m.bridge.cacheSet('api:/books/source-book',{id:'source-book',originalBranchId:'source-line'});book.markBookDeleted('source-book',[]);await m.bridge.purgeBookCache('source-book',()=>true)
  expect((await m.storage.readWorldPackage(entry.id))?.payload.history[1]?.text).toContain('甲😀乙');expect(await m.storage.worldEntries()).toHaveLength(1)
})
it('rejects a late JSON body after authoritative line deletion and retains descendant shared ancestry',async()=>{
  const m=await modules(),source=data(),first=await m.storage.commitWorldPackage(source,()=>true),next=data('line-b')
  next.history.push({...source.history[2]!,id:'three',lineId:'line-b',parentCheckpointId:'two',sequence:3});next.line.headCheckpointId='three';next.line.head=next.history[3]!
  const second=await m.storage.commitWorldPackage(next,()=>true);await m.bridge.cacheSet('world-api:/worlds/world-a/lines/line-a/history',{items:source.history});await m.bridge.cacheSet('world-api:/worlds/world-a/checkpoints/two',source.history[2])
  const held=deferred<unknown>();vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:()=>held.promise} as Response)
  const request=m.reader.api('/worlds/world-a/lines/line-a',{namespace:'world',cache:true}),failed=expect(request).rejects.toMatchObject({code:'WORLD_LINE_DELETED'})
  await vi.waitFor(()=>expect(fetch).toHaveBeenCalled());await m.cache.invalidateWorldCache('world-a','line-a');held.resolve(source.line);await failed
  expect(await m.storage.readWorldPackage(first.id)).toBeUndefined();expect((await m.storage.readWorldPackage(second.id))?.payload.history.map(cp=>cp.id)).toEqual(['root','one','two','three'])
  expect(await m.bridge.cacheGet('world-api:/worlds/world-a/lines/line-a/history')).toBeUndefined();expect(await m.bridge.cacheGet('world-api:/worlds/world-a/checkpoints/two')).toMatchObject({id:'two'})
})
it('filters a late shared graph response without restoring the deleted line or removing retained ancestor nodes',async()=>{
  const m=await modules(),source=data(),descendant={...source.history[2]!,id:'three',lineId:'line-b',parentCheckpointId:'two',sequence:3},exclusive={...source.history[2]!,id:'deleted-only',parentCheckpointId:'one',lineId:'line-a'}
  const graph={lines:[{...source.line},{...source.line,id:'line-b',headCheckpointId:'three'}],nodes:[...source.history,exclusive,descendant]}
  const held=deferred<unknown>();vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:()=>held.promise} as Response)
  const reading=m.reader.api<any>('/worlds/world-a/graph',{namespace:'world',cache:true});await vi.waitFor(()=>expect(fetch).toHaveBeenCalled());await m.cache.invalidateWorldCache('world-a','line-a');held.resolve(graph)
  const result=await reading;expect(result.lines.map((line:any)=>line.id)).toEqual(['line-b']);expect(result.nodes.map((cp:any)=>cp.id)).toEqual(['root','one','two','three'])
  expect(await m.bridge.cacheGet('world-api:/worlds/world-a/graph')).toEqual(result)
})
it.each(['delete-success','missing-world','committed-cleanup-pending'])('persists and purges a %s world boundary without resurrecting normal caches',async(kind)=>{
  const m=await modules(),entry=await m.storage.commitWorldPackage(data(),()=>true)
  await m.bridge.cacheSet('world-api:/worlds/world-a',data().world);await m.bridge.cacheSet('world-input:world-a:line-a','old input');await m.bridge.cacheSet('api:/books/other-book',{id:'other-book',originalBranchId:'other-line'})
  const body=kind==='delete-success'?{deleted:true}:kind==='missing-world'?{code:'RESOURCE_NOT_FOUND',message:'missing'}:{code:'WORLD_CLEANUP_PENDING',message:'cleanup pending',details:{worldId:'world-a',deleted:true,cleanupPending:true}}
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(body),{status:kind==='delete-success'?200:kind==='missing-world'?404:503}))
  const request=m.reader.api('/worlds/world-a',{namespace:'world',method:kind==='missing-world'?'GET':'DELETE'})
  if(kind==='delete-success')await expect(request).resolves.toEqual({deleted:true});else await expect(request).rejects.toMatchObject({status:kind==='missing-world'?404:503})
  expect(m.cache.isWorldDeleted('world-a')).toBe(true);expect(await m.storage.readWorldPackage(entry.id)).toBeUndefined();expect(await m.storage.worldEntries()).toEqual([])
  expect(fixture.values.has('owner-a:world-api:/worlds/world-a')).toBe(false);expect(fixture.values.has('owner-a:world-input:world-a:line-a')).toBe(false);expect(await m.bridge.cacheGet('api:/books/other-book')).toMatchObject({id:'other-book'})
  vi.mocked(fetch).mockResolvedValue(new Response('{"deleted":true}',{status:200}));await expect(m.reader.api('/worlds/world-a',{namespace:'world',method:'DELETE'})).resolves.toEqual({deleted:true})
})
it('honors a persisted other-tab tombstone after reload, and scopes it to its owner and backend',async()=>{
  const m=await modules(),entry=await m.storage.commitWorldPackage(data(),()=>true);let seen=''
  window.addEventListener('reader-world-deleted',event=>{seen=(event as CustomEvent).detail.worldId})
  m.cache.markWorldDeleted('world-a');const name=[...local.keys()].find(key=>key.startsWith('reader-world-deleted:'))!;const event=new Event('storage');Object.defineProperty(event,'key',{value:name});window.dispatchEvent(event);expect(seen).toBe('world-a')
  vi.resetModules();const next=await modules();expect(next.cache.isWorldDeleted('world-a')).toBe(true);expect(await next.storage.readWorldPackage(entry.id)).toBeUndefined();next.bridge.setAccount('owner-b');expect(next.cache.isWorldDeleted('world-a')).toBe(false)
  vi.stubEnv('VITE_API_BASE_URL','https://other.example');vi.resetModules();const other=await modules();expect(other.cache.isWorldDeleted('world-a')).toBe(false);expect(await other.storage.readWorldPackage(entry.id)).toBeUndefined()
})
it('removes an actual hashed native late write after deletion without claiming physical-device execution',async()=>{
  fixture.native=true;vi.stubEnv('VITE_API_BASE_URL','https://fixture.example');vi.stubEnv('VITE_NATIVE_ALLOWED_SERVERS','https://fixture.example')
  const m=await modules(),entered=deferred<void>(),release=deferred<void>(),target='world-api:/worlds/world-a/lines/line-a/history'
  const bytes=await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(target)),file='cache/owner-a/'+Buffer.from(bytes).toString('hex')+'.json'
  fixture.pause=async path=>{if(path===file){entered.resolve();await release.promise}}
  const saving=m.bridge.cacheSet(target,{items:data().history}),failed=expect(saving).rejects.toMatchObject({code:'WORLD_DELETED'})
  await entered.promise;const deleting=m.cache.invalidateWorldCache('world-a','line-a');release.resolve();await failed;await deleting
  expect(fixture.files.has(file)).toBe(false);expect(await m.bridge.cacheGet(target)).toBeUndefined()
})
it('keeps an unvalidated origin out of the package and performs only pure validation plus world GETs',async()=>{
  const m=await modules();provider();vi.mocked(fetch).mockResolvedValue(new Response('{"code":"RESOURCE_NOT_FOUND","message":"denied"}',{status:404}))
  const entry=await m.download.downloadWorldReading('world-a','line-a','two','book-'+'a'.repeat(32))
  expect((await m.storage.readWorldPackage(entry.id))?.payload.origins).toEqual(['world-world-a']);expect(fetch).toHaveBeenCalledTimes(1);expect(vi.mocked(fetch).mock.calls[0]?.[1]).toMatchObject({method:'POST'})
  expect(vi.mocked(worldApi).mock.calls.every(([,options])=>!options?.method||options.method==='GET')).toBe(true)
})
