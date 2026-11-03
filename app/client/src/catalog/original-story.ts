import {canonical,ContractError,sha256,type Document,type LocalStory,type Paragraph,type DomainKind} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'

export interface OriginalStoryDraft {
  assetType:'original-story-draft'
  status:'editing'|'ready'|'completed'|'discarded'
  title:string
  chapterTitle:string
  text:string
  storyId:string
  sourceId:string
  chapterId:string
  paragraphIds:string[]
  operationId:string
  createdAt:string
}
const copy=<T>(value:T):T=>JSON.parse(canonical(value))
const keys='assetType,chapterId,chapterTitle,createdAt,operationId,paragraphIds,sourceId,status,storyId,text,title'
const id=(value:unknown):value is string=>typeof value==='string'&&!!value&&value.length<=160
function fail(message:string):never{throw new ContractError('ORIGINAL_DRAFT',message+'，已保存内容保留')}

/** Line separators and leading/trailing spaces remain in the original bytes.
 * Blank lines are omitted from the readable index, never from the source. */
export function originalParagraphs(text:string){
  const result:{text:string;start:number;end:number}[]=[]
  const expression=/[^\r\n]+/g
  for(const match of text.matchAll(expression))if(match[0].trim())result.push({text:match[0],start:match.index!,end:match.index!+match[0].length})
  return result
}
export function validateOriginalStoryDraft(value:unknown,documentId?:string):asserts value is OriginalStoryDraft {
  const v=value as OriginalStoryDraft
  if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join(',')!==keys||v.assetType!=='original-story-draft'||!['editing','ready','completed','discarded'].includes(v.status)||typeof v.title!=='string'||v.title.length>512||typeof v.chapterTitle!=='string'||v.chapterTitle.length>512||typeof v.text!=='string'||v.text.length>200000||v.text.includes('\0')||![v.storyId,v.sourceId,v.chapterId,v.operationId].every(id)||new Set([v.storyId,v.sourceId,v.chapterId,v.operationId]).size!==4||!Array.isArray(v.paragraphIds)||v.paragraphIds.length>2000||!v.paragraphIds.every(id)||new Set(v.paragraphIds).size!==v.paragraphIds.length||typeof v.createdAt!=='string'||v.createdAt.length>50||!Number.isFinite(Date.parse(v.createdAt))||documentId!==undefined&&(!documentId.startsWith('story-draft:')||documentId.length<=12))fail('新故事草稿无效')
  if(v.status==='editing'&&v.paragraphIds.length)fail('编辑草稿不能包含已冻结段落')
  if(['ready','completed'].includes(v.status)){
    if(!v.title.trim()||v.title!==v.title.trim()||!v.chapterTitle.trim()||v.chapterTitle!==v.chapterTitle.trim()||/(?:[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF])/.test(v.text)||!v.paragraphIds.length||v.paragraphIds.length!==originalParagraphs(v.text).length)fail('请填写书名、章节标题和可阅读的开头')
    if(v.paragraphIds.some(p=>[v.storyId,v.sourceId,v.chapterId,v.operationId].includes(p)))fail('草稿中的内容身份重复')
  }
}
export function newOriginalStoryDraft():{id:string;value:OriginalStoryDraft}{
  return {id:'story-draft:'+crypto.randomUUID(),value:{assetType:'original-story-draft',status:'editing',title:'',chapterTitle:'第一章',text:'',storyId:crypto.randomUUID(),sourceId:crypto.randomUUID(),chapterId:crypto.randomUUID(),paragraphIds:[],operationId:'original-create:'+crypto.randomUUID(),createdAt:new Date().toISOString()}}
}
export function readyOriginalStoryDraft(value:OriginalStoryDraft):OriginalStoryDraft {
  validateOriginalStoryDraft(value)
  if(value.status!=='editing')fail('这份草稿已经冻结')
  const next={...copy(value),title:value.title.trim(),chapterTitle:value.chapterTitle.trim(),status:'ready' as const,paragraphIds:originalParagraphs(value.text).map(()=>crypto.randomUUID())}
  validateOriginalStoryDraft(next);return next
}
export function validateOriginalDraftUpdate(before:OriginalStoryDraft,after:OriginalStoryDraft){
  validateOriginalStoryDraft(before);validateOriginalStoryDraft(after)
  if(['storyId','sourceId','chapterId','operationId','createdAt'].some(key=>before[key as keyof OriginalStoryDraft]!==after[key as keyof OriginalStoryDraft]))fail('新故事的创建身份不能更改')
  const allowed={editing:['editing','ready','discarded'],ready:['ready','completed','discarded'],completed:['completed'],discarded:['discarded']}
  if(!allowed[before.status].includes(after.status))fail('这份创建草稿不能重新执行')
  if(before.status!=='editing'&&canonical({...before,status:after.status})!==canonical(after))fail('冻结后的正文和创建计划不能覆盖')
}
export function originalDraftReferences(value:OriginalStoryDraft):{id:string;kind:DomainKind}[]{
  return value.status==='completed'?[{id:value.storyId,kind:'story'},{id:value.sourceId,kind:'source'},...value.paragraphIds.map(id=>({id,kind:'paragraph' as const}))]:[]
}
export function validateOriginalDraftRelations(value:OriginalStoryDraft,lookup:(id:string,revision?:number)=>{kind:DomainKind;revision?:number;value:unknown}|null|undefined){
  if(value.status!=='completed')return
  const source=lookup(value.sourceId),row=lookup(value.storyId,1),book=row?.value as LocalStory|undefined
  if(source?.kind!=='source'||row?.kind!=='story'||row.revision!==1||!book||book.origin!=='original'||book.immutableSource!==true||book.sourceId!==value.sourceId||book.title!==value.title||book.chapters.length!==1||book.chapters[0]!.id!==value.chapterId||book.chapters[0]!.title!==value.chapterTitle||canonical(book.paragraphIds)!==canonical(value.paragraphIds)||book.sourceHash!==(source.value as {sha256?:string}).sha256)fail('创建结果与原始草稿不一致')
  for(const [index,part] of originalParagraphs(value.text).entries()){
    const row=lookup(value.paragraphIds[index]!),paragraph=row?.value as Paragraph|undefined
    if(row?.kind!=='paragraph'||!paragraph||paragraph.chapterId!==value.chapterId||paragraph.text!==part.text||paragraph.sourceStart!==part.start||paragraph.sourceEnd!==part.end)fail('创建的正文与原始草稿不一致')
  }
}
async function exactDraft(vault:LocalVault,row:Document<OriginalStoryDraft>){
  validateOriginalStoryDraft(row.value,row.id)
  const source=await vault.read(row.id,row.revision)
  if(row.kind!=='settings'||source?.kind!=='settings'||canonical(source.value)!==canonical(row.value))fail('草稿副本与保存版本不一致')
}
async function acknowledgedCommit(vault:LocalVault,operationId:string,writes:WriteDocument[]){
  const hash=await sha256(canonical(writes))
  try{await vault.commitBundle(operationId,writes)}catch(error){if(!vault.unlocked||(await vault.receipt(operationId))?.payloadHash!==hash)throw error}
}
export async function saveOriginalStoryDraft(vault:LocalVault,id:string,revision:number,value:OriginalStoryDraft){
  const frozen=copy(value);validateOriginalStoryDraft(frozen,id)
  await acknowledgedCommit(vault,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:revision,value:frozen}])
  return (await vault.read<OriginalStoryDraft>(id))!
}
/** The durable ready row supplies every identity and byte for this operation.
 * A retry from an old ready revision reuses the same exact transaction. */
