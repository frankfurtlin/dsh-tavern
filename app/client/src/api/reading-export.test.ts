import {it,expect,vi,beforeEach,afterEach} from 'vitest'
import {createHash} from 'node:crypto'
const mock=vi.hoisted(()=>({owner:'a'.repeat(32),base:'https://fixture.invalid',epoch:1,deleted:false,lineDeleted:false,native:false,values:new Map<string,unknown>(),api:vi.fn(),refresh:vi.fn(),cap:vi.fn()}))
vi.mock('./index',()=>({api:mock.api,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}},authGeneration:()=>mock.epoch,authorizationHeaders:()=>({Authorization:'Bearer own-fixture-only'}),refreshAuth:mock.refresh}))
vi.mock('../platform/bridge',()=>({accountId:()=>mock.owner,backendBase:()=>mock.base,get native(){return mock.native},cacheGet:async(key:string)=>mock.values.get(mock.owner+':'+mock.base+':'+key),cacheSet:async(key:string,value:unknown)=>mock.values.set(mock.owner+':'+mock.base+':'+key,value)}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>mock.deleted}))
vi.mock('../platform/world-cache',()=>({isWorldLineDeleted:()=>mock.lineDeleted}))
vi.mock('./backups',()=>({backupCapabilities:mock.cap}))
vi.mock('../platform/reader-backup',()=>({nativeReadingExportCapabilities:vi.fn().mockResolvedValue(null),downloadNativeReadingExport:vi.fn().mockRejectedValue(Object.assign(new Error('旧安装包尚未提供阅读文件保存'),{code:'EXPORT_NATIVE_UNAVAILABLE'}))}))
import {captureReadingExportScope,createReadingExport,queryReadingExport,saveReadingIntent,loadReadingIntent,validateReadingJob,validateReadingRequest,writeReadingResponse,downloadReadingExport,readingExportAvailable,type ReadingExportIntent,type ReadingExportJob} from './reading-export'
const scope={kind:'book' as const,bookId:'b'.repeat(32),branchId:'c'.repeat(32),revisionId:'d'.repeat(32)},world={kind:'world' as const,worldId:'b'.repeat(32),lineId:'c'.repeat(32),checkpointId:'d'.repeat(32)}
const request={format:'txt' as const,scope,range:'observed' as const,allowUnseen:false},intent:ReadingExportIntent={schema:1,requestId:'same-public-key',request}
const raw=new TextEncoder().encode('第一章\n甲😀乙\n'),sha=createHash('sha256').update(raw).digest('hex')
const job:ReadingExportJob={id:'e'.repeat(32),status:'ready',version:2,expiresAt:'2099-01-01T00:00:00Z',protocol:'reader-export-v1',format:'txt',fileName:'我的故事.txt',sha256:sha,size:raw.length,chapterCount:1,imageCount:0}
beforeEach(()=>{mock.owner='a'.repeat(32);mock.base='https://fixture.invalid';mock.epoch++;mock.deleted=false;mock.lineDeleted=false;mock.native=false;mock.values.clear();mock.api.mockReset();mock.refresh.mockReset();mock.cap.mockReset();mock.cap.mockResolvedValue({ready:true});vi.stubGlobal('window',{});vi.stubGlobal('document',{createElement:vi.fn(),body:{append:vi.fn()}})})
afterEach(()=>vi.unstubAllGlobals())
it('keeps a lost POST request and reopens with pure GET rather than creating another export',async()=>{
  await saveReadingIntent(scope,intent);mock.api.mockRejectedValueOnce(new Error('response lost'))
  await expect(createReadingExport(request,intent.requestId)).rejects.toThrow('lost');const restored=await loadReadingIntent(scope)
  mock.api.mockResolvedValueOnce({found:true,job});expect(await queryReadingExport(restored!)).toEqual(job)
  expect(mock.api.mock.calls).toEqual([['/exports',{method:'POST',body:request,key:intent.requestId,retry:false}],['/exports/requests?requestId=same-public-key']])
})
it('validates explicit chapter consent without progress writes, hidden state or guessed scopes',()=>{
  expect(validateReadingRequest({...request,scope:{...scope,privateGoals:'secret'},definition:'secret'})).toEqual(request)
  expect(()=>validateReadingRequest({...request,allowUnseen:true})).toThrow()
  expect(()=>validateReadingRequest({...request,range:'selected',startChapter:0,endChapter:3})).toThrow()
  expect(validateReadingRequest({...request,scope:world,range:'selected',startChapter:1,endChapter:3,allowUnseen:true})).toEqual({...request,scope:world,range:'selected',startChapter:1,endChapter:3,allowUnseen:true})
  expect(mock.api).not.toHaveBeenCalled()
})
it('projects finite jobs and rejects a malformed ready file instead of exposing internal fields',()=>{
  expect(validateReadingJob({...job,definition:{secret:true},password:'hidden'})).toEqual(job)
  expect(()=>validateReadingJob({...job,fileName:'../secret.txt'})).toThrow()
  expect(()=>validateReadingJob({...job,sha256:undefined})).toThrow()
  expect(()=>validateReadingJob({...job,format:'epub'},{format:'txt'})).toThrow()
})
it('rejects delayed old owner results and authoritative source or world-line tombstones',async()=>{
  let resolve!:(value:unknown)=>void;mock.api.mockReturnValueOnce(new Promise(done=>resolve=done));const pending=queryReadingExport(intent);mock.owner='f'.repeat(32);mock.epoch++;resolve({found:true,job});await expect(pending).rejects.toThrow('账号')
  const source=captureReadingExportScope(scope),line=captureReadingExportScope(world);mock.deleted=true;mock.lineDeleted=true;expect(source()).toBe(false);expect(line()).toBe(false)
})
it('persists public IDs and the unchanged request in an owner/backend scope without private source payload',async()=>{
  await saveReadingIntent(scope,{...intent,request:{...request,definition:'secret'} as any});expect(JSON.stringify([...mock.values.values()])).not.toContain('secret');mock.base='https://other.invalid';expect(await loadReadingIntent(scope)).toBeNull();mock.base='https://fixture.invalid';mock.owner='f'.repeat(32);expect(await loadReadingIntent(scope)).toBeNull()
})
function response(bytes=raw,type='text/plain; charset=utf-8'){return new Response(new ReadableStream({start(c){c.enqueue(bytes.subarray(0,5));c.enqueue(bytes.subarray(5));c.close()}}),{headers:{'Content-Type':type,'Content-Length':String(bytes.length)}})}
it('streams each chunk and commits the writer only after matching exact bytes and SHA-256',async()=>{
  const chunks:Uint8Array[]=[],writer={write:vi.fn(async(bytes:Uint8Array)=>{chunks.push(bytes)}),close:vi.fn(),abort:vi.fn()},progress=vi.fn();await writeReadingResponse(response(),job,writer,()=>true,progress)
  expect(writer.write).toHaveBeenCalledTimes(2);expect(writer.close).toHaveBeenCalledOnce();expect(writer.abort).not.toHaveBeenCalled();expect(Buffer.concat(chunks)).toEqual(Buffer.from(raw));expect(progress).toHaveBeenLastCalledWith(raw.length)
})
it('aborts without committing on corruption, truncation, wrong content type or late account change',async()=>{
  for(const mode of ['hash','short','type','owner']){
    const writer={write:vi.fn(),close:vi.fn(),abort:vi.fn()},active=vi.fn(()=>true);if(mode==='owner')writer.write.mockImplementation(async()=>{active.mockReturnValue(false)})
    const bytes=mode==='hash'?new Uint8Array(raw.length):mode==='short'?raw.subarray(0,3):raw
    await expect(writeReadingResponse(response(bytes,mode==='type'?'application/octet-stream':'text/plain'),job,writer,active)).rejects.toThrow();expect(writer.close).not.toHaveBeenCalled();expect(writer.abort).toHaveBeenCalledOnce()
  }
})
it('downloads a verified authenticated file through a real writer seam and never follows redirects',async()=>{
  const writer={write:vi.fn(),close:vi.fn(),abort:vi.fn()},picker=vi.fn().mockResolvedValue({createWritable:async()=>writer}),fetcher=vi.fn().mockResolvedValue(response());vi.stubGlobal('window',{showSaveFilePicker:picker});vi.stubGlobal('fetch',fetcher)
  await downloadReadingExport(job,()=>true);expect(picker).toHaveBeenCalledWith(expect.objectContaining({suggestedName:job.fileName}));expect(fetcher).toHaveBeenCalledWith(mock.base+'/api/reader/v1/exports/'+job.id+'/download',expect.objectContaining({redirect:'error',credentials:'include'}));expect(writer.close).toHaveBeenCalledOnce()
})
it('keeps missing export readiness and native reading-file support unavailable without pretending a save',async()=>{
  mock.api.mockResolvedValueOnce(null);expect(await readingExportAvailable()).toBe(false);mock.native=true;expect(await readingExportAvailable()).toBe(false);await expect(downloadReadingExport(job,()=>true)).rejects.toMatchObject({code:'EXPORT_NATIVE_UNAVAILABLE'});expect(mock.api.mock.calls).toEqual([['/exports/capabilities'],['/exports/capabilities']])
})
it('requires the public export capability contract without probing private backup readiness or creating jobs',async()=>{
  const capability={protocol:'reader-export-v1',ready:true,formats:['txt','epub'],defaultRange:'observed',selectedUnseenRequiresConfirmation:true,maxBytes:2147483648}
  mock.api.mockResolvedValueOnce(capability);expect(await readingExportAvailable()).toBe(true);expect(mock.api).toHaveBeenCalledExactlyOnceWith('/exports/capabilities');expect(mock.cap).not.toHaveBeenCalled()
  mock.api.mockResolvedValueOnce({...capability,ready:false});expect(await readingExportAvailable()).toBe(false)
  mock.api.mockResolvedValueOnce({...capability,selectedUnseenRequiresConfirmation:false});expect(await readingExportAvailable()).toBe(false)
  mock.api.mockResolvedValueOnce({...capability,protocol:'private-backup-v1'});expect(await readingExportAvailable()).toBe(false)
  const calls=mock.api.mock.calls.length;mock.native=true;mock.api.mockResolvedValueOnce(capability);expect(await readingExportAvailable()).toBe(false);expect(mock.api.mock.calls).toHaveLength(calls+1)
})
