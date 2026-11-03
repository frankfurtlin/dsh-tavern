import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createSpeechController} from './speech-controller'
import {createWebSpeech} from './web-speech'
import {defaultSpeechPreferences,type SpeechDriver,type SpeechState,type SpeechSegment} from './speech-types'
import {flush,deferred} from '../test/host-renderer'
const mock=vi.hoisted(()=>({owner:'a'.repeat(32),base:'https://cloud.invalid',epoch:0,request:vi.fn(),cache:new Map<string,unknown>(),failIntent:false}))
vi.mock('../api',()=>({api:mock.request,authGeneration:()=>mock.epoch,newKey:()=>crypto.randomUUID(),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('./bridge',()=>({accountId:()=>mock.owner,backendBase:()=>mock.base,native:false,cacheGet:async(key:string)=>mock.cache.get(key),cacheSet:async(key:string,value:unknown)=>{if(mock.failIntent&&key.startsWith('speech-cloud-position:'))throw new Error('IDB unavailable');mock.cache.set(key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('./book-cache',()=>({isBookDeleted:()=>false}));vi.mock('./world-cache',()=>({isWorldLineDeleted:()=>false}));vi.mock('./system-speech',()=>({systemSpeech:()=>{throw new Error('inject engine')}}))
const ids={book:'b'.repeat(32),line:'c'.repeat(32),rev:'d'.repeat(32),chapter:'e'.repeat(32),block:'f'.repeat(32)}
const anchor={bookId:ids.book,branchId:ids.line,revisionId:ids.rev,chapterId:ids.chapter,blockId:ids.block,start:4,end:4,offsetUnit:'utf16' as const,quoteHash:'0'.repeat(64)}
const segment:SpeechSegment={id:ids.block,text:'合成正文',chapterId:ids.chapter,chapterTitle:'第一章',anchor}
const context={bookId:ids.book,storyId:'book-'+ids.book,mode:'read' as const,lineId:ids.line,revisionId:ids.rev,baseContext:{storyId:'book-'+ids.book,mode:'read' as const,perspective:'reader' as const,entryOrigin:'reader' as const,bookId:ids.book,readerBranchId:ids.line,anchor:{...anchor,start:0,end:0}}}
let callback:(state:SpeechState)=>void,key='',c:ReturnType<typeof createSpeechController>|undefined
function receipt(body:any,requestId:string){return {version:body.expectedVersion+1,requestId,readAdvance:'advanced',readerProgress:{version:4,anchor:{...anchor,start:0,end:0},maxObservedAnchor:anchor,revisionId:ids.rev},position:{context:body.context,kind:body.kind,mode:body.mode,lineId:body.lineId,contentRevision:body.contentRevision,paragraphId:body.paragraphId,lastCompletedAnchor:body.lastCompletedAnchor,maxHeardAnchor:body.lastCompletedAnchor,ended:body.ended,completedAt:'2026-10-05T00:00:00Z',updatedAt:'2026-10-05T00:00:00Z'}}}
function driver(){return {voices:vi.fn().mockResolvedValue({available:true,voices:[],initializing:false}),status:vi.fn().mockImplementation(async({scopeKey})=>{key=scopeKey;return {status:'idle',queueLoaded:false}}),addListener:vi.fn().mockImplementation(async(_,value)=>{callback=value;return{remove:async()=>{}}}),loadQueue:vi.fn(),play:vi.fn(),pause:vi.fn(),skip:vi.fn(),configure:vi.fn(),stop:vi.fn().mockResolvedValue(undefined),openSettings:vi.fn()} as SpeechDriver}
beforeEach(()=>{mock.owner='a'.repeat(32);mock.base='https://cloud.invalid';mock.epoch=0;mock.failIntent=false;mock.cache.clear();mock.request.mockReset().mockImplementation(async(path:string,options:any={})=>{if(path==='/playback/capabilities')return{ready:true,protocol:'reader-playback-v1'};if(path==='/settings/reading')return{version:9,editVersion:9,speech:defaultSpeechPreferences};if(path.startsWith('/playback/position')&&options.method!=='PUT')return{version:0,position:null};if(path==='/playback/position')return receipt(options.body,options.key);throw new Error('unexpected '+path)});vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',Object.assign(new EventTarget(),{hidden:false}))})
afterEach(async()=>{c?.dispose();c=undefined;await flush();vi.unstubAllGlobals()})
async function open(segments=[segment]){const onProgress=vi.fn();c=createSpeechController(context,'合成书',segments,()=>true,driver(),{progressVersion:()=>3,onReaderProgress:onProgress} as any);await c.initialize();return{value:c,onProgress}}
const puts=()=>mock.request.mock.calls.filter(([path,options])=>path==='/playback/position'&&options?.method==='PUT')
it('actual completion sends one exact cloud PUT and atomically returns reader Progress without moving its anchor',async()=>{
 const {value,onProgress}=await open();expect(mock.request).toHaveBeenCalledWith('/playback/capabilities');expect(puts()).toHaveLength(0)
 callback({scopeKey:key,status:'playing',queueLoaded:true,currentId:ids.block,eventVersion:1});await flush();expect(puts()).toHaveLength(0)
 callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush()
 expect(puts()).toHaveLength(1);expect(puts()[0][1]).toMatchObject({method:'PUT',key:expect.any(String),body:{expectedVersion:0,expectedProgressVersion:3,context:context.baseContext,completed:true,paragraphId:ids.block,lastCompletedAnchor:anchor,ended:true}})
 expect(onProgress).toHaveBeenCalledWith(expect.objectContaining({version:4,anchor:context.baseContext.anchor,maxObservedAnchor:anchor}),3);expect(value.lastCompleted.value?.id).toBe(ids.block)
})
it('a lost completion response retains its original key and recovers only by GET across controller recreation',async()=>{
 const original=mock.request.getMockImplementation()!;let saved:any
 mock.request.mockImplementation(async(path,options:any={})=>{if(path==='/playback/position'&&options.method==='PUT'){saved=receipt(options.body,options.key);throw Object.assign(new Error('lost'),{status:0})}if(path.startsWith('/playback/request-status?'))return{found:true,receipt:saved};return original(path,options)})
 const first=await open();callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();expect(puts()).toHaveLength(1);first.value.dispose()
 const second=await open();await flush();expect(puts()).toHaveLength(1);expect(mock.request.mock.calls.some(([path])=>path.startsWith('/playback/request-status?')&&path.includes(encodeURIComponent(saved.requestId)))).toBe(true);expect(second.value.lastCompleted.value?.id).toBe(ids.block)
})
it('late cloud success after account change cannot expose position or update reader Progress',async()=>{
 const gate=deferred<any>(),original=mock.request.getMockImplementation()!;mock.request.mockImplementation(async(path,options:any={})=>path==='/playback/position'&&options.method==='PUT'?gate.promise:original(path,options))
 const {value,onProgress}=await open();callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();expect(puts()).toHaveLength(1)
 mock.owner='1'.repeat(32);mock.epoch++;gate.resolve(receipt(puts()[0][1].body,puts()[0][1].key));await flush();expect(onProgress).not.toHaveBeenCalled();expect(value.cloud?.status.value).not.toBe('saved')
})
it('a known position conflict survives recreation with the actual local completed paragraph and no automatic overwrite',async()=>{
 const second={...segment,id:'1'.repeat(32),text:'本机实际已播的第二段',anchor:{...anchor,blockId:'1'.repeat(32)}},original=mock.request.getMockImplementation()!
 mock.request.mockImplementation(async(path,options:any={})=>{if(path==='/playback/position'&&options.method==='PUT')throw Object.assign(new Error('CAS'),{status:409});if(path.startsWith('/playback/position'))return{version:1,position:receipt({...context,context:context.baseContext,kind:'reader',mode:'read',lineId:ids.line,contentRevision:ids.rev,expectedVersion:0,paragraphId:ids.block,lastCompletedAnchor:anchor,ended:false},'old').position};return original(path,options)})
 const first=await open([segment,second]);callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:second.id,eventVersion:2});await flush();expect(first.value.cloud?.status.value).toBe('conflict');first.value.dispose()
 const next=await open([segment,second]);expect(next.value.lastCompleted.value?.id).toBe(second.id);expect(puts()).toHaveLength(1);expect(JSON.stringify([...mock.cache.values()])).not.toContain(second.text)
})
it('failed durable intent storage never issues a HTTP PUT or labels the unsubmitted request unknown',async()=>{
 const {value}=await open();mock.failIntent=true
 callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();expect(puts()).toHaveLength(0);expect(value.cloud?.status.value).toBe('error');expect(value.lastCompleted.value?.id).toBe(ids.block)
})
it('settings CAS preserves local voice choices, explicitly reads current shared version, then saves only after another choice',async()=>{
 let version=9,patches=0;const original=mock.request.getMockImplementation()!
 mock.request.mockImplementation(async(path,options:any={})=>{if(path==='/settings/reading'){if(options.method==='PATCH'){patches++;if(options.body.expectedVersion!==version)throw Object.assign(new Error('CAS'),{status:409});return{version:++version,editVersion:version,speech:options.body.speech}}return{version,editVersion:version,speech:defaultSpeechPreferences}}return original(path,options)})
 const {value}=await open();version=10;await value.configure({...defaultSpeechPreferences,rate:2,voiceId:'local-unavailable'});await flush();expect(value.preferences.value.rate).toBe(2);expect(value.cloud?.preferenceStatus.value).toBe('conflict')
 await value.cloud?.refreshPreferences();expect(patches).toBe(1);expect(value.preferences.value.voiceId).toBe('local-unavailable');await value.cloud?.savePreferences();expect(patches).toBe(2);expect(value.cloud?.preferenceStatus.value).toBe('saved')
})
it('capability absent leaves local completion working without preference or position requests',async()=>{
 mock.request.mockResolvedValue({ready:false,protocol:'reader-playback-v1'});const {value}=await open();callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();expect(value.lastCompleted.value?.id).toBe(ids.block);expect(mock.request).toHaveBeenCalledTimes(1);expect(value.cloud?.supported.value).toBe(false)
})
it('a mismatched success receipt stays unknown and does not generate another completion write',async()=>{
 const original=mock.request.getMockImplementation()!;mock.request.mockImplementation(async(path,options:any={})=>{const result:any=await original(path,options);return options.method==='PUT'?{...result,requestId:'wrong-request'}:result})
 const {value}=await open();callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();expect(value.cloud?.status.value).toBe('unknown');expect(value.cloud?.pending.value?.requestId).not.toBe('wrong-request');expect(puts()).toHaveLength(1)
})
it.each(['world','draft'] as const)('%s actual completed paragraph uses its public base context and never requests formal reader advancement',async kind=>{
 const worldId='1'.repeat(32),checkpoint='2'.repeat(32),generation='3'.repeat(32),content='4'.repeat(32)
 const nextContext=kind==='world'?{storyId:'world-'+worldId,mode:'read' as const,lineId:ids.line,revisionId:checkpoint,worldId,baseContext:{storyId:'world-'+worldId,mode:'read' as const,perspective:'reader' as const,entryOrigin:'reader' as const,worldId,worldLineId:ids.line,checkpointId:checkpoint}}:{...context,mode:'draft' as const,revisionId:generation+':2'}
 const nextSegment:SpeechSegment=kind==='world'?{...segment,id:checkpoint+':0',anchor:{worldId,worldLineId:ids.line,checkpointId:checkpoint,paragraph:0,start:0,end:4,offsetUnit:'utf16'}}:{...segment,id:content,anchor:{bookId:ids.book,generationId:generation,contentId:content,candidateVersion:2,start:0,end:4,offsetUnit:'utf16',draft:true}}
 const original=mock.request.getMockImplementation()!;mock.request.mockImplementation(async(path,options:any={})=>{const value:any=await original(path,options);if(options.method==='PUT'){delete value.readerProgress;value.readAdvance='not-requested'}return value})
 c=createSpeechController(nextContext,'合成正文',[nextSegment],()=>true,driver(),{progressVersion:()=>999});await c.initialize();callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:nextSegment.id,eventVersion:2});await flush()
 expect(puts()).toHaveLength(1);expect(puts()[0][1].body).toMatchObject({kind,mode:kind==='draft'?'draft':'read',context:nextContext.baseContext});expect(puts()[0][1].body).not.toHaveProperty('expectedProgressVersion');expect(c.cloud?.status.value).toBe('saved')
})
it('explicit progress conflict refresh reads real CAS and preserves local completion until a second explicit save',async()=>{
 let progress=3,serverPosition=0;const original=mock.request.getMockImplementation()!,onProgress=vi.fn(value=>{progress=value.version})
 mock.request.mockImplementation(async(path,options:any={})=>{if(path.startsWith('/books/'))return{version:6,anchor:context.baseContext.anchor,maxObservedAnchor:anchor,revisionId:ids.rev};if(path==='/playback/position'&&options.method==='PUT'){if(options.body.expectedProgressVersion!==6)throw Object.assign(new Error('progress conflict'),{status:409});serverPosition++;const value=receipt(options.body,options.key);value.readerProgress.version=7;return value}return original(path,options)})
 c=createSpeechController(context,'合成正文',[segment],()=>true,driver(),{progressVersion:()=>progress,onReaderProgress:onProgress});await c.initialize();callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();expect(c.cloud?.status.value).toBe('conflict');expect(serverPosition).toBe(0)
 await c.cloud?.refreshPosition();expect(progress).toBe(6);expect(serverPosition).toBe(0);expect(c.lastCompleted.value?.id).toBe(ids.block);await c.cloud?.saveLatest();expect(serverPosition).toBe(1);expect(progress).toBe(7);expect(puts()).toHaveLength(2)
})
it('a cloud cold position restores a paused marker only; queue loading and audio still require explicit user play',async()=>{
 const original=mock.request.getMockImplementation()!,engine=driver();mock.request.mockImplementation(async(path,options:any={})=>path.startsWith('/playback/position')?{version:1,position:receipt({expectedVersion:0,context:context.baseContext,kind:'reader',mode:'read',lineId:ids.line,contentRevision:ids.rev,paragraphId:ids.block,lastCompletedAnchor:anchor,ended:false},'stored').position}:original(path,options))
 c=createSpeechController(context,'合成正文',[segment],()=>true,engine);await c.initialize();expect(c.state.value.status).toBe('paused');expect(c.lastCompleted.value?.id).toBe(ids.block);expect(engine.play).not.toHaveBeenCalled();expect(engine.loadQueue).not.toHaveBeenCalled();expect(puts()).toHaveLength(0)
})
it('rapid explicit preference choices serialize against the actual returned shared settings version',async()=>{
 const gate=deferred<any>(),original=mock.request.getMockImplementation()!;let patches=0
 mock.request.mockImplementation(async(path,options:any={})=>{if(path==='/settings/reading'&&options.method==='PATCH'){patches++;if(patches===1)return gate.promise;return{version:11,editVersion:11,speech:options.body.speech}}return original(path,options)})
 const {value}=await open();await value.configure({...defaultSpeechPreferences,rate:1.25});await flush();await value.configure({...defaultSpeechPreferences,rate:2});await flush();expect(patches).toBe(1)
 gate.resolve({version:10,editVersion:10,speech:{...defaultSpeechPreferences,rate:1.25}});await flush();expect(patches).toBe(2);const requests=mock.request.mock.calls.filter(([_,options])=>options?.method==='PATCH');expect(requests[1][1].body).toMatchObject({expectedVersion:10,speech:{rate:2}});expect(value.preferences.value.rate).toBe(2)
})
it('the real WebSpeech final onend publishes one ended cloud completion rather than a paused cold marker',async()=>{
 let utterance:any
 class Utterance{onstart?:()=>void;onend?:()=>void;onerror?:()=>void;constructor(public text:string){}}
 const synthesis={getVoices:()=>[{voiceURI:'fixture-zh',name:'受控回调',lang:'zh-CN',localService:true}],cancel:()=>{},speak:(value:any)=>{utterance=value},addEventListener:()=>{},removeEventListener:()=>{}}
 const engine=createWebSpeech(synthesis as unknown as SpeechSynthesis,Utterance as unknown as typeof SpeechSynthesisUtterance)
 c=createSpeechController(context,'合成正文',[segment],()=>true,engine,{progressVersion:()=>3});await c.initialize();await c.play();utterance.onstart();utterance.onend();await flush()
 expect(c.state.value.status).toBe('ended');expect(puts()).toHaveLength(1);expect(puts()[0][1].body.ended).toBe(true);expect(c.cloud?.position.value.position?.ended).toBe(true)
})
it('a later native ended event for the same completed paragraph is not lost when its first receipt is still pending',async()=>{
 const gate=deferred<any>(),original=mock.request.getMockImplementation()!;let count=0
 mock.request.mockImplementation(async(path,options:any={})=>{if(path==='/playback/position'&&options.method==='PUT'&&++count===1)return gate.promise;return original(path,options)})
 const {value}=await open();callback({scopeKey:key,status:'playing',queueLoaded:true,lastCompletedId:ids.block,eventVersion:1});await flush();expect(puts()).toHaveLength(1)
 callback({scopeKey:key,status:'ended',queueLoaded:true,lastCompletedId:ids.block,eventVersion:2});await flush();gate.resolve(receipt(puts()[0][1].body,puts()[0][1].key));await flush()
 expect(puts()).toHaveLength(2);expect(puts()[1][1].body.ended).toBe(true);expect(value.cloud?.position.value.position?.ended).toBe(true)
})
