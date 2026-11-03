import {api,authGeneration,authorizationHeaders,refreshAuth} from './index'
import {backendBase,accountId,appForeground} from '../platform/bridge'
import type {GenerationSnapshot,GenerationEvent} from './generation-types'
import {parseGenerationSnapshot,mergeGenerationSnapshot,reduceGeneration,generationFinished} from './generation-stream'

export function watchGeneration(id:string,onValue:(value:GenerationSnapshot,current:()=>boolean)=>Promise<void>|void,onError:(error:Error)=>void,scope:()=>boolean=()=>true){
  const auth=authGeneration(),owner=accountId(),base=backendBase()
  let stopped=false,paused=false,connectionVersion=0,value:GenerationSnapshot|null=null,controller:AbortController|null=null,timer:ReturnType<typeof setTimeout>|undefined
  const identity=()=>auth===authGeneration()&&owner===accountId()&&base===backendBase()&&scope()
  const valid=(version:number)=>!stopped&&!paused&&!document.hidden&&appForeground()&&identity()&&version===connectionVersion
  async function publish(next:GenerationSnapshot,version:number){
    if(!valid(version))return
    value=mergeGenerationSnapshot(value,next)
    await onValue(value,()=>valid(version))
  }
  async function snapshot(version:number){
    const received=await api<unknown>('/generations/'+encodeURIComponent(id))
    if(!valid(version))return
    const next=parseGenerationSnapshot(received);if(next.id!==id)throw new Error('写作记录属于其他任务')
    await publish(next,version)
  }
  async function event(input:unknown,version:number){
    if(!valid(version)||!value)return
    if(!input||typeof input!=='object')throw new Error('写作事件暂时不完整')
    const result=reduceGeneration(value,input as GenerationEvent)
    if(result.kind==='resync')await snapshot(version)
    else if(result.kind==='applied')await publish(result.value,version)
  }
  function retry(version:number){if(valid(version)&&!(value&&generationFinished(value))){clearTimeout(timer);timer=setTimeout(()=>void start(),1200)}}
  async function start(){
    if(stopped||paused||document.hidden||!appForeground()||!identity())return
    const version=++connectionVersion
    let connection:AbortController|null=null
    try{
      await snapshot(version);if(!valid(version)||!value||generationFinished(value))return
      connection=new AbortController();controller=connection
      const request=()=>fetch(base+`/api/reader/v1/tasks/${encodeURIComponent(value!.taskId)}/events?afterSeq=${value!.eventCursor}`,{headers:authorizationHeaders(),credentials:'include',signal:connection!.signal})
      let response=await request();if(!valid(version))return
      if(response.status===401){const refreshed=await refreshAuth();if(!valid(version))return;if(refreshed){response=await request();if(!valid(version))return}}
      if(!response.ok||!response.body)throw new Error('写作连接暂时不可用，已收到的正文仍保留')
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffered=''
      try{
        while(valid(version)&&!(value&&generationFinished(value))){
          const chunk=await reader.read();if(!valid(version)||chunk.done)break
          buffered+=decoder.decode(chunk.value,{stream:true});if(buffered.length>1024*1024)throw new Error('写作事件过大，请载入已保存的正文')
          let match:RegExpExecArray|null
          while(valid(version)&&(match=/\r?\n\r?\n/.exec(buffered))){
            const frame=buffered.slice(0,match.index);buffered=buffered.slice(match.index+match[0].length)
            const lines=frame.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,''))
            if(lines.length)await event(JSON.parse(lines.join('\n')),version)
          }
        }
      }finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
      // A clean TCP close is only a transport observation. A new GET determines
      // saved/completed/unknown; reconnect never calls a creating endpoint.
    }catch(error){if(valid(version)&&(error as Error).name!=='AbortError')onError(error as Error)}
    finally{connection?.abort();if(controller===connection)controller=null;retry(version)}
  }
  function pause(){paused=true;connectionVersion++;clearTimeout(timer);controller?.abort();controller=null}
  function resume(){if(stopped||!identity())return;pause();paused=false;void start()}
  function visibility(){if(document.hidden)pause();else resume()}
  document.addEventListener('visibilitychange',visibility);window.addEventListener('reader-save',pause);window.addEventListener('reader-resume',resume)
  void start()
  return()=>{stopped=true;pause();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('reader-save',pause);window.removeEventListener('reader-resume',resume)}
}
