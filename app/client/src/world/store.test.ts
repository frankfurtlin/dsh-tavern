import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useWorld} from './store'
import {worldApi,worldEvents} from './api'
import {ApiError} from '../api'
import {cacheSet,cacheUpdate} from '../platform/bridge'
import type {WorldTask} from './types'
const storage=vi.hoisted(()=>new Map<string,unknown>())
let epoch=0
vi.mock('../api',()=>({ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}},authGeneration:()=>epoch,newKey:()=> 'one-receipt'}))
vi.mock('./api',()=>({worldApi:vi.fn(),worldEvents:vi.fn(async()=>{})}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async(key:string)=>storage.get(key)||null),cacheSet:vi.fn(async()=>{}),cacheUpdate:vi.fn(async(key:string,update:(old:unknown)=>unknown,valid:()=>boolean)=>{if(!valid())return;const value=update(storage.get(key)||null);storage.set(key,value);return value})}))
beforeEach(()=>{epoch=0;storage.clear();vi.mocked(cacheSet).mockReset().mockResolvedValue(undefined);setActivePinia(createPinia());vi.stubGlobal('window',new EventTarget());vi.clearAllMocks()})
afterEach(()=>{vi.unstubAllGlobals()})
function ready(){const store=useWorld();store.world={id:'w'} as any;store.line={id:'l',headCheckpointId:'cp',pendingTaskId:null} as any;store.input='观察渡口';return store}
it('releases a definitely rejected quota request and permits a different action',async()=>{
  const store=ready(),api=vi.mocked(worldApi)
  api.mockRejectedValueOnce(new ApiError('BUDGET_NOT_AUTHORIZED','额度不足',403))
  await store.act('购买药材')
  expect(storage.get('world-pending:w:l')).toBeNull()
  expect(store.input).toBe('观察渡口')
  api.mockImplementation(async(path,options)=>options?.method==='POST'?{taskId:'new-task'} as any:path.startsWith('/tasks/')?{id:'new-task',state:'succeeded',eventSeq:1,result:{},stream:{}} as any:path.endsWith('/history')||path.endsWith('/drafts')?{items:[]} as any:{id:'l',headCheckpointId:'cp2',pendingTaskId:null} as any)
  await store.act('回到街道');expect(api.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(2);store.stopWatching()
})
it('observes the existing task when the action response was lost',async()=>{
  const store=ready(),api=vi.mocked(worldApi)
  api.mockImplementation(async(path,options)=>{
    if(options?.method==='POST')throw new Error('connection lost after commit')
    if(path.includes('/actions/'))return {taskId:'same-task'} as any
    if(path==='/tasks/same-task')return {id:'same-task',state:'succeeded',eventSeq:4,result:{},stream:{}} as any
    if(path.endsWith('/history')||path.endsWith('/drafts'))return {items:[]} as any
    return {id:'l',headCheckpointId:'cp',pendingTaskId:'same-task'} as any
  })
  await store.act();await vi.waitFor(()=>expect(api).toHaveBeenCalledWith('/tasks/same-task'))
  expect(api.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(1)
  await vi.waitFor(()=>expect(storage.get('world-pending:w:l')).toBeNull())
  expect(store.input).toBe('');store.stopWatching()
})
it('keeps the next draft typed while the previous submission is being confirmed',async()=>{
  const store=ready(),api=vi.mocked(worldApi)
  let finish!:(value:any)=>void
  api.mockImplementation(async(path,options)=>options?.method==='POST'?new Promise(resolve=>finish=resolve):({id:'task',state:'failed',result:{},stream:{},eventSeq:0,items:[]} as any))
  const request=store.act();await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));store.input='我还想询问梅舟';finish({taskId:'task'});await request
  expect(store.input).toBe('我还想询问梅舟');store.stopWatching()
})

it('clears only the confirmed lost travel receipt so a different next action can run',async()=>{
  const store=ready(),api=vi.mocked(worldApi);let posts=0
  api.mockImplementation(async(path,options)=>{
    if(options?.method==='POST'){posts++;if(posts===1)throw new Error('ACK lost');return {taskId:'next-task'} as any}
    if(path.includes('/actions/'))return {taskId:'saved-task'} as any
    if(path.startsWith('/tasks/'))return {id:path.split('/').at(-1),state:'succeeded',eventSeq:4,result:{},stream:{}} as any
    if(path.endsWith('/history')||path.endsWith('/drafts'))return {items:[]} as any
    return {id:'l',headCheckpointId:'new-cp',pendingTaskId:null} as any
  })
  await store.act('走出客舍','action',undefined,'exit:door')
  await vi.waitFor(()=>expect(storage.get('world-pending:w:l')).toBeNull())
  await vi.waitFor(()=>expect(store.running).toBe(false))
  store.input='观察庭院';await store.act()
  expect(posts).toBe(2)
  expect(api.mock.calls.filter(([,o])=>o?.method==='POST')[0][0]).toBe('/worlds/w/lines/l/scene/travel')
  expect(store.error).toBe('');store.stopWatching()
})
it('discards a response from a previous account generation',async()=>{
  const store=ready(),api=vi.mocked(worldApi);let finish!:(value:any)=>void
  api.mockImplementation(async()=>new Promise(resolve=>finish=resolve))
  const request=store.act();await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));epoch++;store.reset();finish({taskId:'old-task'});await request
  expect(store.task).toBeNull();expect(store.world).toBeNull();expect(api).toHaveBeenCalledTimes(1)
})
it('does not write an old action receipt or POST after account changes during draft caching',async()=>{
  const store=ready(),cache=vi.mocked(cacheSet)
  cache.mockImplementationOnce(async()=>{epoch++;store.reset()})
  await store.act()
  expect(cache.mock.calls.some(([key])=>key.startsWith('world-pending:'))).toBe(false)
  expect(worldApi).not.toHaveBeenCalled()
})

