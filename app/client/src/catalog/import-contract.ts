import {canonical,ContractError,type Document} from '../domain/local-contract'
import type {ImportEncoding} from '../repositories/local-import'
export type ImportStatus='needs_file'|'ready'|'importing'|'failed'|'cancelled'|'complete'|'rejected'
export interface ImportEntry {assetType:'import-entry';name:string;bytes:number;encoding:ImportEncoding;sha256:string|null;sourceId:string|null;storyId:string|null;status:ImportStatus;ownerId:string|null;error:string;createdAt:string;updatedAt:string;retired:boolean}
export const importPrefix='import-entry:'
const encodings=['auto','utf-8','utf-16le','utf-16be','gb18030','big5']
export function validateImportEntry(value:unknown,documentId?:string):asserts value is ImportEntry {
 const v=value as ImportEntry,bad=()=>{throw new ContractError('IMPORT_ENTRY','导入记录格式无效，原文件保留')},id=(s:unknown)=>typeof s==='string'&&s.length>0&&s.length<=160
 if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join(',')!=='assetType,bytes,createdAt,encoding,error,name,ownerId,retired,sha256,sourceId,status,storyId,updatedAt'||v.assetType!=='import-entry'||typeof v.name!=='string'||!v.name.trim()||v.name.length>1000||!Number.isSafeInteger(v.bytes)||v.bytes<0||v.bytes>Number.MAX_SAFE_INTEGER||!encodings.includes(v.encoding)||!['needs_file','ready','importing','failed','cancelled','complete','rejected'].includes(v.status)||typeof v.error!=='string'||v.error.length>2000||typeof v.retired!=='boolean'||[v.createdAt,v.updatedAt].some(s=>typeof s!=='string'||s.length>50||!Number.isFinite(Date.parse(s)))||documentId!==undefined&&(!documentId.startsWith(importPrefix)||documentId.length>160))bad()
 if(v.sha256!==null&&(typeof v.sha256!=='string'||!/^[a-f0-9]{64}$/.test(v.sha256))||v.sourceId!==null&&!id(v.sourceId)||v.storyId!==null&&!id(v.storyId)||v.ownerId!==null&&!id(v.ownerId))bad()
 if(['needs_file','rejected'].includes(v.status)?v.sourceId!==null||v.sha256!==null:v.sourceId===null||v.sha256===null||v.bytes>100*1024*1024)bad()
 if((v.status==='importing')!==(v.ownerId!==null)||(v.status==='complete')!==(v.storyId!==null)||v.status==='complete'&&v.error)bad()
}
export function validateImportUpdate(previous:Document,next:ImportEntry){
 const before=previous.value as ImportEntry
 for(const key of ['name','bytes','createdAt','assetType',...(before.sourceId?['sourceId','sha256']:[])] as (keyof ImportEntry)[])if(canonical(before[key])!==canonical(next[key]))throw new ContractError('IMPORT_IDENTITY','导入来源身份不能更改')
 if(before.status==='complete'&&(next.status!=='complete'||next.storyId!==before.storyId||next.encoding!==before.encoding))throw new ContractError('IMPORT_COMPLETE','已入库的原文保留；重新解码请新建导入')
 if(before.status==='importing'&&next.status==='importing'&&before.encoding!==next.encoding)throw new ContractError('IMPORT_ACTIVE','转换中的编码不能更改')
}
