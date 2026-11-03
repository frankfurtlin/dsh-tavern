import {ContractError,sha256,type Document} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {archivePath,crc32,inflateBounded,readBoundedZip,writeStoredZip} from '../repositories/bounded-zip'
import {readCard,writeCard,type CharacterCard} from './card-format'
const limit=100*1024*1024,jsonLimit=8*1024*1024,utf8=new TextDecoder('utf-8',{fatal:true}),encoder=new TextEncoder()
const signature=new Uint8Array([137,80,78,71,13,10,26,10])
function bad(message:string):never{throw new ContractError('CARD_CONTAINER_INVALID',message+'；原文件保留')}
export interface CardContainer {format:'png'|'charx';sourceId:string;sha256:string}
export interface CardImport {card:CharacterCard;format:'json'|'png'|'charx';entries:Map<string,Uint8Array>;warnings:string[];avatar:Uint8Array|null;avatarMime:string|null}
function base64(bytes:Uint8Array){let value='';for(let at=0;at<bytes.length;at+=8192)value+=String.fromCharCode(...bytes.subarray(at,at+8192));return btoa(value)}
function fromBase64(value:string,max=jsonLimit){if(value.length>Math.ceil(max/3)*4+4)bad('嵌入数据超限');let result:Uint8Array;try{result=Uint8Array.from(atob(value),c=>c.charCodeAt(0))}catch{bad('嵌入数据不是有效 Base64')}if(result.length>max)bad('嵌入数据超限');return result}
function json(bytes:Uint8Array){if(bytes.length>jsonLimit)bad('角色卡 JSON 超过 8 MiB');try{return utf8.decode(bytes)}catch{bad('角色卡不是 UTF-8')}}
interface PngChunk {type:string;data:Uint8Array;raw:Uint8Array}
function pngChunks(bytes:Uint8Array):PngChunk[]{
  if(bytes.length<57||bytes.length>limit||signature.some((value,index)=>value!==bytes[index]))bad('PNG 文件头无效')
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),chunks:PngChunk[]=[];let at=8,ended=false,image=false
  while(at<bytes.length){if(at+12>bytes.length)bad('PNG 片段不完整');const size=view.getUint32(at);if(at+12+size>bytes.length)bad('PNG 片段越界');const type=String.fromCharCode(...bytes.subarray(at+4,at+8));if(!/^[A-Za-z]{4}$/.test(type))bad('PNG 片段类型无效');const data=bytes.slice(at+8,at+8+size);if(crc32(bytes.subarray(at+4,at+8+size))!==view.getUint32(at+8+size))bad('PNG 完整性校验失败：'+type)
    if(!chunks.length){if(type!=='IHDR'||size!==13)bad('PNG 缺少 IHDR');const head=new DataView(data.buffer),width=head.getUint32(0),height=head.getUint32(4);if(!width||!height||width>16384||height>16384||width*height>40_000_000)bad('PNG 图片尺寸超限')}
    else if(type==='IHDR')bad('PNG 重复 IHDR')
    if(type==='IDAT')image=true
    chunks.push({type,data,raw:bytes.slice(at,at+12+size)});at+=12+size
    if(type==='IEND'){if(size||at!==bytes.length)bad('PNG 结束片段无效');ended=true;break}
  }
  if(!ended||!image)bad('PNG 不完整');return chunks
}
function imageMime(bytes:Uint8Array):string|null{
  if(bytes.length>=8&&signature.every((value,index)=>value===bytes[index]))return 'image/png'
  if(bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return 'image/jpeg'
  if(bytes.length>=12&&String.fromCharCode(...bytes.subarray(0,4))==='RIFF'&&String.fromCharCode(...bytes.subarray(8,12))==='WEBP')return 'image/webp'
  if(bytes.length>=6&&/^GIF8[79]a$/.test(String.fromCharCode(...bytes.subarray(0,6))))return 'image/gif'
  return null
}
function chooseAsset(card:CharacterCard,entries:Map<string,Uint8Array>,fallback:Uint8Array|null,warnings:string[]){
  const assets=Array.isArray(card.data.assets)?card.data.assets as Record<string,unknown>[]:[],icons=assets.filter(row=>row&&row.type==='icon'),icon=icons.find(row=>row.name==='main')||icons[0]
  for(const row of assets){if(typeof row?.uri!=='string')continue;if(row.uri.startsWith('embeded://')||row.uri.startsWith('__asset:')){const path=archivePath(row.uri.replace(/^(?:embeded:\/\/|__asset:)/,''),true);if(!entries.has(path))warnings.push('缺少资源：'+path)}else if(/^https?:\/\//i.test(row.uri))warnings.push('远程资源未加载：'+String(row.name||row.type||'资源'))}
  if(!icon||icon.uri==='ccdefault:')return {avatar:fallback,avatarMime:fallback?'image/png':null}
  const uri=typeof icon.uri==='string'?icon.uri:'';let bytes:Uint8Array|null=null
  if(uri.startsWith('embeded://')||uri.startsWith('__asset:'))bytes=entries.get(archivePath(uri.replace(/^(?:embeded:\/\/|__asset:)/,''),true))||null
  else if(/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(uri))bytes=fromBase64(uri.split(',')[1]!,20*1024*1024)
  const mime=bytes?imageMime(bytes):null;if(bytes&&!mime)warnings.push('头像格式不匹配，已保留资源');return {avatar:mime?bytes:null,avatarMime:mime}
}
export async function parseCardContainer(bytes:Uint8Array,format:'json'|'png'|'charx',signal:AbortSignal):Promise<CardImport>{
  signal.throwIfAborted();if(bytes.length>limit)bad('角色包超过 100 MiB');const entries=new Map<string,Uint8Array>(),warnings:string[]=[];let card:CharacterCard,fallback:Uint8Array|null=null
  if(format==='json')card=readCard(json(bytes))
  else if(format==='charx'){const files=await readBoundedZip(bytes,signal);for(const [path,data] of files)entries.set(path,data);const raw=entries.get('card.json');if(!raw)bad('CHARX 缺少根目录 card.json');card=readCard(json(raw));if(card.format!=='v3')warnings.push('此 CHARX 使用旧版角色卡格式，已保留')}
  else{
    const chunks=pngChunks(bytes),texts=new Map<string,string>();let textBytes=0
    for(const chunk of chunks){signal.throwIfAborted();if(!['tEXt','zTXt'].includes(chunk.type))continue;const zero=chunk.data.indexOf(0);if(zero<1||zero>79)bad('PNG 文本关键字无效');const keyword=String.fromCharCode(...chunk.data.subarray(0,zero));if(!['ccv3','chara'].includes(keyword)&&!keyword.startsWith('chara-ext-asset_:'))continue;if(texts.has(keyword))bad('PNG 存在重复角色数据：'+keyword)
      let payload=chunk.data.slice(zero+1);if(chunk.type==='zTXt'){if(payload[0]!==0)bad('PNG 文本压缩方式无效');payload=await inflateBounded(payload.slice(1),'deflate',Math.min(Math.ceil(limit*4/3)-textBytes,keyword.startsWith('chara-ext-asset_:')?Math.ceil(limit*4/3):Math.ceil(jsonLimit*4/3)),signal)}textBytes+=payload.length;if(textBytes>Math.ceil(limit*4/3))bad('PNG 文本总容量超限');let text='';for(let at=0;at<payload.length;at+=8192)text+=String.fromCharCode(...payload.subarray(at,at+8192));texts.set(keyword,text)
    }
    const key=texts.has('ccv3')?'ccv3':texts.has('chara')?'chara':null;if(!key)bad('PNG 没有角色卡数据');card=readCard(json(fromBase64(texts.get(key)!)));if(key==='ccv3'&&card.format!=='v3')bad('ccv3 片段不是 V3 卡')
    let assetBytes=0;for(const [key,text] of texts)if(key.startsWith('chara-ext-asset_:')){const path=archivePath(key.slice('chara-ext-asset_:'.length)),data=fromBase64(text,limit);assetBytes+=data.length;if(assetBytes>limit)bad('PNG 资源总容量超限');entries.set(path,data)}fallback=bytes
  }
  const avatar=chooseAsset(card,entries,fallback,warnings);signal.throwIfAborted();return {card,format,entries,warnings,...avatar}
}
export async function readContainerSource(vault:LocalVault,container:CardContainer,signal:AbortSignal){
  const active=()=>{signal.throwIfAborted();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，未返回角色包内容')};active()
  signal.throwIfAborted();const source=await vault.read<{chunkIds:string[];bytes:number;sha256:string}>(container.sourceId)
  if(!source||source.kind!=='source'||source.value.sha256!==container.sha256)bad('原角色包来源不一致');const output=new Uint8Array(source.value.bytes);let at=0
  for(const id of source.value.chunkIds){signal.throwIfAborted();const chunk=await vault.read<{data:string;bytes:number;sha256:string}>(id);if(!chunk||chunk.kind!=='sourceChunk')bad('原角色包片段缺失');const bytes=fromBase64(chunk.value.data,1024*1024);if(bytes.length!==chunk.value.bytes||await sha256(bytes)!==chunk.value.sha256||at+bytes.length>output.length)bad('原角色包片段损坏');output.set(bytes,at);at+=bytes.length}
  if(at!==output.length||await sha256(output)!==container.sha256)bad('原角色包完整性校验失败');active();return output
}
export async function importCardFile(vault:LocalVault,file:File,signal:AbortSignal):Promise<{id:string;warnings:string[]}>{
  const format=file.name.toLowerCase().endsWith('.charx')?'charx':file.name.toLowerCase().endsWith('.png')?'png':file.name.toLowerCase().endsWith('.json')?'json':null;if(!format)bad('请选择 JSON、PNG 或 CHARX');if(file.size>limit)bad('角色包超过 100 MiB')
  const bytes=new Uint8Array(await file.arrayBuffer()),parsed=await parseCardContainer(bytes,format,signal),id=crypto.randomUUID(),writes:WriteDocument[]=[]
  if(format!=='json'){
    const hash=await sha256(bytes),sourceId='card-source-'+hash.slice(0,32),existing=await vault.read<{sha256:string;bytes:number}>(sourceId)
    if(existing&&(existing.kind!=='source'||existing.value.sha256!==hash||existing.value.bytes!==bytes.length))bad('已有角色包来源冲突')
    if(!existing){const chunks:WriteDocument[]=[];for(let at=0;at<bytes.length;at+=1024*1024){signal.throwIfAborted();const data=bytes.slice(at,at+1024*1024);chunks.push({kind:'sourceChunk',id:sourceId+':'+chunks.length,expectedRevision:0,value:{data:base64(data),bytes:data.length,sha256:await sha256(data)}})}writes.push({kind:'source',id:sourceId,expectedRevision:0,value:{name:file.name,bytes:bytes.length,sha256:hash,chunkIds:chunks.map(row=>row.id)}},...chunks)}
    parsed.card.container={format,sourceId,sha256:hash}
  }
  signal.throwIfAborted();await vault.commitBundle('import-card:'+id,[{kind:'card',id,expectedRevision:0,value:parsed.card},...writes],signal);return {id,warnings:parsed.warnings}
}
function pngText(keyword:string,value:string){const label=encoder.encode(keyword+'\0'+value),output=new Uint8Array(12+label.length),v=new DataView(output.buffer);v.setUint32(0,label.length);output.set(encoder.encode('tEXt'),4);output.set(label,8);v.setUint32(8+label.length,crc32(output.subarray(4,8+label.length)));return output}
export async function exportCardFile(vault:LocalVault,card:CharacterCard,format:'json'|'png'|'charx',signal:AbortSignal):Promise<Uint8Array>{
  const active=()=>{signal.throwIfAborted();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，未返回导出文件')};active()
  const jsonBytes=encoder.encode(writeCard(card));active();if(format==='json')return jsonBytes
  if(!card.container)bad('此卡没有原 PNG 或 CHARX 容器，请导出 JSON')
  const source=await readContainerSource(vault,card.container,signal)
  if(format!==card.container.format)bad('请选择原角色包格式或 JSON')
  if(format==='charx'){const files=await readBoundedZip(source,signal);active();files.set('card.json',jsonBytes);const output=writeStoredZip(files);active();return output}
  const chunks=pngChunks(source),pieces:Uint8Array[]=[signature];for(const chunk of chunks){const zero=chunk.data.indexOf(0),keyword=zero>0?String.fromCharCode(...chunk.data.subarray(0,zero)):'';if(['tEXt','zTXt','iTXt'].includes(chunk.type)&&['chara','ccv3'].includes(keyword))continue;if(chunk.type==='IEND')pieces.push(pngText(card.format==='v3'?'ccv3':'chara',base64(jsonBytes)));pieces.push(chunk.raw)}
  const length=pieces.reduce((sum,part)=>sum+part.length,0);if(length>limit)bad('导出 PNG 超过容量限额');const output=new Uint8Array(length);let at=0;for(const part of pieces){output.set(part,at);at+=part.length}active();return output
}
export async function cardAvatar(vault:LocalVault,row:Document<CharacterCard>,signal:AbortSignal){if(!row.value.container)return null;const source=await readContainerSource(vault,row.value.container,signal),parsed=await parseCardContainer(source,row.value.container.format,signal),chosen=chooseAsset(row.value,parsed.entries,row.value.container.format==='png'?source:null,[]);signal.throwIfAborted();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，未返回头像');return chosen.avatar&&chosen.avatarMime?URL.createObjectURL(new Blob([Uint8Array.from(chosen.avatar)],{type:chosen.avatarMime})):null}
