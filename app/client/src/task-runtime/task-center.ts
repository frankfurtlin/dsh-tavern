import {ContractError,type DomainKind} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {buildTaskIndex,taskIndexEntry} from './task-index'

const indexKinds:DomainKind[]=['task','settings','mediaJob','agentRun','story','session','world','companion','card']
export async function localTaskIndex(vault:LocalVault){
 // Listing never claims, recovers, starts or queries a provider request.
 return buildTaskIndex((await Promise.all(indexKinds.map(kind=>vault.list(kind)))).flat())
}
export async function stopTaskFromCenter(vault:LocalVault,id:string){
 const row=await vault.read(id),entry=row?taskIndexEntry(row,()=>undefined):null
 if(!row||!entry)throw new ContractError('TASK_NOT_FOUND','这个任务已删除或无法打开')
 if(!entry.canStop)throw new ContractError('TASK_NOT_ACTIVE','这个任务当前无需停止；已收到的内容保留')
 switch(entry.type){
  case 'text':await (await import('./active')).stopStoredText(vault,id);break
  case 'writing':await (await import('../writing/runtime')).stopWriting(vault,id);break
  case 'media':await (await import('../media/runtime')).stopMedia(vault,id);break
  case 'video':await (await import('../media/video-runtime')).stopVideo(vault,id);break
  case 'vector':await (await import('../media/vector-runtime')).stopVector(vault,id);break
  case 'agent':await (await import('../agent/runtime')).stopAgentRun(vault,id);break
  case 'skill':await (await import('../skills/runtime')).stopSkillRun(vault,id);break
  default:throw new ContractError('TASK_NOT_ACTIVE','请回到来源页面处理这项操作')
 }
 // A cancellation signal cannot prove that a disconnected executor stopped.
 // Keep its received text and request identity; never claim success from a noop.
 const current=await vault.read(id),result=current?taskIndexEntry(current,()=>undefined):null
 if(!result)throw new ContractError('TASK_STOP_UNCONFIRMED','停止请求已发送，但原记录暂时无法确认；请稍后查看')
 if(result.canStop)throw new ContractError('TASK_STOP_UNCONFIRMED','停止请求已发送，原执行结果暂时无法确认；已收到的内容保留，请稍后查看')
 return result
}
