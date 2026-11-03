import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {watchTask} from './task-watch'
import type {Task} from './types'

const mock=vi.hoisted(()=>({api:vi.fn(),refresh:vi.fn(),auth:0}))
vi.mock('./index',()=>({api:mock.api,refreshAuth:mock.refresh,authorizationHeaders:()=>({}),authGeneration:()=>mock.auth}))
vi.mock('../platform/bridge',()=>({backendBase:()=>''}))
const deferred=<T>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return{promise,resolve}}
const task={id:'persisted-task',state:'running',eventCursor:1,persistenceState:'saved',result:{}} as unknown as Task
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()}
let hidden=false,documentEvents:EventTarget,windowEvents:EventTarget,fetchMock:ReturnType<typeof vi.fn>,stop:(()=>void)|undefined
beforeEach(()=>{
  vi.useFakeTimers();mock.api.mockReset();mock.refresh.mockReset();mock.auth=0;hidden=false
  documentEvents=new EventTarget();Object.defineProperty(documentEvents,'hidden',{get:()=>hidden});windowEvents=new EventTarget()
  vi.stubGlobal('document',documentEvents);vi.stubGlobal('window',windowEvents);fetchMock=vi.fn();vi.stubGlobal('fetch',fetchMock)
})
afterEach(()=>{stop?.();stop=undefined;vi.useRealTimers();vi.unstubAllGlobals()})
function visibility(value:boolean){hidden=value;documentEvents.dispatchEvent(new Event('visibilitychange'))}
describe('task connection lifecycle',()=>{
  it('ignores an old snapshot returning while hidden and opens only the resumed connection',async()=>{
    const old=deferred<Task>(),fresh=deferred<Task>(),taskCallback=vi.fn(),errors=vi.fn()
    mock.api.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);fetchMock.mockReturnValue(new Promise(()=>{}))
    stop=watchTask(task.id,taskCallback,errors);visibility(true);old.resolve(task);await flush()
    expect(fetchMock).not.toHaveBeenCalled();expect(taskCallback).not.toHaveBeenCalled()
    visibility(false);fresh.resolve(task);await flush();expect(fetchMock).toHaveBeenCalledTimes(1);expect(taskCallback).toHaveBeenCalledTimes(1);expect(errors).not.toHaveBeenCalled()
  })
  it('does not let an obsolete fetch completion replace or reschedule the resumed connection',async()=>{
    const old=deferred<Response>(),current=deferred<Response>(),errors=vi.fn()
    mock.api.mockResolvedValue(task);fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    stop=watchTask(task.id,vi.fn(),errors);await flush();const firstSignal=fetchMock.mock.calls[0][1].signal as AbortSignal
    visibility(true);visibility(false);await flush();const currentSignal=fetchMock.mock.calls[1][1].signal as AbortSignal
    expect(firstSignal.aborted).toBe(true);old.resolve(new Response('',{status:503}));await flush();await vi.advanceTimersByTimeAsync(3000)
    expect(fetchMock).toHaveBeenCalledTimes(2);expect(currentSignal.aborted).toBe(false);expect(errors).not.toHaveBeenCalled();stop();expect(currentSignal.aborted).toBe(true)
  })
  it('does not retry an SSE after refresh completes in the background or deliver another account snapshot',async()=>{
    const refreshed=deferred<boolean>(),snapshot=deferred<Task>(),callback=vi.fn()
    mock.api.mockResolvedValueOnce(task).mockReturnValueOnce(snapshot.promise);mock.refresh.mockReturnValue(refreshed.promise);fetchMock.mockResolvedValue(new Response('',{status:401}))
    stop=watchTask(task.id,callback,vi.fn());await flush();expect(mock.refresh).toHaveBeenCalledTimes(1)
    visibility(true);refreshed.resolve(true);await flush();expect(fetchMock).toHaveBeenCalledTimes(1)
    visibility(false);mock.auth++;snapshot.resolve(task);await flush();expect(callback).toHaveBeenCalledTimes(1);expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
