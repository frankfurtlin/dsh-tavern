import {canonical,ContractError,unicodeOffset,type Chapter,type Paragraph} from '../domain/local-contract'
import {validateChapters} from './contract'

export interface TextSelection {start:{paragraphId:string;offset:number};end:{paragraphId:string;offset:number}}
export interface EditSource {id:string;kind:'story'|'branch';revision:number;storyId:string;storyRevision:number;chapters:Chapter[];policy?:'requireUnchanged'|'forkPinned'}
export interface EditParagraph {id:string;revision:number;value:Paragraph}
export interface EditVariant {id:string;origin:'source'|'manual'|'delete'|'rewrite'|'expand';text:string;raw:string;status:'ready'|'partial';finishReason:string|null;createdAt:string;runId?:string}
export interface WritingEdit {
 assetType:'writing-edit';source:EditSource;selection:TextSelection;paragraphs:EditParagraph[];chapterId:string;
 targetBranchId:string;variants:EditVariant[];selectedVariantId:string;appliedVariantId:string|null;appliedRevision:number;
 draftText:string;direction:string;historical:boolean;createdAt:string;updatedAt:string;
}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const id=(v:unknown)=>typeof v==='string'&&!!v&&v.length<=160
const number=(v:unknown)=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0
const date=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v))
const text=(v:unknown)=>typeof v==='string'&&v.length<=1_000_000
function deny(message='编辑候选格式无效'):never{throw new ContractError('WRITING_EDIT_INVALID',message+'；原文和旧候选保留')}
export function selectionText(selection:TextSelection,paragraphs:EditParagraph[]){
 const first=paragraphs[0],last=paragraphs.at(-1)
 if(!first||!last||first.id!==selection.start.paragraphId||last.id!==selection.end.paragraphId)deny('选区与来源不一致')
 for(const [point,row] of [[selection.start,first],[selection.end,last]] as const)if(!number(point.offset)||point.offset>row.value.text.length||unicodeOffset(row.value.text,point.offset)!==point.offset)deny('选区必须落在完整字符之间')
 if(first===last&&selection.start.offset>selection.end.offset)deny('选区起止顺序无效')
 return paragraphs.map((row,index)=>row.value.text.slice(index===0?selection.start.offset:0,index===paragraphs.length-1?selection.end.offset:undefined)).join('\n\n')
}
export function replacementText(edit:WritingEdit,variant:EditVariant){
 return edit.paragraphs[0]!.value.text.slice(0,edit.selection.start.offset)+variant.text+edit.paragraphs.at(-1)!.value.text.slice(edit.selection.end.offset)
}
export function editChapters(edit:WritingEdit,variant:EditVariant,paragraphId:string):Chapter[]{
 if(variant.origin==='source')return JSON.parse(canonical(edit.source.chapters)) as Chapter[]
 return edit.source.chapters.map(chapter=>{if(chapter.id!==edit.chapterId)return {...chapter,paragraphIds:[...chapter.paragraphIds]};const start=chapter.paragraphIds.indexOf(edit.selection.start.paragraphId),end=chapter.paragraphIds.indexOf(edit.selection.end.paragraphId);return {...chapter,paragraphIds:[...chapter.paragraphIds.slice(0,start),paragraphId,...chapter.paragraphIds.slice(end+1)]}})
}
export function validateWritingEdit(value:unknown,documentId?:string):asserts value is WritingEdit{
 if(!record(value)||value.assetType!=='writing-edit'||documentId!==undefined&&!documentId.startsWith('writing-edit:')||!record(value.source)||!id(value.source.id)||!['story','branch'].includes(String(value.source.kind))||!number(value.source.revision)||!value.source.revision||!id(value.source.storyId)||!number(value.source.storyRevision)||!value.source.storyRevision||!record(value.selection)||!id(value.chapterId)||!id(value.targetBranchId)||!Array.isArray(value.paragraphs)||!value.paragraphs.length||value.paragraphs.length>1000||!Array.isArray(value.variants)||!value.variants.length||value.variants.length>100||!id(value.selectedVariantId)||!(value.appliedVariantId===null||id(value.appliedVariantId))||!number(value.appliedRevision)||typeof value.historical!=='boolean'||!date(value.createdAt)||!date(value.updatedAt))deny()
 validateChapters(value.source.chapters)
 if(value.source.policy!==undefined&&!['requireUnchanged','forkPinned'].includes(String(value.source.policy)))deny('编辑来源策略无效')
 if(!text(value.draftText))deny('编辑草稿超过上限')
 if(typeof value.direction!=='string'||value.direction.length>20000)deny('编辑要求超过上限')
 const edit=value as unknown as WritingEdit,chapter=edit.source.chapters.find(c=>c.id===edit.chapterId)
 if(edit.source.kind==='story'&&edit.source.id!==edit.source.storyId||!chapter)deny()
 for(const point of [edit.selection.start,edit.selection.end])if(!record(point)||!id(point.paragraphId)||!number(point.offset))deny()
 for(const row of edit.paragraphs)if(!record(row)||!id(row.id)||!number(row.revision)||!row.revision||!record(row.value)||row.value.id!==row.id||row.value.chapterId!==edit.chapterId||!text(row.value.text))deny()
 const start=chapter.paragraphIds.indexOf(edit.selection.start.paragraphId),end=chapter.paragraphIds.indexOf(edit.selection.end.paragraphId)
 if(start<0||end<start||canonical(chapter.paragraphIds.slice(start,end+1))!==canonical(edit.paragraphs.map(p=>p.id)))deny('选区不能缺段、重排或跨章节')
 const quote=selectionText(edit.selection,edit.paragraphs)
 if(!quote.length)deny('请选择要编辑的文字')
 if(new Set(edit.variants.map(v=>v.id)).size!==edit.variants.length)deny()
 for(const variant of edit.variants){if(!record(variant)||!id(variant.id)||!['source','manual','delete','rewrite','expand'].includes(variant.origin)||!text(variant.text)||!text(variant.raw)||!['ready','partial'].includes(variant.status)||!(variant.finishReason===null||typeof variant.finishReason==='string'&&variant.finishReason.length<=100)||!date(variant.createdAt))deny();if(variant.origin==='source'&&(variant!==edit.variants[0]||variant.text!==quote||variant.raw!==quote||variant.status!=='ready')||variant.origin==='delete'&&variant.text!==''||['source','manual','delete'].includes(variant.origin)&&(variant.raw!==variant.text||variant.status!=='ready'||variant.finishReason!==null)||['rewrite','expand'].includes(variant.origin)&&variant.status==='ready'&&(!variant.text.trim()||!['stop','end_turn'].includes(variant.finishReason||'')))deny()}
 for(const variant of edit.variants)if(['rewrite','expand'].includes(variant.origin)?!id(variant.runId)||!String(variant.runId).startsWith('writing-run:'):variant.runId!==undefined)deny('生成候选缺少原请求来源')
 if(edit.variants[0]!.origin!=='source'||!edit.variants.some(v=>v.id===edit.selectedVariantId)||(edit.appliedRevision===0?edit.appliedVariantId!==null:!edit.variants.some(v=>v.id===edit.appliedVariantId&&v.status==='ready')))deny()
 if(canonical(edit).length>8*1024*1024)deny('编辑候选超过本地上限')
}
export function validateEditUpdate(before:WritingEdit,after:WritingEdit){
 for(const key of ['assetType','source','selection','paragraphs','chapterId','targetBranchId','historical','createdAt'] as const)if(canonical(before[key])!==canonical(after[key]))deny('编辑来源不能改变')
 // Target metadata (rename/archive/restore) can create intervening revisions.
 // The exact new edition is checked against the referenced branch revision.
 if(after.variants.length<before.variants.length||canonical(after.variants.slice(0,before.variants.length))!==canonical(before.variants)||after.appliedRevision<before.appliedRevision||before.appliedRevision===0&&after.appliedRevision>1||after.appliedRevision===before.appliedRevision&&after.appliedVariantId!==before.appliedVariantId)deny('旧候选和确认历史不能改变')
}
