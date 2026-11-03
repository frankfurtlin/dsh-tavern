import {api,authorizationHeaders,authGeneration,refreshAuth,ApiError} from '../api'
import {backendBase} from '../platform/bridge'
import type {DurableEvent} from '../api/durable-stream'
export function worldApi<T>(path:string,options:Parameters<typeof api>[1]={}):Promise<T>{return api<T>(path,{...options,namespace:'world'})}
export async function worldEvents(taskId:string,afterSeq:number,signal:AbortSignal,onEvent:(event:DurableEvent)=>void){
  const epoch=authGeneration(),url=backendBase()+'/api/world/v1/tasks/'+encodeURIComponent(taskId)+'/events?afterSeq='+afterSeq
  let response=await fetch(url,{credentials:'include',headers:authorizationHeaders(),signal})
  if(response.status===401&&await refreshAuth())response=await fetch(url,{credentials:'include',headers:authorizationHeaders(),signal})
  if(epoch!==authGeneration())throw new ApiError('SESSION_CHANGED','账号已变化',401)
  if(!response.ok||!response.body)throw new ApiError('WORLD_STREAM_UNAVAILABLE','正在重新连接',response.status)
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer=''
  try{while(!signal.aborted){const chunk=await reader.read();if(epoch!==authGeneration())throw new ApiError('SESSION_CHANGED','账号已变化',401);if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true}).replace(/\r\n/g,'\n');let split:number;while((split=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,split);buffer=buffer.slice(split+2);const data=frame.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(data){try{const value=JSON.parse(data);if(value.taskId===taskId&&typeof value.seq==='number')onEvent(value)}catch{/* A malformed event is recovered by task snapshot polling. */}}}}}finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
}
