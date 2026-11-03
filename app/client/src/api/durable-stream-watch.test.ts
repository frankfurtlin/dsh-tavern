import {it,expect,vi,beforeEach,afterEach} from 'vitest'
import {watchTask} from './task-watch'
import type {Task} from './types'
const mock=vi.hoisted(()=>({api:vi.fn(),auth:0}))
vi.mock('./index',()=>({api:mock.api,refreshAuth:vi.fn(),authGeneration:()=>mock.auth,authorizationHeaders:()=>({})}))
vi.mock('../platform/bridge',()=>({backendBase:()=>''}))
let stop:(()=>void)|undefined
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()}
const task={id:'task',state:'running',persistenceState:'saving',eventCursor:1,result:{}} as Task
beforeEach(()=>{vi.useFakeTimers();mock.api.mockReset();mock.auth=0;vi.stubGlobal('document',Object.assign(new EventTarget(),{hidden:false}));vi.stubGlobal('window',new EventTarget());mock.api.mockResolvedValue(task)})
afterEach(()=>{stop?.();vi.useRealTimers();vi.unstubAllGlobals()})
function frames(seq:number){return new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: '+JSON.stringify({taskId:'task',seq,type:'text_delta',payload:{contentId:'content',chapterIndex:1,streamVersion:1,startUtf16:0,delta:'😀',durability:'journaled'}})+'\n\n'));c.close()}}))}
it('delivers a contiguous typed event without fetching an entire task again',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(frames(2)))
  const delta=vi.fn(()=>true),onTask=vi.fn()
  stop=(watchTask as unknown as (id:string,cb:(t:Task)=>void,err:(e:Error)=>void,event:(e:unknown)=>boolean)=>(()=>void))('task',onTask,vi.fn(),delta)
  await flush();expect(delta).toHaveBeenCalledOnce();expect(mock.api).toHaveBeenCalledOnce();expect(onTask).toHaveBeenCalledOnce()
})
it('a sequence gap fetches a snapshot without delivering the untrusted fragment',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(frames(4)))
  const delta=vi.fn(()=>true)
  stop=(watchTask as unknown as (id:string,cb:(t:Task)=>void,err:(e:Error)=>void,event:(e:unknown)=>boolean)=>(()=>void))('task',vi.fn(),vi.fn(),delta)
  await flush();expect(delta).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledTimes(2)
})
