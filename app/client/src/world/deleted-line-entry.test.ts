import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useWorld} from './store'
import {worldApi} from './api'
import {cacheGet,cacheSet} from '../platform/bridge'
import {ApiError} from '../api'
let epoch=0
vi.mock('../api',()=>({authGeneration:()=>epoch,newKey:()=> 'unused',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('./api',()=>({worldApi:vi.fn(),worldEvents:vi.fn(async()=>{})}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>null),cacheSet:vi.fn(async()=>{})}))
beforeEach(()=>{epoch=0;setActivePinia(createPinia());vi.stubGlobal('window',new EventTarget());vi.mocked(worldApi).mockReset();vi.mocked(cacheGet).mockReset().mockResolvedValue(null);vi.mocked(cacheSet).mockReset().mockResolvedValue(undefined)})
afterEach(()=>{useWorld().stopWatching();vi.unstubAllGlobals()})
const info=(activeLineId:string|null)=>({id:'w',activeLineId})
const deleted=()=>new ApiError('WORLD_LINE_DELETED','已删除',404)
function fixture(active='b'){
  const api=vi.mocked(worldApi)
  api.mockImplementation(async(path)=>{
    if(path==='/worlds/w')return info(active) as any
    if(path.startsWith('/worlds/w/lines/a'))throw deleted()
    if(path.endsWith('/history'))return {items:[{id:'new-head',text:'子线正文'}]} as any
    if(path.endsWith('/drafts'))return {items:[]} as any
    return {id:'b',worldId:'w',headCheckpointId:'new-head',pendingTaskId:null} as any
  });return api
}
it('opens the server-selected surviving line after an authoritative own-line deletion',async()=>{
  const api=fixture();vi.mocked(cacheGet).mockImplementation(async(key)=>key==='world-input:w:b'?'子线草稿':null as any)
  const store=useWorld();await store.load('w','a')
  expect(store.line?.id).toBe('b');expect(store.history[0]?.text).toBe('子线正文');expect(store.input).toBe('子线草稿');expect(store.error).toBe('')
  expect(api.mock.calls.filter(([p])=>p==='/worlds/w')).toHaveLength(2)
  expect(api.mock.calls.some(([,o])=>o?.method==='POST')).toBe(false)
})
it('clears cached deleted action controls, task and drafts when the last line is gone',async()=>{
  fixture();vi.mocked(worldApi).mockImplementation(async(path)=>{if(path==='/worlds/w')return info(null) as any;throw deleted()})
  const store=useWorld();store.world=info('a') as any;store.line={id:'a'} as any;store.history=[{id:'old'}] as any;store.drafts=[{id:'old-draft'}] as any;store.task={id:'old-task'} as any;store.input='已删除线草稿'
  await store.load('w','a')
  expect(store.line).toBeNull();expect(store.history).toEqual([]);expect(store.drafts).toEqual([]);expect(store.task).toBeNull();expect(store.input).toBe('');expect(store.error).toBe('')
})
for(const code of ['RESOURCE_NOT_FOUND','NETWORK_UNAVAILABLE','WORLD_ARCHIVED'])it('does not use deletion fallback for '+code,async()=>{
  const api=fixture();api.mockImplementation(async(path)=>{if(path==='/worlds/w')return info('b') as any;throw new ApiError(code,'不应被吞掉',code==='NETWORK_UNAVAILABLE'?0:404)})
  const store=useWorld();await store.load('w','a')
  expect(store.line).toBeNull();expect(store.error).toBe('不应被吞掉');expect(api.mock.calls.some(([p])=>p.startsWith('/worlds/w/lines/b'))).toBe(false)
})
it('ignores a deletion recovery response after the account generation changes',async()=>{
  let finish!:(value:any)=>void,reads=0;const api=fixture();api.mockImplementation(async(path)=>{if(path==='/worlds/w'){if(++reads===1)return info('b') as any;return new Promise(resolve=>finish=resolve)}throw deleted()})
  const store=useWorld(),loading=store.load('w','a');await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));epoch++;store.reset();finish(info('b'));await loading
  expect(store.world).toBeNull();expect(store.line).toBeNull();expect(api.mock.calls.some(([p])=>p.includes('/lines/b'))).toBe(false)
})
it('never resurrects a known deleted cached line when the authoritative entry is offline',async()=>{
  const api=fixture();vi.mocked(cacheGet).mockImplementation(async(key)=>key==='world-line-deleted:w:a'?true as any:null)
  let reads=0;api.mockImplementation(async(path)=>{if(path==='/worlds/w'){if(++reads===1)return info('b') as any;throw new ApiError('NETWORK_UNAVAILABLE','正在离线',0)}return {id:'a',pendingTaskId:null} as any})
  const store=useWorld();store.world=info('a') as any;store.line={id:'a'} as any;store.input='旧稿';await store.load('w','a')
  expect(store.line).toBeNull();expect(store.input).toBe('');expect(store.error).toBe('正在离线');expect(api.mock.calls.some(([p])=>p.includes('/lines/a'))).toBe(false)
})
it('does not read a replacement or write a receipt after a deletion marker switches the account',async()=>{
  const api=fixture(),store=useWorld();vi.mocked(cacheSet).mockImplementation(async()=>{epoch++;store.reset()})
  await store.load('w','a');expect(store.world).toBeNull();expect(store.line).toBeNull();expect(api.mock.calls.filter(([p])=>p==='/worlds/w')).toHaveLength(1)
  expect(api.mock.calls.some(([p])=>p.includes('/lines/b'))).toBe(false)
})

