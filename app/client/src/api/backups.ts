import {api,ApiError,authGeneration,authorizationHeaders,refreshAuth} from './index'
import {accountId,backendBase,cacheGet,cacheSet,native} from '../platform/bridge'
import {isBookDeleted} from '../platform/book-cache'
import {isWorldDeleted} from '../platform/world-cache'
import {nativeBackupCapabilities,prepareNativeBackup,downloadNativeBackup,type NativeBackupSelection} from '../platform/reader-backup'

export const BACKUP_PROTOCOL='reader-portable-v1' as const
export const MAX_BACKUP_UPLOAD=2*1024**3+64*1024**2+128
export const MAX_BUFFERED_DOWNLOAD=64*1024**2
export type BackupFile=File|NativeBackupSelection
export interface BackupCapabilities {protocol:typeof BACKUP_PROTOCOL;ready:boolean;formatVersion:number;schemaVersion:number;maxUncompressedBytes:number;maxEntries:number;maxImageBytes:number;encrypted:true;defaultRestore:'new-copy';privateContainsHiddenWorldData:true}
export interface BackupReceipt {storyId:string;counts:Record<string,number>;historicalTasksReplayed:false;creditTransferred:false}
export interface BackupOperation {id:string;kind:'export'|'restore';status:'preparing'|'ready'|'restored'|'failed';version:number;counts:Record<string,number>;expiresAt:string;protocol:typeof BACKUP_PROTOCOL;objectType?:string;targetOwnerId?:string;errorCode?:string;receipt?:BackupReceipt}
export interface BackupIntent {schema:1;kind:'export'|'restore';phase:'export'|'prepare'|'confirm';requestId:string;storyId?:string;includeSource?:boolean;operationId?:string;expectedVersion?:number;targetOwnerId?:string}
const id=/^[a-f0-9]{32}$/,story=/^(book|world)-[a-f0-9]{32}$/
const fail=()=>new Error('备份服务返回的信息不完整，请查询已有请求')
function counts(value:unknown):Record<string,number>{
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length>100)throw fail()
  const output:Record<string,number>={}
  for(const [key,count] of Object.entries(value)){if(!/^[a-zA-Z0-9_-]{1,80}$/.test(key)||!Number.isSafeInteger(count)||Number(count)<0)throw fail();output[key]=Number(count)}
  return output
}
export function validateBackupOperation(value:unknown,expected?:{kind?:BackupOperation['kind'];id?:string}):BackupOperation{
  if(!value||typeof value!=='object')throw fail()
  const v=value as Record<string,unknown>
  if(typeof v.id!=='string'||!id.test(v.id)||!['export','restore'].includes(String(v.kind))||!['preparing','ready','restored','failed'].includes(String(v.status))||!Number.isSafeInteger(v.version)||Number(v.version)<1||v.protocol!==BACKUP_PROTOCOL||typeof v.expiresAt!=='string'||!Number.isFinite(Date.parse(v.expiresAt))||expected?.kind&&v.kind!==expected.kind||expected?.id&&v.id!==expected.id)throw fail()
  const result:BackupOperation={id:v.id,kind:v.kind as BackupOperation['kind'],status:v.status as BackupOperation['status'],version:Number(v.version),counts:counts(v.counts),expiresAt:v.expiresAt,protocol:BACKUP_PROTOCOL}
  if(v.objectType!==undefined){if(typeof v.objectType!=='string'||!['story','book','world'].includes(v.objectType))throw fail();result.objectType=v.objectType}
  if(v.targetOwnerId!==undefined){if(typeof v.targetOwnerId!=='string'||v.targetOwnerId!==accountId())throw fail();result.targetOwnerId=v.targetOwnerId}
  if(v.errorCode!==undefined){if(typeof v.errorCode!=='string'||!/^[A-Z0-9_]{1,100}$/.test(v.errorCode))throw fail();result.errorCode=v.errorCode}
  if(v.receipt!==undefined){const receipt=v.receipt as Record<string,unknown>;if(!receipt||typeof receipt.storyId!=='string'||!story.test(receipt.storyId)||receipt.historicalTasksReplayed!==false||receipt.creditTransferred!==false||result.kind!=='restore'||result.status!=='restored')throw fail();result.receipt={storyId:receipt.storyId,counts:counts(receipt.counts),historicalTasksReplayed:false,creditTransferred:false}}
  if(result.status==='restored'&&!result.receipt)throw fail()
  return result
}
export function captureBackupScope(storyId?:string,active:()=>boolean=()=>true){
  const epoch=authGeneration(),owner=accountId(),base=backendBase()
  return()=>!!owner&&epoch===authGeneration()&&owner===accountId()&&base===backendBase()&&active()&&(!storyId||story.test(storyId)&&!(storyId.startsWith('book-')?isBookDeleted(storyId.slice(5)):isWorldDeleted(storyId.slice(6))))
}
function check(active:()=>boolean){if(!active())throw new ApiError('SESSION_CHANGED','账号、服务器或故事已变化，请重新打开备份',401)}
export async function backupCapabilities():Promise<BackupCapabilities|null>{
  const active=captureBackupScope()
  try{const v=await api<BackupCapabilities>('/backups/capabilities');check(active);if(v.protocol!==BACKUP_PROTOCOL||v.ready!==true||v.encrypted!==true||v.defaultRestore!=='new-copy'||v.formatVersion!==1||v.schemaVersion!==1||v.privateContainsHiddenWorldData!==true)return null;return v}catch{return null}
}
export const backupIntentKey=(storyId?:string)=>'private-backup-intent:v1:'+(storyId||'import')
export function validateBackupIntent(value:unknown,storyId?:string):BackupIntent|null{
  if(!value||typeof value!=='object')return null
  const v=value as BackupIntent
  if(v.schema!==1||!['export','restore'].includes(v.kind)||!['export','prepare','confirm'].includes(v.phase)||typeof v.requestId!=='string'||!v.requestId||v.requestId.length>160||v.storyId!==storyId||storyId&&(!story.test(storyId)||v.kind!=='export'||v.phase!=='export')||!storyId&&(v.kind!=='restore'||v.phase==='export')||v.operationId!==undefined&&!id.test(v.operationId)||v.phase==='confirm'&&(!v.operationId||!Number.isInteger(v.expectedVersion)||Number(v.expectedVersion)<1||v.targetOwnerId!==accountId()))return null
  if(v.includeSource!==undefined&&(v.kind!=='export'||typeof v.includeSource!=='boolean'))return null
  return {schema:1,kind:v.kind,phase:v.phase,requestId:v.requestId,...(storyId?{storyId,includeSource:v.includeSource===true}:{}),...(v.operationId?{operationId:v.operationId}:{}),...(v.expectedVersion?{expectedVersion:v.expectedVersion}:{}),...(v.targetOwnerId?{targetOwnerId:v.targetOwnerId}:{})}
}
export async function loadBackupIntent(storyId?:string){const active=captureBackupScope(storyId),value=await cacheGet<unknown>(backupIntentKey(storyId));check(active);return validateBackupIntent(value,storyId)}
export async function saveBackupIntent(value:BackupIntent|null,storyId?:string,active=captureBackupScope(storyId)){
  check(active);const projected=value===null?null:validateBackupIntent(value,storyId);if(value!==null&&!projected)throw fail();await cacheSet(backupIntentKey(storyId),projected);check(active)
}
export async function backupStatus(operationId:string,kind:BackupOperation['kind'],active=captureBackupScope()){
  if(!id.test(operationId))throw fail();check(active);const value=await api('/backups/'+operationId);check(active);return validateBackupOperation(value,{id:operationId,kind})
}
export async function lookupBackup(intent:BackupIntent,active=captureBackupScope(intent.storyId)):Promise<BackupOperation|null>{
  check(active)
  if(intent.operationId)return backupStatus(intent.operationId,intent.kind,active)
  const response=await api<{found:boolean;operation:unknown}>('/backups/requests?kind='+intent.kind+'&requestId='+encodeURIComponent(intent.requestId));check(active)
  if(response?.found===false&&response.operation===null)return null
  if(response?.found!==true)throw fail()
  return validateBackupOperation(response.operation,{kind:intent.kind})
}
export async function exportPrivateBackup(storyId:string,password:string,includeSource:boolean,key:string,active=captureBackupScope(storyId)){
  if(native&&!(await nativeBackupCapabilities()))throw new Error('此安装包尚未提供流式备份文件保存，请使用网页版')
  if(!story.test(storyId)||!password)throw new Error('请填写本次备份密码');check(active)
  const body=new FormData();body.append('password',password);body.append('includeSource',String(includeSource))
  const value=await api('/stories/'+storyId+'/backup',{method:'POST',body,key,retry:false});check(active);return validateBackupOperation(value,{kind:'export'})
}
export async function preparePrivateBackup(file:BackupFile,password:string,key:string,active=captureBackupScope()){
  const name='selectionId'in file?file.displayName:file.name
  if(!name.toLowerCase().endsWith('.srbackup')||file.size!==null&&(file.size<1||file.size>MAX_BACKUP_UPLOAD))throw new Error('请选择配额内的 .srbackup 文件，原文件不会改动')
  if(!password)throw new Error('请填写备份密码');check(active)
  if(native){if(!('selectionId'in file))throw new Error('请使用原生流式文件选择');const value=await prepareNativeBackup(file,password,key);check(active);return validateBackupOperation(value,{kind:'restore'})}
  if('selectionId'in file)throw new Error('请选择当前浏览器中的备份文件')
  // The browser streams the File as multipart. Never read it into Base64 or
  // pass it through the novel picker and its unrelated 50 MiB limit.
  const body=new FormData();body.append('file',file);body.append('password',password)
  const value=await api('/backups/prepare',{method:'POST',body,key,retry:false});check(active);return validateBackupOperation(value,{kind:'restore'})
}
export async function confirmPrivateBackup(operation:BackupOperation,key:string,active=captureBackupScope()){
  check(active);if(operation.kind!=='restore'||operation.status!=='ready'||operation.targetOwnerId!==accountId()||Date.parse(operation.expiresAt)<=Date.now())throw new Error('预检已失效，请重新查询或选择文件')
  const value=await api('/backups/'+operation.id+'/restore',{method:'POST',key,retry:false,body:{confirmed:true,expectedVersion:operation.version,targetOwnerId:accountId()}});check(active);return validateBackupOperation(value,{id:operation.id,kind:'restore'})
}
export interface BackupWriter {write(data:Uint8Array):Promise<void>;close():Promise<void>;abort():Promise<void>}
export async function writeBackupResponse(response:Response,writer:BackupWriter,active:()=>boolean,onProgress:(bytes:number)=>void=()=>{}){
  const reader=response.body?.getReader();let received=0
  try{if(!response.ok||!reader||!['application/octet-stream','application/x-srbackup'].includes((response.headers.get('Content-Type')||'').split(';')[0]!))throw new Error('备份下载响应无效');const size=Number(response.headers.get('Content-Length')||0);if(size>MAX_BACKUP_UPLOAD)throw new Error('备份文件超过下载配额');check(active);for(;;){const result=await reader.read();check(active);if(result.done)break;received+=result.value.byteLength;if(received>MAX_BACKUP_UPLOAD)throw new Error('备份文件超过下载配额');await writer.write(result.value);check(active);onProgress(received)}if(size&&size!==received||!received)throw new Error('下载未完整结束，请重新下载');await writer.close();check(active)}
  catch(error){await reader?.cancel().catch(()=>{});await writer.abort().catch(()=>{});throw error}
  finally{reader?.releaseLock()}
}
export async function downloadPrivateBackup(operation:BackupOperation,active=captureBackupScope(),onProgress:(bytes:number)=>void=()=>{}){
  if(operation.kind!=='export'||operation.status!=='ready'||Date.parse(operation.expiresAt)<=Date.now())throw new Error('下载已失效，请重新导出')
  check(active)
  if(native){await downloadNativeBackup(operation.id,onProgress);check(active);return}
  type SavePicker=(options:unknown)=>Promise<{createWritable:()=>Promise<BackupWriter>}>
  const picker=(window as unknown as {showSaveFilePicker?:SavePicker}).showSaveFilePicker
  // Obtain the handle in the user's click before awaiting the authenticated GET.
  const handle=picker?await picker({suggestedName:'story-'+operation.id+'.srbackup',types:[{description:'私人加密备份',accept:{'application/octet-stream':['.srbackup']}}]}):undefined
  check(active)
  const endpoint=backendBase()+'/api/reader/v1/backups/'+operation.id+'/download'
  let response=await fetch(endpoint,{headers:authorizationHeaders(),credentials:'include'});check(active)
  if(response.status===401){if(await refreshAuth()){check(active);response=await fetch(endpoint,{headers:authorizationHeaders(),credentials:'include'});check(active)}}
  if(!response.ok){await response.body?.cancel();throw new Error(response.status===401?'请重新登录后下载备份':'备份文件不可下载，请查询最新状态')}
  if(handle){const writer=await handle.createWritable();return writeBackupResponse(response,writer,active,onProgress)}
  const chunks:Uint8Array[]=[];let bytes=0
  const writer:BackupWriter={async write(data){bytes+=data.byteLength;if(bytes>MAX_BUFFERED_DOWNLOAD)throw new Error('此浏览器不支持大文件流式保存，请使用支持文件保存的桌面浏览器');chunks.push(data)},async close(){check(active);const url=URL.createObjectURL(new Blob(chunks as BlobPart[],{type:'application/octet-stream'})),link=document.createElement('a');try{link.href=url;link.download='story-'+operation.id+'.srbackup';document.body.append(link);link.click();link.remove()}finally{setTimeout(()=>URL.revokeObjectURL(url),1000)}},async abort(){chunks.length=0}}
  if(Number(response.headers.get('Content-Length')||0)>MAX_BUFFERED_DOWNLOAD){await response.body?.cancel();throw new Error('此浏览器不支持大文件流式保存，请使用支持文件保存的桌面浏览器')}
  return writeBackupResponse(response,writer,active,onProgress)
}
