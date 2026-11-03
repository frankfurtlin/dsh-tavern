import type {Task} from './types'
import type {DurableEvent} from './durable-stream'
import {api,refreshAuth,authGeneration} from './index'
import {backendBase} from '../platform/bridge'
import {authorizationHeaders} from './index'

export function taskFinished(task:Task){return ['succeeded','failed','cancelled'].includes(task.state)&&task.persistenceState!=='recoverable'}

export function watchTask(taskId:string,onTask:(task:Task)=>Promise<void>|void,onError:(error:Error)=>void,onEvent?:(event:DurableEvent)=>boolean|Promise<boolean>){
  const auth=authGeneration()
  let stopped=false,terminal=false,cursor=0,failures=0,connectionGeneration=0,controller:AbortController|null=null,timer:ReturnType<typeof setTimeout>|undefined
  const valid=(generation:number)=>!stopped&&!document.hidden&&auth===authGeneration()&&generation===connectionGeneration
  async function snapshot(generation:number){if(!valid(generation))return;try{const task=await api<Task>('/tasks/'+taskId);if(!valid(generation)||task.eventCursor<cursor)return;cursor=Math.max(cursor,task.eventCursor);terminal=taskFinished(task);await onTask(task);if(valid(generation)&&terminal)controller?.abort()}catch(error){if(valid(generation))onError(error as Error)}}
  function later(generation:number){if(!valid(generation)||terminal)return;clearTimeout(timer);timer=setTimeout(start,failures>=3?2000:1000)}
  async function start(){
    if(stopped||terminal||document.hidden||auth!==authGeneration())return
    const generation=++connectionGeneration
    await snapshot(generation);if(!valid(generation)||terminal)return
    if(failures>=3){later(generation);return}
    const connection=new AbortController();controller=connection
    const request=()=>fetch(backendBase()+`/api/reader/v1/tasks/${taskId}/events?afterSeq=${cursor}`,{headers:authorizationHeaders(),credentials:'include',signal:connection.signal})
    try{
      let response=await request();if(!valid(generation))return
      if(response.status===401){const refreshed=await refreshAuth();if(!valid(generation))return;if(refreshed){response=await request();if(!valid(generation))return}}
      if(!response.ok||!response.body)throw new Error('任务事件连接暂时不可用')
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffered=''
      while(valid(generation)&&!terminal){const {value,done}=await reader.read();if(!valid(generation)||done)break;buffered+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');let boundary:number;while(valid(generation)&&!terminal&&(boundary=buffered.indexOf('\n\n'))>=0){const event=buffered.slice(0,boundary);buffered=buffered.slice(boundary+2);const line=event.split('\n').find(l=>l.startsWith('data: '));if(line){const payload=JSON.parse(line.slice(6)) as DurableEvent;if(Number.isSafeInteger(payload.seq)&&payload.seq>cursor){const applied=!!onEvent&&payload.taskId===taskId&&payload.seq===cursor+1&&await onEvent(payload);if(!valid(generation))break;if(applied)cursor=payload.seq;else await snapshot(generation);if(!valid(generation))break}}}}
      if(valid(generation))failures=0
    }catch(error){if(valid(generation)&&(error as Error).name!=='AbortError'){failures++;onError(error as Error)}}
    finally{connection.abort();if(controller===connection)controller=null;if(valid(generation))later(generation)}
  }
  function pause(){++connectionGeneration;controller?.abort();controller=null;clearTimeout(timer)}
  function resume(){pause();failures=0;start()}
  document.addEventListener('visibilitychange',visibility)
  function visibility(){if(document.hidden)pause();else resume()}
  window.addEventListener('reader-resume',resume);start()
  return()=>{stopped=true;pause();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('reader-resume',resume)}
}
