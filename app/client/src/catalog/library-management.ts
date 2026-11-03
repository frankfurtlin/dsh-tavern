import {canonical,ContractError,type Document,type LocalStory,type ProgressAnchor} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
export interface LibraryView {assetType:'library-view';query:string;tag:string;group:string;filter:'all'|'favorites'|'pinned';sort:'recent'|'added'|'title'|'progress';layout:'grid'|'list';scroll:number}
export const libraryViewId='settings:library-view'
export const defaultLibraryView:LibraryView={assetType:'library-view',query:'',tag:'',group:'',filter:'all',sort:'recent',layout:'grid',scroll:0}
export function validateLibraryView(value:unknown):asserts value is LibraryView {
 const v=value as LibraryView
 if(!v||typeof v!=='object'||Object.keys(v).sort().join(',')!=='assetType,filter,group,layout,query,scroll,sort,tag'||v.assetType!=='library-view'||typeof v.query!=='string'||v.query.length>512||typeof v.tag!=='string'||v.tag.length>120||typeof v.group!=='string'||v.group.length>120||!['all','favorites','pinned'].includes(v.filter)||!['recent','added','title','progress'].includes(v.sort)||!['grid','list'].includes(v.layout)||!Number.isFinite(v.scroll)||v.scroll<0||v.scroll>10_000_000)throw new ContractError('LIBRARY_VIEW','书架视图设置无效')
}
export const groupsOf=(book:LocalStory)=>Array.isArray(book.groups)?book.groups:[]
export function libraryQuery(books:Document<LocalStory>[],view:LibraryView,reading:Map<string,{at:string;position:number}>,added:Map<string,string>=new Map()):Document<LocalStory>[] {
 const query=view.query.trim().toLocaleLowerCase(),rows=books.filter(row=>(!query||[row.value.title,...row.value.tags].join('\n').toLocaleLowerCase().includes(query))&&(!view.tag||row.value.tags.includes(view.tag))&&(!view.group||groupsOf(row.value).includes(view.group))&&(view.filter!=='favorites'||row.value.favorite)&&(view.filter!=='pinned'||row.value.pinned))
 return rows.sort((a,b)=>Number(!!b.value.pinned)-Number(!!a.value.pinned)||(view.sort==='title'?a.value.title.localeCompare(b.value.title,'zh-CN'):view.sort==='progress'?(reading.get(b.id)?.position||0)-(reading.get(a.id)?.position||0):view.sort==='recent'?(reading.get(b.id)?.at||added.get(b.id)||b.updatedAt).localeCompare(reading.get(a.id)?.at||added.get(a.id)||a.updatedAt):(added.get(b.id)||b.updatedAt).localeCompare(added.get(a.id)||a.updatedAt))||a.id.localeCompare(b.id))
}
export interface BookMetadata {title?:string;favorite?:boolean;pinned?:boolean;tags?:string[];groups?:string[]}
export function validateBookMetadata(value:unknown):asserts value is BookMetadata {
 const v=value as BookMetadata
 if(!v||typeof v!=='object'||Array.isArray(v)||!Object.keys(v).length||Object.keys(v).some(k=>!['title','favorite','pinned','tags','groups'].includes(k))||v.title!==undefined&&(typeof v.title!=='string'||!v.title.trim()||v.title.length>512)||v.favorite!==undefined&&typeof v.favorite!=='boolean'||v.pinned!==undefined&&typeof v.pinned!=='boolean')throw new ContractError('BOOK_METADATA','书籍信息无效，正文保留')
 for(const values of [v.tags,v.groups])if(values!==undefined&&(!Array.isArray(values)||values.length>100||values.some(s=>typeof s!=='string'||!s.trim()||s.length>120)||new Set(values).size!==values.length))throw new ContractError('BOOK_METADATA','标签或分组不能重复，且每项最多 120 个字符')
}
/** CAS verifies revision ownership; this also verifies the caller's actual bytes.
 * Freeze before awaiting reads, so no mutable caller-owned value can cross the
 * metadata-only boundary while the encrypted historical rows are checked. */
