import {canonical,sha256,ContractError,type LocalStory,type Chapter,type Paragraph,type DomainKind} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from './local-vault'
import {parseLocalEpub} from './local-epub'
import {parseTextInBackground} from './local-text-background'
export type ImportEncoding='auto'|'utf-8'|'utf-16le'|'utf-16be'|'gb18030'|'big5'
export interface ImportProgress {phase:'reading'|'indexing'|'saving'|'complete';paragraphs:number;chapters:number}
/** An explicit fresh import gets the next content identity. Deleted identities
 * and their receipts remain sealed; concurrent equal content shares this ID. */
async function freshContentHash(vault:LocalVault,hash:string){for(let generation=0;generation<1000;generation++){const candidate=generation?await sha256(canonical({contentHash:hash,generation})):hash;if(!await vault.wasDeleted('story-'+candidate.slice(0,32)))return candidate}throw new ContractError('IMPORT_IDENTITY_LIMIT','导入历史过多，请从备份恢复到新私人库')}
export async function importSourceId(vault:LocalVault,hash:string){for(let generation=0;generation<1000;generation++){const candidate=generation?await sha256(canonical({sourceHash:hash,generation})):hash,id='source-'+candidate.slice(0,32);if(!await vault.wasDeleted(id))return id}throw new ContractError('IMPORT_IDENTITY_LIMIT','原文件历史过多，请恢复到新私人库')}
function decode(bytes:Uint8Array,selection:ImportEncoding){
  const encoding=selection!=='auto'?selection:bytes[0]===0xff&&bytes[1]===0xfe?'utf-16le':bytes[0]===0xfe&&bytes[1]===0xff?'utf-16be':'utf-8'
  try{return {text:new TextDecoder(encoding,{fatal:true}).decode(bytes),encoding}}
  catch{if(selection==='auto'&&encoding==='utf-8'){try{return {text:new TextDecoder('gb18030',{fatal:true}).decode(bytes),encoding:'gb18030'}}catch{/* User can choose another encoding without overwriting the file. */}}
    throw new ContractError('ENCODING_INVALID','无法解码，请重新选择文件编码')}
}
export async function importLocalText(vault:LocalVault,file:File,encoding:ImportEncoding,onProgress:(value:ImportProgress)=>void,signal:AbortSignal,staged?:{sourceId:string;preconditions:{kind:DomainKind;id:string;revision:number}[]},presentation?:{coverAssetId:string}):Promise<string>{
  const suffix=file.name.toLowerCase().split('.').pop()
  if(!['txt','md','markdown','epub'].includes(suffix||''))throw new ContractError('UNSUPPORTED_FORMAT','请选择 TXT、Markdown 或 EPUB 文件')
  if(file.size>100*1024*1024)throw new ContractError('IMPORT_LIMIT','文件超过 100 MiB')
  const active=()=>{signal.throwIfAborted();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定')}
  active();onProgress({phase:'reading',paragraphs:0,chapters:0})
  const bytes=new Uint8Array(await file.arrayBuffer());active()
  const sourceHash=await sha256(bytes),epub=suffix==='epub'?await parseLocalEpub(bytes,signal):null,decoded=epub?{text:'',encoding:'epub-spine-v1'}:decode(bytes,encoding),previous=await vault.list<LocalStory>('story'),existing=previous.find(row=>row.value.sourceHash===sourceHash&&row.value.encoding===decoded.encoding)
  active()
  async function completeCover(id:string){
    if(!presentation?.coverAssetId)return
    for(let attempt=0;attempt<3;attempt++){
      const row=await vault.read<LocalStory>(id);active()
      if(!row||row.kind!=='story')throw new ContractError('IMPORT_SOURCE','已导入的故事已不可用')
      // Keep an existing user cover. A prior import without one can be completed
      // explicitly; the revision fence also protects concurrent cover changes.
      if(row.value.coverAssetId)return
      try{await vault.commitBundle('import-cover:'+id+':'+row.revision+':'+presentation.coverAssetId,[{kind:'story',id,expectedRevision:row.revision,value:{...row.value,coverAssetId:presentation.coverAssetId}}],signal,staged?.preconditions);return}
      catch(e){if((e as {code?:string}).code!=='REVISION_CONFLICT'||attempt===2)throw e}
    }
  }
  if(existing){if(staged)await vault.checkPreconditions(staged.preconditions);await completeCover(existing.id);onProgress({phase:'complete',paragraphs:existing.value.paragraphIds.length,chapters:existing.value.chapters.length});return existing.id}
  const text=decoded.text,parseHash=await freshContentHash(vault,await sha256(canonical({sourceHash,encoding:decoded.encoding,parser:epub?'local-epub-v1':'local-text-v1'})))
  if(!epub&&text.includes('\0'))throw new ContractError('IMPORT_LIMIT','正文不能包含空字符')
  const storyId='story-'+parseHash.slice(0,32)
  let chapters:Chapter[]=[],paragraphs:Paragraph[]=[],chapter:Chapter|undefined,offset=0
  if(epub){
    for(const entry of epub.chapters){chapter={id:'chapter-'+parseHash.slice(0,16)+'-'+chapters.length,title:entry.title,paragraphIds:[]};chapters.push(chapter);for(const body of entry.text.split('\n')){if(body.trim()){const id='paragraph-'+parseHash.slice(0,20)+'-'+paragraphs.length;paragraphs.push({id,chapterId:chapter.id,text:body,sourceStart:offset,sourceEnd:offset+body.length,textHash:''});chapter.paragraphIds.push(id)}offset+=body.length+1;}active();onProgress({phase:'indexing',paragraphs:paragraphs.length,chapters:chapters.length})}
  }else{
    const parsed=await parseTextInBackground(text,suffix!,parseHash,signal,p=>{active();onProgress({phase:'indexing',...p})});active();chapters=parsed.chapters;paragraphs=parsed.paragraphs
  }
  if(!paragraphs.length)throw new ContractError('IMPORT_EMPTY','文件没有可阅读的正文')
  if(epub)for(let start=0;start<paragraphs.length;start+=64){await Promise.all(paragraphs.slice(start,start+64).map(async p=>{p.textHash=await sha256(p.text)}));active()}
  const sourceId=staged?.sourceId||await importSourceId(vault,sourceHash),chunks:WriteDocument[]=[]
  for(let start=0;start<bytes.length;start+=1024*1024){
    const chunk=bytes.slice(start,start+1024*1024);let binary=''
    for(let offset=0;offset<chunk.length;offset+=8192)binary+=String.fromCharCode(...chunk.subarray(offset,offset+8192))
    chunks.push({kind:'sourceChunk',id:sourceId+':'+chunks.length,expectedRevision:0,value:{data:btoa(binary),sha256:await sha256(chunk),bytes:chunk.length}});active()
  }
  const title=(epub?.title||file.name.replace(/\.(txt|md|markdown|epub)$/i,''))+(previous.some(row=>row.value.sourceHash===sourceHash)?'（'+decoded.encoding+'）':'')
  const story:LocalStory={title,origin:'import',immutableSource:true,sourceHash,sourceId,format:suffix==='epub'?'epub':suffix==='txt'?'txt':'md',encoding:decoded.encoding,chapters,paragraphIds:paragraphs.map(p=>p.id),tags:[],favorite:false,...(presentation?.coverAssetId?{coverAssetId:presentation.coverAssetId}:{})}
  const storedSource=await vault.read<{sha256:string;bytes:number}>(sourceId)
  if(staged&&!storedSource)throw new ContractError('IMPORT_SOURCE','暂存的原文件已不可用，未创建书籍')
  if(storedSource&&(storedSource.kind!=='source'||storedSource.value.sha256!==sourceHash||storedSource.value.bytes!==bytes.length))throw new ContractError('SOURCE_INTEGRITY_INVALID','已有原始文件校验失败，未覆盖')
  const sourceWrites:WriteDocument[]=storedSource?[]:[{kind:'source',id:sourceId,expectedRevision:0,value:{name:file.name,sha256:sourceHash,bytes:bytes.length,detectedEncodingAtImport:decoded.encoding,offsetUnit:epub?'utf16-normalized-epub-spine-v1':'utf16-decoded-with-story-encoding',chunkIds:chunks.map(row=>row.id)}},...chunks]
  const writes:WriteDocument[]=[{kind:'story',id:storyId,expectedRevision:0,value:story},...sourceWrites,...paragraphs.map(p=>({kind:'paragraph' as const,id:p.id,expectedRevision:0,value:p}))]
  active();onProgress({phase:'saving',paragraphs:paragraphs.length,chapters:chapters.length})
  try{await vault.commitBundle('import:'+parseHash,writes,signal,staged?.preconditions)}catch(error){
    // Two queue entries may own the same original file. The content operation
    // is shared, while each entry keeps its own authenticated claim fence.
    if((!staged&&!presentation)||!['IDEMPOTENCY_CONFLICT','REVISION_CONFLICT'].includes((error as {code?:string}).code||''))throw error
    const winner=await vault.read<LocalStory>(storyId)
    if(!winner||winner.kind!=='story'||winner.value.sourceHash!==sourceHash||winner.value.encoding!==decoded.encoding)throw error
    if(staged)await vault.checkPreconditions(staged.preconditions);active();await completeCover(storyId)
  }
  // Once the transaction commits, cancellation must not claim that it did not.
  onProgress({phase:'complete',paragraphs:paragraphs.length,chapters:chapters.length});return storyId
}
