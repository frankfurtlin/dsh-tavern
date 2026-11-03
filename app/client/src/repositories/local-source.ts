import {sha256,ContractError,type LocalStory} from '../domain/local-contract'
import type {LocalVault} from './local-vault'
interface Source {name:string;sha256:string;bytes:number;encoding:string;offsetUnit:string;chunkIds:string[]}
interface Chunk {data:string;sha256:string;bytes:number}
/** Rebuild exact original bytes, rather than exporting normalized paragraphs. */
export async function originalSource(vault:LocalVault,story:LocalStory):Promise<{name:string;bytes:Uint8Array}>{
  const active=()=>{if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，原文件未导出')}
  active()
  if(!story.sourceId)throw new ContractError('SOURCE_UNAVAILABLE','此版本没有保留原始文件；正文仍可阅读')
  const source=await vault.read<Source>(story.sourceId)
  if(!source||source.kind!=='source'||source.value.sha256!==story.sourceHash||source.value.bytes>100*1024*1024)throw new ContractError('SOURCE_INTEGRITY_INVALID','原始文件索引校验失败')
  const bytes=new Uint8Array(source.value.bytes);let offset=0
  for(const id of source.value.chunkIds){
    const row=await vault.read<Chunk>(id)
    if(!row||row.kind!=='sourceChunk')throw new ContractError('SOURCE_INTEGRITY_INVALID','原始文件缺少片段')
    const binary=atob(row.value.data),chunk=Uint8Array.from(binary,c=>c.charCodeAt(0))
    if(chunk.length!==row.value.bytes||await sha256(chunk)!==row.value.sha256||offset+chunk.length>bytes.length)throw new ContractError('SOURCE_INTEGRITY_INVALID','原始文件片段校验失败')
    active()
    bytes.set(chunk,offset);offset+=chunk.length
  }
  if(offset!==bytes.length||await sha256(bytes)!==story.sourceHash)throw new ContractError('SOURCE_INTEGRITY_INVALID','原始文件 hash 不一致，未导出')
  active()
  return {name:source.value.name,bytes}
}
