import {it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import {installReaderBackupDocumentGate} from './reader-backup-document'
// Execute the installed Capacitor 8 bridge and its private callback map. This is
// SDK JS in a VM, not a fake callback map and not Android/WebView execution.
const source=readFileSync(new URL('../../node_modules/@capacitor/android/capacitor/src/main/assets/native-bridge.js',import.meta.url),'utf8')
const oldNonce='1'.repeat(32),newNonce='2'.repeat(32),signed=(data:object,nonce=newNonce)=>({...data,documentProtocol:'reader-document-v1',documentNonce:nonce})
function document(){
 const calls:any[]=[],sandbox:any={androidBridge:{postMessage:(value:string)=>calls.push(JSON.parse(value))},console:{warn(){},error(){},debug(){},log(){},info(){},trace(){}},Math:Object.assign(Object.create(Math),{random:()=>.123456}),setTimeout,clearTimeout,Document:class Document{},HTMLDocument:class HTMLDocument{},XMLHttpRequest:class XMLHttpRequest{}}
 sandbox.window=sandbox;vm.runInNewContext(source,sandbox);return{window:sandbox,calls}
}
async function flush(){for(let i=0;i<5;i++)await Promise.resolve()}
function send(doc:ReturnType<typeof document>,kind:string,result:unknown){if(kind==='modern')doc.window.androidBridge.onmessage({data:JSON.stringify(result)});else doc.window.Capacitor.fromNative(result)}
it.each(['legacy','modern'])('%s drops old success before a reused SDK callback ID is consumed',async kind=>{
 const old=document();old.window.Capacitor.nativePromise('ReaderBackup','pickBackup',{documentNonce:oldNonce});const oldId=old.calls.at(-1).callbackId
 const current=document();installReaderBackupDocumentGate(current.window,newNonce);let settled=false;const work=current.window.Capacitor.nativePromise('ReaderBackup','pickBackup',{documentNonce:newNonce});void work.then(()=>{settled=true});const id=current.calls.at(-1).callbackId;expect(id).toBe(oldId)
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'pickBackup',success:true,data:signed({displayName:'old-private.srbackup'},oldNonce)});await flush();expect(settled).toBe(false)
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'pickBackup',success:true,data:signed({displayName:'current.srbackup'})});expect(await work).toMatchObject({displayName:'current.srbackup'})
})
it.each(['legacy','modern'])('%s drops old error without rejecting or deleting the current SDK call',async kind=>{
 const current=document();installReaderBackupDocumentGate(current.window,newNonce);let rejected=false,resolved=false;const work=current.window.Capacitor.nativePromise('ReaderBackup','prepareBackup',{documentNonce:newNonce});void work.then(()=>{resolved=true},()=>{rejected=true});const id=current.calls.at(-1).callbackId
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'prepareBackup',success:false,error:signed({message:'old-private-provider-error',code:'BACKUP_RESPONSE_UNKNOWN'},oldNonce)});await flush();expect(rejected||resolved).toBe(false)
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'prepareBackup',success:true,data:signed({id:'3'.repeat(32),status:'ready'})});expect(await work).toMatchObject({status:'ready'})
})
it.each(['legacy','modern'])('%s leaves a signed listener alive after stale unsigned save:false and does not intercept other plugins',async kind=>{
 const current=document();installReaderBackupDocumentGate(current.window,newNonce);const bytes:number[]=[];current.window.Capacitor.nativeCallback('ReaderBackup','addListener',{eventName:'backupProgress'},(value:any)=>bytes.push(value.bytes));const id=current.calls.at(-1).callbackId
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'addListener',success:true,save:false,data:{phase:'download',bytes:1}});expect(bytes).toEqual([])
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'addListener',success:true,save:true,data:signed({phase:'download',bytes:2})});expect(bytes).toEqual([2])
 const other=current.window.Capacitor.nativePromise('ReaderDisplay','getInsets',{}),otherId=current.calls.at(-1).callbackId;send(current,kind,{callbackId:otherId,pluginId:'ReaderDisplay',methodName:'getInsets',success:true,data:{systemBottomPx:48}});expect(await other).toEqual({systemBottomPx:48})
})

it.each(['legacy','modern'])('%s keeps stale Android error.data away from callback lookup but delivers the current typed error',async kind=>{
 const current=document();installReaderBackupDocumentGate(current.window,newNonce);let rejection:any
 const work=current.window.Capacitor.nativePromise('ReaderBackup','cancel',{documentNonce:newNonce});void work.catch((error:unknown)=>{rejection=error});const id=current.calls.at(-1).callbackId
 // Android PluginCall.reject stores its JSObject in error.data. The installed
 // SDK copies it into Exception.data, without flattening it into the error.
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'cancel',success:false,error:{message:'previous-private-error',code:'BACKUP_TRANSFER_BUSY',data:signed({},oldNonce)}})
 await flush();expect(rejection).toBeUndefined()
 send(current,kind,{callbackId:id,pluginId:'ReaderBackup',methodName:'cancel',success:false,error:{message:'current cancellation',code:'BACKUP_CANCELLED',data:signed({})}})
 await flush();expect(rejection).toMatchObject({message:'current cancellation',code:'BACKUP_CANCELLED',data:signed({})})
})
