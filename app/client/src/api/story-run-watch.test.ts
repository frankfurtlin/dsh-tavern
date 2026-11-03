import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {watchStoryRun} from './story-run-watch'
import type {StoryRun} from './types'

const mock=vi.hoisted(()=>({api:vi.fn(),auth:0,foreground:true}))
vi.mock('./index',()=>({api:mock.api,authGeneration:()=>mock.auth}))
vi.mock('../platform/bridge',()=>({appForeground:()=>mock.foreground}))
const deferred=<T>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return{promise,resolve}}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()}
const run={id:'run',state:'preparing',requestedChapters:3,completedChapters:1} as StoryRun
let hidden=false,documentEvents:EventTarget,windowEvents:EventTarget,stop:(()=>void)|undefined
beforeEach(()=>{
  vi.useFakeTimers();mock.api.mockReset();mock.auth=0;mock.foreground=true;hidden=false
  documentEvents=new EventTarget();Object.defineProperty(documentEvents,'hidden',{get:()=>hidden});windowEvents=new EventTarget()
  vi.stubGlobal('document',documentEvents);vi.stubGlobal('window',windowEvents)
})
afterEach(()=>{stop?.();stop=undefined;vi.useRealTimers();vi.unstubAllGlobals()})
describe('durable writing progress',()=>{
  it('refreshes progress without SSE and keeps retrying a failed result render without creating work',async()=>{
    const render=vi.fn().mockRejectedValueOnce(new Error('local result cache failed')).mockResolvedValue(undefined),errors=vi.fn()
    mock.api.mockResolvedValue({...run,state:'succeeded'})
    stop=watchStoryRun(run.id,render,errors);await flush();expect(errors).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1500);expect(mock.api).toHaveBeenCalledTimes(2);expect(render).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(5000);expect(mock.api).toHaveBeenCalledTimes(2)
    expect(mock.api.mock.calls.every(call=>call[0]==='/story-runs/run'&&call.length===1)).toBe(true)
  })
  it('ignores a late result while hidden and resumes only a fresh foreground snapshot',async()=>{
    const old=deferred<StoryRun>(),callback=vi.fn();mock.api.mockReturnValueOnce(old.promise).mockResolvedValue(run)
    stop=watchStoryRun(run.id,callback,vi.fn());mock.foreground=false;windowEvents.dispatchEvent(new Event('reader-save'))
    old.resolve(run);await flush();await vi.advanceTimersByTimeAsync(3000);expect(callback).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledTimes(1)
    mock.foreground=true;windowEvents.dispatchEvent(new Event('reader-resume'));await flush();expect(callback).toHaveBeenCalledTimes(1);expect(mock.api).toHaveBeenCalledTimes(2)
  })
  it('drops another account completion and cancels all callbacks and refreshes after disposal',async()=>{
    const old=deferred<StoryRun>(),callback=vi.fn();mock.api.mockReturnValue(old.promise)
    stop=watchStoryRun(run.id,callback,vi.fn());mock.auth++;old.resolve(run);await flush();await vi.advanceTimersByTimeAsync(5000)
    expect(callback).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledTimes(1)
    stop();windowEvents.dispatchEvent(new Event('reader-resume'));expect(mock.api).toHaveBeenCalledTimes(1)
  })
  it('invalidates async consumer work that began before backgrounding, and gives resumed work a fresh guard',async()=>{
    const gate=deferred<void>(),writes:string[]=[],errors=vi.fn()
    mock.api.mockResolvedValueOnce(run).mockResolvedValue({...run,state:'succeeded'})
    stop=watchStoryRun(run.id,async(value,current)=>{if(value.state==='preparing')await gate.promise;if(current())writes.push(value.state)},errors)
    await flush();mock.foreground=false;windowEvents.dispatchEvent(new Event('reader-save'))
    mock.foreground=true;windowEvents.dispatchEvent(new Event('reader-resume'));await flush()
    gate.resolve();await flush();expect(writes).toEqual(['succeeded']);expect(errors).not.toHaveBeenCalled()
  })
})