export async function frozenMetadataSnapshots(vault:LocalVault,rows:Document[]):Promise<Document[]>{
 const frozen=JSON.parse(canonical(rows)) as Document[]
 for(let start=0;start<frozen.length;start+=64)await Promise.all(frozen.slice(start,start+64).map(async row=>{
  if(typeof row.id!=='string'||!row.id||!Number.isSafeInteger(row.revision)||row.revision<1)throw new ContractError('LIBRARY_SNAPSHOT','所选作品版本无效，原内容保留')
  const source=await vault.read(row.id,row.revision)
  if(!source||source.kind!==row.kind||canonical(source.value)!==canonical(row.value))throw new ContractError('LIBRARY_SNAPSHOT','所选作品副本与保存版本不一致，原内容保留')
 }))
 return frozen
}
/** One receipt/transaction for every selected book. No fallback partial save. */
export async function updateBookMetadata(vault:LocalVault,operationId:string,books:Document<LocalStory>[],patch:BookMetadata){
 validateBookMetadata(patch)
 if(!books.length||books.length>1000||new Set(books.map(r=>r.id)).size!==books.length||books.some(r=>r.kind!=='story'))throw new ContractError('BOOK_SELECTION','请重新选择要修改的书籍')
 const frozen=JSON.parse(canonical(patch)) as BookMetadata
 books=await frozenMetadataSnapshots(vault,books) as Document<LocalStory>[]
 const documents=books.filter(row=>Object.entries(frozen).some(([key,value])=>canonical((row.value as unknown as Record<string,unknown>)[key]??null)!==canonical(value))).map(row=>({kind:'story' as const,id:row.id,expectedRevision:row.revision,value:{...row.value,...frozen}})),conditions=books.map(row=>({kind:'story' as const,id:row.id,revision:row.revision}))
 if(!documents.length){await vault.checkPreconditions(conditions,operationId);return []}
 return vault.commitBundle(operationId,documents,undefined,conditions)
}
export async function addBooksToGroup(vault:LocalVault,operationId:string,books:Document<LocalStory>[],group:string){
 validateBookMetadata({groups:[group]})
 if(!books.length||books.length>1000||new Set(books.map(r=>r.id)).size!==books.length||books.some(r=>r.kind!=='story'))throw new ContractError('BOOK_SELECTION','请重新选择要修改的书籍')
 books=await frozenMetadataSnapshots(vault,books) as Document<LocalStory>[]
 const documents=books.filter(row=>!groupsOf(row.value).includes(group)).map(row=>{const groups=[...new Set([...groupsOf(row.value),group])];validateBookMetadata({groups});return {kind:'story' as const,id:row.id,expectedRevision:row.revision,value:{...row.value,groups}}}),conditions=books.map(row=>({kind:'story' as const,id:row.id,revision:row.revision}))
 if(!documents.length){await vault.checkPreconditions(conditions,operationId);return []}
 return vault.commitBundle(operationId,documents,undefined,conditions)
}
export function currentReading(book:Document<LocalStory>,paragraphIds:string[],progress:Document<ProgressAnchor>|undefined,lengths:ReadonlyMap<string,number>){
 const index=progress?paragraphIds.indexOf(progress.value.paragraphId):-1
 if(index<0)return {at:'',position:0}
 const sizes=paragraphIds.map(id=>lengths.get(id));if(sizes.some(n=>n===undefined||!Number.isSafeInteger(n)||n<0))throw new ContractError('READING_INDEX','阅读索引不完整，请重新打开书架')
 const known=sizes as number[],total=known.reduce((sum,n)=>sum+n,0),before=known.slice(0,index).reduce((sum,n)=>sum+n,0),inside=Math.min(known[index]!,Math.max(0,progress!.value.offset))
 return {at:progress!.updatedAt,position:total?(before+inside)/total:0}
}
