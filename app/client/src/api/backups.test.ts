import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mock=vi.hoisted(()=>({owner:'a'.repeat(32),epoch:1,base:'https://backup.invalid',values:new Map<string,unknown>(),deleted:false,api:vi.fn()}))
vi.mock('./index',()=>({api:mock.api,authGeneration:()=>mock.epoch,authorizationHeaders:()=>({Authorization:'Bearer synthetic'}),refreshAuth:vi.fn(async()=>false),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({native:false,accountId:()=>mock.owner,backendBase:()=>mock.base,cacheGet:async(key:string)=>mock.values.get(key),cacheSet:async(key:string,value:unknown)=>{mock.values.set(key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>mock.deleted}))
vi.mock('../platform/world-cache',()=>({isWorldDeleted:()=>mock.deleted}))
vi.mock('../platform/reader-backup',()=>({nativeBackupCapabilities:vi.fn(),prepareNativeBackup:vi.fn(),downloadNativeBackup:vi.fn()}))
import {BACKUP_PROTOCOL,backupCapabilities,backupIntentKey,captureBackupScope,confirmPrivateBackup,downloadPrivateBackup,exportPrivateBackup,lookupBackup,preparePrivateBackup,saveBackupIntent,validateBackupOperation,writeBackupResponse,type BackupIntent,type BackupOperation} from './backups'
const storyId='book-'+'b'.repeat(32),operationId='c'.repeat(32)
const operation=(kind:'export'|'restore'='restore'):BackupOperation=>({id:operationId,kind,status:'ready',version:2,counts:{book:1,bookmark:2},expiresAt:'2099-01-01T00:00:00Z',protocol:BACKUP_PROTOCOL,...(kind==='restore'?{targetOwnerId:mock.owner,objectType:'book'}:{})})
const request:BackupIntent={schema:1,kind:'restore',phase:'prepare',requestId:'synthetic-key'}
beforeEach(()=>{mock.owner='a'.repeat(32);mock.epoch=1;mock.deleted=false;mock.api.mockReset();mock.values.clear();vi.stubGlobal('window',{});vi.stubGlobal('fetch',vi.fn())})
afterEach(()=>vi.unstubAllGlobals())

it('projects only operation metadata and rejects a restored receipt claiming replayed tasks or another owner',()=>{
  const value=validateBackupOperation({...operation(),secret:'not-a-dto',definition:{hidden:true}});expect(value).not.toHaveProperty('secret');expect(value).not.toHaveProperty('definition')
  expect(()=>validateBackupOperation({...operation(),targetOwnerId:'d'.repeat(32)})).toThrow()
  expect(()=>validateBackupOperation({...operation(),status:'restored',receipt:{storyId,counts:{book:1},historicalTasksReplayed:true,creditTransferred:false}})).toThrow()
})
it('persists only request identifiers and discards accidental passwords, File and hidden fields',async()=>{
  await saveBackupIntent({...request,password:'never-store',file:new File(['encrypted'],'private.srbackup'),definition:{private:true}} as BackupIntent)
  expect(mock.values.get(backupIntentKey())).toEqual(request)
  expect(JSON.stringify([...mock.values.values()])).not.toContain('never-store')
})
it('unknown export sends one keyed POST without retry and an explicit lookup remains a pure GET',async()=>{
  mock.api.mockRejectedValueOnce(new Error('response-lost'))
  await expect(exportPrivateBackup(storyId,'memory-password',false,'request1')).rejects.toThrow('response-lost')
  expect(mock.api).toHaveBeenCalledTimes(1);expect(mock.api.mock.calls[0]![1]).toMatchObject({method:'POST',retry:false,key:'request1'})
  const body=mock.api.mock.calls[0]![1].body as FormData;expect(body.get('password')).toBe('memory-password');expect(body.get('includeSource')).toBe('false')
  mock.api.mockResolvedValueOnce({found:false,operation:null});expect(await lookupBackup({schema:1,kind:'export',phase:'export',storyId,requestId:'request1'})).toBeNull()
  expect(mock.api.mock.calls.at(-1)).toEqual(['/backups/requests?kind=export&requestId=request1'])
})
it('passes an actual File through multipart without invoking full file reads or the novel picker',async()=>{
  const file=new File(['SRBKUP01-encrypted'],'story.srbackup');vi.spyOn(file,'arrayBuffer').mockImplementation(()=>{throw new Error('whole file read forbidden')});mock.api.mockResolvedValueOnce(operation())
  await preparePrivateBackup(file,'memory-only','prepare1')
  expect(mock.api.mock.calls[0]![1]).toMatchObject({method:'POST',key:'prepare1',retry:false});expect(mock.api.mock.calls[0]![1].body).toBeInstanceOf(FormData);expect((mock.api.mock.calls[0]![1].body as FormData).get('file')).toBeInstanceOf(File);expect(file.arrayBuffer).not.toHaveBeenCalled()
})
it('accepts the real story manifest objectType from a successful portable-v1 preparation',async()=>{
  mock.api.mockResolvedValueOnce({...operation(),objectType:'story',counts:{story:1,role_message:1,image_asset:7}})
  const preview=await preparePrivateBackup(new File(['SRBKUP01-cipher'],'own.srbackup'),'memory-only','actual-story-contract')
  expect(preview).toMatchObject({kind:'restore',status:'ready',objectType:'story',targetOwnerId:mock.owner,counts:{story:1,role_message:1,image_asset:7}})
})
it('does not expose a delayed status after owner change or an authoritative source tombstone',async()=>{
  let done!:(value:unknown)=>void;mock.api.mockReturnValueOnce(new Promise(resolve=>done=resolve));const pending=lookupBackup(request);mock.owner='d'.repeat(32);mock.epoch++;done({found:true,operation:operation()});await expect(pending).rejects.toThrow('账号')
  const active=captureBackupScope(storyId);mock.deleted=true;expect(active()).toBe(false)
})
it('confirms the exact preview version and current owner, never transferring credits or passing a password',async()=>{
  const preview=operation();mock.api.mockResolvedValueOnce({...preview,status:'restored',version:3,receipt:{storyId,counts:{book:1},historicalTasksReplayed:false,creditTransferred:false}})
  expect((await confirmPrivateBackup(preview,'confirm1')).receipt?.storyId).toBe(storyId)
  expect(mock.api.mock.calls[0]).toEqual(['/backups/'+operationId+'/restore',{method:'POST',key:'confirm1',retry:false,body:{confirmed:true,expectedVersion:2,targetOwnerId:mock.owner}}])
  await expect(confirmPrivateBackup({...preview,expiresAt:'2000-01-01T00:00:00Z'},'later')).rejects.toThrow('失效');expect(mock.api).toHaveBeenCalledTimes(1)
})
it('writes encrypted response chunks to a stream writer and closes only after complete length verification',async()=>{
  const parts=[new Uint8Array([1,2,3]),new Uint8Array([4,5])],write=vi.fn(async(_value:Uint8Array)=>{}),close=vi.fn(async()=>{}),abort=vi.fn(async()=>{})
  const response=new Response(new ReadableStream({start(controller){parts.forEach(value=>controller.enqueue(value));controller.close()}}),{headers:{'Content-Type':'application/octet-stream','Content-Length':'5'}})
  const progress=vi.fn();await writeBackupResponse(response,{write,close,abort},()=>true,progress)
  expect(write.mock.calls.map(([part])=>Array.from(part))).toEqual([[1,2,3],[4,5]]);expect(close).toHaveBeenCalledOnce();expect(abort).not.toHaveBeenCalled();expect(progress.mock.calls.map(([size])=>size)).toEqual([3,5])
})
it('aborts a partially written file after account change, never closing it as a completed backup',async()=>{
  const active=captureBackupScope(),close=vi.fn(async()=>{}),abort=vi.fn(async()=>{})
  const response=new Response(new Uint8Array([1,2]),{headers:{'Content-Type':'application/octet-stream','Content-Length':'2'}})
  await expect(writeBackupResponse(response,{write:async()=>{mock.epoch++},close,abort},active)).rejects.toThrow('账号');expect(abort).toHaveBeenCalledOnce();expect(close).not.toHaveBeenCalled()
})
it('rejects incomplete downloads and oversized buffering before allocating the entire file',async()=>{
  const abort=vi.fn(async()=>{}),close=vi.fn(async()=>{});await expect(writeBackupResponse(new Response(new Uint8Array([1]),{headers:{'Content-Type':'application/octet-stream','Content-Length':'2'}}),{write:async()=>{},close,abort},()=>true)).rejects.toThrow('完整');expect(abort).toHaveBeenCalledOnce();expect(close).not.toHaveBeenCalled()
  vi.mocked(fetch).mockResolvedValueOnce(new Response(new Uint8Array([1]),{headers:{'Content-Type':'application/octet-stream','Content-Length':String(65*1024**2)}}))
  await expect(downloadPrivateBackup(operation('export'))).rejects.toThrow('大文件流式保存')
})
it('does not activate a service with a different protocol or without encrypted new-copy capability',async()=>{
  mock.api.mockResolvedValueOnce({protocol:'legacy',ready:true});expect(await backupCapabilities()).toBeNull()
  mock.api.mockResolvedValueOnce({protocol:BACKUP_PROTOCOL,ready:false});expect(await backupCapabilities()).toBeNull()
})
