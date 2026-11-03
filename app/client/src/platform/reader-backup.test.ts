import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mock=vi.hoisted(()=>{const window=Object.assign(new EventTarget(),{Capacitor:{fromNative:vi.fn()}});Object.assign(globalThis,{window});return{window,owner:'a'.repeat(32),base:'https://backup.invalid',epoch:1,cancel:vi.fn(),context:vi.fn(),cap:vi.fn(),pick:vi.fn(),prepare:vi.fn(),download:vi.fn(),listener:vi.fn(),remove:vi.fn()}})
vi.mock('@capacitor/core',()=>({registerPlugin:()=>({setContext:mock.context,getCapabilities:mock.cap,pickBackup:mock.pick,prepareBackup:mock.prepare,downloadBackup:mock.download,addListener:mock.listener,cancel:mock.cancel})}))
vi.mock('./bridge',()=>({native:true,accountId:()=>mock.owner,backendBase:()=>mock.base}))
vi.mock('../api',()=>({authGeneration:()=>mock.epoch,authorizationHeaders:()=>({Authorization:'Bearer memory-token'}),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
import {nativeBackupCapabilities,selectNativeBackup,prepareNativeBackup,downloadNativeBackup,cancelNativeBackup} from './reader-backup'
const selectionId='b'.repeat(32),operationId='c'.repeat(32)
const nonce=()=>((window as unknown as Record<string,unknown>).__readerBackupDocumentNonce as string)
const signed=(value:object,documentNonce=nonce())=>({...value,documentProtocol:'reader-document-v1',documentNonce})
beforeEach(()=>{mock.owner='a'.repeat(32);mock.base='https://backup.invalid';mock.epoch=1;Object.assign(globalThis,{window:mock.window});for(const fn of [mock.context,mock.cap,mock.pick,mock.prepare,mock.download,mock.listener,mock.remove,mock.cancel])fn.mockReset();mock.context.mockResolvedValue(signed({ready:true}));mock.cap.mockResolvedValue(signed({ready:true,streaming:true,maxFileBytes:2214592640}));mock.listener.mockResolvedValue({remove:mock.remove});mock.remove.mockResolvedValue(undefined)})
afterEach(()=>{vi.useRealTimers()})
it('binds the exact owner origin and epoch before testing real plugin streaming capabilities',async()=>{expect(await nativeBackupCapabilities()).toEqual({ready:true,streaming:true,maxFileBytes:2214592640});expect(mock.context).toHaveBeenCalledExactlyOnceWith({ownerId:mock.owner,backend:mock.base,epoch:1,documentNonce:expect.stringMatching(/^[a-f0-9]{32}$/)});expect(mock.cap).toHaveBeenCalledOnce();mock.cap.mockResolvedValueOnce({ready:true,streaming:false,maxFileBytes:2214592640});expect(await nativeBackupCapabilities()).toBeNull()})
it('keeps only opaque selection and display fields, allowing Android providers with unknown size without reading URI or Base64',async()=>{mock.pick.mockResolvedValueOnce(signed({selectionId,size:null,displayName:'encrypted.srbackup',uri:'not-public',base64:'not-public'}));expect(await selectNativeBackup()).toEqual({selectionId,size:null,displayName:'encrypted.srbackup'})})
it('rejects a delayed picker from a previous account instead of exposing its private filename',async()=>{let resolve!:(value:unknown)=>void;mock.pick.mockReturnValueOnce(new Promise(done=>resolve=done));const pending=selectNativeBackup();for(let i=0;i<12;i++)await Promise.resolve();mock.owner='d'.repeat(32);mock.epoch++;resolve(signed({selectionId,size:8,displayName:'old.srbackup'}));await expect(pending).rejects.toThrow('账号')})
it('passes password and token only to the one in-memory streaming request and propagates unknown without replay',async()=>{mock.prepare.mockRejectedValueOnce(Object.assign(new Error('unknown'),signed({code:'BACKUP_RESPONSE_UNKNOWN'})));await expect(prepareNativeBackup({selectionId,size:null,displayName:'a.srbackup'},'private-password','request1')).rejects.toMatchObject({code:'BACKUP_RESPONSE_UNKNOWN'});expect(mock.prepare).toHaveBeenCalledExactlyOnceWith({selectionId,password:'private-password',requestId:'request1',accessToken:'memory-token',epoch:1,documentNonce:expect.stringMatching(/^[a-f0-9]{32}$/)})})
it('filters old epoch progress and removes native listeners on a failed download',async()=>{const progress=vi.fn();mock.download.mockImplementationOnce(async()=>{const handler=mock.listener.mock.calls[0]![1];handler(signed({phase:'download',bytes:64}));mock.epoch++;handler(signed({phase:'download',bytes:128}));return signed({saved:true})});await expect(downloadNativeBackup(operationId,progress)).rejects.toThrow('账号');expect(progress).toHaveBeenCalledExactlyOnceWith(64);expect(mock.remove).toHaveBeenCalledOnce()})
it('clears native selection/transfer context on private data revocation without deleting arbitrary user files',async()=>{mock.epoch=3;mock.window.dispatchEvent(new Event('reader-private-cleared'));for(let i=0;i<12;i++)await Promise.resolve();expect(mock.context).toHaveBeenCalledWith({backend:'',ownerId:'',epoch:3,documentNonce:expect.stringMatching(/^[a-f0-9]{32}$/)});expect(mock.prepare).not.toHaveBeenCalled();expect(mock.download).not.toHaveBeenCalled()})

it('binds a fixed readonly document nonce to every explicit native method',async()=>{
  const nonce=(window as unknown as Record<string,unknown>).__readerBackupDocumentNonce
  expect(nonce).toMatch(/^[a-f0-9]{32}$/);const descriptor=Object.getOwnPropertyDescriptor(window,'__readerBackupDocumentNonce')!
  expect(descriptor.writable).toBe(false);expect(descriptor.configurable).toBe(false)
  await nativeBackupCapabilities();expect(mock.context.mock.calls[0]![0].documentNonce).toBe(nonce);expect(mock.cap).toHaveBeenCalledWith({documentNonce:nonce})
  mock.pick.mockResolvedValueOnce(signed({selectionId,size:8,displayName:'a.srbackup'}));await selectNativeBackup();expect(mock.pick).toHaveBeenCalledWith({documentNonce:nonce})
  mock.prepare.mockResolvedValueOnce(signed({status:'ready'}));await prepareNativeBackup({selectionId,size:8,displayName:'a.srbackup'},'memory-password','request');expect(mock.prepare.mock.calls[0]![0].documentNonce).toBe(nonce)
  mock.download.mockResolvedValueOnce(signed({saved:true}));await downloadNativeBackup(operationId);expect(mock.download.mock.calls[0]![0].documentNonce).toBe(nonce)
})

it('cancels only the current context with the same document nonce',async()=>{mock.cancel.mockResolvedValueOnce(signed({}));await cancelNativeBackup();expect(mock.cancel).toHaveBeenCalledExactlyOnceWith({epoch:1,documentNonce:(window as unknown as Record<string,unknown>).__readerBackupDocumentNonce})})

// Actual bridge callbacks use a queued envelope; neither capability nor body is
// trusted merely because a native method was originally called in this document.
it('keeps old unsigned or wrong-document native capabilities unavailable',async()=>{
 mock.cap.mockResolvedValueOnce({ready:true,streaming:true,maxFileBytes:2214592640});expect(await nativeBackupCapabilities()).toBeNull()
 mock.cap.mockResolvedValueOnce(signed({ready:true,streaming:true,maxFileBytes:2214592640},'f'.repeat(32)));expect(await nativeBackupCapabilities()).toBeNull()
 mock.context.mockResolvedValueOnce({ready:true});expect(await nativeBackupCapabilities()).toBeNull()
})
it('rejects mismatched queued success and error without exposing a previous document filename or error',async()=>{
 mock.pick.mockResolvedValueOnce(signed({selectionId,size:8,displayName:'previous-private.srbackup'},'f'.repeat(32)))
 await expect(selectNativeBackup()).rejects.toMatchObject({code:'BACKUP_DOCUMENT_CHANGED'})
 mock.prepare.mockRejectedValueOnce(Object.assign(new Error('old-private-provider-error'),signed({code:'BACKUP_RESPONSE_UNKNOWN'},'f'.repeat(32))))
 try{await prepareNativeBackup({selectionId,size:8,displayName:'new.srbackup'},'memory-password','current-request');throw new Error('unexpected success')}catch(error){expect(error).toMatchObject({code:'BACKUP_DOCUMENT_CHANGED',status:0});expect((error as Error).message).not.toContain('old-private')}
 expect(mock.prepare).toHaveBeenCalledOnce()
})
it('strips the validated document envelope before strict server operation validation',async()=>{
 mock.prepare.mockResolvedValueOnce(signed({id:operationId,kind:'restore',status:'ready',version:1,counts:{book:1}}))
 expect(await prepareNativeBackup({selectionId,size:8,displayName:'new.srbackup'},'memory-password','current-request')).toEqual({id:operationId,kind:'restore',status:'ready',version:1,counts:{book:1}})
})
it('drops unsigned and previous-document progress while retaining current signed progress',async()=>{
 const progress=vi.fn();mock.download.mockImplementationOnce(async()=>{const handler=mock.listener.mock.calls[0]![1];handler({phase:'download',bytes:1});handler(signed({phase:'download',bytes:2},'f'.repeat(32)));handler(signed({phase:'download',bytes:3}));return signed({saved:true})})
 await downloadNativeBackup(operationId,progress);expect(progress).toHaveBeenCalledExactlyOnceWith(3);expect(mock.remove).toHaveBeenCalledOnce()
})
it('keeps a legacy unsigned bridge unavailable after a bounded capability/context wait',async()=>{
 vi.useFakeTimers();mock.context.mockReturnValueOnce(new Promise(()=>{}));const result=nativeBackupCapabilities();for(let i=0;i<8;i++)await Promise.resolve();await vi.advanceTimersByTimeAsync(15001);expect(await result).toBeNull();expect(mock.cap).not.toHaveBeenCalled()
})

it('preserves Android Exception.data typed cancellation and unknown without exposing the envelope or replaying',async()=>{
 mock.pick.mockRejectedValueOnce(Object.assign(new Error('current cancellation'),{code:'BACKUP_CANCELLED',data:signed({})}))
 await expect(selectNativeBackup()).rejects.toMatchObject({code:'BACKUP_CANCELLED',message:'current cancellation'})
 mock.prepare.mockRejectedValueOnce(Object.assign(new Error('current response unknown'),{code:'BACKUP_RESPONSE_UNKNOWN',data:signed({})}))
 await expect(prepareNativeBackup({selectionId,size:8,displayName:'own.srbackup'},'memory-password','same-request')).rejects.toMatchObject({code:'BACKUP_RESPONSE_UNKNOWN'})
 expect(mock.prepare).toHaveBeenCalledOnce()
})
it('rejects stale nested Android errors and conflicting flat/nested document envelopes with a generic message',async()=>{
 mock.pick.mockRejectedValueOnce(Object.assign(new Error('previous-private-error'),{code:'BACKUP_CANCELLED',data:signed({},'f'.repeat(32))}))
 await expect(selectNativeBackup()).rejects.toMatchObject({code:'BACKUP_DOCUMENT_CHANGED',status:0})
 mock.pick.mockRejectedValueOnce(Object.assign(new Error('conflicting-private-error'),signed({code:'BACKUP_CANCELLED',data:signed({},'f'.repeat(32))})))
 await expect(selectNativeBackup()).rejects.toMatchObject({code:'BACKUP_DOCUMENT_CHANGED',status:0})
})
