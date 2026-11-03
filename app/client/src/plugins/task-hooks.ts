import {canonical,ContractError,type Document} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import type {TextTask} from '../task-runtime/local-text'
import type {PluginReport,PluginSnapshot,PluginTrace} from './contract'
import {pluginAuthority,runPluginHook} from './runtime'
type TraceSnapshot={snapshot:PluginSnapshot;trace:PluginTrace[]}
export const taskPlugins=(task:TextTask)=>task.promptTrace?.plugins as TraceSnapshot|undefined
export async function authorizeTaskPlugins(vault:LocalVault,task:TextTask,signal:AbortSignal){const plugins=taskPlugins(task);if(!plugins)return;for(const row of plugins.snapshot.plugins)await pluginAuthority(vault,plugins.snapshot,row.id,signal)}
export async function pluginReportWrite(vault:LocalVault,task:Document<TextTask>,hook:'afterText'|'afterCommit',signal:AbortSignal):Promise<WriteDocument|null>{
 const plugins=taskPlugins(task.value);if(!plugins||task.value.historical)return null
 const id='plugin-report:'+task.id,prior=await vault.read<PluginReport>(id)
 if(prior?.value.trace.some(row=>row.hook===hook))return null
 const previous=prior?.value.trace||plugins.trace,result=await runPluginHook(vault,plugins.snapshot,hook,task.value.text,'',task.value.speakerId,signal,previous),value:PluginReport={assetType:'plugin-report',targetId:task.value.targetId,taskId:task.id,snapshot:plugins.snapshot,trace:[...previous,...result.trace],historical:false,createdAt:prior?.value.createdAt||new Date().toISOString()}
 return {kind:'settings',id,expectedRevision:prior?.revision||0,value}
}
export async function persistTextPluginReport(vault:LocalVault,task:Document<TextTask>,signal:AbortSignal){
 const write=await pluginReportWrite(vault,task,'afterText',signal);if(!write)return
 const operation='plugins-after-text:'+task.id
 try{await vault.commitBundle(operation,[write],signal)}catch(error){if(!vault.unlocked||!(await vault.receipt(operation)))throw error;const row=await vault.read(write.id);if(canonical(row?.value)!==canonical(write.value))throw new ContractError('PLUGIN_CONFLICT','插件诊断已在另一窗口更新')}
}
