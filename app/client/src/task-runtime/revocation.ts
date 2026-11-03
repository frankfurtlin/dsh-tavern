const name='reader-private-scope-events-v1'
export function publishRevocation(vaultId:string,targetIds:string[]){const detail={type:'scope-revoked',vaultId,targetIds};window.dispatchEvent(new CustomEvent('reader-local-scope-revoked',{detail}));if(typeof BroadcastChannel!=='undefined'){const channel=new BroadcastChannel(name);channel.postMessage(detail);channel.close()}}
export function observeRevocation(vaultId:string,targetId:string,stop:()=>void):()=>void{
  const inspect=(value:unknown)=>{const event=value as {type?:string;vaultId?:string;targetIds?:unknown};if(event?.type==='scope-revoked'&&event.vaultId===vaultId&&Array.isArray(event.targetIds)&&event.targetIds.includes(targetId))stop()},local=(event:Event)=>inspect((event as CustomEvent).detail),channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel(name):null
  window.addEventListener('reader-local-scope-revoked',local);if(channel)channel.onmessage=event=>inspect(event.data)
  return ()=>{window.removeEventListener('reader-local-scope-revoked',local);channel?.close()}
}
