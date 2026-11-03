import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {webcrypto} from 'node:crypto'

const fixture=vi.hoisted(()=>({native:false,values:new Map<string,unknown>(),files:new Map<string,string>(),writes:[] as string[],deletes:[] as string[],failDeletes:new Set<string>(),pause:undefined as undefined|((path:string)=>Promise<void>)}))
vi.mock('@capacitor/core',()=>({Capacitor:{isNativePlatform:()=>fixture.native,getPlatform:()=>fixture.native?'android':'web'},registerPlugin:()=>({})}))
vi.mock('@capacitor/app',()=>({App:{}}));vi.mock('@capacitor/network',()=>({Network:{}}));vi.mock('@capacitor/keyboard',()=>({Keyboard:{}}))
vi.mock('@capawesome/capacitor-file-picker',()=>({FilePicker:{}}));vi.mock('capacitor-secure-storage-plugin',()=>({SecureStoragePlugin:{}}))
vi.mock('@capacitor/filesystem',()=>({Directory:{Data:'DATA'},Encoding:{UTF8:'utf8'},Filesystem:{
  readFile:vi.fn(async({path}:{path:string})=>{if(!fixture.files.has(path))throw new Error('ENOENT');return {data:fixture.files.get(path)}}),
  writeFile:vi.fn(async({path,data}:{path:string;data:string})=>{if(fixture.pause)await fixture.pause(path);fixture.writes.push(path);fixture.files.set(path,data)}),
  deleteFile:vi.fn(async({path}:{path:string})=>{fixture.deletes.push(path);if(fixture.failDeletes.has(path))throw new Error('permission denied');fixture.files.delete(path)}),
  rmdir:vi.fn(async({path}:{path:string})=>{for(const key of fixture.files.keys())if(key.startsWith(path+'/'))fixture.files.delete(key)})
}}))
vi.mock('idb',()=>({openDB:async()=>({
  get:async(_store:string,key:string)=>structuredClone(fixture.values.get(key)),
  put:async(_store:string,value:unknown,key:string)=>{fixture.values.set(key,structuredClone(value))},
  delete:async(_store:string,key:string)=>{fixture.values.delete(key)},getAllKeys:async()=>[...fixture.values.keys()],
  transaction:()=>{
    const working=new Map(fixture.values);let aborted=false
    const store={get:async(key:string)=>structuredClone(working.get(key)),put:async(value:unknown,key:string)=>{working.set(key,structuredClone(value))},delete:async(key:string)=>{working.delete(key)},getAllKeys:async()=>[...working.keys()]}
    return {store,abort:()=>{aborted=true},get done(){if(aborted)return Promise.reject(new Error('aborted'));fixture.values.clear();for(const [k,v] of working)fixture.values.set(k,v);return Promise.resolve()}}
  }
})}))
const storageValues=new Map<string,string>()
beforeEach(()=>{
  vi.resetModules();fixture.native=false;fixture.values.clear();fixture.files.clear();fixture.writes=[];fixture.deletes=[];fixture.failDeletes.clear();fixture.pause=undefined;storageValues.clear()
  vi.stubGlobal('crypto',webcrypto);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{hidden:false})
  vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})
  vi.stubGlobal('localStorage',{getItem:(key:string)=>storageValues.get(key)||null,setItem:(key:string,value:string)=>storageValues.set(key,value),removeItem:(key:string)=>storageValues.delete(key),key:(index:number)=>[...storageValues.keys()][index]||null,get length(){return storageValues.size}})
  vi.stubGlobal('fetch',vi.fn());vi.stubEnv('VITE_API_BASE_URL','');vi.stubEnv('VITE_NATIVE_ALLOWED_SERVERS','');vi.stubEnv('VITE_NATIVE_DEV_SERVER','')
})
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs()})
async function modules(){const bridge=await import('./bridge'),book=await import('./book-cache');bridge.setAccount('owner-a');return {bridge,book}}
const anchor={bookId:'source-a',branchId:'branch-a',revisionId:'revision-a',chapterId:'chapter-a',blockId:'block-a',start:0,end:0,offsetUnit:'utf16',quoteHash:'known'} as const
const source={id:'source-a',title:'私有原文',originalBranchId:'branch-a',originalRevisionId:'revision-a'}
function body(bookId='source-a',branchId='branch-a'){
  const at={...anchor,bookId,branchId}
  return {book:{...source,id:bookId,originalBranchId:branchId},branch:{id:branchId,name:'原文',headRevisionId:anchor.revisionId,parentBranchId:null,forkAnchor:null},revisionId:anchor.revisionId,directory:[{id:'chapter-a',title:'第一章',blockCount:1}],chapters:[{id:'chapter-a',revisionId:anchor.revisionId,title:'第一章',blocks:[{id:'block-a',text:'甲😀乙',startAnchor:at,endAnchor:at}]}],bookmarks:[{id:'note-a',anchor:at,note:'本机笔记',version:1}],progress:{version:1,anchor:at,maxObservedAnchor:at},assets:{image:'data:image/png;base64,known'},origins:['book-'+bookId]}
}
it('blocks source reads/writes immediately and persists its tombstone across module reload and owner changes',async()=>{
  const {bridge,book}=await modules();await bridge.cacheSet('asset:source-a:image','private pixels');book.markBookDeleted('source-a')
  expect(await bridge.cacheGet('asset:source-a:image')).toBeUndefined();await expect(bridge.cacheSet('asset:source-a:image','late pixels')).rejects.toMatchObject({code:'BOOK_DELETED'})
  bridge.setAccount('owner-b');expect(book.isBookDeleted('source-a')).toBe(false);await bridge.cacheSet('asset:source-a:image','other owner pixels')
  bridge.setAccount('owner-a');expect(book.isBookDeleted('source-a')).toBe(true)
  vi.resetModules();const next=await modules();expect(next.book.deletedBook('source-a')).toMatchObject({cleanupPending:true});expect(await next.bridge.cacheGet('asset:source-a:image')).toBeUndefined()
})
it('uses backend as part of the tombstone identity and broadcasts a persisted other-tab deletion',async()=>{
  const first=await modules();first.book.markBookDeleted('source-a');const deletedKey=[...storageValues.keys()].find(key=>key.startsWith('reader-book-deleted:'))!
  let seen='';window.addEventListener('reader-book-deleted',event=>{seen=(event as CustomEvent).detail.bookId})
  const event=new Event('storage');Object.defineProperty(event,'key',{value:deletedKey});window.dispatchEvent(event);expect(seen).toBe('source-a')
  vi.stubEnv('VITE_API_BASE_URL','https://other.example');vi.resetModules();const second=await modules();expect(second.book.isBookDeleted('source-a')).toBe(false)
})
it('purges actual API, branch, role, task, draft and asset keys while retaining another book and world history',async()=>{
  const {bridge,book}=await modules();book.recordBookReferences('/books/source-a/branches',{items:[{id:'branch-a',headRevisionId:'revision-a'}]})
  book.recordBookReferences('/books/source-a/character-chats',{id:'session-a',bookId:'source-a',branchId:'branch-a',pendingTaskId:'task-a'})
  const old=['api:/books/source-a','api:/branches/branch-a/chapters?revisionId=revision-a','api:/role-sessions/session-a/messages','api:/tasks/task-a','chat-draft:session-a','chat-pending:session-a','generation:branch-a','note-draft:source-a:branch-a:revision-a:block-a:new','asset:source-a:image']
  for(const key of old)await bridge.cacheSet(key,key==='api:/books/source-a'?source:{text:'private source body'})
  await bridge.cacheSet('api:/books?limit=20',{items:[source,{...source,id:'other-book',originalBranchId:'other-branch'}]})
  await bridge.cacheSet('asset:other-book:image','other pixels');await bridge.cacheSet('world-api:/worlds/world-a',{sourceBookId:'source-a',sourceAnchor:anchor,text:'independent history'})
  book.markBookDeleted('source-a');await bridge.purgeBookCache('source-a',()=>true)
  for(const key of old){expect(await bridge.cacheGet(key,{allowDeleted:true})).toBeUndefined();expect(fixture.values.has('owner-a:'+key)).toBe(false)}
  expect(await bridge.cacheGet('asset:other-book:image')).toBe('other pixels')
  expect(await bridge.cacheGet<any>('api:/books?limit=20')).toMatchObject({items:[{id:'other-book'}]})
  expect(await bridge.cacheGet<any>('world-api:/worlds/world-a')).toMatchObject({text:'independent history'})
})
it('removes new and legacy selection drafts and blocks late revision reads after source deletion',async()=>{
  const {bridge,book}=await modules(),{api}=await import('../api')
  book.recordBookReferences('/books/source-a/branches',{items:[{id:'branch-a',headRevisionId:'revision-a'}]})
  const legacy='selection-draft:branch-a:revision-a:block-a:0:4',draft='selection-draft:branch-a:revision-a:block-a:4:8',intent='selection-revision-intent:branch-a'
  await bridge.cacheSet(legacy,{text:'旧草稿',intents:[]})
  await bridge.cacheSet(draft,{bookId:'source-a',startAnchor:anchor,text:'新草稿',intents:[]})
  await bridge.cacheSet(intent,{bookId:'source-a',branchId:'branch-a',key:'durable-choice',body:{action:'restore_original',expectedTargetHeadRevisionId:'revision-a'}})
  await bridge.cacheSet('selection-draft:other-branch:other-revision:p:0:1',{bookId:'other-book',text:'另一书草稿'})
  book.markBookDeleted('source-a')
  for(const key of [legacy,draft,intent]){expect(await bridge.cacheGet(key)).toBeUndefined();await expect(bridge.cacheSet(key,{text:'晚到输入'})).rejects.toMatchObject({code:'BOOK_DELETED'})}
  await expect(api('/revisions/revision-a/selection-edit')).rejects.toMatchObject({code:'BOOK_DELETED'});expect(fetch).not.toHaveBeenCalled()
  await bridge.purgeBookCache('source-a',()=>true)
  for(const key of [legacy,draft,intent])expect(fixture.values.has('owner-a:'+key)).toBe(false)
  expect(await bridge.cacheGet('selection-draft:other-branch:other-revision:p:0:1')).toMatchObject({text:'另一书草稿'})
})
it('rejects a delayed actual API JSON reply after deletion without returning or caching its old body',async()=>{
  const {bridge,book}=await modules(),{api}=await import('../api');let release!:(value:unknown)=>void
  vi.mocked(fetch).mockResolvedValue({ok:true,status:200,json:()=>new Promise(resolve=>{release=resolve})} as Response)
  const request=api('/books/source-a',{cache:true});const rejected=expect(request).rejects.toMatchObject({code:'BOOK_DELETED'})
  await vi.waitFor(()=>expect(release).toBeTypeOf('function'));book.markBookDeleted('source-a');release(source);await rejected
  expect(await bridge.cacheGet('api:/books/source-a',{allowDeleted:true})).toBeUndefined()
})
it('checks account scope on a late API response before marking or cleaning any book',async()=>{
  const {bridge,book}=await modules(),{api}=await import('../api');let release!:(value:unknown)=>void
  vi.mocked(fetch).mockResolvedValue({ok:false,status:404,json:()=>new Promise(resolve=>{release=resolve})} as Response)
  const request=api('/books/source-a'),rejected=expect(request).rejects.toMatchObject({code:'SESSION_CHANGED'})
  await vi.waitFor(()=>expect(release).toBeTypeOf('function'));bridge.setAccount('owner-b');release({code:'BOOK_DELETED'});await rejected
  expect(book.isBookDeleted('source-a')).toBe(false);bridge.setAccount('owner-a');expect(book.isBookDeleted('source-a')).toBe(false)
})
it('rejects old branch API and queued POST while allowing exact deletion receipt, DELETE retry and context resolver',async()=>{
  const {book}=await modules(),{api}=await import('../api');book.recordBookReferences('/books/source-a/branches',{items:[{id:'branch-a'}]});book.markBookDeleted('source-a')
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ok:true}),{status:200,headers:{'Content-Type':'application/json'}}))
  await expect(api('/branches/branch-a/chapters',{cache:true})).rejects.toMatchObject({code:'BOOK_DELETED'})
  await expect(api('/books/source-a/bookmarks',{method:'POST',body:{anchor,note:'must not send'},key:'old-note-key'})).rejects.toMatchObject({code:'BOOK_DELETED'});expect(fetch).not.toHaveBeenCalled()
  await api('/books/source-a/deletion');await api('/reading-context/resolve',{method:'POST',body:{bookId:'source-a',branchId:'branch-a',anchor}})
  await api('/books/source-a',{method:'DELETE',body:{expectedVersion:1},key:'same-delete-key'});expect(fetch).toHaveBeenCalledTimes(3)
})
it('blocks a server BOOK_DELETED network fallback even when receipt lookup is unavailable',async()=>{
  const {bridge,book}=await modules(),{api}=await import('../api');await bridge.cacheSet('api:/books/source-a',source)
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({code:'BOOK_DELETED',message:'removed'}),{status:404})).mockRejectedValue(new Error('offline'))
  await expect(api('/books/source-a',{cache:true})).rejects.toMatchObject({code:'BOOK_DELETED'})
  expect(book.deletedBook('source-a')).toMatchObject({cleanupPending:true});await expect(api('/books/source-a',{cache:true})).rejects.toMatchObject({code:'BOOK_DELETED'})
  expect(await bridge.cacheGet('api:/books/source-a')).toBeUndefined()
})
it('migrates only exact retained publication packages and unsent notes, keeps uncertain text blocked and removes originals',async()=>{
  const {bridge,book}=await modules(),offline=await import('../offline/storage')
  await offline.commitPackage(body() as any,()=>true);await offline.commitPackage(body('source-a','pub-branch') as any,()=>true);await offline.commitPackage(body('other-book','other-branch') as any,()=>true)
  await bridge.cacheSet(offline.queueKey,[{id:'original',anchor,note:'remove original',kind:'create',key:'key-original',baseVersion:1,attempted:false},{id:'unsent',anchor:{...anchor,branchId:'pub-branch'},note:'retain unsent',kind:'create',key:'stable-unsent',baseVersion:1,attempted:false},{id:'uncertain',anchor:{...anchor,branchId:'pub-branch'},note:'retain unknown',kind:'create',key:'stable-uncertain',baseVersion:1,attempted:true}])
  const carrier={...source,id:'carrier-book',title:'公开保留故事',originalBranchId:'pub-branch'}
  await book.invalidateBookCache('source-a',[{bookId:carrier.id,storyId:'world-owned',branchIds:['pub-branch'],book:carrier as any}])
  expect(book.deletedBook('source-a')?.cleanupPending).toBe(false)
  expect([...storageValues.values()].some(value=>value.includes('公开保留故事'))).toBe(false)
  expect((await offline.entries()).map(item=>item.bookId).sort()).toEqual(['carrier-book','other-book'])
  const kept=await offline.readPackage(offline.packageId('carrier-book','pub-branch','revision-a'));expect(kept?.payload.book.title).toBe('公开保留故事');expect(kept?.payload.chapters[0].blocks[0].startAnchor.bookId).toBe('carrier-book')
  const queue=await offline.noteQueue();expect(queue).toHaveLength(2);expect(queue.find(item=>item.id==='unsent')).toMatchObject({anchor:{bookId:'carrier-book'},key:'stable-unsent',attempted:false});expect(queue.find(item=>item.id==='uncertain')).toMatchObject({anchor:{bookId:'carrier-book'},key:'stable-uncertain',attempted:true,blocked:expect.stringContaining('待确认')})
  expect(await offline.readPackage(offline.packageId('source-a','branch-a','revision-a'))).toBeUndefined()
})
it('uses the native key index to remove actual hashed files and prevents a late asset write from recreating them',async()=>{
  fixture.native=true;const {bridge,book}=await modules();await bridge.cacheSet('asset:source-a:image','original pixels');await bridge.cacheSet('asset:other-book:image','other pixels')
  const sourcePath=fixture.writes.find(path=>fixture.files.get(path)==='"original pixels"')!,otherPath=fixture.writes.find(path=>fixture.files.get(path)==='"other pixels"')!
  expect([...fixture.files.values()].some(value=>value.includes('asset:source-a:image'))).toBe(true)
  let release!:()=>void,held=false;fixture.pause=async path=>{if(path===sourcePath&&!held){held=true;await new Promise<void>(resolve=>{release=resolve})}}
  const late=bridge.cacheSet('asset:source-a:image','late pixels'),rejected=expect(late).rejects.toMatchObject({code:'BOOK_DELETED'})
  await vi.waitFor(()=>expect(release).toBeTypeOf('function'));book.markBookDeleted('source-a');release();await rejected;fixture.pause=undefined
  await bridge.purgeBookCache('source-a',()=>true);expect(fixture.files.has(sourcePath)).toBe(false);expect(fixture.files.has(otherPath)).toBe(true);expect(fixture.deletes).toContain(sourcePath)
})
it('prevents an old queued cache transform from adding source notes after the persistent tombstone',async()=>{
  const {bridge,book}=await modules();book.markBookDeleted('source-a')
  await bridge.cacheUpdate<any[]>('offline-note-queue:v1',()=>[{anchor,note:'late source'}, {anchor:{...anchor,bookId:'other-book'},note:'other note'}])
  expect(await bridge.cacheGet<any[]>('offline-note-queue:v1')).toMatchObject([{note:'other note'}]);expect(fixture.values.get('owner-a:offline-note-queue:v1')).toMatchObject([{note:'other note'}])
})
it('does not let account A to B to A reuse an old cleanup scope',async()=>{
  const {bridge,book}=await modules();const valid=book.captureBookCacheScope();bridge.setAccount('owner-b');bridge.setAccount('owner-a');expect(valid()).toBe(false)
  await expect(bridge.purgeBookCache('source-a',valid)).rejects.toThrow('账号或服务器已变化')
})
it('keeps canonical carrier branch actions writable after stale source cache reads while rejecting old task receipts',async()=>{
  const {bridge,book}=await modules();book.recordBookReferences('/books/source-a/branches',{items:[{id:'pub-branch'}]})
  book.recordBookReferences('/books/source-a/character-chats',{id:'old-session',bookId:'source-a',pendingTaskId:'old-task'})
  await bridge.cacheSet('api:/books/source-a/branches',{items:[{id:'pub-branch'}]})
  book.markBookDeleted('source-a',[{bookId:'carrier',storyId:'world-owned',branchIds:['pub-branch']}])
  book.recordBookReferences('/books/carrier',{id:'carrier',title:'carrier',originalBranchId:'pub-branch',originalRevisionId:'pub-revision'})
  await bridge.cacheGet('api:/books/source-a/branches',{allowDeleted:true})
  expect(book.bookRequestDeleted('/branches/pub-branch/chapters')).toBeUndefined()
  book.recordBookReferences('/story-runs',{id:'new-run',taskId:'new-task'},{anchor:{...anchor,bookId:'carrier',branchId:'pub-branch'}})
  await bridge.cacheSet('generation:pub-branch',{taskId:'new-task'});expect(await bridge.cacheGet('generation:pub-branch')).toEqual({taskId:'new-task'})
  await expect(bridge.cacheSet('generation:pub-branch',{taskId:'old-task'})).rejects.toMatchObject({code:'BOOK_DELETED'})
})
it('persists pending cleanup on native failure and retries only indexed source files without leaving carrier titles in receipts',async()=>{
  fixture.native=true;const {bridge,book}=await modules();await bridge.cacheSet('asset:source-a:image','source pixels');await bridge.cacheSet('asset:other-book:image','other pixels')
  const path=fixture.writes.find(path=>fixture.files.get(path)==='"source pixels"')!;fixture.failDeletes.add(path)
  await expect(book.invalidateBookCache('source-a')).rejects.toThrow('permission denied');expect(book.deletedBook('source-a')?.cleanupPending).toBe(true)
  expect(await bridge.cacheGet('asset:source-a:image')).toBeUndefined();expect(await bridge.cacheGet('asset:other-book:image')).toBe('other pixels')
  fixture.failDeletes.clear();await book.invalidateBookCache('source-a');expect(book.deletedBook('source-a')?.cleanupPending).toBe(false);expect(fixture.files.has(path)).toBe(false)
  expect([...storageValues.values()].some(value=>value.includes('私有原文'))).toBe(false)
})
it('reopens an unknown deletion receipt before touching pending publication packages and resumes exact cleanup',async()=>{
  const {bridge,book}=await modules(),offline=await import('../offline/storage')
  await offline.commitPackage(body('source-a','pub-branch') as any,()=>true);book.markBookDeleted('source-a')
  vi.mocked(fetch).mockRejectedValueOnce(new Error('offline'))
  await book.resumeDeletedBookCleanup();expect(book.deletedBook('source-a')).toMatchObject({cleanupPending:true,retainedKnown:false})
  expect(await bridge.cacheGet(offline.packageKey(offline.packageId('source-a','pub-branch','revision-a')),{allowDeleted:true})).toBeDefined()
  vi.mocked(fetch).mockImplementation(async address=>new Response(JSON.stringify(String(address).endsWith('/deletion')?{retainedStories:[{bookId:'carrier',storyId:'world-owned',branchIds:['pub-branch']}]}:{...source,id:'carrier',originalBranchId:'pub-branch'}),{status:200}))
  await book.resumeDeletedBookCleanup();expect(book.deletedBook('source-a')).toMatchObject({cleanupPending:false,retainedKnown:true})
  expect((await offline.entries()).map(item=>item.bookId)).toEqual(['carrier']);expect(vi.mocked(fetch).mock.calls.map(args=>String(args[0]))).toEqual(['/api/reader/v1/books/source-a/deletion','/api/reader/v1/books/source-a/deletion','/api/reader/v1/books/carrier'])
})
it('filters a shared native queue after a tombstone arrives during the actual SDK write',async()=>{
  fixture.native=true;const {bridge,book}=await modules();await bridge.cacheSet('offline-note-queue:v1',[])
  const queuePath=fixture.writes.find(path=>fixture.files.get(path)==='[]')!;let release!:()=>void,held=false
  fixture.pause=async path=>{if(path===queuePath&&!held){held=true;await new Promise<void>(resolve=>{release=resolve})}}
  const late=bridge.cacheSet('offline-note-queue:v1',[{anchor,note:'old source'},{anchor:{...anchor,bookId:'other-book'},note:'keep other'}])
  await vi.waitFor(()=>expect(release).toBeTypeOf('function'));book.markBookDeleted('source-a');release();await late
  expect(JSON.parse(fixture.files.get(queuePath)!)).toMatchObject([{note:'keep other'}]);expect(await bridge.cacheGet<any[]>('offline-note-queue:v1')).toHaveLength(1)
})
it('does not revoke or replay under a newer owner when an old refresh response arrives',async()=>{
  const {bridge}=await modules(),client=await import('../api');let release!:(value:unknown)=>void
  client.setAccess('old-owner-token')
  vi.mocked(fetch).mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce({ok:true,status:200,json:()=>new Promise(resolve=>{release=resolve})} as Response)
  const request=client.api('/books/source-a'),rejected=expect(request).rejects.toMatchObject({code:'SESSION_CHANGED'})
  await vi.waitFor(()=>expect(release).toBeTypeOf('function'));bridge.setAccount('owner-b');client.setAccess('new-owner-token');release({accessToken:'stale-token',user:{id:'owner-a'}});await rejected
  expect(bridge.accountId()).toBe('owner-b');expect(client.authorizationHeaders()).toEqual({Authorization:'Bearer new-owner-token'});expect(fetch).toHaveBeenCalledTimes(2)
})

