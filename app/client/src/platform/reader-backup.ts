import {registerPlugin} from '@capacitor/core'
import {accountId,backendBase,native} from './bridge'
import {authGeneration,authorizationHeaders,ApiError} from '../api'
import {installReaderBackupDocumentGate,readerBackupErrorMatchesDocument,type ReaderBackupBridgeWindow} from './reader-backup-document'

export interface NativeBackupSelection {selectionId:string;size:number|null;displayName:string}
export interface NativeBackupCapabilities {ready:boolean;streaming:boolean;maxFileBytes:number;readingExport?:true;maxReadingBytes?:number}
export interface NativeReadingExportCapabilities {ready:true;maxReadingBytes:number}
export interface NativeReadingExport {operationId:string;format:'txt'|'epub';size:number;sha256:string;fileName:string}
export interface NativeBackupProgress {phase:'upload'|'download';bytes:number;totalBytes?:number}
type DocumentCall={documentNonce:string}
type DocumentEnvelope={documentProtocol:'reader-document-v1';documentNonce:string}
export interface ReaderBackupPlugin {
  setContext(value:{backend:string;ownerId:string;epoch:number}&DocumentCall):Promise<{ready:boolean}&DocumentEnvelope>
  getCapabilities(value:DocumentCall):Promise<NativeBackupCapabilities&DocumentEnvelope>
  pickBackup(value:DocumentCall):Promise<NativeBackupSelection&DocumentEnvelope>
  prepareBackup(value:{selectionId:string;password:string;requestId:string;accessToken:string;epoch:number}&DocumentCall):Promise<unknown>
  downloadBackup(value:{operationId:string;accessToken:string;epoch:number}&DocumentCall):Promise<{saved:boolean}&DocumentEnvelope>
  downloadReadingExport(value:NativeReadingExport&{accessToken:string;epoch:number}&DocumentCall):Promise<{saved:boolean}&DocumentEnvelope>
  cancel(value:{epoch:number}&DocumentCall):Promise<unknown>
  addListener(name:'backupProgress',callback:(value:NativeBackupProgress&DocumentEnvelope)=>void):Promise<{remove:()=>Promise<void>}>
}
const plugin=registerPlugin<ReaderBackupPlugin>('ReaderBackup')
const documentCall:DocumentCall=(()=>{
  const name='__readerBackupDocumentNonce',existing=(window as unknown as Record<string,unknown>)[name]
  if(typeof existing==='string'&&/^[a-f0-9]{32}$/.test(existing))return {documentNonce:existing}
  const bytes=crypto.getRandomValues(new Uint8Array(16)),nonce=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('')
  Object.defineProperty(window,name,{value:nonce,writable:false,configurable:false,enumerable:false})
  return {documentNonce:nonce}
})()
if(native)installReaderBackupDocumentGate(window as unknown as ReaderBackupBridgeWindow,documentCall.documentNonce)
let contextWork:Promise<unknown>=Promise.resolve()
const identity=()=>({backend:backendBase(),ownerId:accountId(),epoch:authGeneration()})
const active=(value:ReturnType<typeof identity>)=>value.backend===backendBase()&&value.ownerId===accountId()&&value.epoch===authGeneration()
function changed(){return new ApiError('BACKUP_CONTEXT_CHANGED','账号或服务器已变化，请重新选择备份文件',401)}
function documentChanged(){return new ApiError('BACKUP_DOCUMENT_CHANGED','原生文件通道已变化，请重新打开',0)}
function sameDocument(value:unknown):value is Record<string,unknown>&DocumentEnvelope{
  return !!value&&typeof value==='object'&&!Array.isArray(value)&&(value as DocumentEnvelope).documentProtocol==='reader-document-v1'&&(value as DocumentEnvelope).documentNonce===documentCall.documentNonce
}
function body<T>(value:unknown):T{
  if(!sameDocument(value))throw documentChanged()
  const {documentProtocol:_,documentNonce:__,...result}=value;return result as T
}
async function call<T>(work:()=>Promise<unknown>):Promise<T>{
  try{return body<T>(await work())}
  catch(error){
    if(error instanceof ApiError&&error.code==='BACKUP_DOCUMENT_CHANGED')throw error
    if(!readerBackupErrorMatchesDocument(error,documentCall.documentNonce))throw documentChanged()
    const value=error as {message?:unknown;code?:unknown}
    const message=typeof value.message==='string'?value.message:'原生文件操作失败'
    throw Object.assign(new Error(message),{code:typeof value.code==='string'?value.code:'BACKUP_TRANSFER_FAILED'})
  }
}
function timedContext<T>(work:Promise<T>):Promise<T>{
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(documentChanged()),15000)
    work.then(resolve,reject).finally(()=>clearTimeout(timer))
  })
}
async function context(){
  if(!native)throw new Error('此功能仅用于原生文件流')
  const value=identity();if(!/^[a-f0-9]{32}$/.test(value.ownerId)||!value.backend)throw changed()
  const protectedEntries=installReaderBackupDocumentGate(window as unknown as ReaderBackupBridgeWindow,documentCall.documentNonce)
  if(!protectedEntries.legacy&&!protectedEntries.modern)throw documentChanged()
  const request=contextWork.catch(()=>{}).then(()=>timedContext(call<{ready:boolean}>(()=>plugin.setContext({...value,...documentCall}))));contextWork=request
  const response=await request;if(!active(value)||response.ready!==true)throw changed();return value
}
function token(){const value=authorizationHeaders().Authorization;if(!value?.startsWith('Bearer '))throw new ApiError('AUTH_REQUIRED','请重新登录后处理备份',401);return value.slice(7)}
export async function nativeBackupCapabilities():Promise<NativeBackupCapabilities|null>{
  if(!native)return null
  try{
    const value=await context(),result=await timedContext(call<NativeBackupCapabilities>(()=>plugin.getCapabilities(documentCall)));if(!active(value))throw changed()
    if(result.ready!==true||result.streaming!==true||!Number.isSafeInteger(result.maxFileBytes)||result.maxFileBytes<=0)return null
    const projected:NativeBackupCapabilities={ready:true,streaming:true,maxFileBytes:result.maxFileBytes},reading=readingCapability(result)
    if(reading){projected.readingExport=true;projected.maxReadingBytes=reading.maxReadingBytes}
    return projected
  }catch{return null}
}
const MAX_NATIVE_READING_BYTES=2*1024**3
function readingCapability(value:NativeBackupCapabilities|null):NativeReadingExportCapabilities|null{
  return value?.ready===true&&value.streaming===true&&value.readingExport===true&&Number.isSafeInteger(value.maxReadingBytes)&&value.maxReadingBytes!>0&&value.maxReadingBytes!<=MAX_NATIVE_READING_BYTES?{ready:true,maxReadingBytes:value.maxReadingBytes!}:null
}
export async function nativeReadingExportCapabilities():Promise<NativeReadingExportCapabilities|null>{return readingCapability(await nativeBackupCapabilities())}
function readingMetadata(value:NativeReadingExport):NativeReadingExport{
  if(!value||typeof value.operationId!=='string'||!/^[a-f0-9]{32}$/.test(value.operationId)||typeof value.format!=='string'||!['txt','epub'].includes(value.format)||!Number.isSafeInteger(value.size)||value.size<1||value.size>MAX_NATIVE_READING_BYTES||typeof value.sha256!=='string'||!/^[a-f0-9]{64}$/.test(value.sha256)||typeof value.fileName!=='string'||!value.fileName||value.fileName.startsWith('.')||value.fileName.endsWith(' ')||/[\x00-\x1f\x7f\\/:*?"<>|]/.test(value.fileName)||!value.fileName.endsWith('.'+value.format)||[...value.fileName].some(char=>char.length===1&&char.charCodeAt(0)>=0xd800&&char.charCodeAt(0)<=0xdfff)||new TextEncoder().encode(value.fileName).length>512)throw new ApiError('EXPORT_FILE_INVALID','导出文件信息无效，请查询本次请求',0)
  return {operationId:value.operationId,format:value.format,size:value.size,sha256:value.sha256,fileName:value.fileName}
}
function readingFailure(error:unknown){
  const allowed=new Set('BACKUP_CANCELLED BACKUP_CONTEXT_CHANGED BACKUP_DOCUMENT_CHANGED AUTH_REQUIRED EXPORT_FILE_INVALID EXPORT_NATIVE_UNAVAILABLE BACKUP_DOCUMENT_UNAVAILABLE BACKUP_TRANSFER_BUSY BACKUP_PICKER_UNAVAILABLE BACKUP_SIZE_LIMIT BACKUP_REDIRECT_REFUSED BACKUP_TRANSFER_FAILED BACKUP_TEMP_CLEANUP_FAILED BACKUP_DOWNLOAD_FAILED'.split(' '))
  const candidate=(error as {code?:unknown})?.code,code=typeof candidate==='string'&&allowed.has(candidate)?candidate:'BACKUP_TRANSFER_FAILED'
  return new ApiError(code,code==='BACKUP_CANCELLED'?'已取消文件保存':code==='EXPORT_NATIVE_UNAVAILABLE'?'此安装包尚未提供阅读文件保存，请更新后重试':code==='BACKUP_CONTEXT_CHANGED'?'账号或故事已变化，请重新打开导出':code==='BACKUP_DOCUMENT_CHANGED'?'原生文件通道已变化，请重新打开':'文件尚未保存，请重试或查询本次请求',0)
}
export async function downloadNativeReadingExport(input:NativeReadingExport,onProgress:(bytes:number)=>void=()=>{},valid:()=>boolean=()=>true):Promise<void>{
  const metadata=readingMetadata(input),request=identity();let listener:Awaited<ReturnType<ReaderBackupPlugin['addListener']>>|undefined,received=0
  const check=()=>{if(!active(request)||!valid())throw changed()}
  check()
  try{
    const value=await context();check()
    const result=await timedContext(call<NativeBackupCapabilities>(()=>plugin.getCapabilities(documentCall)));check()
    const capability=readingCapability(result)
    if(!capability||metadata.size>capability.maxReadingBytes||typeof plugin.downloadReadingExport!=='function')throw new ApiError('EXPORT_NATIVE_UNAVAILABLE','原生阅读文件保存不可用',0)
    listener=await plugin.addListener('backupProgress',progress=>{
      if(active(value)&&valid()&&sameDocument(progress)&&progress.phase==='download'&&Number.isSafeInteger(progress.bytes)&&progress.bytes>=received&&progress.bytes<=metadata.size){received=progress.bytes;onProgress(received)}
    });check()
    const saved=await call<{saved:boolean}>(()=>plugin.downloadReadingExport({...metadata,accessToken:token(),epoch:value.epoch,...documentCall}));check()
    if(saved.saved!==true)throw new ApiError('BACKUP_TRANSFER_FAILED','原生文件保存尚未确认',0)
  }catch(error){check();throw readingFailure(error)}
  finally{await listener?.remove().catch(()=>{})}
}
export async function selectNativeBackup():Promise<NativeBackupSelection>{
  const value=await context(),result=await call<NativeBackupSelection>(()=>plugin.pickBackup(documentCall));if(!active(value))throw changed()
  if(!/^[a-f0-9]{32}$/.test(result.selectionId)||result.size!==null&&(!Number.isSafeInteger(result.size)||result.size<1)||typeof result.displayName!=='string'||result.displayName.length>500)throw new Error('原生文件选择结果无效')
  return {selectionId:result.selectionId,size:result.size,displayName:result.displayName}
}
export async function prepareNativeBackup(selection:NativeBackupSelection,password:string,requestId:string){
  const value=await context(),result=await call<unknown>(()=>plugin.prepareBackup({selectionId:selection.selectionId,password,requestId,accessToken:token(),epoch:value.epoch,...documentCall}));if(!active(value))throw changed();return result
}
export async function downloadNativeBackup(operationId:string,onProgress:(bytes:number)=>void=()=>{}){
  const value=await context();let listener:Awaited<ReturnType<ReaderBackupPlugin['addListener']>>|undefined
  try{
    listener=await plugin.addListener('backupProgress',progress=>{if(active(value)&&sameDocument(progress)&&progress.phase==='download'&&Number.isSafeInteger(progress.bytes)&&progress.bytes>=0)onProgress(progress.bytes)})
    if(!active(value))throw changed()
    const result=await call<{saved:boolean}>(()=>plugin.downloadBackup({operationId,accessToken:token(),epoch:value.epoch,...documentCall}));if(!active(value))throw changed();if(result.saved!==true)throw new Error('原生文件保存尚未确认')
  }finally{await listener?.remove().catch(()=>{})}
}
export async function cancelNativeBackup(){const value=await context();await call(()=>plugin.cancel({epoch:value.epoch,...documentCall}));if(!active(value))throw changed()}
function clearContext(){if(!native)return;installReaderBackupDocumentGate(window as unknown as ReaderBackupBridgeWindow,documentCall.documentNonce);const epoch=authGeneration();contextWork=contextWork.catch(()=>{}).then(()=>call(()=>plugin.setContext({backend:'',ownerId:'',epoch,...documentCall}))).catch(()=>{})}
if(typeof window!=='undefined'){
  window.addEventListener('reader-private-cleared',clearContext)
  window.addEventListener('reader-backend-changed',clearContext)
}
