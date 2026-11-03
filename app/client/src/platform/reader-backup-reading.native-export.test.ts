import {beforeEach,it,expect,vi} from 'vitest'
const mock=vi.hoisted(()=>{const window=Object.assign(new EventTarget(),{Capacitor:{fromNative:vi.fn()}});Object.assign(globalThis,{window});return {window,owner:'a'.repeat(32),base:'https://owned.invalid',epoch:1,context:vi.fn(),cap:vi.fn(),download:vi.fn(),listener:vi.fn(),remove:vi.fn()}})
vi.mock('@capacitor/core',()=>({registerPlugin:()=>({setContext:mock.context,getCapabilities:mock.cap,downloadReadingExport:mock.download,addListener:mock.listener})}))
vi.mock('./bridge',()=>({native:true,accountId:()=>mock.owner,backendBase:()=>mock.base}))
vi.mock('../api',()=>({authGeneration:()=>mock.epoch,authorizationHeaders:()=>({Authorization:'Bearer owned-memory-token'}),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
import * as actual from './reader-backup'
// Namespace access allows the unchanged old module to execute the red tests;
// absence is a business capability failure, not a collection/import error.
const module=actual as unknown as {nativeReadingExportCapabilities:()=>Promise<{ready:true;maxReadingBytes:number}|null>;downloadNativeReadingExport:(metadata:Record<string,unknown>,progress?:(n:number)=>void,valid?:()=>boolean)=>Promise<void>}
const nonce=()=>String((window as unknown as Record<string,unknown>).__readerBackupDocumentNonce)
const signed=(value:object,documentNonce=nonce())=>({...value,documentProtocol:'reader-document-v1',documentNonce})
const metadata={operationId:'e'.repeat(32),format:'epub',size:128*1024**2,sha256:'f'.repeat(64),fileName:'本人正文.epub'}
beforeEach(()=>{Object.assign(globalThis,{window:mock.window});mock.owner='a'.repeat(32);mock.base='https://owned.invalid';mock.epoch=1;for(const fn of [mock.context,mock.cap,mock.download,mock.listener,mock.remove])fn.mockReset();mock.context.mockResolvedValue(signed({ready:true}));mock.cap.mockResolvedValue(signed({ready:true,streaming:true,maxFileBytes:2214592640,readingExport:true,maxReadingBytes:2147483648}));mock.download.mockResolvedValue(signed({saved:true}));mock.listener.mockResolvedValue({remove:mock.remove});mock.remove.mockResolvedValue(undefined)})
it('exposes a signed streaming native reading capability without forwarding arbitrary native fields',async()=>{
  expect(typeof module.nativeReadingExportCapabilities).toBe('function');mock.cap.mockResolvedValueOnce(signed({ready:true,streaming:true,maxFileBytes:2214592640,readingExport:true,maxReadingBytes:2147483648,privateReasoning:'not-public'}))
  expect(await module.nativeReadingExportCapabilities()).toEqual({ready:true,maxReadingBytes:2147483648})
})
it.each(['old','unsigned','stale','oversize'])('old or invalid native reading support stays unavailable (%s)',async mode=>{
  const cap=mode==='old'?{ready:true,streaming:true,maxFileBytes:2214592640}:{ready:true,streaming:true,maxFileBytes:2214592640,readingExport:true,maxReadingBytes:mode==='oversize'?3*1024**3:2147483648}
  mock.cap.mockResolvedValueOnce(mode==='unsigned'?cap:signed(cap,mode==='stale'?'9'.repeat(32):nonce()))
  expect(await module.nativeReadingExportCapabilities()).toBeNull();expect(mock.download).not.toHaveBeenCalled()
})
it('sends only allowlisted metadata and current in-memory auth to the unchanged native system save method',async()=>{
  const progress=vi.fn();mock.download.mockImplementationOnce(async()=>{const callback=mock.listener.mock.calls[0]![1];callback(signed({phase:'download',bytes:65536,totalBytes:metadata.size}));return signed({saved:true,uri:'must-not-return'})})
  expect(await module.downloadNativeReadingExport({...metadata,privateState:'never-native'},progress)).toBeUndefined()
  expect(mock.context).toHaveBeenCalledWith({ownerId:mock.owner,backend:mock.base,epoch:1,documentNonce:nonce()})
  expect(mock.download).toHaveBeenCalledExactlyOnceWith({...metadata,accessToken:'owned-memory-token',epoch:1,documentNonce:nonce()});expect(progress).toHaveBeenCalledExactlyOnceWith(65536);expect(mock.remove).toHaveBeenCalledOnce()
})
it.each(['owner','base','epoch','scope'])('late progress and completion cannot revive old %s scope',async field=>{
  let allowed=true;const progress=vi.fn();mock.download.mockImplementationOnce(async()=>{const callback=mock.listener.mock.calls[0]![1];if(field==='owner')mock.owner='9'.repeat(32);else if(field==='base')mock.base='https://another.invalid';else if(field==='epoch')mock.epoch++;else allowed=false;callback(signed({phase:'download',bytes:metadata.size}));return signed({saved:true})})
  await expect(module.downloadNativeReadingExport(metadata,progress,()=>allowed)).rejects.toThrow();expect(progress).not.toHaveBeenCalled();expect(mock.download).toHaveBeenCalledOnce();expect(mock.remove).toHaveBeenCalledOnce()
})
it('filters unsigned, previous-document, regressing and oversized byte events without exposing payloads',async()=>{
  const progress=vi.fn();mock.download.mockImplementationOnce(async()=>{const callback=mock.listener.mock.calls[0]![1];callback({phase:'download',bytes:1});callback(signed({phase:'download',bytes:2},'9'.repeat(32)));callback(signed({phase:'download',bytes:3}));callback(signed({phase:'download',bytes:2}));callback(signed({phase:'download',bytes:metadata.size+1}));return signed({saved:true})})
  await module.downloadNativeReadingExport(metadata,progress);expect(progress.mock.calls).toEqual([[3]]);expect(mock.remove).toHaveBeenCalledOnce()
})
it.each(['unsigned','stale','unconfirmed','cancelled'])('rejects an unconfirmed save or signed cancellation and never replays (%s)',async mode=>{
  if(mode==='cancelled')mock.download.mockRejectedValueOnce(Object.assign(new Error('PRIVATE-INTERNAL-DETAIL'),{code:'BACKUP_CANCELLED',data:signed({})}))
  else mock.download.mockResolvedValueOnce(mode==='unsigned'?{saved:true}:signed({saved:mode!=='unconfirmed'},mode==='stale'?'9'.repeat(32):nonce()))
  try{await module.downloadNativeReadingExport(metadata);throw new Error('unexpected saved')}catch(error){expect((error as Error).message).not.toContain('PRIVATE');if(mode==='cancelled')expect(error).toMatchObject({code:'BACKUP_CANCELLED'})}
  expect(mock.download).toHaveBeenCalledOnce();expect(mock.remove).toHaveBeenCalledOnce()
})
it.each([{size:2147483649},{sha256:'invalid'},{fileName:'../private.epub'},{fileName:'\ud800.epub'},{format:'html'}])('rejects malformed public file metadata before registering a transfer: %j',async bad=>{
  await expect(module.downloadNativeReadingExport({...metadata,...bad})).rejects.toMatchObject({code:'EXPORT_FILE_INVALID'});expect(mock.download).not.toHaveBeenCalled();expect(mock.listener).not.toHaveBeenCalled()
})
it('does not send a native command after the active story changed during listener registration',async()=>{
  let allowed=true;mock.listener.mockImplementationOnce(async()=>{allowed=false;return{remove:mock.remove}})
  await expect(module.downloadNativeReadingExport(metadata,()=>{},()=>allowed)).rejects.toThrow();expect(mock.download).not.toHaveBeenCalled();expect(mock.remove).toHaveBeenCalledOnce()
})
it('a signed context initialization failure cannot expose internal native details',async()=>{
  mock.context.mockRejectedValueOnce(Object.assign(new Error('PRIVATE-CONTEXT-DETAIL'),{code:'BACKUP_TRANSFER_FAILED',data:signed({})}))
  await expect(module.downloadNativeReadingExport(metadata)).rejects.toMatchObject({code:'BACKUP_TRANSFER_FAILED',message:'文件尚未保存，请重试或查询本次请求'});expect(mock.download).not.toHaveBeenCalled()
})
it('respects a reduced native quota before opening the file save transfer',async()=>{
  mock.cap.mockResolvedValueOnce(signed({ready:true,streaming:true,maxFileBytes:2214592640,readingExport:true,maxReadingBytes:65536}))
  await expect(module.downloadNativeReadingExport(metadata)).rejects.toMatchObject({code:'EXPORT_NATIVE_UNAVAILABLE'});expect(mock.download).not.toHaveBeenCalled();expect(mock.listener).not.toHaveBeenCalled()
})
