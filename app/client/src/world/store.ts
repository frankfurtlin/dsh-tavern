import {trackProductEvent} from "../analytics/product-events"
import {defineStore} from 'pinia'
import {ref,computed} from 'vue'
import {ApiError,authGeneration,newKey} from '../api'
import {worldApi,worldEvents} from './api'
import {reduceDurable,streamSnapshot} from '../api/durable-stream'
import {cacheGet,cacheSet,cacheUpdate} from '../platform/bridge'
import {worldTaskFinished,worldTaskRecovering} from './task-state'
import {taskError} from './task-error'
import type {WorldInfo,WorldLine,WorldCheckpoint,WorldTask,WorldTurn,ActionKind} from './types'
export const useWorld=defineStore('world',()=>{
  const world=ref<WorldInfo|null>(null),line=ref<WorldLine|null>(null),history=ref<WorldCheckpoint[]>([]),task=ref<WorldTask|null>(null),drafts=ref<WorldTurn[]>([]),input=ref(''),error=ref(''),busy=ref(false),connected=ref(true)
  let generation=0,controller:AbortController|null=null,timer:ReturnType<typeof setTimeout>|undefined
  const running=computed(()=>!!task.value&&!worldTaskFinished(task.value)),recovering=computed(()=>worldTaskRecovering(task.value))
  const state=computed(()=>line.value?.head.state)
  const lastAction=ref('')
  const retryMessage=computed(()=>!running.value&&task.value&&['failed','cancelled'].includes(task.value.state)?drafts.value.find(d=>d.taskId===task.value?.id)?.action||lastAction.value:'')
  function restoreAction(){if(retryMessage.value&&!running.value&&!busy.value){input.value=retryMessage.value;void saveInput()}}
  function stopWatching(){generation++;controller?.abort();controller=null;clearTimeout(timer)}
  function reset(){stopWatching();world.value=null;line.value=null;history.value=[];task.value=null;drafts.value=[];input.value='';lastAction.value='';error.value='';busy.value=false}
  window.addEventListener('reader-private-cleared',reset)
  async function readDrafts(root:string,valid:()=>boolean){
    const items:WorldTurn[]=[];let cursor:string|null=null
    do{const page:{items:WorldTurn[];nextCursor?:string|null}=await worldApi(root+'/drafts'+(cursor?'?cursor='+encodeURIComponent(cursor):''),{cache:true});if(!valid())return [];items.push(...page.items);cursor=page.nextCursor||null}while(cursor)
    return items.filter(item=>item.draftText?.trim()||item.chapters?.length)
  }
  async function load(worldId:string,lineId?:string){
    stopWatching();const version=generation,epoch=authGeneration(),valid=()=>version===generation&&epoch===authGeneration();busy.value=true;error.value=''
    const clearLine=()=>{line.value=null;history.value=[];drafts.value=[];task.value=null;input.value=''}
    if(world.value?.id!==worldId||lineId&&line.value?.id!==lineId){world.value=null;line.value=null;history.value=[];drafts.value=[];task.value=null;input.value=''}
    try{let info=await worldApi<WorldInfo>('/worlds/'+worldId,{cache:true});if(!valid())return;world.value=info;let id=lineId||info.activeLineId;if(!id){clearLine();return}
      const isDeleted=(e:unknown)=>e instanceof Error&&'code'in e&&e.code==='WORLD_LINE_DELETED'&&'status'in e&&e.status===404
      async function readLine(selectedId:string){
        try{
          const deleted=await cacheGet<boolean>('world-line-deleted:'+worldId+':'+selectedId);if(!valid())return null
          if(deleted)throw Object.assign(new Error('这条世界线已删除'),{code:'WORLD_LINE_DELETED',status:404})
          const root='/worlds/'+worldId+'/lines/'+selectedId
          const loaded=await worldApi<WorldLine>(root,{cache:true});if(!valid())return null
          const [items,saved,typed]=await Promise.all([worldApi<{items:WorldCheckpoint[]}>(root+'/history',{cache:true}),readDrafts(root,valid),cacheGet<string>('world-input:'+worldId+':'+selectedId)])
          if(!valid())return null
          return {loaded,items:items.items,saved,typed}
        }catch(e){
          if(valid()&&isDeleted(e)){
            clearLine()
            await cacheSet('world-line-deleted:'+worldId+':'+selectedId,true).catch(()=>{})
          }
          throw e
        }
      }
      let selected
      try{selected=await readLine(id)}
      catch(e){
        if(!valid())return
        if(!isDeleted(e))throw e
        // Only an owned tombstone authorizes fallback. Fetch an authoritative
        // entry again; foreign/missing IDs and network failures never redirect.
        info=await worldApi<WorldInfo>('/worlds/'+worldId);if(!valid())return;world.value=info
        const replacement=info.activeLineId;if(!replacement||replacement===id)return
        // Only one retained entry is attempted. A second deletion is marked by
        // readLine and remains an error rather than causing a fallback loop.
        id=replacement;selected=await readLine(id)
      }
      if(!valid()||!selected)return
      line.value=selected.loaded;history.value=selected.items;drafts.value=selected.saved;input.value=selected.typed||'';task.value=null;if(selected.loaded.pendingTaskId)void watchTask(selected.loaded.pendingTaskId);void resumeReceipt(worldId,id)
    }catch(e){if(valid()){if(e instanceof Error&&'status'in e&&e.status===404)clearLine();error.value=(e as Error).message}}finally{if(valid())busy.value=false}
  }
  async function reloadHead(){const w=world.value,l=line.value;if(!w||!l)return;const version=generation,epoch=authGeneration(),valid=()=>version===generation&&epoch===authGeneration();const root='/worlds/'+w.id+'/lines/'+l.id;const [loaded,items,saved]=await Promise.all([worldApi<WorldLine>(root,{cache:true}),worldApi<{items:WorldCheckpoint[]}>(root+'/history',{cache:true}),readDrafts(root,valid)]);if(!valid())return;line.value=loaded;history.value=items.items;drafts.value=saved}
  async function resumeReceipt(w:string,l:string){
    const version=generation,epoch=authGeneration(),valid=()=>version===generation&&epoch===authGeneration()&&world.value?.id===w&&line.value?.id===l
    const slot='world-pending:'+w+':'+l,pending=await cacheGet<{key:string;message:string}>(slot)
    if(!pending||!valid())return
    try{
      const receipt=await worldApi<{taskId:string}>('/worlds/'+w+'/lines/'+l+'/actions/'+encodeURIComponent(pending.key))
      if(!valid())return
      await cacheUpdate<{key:string;message:string}|null>(slot,saved=>saved?.key===pending.key?null:saved||null,valid)
      if(!valid())return
      if(input.value===pending.message){input.value='';await saveInput()}
      if(valid()&&(!line.value?.pendingTaskId||line.value.pendingTaskId===receipt.taskId)&&task.value?.id!==receipt.taskId)void watchTask(receipt.taskId)
    }catch{/* Observe the receipt; never resend an uncertain request. */}
  }

  async function saveInput(){if(world.value&&line.value)await cacheSet('world-input:'+world.value.id+':'+line.value.id,input.value)}
  async function watchTask(id:string){
    controller?.abort();clearTimeout(timer);const abort=new AbortController();controller=abort;const version=generation,epoch=authGeneration(),valid=()=>version===generation&&epoch===authGeneration()&&!abort.signal.aborted;let cursor=0,polling=false
    async function poll(){if(!valid()||polling)return;polling=true;try{const value=await worldApi<WorldTask>('/tasks/'+id);if(!valid()||value.eventSeq<cursor)return;if(worldTaskRecovering(value)&&!value.stream.text&&task.value?.id===id&&task.value.stream.text)value.stream={...value.stream,text:task.value.stream.text};task.value=value;cursor=value.eventSeq;connected.value=true;if(worldTaskFinished(value)){abort.abort();if(value.errorCode)error.value=taskError(value.errorCode,value.state,!!value.stream.text);await reloadHead();return}}catch(e){if(valid()){connected.value=false;error.value=(e as Error).message}}finally{polling=false;if(valid()){clearTimeout(timer);timer=setTimeout(()=>void poll(),900)}}}
    await poll();if(!valid())return
    void worldEvents(id,cursor,abort.signal,event=>{
      if(!valid())return
      const current=task.value
      if(!current||current.id!==id||!current.stream.contentId&&current.stream.text){void poll();return}
      const previous=streamSnapshot(id,cursor,current.stream),next=reduceDurable(previous,event)
      if(next.resync){void poll();return}
      cursor=next.state.seq
      task.value={...current,eventSeq:cursor,stream:next.state.content&&next.state.content!==previous.content?{...current.stream,...next.state.content,version:Math.max((current.stream.version||0)+1,next.state.content.snapshotVersion)}:current.stream}
    }).catch(()=>{if(valid())connected.value=false})
  }
  async function act(message=input.value,kind:ActionKind='action',targetEntityId?:string,sceneExitId?:string){
    const w=world.value,l=line.value;if(!w||!l||!message.trim()||running.value||busy.value)return;const epoch=authGeneration(),version=generation,submitted=input.value;busy.value=true;error.value=''
    const valid=()=>epoch===authGeneration()&&version===generation
    type Pending={key:string;message:string;kind:ActionKind;expectedCheckpointId:string;targetEntityId?:string;sceneExitId?:string}
    const slot='world-pending:'+w.id+':'+l.id
    let persisted=false,pendingKey=""
    try{
      await saveInput();if(!valid())return
      const pending=await cacheUpdate<Pending|null>(slot,saved=>saved||{key:newKey(),message,kind,expectedCheckpointId:l.headCheckpointId,...(targetEntityId?{targetEntityId}:{}),...(sceneExitId?{sceneExitId}:{})},valid)
      if(!pending||!valid())return
      persisted=true;pendingKey=pending.key;lastAction.value=message
      if(pending.message!==message||pending.kind!==kind||pending.targetEntityId!==targetEntityId||pending.sceneExitId!==sceneExitId)throw new Error('上次行动尚未确认，请先恢复它，再提交新的行动。')
      const result=await worldApi<{taskId:string}>('/worlds/'+w.id+'/lines/'+l.id+(pending.sceneExitId?'/scene/travel':pending.targetEntityId?'/scene/actions':'/actions'),{method:'POST',key:pending.key,body:{message:pending.message,kind:pending.kind,expectedCheckpointId:pending.expectedCheckpointId,turnId:pending.key,...(pending.targetEntityId?{targetEntityId:pending.targetEntityId}:{}),...(pending.sceneExitId?{sceneExitId:pending.sceneExitId}:{})}})
      if(!valid())return
      trackProductEvent('action.submit',{worldId:w.id,lineId:l.id,taskId:result.taskId,mode:pending.targetEntityId?'dialogue':pending.sceneExitId?'travel':pending.kind});await cacheUpdate<Pending|null>(slot,saved=>saved?.key===pending.key?null:saved||null,valid)
      if(!valid())return
      if(input.value===submitted&&message===submitted)input.value=''
      await saveInput();if(!valid())return;void watchTask(result.taskId)
    }catch(e){
      if(valid()){
        error.value=(e as Error).message
        if(persisted&&e instanceof ApiError&&['BUDGET_NOT_AUTHORIZED','CONTEXT_BUDGET_EXCEEDED','WORLD_ACTION_INVALID','WORLD_TARGET_UNAVAILABLE'].includes(e.code)){
          await cacheUpdate<Pending|null>(slot,saved=>saved?.key===pendingKey?null:saved||null,valid);persisted=false
        }
        if(persisted){await reloadHead().catch(()=>{});if(!valid())return;if(line.value?.pendingTaskId)void watchTask(line.value.pendingTaskId);void resumeReceipt(w.id,l.id)}
      }
    }finally{if(valid())busy.value=false}
  }
  async function cancel(){if(task.value)await worldApi('/tasks/'+task.value.id+'/cancel',{method:'POST',body:{}})}
  return{world,line,history,task,drafts,input,error,busy,connected,running,recovering,state,retryMessage,restoreAction,load,reloadHead,saveInput,watchTask,act,cancel,stopWatching,reset}
})