export async function createOriginalStory(vault:LocalVault,input:Document<OriginalStoryDraft>):Promise<string>{
  const row=copy(input);await exactDraft(vault,row)
  const v=row.value;if(v.status!=='ready')fail('请先保存并冻结开头')
  const bytes=new TextEncoder().encode(v.text),sourceHash=await sha256(bytes),paragraphs=await Promise.all(originalParagraphs(v.text).map(async(part,index):Promise<Paragraph>=>({id:v.paragraphIds[index]!,chapterId:v.chapterId,text:part.text,sourceStart:part.start,sourceEnd:part.end,textHash:await sha256(part.text)})))
  let binary='';for(let start=0;start<bytes.length;start+=8192)binary+=String.fromCharCode(...bytes.subarray(start,start+8192))
  const chunkId=v.sourceId+':0',book:LocalStory={title:v.title,origin:'original',immutableSource:true,sourceHash,sourceId:v.sourceId,format:'txt',encoding:'utf-8',chapters:[{id:v.chapterId,title:v.chapterTitle,paragraphIds:[...v.paragraphIds]}],paragraphIds:[...v.paragraphIds],tags:[],favorite:false}
  const writes:WriteDocument[]=[{kind:'story',id:v.storyId,expectedRevision:0,value:book},{kind:'source',id:v.sourceId,expectedRevision:0,value:{name:v.title+'.txt',sha256:sourceHash,bytes:bytes.length,detectedEncodingAtImport:'utf-8',offsetUnit:'utf16-decoded-with-story-encoding',chunkIds:[chunkId]}},{kind:'sourceChunk',id:chunkId,expectedRevision:0,value:{data:btoa(binary),sha256:sourceHash,bytes:bytes.length}},...paragraphs.map(value=>({kind:'paragraph' as const,id:value.id,expectedRevision:0,value})),{kind:'settings',id:row.id,expectedRevision:row.revision,value:{...v,status:'completed'}}]
  await acknowledgedCommit(vault,v.operationId,writes);return v.storyId
}
