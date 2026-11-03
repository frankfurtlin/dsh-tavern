import {canonical,sha256,ContractError,type Document,type Paragraph,type LocalStory} from '../domain/local-contract'
import {LocalVault,type WriteDocument} from '../repositories/local-vault'
import {readingLine} from './runtime'
import {editChapters,replacementText,selectionText,validateWritingEdit,type WritingEdit,type EditVariant,type TextSelection} from './edits-contract'
import type {LocalBranch} from './contract'
import {editSourceConditions} from './edit-authority'
const clone=<T>(v:T):T=>JSON.parse(canonical(v)),stamp=()=>new Date().toISOString()
async function commit(vault:LocalVault,operation:string,documents:WriteDocument[],conditions:Parameters<LocalVault['commitBundle']>[3]=[],signal?:AbortSignal){signal?.throwIfAborted();const hash=await sha256(canonical(conditions.length?{documents,preconditions:conditions}:documents));try{await vault.commitBundle(operation,documents,signal,conditions)}catch(error){if(!vault.unlocked||(await vault.receipt(operation))?.payloadHash!==hash)throw error}}
export async function createWritingEdit(vault:LocalVault,storyId:string,branchId:string|null,selection:TextSelection,signal?:AbortSignal,sourceRevision?:number){
 signal?.throwIfAborted()
 const {story,branch,line,storyHead,branchHead}=await readingLine(vault,storyId,branchId,sourceRevision),chapter=line.value.chapters.find(c=>c.paragraphIds.includes(selection.start.paragraphId))
 if(branchHead?.value.archived)throw new ContractError('BRANCH_ARCHIVED','这条故事线已归档，可以阅读；恢复后再修订')
 const sourceStory=sourceRevision!==undefined&&branch?await vault.read<LocalStory>(storyId,branch.value.storyRevision):story
 if(sourceStory?.kind!=='story')throw new ContractError('WRITING_EDIT_MISSING','选区绑定的故事历史版本缺失，原文保留')
 signal?.throwIfAborted()
 if(!chapter||!chapter.paragraphIds.includes(selection.end.paragraphId))throw new ContractError('WRITING_EDIT_RANGE','请选择同一章内要编辑的文字')
 const start=chapter.paragraphIds.indexOf(selection.start.paragraphId),end=chapter.paragraphIds.indexOf(selection.end.paragraphId),ids=chapter.paragraphIds.slice(start,end+1),paragraphs=await vault.readMany<Paragraph>(ids)
 signal?.throwIfAborted()
 if(end<start||paragraphs.length!==ids.length||paragraphs.some(p=>p.kind!=='paragraph'))throw new ContractError('WRITING_EDIT_RANGE','选区不属于这条故事线')
 const at=stamp(),quote=selectionText(selection,paragraphs),original:EditVariant={id:crypto.randomUUID(),origin:'source',text:quote,raw:quote,status:'ready',finishReason:null,createdAt:at},id='writing-edit:'+crypto.randomUUID(),value:WritingEdit={assetType:'writing-edit',source:{id:branch?.id||story.id,kind:branch?'branch':'story',revision:branch?.revision||story.revision,storyId,storyRevision:sourceStory.revision,chapters:clone(line.value.chapters)},selection:clone(selection),paragraphs:paragraphs.map(p=>({id:p.id,revision:p.revision,value:clone(p.value)})),chapterId:chapter.id,targetBranchId:crypto.randomUUID(),variants:[original],selectedVariantId:original.id,appliedVariantId:null,appliedRevision:0,draftText:quote,direction:'',historical:false,createdAt:at,updatedAt:at}
 validateWritingEdit(value,id)
 if(sourceRevision!==undefined)value.source.policy='forkPinned'
 await commit(vault,'writing-edit-create:'+id,[{kind:'settings',id,expectedRevision:0,value}],[{id:story.id,kind:'story',revision:storyHead.revision},...(branchHead?[{id:branchHead.id,kind:'branch' as const,revision:branchHead.revision}]:[]),...paragraphs.map(p=>({id:p.id,kind:'paragraph' as const,revision:p.revision}))],signal)
 return (await vault.read<WritingEdit>(id))!
}
export async function addWritingVariant(vault:LocalVault,id:string,expectedRevision:number,variant:Omit<EditVariant,'id'|'createdAt'>,variantId:string=crypto.randomUUID()){
 const row=await vault.read<WritingEdit>(id);if(row?.kind!=='settings'||row.value.assetType!=='writing-edit'||row.revision!==expectedRevision)throw new ContractError('REVISION_CONFLICT','编辑候选已变化，当前输入仍可另存')
 const value=clone(row.value),next={...clone(variant),id:variantId,createdAt:stamp()};value.variants.push(next);value.selectedVariantId=next.id;value.draftText=next.text;value.updatedAt=stamp()
 await commit(vault,'writing-edit-variant:'+id+':'+variantId,[{kind:'settings',id,expectedRevision,value}]);return (await vault.read<WritingEdit>(id))!
}
export async function selectWritingVariant(vault:LocalVault,id:string,expectedRevision:number,variantId:string){const row=await vault.read<WritingEdit>(id);if(row?.kind!=='settings'||row.revision!==expectedRevision||row.value.assetType!=='writing-edit')throw new ContractError('REVISION_CONFLICT','编辑记录已变化');const variant=row.value.variants.find(v=>v.id===variantId);if(!variant)throw new ContractError('WRITING_EDIT_VARIANT','找不到旧候选');if(row.value.selectedVariantId===variantId&&row.value.draftText===variant.text)return row;await commit(vault,'writing-edit-select:'+id+':'+expectedRevision+':'+variantId,[{kind:'settings',id,expectedRevision,value:{...row.value,selectedVariantId:variantId,draftText:variant.text,updatedAt:stamp()}}]);return (await vault.read<WritingEdit>(id))!}
export async function saveWritingEditDraft(vault:LocalVault,id:string,expectedRevision:number,text:string,direction?:string){const row=await vault.read<WritingEdit>(id);if(row?.kind!=='settings'||row.revision!==expectedRevision||row.value.assetType!=='writing-edit')throw new ContractError('REVISION_CONFLICT','另一窗口的编辑草稿已变化，请另存这份输入');const requirement=direction??row.value.direction;if(row.value.draftText===text&&row.value.direction===requirement)return row;await commit(vault,'writing-edit-draft:'+id+':'+expectedRevision+':'+await sha256(canonical({text,direction:requirement})),[{kind:'settings',id,expectedRevision,value:{...row.value,draftText:text,direction:requirement,updatedAt:stamp()}}]);return (await vault.read<WritingEdit>(id))!}
export async function copyWritingEditDraft(vault:LocalVault,row:Document<WritingEdit>,text:string,direction:string){
 const at=stamp(),source=clone(row.value),original=clone(source.variants[0]!),manual:EditVariant={id:crypto.randomUUID(),origin:'manual',text,raw:text,status:'ready',finishReason:null,createdAt:at},id='writing-edit:'+crypto.randomUUID()
 const value:WritingEdit={...source,targetBranchId:crypto.randomUUID(),variants:[original,manual],selectedVariantId:manual.id,appliedVariantId:null,appliedRevision:0,draftText:text,direction,historical:true,createdAt:at,updatedAt:at}
 await commit(vault,'writing-edit-copy:'+id,[{kind:'settings',id,expectedRevision:0,value}]);return (await vault.read<WritingEdit>(id))!
}
export async function applyWritingVariant(vault:LocalVault,id:string,expectedRevision:number,variantId:string){
 const row=await vault.read<WritingEdit>(id);if(row?.kind!=='settings'||row.value.assetType!=='writing-edit')throw new ContractError('WRITING_EDIT_MISSING','找不到编辑候选')
 if(row.revision!==expectedRevision){const known=await vault.receipt('writing-edit-apply:'+id+':'+expectedRevision+':'+variantId);if(known){const receipts=await vault.receipts(known.operationId);const branch=receipts?.find(r=>r.documentId===row.value.targetBranchId);if(branch)return {branchId:branch.documentId,revision:branch.revision}}throw new ContractError('REVISION_CONFLICT','编辑候选已变化，请先查看当前版本')}
 const value=clone(row.value),variant=value.variants.find(v=>v.id===variantId);if(!variant||variant.status!=='ready')throw new ContractError('WRITING_EDIT_PARTIAL','未完成片段可以查看，需另存为手改候选后接受')
 const target=await vault.read<LocalBranch>(value.targetBranchId)
 if(target?.value.archived)throw new ContractError('BRANCH_ARCHIVED','目标故事线已归档，可以阅读；恢复后再修订')
 if((target?.revision||0)!==value.appliedRevision){
  const applied=value.appliedRevision?await vault.read<LocalBranch>(value.targetBranchId,value.appliedRevision):null
  const content=(v:LocalBranch)=>{const {name,archived,...rest}=v;return rest}
  if(!target||applied?.kind!=='branch'||canonical(content(target.value))!==canonical(content(applied.value)))throw new ContractError('REVISION_CONFLICT','目标故事线已有其他修改，候选保留')
 }
 if(value.appliedVariantId===variantId&&target)return {branchId:target.id,revision:target.revision}
 const conditions=await editSourceConditions(vault,value.source,value.source.policy==='forkPinned'||!!target)
 const documents:WriteDocument[]=[],paragraphId=variant.origin==='source'?null:crypto.randomUUID()
 if(paragraphId){const text=replacementText(value,variant);documents.push({kind:'paragraph',id:paragraphId,expectedRevision:0,value:{id:paragraphId,chapterId:value.chapterId,text,sourceStart:0,sourceEnd:text.length,textHash:await sha256(text),writingEditId:id,editVariantId:variant.id}})}
 const chapters=editChapters(value,variant,paragraphId||''),source=value.source,index=source.chapters.findIndex(c=>c.id===value.chapterId),branch:LocalBranch&{edition:unknown}={storyId:source.storyId,name:target?.value.name||'改写 · '+(source.chapters[index]?.title||'故事'),archived:target?.value.archived||false,parentBranchId:source.kind==='branch'?source.id:null,parentRevision:source.revision,storyRevision:source.storyRevision,fork:{storyId:source.storyId,branchId:source.kind==='branch'?source.id:null,...value.selection.start},prefixChapters:clone(source.chapters.slice(0,index)),chapters,paragraphIds:chapters.flatMap(c=>c.paragraphIds),createdAt:target?.value.createdAt||stamp(),edition:{editId:id,editRevision:expectedRevision+1,variantId:variant.id,paragraphId}}
 value.appliedVariantId=variant.id;value.selectedVariantId=variant.id;value.draftText=variant.text;value.appliedRevision=(target?.revision||0)+1;value.updatedAt=stamp()
 documents.push({kind:'branch',id:value.targetBranchId,expectedRevision:target?.revision||0,value:branch},{kind:'settings',id,expectedRevision,value})
 await commit(vault,'writing-edit-apply:'+id+':'+expectedRevision+':'+variantId,documents,conditions)
 return {branchId:value.targetBranchId,revision:value.appliedRevision}
}
