import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createSpeechController} from './speech-controller'
import {defaultSpeechPreferences,type SpeechDriver,type SpeechState,type SpeechSegment} from './speech-types'
import {deferred,flush} from '../test/host-renderer'
const mock=vi.hoisted(()=>({owner:'owner',base:'https://fixture.invalid',auth:0,deleted:false,worldDeleted:false,native:false,cacheGet:vi.fn(),cacheSet:vi.fn()}))
vi.mock('../api',()=>({authGeneration:()=>mock.auth}));vi.mock('./bridge',()=>({accountId:()=>mock.owner,backendBase:()=>mock.base,cacheGet:mock.cacheGet,cacheSet:mock.cacheSet,get native(){return mock.native}}));vi.mock('./book-cache',()=>({isBookDeleted:()=>mock.deleted}));vi.mock('./world-cache',()=>({isWorldLineDeleted:()=>mock.worldDeleted}));vi.mock('./system-speech',()=>({systemSpeech:()=>{throw new Error('tests must inject their own driver')}}))
const segment:SpeechSegment={id:'paragraph',text:'合成段落😀',chapterId:'chapter',chapterTitle:'第一章',anchor:{bookId:'book',branchId:'line',revisionId:'rev',chapterId:'chapter',blockId:'paragraph',start:8,end:8,offsetUnit:'utf16',quoteHash:'hash'}}
let driver:SpeechDriver,callback:(value:SpeechState)=>void,c:ReturnType<typeof createSpeechController>|undefined,key=''
const context={bookId:'book',storyId:'book-book',mode:'read' as const,lineId:'line',revisionId:'rev'}
function engine(){return {voices:vi.fn().mockResolvedValue({available:true,voices:[{id:'zh',name:'中文',language:'zh-CN',offline:true}],initializing:false}),status:vi.fn().mockImplementation(async({scopeKey})=>{key=scopeKey;return {status:'idle',queueLoaded:false}}),addListener:vi.fn().mockImplementation(async(_event,listener)=>{callback=listener;return {remove:vi.fn().mockResolvedValue(undefined)}}),loadQueue:vi.fn().mockImplementation(async options=>({scopeKey:options.scopeKey,status:'ready',queueLoaded:true,currentId:'paragraph',eventVersion:1})),play:vi.fn().mockImplementation(async({scopeKey})=>({scopeKey,status:'starting',queueLoaded:true,currentId:'paragraph',eventVersion:2})),pause:vi.fn().mockImplementation(async({scopeKey})=>({scopeKey,status:'paused',queueLoaded:true,currentId:'paragraph',eventVersion:4})),skip:vi.fn(),configure:vi.fn(),stop:vi.fn().mockResolvedValue(undefined),openSettings:vi.fn().mockResolvedValue({opened:false,instructions:'安装中文声音'})} as SpeechDriver}
beforeEach(()=>{mock.owner='owner';mock.base='https://fixture.invalid';mock.auth=0;mock.deleted=false;mock.worldDeleted=false;mock.native=false;mock.cacheGet.mockReset().mockResolvedValue(undefined);mock.cacheSet.mockReset().mockResolvedValue(undefined);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',Object.assign(new EventTarget(),{hidden:false}));vi.stubGlobal('CustomEvent',class extends Event{detail:any;constructor(name:string,options:any){super(name);this.detail=options?.detail}});driver=engine();key=''})
afterEach(async()=>{c?.dispose();c=undefined;await flush();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})
async function open(){c=createSpeechController(context,'合成书',[segment],()=>true,driver);await c.initialize();return c}
it('only actual completed events persist a public position marker and never cache full narration text',async()=>{
  const value=await open();expect(driver.play).not.toHaveBeenCalled();expect(driver.loadQueue).not.toHaveBeenCalled();await value.play('paragraph');expect(value.state.value.status).toBe('starting');expect(value.lastCompleted.value).toBeNull()
  callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:'paragraph',eventVersion:3});expect(value.lastCompleted.value).toBeNull();expect(mock.cacheSet).not.toHaveBeenCalled()
  callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:'paragraph',eventVersion:4});await flush();expect(value.lastCompleted.value?.id).toBe('paragraph');const saved=mock.cacheSet.mock.calls.at(-1)![1];expect(saved).toMatchObject({bookId:'book',lastCompletedId:'paragraph',ended:true});expect(JSON.stringify(saved)).not.toContain(segment.text);expect(saved.anchor).toEqual(segment.anchor)
})
it('cold native end preserves ended and does not silently load or restart the first paragraph',async()=>{
  mock.native=true;vi.mocked(driver.status).mockImplementation(async({scopeKey})=>{key=scopeKey;return {scopeKey,status:'ended',queueLoaded:false,lastCompletedId:'paragraph',reason:'process-restored'}})
  const value=await open();expect(value.state.value.status).toBe('ended');expect(value.lastCompleted.value?.id).toBe('paragraph');await value.play();expect(driver.play).not.toHaveBeenCalled();expect(driver.loadQueue).not.toHaveBeenCalled();expect(value.error.value).toContain('已听完');await value.play('paragraph');expect(driver.loadQueue).toHaveBeenCalledOnce();expect(driver.play).toHaveBeenCalledOnce()
})
it('cold paused marker restores its current paragraph without claiming it was completed or autoplaying',async()=>{
  mock.cacheGet.mockImplementation(async name=>name.startsWith('speech-position:')?{scopeKey:name.slice('speech-position:'.length),currentId:'paragraph',lastCompletedId:'',ended:false}:undefined)
  const value=await open();expect(value.state.value.status).toBe('paused');expect(value.lastCompleted.value).toBeNull();expect(driver.play).not.toHaveBeenCalled();await value.play();expect(vi.mocked(driver.loadQueue).mock.calls[0][0].fromId).toBe('paragraph')
})
it('cold marker eventVersion belongs to the exited process, not the freshly loaded queue callbacks',async()=>{
  mock.native=true;vi.mocked(driver.status).mockImplementation(async({scopeKey})=>{key=scopeKey;return {scopeKey,status:'paused',queueLoaded:false,currentId:'paragraph',eventVersion:200,reason:'process-restored'}})
  const value=await open();await value.play();expect(value.state.value.status).toBe('starting');callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:'paragraph',eventVersion:3});expect(value.state.value.status).toBe('playing');callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:'paragraph',eventVersion:4});expect(value.lastCompleted.value?.id).toBe('paragraph')
})
it('late queue loading after account changes never proceeds to audio or applies old state',async()=>{
  const value=await open(),gate=deferred<SpeechState>();vi.mocked(driver.loadQueue).mockReturnValueOnce(gate.promise);const starting=value.play();await flush();mock.owner='another';mock.auth++;gate.resolve({scopeKey:key,status:'ready',queueLoaded:true,currentId:'paragraph'});await starting;expect(driver.play).not.toHaveBeenCalled();callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:'paragraph'});expect(value.state.value.status).toBe('idle')
})
it('source deletion immediately clears visible completed state and blocks late callbacks and playback',async()=>{
  const value=await open();await value.play();callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:'paragraph',lastCompletedId:'paragraph',eventVersion:3});mock.deleted=true;window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:'book'}}));await flush();expect(value.lastCompleted.value).toBeNull();expect(value.state.value.status).toBe('idle');expect(driver.stop).toHaveBeenCalledWith({clearPosition:true});callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:'paragraph',eventVersion:8});await value.play();expect(value.lastCompleted.value).toBeNull();expect(driver.play).toHaveBeenCalledOnce()
})
it('new story lease waits old in-flight command, blocks old playback and old cleanup cannot stop the new queue',async()=>{
  const first=await open(),gate=deferred<SpeechState>();vi.mocked(driver.loadQueue).mockReturnValueOnce(gate.promise);const pending=first.play();await flush();const second=createSpeechController({...context,revisionId:'new-rev'},'下一修订',[segment],()=>true,driver),ready=second.initialize();await flush();first.dispose();gate.resolve({scopeKey:key,status:'ready',queueLoaded:true,currentId:'paragraph'});await pending;await ready;expect(driver.play).not.toHaveBeenCalled();await second.play();expect(driver.play).toHaveBeenCalledOnce();expect(driver.stop).not.toHaveBeenCalled();second.dispose();await flush();expect(driver.stop).toHaveBeenCalledOnce()
})
it('real visibility hidden pauses web audio, and foreground does not autoplay it again',async()=>{
  const value=await open();await value.play();callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:'paragraph',eventVersion:3});(document as any).hidden=true;document.dispatchEvent(new Event('visibilitychange'));await flush();expect(driver.pause).toHaveBeenCalledOnce();expect(value.state.value.reason).toBe('page-hidden');(document as any).hidden=false;document.dispatchEvent(new Event('visibilitychange'));await flush();expect(driver.play).toHaveBeenCalledOnce()
})
it('native background is left to the native service and a wrong-scope or older event cannot update its position',async()=>{
  mock.native=true;const value=await open();await value.play();callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:'paragraph',eventVersion:5});callback({scopeKey:'other-owner',status:'ended',queueLoaded:true,lastCompletedId:'paragraph',eventVersion:20});callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:'paragraph',eventVersion:4});expect(value.lastCompleted.value).toBeNull();(document as any).hidden=true;document.dispatchEvent(new Event('visibilitychange'));await flush();expect(driver.pause).not.toHaveBeenCalled()
})
it('preferences are owner/backend local and a late local save cannot configure a different account',async()=>{
  const value=await open();await value.play();const gate=deferred<void>();mock.cacheSet.mockReturnValueOnce(gate.promise);const changing=value.configure({...defaultSpeechPreferences,rate:2,sleepMinutes:60});await flush();mock.auth++;gate.resolve();await changing;expect(driver.configure).not.toHaveBeenCalled();expect(mock.cacheSet.mock.calls[0]).toEqual(['system-speech-preferences:https://fixture.invalid',{rate:2,voiceId:'',sleepMinutes:60,stopAtChapterEnd:false}])
})
it('old native controller cleanup cannot handshake and stop the next authenticated account',async()=>{
 mock.native=true;const value=await open();mock.owner='next-owner';mock.auth++;window.dispatchEvent(new Event('reader-private-cleared'));await flush()
 expect(value.state.value.status).toBe('idle');expect(driver.stop).not.toHaveBeenCalled()
})

