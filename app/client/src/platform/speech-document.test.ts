import {it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import {installSpeechDocumentGate} from './speech-document'
import {installReaderBackupDocumentGate} from './reader-backup-document'
// Run the installed Capacitor 8 JS callback map, not a mocked lookup. This
// proves the prelookup policy only; it does not execute an Android/iOS bridge.
const sdk=readFileSync(new URL('../../node_modules/@capacitor/android/capacitor/src/main/assets/native-bridge.js',import.meta.url),'utf8')
const nonce='2'.repeat(32),old='1'.repeat(32),signed=(value:object,epoch=1,doc=nonce)=>({...value,documentProtocol:'reader-document-v1',documentNonce:doc,contextEpoch:epoch})
function document(){const calls:any[]=[],window:any={androidBridge:{postMessage:(value:string)=>calls.push(JSON.parse(value))},console:{warn(){},error(){},debug(){},log(){},info(){},trace(){}},Math:Object.assign(Object.create(Math),{random:()=>.123456}),setTimeout,clearTimeout,Document:class Document{},HTMLDocument:class HTMLDocument{},XMLHttpRequest:class XMLHttpRequest{}};window.window=window;vm.runInNewContext(sdk,window);return{window,calls}}
const flush=async()=>{for(let i=0;i<5;i++)await Promise.resolve()}
function send(doc:ReturnType<typeof document>,kind:string,value:unknown){if(kind==='modern')doc.window.androidBridge.onmessage({data:JSON.stringify(value)});else doc.window.Capacitor.fromNative(value)}
it.each(['legacy','modern'])('%s drops a previous document before consuming a colliding capability callback ID',async kind=>{
 const previous=document();previous.window.Capacitor.nativePromise('ReaderSpeech','capabilities',{documentNonce:old});const oldId=previous.calls.at(-1).callbackId
 const current=document();installSpeechDocumentGate(current.window,nonce,()=>1);let settled=false;const promise=current.window.Capacitor.nativePromise('ReaderSpeech','capabilities',{documentNonce:nonce});void promise.then(()=>settled=true);const id=current.calls.at(-1).callbackId;expect(id).toBe(oldId)
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'capabilities',callbackId:id,success:true,data:signed({ready:true,privateTitle:'old'},1,old)});await flush();expect(settled).toBe(false)
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'capabilities',callbackId:id,success:true,data:{ready:true,contextRequired:true,documentProtocol:'reader-document-v1',documentNonce:nonce}});expect(await promise).toMatchObject({ready:true,contextRequired:true})
})
it.each(['legacy','modern'])('%s keeps an old account error away from SDK rejection and accepts its current nested Android error',async kind=>{
 const current=document();let epoch=1;installSpeechDocumentGate(current.window,nonce,()=>epoch);let error:any;const promise=current.window.Capacitor.nativePromise('ReaderSpeech','play',{epoch,documentNonce:nonce});void promise.catch((value:unknown)=>error=value);const id=current.calls.at(-1).callbackId
 epoch=2;send(current,kind,{pluginId:'ReaderSpeech',methodName:'play',callbackId:id,success:false,error:{code:'SYSTEM_SPEECH_FAILED',message:'old-private-title',data:signed({},1)}});await flush();expect(error).toBeUndefined()
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'play',callbackId:id,success:false,error:{code:'SPEECH_CONTEXT_CHANGED',message:'current',data:signed({},2)}});await flush();expect(error).toMatchObject({code:'SPEECH_CONTEXT_CHANGED',message:'current'})
})
it.each(['legacy','modern'])('%s preserves a current speech listener after a stale save:false and leaves Backup and Display callbacks alone',async kind=>{
 const current=document();installReaderBackupDocumentGate(current.window,old);installSpeechDocumentGate(current.window,nonce,()=>1);const received:any[]=[]
 current.window.Capacitor.nativeCallback('ReaderSpeech','addListener',{eventName:'speechState'},(value:any)=>received.push(value));const id=current.calls.at(-1).callbackId
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'addListener',callbackId:id,success:true,save:false,data:signed({status:'ended'},0)});expect(received).toEqual([])
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'addListener',callbackId:id,success:true,save:true,data:signed({status:'playing'})});expect(received).toHaveLength(1)
 const backup=current.window.Capacitor.nativePromise('ReaderBackup','getCapabilities',{}),bid=current.calls.at(-1).callbackId;send(current,kind,{pluginId:'ReaderBackup',methodName:'getCapabilities',callbackId:bid,success:true,data:signed({ready:true},0,old)});expect(await backup).toMatchObject({ready:true})
 const display=current.window.Capacitor.nativePromise('ReaderDisplay','getInsets',{}),did=current.calls.at(-1).callbackId;send(current,kind,{pluginId:'ReaderDisplay',methodName:'getInsets',callbackId:did,success:true,data:{systemBottomPx:48}});expect(await display).toEqual({systemBottomPx:48})
})
it.each(['legacy','modern'])('%s refuses contradictory iOS and Android error envelopes without consuming the current callback',async kind=>{
 const current=document();installSpeechDocumentGate(current.window,nonce,()=>1);let settled=false;const promise=current.window.Capacitor.nativePromise('ReaderSpeech','stop',{epoch:1,documentNonce:nonce});void promise.then(()=>settled=true,()=>settled=true);const id=current.calls.at(-1).callbackId
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'stop',callbackId:id,success:false,error:signed({message:'old-private-error',data:signed({},2)},1)});await flush();expect(settled).toBe(false)
 send(current,kind,{pluginId:'ReaderSpeech',methodName:'stop',callbackId:id,success:true,data:signed({stopped:true})});expect(await promise).toMatchObject({stopped:true})
})
