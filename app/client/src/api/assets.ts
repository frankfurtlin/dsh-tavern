import {api,ApiError,authGeneration,authorizationHeaders,refreshAuth} from './index'
import type {Anchor} from './types'
import {accountId,backendBase,cacheGet,cacheSet} from '../platform/bridge'
import {isBookDeleted} from '../platform/book-cache'
import {isWorldDeleted,isWorldLineDeleted} from '../platform/world-cache'
import {currentImagePlacements} from './image-changes'

export type ImagePurpose='cover'|'avatar'|'scene'|'illustration'
export type ImageScope={kind:'book';bookId:string;branchId:string;revisionId:string;anchor?:Anchor;identityId?:string}|{kind:'world';worldId:string;lineId:string;checkpointId:string;entityId?:string;identityId?:string}
export interface ImageVariant {width:number;height:number;mime:string;sha256:string;bytes:number;path:string}
export interface StoryImage {assetId:string;ownerId:string;sourceKind:string;licenseNote:string;sha256:string;mime:string;width:number;height:number;scope:ImageScope;createdAt:string;version:number;referenceCount:number;variants:Record<string,ImageVariant>}
export interface ImagePlacement {id:string;scope:ImageScope;purpose:ImagePurpose;contentId:string|null;paragraphId:string|null;side:'before'|'after';order:number;assetId:string;alt:string;version:number;asset:StoryImage}
export interface ImageCapabilities {protocol:'reader-assets-v1';ready:boolean;maxUploadBytes:number;maxEdge:number;maxPixels:number;mimeTypes:string[]}
export interface ImageCrop {x:number;y:number;width:number;height:number}
export interface OfflineImages {schema:1;placements:ImagePlacement[];bytes:Record<string,string>}
const id=/^[a-f0-9]{32}$/,hash=/^[a-f0-9]{64}$/
const canonical=(value:unknown):string=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v)
export function imageScopeKey(scope:ImageScope){return canonical(scope)}
export function sameImageRoot(a:ImageScope,b:ImageScope){return a.kind===b.kind&&(a.kind==='book'&&b.kind==='book'?a.bookId===b.bookId&&a.branchId===b.branchId&&a.revisionId===b.revisionId:a.kind==='world'&&b.kind==='world'?a.worldId===b.worldId&&a.lineId===b.lineId:false)}
export function imageScopeActive(scope:ImageScope){return scope.kind==='book'?!isBookDeleted(scope.bookId):!isWorldDeleted(scope.worldId)&&!isWorldLineDeleted(scope.worldId,scope.lineId)}
export function captureImageScope(scope:ImageScope,active:()=>boolean=()=>true){
  const epoch=authGeneration(),owner=accountId(),base=backendBase(),key=imageScopeKey(scope)
  return()=>!!owner&&epoch===authGeneration()&&owner===accountId()&&base===backendBase()&&imageScopeKey(scope)===key&&imageScopeActive(scope)&&active()
}
let capability:{identity:string;until:number;value:Promise<ImageCapabilities|null>}|undefined
export async function imageCapabilities(){
  if(!accountId())return null
  const identity=[backendBase(),accountId(),authGeneration()].join(':')
  if(capability?.identity===identity&&capability.until>Date.now())return capability.value
  const value=(async()=>{try{const result=await api<ImageCapabilities>('/assets/capabilities',{cache:true});return result.protocol==='reader-assets-v1'&&result.ready?result:null}catch{return null}})()
  capability={identity,until:Date.now()+5000,value};return value
}
export function validateImageScope(scope:ImageScope){
  const ids=scope.kind==='book'?[scope.bookId,scope.branchId,scope.revisionId]:[scope.worldId,scope.lineId,scope.checkpointId]
  if(ids.some(value=>!id.test(value))||scope.identityId&&!id.test(scope.identityId)||scope.kind==='book'&&scope.anchor&&(scope.anchor.bookId!==scope.bookId||scope.anchor.branchId!==scope.branchId||scope.anchor.revisionId!==scope.revisionId))throw new Error('图片范围不属于当前故事')
  return scope
}
export function validatePlacement(value:ImagePlacement,scope:ImageScope){
  if(!id.test(value.id)||!id.test(value.assetId)||value.asset.assetId!==value.assetId||value.asset.ownerId!==accountId()||!sameImageRoot(value.scope,scope)||!Number.isInteger(value.version)||value.version<1)throw new Error('图片位置与当前故事不一致')
  for(const variant of Object.values(value.asset.variants))if(!hash.test(variant.sha256)||!Number.isInteger(variant.width)||!Number.isInteger(variant.height)||variant.width<1||variant.height<1||!['image/png','image/webp'].includes(variant.mime)||!Number.isInteger(variant.bytes)||variant.bytes<1||variant.bytes>64*1024*1024)throw new Error('图片元数据无效')
  return value
}
export async function imagePlacements(scope:ImageScope,cache=true){
  validateImageScope(scope)
  const valid=captureImageScope(scope),reference=scope.kind==='world'?{kind:'world',worldId:scope.worldId,lineId:scope.lineId,headCheckpointId:scope.checkpointId}:scope
  const key=(scope.kind==='world'?'world-image-list:':'image-list:')+encodeURIComponent(imageScopeKey(scope))
  let value:{items:ImagePlacement[]}
  try{value=await api('/asset-placements?scope='+encodeURIComponent(imageScopeKey(scope)));if(valid()&&cache)await cacheSet(key,{...reference,scope,items:value.items})}
  catch(e){if(!cache||!(e instanceof ApiError)||e.code!=='NETWORK_UNAVAILABLE')throw e;const stored=await cacheGet<{items:ImagePlacement[]}>(key);if(!stored)throw e;value=stored}
  if(!valid())throw new ApiError('SESSION_CHANGED','故事或账号已变化',401)
  return currentImagePlacements(value.items.map(item=>validatePlacement(item,scope)),valid)
}
export const imageByteKey=(asset:StoryImage,name:string)=>asset.assetId+':'+name+':'+asset.variants[name]?.sha256
export function imageCacheKey(asset:StoryImage,name:string,scope:ImageScope){
  const root=scope.kind==='world'?'world-image:'+scope.worldId+':'+scope.lineId:'image:'+scope.bookId+':'+scope.branchId+':'+scope.revisionId
  return root+':'+imageByteKey(asset,name)+':'+encodeURIComponent(imageScopeKey(scope))
}
export async function bytesHash(bytes:ArrayBuffer){const value=await crypto.subtle.digest('SHA-256',bytes);return Array.from(new Uint8Array(value),byte=>byte.toString(16).padStart(2,'0')).join('')}
export async function dataUrlHash(value:string){
  const match=value.match(/^data:(image\/(?:png|webp));base64,([a-zA-Z0-9+/=]+)$/)
  if(!match)throw new Error('图片缓存无法读取')
  const bytes=Uint8Array.from(atob(match[2]!),char=>char.charCodeAt(0))
  return {sha256:await bytesHash(bytes.buffer),mime:match[1],bytes:bytes.byteLength}
}
export async function verifiedImageData(data:string,variant:ImageVariant){
  const actual=await dataUrlHash(data)
  if(actual.sha256!==variant.sha256||actual.mime!==variant.mime||actual.bytes!==variant.bytes)throw new Error('图片字节未通过完整性检查')
  return data
}
export async function imageSource(asset:StoryImage,name:string,scope:ImageScope,active:()=>boolean=()=>true,offline?:OfflineImages){
  const variant=asset.variants[name];if(!variant)return ''
  validateImageScope(scope)
  const valid=captureImageScope(scope,active),key=imageCacheKey(asset,name,scope)
  const check=()=>{if(!valid())throw new ApiError('SESSION_CHANGED','故事或账号已变化',401)}
  const fallback=async()=>{
    check();const entry=await cacheGet<{data:string;sha256:string}>(key);check()
    const data=entry?.sha256===variant.sha256?entry.data:offline?.bytes[imageByteKey(asset,name)]
    if(!data)return ''
    const result=await verifiedImageData(data,variant);check();return result
  }
  const endpoint=backendBase()+'/api/reader/v1/assets/'+asset.assetId+'/variants/'+encodeURIComponent(name)+'?scope='+encodeURIComponent(imageScopeKey(scope))
  let response:Response
  try{response=await fetch(endpoint,{headers:authorizationHeaders(),credentials:'include'});check()}
  catch(e){check();return fallback()}
  if(response.status===401){const refreshed=await refreshAuth();check();if(refreshed){response=await fetch(endpoint,{headers:authorizationHeaders(),credentials:'include'});check()}}
  if(!response.ok){if([401,403,404,410].includes(response.status)){await cacheSet(key,null);check();return ''}return fallback()}
  const blob=await response.blob();check()
  if(blob.type!==variant.mime||blob.size!==variant.bytes)throw new Error('图片类型或体积与回执不一致')
  const data=await blob.arrayBuffer();check()
  if(await bytesHash(data)!==variant.sha256)throw new Error('图片哈希校验失败');check()
  const url=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('图片未能保存'));reader.readAsDataURL(blob)});check()
  const reference=scope.kind==='world'?{kind:'world',worldId:scope.worldId,lineId:scope.lineId,headCheckpointId:scope.checkpointId}:scope
  await cacheSet(key,{...reference,scope,assetId:asset.assetId,variant:name,sha256:variant.sha256,data:url});check()
  return url
}
export const preferredVariant=(asset:StoryImage,purpose:ImagePurpose,large=false)=>purpose==='cover'?(large?'cover640':'cover320'):purpose==='avatar'?(large?'avatar512':'avatar256'):'reading'
export async function downloadImages(scope:ImageScope,active:()=>boolean){
  const capability=await imageCapabilities();if(!capability)return undefined
  const valid=captureImageScope(scope,active),items=await imagePlacements(scope,false);if(!valid())throw new Error('下载页面已变化')
  const bytes:Record<string,string>={}
  for(const item of items){for(const name of new Set([preferredVariant(item.asset,item.purpose),preferredVariant(item.asset,item.purpose,true)])){
    const data=await imageSource(item.asset,name,scope,valid)
    if(!valid()||!data)throw new Error('图片尚未完整下载，请重试')
    bytes[imageByteKey(item.asset,name)]=data
  }}
  const {publicImages}=await import('./offline-images');return publicImages({schema:1,placements:items,bytes})
}
export function imagesChanged(scope:ImageScope){window.dispatchEvent(new CustomEvent('reader-images-changed',{detail:{scope}}))}