it.each([false,true])('preserves another pending retained publication while deleting only B, native=%s',async native=>{
  fixture.native=native;if(native)vi.stubEnv('VITE_API_BASE_URL','https://synthetic-cache.example');const {bridge,book}=await modules(),offline=await import('../offline/storage')
  const id=offline.packageId('source-a','pub-branch','revision-a'),key=offline.packageKey(id)
  await offline.commitPackage(body('source-a','pub-branch') as any,()=>true)
  await bridge.cacheSet(offline.queueKey,[{id:'retain-unsent',anchor:{...anchor,branchId:'pub-branch'},note:'待迁移的私人笔记',kind:'create',key:'stable-retained',baseVersion:1,attempted:false}])
  book.markBookDeleted('source-a');await book.invalidateBookCache('book-b',[])
  expect(await bridge.cacheGet(key,{allowDeleted:true})).toBeDefined()
  expect(await bridge.cacheGet<any[]>(offline.queueKey,{allowDeleted:true})).toHaveLength(1)
  expect((await offline.entries(true)).map(item=>item.id)).toContain(id)
  expect(await offline.entries()).toEqual([]);expect(await offline.noteQueue()).toEqual([])
  vi.mocked(fetch).mockImplementation(async address=>new Response(JSON.stringify(String(address).endsWith('/deletion')?{retainedStories:[{bookId:'carrier',storyId:'world-owned',branchIds:['pub-branch']}]}:{...source,id:'carrier',originalBranchId:'pub-branch'}),{status:200}))
  await book.resumeDeletedBookCleanup()
  expect(await offline.readPackage(offline.packageId('carrier','pub-branch','revision-a'))).toBeDefined()
  expect(await offline.noteQueue()).toMatchObject([{note:'待迁移的私人笔记',key:'stable-retained',anchor:{bookId:'carrier'}}])
})
it.each([false,true])('preserves quarantined existing rows during unrelated shared writes while rejecting late deleted rows, native=%s',async native=>{
  fixture.native=native;if(native)vi.stubEnv('VITE_API_BASE_URL','https://synthetic-cache.example');const {bridge,book}=await modules(),offline=await import('../offline/storage')
  const old={id:'existing-a',anchor,note:'已保存待迁移',kind:'create',key:'existing-a-key',baseVersion:1,attempted:false}
  await bridge.cacheSet(offline.queueKey,[old]);book.markBookDeleted('source-a')
  const fresh={id:'late-a',anchor,note:'不能迟到新增',kind:'create',key:'late-a-key',baseVersion:1,attempted:false},other={...old,id:'other',anchor:{...anchor,bookId:'book-b'}}
  await bridge.cacheUpdate<any[]>(offline.queueKey,visible=>[...(visible||[]),other,fresh])
  expect(await bridge.cacheGet<any[]>(offline.queueKey,{allowDeleted:true})).toEqual([other,old])
  expect(await offline.noteQueue()).toEqual([other])
  await bridge.cacheSet(offline.queueKey,[{...old,note:'不能迟到修改'},other,fresh])
  expect(await bridge.cacheGet<any[]>(offline.queueKey,{allowDeleted:true})).toEqual([other,old])
})

