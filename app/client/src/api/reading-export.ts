import {api,ApiError,authGeneration,authorizationHeaders,refreshAuth} from './index'
import {accountId,backendBase,cacheGet,cacheSet,native} from '../platform/bridge'
import {isBookDeleted} from '../platform/book-cache'
import {isWorldLineDeleted} from '../platform/world-cache'
import type {BackupWriter} from './backups'
import {ByteSha256} from './stream-hash'

export type ReadingExportScope={kind:'book';bookId:string;branchId:string;revisionId:string}|{kind:'world';worldId:string;lineId:string;checkpointId:string}
export interface ReadingExportRequest {format:'txt'|'epub';scope:ReadingExportScope;range:'observed'|'selected';startChapter?:number;endChapter?:number;allowUnseen:boolean}
export interface ReadingExportJob {id:string;status:'preparing'|'ready'|'failed'|'expired';version:number;expiresAt:string;protocol:'reader-export-v1';format?:'txt'|'epub';fileName?:string;sha256?:string;size?:number;chapterCount?:number;imageCount?:number;errorCode?:string}
export interface ReadingExportIntent {schema:1;requestId:string;request:ReadingExportRequest;jobId?:string}
export const MAX_READING_DOWNLOAD=3*1024**3,MAX_BUFFERED_READING_DOWNLOAD=64*1024**2
const id=/^[a-f0-9]{32}$/,hash=/^[a-f0-9]{64}$/
const invalid=()=>new Error('导出信息不完整，请查询本次请求')
export function validateReadingScope(value:unknown):ReadingExportScope{
  if(!value||typeof value!=='object')throw invalid()
  const v=value as Record<string,unknown>,fields=v.kind==='book'?['bookId','branchId','revisionId']:v.kind==='world'?['worldId','lineId','checkpointId']:null
  if(!fields||fields.some(key=>typeof v[key]!=='string'||!id.test(v[key] as string)))throw invalid()
  return Object.fromEntries([['kind',v.kind],...fields.map(key=>[key,v[key]])]) as ReadingExportScope
}
export function validateReadingRequest(value:unknown):ReadingExportRequest{
  if(!value||typeof value!=='object')throw invalid()
  const v=value as ReadingExportRequest,scope=validateReadingScope(v.scope)
  if(!['txt','epub'].includes(v.format)||!['observed','selected'].includes(v.range)||typeof v.allowUnseen!=='boolean')throw invalid()
  if(v.range==='observed'){
    if(v.startChapter!==undefined||v.endChapter!==undefined||v.allowUnseen)throw invalid()
    return {format:v.format,scope,range:'observed',allowUnseen:false}
  }
  if(!Number.isSafeInteger(v.startChapter)||!Number.isSafeInteger(v.endChapter)||v.startChapter!<1||v.endChapter!<v.startChapter!||v.endChapter!>10000)throw new Error('请选择有效的起止章节')
  return {format:v.format,scope,range:'selected',startChapter:v.startChapter,endChapter:v.endChapter,allowUnseen:v.allowUnseen}
}
export function validateReadingJob(value:unknown,expected?:{id?:string;format?:'txt'|'epub'}):ReadingExportJob{
  if(!value||typeof value!=='object')throw invalid()
  const v=value as ReadingExportJob
  if(!id.test(v.id)||!['preparing','ready','failed','expired'].includes(v.status)||!Number.isSafeInteger(v.version)||v.version<1||v.protocol!=='reader-export-v1'||typeof v.expiresAt!=='string'||!Number.isFinite(Date.parse(v.expiresAt))||expected?.id&&v.id!==expected.id)throw invalid()
  const result:ReadingExportJob={id:v.id,status:v.status,version:v.version,expiresAt:v.expiresAt,protocol:v.protocol}
  if(v.format!==undefined){if(!['txt','epub'].includes(v.format)||expected?.format&&v.format!==expected.format)throw invalid();result.format=v.format}
  if(v.fileName!==undefined){if(typeof v.fileName!=='string'||v.fileName.length>240||/[\x00-\x1f/\\]/.test(v.fileName)||!v.fileName.trim())throw invalid();result.fileName=v.fileName}
  if(v.sha256!==undefined){if(!hash.test(v.sha256))throw invalid();result.sha256=v.sha256}
  for(const field of ['size','chapterCount','imageCount'] as const)if(v[field]!==undefined){if(!Number.isSafeInteger(v[field])||v[field]!<0||field==='size'&&v[field]!>MAX_READING_DOWNLOAD)throw invalid();result[field]=v[field]}
  if(v.errorCode!==undefined){if(typeof v.errorCode!=='string'||!/^[A-Z0-9_]{1,100}$/.test(v.errorCode))throw invalid();result.errorCode=v.errorCode}
  if(result.status==='ready'&&(!result.format||!result.fileName||!result.sha256||!result.size||result.chapterCount===undefined||!result.fileName.toLowerCase().endsWith('.'+result.format)))throw invalid()
  return result
}
export function captureReadingExportScope(scope:ReadingExportScope,active:()=>boolean=()=>true){
  const owner=accountId(),base=backendBase(),epoch=authGeneration(),value=validateReadingScope(scope)
  return()=>!!owner&&owner===accountId()&&base===backendBase()&&epoch===authGeneration()&&active()&&!(value.kind==='book'?isBookDeleted(value.bookId):isWorldLineDeleted(value.worldId,value.lineId))
}
function check(active:()=>boolean){if(!active())throw new ApiError('SESSION_CHANGED','账号或故事已变化，请重新打开导出',401)}
export const readingIntentKey=(scope:ReadingExportScope)=>'reading-export-intent:v1:'+JSON.stringify(validateReadingScope(scope))
function validateIntent(value:unknown,scope:ReadingExportScope):ReadingExportIntent|null{
  try{if(!value||typeof value!=='object')return null;const v=value as ReadingExportIntent,request=validateReadingRequest(v.request);if(v.schema!==1||typeof v.requestId!=='string'||!v.requestId||v.requestId.length>160||JSON.stringify(request.scope)!==JSON.stringify(validateReadingScope(scope))||v.jobId!==undefined&&!id.test(v.jobId))return null;return {schema:1,requestId:v.requestId,request,...(v.jobId?{jobId:v.jobId}:{})}}catch{return null}
}
export async function loadReadingIntent(scope:ReadingExportScope,active=captureReadingExportScope(scope)){check(active);const value=await cacheGet(readingIntentKey(scope));check(active);return validateIntent(value,scope)}
export async function saveReadingIntent(scope:ReadingExportScope,value:ReadingExportIntent|null,active=captureReadingExportScope(scope)){check(active);const projected=value===null?null:validateIntent(value,scope);if(value&&!projected)throw invalid();await cacheSet(readingIntentKey(scope),projected);check(active)}
export interface ReadingExportCapabilities {protocol:'reader-export-v1';ready:boolean;formats:('txt'|'epub')[];defaultRange:'observed';selectedUnseenRequiresConfirmation:true;maxBytes:number}
export async function readingExportAvailable(){
  const owner=accountId(),base=backendBase(),epoch=authGeneration(),valid=()=>!!owner&&owner===accountId()&&base===backendBase()&&epoch===authGeneration()
  try{
    const value=await api<ReadingExportCapabilities>('/exports/capabilities')
    if(!valid()||value?.ready!==true||value.protocol!=='reader-export-v1'||!Array.isArray(value.formats)||value.formats.length!==2||!value.formats.includes('txt')||!value.formats.includes('epub')||value.defaultRange!=='observed'||value.selectedUnseenRequiresConfirmation!==true||!Number.isSafeInteger(value.maxBytes)||value.maxBytes<=0||value.maxBytes>MAX_READING_DOWNLOAD)return false
    if(!native)return true
    const {nativeReadingExportCapabilities}=await import('../platform/reader-backup'),capability=await nativeReadingExportCapabilities()
    return valid()&&capability?.ready===true
  }catch{return false}
}
export async function createReadingExport(request:ReadingExportRequest,key:string,active=captureReadingExportScope(request.scope)){
  const body=validateReadingRequest(request);if(!key||key.length>160)throw invalid();check(active)
  const value=await api('/exports',{method:'POST',body,key,retry:false});check(active);return validateReadingJob(value,{format:body.format})
}
export async function queryReadingExport(intent:ReadingExportIntent,active=captureReadingExportScope(intent.request.scope)):Promise<ReadingExportJob|null>{
  const value=validateIntent(intent,intent.request.scope);if(!value)throw invalid();check(active)
  if(value.jobId){const result=await api('/exports/'+value.jobId);check(active);return validateReadingJob(result,{id:value.jobId,format:value.request.format})}
  const result=await api<{found:boolean;job?:unknown}>('/exports/requests?requestId='+encodeURIComponent(value.requestId));check(active)
  if(result?.found===false)return null
  if(result?.found!==true)throw invalid()
  return validateReadingJob(result.job,{format:value.request.format})
}
export async function writeReadingResponse(response:Response,job:ReadingExportJob,writer:BackupWriter,active:()=>boolean,onProgress:(bytes:number)=>void=()=>{}){
  const ready=validateReadingJob(job),reader=response.body?.getReader(),digest=new ByteSha256();let received=0
  try{
    const type=ready.format==='txt'?'text/plain':'application/epub+zip',declared=response.headers.get('Content-Length')
    if(ready.status!=='ready'||!response.ok||response.redirected||!reader||(response.headers.get('Content-Type')||'').split(';')[0]!==type||declared!==null&&Number(declared)!==ready.size)throw new Error('导出文件响应无效')
    check(active)
    for(;;){const part=await reader.read();check(active);if(part.done)break;received+=part.value.byteLength;if(received>ready.size!)throw new Error('导出文件大小不符');digest.update(part.value);await writer.write(part.value);check(active);onProgress(received)}
    if(received!==ready.size||digest.hex()!==ready.sha256)throw new Error('导出文件未通过完整性检查')
    await writer.close();check(active)
  }catch(error){await reader?.cancel().catch(()=>{});await writer.abort().catch(()=>{});throw error}
  finally{reader?.releaseLock()}
}
export async function downloadReadingExport(job:ReadingExportJob,active:()=>boolean,onProgress:(bytes:number)=>void=()=>{}){
  const ready=validateReadingJob(job)
  if(ready.status!=='ready'||Date.parse(ready.expiresAt)<=Date.now())throw new Error('文件已过期，请明确重新导出');check(active)
  if(native){
    const owner=accountId(),base=backendBase(),epoch=authGeneration(),valid=()=>!!owner&&owner===accountId()&&base===backendBase()&&epoch===authGeneration()&&active()
    check(valid)
    const {downloadNativeReadingExport}=await import('../platform/reader-backup');check(valid)
    await downloadNativeReadingExport({operationId:ready.id,format:ready.format!,size:ready.size!,sha256:ready.sha256!,fileName:ready.fileName!},bytes=>{if(valid()&&Number.isSafeInteger(bytes)&&bytes>=0&&bytes<=ready.size!)onProgress(bytes)},valid)
    check(valid);return
  }
  type Picker=(options:unknown)=>Promise<{createWritable:()=>Promise<BackupWriter>}>
  const picker=(window as unknown as {showSaveFilePicker?:Picker}).showSaveFilePicker,type=ready.format==='txt'?'text/plain':'application/epub+zip'
  if(!picker&&ready.size!>MAX_BUFFERED_READING_DOWNLOAD)throw new Error('请使用支持文件流式保存的浏览器下载此文件')
  const handle=picker?await picker({suggestedName:ready.fileName,types:[{description:ready.format!.toUpperCase(),accept:{[type]:['.'+ready.format]}}]}):undefined;check(active)
  const url=backendBase()+'/api/reader/v1/exports/'+ready.id+'/download'
  let response=await fetch(url,{headers:authorizationHeaders(),credentials:'include',redirect:'error'});check(active)
  if(response.status===401&&await refreshAuth()){check(active);response=await fetch(url,{headers:authorizationHeaders(),credentials:'include',redirect:'error'});check(active)}
  if(handle){const writer=await handle.createWritable();return writeReadingResponse(response,ready,writer,active,onProgress)}
  const chunks:Uint8Array[]=[];let bytes=0
  const writer:BackupWriter={async write(data){bytes+=data.byteLength;if(bytes>MAX_BUFFERED_READING_DOWNLOAD)throw new Error('此浏览器无法保存这个大小的文件');chunks.push(data.slice())},async close(){check(active);const object=URL.createObjectURL(new Blob(chunks as BlobPart[],{type})),link=document.createElement('a');try{link.href=object;link.download=ready.fileName!;document.body.append(link);link.click();link.remove()}finally{setTimeout(()=>URL.revokeObjectURL(object),1000);chunks.length=0}},async abort(){chunks.length=0}}
  return writeReadingResponse(response,ready,writer,active,onProgress)
}
