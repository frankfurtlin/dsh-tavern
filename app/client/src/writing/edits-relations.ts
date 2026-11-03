import {canonical,ContractError,type DomainKind,type Chapter} from '../domain/local-contract'
import type {WritingRun} from './contract'
import {editChapters,replacementText,type WritingEdit} from './edits-contract'

export interface BranchEdition {editId:string;editRevision:number;variantId:string;paragraphId:string|null}
type Lookup=(id:string,revision?:number)=>{kind:DomainKind;revision?:number;value:unknown}|undefined|null
export function editReferences(kind:DomainKind,value:Record<string,unknown>){
 const rows:{id:string;kind:DomainKind;revision?:number}[]=[]
 if(kind==='settings'&&value.assetType==='writing-edit'){
  const edit=value as unknown as WritingEdit;rows.push({id:edit.source.id,kind:edit.source.kind,revision:edit.source.revision},{id:edit.source.storyId,kind:'story',revision:edit.source.storyRevision},...edit.paragraphs.map(p=>({id:p.id,kind:'paragraph' as const,revision:p.revision})))
  for(const chapter of edit.source.chapters)for(const id of chapter.paragraphIds)rows.push({id,kind:'paragraph'})
  for(const variant of edit.variants)if(variant.runId)rows.push({id:variant.runId,kind:'settings'})
  if(edit.appliedRevision)rows.push({id:edit.targetBranchId,kind:'branch',revision:edit.appliedRevision})
 }
 if(kind==='branch'&&value.edition){const edition=value.edition as BranchEdition;rows.push({id:edition.editId,kind:'settings',revision:edition.editRevision})}
 if(kind==='paragraph'&&value.writingEditId)rows.push({id:String(value.writingEditId),kind:'settings'})
 if(kind==='settings'&&value.assetType==='writing-run'){const purpose=(value as unknown as WritingRun).purpose;if(purpose&&purpose.kind!=='directions')rows.push({id:purpose.editId,kind:'settings',revision:purpose.editRevision})}
 return rows
}
export function validateEditRelations(kind:DomainKind,id:string,value:Record<string,unknown>,lookup:Lookup){
 const deny=():never=>{throw new ContractError('WRITING_EDIT_REFERENCE','编辑候选、原文版本与故事线关联不一致；原数据保留')}
 if(kind==='settings'&&value.assetType==='writing-edit'){
  const edit=value as unknown as WritingEdit,source=lookup(edit.source.id,edit.source.revision),story=lookup(edit.source.storyId,edit.source.storyRevision),sourceValue=source?.value as {storyId?:string;chapters:Chapter[]}|undefined
  if(source?.kind!==edit.source.kind||source.revision!==edit.source.revision||story?.kind!=='story'||story.revision!==edit.source.storyRevision||edit.source.kind==='branch'&&sourceValue?.storyId!==edit.source.storyId||canonical(sourceValue?.chapters)!==canonical(edit.source.chapters)||edit.source.id===edit.targetBranchId)deny()
  for(const paragraph of edit.paragraphs){const original=lookup(paragraph.id,paragraph.revision);if(original?.kind!=='paragraph'||original.revision!==paragraph.revision||canonical(original.value)!==canonical(paragraph.value))deny()}
  for(const variant of edit.variants)if(variant.runId){const original=lookup(variant.runId),run=original?.value as WritingRun|undefined,c=run?.chapters?.[0];if(original?.kind!=='settings'||run?.assetType!=='writing-run'||run.purpose?.kind==='directions'||run.purpose?.kind!==variant.origin||run.purpose.editId!==id||!c||run.status==='queued'||run.status==='running'||c.text!==variant.text||(c.raw??c.text)!==variant.raw||c.finishReason!==variant.finishReason||variant.status!==(c.status==='complete'?'ready':'partial'))deny()}
  if(edit.appliedRevision){const branch=lookup(edit.targetBranchId,edit.appliedRevision),b=branch?.value as {edition?:BranchEdition;chapters:Chapter[]}|undefined;if(branch?.kind!=='branch'||branch.revision!==edit.appliedRevision||b?.edition?.editId!==id||b.edition.variantId!==edit.appliedVariantId)deny();const variant=edit.variants.find(v=>v.id===edit.appliedVariantId);if(!variant||canonical(b?.chapters)!==canonical(editChapters(edit,variant,b?.edition?.paragraphId||'')))deny()}
 }
 if(kind==='branch'&&value.edition){
  const edition=value.edition as BranchEdition,owner=lookup(edition.editId,edition.editRevision),edit=owner?.value as WritingEdit|undefined,variant=edit?.variants.find(v=>v.id===edition.variantId),index=edit?.source.chapters.findIndex(c=>c.id===edit.chapterId)
  if(owner?.kind!=='settings'||owner.revision!==edition.editRevision||edit?.assetType!=='writing-edit'||edit.targetBranchId!==id||edit.appliedVariantId!==edition.variantId||!variant||variant.status!=='ready'||value.storyId!==edit.source.storyId||value.storyRevision!==edit.source.storyRevision||value.parentBranchId!==(edit.source.kind==='branch'?edit.source.id:null)||value.parentRevision!==edit.source.revision||canonical(value.fork)!==canonical({storyId:edit.source.storyId,branchId:edit.source.kind==='branch'?edit.source.id:null,...edit.selection.start})||canonical(value.prefixChapters)!==canonical(edit.source.chapters.slice(0,index))||canonical(value.chapters)!==canonical(editChapters(edit,variant,edition.paragraphId||''))||(variant.origin==='source'?edition.paragraphId!==null:!edition.paragraphId))deny()
  if(edition.paragraphId){const p=lookup(edition.paragraphId);const v=p?.value as {writingEditId?:string;editVariantId?:string;text?:string;chapterId?:string};if(p?.kind!=='paragraph'||v.writingEditId!==edition.editId||v.editVariantId!==variant!.id||v.chapterId!==edit!.chapterId||v.text!==replacementText(edit!,variant!))deny()}
 }
 if(kind==='paragraph'&&value.writingEditId){const owner=lookup(String(value.writingEditId)),edit=owner?.value as WritingEdit|undefined,variant=edit?.variants.find(v=>v.id===value.editVariantId),fork=value.forkOf as {paragraphId:string}|undefined,original=fork?lookup(fork.paragraphId)?.value as Record<string,unknown>|undefined:null;if(owner?.kind!=='settings'||edit?.assetType!=='writing-edit'||!variant||variant.origin==='source'||variant.status!=='ready'||value.chapterId!==edit.chapterId||(fork?original?.writingEditId!==value.writingEditId||original?.editVariantId!==value.editVariantId:value.text!==replacementText(edit,variant)))deny()}
 if(kind==='settings'&&value.assetType==='writing-run'){const run=value as unknown as WritingRun,purpose=run.purpose;if(purpose&&purpose.kind!=='directions'){const original=lookup(purpose.editId,purpose.editRevision),edit=original?.value as WritingEdit|undefined;if(original?.kind!=='settings'||original.revision!==purpose.editRevision||edit?.assetType!=='writing-edit'||run.storyId!==edit.source.storyId||run.storyRevision!==edit.source.storyRevision||run.parentBranchId!==(edit.source.kind==='branch'?edit.source.id:null)||run.parentRevision!==edit.source.revision||canonical(run.anchor)!==canonical({storyId:edit.source.storyId,branchId:edit.source.kind==='branch'?edit.source.id:null,...edit.selection.start}))deny()}}
}