it('keeps pure generation capability/receipt routes independent of a different deleted book',async()=>{
  const {book}=await modules()
  book.recordBookReferences('/generations/request-status?requestId=a&bookId=source-a',{found:true,receipt:{generationId:'batch-a',taskId:'parent-a'}})
  book.recordBookReferences('/generations/request-status?requestId=b&bookId=other-book',{found:true,receipt:{generationId:'batch-b',taskId:'parent-b'}})
  book.markBookDeleted('source-a')
  expect(book.bookRequestDeleted('/generations/request-status?requestId=b&bookId=other-book')).toBeUndefined()
  expect(book.bookRequestDeleted('/generations/capabilities')).toBeUndefined()
  expect(book.bookRequestDeleted('/generations/batch-a')).toBe('source-a')
  expect(book.bookRequestDeleted('/generations/batch-b')).toBeUndefined()
})
it('purges generation intents and unknown request history while rejecting their late recreation',async()=>{
  const {bridge,book}=await modules();book.recordBookReferences('/books/source-a/branches',{items:[{id:'branch-a'}]})
  const pending={bookId:'source-a',intent:{key:'request-a',request:{originAnchor:anchor,instruction:'私有愿望'}}}
  await bridge.cacheSet('generation-stream:branch-a',pending);await bridge.cacheSet('generation-unconfirmed:branch-a',{bookId:'source-a',records:[pending]});await bridge.cacheSet('generation-stream:other-branch',{bookId:'other-book',text:'其他书草稿'})
  book.markBookDeleted('source-a');await bridge.purgeBookCache('source-a',()=>true)
  expect(await bridge.cacheGet('generation-stream:branch-a',{allowDeleted:true})).toBeUndefined();expect(await bridge.cacheGet('generation-unconfirmed:branch-a',{allowDeleted:true})).toBeUndefined()
  await expect(bridge.cacheSet('generation-stream:branch-a',pending)).rejects.toMatchObject({code:'BOOK_DELETED'});expect(await bridge.cacheGet('generation-stream:other-branch')).toMatchObject({text:'其他书草稿'})
})
it('keeps a bookmark tombstone exact to its owner/backend/book/id and broadcasts another-tab removal',async()=>{
  const {bridge,book}=await modules(),notes=await import('./bookmark-cache');notes.markBookmarkDeleted('source-a','note-a')
  expect(notes.isBookmarkDeleted('source-a','note-a')).toBe(true);expect(notes.isBookmarkDeleted('source-a','note-b')).toBe(false);expect(notes.isBookmarkDeleted('book-b','note-a')).toBe(false);expect(book.isBookDeleted('source-a')).toBe(false)
  bridge.setAccount('owner-b');expect(notes.isBookmarkDeleted('source-a','note-a')).toBe(false);bridge.setAccount('owner-a')
  const key=[...storageValues.keys()].find(key=>key.startsWith('reader-bookmark-deleted:'))!,event=new Event('storage');Object.defineProperties(event,{key:{value:key},newValue:{value:storageValues.get(key)}});const observed=vi.fn();window.addEventListener('reader-bookmark-deleted',observed);window.dispatchEvent(event);expect(observed).toHaveBeenCalledTimes(1)
  vi.resetModules();await modules();const reloaded=await import('./bookmark-cache');expect(reloaded.isBookmarkDeleted('source-a','note-a')).toBe(true)
})
it('rejects late bookmark reads while keeping the book readable and pure deletion receipts accessible',async()=>{
  await modules();const client=await import('../api'),notes=await import('./bookmark-cache');let release!:(value:Response)=>void
  vi.mocked(fetch).mockReturnValueOnce(new Promise(resolve=>{release=resolve}));const request=client.api('/books/source-a/bookmarks/note-a'),rejected=expect(request).rejects.toMatchObject({code:'BOOKMARK_DELETED'})
  notes.markBookmarkDeleted('source-a','note-a');release(new Response(JSON.stringify({id:'note-a',anchor,note:'迟到私人笔记',version:1})));await rejected
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(source)));expect(await client.api('/books/source-a')).toMatchObject({title:'私有原文'})
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({found:false})));expect(await client.api('/books/source-a/bookmarks/note-a/deletion?requestId=original-key')).toEqual({found:false})
})
it('filters deleted bookmark lists and offline ranges while preserving other notes and unsent local words',async()=>{
  const {bridge}=await modules(),notes=await import('./bookmark-cache'),offline=await import('../offline/storage'),client=await import('../api')
  const data=body();data.bookmarks.push({...data.bookmarks[0],id:'note-b',note:'另一条笔记'});await offline.commitPackage(data as any,()=>true)
  await bridge.cacheSet(offline.queueKey,[{id:'note-a',remoteId:'note-a',anchor,note:'删除前尚未提交的草稿',kind:'update',key:'local-edit',baseVersion:1,attempted:false}])
  notes.markBookmarkDeleted('source-a','note-a');const local=await offline.localNotes(anchor.bookId,anchor.branchId,anchor.revisionId,data.bookmarks as any)
  expect(local.map(item=>item.id)).toEqual(['note-b','deleted-local:note-a']);expect(local[1]).toMatchObject({note:'删除前尚未提交的草稿',status:'deleted_original'})
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({items:data.bookmarks,nextCursor:null})));expect(await client.allPages('/notebook')).toEqual([data.bookmarks[1]])
  const original=(await offline.readPackage(offline.packageId('source-a','branch-a','revision-a')))!.payload;expect(original.chapters).toEqual(data.chapters);expect(original.progress).toEqual(data.progress)
  const sync=await import('../offline/notes');vi.mocked(fetch).mockClear();expect(await sync.syncNotes()).toEqual({synced:0,remaining:1});expect(fetch).not.toHaveBeenCalled()
})
it('handles BOOKMARK_DELETED on replay without creating a whole-book tombstone',async()=>{
  const {book}=await modules(),notes=await import('./bookmark-cache'),client=await import('../api')
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({code:'BOOKMARK_DELETED',message:'已删除',details:{bookId:'source-a',bookmarkId:'note-a'}}),{status:404}))
  await expect(client.api('/books/source-a/bookmarks',{method:'POST',key:'original-create',body:{anchor,note:'重放'}})).rejects.toMatchObject({code:'BOOKMARK_DELETED'})
  expect(notes.isBookmarkDeleted('source-a','note-a')).toBe(true);expect(book.isBookDeleted('source-a')).toBe(false);expect(notes.isBookmarkDeleted('book-b','note-a')).toBe(false)
})
it('filters a deletion that lands during the actual asynchronous native cache write before returning the list',async()=>{
  fixture.native=true;vi.stubEnv('VITE_API_BASE_URL','https://synthetic-cache.example');await modules();const client=await import('../api'),notes=await import('./bookmark-cache')
  let release!:()=>void,started=false;fixture.pause=async()=>{started=true;await new Promise<void>(resolve=>{release=resolve});fixture.pause=undefined}
  const items=[{id:'note-a',anchor,note:'迟到笔记'},{id:'note-b',anchor,note:'应当保留'}];vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({items,nextCursor:null})))
  const response=client.api<{items:unknown[]}>('/notebook',{cache:true});await vi.waitFor(()=>expect(started).toBe(true));notes.markBookmarkDeleted('source-a','note-a');release()
  expect((await response).items).toEqual([items[1]])
})
