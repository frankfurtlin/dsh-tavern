import {ContractError,type Document,type ProviderConfiguration,type ProgressAnchor} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {LocalWritingExecution,prepareWriting,hasPendingWritingSave} from './runtime'
import type {WritingRun} from './contract'
import {addWritingVariant} from './edits-runtime'
import type {WritingEdit} from './edits-contract'
import type {WritingPreferences} from './preferences'
import {editSourceConditions} from './edit-authority'

export async function prepareEditWriting(vault:LocalVault,config:Document<ProviderConfiguration>,editId:string,revision:number,mode:'rewrite'|'expand',direction:string,length=800){
 const row=await vault.read<WritingEdit>(editId);if(row?.kind!=='settings'||row.value.assetType!=='writing-edit'||row.revision!==revision)throw new ContractError('REVISION_CONFLICT','选区编辑已变化，未调用模型')
 const source=row.value.source,anchor={storyId:source.storyId,branchId:source.kind==='branch'?source.id:null,...row.value.selection.start}
 await editSourceConditions(vault,source)
 return prepareWriting(vault,config,anchor,{chapterCount:1,direction,length,purpose:{kind:mode,editId,editRevision:revision},...(source.policy==='forkPinned'?{sourceRevision:source.revision,sourceStoryRevision:source.storyRevision}:{})})
}
async function authority(vault:LocalVault,run:WritingRun){
 const purpose=run.purpose;if(purpose&&purpose.kind!=='directions'){
  const edit=await vault.read<WritingEdit>(purpose.editId);if(edit?.revision!==purpose.editRevision||edit.value.assetType!=='writing-edit')throw new ContractError('REVISION_CONFLICT','选区编辑在发送前变化，未继续调用模型')
  await editSourceConditions(vault,edit.value.source)
 }
}
export async function executeWritingAction(vault:LocalVault,id:string,changed:(row:Document<WritingRun>)=>void=()=>{},transport:typeof fetch=fetch){const row=await vault.read<WritingRun>(id);if(row?.kind!=='settings'||!row.value.purpose)throw new ContractError('WRITING_ACTION','找不到这次选区／方向请求');return new LocalWritingExecution(vault,id,changed,transport,()=>authority(vault,row.value)).execute()}
export async function keepEditWriting(vault:LocalVault,runId:string,reconcile=false){
 const run=await vault.read<WritingRun>(runId),purpose=run?.value.purpose;if(run?.kind!=='settings'||!purpose||purpose.kind==='directions')throw new ContractError('WRITING_ACTION','找不到这次选区结果')
 if(hasPendingWritingSave(vault,runId))throw new ContractError('WRITING_SAVE_PENDING','请先仅重试保存收到的文字，不会重新请求模型')
 const chapter=run.value.chapters[0]!;if(run.value.status==='queued'||run.value.status==='running')throw new ContractError('WRITING_ACTION','这次写法还在生成')
 if(!chapter.text.trim())throw new ContractError('NO_OUTPUT','没有可读正文，原返回已保留在请求记录中')
 const edit=await vault.read<WritingEdit>(purpose.editId);if(edit?.kind!=='settings'||edit.value.assetType!=='writing-edit')throw new ContractError('WRITING_EDIT_MISSING','原选区记录缺失')
 const variantId='writing-variant:'+run.id,prior=edit.value.variants.find(v=>v.runId===run.id)
 if(prior){if(prior.raw!==(chapter.raw??chapter.text)||prior.text!==chapter.text||prior.origin!==purpose.kind)throw new ContractError('IDEMPOTENCY_CONFLICT','这次候选与已保存的返回不同');return edit}
 return addWritingVariant(vault,edit.id,reconcile?edit.revision:purpose.editRevision,{origin:purpose.kind,text:chapter.text,raw:chapter.raw??chapter.text,status:chapter.status==='complete'?'ready':'partial',finishReason:chapter.finishReason,runId:run.id},variantId)
}
export async function prepareDirectionWriting(vault:LocalVault,config:Document<ProviderConfiguration>,anchor:ProgressAnchor,direction='',once:Partial<WritingPreferences>={}){
 return prepareWriting(vault,config,anchor,{chapterCount:1,direction,...once,purpose:{kind:'directions'}})
}
