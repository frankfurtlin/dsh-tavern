import {LocalTextExecution,type TextTask} from './local-text'
import type {Document} from '../domain/local-contract'
import {LocalVault} from '../repositories/local-vault'
interface Execution {runner:LocalTextExecution;listeners:Set<(task:Document<TextTask>)=>void>;work:Promise<Document<TextTask>>}
const executions=new Map<string,Execution>()
const key=(vault:LocalVault,id:string)=>vault.id+':'+id
const controls=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('reader-local-task-control-v1')
;(controls as (BroadcastChannel&{unref?:()=>void})|null)?.unref?.()
controls?.addEventListener('message',event=>{const value=event.data;if(value?.type==='stop-task'&&typeof value.vaultId==='string'&&typeof value.taskId==='string'&&value.vaultId.length<=160&&value.taskId.length<=160)executions.get(value.vaultId+':'+value.taskId)?.runner.stop()})
/** In-page navigation detaches the view, not the durable request. Reload never auto-starts. */
export function startExecution(vault:LocalVault,id:string,listener:(task:Document<TextTask>)=>void){
  const executionKey=key(vault,id),existing=executions.get(executionKey)
  if(existing){existing.listeners.add(listener);return {work:existing.work,detach:()=>existing.listeners.delete(listener),stop:()=>existing.runner.stop()}}
  const listeners=new Set([listener]),runner=new LocalTextExecution(vault,id,task=>{for(const notify of listeners){try{notify(task)}catch{/* Views do not control persistence. */}}})
  const work=runner.execute().finally(()=>executions.delete(executionKey)),entry={runner,listeners,work};executions.set(executionKey,entry)
  return {work,detach:()=>listeners.delete(listener),stop:()=>runner.stop()}
}
export function stopExecution(vault:LocalVault,id:string){void stopStoredText(vault,id).catch(()=>{/* A concurrent runner also receives the cancellation signal. */})}
export async function stopStoredText(vault:LocalVault,id:string){executions.get(key(vault,id))?.runner.stop();controls?.postMessage({type:'stop-task',vaultId:vault.id,taskId:id});const row=await vault.read<TextTask>(id);if(row?.kind==='task'&&row.value.status==='queued')await vault.commitBundle('cancel-queued:'+id,[{kind:'task',id,expectedRevision:row.revision,value:{...row.value,status:'cancelled',updatedAt:new Date().toISOString()}}])}
export function hasExecution(vault:LocalVault,id:string){return executions.has(key(vault,id))}
