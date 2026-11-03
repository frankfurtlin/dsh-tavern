import {canonical,sha256,ContractError,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {validateWritingPreferenceLayer,validateWritingPreferences,type WritingPreferenceLayer,type WritingPreferences} from './preferences'
import {parseStoryDirections} from './directions'
import {hasPendingWritingSave} from './runtime'
import type {WritingRun} from './contract'
import type {WritingDirectionPlan,WritingComposeDraft} from './planning-contract'
async function save<T>(vault:LocalVault,id:string,revision:number,value:T){
 const operation='writing-settings:'+await sha256(canonical({id,revision,value})),document={kind:'settings' as const,id,expectedRevision:revision,value},hash=await sha256(canonical([document]))
 try{await vault.commitBundle(operation,[document])}catch(e){if(!vault.unlocked||(await vault.receipt(operation))?.payloadHash!==hash)throw e}
 return (await vault.read<T>(id))!
}
export async function writingPreferenceLayers(vault:LocalVault,storyId:string){
 const [global,book]=await Promise.all([vault.read<WritingPreferenceLayer>('settings:writing:global'),vault.read<WritingPreferenceLayer>('settings:writing:book:'+storyId)])
 for(const row of [global,book])if(row){if(row.kind!=='settings')throw new ContractError('WRITING_PREFERENCES','写作设置范围无效');validateWritingPreferenceLayer(row.value,row.id)}
 return {global,book}
}
export async function saveWritingPreferenceLayer(vault:LocalVault,scope:'global'|'book',storyId:string,revision:number,values:Partial<WritingPreferences>){validateWritingPreferences(values);return save(vault,scope==='global'?'settings:writing:global':'settings:writing:book:'+storyId,revision,{assetType:'writing-preferences' as const,scope,storyId:scope==='global'?null:storyId,values})}
export async function saveWritingComposeDraft(vault:LocalVault,revision:number,value:WritingComposeDraft,id='settings:writing-draft:'+value.storyId){return save(vault,id,revision,value)}
export async function saveWritingComposeView(vault:LocalVault,storyId:string,draftId:string){const id='settings:writing-compose-view:'+storyId,head=await vault.read<import('./planning-contract').WritingComposeView>(id);if(head?.value.draftId===draftId){await vault.checkPreconditions([{kind:'settings',id,revision:head.revision}]);return head}return save(vault,id,head?.revision||0,{assetType:'writing-compose-view' as const,storyId,draftId})}
export async function keepWritingDirections(vault:LocalVault,runId:string):Promise<Document<WritingDirectionPlan>>{
 const run=await vault.read<WritingRun>(runId)
 if(run?.kind!=='settings'||run.value.purpose?.kind!=='directions'||run.value.status!=='completed'||hasPendingWritingSave(vault,run.id))throw new ContractError('WRITING_DIRECTIONS','本次方向未保存完整，原返回保留')
 const id='writing-directions:'+await sha256(run.id),prior=await vault.read<WritingDirectionPlan>(id)||(await vault.list<WritingDirectionPlan>('settings')).find(p=>p.value.assetType==='writing-directions'&&p.value.runId===run.id)
 if(prior){if(prior.value.runId!==run.id)throw new ContractError('WRITING_DIRECTIONS','方向来源不一致');return prior}
 const original=parseStoryDirections(run.value.chapters[0]!.raw??run.value.chapters[0]!.text)
 return save(vault,id,0,{assetType:'writing-directions',runId:run.id,runRevision:run.revision,storyId:run.value.storyId,original,choices:original.map((c,i)=>({id:'direction:'+i,title:c.title,direction:c.direction})),createdAt:run.value.updatedAt})
}
export async function saveWritingDirectionChoices(vault:LocalVault,row:Document<WritingDirectionPlan>,choices:WritingDirectionPlan['choices']){const head=await vault.read<WritingDirectionPlan>(row.id);if(head?.kind!=='settings'||head.revision!==row.revision||canonical(head.value)!==canonical(row.value))throw new ContractError('REVISION_CONFLICT','另一窗口已修改这组方向，当前输入可另存');if(canonical(choices)===canonical(row.value.choices)){await vault.checkPreconditions([{kind:'settings',id:row.id,revision:row.revision}]);return head}return save(vault,row.id,row.revision,{...row.value,choices})}
export async function copyWritingDirectionPlan(vault:LocalVault,row:Document<WritingDirectionPlan>,choices:WritingDirectionPlan['choices']){return save(vault,'writing-directions:'+crypto.randomUUID(),0,{...row.value,choices,createdAt:new Date().toISOString()})}
