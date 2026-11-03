import type {ReaderBackupBridgeWindow} from './reader-backup-document'
const installed=new WeakSet<Function>()
function envelope(value:unknown,nonce:string,epoch?:number){
 if(!value||typeof value!=='object'||Array.isArray(value))return false
 const row=value as Record<string,unknown>
 return row.documentProtocol==='reader-document-v1'&&row.documentNonce===nonce&&(epoch===undefined||row.contextEpoch===epoch)
}
export function speechErrorMatchesContext(value:unknown,nonce:string,epoch?:number){
 if(!value||typeof value!=='object'||Array.isArray(value))return false
 const row=value as Record<string,unknown>,data=row.data,has=(item:unknown)=>!!item&&typeof item==='object'&&('documentProtocol'in item||'documentNonce'in item||'contextEpoch'in item)
 const flat=has(row),nested=has(data)
 return (flat||nested)&&(!flat||envelope(row,nonce,epoch))&&(!nested||envelope(data,nonce,epoch))
}
function permit(value:unknown,nonce:string,epoch:()=>number|undefined){
 if(!value||typeof value!=='object'||(value as {pluginId?:unknown}).pluginId!=='ReaderSpeech')return true
 const row=value as {methodName?:unknown;success?:unknown;data?:unknown;error?:unknown}
 // Capabilities precede the owner handshake. Every business call and native
 // event carries its captured context epoch, including Android error.data.
 const current=row.methodName==='capabilities'?undefined:epoch()
 if(row.methodName!=='capabilities'&&current===undefined)return false
 return row.success===true?envelope(row.data,nonce,current):speechErrorMatchesContext(row.error,nonce,current)
}
export function installSpeechDocumentGate(window:ReaderBackupBridgeWindow,nonce:string,epoch:()=>number|undefined){
 let legacy=false,modern=false
 if(typeof window.Capacitor?.fromNative==='function'){
  legacy=true;const original=window.Capacitor.fromNative
  if(!installed.has(original)){const guarded=function(this:unknown,value:unknown){if(permit(value,nonce,epoch))return original.call(this,value)};installed.add(guarded);window.Capacitor.fromNative=guarded}
 }
 if(typeof window.androidBridge?.onmessage==='function'){
  modern=true;const original=window.androidBridge.onmessage
  if(!installed.has(original)){const guarded=function(this:unknown,event:{data:string}){let value:unknown;try{value=JSON.parse(event.data)}catch{return original.call(this,event)}if(permit(value,nonce,epoch))return original.call(this,event)};installed.add(guarded);window.androidBridge.onmessage=guarded}
 }
 return {legacy,modern}
}
