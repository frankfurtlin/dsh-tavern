import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mock=vi.hoisted(()=>({owner:'a'.repeat(32),base:'https://owned.invalid',epoch:1,native:true,deleted:false,lineDeleted:false,api:vi.fn(),cap:vi.fn(),save:vi.fn()}))
vi.mock('./index',()=>({api:mock.api,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}},authGeneration:()=>mock.epoch,authorizationHeaders:()=>({Authorization:'Bearer owned-in-memory-fixture'}),refreshAuth:vi.fn()}))
vi.mock('../platform/bridge',()=>({accountId:()=>mock.owner,backendBase:()=>mock.base,get native(){return mock.native},cacheGet:vi.fn(),cacheSet:vi.fn()}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>mock.deleted}))
vi.mock('../platform/world-cache',()=>({isWorldLineDeleted:()=>mock.lineDeleted}))
vi.mock('../platform/reader-backup',()=>({nativeReadingExportCapabilities:mock.cap,downloadNativeReadingExport:mock.save}))
import {readingExportAvailable,downloadReadingExport,captureReadingExportScope} from './reading-export'
const scope={kind:'book' as const,bookId:'b'.repeat(32),branchId:'c'.repeat(32),revisionId:'d'.repeat(32)}
const server={protocol:'reader-export-v1',ready:true,formats:['txt','epub'],defaultRange:'observed',selectedUnseenRequiresConfirmation:true,maxBytes:2147483648}
const job={id:'e'.repeat(32),status:'ready' as const,version:1,expiresAt:'2099-01-01T00:00:00Z',protocol:'reader-export-v1' as const,format:'txt' as const,fileName:'专有正文.txt',sha256:'f'.repeat(64),size:128*1024**2,chapterCount:3}
beforeEach(()=>{mock.owner='a'.repeat(32);mock.base='https://owned.invalid';mock.epoch=1;mock.native=true;mock.deleted=false;mock.lineDeleted=false;mock.api.mockReset().mockResolvedValue(server);mock.cap.mockReset().mockResolvedValue({ready:true,maxReadingBytes:2147483648});mock.save.mockReset().mockResolvedValue(undefined);vi.stubGlobal('window',{});vi.stubGlobal('fetch',vi.fn(()=>{throw new Error('file bytes must stay native')}))})
afterEach(()=>vi.unstubAllGlobals())
it('enables native public export only when the backend and signed native reading channel both support it',async()=>{
  expect(await readingExportAvailable()).toBe(true)
  expect(mock.api).toHaveBeenCalledExactlyOnceWith('/exports/capabilities');expect(mock.cap).toHaveBeenCalledOnce()
})
it('uses native system save for a verified ready 128MiB job without fetching or Base64 in JS',async()=>{
  const progress=vi.fn();mock.save.mockImplementationOnce(async(_metadata,received)=>received(65536))
  await downloadReadingExport({...job,privateState:'must-not-transfer'} as any,captureReadingExportScope(scope),progress)
  expect(mock.save).toHaveBeenCalledExactlyOnceWith({operationId:job.id,format:job.format,size:job.size,sha256:job.sha256,fileName:job.fileName},expect.any(Function),expect.any(Function));expect(progress).toHaveBeenCalledExactlyOnceWith(65536);expect(fetch).not.toHaveBeenCalled();expect(mock.api).not.toHaveBeenCalled()
})
it.each(['backend-missing','native-old','protocol'])('keeps unavailable capability safe without creating an export (%s)',async mode=>{
  if(mode==='backend-missing')mock.api.mockResolvedValueOnce({...server,ready:false})
  else if(mode==='native-old')mock.cap.mockResolvedValueOnce(null)
  else mock.api.mockResolvedValueOnce({...server,protocol:'reader-private-backup-v1'})
  expect(await readingExportAvailable()).toBe(false);expect(mock.save).not.toHaveBeenCalled();expect(mock.api.mock.calls.every(([path])=>path==='/exports/capabilities')).toBe(true)
})
it.each(['owner','base','epoch'])('drops capability readiness from the old %s scope',async field=>{
  let resolve!:(v:unknown)=>void;mock.api.mockReturnValueOnce(new Promise(done=>resolve=done));const result=readingExportAvailable()
  if(field==='owner')mock.owner='9'.repeat(32);else if(field==='base')mock.base='https://another.invalid';else mock.epoch++
  resolve(server);expect(await result).toBe(false);expect(mock.cap).not.toHaveBeenCalled()
})
it('rejects expired or preparing jobs before any native file method',async()=>{
  await expect(downloadReadingExport({...job,expiresAt:'2000-01-01T00:00:00Z'},()=>true)).rejects.toThrow('过期')
  await expect(downloadReadingExport({...job,status:'preparing'},()=>true)).rejects.toThrow('过期')
  expect(mock.save).not.toHaveBeenCalled()
})
it('drops late native progress and success after source deletion without writing progress or replaying',async()=>{
  const valid=captureReadingExportScope(scope),progress=vi.fn();mock.save.mockImplementationOnce(async(_metadata,received)=>{mock.deleted=true;received(65536)})
  await expect(downloadReadingExport(job,valid,progress)).rejects.toThrow('账号或故事');expect(progress).not.toHaveBeenCalled();expect(mock.save).toHaveBeenCalledOnce();expect(mock.api).not.toHaveBeenCalled()
})
it('cannot bind an old ready job to a new owner while the native module is being obtained',async()=>{
  const pending=downloadReadingExport(job,()=>true);mock.owner='9'.repeat(32);mock.epoch++
  await expect(pending).rejects.toThrow('账号或故事');expect(mock.save).not.toHaveBeenCalled()
})