function pendingVoices(){
  vi.useFakeTimers()
  vi.spyOn(crypto.subtle,'digest').mockResolvedValue(new Uint8Array(32).buffer)
  vi.mocked(driver.voices).mockResolvedValue({available:false,voices:[],initializing:true,reason:'VOICE_ENGINE_UNAVAILABLE'})
  c=createSpeechController(context,'合成书',[segment],()=>true,driver)
  return c
}
it('first initialization waits for the existing voice engine and explicit play starts exactly once after it is ready',async()=>{
  const value=pendingVoices(),ready=value.initialize()
  await vi.advanceTimersByTimeAsync(0)
  expect(value.loading.value).toBe(true);expect(driver.loadQueue).not.toHaveBeenCalled();expect(driver.play).not.toHaveBeenCalled()
  vi.mocked(driver.voices).mockResolvedValueOnce({available:false,voices:[],initializing:true}).mockResolvedValueOnce({available:true,voices:[{id:'zh',name:'中文',language:'zh-CN',offline:true}],initializing:false})
  await vi.advanceTimersByTimeAsync(200);await ready
  expect(vi.mocked(driver.voices).mock.calls).toEqual([[{retry:false}],[{retry:false}],[{retry:false}]])
  expect(value.loading.value).toBe(false);expect(value.available.value).toBe(true);expect(value.error.value).toBe('');expect(driver.play).not.toHaveBeenCalled()
  await value.play();expect(driver.loadQueue).toHaveBeenCalledOnce();expect(driver.play).toHaveBeenCalledOnce()
})
it('simultaneous explicit voice repair shares one restart and polls without restarting or autoplaying',async()=>{
  vi.useFakeTimers();vi.spyOn(crypto.subtle,'digest').mockResolvedValue(new Uint8Array(32).buffer)
  const value=await open();vi.mocked(driver.voices).mockClear().mockResolvedValueOnce({available:false,voices:[],initializing:true}).mockResolvedValue({available:true,voices:[{id:'zh',name:'中文',language:'zh-CN',offline:true}],initializing:false})
  const first=value.retryVoices(),second=value.retryVoices();await vi.advanceTimersByTimeAsync(100);await Promise.all([first,second])
  expect(vi.mocked(driver.voices).mock.calls).toEqual([[{retry:true}],[{retry:false}]]);expect(value.available.value).toBe(true);expect(driver.play).not.toHaveBeenCalled();expect(driver.loadQueue).not.toHaveBeenCalled()
})
it('voice engine initialization has a ten second deadline and leaves unavailable playback visibly retryable',async()=>{
  const value=pendingVoices(),ready=value.initialize();await vi.advanceTimersByTimeAsync(10100);await ready
  expect(value.loading.value).toBe(false);expect(value.available.value).toBe(false);expect(value.error.value).toContain('初始化');expect(driver.voices).toHaveBeenCalledTimes(100)
  expect(vi.mocked(driver.voices).mock.calls.every(([options])=>options?.retry===false)).toBe(true);expect(driver.play).not.toHaveBeenCalled();expect(driver.loadQueue).not.toHaveBeenCalled();expect(vi.getTimerCount()).toBe(0)
})
it.each(['owner','backend','auth','dispose'] as const)('voice initialization cannot poll or revive the prior context after %s changes',async(change)=>{
  const value=pendingVoices(),ready=value.initialize();await vi.advanceTimersByTimeAsync(0);expect(driver.voices).toHaveBeenCalledOnce()
  if(change==='owner')mock.owner='another';else if(change==='backend')mock.base='https://another.invalid';else if(change==='auth')mock.auth++;else value.dispose()
  await vi.advanceTimersByTimeAsync(100);await ready
  expect(driver.voices).toHaveBeenCalledOnce();expect(value.available.value).toBe(false);expect(value.voices.value).toEqual([]);expect(driver.play).not.toHaveBeenCalled();expect(driver.loadQueue).not.toHaveBeenCalled();expect(vi.getTimerCount()).toBe(0)
})
it('a stalled voice response cannot hold initialization past its deadline or apply its late ready result',async()=>{
  const value=pendingVoices(),gate=deferred<Awaited<ReturnType<SpeechDriver['voices']>>>()
  vi.mocked(driver.voices).mockReturnValueOnce(gate.promise)
  let finished=false;const ready=value.initialize().then(()=>{finished=true})
  await vi.advanceTimersByTimeAsync(10000);expect(finished).toBe(true);await ready
  expect(value.loading.value).toBe(false);expect(value.available.value).toBe(false);expect(value.error.value).toContain('初始化');expect(driver.voices).toHaveBeenCalledOnce()
  gate.resolve({available:true,voices:[{id:'zh',name:'中文',language:'zh-CN',offline:true}],initializing:false});await vi.advanceTimersByTimeAsync(0)
  expect(value.available.value).toBe(false);expect(value.voices.value).toEqual([]);expect(driver.play).not.toHaveBeenCalled();expect(driver.loadQueue).not.toHaveBeenCalled();expect(mock.cacheSet).not.toHaveBeenCalled();expect(vi.getTimerCount()).toBe(0)
})
it('explicit play during voice repair waits for the shared engine initialization before loading a queue',async()=>{
  vi.useFakeTimers();vi.spyOn(crypto.subtle,'digest').mockResolvedValue(new Uint8Array(32).buffer)
  const value=await open()
  vi.mocked(driver.voices).mockClear().mockResolvedValueOnce({available:false,voices:[],initializing:true}).mockResolvedValue({available:true,voices:[{id:'zh',name:'中文',language:'zh-CN',offline:true}],initializing:false})
  const repairing=value.retryVoices(),playing=value.play();await vi.advanceTimersByTimeAsync(0)
  expect(driver.loadQueue).not.toHaveBeenCalled();expect(driver.play).not.toHaveBeenCalled();expect(value.busy.value).toBe(true)
  await vi.advanceTimersByTimeAsync(100);await Promise.all([repairing,playing])
  expect(vi.mocked(driver.voices).mock.calls).toEqual([[{retry:true}],[{retry:false}]]);expect(value.available.value).toBe(true);expect(driver.loadQueue).toHaveBeenCalledOnce();expect(driver.play).toHaveBeenCalledOnce()
})

