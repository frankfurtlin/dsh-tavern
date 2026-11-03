import {withBase} from '../platform/base'
import {ContractError,sha256} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {LocalAsset} from './contract'
import {importAsset,inspectMedia} from './assets'
export interface OriginalVariant {role:string;url:string;width:number;height:number;bytes:number;sha256:string}
export interface OriginalAsset {id:string;name:string;purpose:'cover'|'stage-background'|'stage-portrait';expression:string;masterHash:string;variants:OriginalVariant[]}
export interface OriginalPack {schema:'original-pack-v1';version:1;assets:OriginalAsset[]}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const text=(v:unknown,n=200):v is string=>typeof v==='string'&&v.length<=n
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)
const positive=(v:unknown,n:number)=>Number.isSafeInteger(v)&&Number(v)>0&&Number(v)<=n
const ownPath=(v:unknown)=>typeof v==='string'&&/^\/assets\/original\/final-product\/[a-z0-9-]+\.webp$/.test(v)
function fail():never{throw new ContractError('ORIGINAL_PACK','原创素材清单暂时无法打开')}
export function parseOriginalPack(value:unknown):OriginalPack{
 if(!record(value)||value.schema!=='original-pack-v1'||value.version!==1||!Array.isArray(value.assets)||value.assets.length>512)fail()
 const known=new Set<string>()
 for(const a of value.assets){if(!record(a)||!text(a.id,160)||!a.id||known.has(a.id)||!text(a.name)||!a.name||!['cover','stage-background','stage-portrait'].includes(String(a.purpose))||!text(a.expression,40)||!hash(a.masterHash)||!Array.isArray(a.variants)||!a.variants.length||a.variants.length>12)fail();known.add(a.id)
  const roles=new Set<string>();for(const v of a.variants){if(!record(v)||!text(v.role,60)||!v.role||roles.has(v.role)||!ownPath(v.url)||!positive(v.width,20000)||!positive(v.height,20000)||!positive(v.bytes,25*1024*1024)||!hash(v.sha256))fail();roles.add(v.role)}
 }
 return {schema:'original-pack-v1',version:1,assets:value.assets.map(a=>({id:a.id,name:a.name,purpose:a.purpose,expression:a.expression,masterHash:a.masterHash,variants:a.variants.map((v:OriginalVariant)=>({role:v.role,url:v.url,width:v.width,height:v.height,bytes:v.bytes,sha256:v.sha256}))}))}
}
export async function loadOriginalPack(signal:AbortSignal,transport:typeof fetch=fetch){
 const response=await transport(withBase('/assets/original/final-product/manifest.json'),{signal,redirect:'error'});if(!response.ok)fail()
 const raw=await response.text();signal.throwIfAborted();if(raw.length>512*1024)fail()
 let value:unknown;try{value=JSON.parse(raw)}catch{fail()}return parseOriginalPack(value)
}
/** Explicit copy of one verified display variant; no model endpoint is used. */
export async function copyOriginalAsset(vault:LocalVault,asset:OriginalAsset,role:string,signal:AbortSignal,transport:typeof fetch=fetch){
 signal.throwIfAborted();const valid=parseOriginalPack({schema:'original-pack-v1',version:1,assets:[asset]}).assets[0]!,variant=valid.variants.find(v=>v.role===role);if(!variant)fail()
 const existing=(await vault.list<LocalAsset>('asset')).find(a=>a.value.sha256===variant.sha256&&a.value.bytes===variant.bytes)
 signal.throwIfAborted();if(existing){if(existing.value.width!==variant.width||existing.value.height!==variant.height)fail();return existing}
 const response=await transport(variant.url,{signal,redirect:'error'});if(!response.ok||response.redirected)fail()
 const declared=response.headers.get('content-length');if(declared&&Number(declared)!==variant.bytes)fail()
 const chunks:Uint8Array[]=[];let bytes=0
 if(!response.body)fail()
 const reader=response.body.getReader()
 try{while(true){signal.throwIfAborted();const part=await reader.read();if(part.done)break;bytes+=part.value.length;if(bytes>variant.bytes){await reader.cancel();fail()}chunks.push(part.value)}}finally{reader.releaseLock()}
 const buffer=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){buffer.set(chunk,offset);offset+=chunk.length}signal.throwIfAborted()
 if(buffer.length!==variant.bytes||await sha256(buffer)!==variant.sha256)throw new ContractError('ORIGINAL_ASSET_HASH','素材文件与清单不一致，未保存')
 if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定')
 const details=await inspectMedia(buffer,'image/webp');signal.throwIfAborted();if(details.mime!=='image/webp'||details.width!==variant.width||details.height!==variant.height)throw new ContractError('ORIGINAL_ASSET_DIMENSIONS','素材尺寸与清单不一致，未保存')
 const labels:Record<string,string>={neutral:'中性',joy:'喜悦',worry:'忧虑'}
 return importAsset(vault,new File([buffer],`预置 · ${valid.name}${valid.expression?' · '+(labels[valid.expression]||valid.expression):''} · ${role}.webp`,{type:'image/webp'}),[],signal)
}