function midloadFixture(stage:'history'|'drafts',error:Error=deleted()){
  let worlds=0
  const api=vi.mocked(worldApi)
  api.mockImplementation(async(path)=>{
    if(path==='/worlds/w')return info(++worlds===1?'a':'b') as any
    if(path==='/worlds/w/lines/a')return {id:'a',worldId:'w',headCheckpointId:'a-head',pendingTaskId:null} as any
    if(path==='/worlds/w/lines/a/'+stage)throw error
    if(path.endsWith('/history'))return {items:[{id:path.includes('/lines/b/')?'b-head':'a-head',text:path.includes('/lines/b/')?'保留线正文':'已删除线正文'}]} as any
    if(path.endsWith('/drafts'))return {items:[{id:path.includes('/lines/b/')?'b-draft':'a-draft',draftText:'保留正文',chapters:[]}]} as any
    if(path==='/worlds/w/lines/b')return {id:'b',worldId:'w',headCheckpointId:'b-head',pendingTaskId:null} as any
    throw new Error('Unexpected synthetic path: '+path)
  })
  return api
}
it.each(['history','drafts'] as const)('recovers one fresh retained line after own deletion occurs during %s loading',async(stage)=>{
  const api=midloadFixture(stage)
  vi.mocked(cacheGet).mockImplementation(async(key)=>key==='world-input:w:b'?'保留线自己的草稿':null as any)
  const store=useWorld();await store.load('w','a')
  expect(store.world?.activeLineId).toBe('b');expect(store.line?.id).toBe('b');expect(store.history).toEqual([{id:'b-head',text:'保留线正文'}]);expect(store.drafts).toEqual([{id:'b-draft',draftText:'保留正文',chapters:[]}]);expect(store.input).toBe('保留线自己的草稿');expect(store.error).toBe('')
  expect(vi.mocked(cacheSet)).toHaveBeenCalledWith('world-line-deleted:w:a',true)
  const worlds=api.mock.calls.filter(([path])=>path==='/worlds/w');expect(worlds).toHaveLength(2);expect(worlds[1]![1]?.cache).not.toBe(true)
  expect(api.mock.calls.filter(([path])=>path==='/worlds/w/lines/b')).toHaveLength(1)
  expect(api.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false)
})
it.each([['RESOURCE_NOT_FOUND',404],['NETWORK_UNAVAILABLE',0],['WORLD_LINE_DELETED',403]] as const)('preserves a non-authorizing %s/%s error from history without deletion recovery',async(code,status)=>{
  const api=midloadFixture('history',new ApiError(code,'原始中途读取错误',status)),store=useWorld()
  await store.load('w','a')
  expect(store.error).toBe('原始中途读取错误');expect(vi.mocked(cacheSet)).not.toHaveBeenCalled()
  expect(api.mock.calls.filter(([path])=>path==='/worlds/w')).toHaveLength(1)
  expect(api.mock.calls.some(([path])=>path.includes('/lines/b'))).toBe(false)
})
it('ignores a history deletion received after the account changes',async()=>{
  let reject!:(error:Error)=>void;const api=midloadFixture('history'),base=api.getMockImplementation()!
  api.mockImplementation((path,options)=>path==='/worlds/w/lines/a/history'?new Promise((_resolve,rejectPromise)=>{reject=rejectPromise}):base(path,options))
  const store=useWorld(),loading=store.load('w','a');await vi.waitFor(()=>expect(reject).toBeTypeOf('function'))
  epoch++;store.reset();reject(deleted());await loading
  expect(store.world).toBeNull();expect(store.line).toBeNull();expect(store.history).toEqual([]);expect(store.error).toBe('');expect(vi.mocked(cacheSet)).not.toHaveBeenCalled()
  expect(api.mock.calls.filter(([path])=>path==='/worlds/w')).toHaveLength(1);expect(api.mock.calls.some(([path])=>path.includes('/lines/b'))).toBe(false)
})
it('records a second deletion during retained drafts loading and stops without another fallback loop',async()=>{
  const api=midloadFixture('history'),base=api.getMockImplementation()!
  api.mockImplementation((path,options)=>path==='/worlds/w/lines/b/drafts'?Promise.reject(deleted()):base(path,options))
  const store=useWorld();await store.load('w','a')
  expect(vi.mocked(cacheSet)).toHaveBeenCalledWith('world-line-deleted:w:a',true);expect(vi.mocked(cacheSet)).toHaveBeenCalledWith('world-line-deleted:w:b',true)
  expect(store.line).toBeNull();expect(store.history).toEqual([]);expect(store.drafts).toEqual([]);expect(store.input).toBe('');expect(store.error).toBe('已删除')
  expect(api.mock.calls.filter(([path])=>path==='/worlds/w')).toHaveLength(2);expect(api.mock.calls.filter(([path])=>path==='/worlds/w/lines/b')).toHaveLength(1)
  expect(api.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false)
})
