import {sha256,ContractError,type Document} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {importLocalText,importSourceId,type ImportEncoding,type ImportProgress} from '../repositories/local-import'
import {importPrefix,validateImportEntry,type ImportEntry} from './import-contract'
export async function importEntries(vault:LocalVault){const rows=await vault.listPrefix<ImportEntry>(importPrefix);for(const row of rows){if(row.kind!=='settings')throw new ContractError('IMPORT_ENTRY','导入记录类型错误');validateImportEntry(row.value,row.id)}return rows.sort((a,b)=>a.value.createdAt.localeCompare(b.value.createdAt)||a.id.localeCompare(b.id))}
export function importManifest(files:Pick<File,'name'|'size'>[],encoding:ImportEncoding):WriteDocument[]{
 if(!files.length||files.length>200)throw new ContractError('IMPORT_BATCH','每批请选择 1—200 个文件')
 const now=new Date().toISOString()
 return files.map(file=>{const id=importPrefix+crypto.randomUUID(),supported=/\.(txt|md|markdown|epub)$/i.test(file.name)&&file.size<=100*1024*1024,value:ImportEntry={assetType:'import-entry',name:file.name,bytes:file.size,encoding,sha256:null,sourceId:null,storyId:null,status:supported?'needs_file':'rejected',ownerId:null,error:supported?'':file.size>100*1024*1024?'文件超过 100 MiB':'请选择 TXT、Markdown 或 EPUB',createdAt:now,updatedAt:now,retired:false};validateImportEntry(value,id);return {kind:'settings',id,expectedRevision:0,value}})
}
async function entry(vault:LocalVault,id:string){const row=await vault.read<ImportEntry>(id);if(!row||row.kind!=='settings')throw new ContractError('IMPORT_ENTRY','这项导入记录不存在');validateImportEntry(row.value,id);return row}
async function write(vault:LocalVault,row:Document<ImportEntry>,patch:Partial<ImportEntry>){const value={...row.value,...patch,updatedAt:new Date().toISOString()};validateImportEntry(value,row.id);const receipt=await vault.commit(crypto.randomUUID(),'settings',row.id,row.revision,value);return {id:row.id,kind:'settings' as const,revision:receipt.revision,value,updatedAt:receipt.committedAt}}
/** The full raw source and its queue record appear together, before conversion. */
export async function stageImportFile(vault:LocalVault,id:string,file:File,signal:AbortSignal){
 signal.throwIfAborted();let row=await entry(vault,id)
 if(row.value.retired||row.value.status==='rejected'||row.value.name!==file.name||row.value.bytes!==file.size)throw new ContractError('IMPORT_FILE','请重新选择记录中同名、同长度的原文件')
 const bytes=new Uint8Array(await file.arrayBuffer()),hash=await sha256(bytes);signal.throwIfAborted()
 if(row.value.sourceId){if(row.value.sha256!==hash)throw new ContractError('IMPORT_FILE_CHANGED','文件内容已变化，请作为新文件导入');return row}
 const sourceId=await importSourceId(vault,hash),chunks:WriteDocument[]=[]
 for(let start=0;start<bytes.length;start+=1024*1024){signal.throwIfAborted();const chunk=bytes.slice(start,start+1024*1024);let binary='';for(let i=0;i<chunk.length;i+=8192)binary+=String.fromCharCode(...chunk.subarray(i,i+8192));chunks.push({kind:'sourceChunk',id:sourceId+':'+chunks.length,expectedRevision:0,value:{data:btoa(binary),bytes:chunk.length,sha256:await sha256(chunk)}})}
 for(let attempt=0;attempt<3;attempt++){
  row=await entry(vault,id);if(row.value.retired)throw new ContractError('IMPORT_RETIRED','该记录已移出列表');if(row.value.sourceId){if(row.value.sha256!==hash)throw new ContractError('IMPORT_FILE_CHANGED','原文件校验不一致');return row}
  const source=await vault.read<{sha256:string;bytes:number}>(sourceId);if(source&&(source.kind!=='source'||source.value.sha256!==hash||source.value.bytes!==bytes.length))throw new ContractError('IMPORT_SOURCE','已有文件校验不一致')
  const writes:WriteDocument[]=source?[]:[{kind:'source',id:sourceId,expectedRevision:0,value:{name:file.name,sha256:hash,bytes:bytes.length,chunkIds:chunks.map(c=>c.id),detectedEncodingAtImport:'pending-conversion'}},...chunks]
  writes.push({kind:'settings',id,expectedRevision:row.revision,value:{...row.value,sha256:hash,sourceId,status:'ready',error:'',updatedAt:new Date().toISOString()}})
  try{await vault.commitBundle('stage:'+id+':'+hash,writes,signal);return entry(vault,id)}catch(e){if((e as {code?:string}).code!=='REVISION_CONFLICT'||attempt===2)throw e}
 }
 throw new ContractError('REVISION_CONFLICT','导入记录已变化')
}
async function fileFromSource(vault:LocalVault,row:Document<ImportEntry>,signal:AbortSignal){
 const v=row.value,source=await vault.read<{sha256:string;bytes:number;chunkIds:string[]}>(v.sourceId!);if(!source||source.kind!=='source'||source.value.sha256!==v.sha256||source.value.bytes!==v.bytes)throw new ContractError('IMPORT_SOURCE','原文件来源校验失败')
 const bytes=new Uint8Array(v.bytes);let offset=0
 for(let start=0;start<source.value.chunkIds.length;start+=16){signal.throwIfAborted();const ids=source.value.chunkIds.slice(start,start+16),parts=new Map((await vault.readMany<{data:string;bytes:number;sha256:string}>(ids)).map(r=>[r.id,r]));for(const id of ids){const part=parts.get(id);if(!part||part.kind!=='sourceChunk')throw new ContractError('IMPORT_SOURCE','原文件片段缺失');const data=Uint8Array.from(atob(part.value.data),c=>c.charCodeAt(0));if(data.length!==part.value.bytes||await sha256(data)!==part.value.sha256||offset+data.length>bytes.length)throw new ContractError('IMPORT_SOURCE','原文件片段校验失败');bytes.set(data,offset);offset+=data.length}}
 if(offset!==v.bytes||await sha256(bytes)!==v.sha256)throw new ContractError('IMPORT_SOURCE','原文件校验失败')
 return new File([bytes],v.name,{type:/\.epub$/i.test(v.name)?'application/epub+zip':'text/plain'})
}
export async function convertImportEntry(vault:LocalVault,id:string,signal:AbortSignal,onProgress:(value:ImportProgress)=>void,takeover=false):Promise<string>{
 const old=await entry(vault,id);if(old.value.retired)throw new ContractError('IMPORT_RETIRED','该记录已移出列表');if(old.value.status==='complete')return old.value.storyId!;if(!old.value.sourceId)throw new ContractError('IMPORT_FILE_REQUIRED','请重新选择原文件');if(old.value.status==='importing'&&!takeover)throw new ContractError('IMPORT_BUSY','另一窗口正在转换；关闭后可接管并继续')
 const claim=await write(vault,old,{status:'importing',ownerId:crypto.randomUUID(),error:''})
 try{
  const file=await fileFromSource(vault,claim,signal),storyId=await importLocalText(vault,file,claim.value.encoding,onProgress,signal,{sourceId:claim.value.sourceId!,preconditions:[{kind:'settings',id,revision:claim.revision}]})
  await write(vault,claim,{status:'complete',ownerId:null,storyId,error:''});return storyId
 }catch(e){
  if(vault.unlocked){const current=await entry(vault,id);if(current.value.status==='complete'&&current.value.sha256===claim.value.sha256)return current.value.storyId!;if(current.revision===claim.revision&&current.value.ownerId===claim.value.ownerId)await write(vault,current,{status:(e as Error).name==='AbortError'?'cancelled':'failed',ownerId:null,error:String((e as Error).message).slice(0,2000)})}
  throw e
 }
}
export async function updateImportEntry(vault:LocalVault,id:string,patch:Pick<Partial<ImportEntry>,'encoding'|'retired'>){const row=await entry(vault,id);if(row.value.status==='importing'&&patch.encoding!==undefined)throw new ContractError('IMPORT_ACTIVE','请先停止转换');return write(vault,row,patch)}
export async function recordImportStageError(vault:LocalVault,id:string,message:string){const row=await entry(vault,id);if(row.value.status==='needs_file')await write(vault,row,{error:message.slice(0,2000)})}
