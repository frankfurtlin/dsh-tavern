import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {watchGeneration} from './generation-watch'
import {snapshot} from './generation-test-fixtures'
import type {GenerationSnapshot} from './generation-types'
const mock=vi.hoisted(()=>({api:vi.fn(),auth:0,owner:'owner',base:'https://fixture.invalid',foreground:true}))
vi.mock('./index',()=>({api:mock.api,authGeneration:()=>mock.auth,authorizationHeaders:()=>({Authorization:'fixture-token-not-recorded'}),refreshAuth:vi.fn()}))
vi.mock('../platform/bridge',()=>({backendBase:()=>mock.base,accountId:()=>mock.owner,appForeground:()=>mock.foreground}))
const deferred=<T>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return{promise,resolve}}
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve()}
let stop:(()=>void)|undefined,stream:ReadableStreamDefaultController<Uint8Array>,hidden=false
function send(value:unknown){stream.enqueue(new TextEncoder().encode('data: '+JSON.stringify(value)+'\r\n\r\n'))}
beforeEach(()=>{vi.useFakeTimers();mock.api.mockReset();mock.auth=0;mock.owner='owner';mock.base='https://fixture.invalid';mock.foreground=true;hidden=false
  const doc=new EventTarget();Object.defineProperty(doc,'hidden',{get:()=>hidden});vi.stubGlobal('document',doc);vi.stubGlobal('window',new EventTarget())
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(new ReadableStream({start(c){stream=c}}),{status:200})))
})
afterEach(()=>{stop?.();stop=undefined;vi.useRealTimers();vi.unstubAllGlobals()})
it('reads imported history without SSE, recovery timers or transport replay even when historical metadata says running',async()=>{
  mock.api.mockResolvedValue({...snapshot({state:'running',persistenceState:'recoverable',eventCursor:0}),importedHistory:true,readOnly:true});const values:GenerationSnapshot[]=[]
  stop=watchGeneration('batch',v=>{values.push(v)},vi.fn());await flush();await vi.advanceTimersByTimeAsync(10000)
  expect(values).toHaveLength(1);expect(values[0]).toMatchObject({importedHistory:true,readOnly:true});expect(fetch).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledTimes(1)
})
it('renders a real delta before terminal and ignores its duplicate without extra GET',async()=>{
  mock.api.mockResolvedValue(snapshot());const values:GenerationSnapshot[]=[];stop=watchGeneration('batch',v=>{values.push(v)},vi.fn());await flush()
  const d={taskId:'parent',seq:6,type:'text_delta',payload:{candidateVersion:1,contentId:'content-1',chapterIndex:1,streamVersion:1,startUtf16:3,delta:'乙',durability:'journaled'}}
  send(d);await flush();send(d);await flush();expect(values.at(-1)?.chapters[0].text).toBe('甲😀乙');expect(mock.api).toHaveBeenCalledTimes(1)
})
it('replaces a gap from an authoritative snapshot and resumes at the saved cursor',async()=>{
  mock.api.mockResolvedValueOnce(snapshot()).mockResolvedValueOnce(snapshot({eventCursor:8,chapters:[{...snapshot().chapters[0],text:'甲😀乙丙',snapshotVersion:2}]}));const values:GenerationSnapshot[]=[]
  stop=watchGeneration('batch',v=>{values.push(v)},vi.fn());await flush();send({taskId:'parent',seq:8,type:'text_delta',payload:{startUtf16:100,delta:'错误的跳字'}});await flush()
  expect(values.at(-1)?.chapters[0].text).toBe('甲😀乙丙');expect(mock.api.mock.calls.map(c=>c[0])).toEqual(['/generations/batch','/generations/batch'])
})
it('does not interpret SSE close as success and reconnects to the same task by GET only',async()=>{
  mock.api.mockResolvedValueOnce(snapshot()).mockResolvedValueOnce(snapshot({state:'succeeded',persistenceState:'saved'}));const values:GenerationSnapshot[]=[]
  stop=watchGeneration('batch',v=>{values.push(v)},vi.fn());await flush();stream.close();await flush();expect(values.at(-1)?.state).toBe('running')
  await vi.advanceTimersByTimeAsync(1200);expect(values.at(-1)?.state).toBe('succeeded');expect(mock.api.mock.calls.every(c=>c[0]==='/generations/batch'&&c.length===1)).toBe(true)
})
it('continues observing recoverable failure until the same task is saved',async()=>{
  mock.api.mockResolvedValueOnce(snapshot({state:'failed',persistenceState:'recoverable'})).mockResolvedValueOnce(snapshot({state:'cancelled',persistenceState:'saved',eventCursor:6}));const values:GenerationSnapshot[]=[]
  stop=watchGeneration('batch',v=>{values.push(v)},vi.fn());await flush();send({taskId:'parent',seq:6,type:'terminal',payload:{}});await flush()
  expect(values.map(v=>v.persistenceState)).toEqual(['recoverable','saved']);expect(values.at(-1)?.state).toBe('cancelled')
})
it.each(['account','backend','route','dispose'])('drops an old snapshot after %s changes without starting another request',async change=>{
  const pending=deferred<GenerationSnapshot>(),callback=vi.fn();mock.api.mockReturnValue(pending.promise);let route=true
  stop=watchGeneration('batch',callback,vi.fn(),()=>route)
  if(change==='account')mock.auth++;else if(change==='backend')mock.base='https://other.invalid';else if(change==='route')route=false;else stop()
  pending.resolve(snapshot());await flush();await vi.advanceTimersByTimeAsync(5000);expect(callback).not.toHaveBeenCalled();expect(mock.api).toHaveBeenCalledTimes(1);expect(fetch).not.toHaveBeenCalled()
})
it('invalidates awaited consumer work on background and resumes with a fresh guard',async()=>{
  const gate=deferred<void>(),rendered:string[]=[];mock.api.mockResolvedValueOnce(snapshot()).mockResolvedValueOnce(snapshot({state:'cancelled',persistenceState:'saved'}))
  stop=watchGeneration('batch',async(v,current)=>{if(v.state==='running')await gate.promise;if(current())rendered.push(v.state)},vi.fn());await flush()
  mock.foreground=false;window.dispatchEvent(new Event('reader-save'));mock.foreground=true;window.dispatchEvent(new Event('reader-resume'));await flush();gate.resolve();await flush()
  expect(rendered).toEqual(['cancelled']);expect(fetch).not.toHaveBeenCalled()
})
it('does not abort a resumed connection when the previous stream finishes late',async()=>{
  const old=deferred<ReadableStreamReadResult<Uint8Array>>(),signals:AbortSignal[]=[];let requests=0
  vi.mocked(fetch).mockImplementation(async(_url,options)=>{signals.push(options!.signal as AbortSignal);requests++;return {ok:true,status:200,body:requests===1?{getReader:()=>({read:()=>old.promise,cancel:async()=>{},releaseLock:()=>{}})}:new ReadableStream({start(c){stream=c}})} as Response})
  mock.api.mockResolvedValue(snapshot());stop=watchGeneration('batch',vi.fn(),vi.fn());await flush();window.dispatchEvent(new Event('reader-save'));window.dispatchEvent(new Event('reader-resume'));await flush()
  expect(signals).toHaveLength(2);expect(signals[1].aborted).toBe(false);old.resolve({done:true,value:undefined});await flush();expect(signals[1].aborted).toBe(false)
})
