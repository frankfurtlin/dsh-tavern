import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {webcrypto} from 'node:crypto'
const state=vi.hoisted(()=>({values:new Map<string,unknown>(),base:'',hold:undefined as undefined|(()=>Promise<void>),reads:[] as string[],messages:[] as unknown[],channel:undefined as undefined|{onmessage:((event:{data:unknown})=>void)|null},validation:vi.fn(async()=>{})}))
vi.mock('@capacitor/core',()=>({Capacitor:{isNativePlatform:()=>false,getPlatform:()=> 'web'},registerPlugin:()=>({})}))
vi.mock('@capacitor/app',()=>({App:{}}));vi.mock('@capacitor/network',()=>({Network:{}}));vi.mock('@capacitor/keyboard',()=>({Keyboard:{}}))
vi.mock('@capawesome/capacitor-file-picker',()=>({FilePicker:{}}));vi.mock('capacitor-secure-storage-plugin',()=>({SecureStoragePlugin:{}}))
vi.mock('@capacitor/filesystem',()=>({Directory:{Data:'DATA'},Encoding:{UTF8:'utf8'},Filesystem:{}}))
vi.mock('../api/offline-images',()=>({validateOfflineImages:state.validation,remapBookImages:()=>undefined}))
vi.mock('idb',()=>({openDB:async()=>({
 get:async(_store:string,key:string)=>{state.reads.push(key);return structuredClone(state.values.get(key))},
 put:async(_store:string,value:unknown,key:string)=>{state.values.set(key,structuredClone(value))},delete:async(_store:string,key:string)=>{state.values.delete(key)},getAllKeys:async()=>[...state.values.keys()],
 transaction:()=>{const staged=new Map(state.values);let aborted=false;return {store:{get:async(key:string)=>structuredClone(staged.get(key)),put:async(value:unknown,key:string)=>{staged.set(key,structuredClone(value))},delete:async(key:string)=>{staged.delete(key)},getAllKeys:async()=>[...staged.keys()]},abort:()=>{aborted=true},get done(){if(aborted)return Promise.reject(Error('aborted'));state.values.clear();for(const [key,value] of staged)state.values.set(key,value);return Promise.resolve()}}}
})}))
beforeEach(()=>{
 vi.resetModules();state.values.clear();state.reads=[];state.messages=[];state.hold=undefined;state.validation.mockReset();state.validation.mockImplementation(async()=>{if(state.hold)await state.hold()});state.channel=undefined
 const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>{values.set(key,value)},removeItem:(key:string)=>{values.delete(key)},key:(index:number)=>[...values.keys()][index]||null,get length(){return values.size}})
 vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{hidden:false});vi.stubGlobal('crypto',webcrypto);vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})
 vi.stubGlobal('BroadcastChannel',class {onmessage:((event:{data:unknown})=>void)|null=null;constructor(){state.channel=this}postMessage(value:unknown){state.messages.push(structuredClone(value))}close(){}})
 vi.stubEnv('VITE_API_BASE_URL','');vi.stubGlobal('fetch',vi.fn(async()=>{throw Error('No backend HTTP in storage tests')}))
})
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs()})
async function modules(){const bridge=await import('../platform/bridge'),client=await import('../api'),storage=await import('./storage'),books=await import('../platform/book-cache');bridge.setAccount('owner-a');return {bridge,client,storage,books}}
function payload(bookId='book-a'){
 const anchor={bookId,branchId:'branch-'+bookId,revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:0,end:0,offsetUnit:'utf16',quoteHash:'hash'} as const
 return {book:{id:bookId,title:'Own offline source',originalBranchId:anchor.branchId,originalRevisionId:anchor.revisionId},branch:{id:anchor.branchId,name:'original',headRevisionId:anchor.revisionId},revisionId:anchor.revisionId,directory:[{id:'chapter-a',title:'One',observed:true,blockCount:1}],chapters:[{id:'chapter-a',title:'One',revisionId:anchor.revisionId,contentHash:'known',blocks:[{id:'block-a',text:'Own source text',html:null,source:'original',startAnchor:anchor,endAnchor:anchor}]}],bookmarks:[],progress:{version:0,anchor:null,maxObservedAnchor:null},assets:{},origins:['book-'+bookId],images:{schema:1,placements:[],bytes:{}}} as any
}
const route=(book='book-a')=>'/branches/branch-'+book+'/chapters/chapter-a?revisionId=revision-a'
it('reuses an actually verified package for hot chapter reads without copying or verifying the whole payload again',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);await m.storage.readPackage(entry.id);state.validation.mockClear();state.reads=[]
 for(let i=0;i<3;i++)expect((await m.storage.offlineRead<any>(route())).id).toBe('chapter-a')
 expect(state.validation).not.toHaveBeenCalled();expect(state.reads.filter(key=>key.includes('offline-reading-package:'))).toHaveLength(0)
})
it('never exposes the mutable internal package and keeps explicit package reads fully verified',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true),first=(await m.storage.readPackage(entry.id))!;first.payload.chapters[0]!.blocks[0]!.text='caller mutation'
 const chapter=await m.storage.offlineRead<any>(route());expect(chapter.blocks[0].text).toBe('Own source text');chapter.blocks[0].text='another caller mutation'
 expect((await m.storage.offlineRead<any>(route())).blocks[0].text).toBe('Own source text');state.validation.mockClear();await m.storage.readPackage(entry.id);await m.storage.readPackage(entry.id);expect(state.validation).toHaveBeenCalledTimes(2)
})
it('invalidates a hot package before a replacement write and verifies the new bytes',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());const changed=payload();changed.chapters[0].blocks[0].text='Replacement'
 const replacement={schema:1,id:entry.id,downloadedAt:'new',sha256:await m.storage.digest(changed),payload:changed};await m.bridge.cacheSet(m.storage.packageKey(entry.id),replacement);state.validation.mockClear()
 expect((await m.storage.offlineRead<any>(route())).blocks[0].text).toBe('Replacement');expect(state.validation).toHaveBeenCalledTimes(1)
})
it('does not treat a damaged durable package as valid and does not retain its old hot entry after explicit verification',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true),key=m.storage.packageKey(entry.id);await m.storage.offlineRead(route())
 const stored=(state.values.get('owner-a:'+key) as any);stored.payload.chapters[0].blocks[0].text='Damaged on disk'
 expect(await m.storage.readPackage(entry.id)).toBeUndefined();expect(await m.storage.offlineRead(route())).toBeUndefined()
})
it('deleting a download, marking its book removed and purging its cache all revoke hot prose',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());await m.storage.removePackage(entry.id);expect(await m.storage.offlineRead(route())).toBeUndefined()
 const next=await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());m.books.markBookDeleted('book-a');expect(await m.storage.offlineRead(route())).toBeUndefined();expect(await m.storage.readPackage(next.id)).toBeUndefined()
 await m.bridge.purgeBookCache('book-a',()=>true);expect(await m.storage.readPackage(next.id,true)).toBeUndefined()
})
it('losing auth, switching owner away and back, and private teardown do not return or resurrect an old package',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());state.validation.mockClear();m.client.beginAuthentication();await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(1)
 m.bridge.setAccount('owner-b');expect(await m.storage.offlineRead(route())).toBeUndefined();m.bridge.setAccount('owner-a');state.validation.mockClear();await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(1)
 await m.bridge.clearPrivate();expect(await m.storage.readPackage(entry.id)).toBeUndefined();m.bridge.setAccount('owner-a');expect(await m.storage.offlineRead(route())).toBeUndefined()
})
it('a late validated read is discarded when a replacement starts and cannot repopulate the hot cache',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);let release!:()=>void;state.hold=()=>new Promise<void>(r=>{release=r});const pending=m.storage.readPackage(entry.id);await vi.waitFor(()=>expect(release).toBeTypeOf('function'))
 await m.bridge.cacheSet(m.storage.packageKey(entry.id),null);state.hold=undefined;release();expect(await pending).toBeUndefined();expect(await m.storage.offlineRead(route())).toBeUndefined()
})
it('a late read crossing an owner round trip or a server change cannot be cached',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);let release!:()=>void;state.hold=()=>new Promise<void>(r=>{release=r});const pending=m.storage.readPackage(entry.id);await vi.waitFor(()=>expect(release).toBeTypeOf('function'))
 m.bridge.setAccount('owner-b');m.bridge.setAccount('owner-a');state.hold=undefined;release();expect(await pending).toBeUndefined()
 window.dispatchEvent(Object.assign(new Event('storage'),{key:'reader-server',newValue:'https://new.invalid'}));state.validation.mockClear();await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(1)
})
it('cross-tab package writes, account changes and revocation invalidate the hot entry without broadcasting private values',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());state.validation.mockClear()
 state.channel?.onmessage?.({data:{owner:'owner-a',base:'',key:m.storage.packageKey(entry.id)}});await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(1)
 for(const key of ['reader-account','reader-session-revoked']){state.validation.mockClear();window.dispatchEvent(Object.assign(new Event('storage'),{key}));await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(1)}
})
it('keeps at most two verified packages and evicts the least recently used one',async()=>{
 const m=await modules();for(const book of ['book-a','book-b','book-c'])await m.storage.commitPackage(payload(book),()=>true)
 state.validation.mockClear();await m.storage.offlineRead(route('book-b'));await m.storage.offlineRead(route('book-c'));expect(state.validation).not.toHaveBeenCalled();await m.storage.offlineRead(route('book-a'));expect(state.validation).toHaveBeenCalledTimes(1)
})
it('does not keep packages larger than the fixed serialized byte budget hot',async()=>{
 const m=await modules(),large=payload();large.chapters[0].blocks[0].text='a'.repeat(32*1024*1024);await m.storage.commitPackage(large,()=>true);state.validation.mockClear();await m.storage.offlineRead(route());await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(2)
},15000)
it('revocation or an account change in another tab prevents fresh and cached reads under the stale local session',async()=>{
 const m=await modules();await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());localStorage.setItem('reader-session-revoked','1');window.dispatchEvent(Object.assign(new Event('storage'),{key:'reader-session-revoked'}));expect(await m.storage.offlineRead(route())).toBeUndefined()
 localStorage.removeItem('reader-session-revoked');localStorage.setItem('reader-account','owner-b');window.dispatchEvent(Object.assign(new Event('storage'),{key:'reader-account'}));expect(await m.storage.offlineRead(route())).toBeUndefined()
})
it('an atomic package update invalidates the old immutable version without affecting unrelated progress writes',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);await m.storage.offlineRead(route());state.validation.mockClear();await m.bridge.cacheSet('pending-progress:other','no package replacement');await m.storage.offlineRead(route());expect(state.validation).not.toHaveBeenCalled()
 const changed=payload();changed.chapters[0].blocks[0].text='Updated package';const sha256=await m.storage.digest(changed);await m.bridge.cacheUpdate(m.storage.packageKey(entry.id),(old:any)=>({...old,payload:changed,sha256}));expect((await m.storage.offlineRead<any>(route())).blocks[0].text).toBe('Updated package');expect(state.validation).toHaveBeenCalledTimes(1)
})
it('an in-flight validation cannot repopulate a package after a cross-tab write notification',async()=>{
 const m=await modules(),entry=await m.storage.commitPackage(payload(),()=>true);let release!:()=>void;state.hold=()=>new Promise<void>(r=>{release=r});const pending=m.storage.readPackage(entry.id);await vi.waitFor(()=>expect(release).toBeTypeOf('function'))
 expect(state.channel).toBeDefined();state.channel!.onmessage?.({data:{owner:'owner-a',base:'',key:m.storage.packageKey(entry.id)}});state.hold=undefined;release();expect(await pending).toBeUndefined();state.validation.mockClear();await m.storage.offlineRead(route());expect(state.validation).toHaveBeenCalledTimes(1)
})
it('cross-tab invalidation carries only cache scope identities and never a cached value or prose',async()=>{
 const m=await modules();await m.bridge.cacheSet('own-package-key',{text:'PRIVATE_SYNTHETIC_TEXT'});expect(state.messages.length).toBeGreaterThanOrEqual(2)
 for(const message of state.messages){expect(Object.keys(message as object).sort()).toEqual(['base','key','owner']);expect(JSON.stringify(message)).not.toContain('PRIVATE_SYNTHETIC_TEXT')}
})
it('the aggregate serialized byte cap also evicts an otherwise valid second package',async()=>{
 const m=await modules();for(const book of ['book-a','book-b']){const data=payload(book);data.chapters[0].blocks[0].text='x'.repeat(17*1024*1024);await m.storage.commitPackage(data,()=>true)}state.validation.mockClear();await m.storage.offlineRead(route('book-b'));expect(state.validation).not.toHaveBeenCalled();await m.storage.offlineRead(route('book-a'));expect(state.validation).toHaveBeenCalledTimes(1)
},15000)