const later:SpeechSegment={...segment,id:'later-paragraph',text:'明确跳转的后段',anchor:{...segment.anchor,blockId:'later-paragraph',start:8,end:8}}
it('observes only explicit reader selections and skips without fabricating an actual completion',async()=>{
  const observe=vi.fn().mockResolvedValue(undefined)
  vi.mocked(driver.loadQueue).mockImplementation(async options=>({scopeKey:options.scopeKey,status:'ready',queueLoaded:true,currentId:options.fromId||segment.id,eventVersion:1}))
  vi.mocked(driver.play).mockImplementation(async({scopeKey})=>({scopeKey,status:'starting',queueLoaded:true,currentId:later.id,eventVersion:2}))
  c=createSpeechController(context,'合成故事',[segment,later],()=>true,driver,{onReaderObserve:observe} as any);await c.initialize()
  await c.play(later.id)
  expect(observe).toHaveBeenCalledTimes(1);expect(observe.mock.calls[0][0]).toEqual(later);expect(observe.mock.calls[0][1]()).toBe(true)
  expect(c.lastCompleted.value).toBeNull()
  callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:segment.id,eventVersion:3});await flush()
  expect(observe).toHaveBeenCalledTimes(1);expect(c.lastCompleted.value).toBeNull()
  vi.mocked(driver.skip).mockResolvedValueOnce({scopeKey:key,status:'paused',queueLoaded:true,currentId:later.id,eventVersion:4})
  await c.skip(1);expect(observe).toHaveBeenCalledTimes(2);expect(c.lastCompleted.value).toBeNull()
})
it.each(['owner','backend','auth','dispose'] as const)('ignores an explicit reader skip result after its %s scope expires',async(change)=>{
  const observe=vi.fn().mockResolvedValue(undefined),gate=deferred<SpeechState>()
  c=createSpeechController(context,'合成故事',[segment,later],()=>true,driver,{onReaderObserve:observe} as any);await c.initialize();await c.play()
  vi.mocked(driver.skip).mockReturnValueOnce(gate.promise);const pending=c.skip(1);await flush()
  if(change==='owner')mock.owner='other';else if(change==='backend')mock.base='https://other.invalid';else if(change==='auth')mock.auth++;else c.dispose()
  gate.resolve({scopeKey:key,status:'paused',queueLoaded:true,currentId:later.id,eventVersion:4});await pending
  expect(observe).not.toHaveBeenCalled();expect(c.lastCompleted.value).toBeNull()
})
it.each(['world','draft'] as const)('keeps an explicit %s speech selection outside formal Reader Progress',async(kind)=>{
  const observe=vi.fn().mockResolvedValue(undefined),value=kind==='world'?{...context,bookId:undefined,worldId:'world'}:{...context,mode:'draft' as const}
  c=createSpeechController(value,'合成故事',[segment,later],()=>true,driver,{onReaderObserve:observe} as any);await c.initialize();await c.play(later.id)
  vi.mocked(driver.skip).mockResolvedValueOnce({scopeKey:key,status:'paused',queueLoaded:true,currentId:later.id,eventVersion:4});await c.skip(1)
  expect(observe).not.toHaveBeenCalled();expect(c.lastCompleted.value).toBeNull()
})
it('expires the reader observation callback guard on account changes and keeps a failed explicit intent visibly retryable',async()=>{
  const observe=vi.fn().mockRejectedValueOnce(new Error('PROGRESS_CONFLICT')).mockResolvedValueOnce(undefined)
  vi.mocked(driver.loadQueue).mockImplementation(async options=>({scopeKey:options.scopeKey,status:'ready',queueLoaded:true,currentId:options.fromId||segment.id,eventVersion:1}))
  c=createSpeechController(context,'合成故事',[segment,later],()=>true,driver,{onReaderObserve:observe} as any);await c.initialize();await c.play(later.id)
  expect(c.lastCompleted.value).toBeNull();expect(c.error.value).toContain('阅读位置')
  await c.play(later.id);expect(observe).toHaveBeenCalledTimes(2);expect(c.lastCompleted.value).toBeNull();expect(c.error.value).toBe('')
  const valid=observe.mock.calls[1][1];mock.auth++;expect(valid()).toBe(false)
})
