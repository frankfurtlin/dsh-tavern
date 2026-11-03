export interface ReaderBackupBridgeWindow {
  Capacitor?:{fromNative?:(result:unknown)=>unknown}
  androidBridge?:{onmessage?:(event:{data:string})=>unknown}
}
const installed=new WeakSet<Function>()
function sameDocument(value:unknown,nonce:string){return !!value&&typeof value==='object'&&!Array.isArray(value)&&(value as {documentProtocol?:unknown}).documentProtocol==='reader-document-v1'&&(value as {documentNonce?:unknown}).documentNonce===nonce}
export function readerBackupErrorMatchesDocument(value:unknown,nonce:string){
  if(!value||typeof value!=='object'||Array.isArray(value))return false
  const error=value as Record<string,unknown>,data=error.data
  const hasEnvelope=(item:unknown)=>!!item&&typeof item==='object'&&('documentProtocol'in item||'documentNonce'in item)
  const flat=hasEnvelope(error),nested=hasEnvelope(data)
  // iOS CAPPluginCall.reject merges data into the top-level error. Android's
  // PluginCall.reject keeps it in error.data / Exception.data. If both exist,
  // neither is allowed to contradict the other document identity.
  return (flat||nested)&&(!flat||sameDocument(error,nonce))&&(!nested||sameDocument(data,nonce))
}
function permit(result:unknown,nonce:string){
  if(!result||typeof result!=='object'||(result as {pluginId?:unknown}).pluginId!=='ReaderBackup')return true
  const response=result as {success?:unknown;data?:unknown;error?:unknown},value=response.success===true?response.data:response.error
  return response.success===true?sameDocument(value,nonce):readerBackupErrorMatchesDocument(value,nonce)
}
// Capacitor 8's modern Android handler calls a private returnResult closure,
// bypassing Capacitor.fromNative. Protect both entries before SDK callback lookup.
export function installReaderBackupDocumentGate(window:ReaderBackupBridgeWindow,nonce:string){
  let legacy=false,modern=false
  if(typeof window.Capacitor?.fromNative==='function'){
    const original=window.Capacitor.fromNative;legacy=true
    if(!installed.has(original)){
      const guarded=function(this:unknown,result:unknown){if(permit(result,nonce))return original.call(this,result)}
      installed.add(guarded);window.Capacitor.fromNative=guarded
    }
  }
  if(typeof window.androidBridge?.onmessage==='function'){
    const original=window.androidBridge.onmessage;modern=true
    if(!installed.has(original)){
      const guarded=function(this:unknown,event:{data:string}){
        let result:unknown
        try{result=JSON.parse(event.data)}catch{return original.call(this,event)}
        if(permit(result,nonce))return original.call(this,event)
      }
      installed.add(guarded);window.androidBridge.onmessage=guarded
    }
  }
  return {legacy,modern}
}
