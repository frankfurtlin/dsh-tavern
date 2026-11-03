import {api,authGeneration} from './index'
import {appForeground} from '../platform/bridge'
import type {StoryRun} from './types'

// Progress is durable on the parent run; it may change without a new SSE event.
export function watchStoryRun(id:string,onRun:(run:StoryRun,current:()=>boolean)=>Promise<void>|void,onError:(error:Error)=>void){
  const auth=authGeneration();let stopped=false,paused=false,terminal=false,generation=0,timer:ReturnType<typeof setTimeout>|undefined
  const valid=(current:number)=>!stopped&&!paused&&!document.hidden&&appForeground()&&auth===authGeneration()&&current===generation
  async function refresh(){
    if(stopped||paused||document.hidden||!appForeground()||auth!==authGeneration())return
    const current=++generation
    try{
      const value=await api<StoryRun>('/story-runs/'+id);if(!valid(current))return
      await onRun(value,()=>valid(current));if(!valid(current))return
      terminal=['succeeded','failed','cancelled'].includes(value.state)
    }catch(e){if(valid(current))onError(e as Error)}
    if(valid(current)&&!terminal)timer=setTimeout(refresh,1500)
  }
  function pause(){paused=true;generation++;clearTimeout(timer)}
  function resume(){paused=false;terminal=false;generation++;clearTimeout(timer);void refresh()}
  function visibility(){if(document.hidden)pause();else resume()}
  document.addEventListener('visibilitychange',visibility);window.addEventListener('reader-save',pause);window.addEventListener('reader-resume',resume)
  void refresh()
  return()=>{stopped=true;pause();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('reader-save',pause);window.removeEventListener('reader-resume',resume)}
}
