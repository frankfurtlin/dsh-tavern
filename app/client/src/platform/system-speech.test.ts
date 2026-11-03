import {beforeEach,afterEach,it,expect,vi} from 'vitest'
const mock=vi.hoisted(()=>{
 const window=Object.assign(new EventTarget(),{Capacitor:{fromNative:vi.fn()}});Object.assign(globalThis,{window})
 const names=['capabilities','setContext','revokeContext','voices','status','loadQueue','play','pause','skip','configure','stop','openSettings','addListener']
 return {window,owner:'a'.repeat(32),origin:'https://speech.invalid',auth:0,plugin:Object.fromEntries(names.map(name=>[name,vi.fn()])),remove:vi.fn()}
})
vi.mock('@capacitor/core',()=>({registerPlugin:()=>mock.plugin}))
vi.mock('./bridge',()=>({native:true,accountId:()=>mock.owner,backendBase:()=>mock.origin}))
vi.mock('../api',()=>({authGeneration:()=>mock.auth}))
import {systemSpeech} from './system-speech'
const driver=systemSpeech(),nonce=()=>((mock.window as unknown as Record<string,unknown>).__readerSpeechDocumentNonce as string)
const signed=(body:object,epoch?:number,documentNonce=nonce())=>({...body,documentProtocol:'reader-document-v1',documentNonce,...(epoch===undefined?{}:{contextEpoch:epoch})})
const flush=async()=>{for(let i=0;i<25;i++)await Promise.resolve()}
beforeEach(()=>{
 mock.owner='a'.repeat(32);mock.origin='https://speech.invalid';mock.auth++;Object.assign(globalThis,{window:mock.window})
 for(const fn of Object.values(mock.plugin))fn.mockReset();mock.remove.mockReset();mock.remove.mockResolvedValue(undefined)
 mock.plugin.capabilities!.mockResolvedValue(signed({ready:true,contextRequired:true}))
 mock.plugin.setContext!.mockImplementation(async value=>signed({ready:true},value.epoch))
 mock.plugin.revokeContext!.mockImplementation(async value=>signed({ready:false},value.epoch))
 mock.plugin.status!.mockImplementation(async value=>signed({scopeKey:value.scopeKey,status:'paused',reason:'process-restored',queueLoaded:false},value.epoch))
 mock.plugin.voices!.mockImplementation(async value=>signed({voices:[],available:false,initializing:false},value.epoch))
 mock.plugin.addListener!.mockResolvedValue({remove:mock.remove})
})
afterEach(()=>vi.useRealTimers())
it('handshakes the exact identity and strips the verified envelope without clearing a cold marker or playing',async()=>{
 const state=await driver.status({scopeKey:'scope'})
 expect(mock.plugin.capabilities).toHaveBeenCalledWith({documentNonce:expect.stringMatching(/^[a-f0-9]{32}$/)})
 expect(mock.plugin.setContext).toHaveBeenCalledExactlyOnceWith({owner:mock.owner,origin:mock.origin,epoch:expect.any(Number),documentNonce:nonce()})
 expect(state).toEqual({scopeKey:'scope',status:'paused',reason:'process-restored',queueLoaded:false})
 expect(mock.plugin.stop).not.toHaveBeenCalled();expect(mock.plugin.play).not.toHaveBeenCalled()
 const descriptor=Object.getOwnPropertyDescriptor(mock.window,'__readerSpeechDocumentNonce')!
 expect(descriptor.writable).toBe(false);expect(descriptor.configurable).toBe(false)
 await driver.voices({retry:true});expect(mock.plugin.setContext).toHaveBeenCalledOnce()
 expect(mock.plugin.voices).toHaveBeenCalledWith({retry:true,epoch:mock.plugin.setContext!.mock.calls[0]![0].epoch,documentNonce:nonce()})
})
it('keeps a signed old capability without contextRequired unavailable and does not read its private native marker',async()=>{
 mock.plugin.capabilities!.mockResolvedValueOnce(signed({ready:true}))
 expect(await driver.voices()).toMatchObject({available:false,reason:'NATIVE_SPEECH_PROTOCOL_UNAVAILABLE'})
 expect(mock.plugin.setContext).not.toHaveBeenCalled();expect(mock.plugin.status).not.toHaveBeenCalled()
})
it('does not dispatch old status after an account changes while its handshake is pending',async()=>{
 let resolve!:(value:unknown)=>void;mock.plugin.setContext!.mockImplementationOnce(()=>new Promise(done=>resolve=done))
 const pending=driver.status({scopeKey:'private-old'});void pending.catch(()=>{});await flush()
 const epoch=mock.plugin.setContext!.mock.calls[0]![0].epoch;mock.owner='b'.repeat(32);mock.auth++
 resolve(signed({ready:true},epoch));await expect(pending).rejects.toMatchObject({code:'SPEECH_CONTEXT_CHANGED'})
 expect(mock.plugin.status).not.toHaveBeenCalled()
})
it('preserves the current Android nested typed failure and drops contradictory epoch envelopes',async()=>{
 mock.plugin.play!.mockImplementationOnce(async value=>{throw Object.assign(new Error('current voice unavailable'),{code:'NO_SYSTEM_VOICE',data:signed({},value.epoch)})})
 await expect(driver.play({scopeKey:'scope'})).rejects.toMatchObject({code:'NO_SYSTEM_VOICE',message:'current voice unavailable'})
 mock.plugin.play!.mockImplementationOnce(async value=>{throw Object.assign(new Error('previous-private-error'),signed({code:'NO_SYSTEM_VOICE',data:signed({},value.epoch+1)},value.epoch))})
 await expect(driver.play({scopeKey:'scope'})).rejects.toMatchObject({code:'SPEECH_DOCUMENT_CHANGED'})
})
it('accepts only events from the handshaken document and epoch and removes an obsolete registration',async()=>{
 const states:any[]=[];const handle=await driver.addListener('speechState',state=>states.push(state));const callback=mock.plugin.addListener!.mock.calls[0]![1],epoch=mock.plugin.setContext!.mock.calls[0]![0].epoch
 callback({scopeKey:'scope',status:'playing',queueLoaded:true})
 callback(signed({scopeKey:'scope',status:'playing',queueLoaded:true},epoch,'f'.repeat(32)))
 callback(signed({scopeKey:'scope',status:'playing',queueLoaded:true},epoch+1))
 callback(signed({scopeKey:'scope',status:'playing',queueLoaded:true},epoch))
 expect(states).toEqual([{scopeKey:'scope',status:'playing',queueLoaded:true}])
 mock.auth++;callback(signed({scopeKey:'scope',status:'ended',queueLoaded:true},epoch));expect(states).toHaveLength(1)
 await handle.remove();expect(mock.remove).toHaveBeenCalledOnce()
})
it('revokes with a new epoch immediately on private clear and rejects the old queued native success',async()=>{
 await driver.status({scopeKey:'scope'});let resolve!:(value:unknown)=>void
 mock.plugin.play!.mockImplementationOnce(()=>new Promise(done=>resolve=done));const pending=driver.play({scopeKey:'scope'});void pending.catch(()=>{});await flush()
 const oldEpoch=mock.plugin.play!.mock.calls[0]![0].epoch;mock.owner='';mock.auth++;mock.window.dispatchEvent(new Event('reader-private-cleared'));await flush()
 expect(mock.plugin.revokeContext).toHaveBeenCalledExactlyOnceWith({epoch:expect.any(Number),documentNonce:nonce()})
 expect(mock.plugin.revokeContext!.mock.calls[0]![0].epoch).toBeGreaterThan(oldEpoch)
 resolve(signed({scopeKey:'scope',status:'playing',queueLoaded:true},oldEpoch));await expect(pending).rejects.toMatchObject({code:'SPEECH_CONTEXT_CHANGED'})
 mock.owner='a'.repeat(32);mock.auth++;await driver.status({scopeKey:'new'})
 expect(mock.plugin.setContext!.mock.calls.at(-1)![0].epoch).toBeGreaterThan(mock.plugin.revokeContext!.mock.calls[0]![0].epoch)
})
it('does not return the previous backend marker after its delayed status completes',async()=>{
 let resolve!:(value:unknown)=>void;mock.plugin.status!.mockImplementationOnce(()=>new Promise(done=>resolve=done))
 const pending=driver.status({scopeKey:'old'});void pending.catch(()=>{});await flush();const epoch=mock.plugin.status!.mock.calls[0]![0].epoch
 mock.origin='https://other.invalid';mock.auth++;resolve(signed({scopeKey:'old',title:'old-private-title',status:'paused',queueLoaded:false},epoch))
 await expect(pending).rejects.toMatchObject({code:'SPEECH_CONTEXT_CHANGED'})
})
it('bounds a dropped unsigned capability response without opening old voices or status',async()=>{
 vi.useFakeTimers();mock.plugin.capabilities!.mockReturnValueOnce(new Promise(()=>{}));const work=driver.voices();void work.catch(()=>{});await flush();await vi.advanceTimersByTimeAsync(15001)
 expect(await work).toMatchObject({available:false,reason:'NATIVE_SPEECH_PROTOCOL_UNAVAILABLE'});expect(mock.plugin.voices).not.toHaveBeenCalled();expect(mock.plugin.status).not.toHaveBeenCalled()
})