it('does not attach the old task after navigation during post-submit draft saving',async()=>{
  const store=ready(),api=vi.mocked(worldApi),cache=vi.mocked(cacheSet)
  api.mockResolvedValue({taskId:'old-task'} as any)
  cache.mockImplementation(async()=>{}).mockImplementationOnce(async()=>{}).mockImplementationOnce(async()=>{store.stopWatching();store.world={id:'world-b'} as any;store.line={id:'line-b'} as any})
  await store.act()
  expect(store.task).toBeNull();expect(store.world?.id).toBe('world-b');expect(api).toHaveBeenCalledTimes(1)
})

const flush=async()=>{for(let i=0;i<16;i++)await Promise.resolve()}
function snapshot(state:string,persistenceState:string,text='已经收到的正文'):WorldTask{return {id:'recovering-task',type:'world_turn',state,persistenceState,phase:state==='failed'?'interrupted':'writing',errorCode:state==='failed'?'PERSISTENCE_UNAVAILABLE':state==='cancelled'?'TASK_CANCELLED':null,result:{worldId:'w',lineId:'l'},eventSeq:1,providerState:'received',stream:{text}}}
describe('recoverable world task observation',()=>{
  beforeEach(()=>{vi.useFakeTimers();vi.mocked(cacheSet).mockReset().mockResolvedValue(undefined);vi.mocked(worldApi).mockReset()})
  afterEach(()=>{useWorld().stopWatching();vi.useRealTimers()})
  it('keeps the same action, streamed text, next draft and stop affordance through recovery to a saved head',async()=>{
    const store=ready(),api=vi.mocked(worldApi),states=[snapshot('writing','pending'),snapshot('failed','recoverable'),snapshot('preparing','recoverable',''),snapshot('succeeded','saved')]
    api.mockImplementation(async(path,options)=>{
      if(options?.method==='POST')return {taskId:'recovering-task'} as any
      if(path==='/tasks/recovering-task')return states.shift() as any
      if(path.endsWith('/history'))return {items:[{id:'saved-head',text:'已经收到的正文'}]} as any
      if(path.endsWith('/drafts'))return {items:[]} as any
      return {id:'l',headCheckpointId:'saved-head',pendingTaskId:null} as any
    })
    await store.act();await flush();store.input='下一步我想问梅舟'
    await vi.advanceTimersByTimeAsync(900)
    expect(store.running).toBe(true);expect(store.task?.id).toBe('recovering-task');expect(store.task?.stream.text).toBe('已经收到的正文');expect(store.error).toBe('')
    await store.act();expect(api.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(900)
    expect(store.running).toBe(true);expect(store.task?.stream.text).toBe('已经收到的正文');expect(store.input).toBe('下一步我想问梅舟')
    await vi.advanceTimersByTimeAsync(900)
    expect(store.running).toBe(false);expect(store.task?.state).toBe('succeeded');expect(store.line?.headCheckpointId).toBe('saved-head');expect(store.history[0].text).toBe('已经收到的正文');expect(store.input).toBe('下一步我想问梅舟')
    expect(api.mock.calls.filter(([p])=>p==='/tasks/recovering-task')).toHaveLength(4);expect(api.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(1)
    expect((vi.mocked(worldEvents).mock.calls.at(-1)?.[2] as AbortSignal).aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(5000);expect(api.mock.calls.filter(([p])=>p==='/tasks/recovering-task')).toHaveLength(4)
  })
  it('can cancel the existing recoverable task and waits for its cancelled snapshot without resending',async()=>{
    const store=ready(),api=vi.mocked(worldApi);let cancelled=false
    api.mockImplementation(async(path,options)=>{
      if(path==='/tasks/recovering-task/cancel'&&options?.method==='POST'){cancelled=true;return {} as any}
      if(path==='/tasks/recovering-task')return snapshot(cancelled?'cancelled':'failed',cancelled?'saved':'recoverable') as any
      if(path.endsWith('/drafts'))return {items:[{id:'saved-draft',draftText:'已经收到的正文'}]} as any
      if(path.endsWith('/history'))return {items:[]} as any
      return {id:'l',headCheckpointId:'cp',pendingTaskId:null} as any
    })
    await store.watchTask('recovering-task');expect(store.running).toBe(true);expect(store.task?.stream.text).toBe('已经收到的正文')
    await store.cancel();expect(store.running).toBe(true);await vi.advanceTimersByTimeAsync(900)
    expect(store.running).toBe(false);expect(store.task?.state).toBe('cancelled');expect(store.drafts[0].draftText).toBe('已经收到的正文');expect(store.input).toBe('观察渡口');expect(store.error).toContain('已停止')
    expect(api.mock.calls.filter(([,o])=>o?.method==='POST').map(([p])=>p)).toEqual(['/tasks/recovering-task/cancel'])
    await vi.advanceTimersByTimeAsync(5000);expect(api.mock.calls.filter(([p])=>p==='/tasks/recovering-task')).toHaveLength(2)
  })
  it('ends a genuine saved failure, reloads its draft and does not automatically retry',async()=>{
    const store=ready(),api=vi.mocked(worldApi)
    api.mockImplementation(async(path)=>{
      if(path==='/tasks/recovering-task')return {...snapshot('failed','saved'),errorCode:'WORLD_EXECUTION_FAILED'} as any
      if(path.endsWith('/drafts'))return {items:[{id:'failed-draft',draftText:'已经收到的正文'}]} as any
      if(path.endsWith('/history'))return {items:[]} as any
      return {id:'l',headCheckpointId:'cp',pendingTaskId:null} as any
    })
    await store.watchTask('recovering-task');expect(store.running).toBe(false);expect(store.drafts[0].draftText).toBe('已经收到的正文');expect(store.input).toBe('观察渡口');expect(store.error).toContain('这次未完成')
    await vi.advanceTimersByTimeAsync(5000);expect(api.mock.calls.filter(([p])=>p==='/tasks/recovering-task')).toHaveLength(1);expect(api.mock.calls.some(([,o])=>o?.method==='POST')).toBe(false)
  })
  it('ignores an in-flight recovery snapshot after the account is cleared and schedules no further requests',async()=>{
    const store=ready(),api=vi.mocked(worldApi);let resolveOld!:(value:WorldTask)=>void,reads=0
    api.mockImplementation(async(path)=>{if(path==='/tasks/recovering-task'){if(++reads===1)return snapshot('failed','recoverable') as any;return new Promise(resolve=>{resolveOld=resolve})}throw new Error('old account must not reload the head')})
    await store.watchTask('recovering-task');await vi.advanceTimersByTimeAsync(900);expect(reads).toBe(2)
    epoch++;window.dispatchEvent(new Event('reader-private-cleared'));resolveOld(snapshot('succeeded','saved'));await flush();await vi.advanceTimersByTimeAsync(5000)
    expect(store.world).toBeNull();expect(store.task).toBeNull();expect(store.input).toBe('');expect(reads).toBe(2);expect(api).toHaveBeenCalledTimes(2)
  })
  it('does not let the old recovery request replace a newly loaded world or poll its task after navigation',async()=>{
    const store=ready(),api=vi.mocked(worldApi);let resolveOld!:(value:WorldTask)=>void,oldReads=0,newReads=0
    api.mockImplementation(async(path)=>{
      if(path==='/tasks/recovering-task'){if(++oldReads===1)return snapshot('failed','recoverable') as any;return new Promise(resolve=>{resolveOld=resolve})}
      if(path==='/worlds/next-world')return {id:'next-world',activeLineId:'next-line'} as any
      if(path==='/tasks/next-task'){newReads++;return {...snapshot('writing','pending','新世界正在展开'),id:'next-task'} as any}
      if(path.startsWith('/worlds/next-world/lines/next-line'))return path.endsWith('/history')||path.endsWith('/drafts')?{items:[]} as any:{id:'next-line',headCheckpointId:'next-head',pendingTaskId:'next-task'} as any
      throw new Error('old world must not reload the head')
    })
    await store.watchTask('recovering-task');await vi.advanceTimersByTimeAsync(900);expect(oldReads).toBe(2)
    await store.load('next-world','next-line');await flush();store.input='新世界下一草稿';resolveOld(snapshot('succeeded','saved'));await flush();await vi.advanceTimersByTimeAsync(1800)
    expect(store.world?.id).toBe('next-world');expect(store.line?.id).toBe('next-line');expect(store.task?.id).toBe('next-task');expect(store.task?.stream.text).toBe('新世界正在展开');expect(store.input).toBe('新世界下一草稿');expect(oldReads).toBe(2);expect(newReads).toBe(3)
    expect(api.mock.calls.some(([,o])=>o?.method==='POST')).toBe(false)
  })
})
