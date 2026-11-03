import {ContractError,canonical} from '../domain/local-contract'
import {pluginBytes,type PluginInput,type PluginOutput} from './contract'
/** Opaque-origin iframe gives its blob worker a network-denying CSP. No vault or keys enter it. */
export async function runPluginSandbox(source:string,input:PluginInput,parameters:Record<string,string|number|boolean>,milliseconds:number,bytes:number,signal:AbortSignal):Promise<PluginOutput>{
 signal.throwIfAborted()
 const payload=canonical({source,input,parameters,bytes})
 if(pluginBytes(payload)>262144)throw new ContractError('PLUGIN_INPUT_LIMIT','插件输入超过 256 KiB')
 const frozen=JSON.parse(payload)
 const frame=document.createElement('iframe'),nonce=crypto.randomUUID().replaceAll('-',''),workerSource=`'use strict';(()=>{const emit=self.postMessage.bind(self);self.onmessage=async e=>{try{const v=e.data;for(const name of ['fetch','XMLHttpRequest','WebSocket','EventSource','importScripts','Worker','SharedWorker','indexedDB','caches','postMessage']){try{Object.defineProperty(self,name,{value:undefined,writable:false,configurable:false})}catch{}}for(let proto=Object.getPrototypeOf(self);proto;proto=Object.getPrototypeOf(proto)){try{if(Object.hasOwn(proto,'postMessage'))Object.defineProperty(proto,'postMessage',{value:undefined,writable:false,configurable:false})}catch{}}const fn=new Function('input','parameters','"use strict";\\n'+v.source);const result=await fn(v.input,v.parameters);const json=JSON.stringify(result);if(typeof json!=='string'||new TextEncoder().encode(json).byteLength>v.bytes)throw Error('返回过大或无效');emit({ok:true,json})}catch{emit({ok:false})}};emit({ready:true});})();`
 frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('aria-hidden','true');frame.hidden=true
 const bootstrap=`window.addEventListener('message',e=>{if(e.source!==parent||!e.ports[0])return;const port=e.ports[0];const worker=new Worker(URL.createObjectURL(new Blob([${JSON.stringify(workerSource)}],{type:'text/javascript'})));worker.onmessage=e=>{port.postMessage(e.data);if(!e.data?.ready)worker.terminate()};worker.onerror=()=>{port.postMessage({ok:false});worker.terminate()};port.onmessage=e=>worker.postMessage(e.data);port.start()},{once:true});`
 frame.srcdoc=`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; connect-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; frame-src 'none'; worker-src blob:; script-src 'nonce-${nonce}' 'unsafe-eval'; base-uri 'none'; form-action 'none'"><script nonce="${nonce}">${bootstrap.replaceAll('</script','<\\/script')}</script>`
 const channel=new MessageChannel()
 return new Promise((resolve,reject)=>{
  let timer:ReturnType<typeof setTimeout>,finished=false,running=false
  const finish=(value?:PluginOutput,error?:Error)=>{if(finished)return;finished=true;clearTimeout(timer);signal.removeEventListener('abort',abort);channel.port1.close();channel.port2.close();frame.remove();error?reject(error):resolve(value!)}
  const abort=()=>finish(undefined,new ContractError('PLUGIN_STOPPED','插件已停止'))
  channel.port1.onmessage=event=>{const data=event.data;if(data?.ready){if(running)return;running=true;clearTimeout(timer);timer=setTimeout(()=>finish(undefined,new ContractError('PLUGIN_TIMEOUT','插件超时，主回复保留')),milliseconds);try{channel.port1.postMessage(frozen)}catch{finish(undefined,new ContractError('PLUGIN_SANDBOX','插件消息未能传送，主回复保留'))}return}if(!data?.ok||typeof data.json!=='string'||pluginBytes(data.json)>bytes){finish(undefined,new ContractError('PLUGIN_SANDBOX','插件返回无效，主回复保留'));return}try{finish(JSON.parse(data.json))}catch{finish(undefined,new ContractError('PLUGIN_SANDBOX','插件格式无效'))}}
  frame.onload=()=>{if(finished)return;try{frame.contentWindow!.postMessage('start','*',[channel.port2])}catch{finish(undefined,new ContractError('PLUGIN_SANDBOX','插件消息未能传送，主回复保留'))}}
  signal.addEventListener('abort',abort,{once:true});timer=setTimeout(()=>finish(undefined,new ContractError('PLUGIN_TIMEOUT','插件环境启动超时，主回复保留')),1000)
  document.body.appendChild(frame)
 })
}
