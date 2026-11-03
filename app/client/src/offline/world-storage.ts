import {accountId,backendBase,cacheGet,cacheSet,cacheUpdate} from '../platform/bridge'
import {authGeneration} from '../api'
import {captureWorldCacheScope,isWorldDeleted,isWorldLineDeleted,resumeDeletedWorldCleanup} from '../platform/world-cache'
import {digest} from './storage'
import type {WorldInfo,WorldLine,WorldCheckpoint} from '../world/types'
import type {OfflineImages} from '../api/assets'
import {publicImages,validateOfflineImages} from '../api/offline-images'

export type PublicReadWorld=Pick<WorldInfo,'id'|'name'|'status'|'sourceRemoved'|'activeLineId'>
export type PublicReadLine=Pick<WorldLine,'id'|'worldId'|'name'|'status'|'version'|'headCheckpointId'|'readingPosition'>
export type PublicReadCheckpoint=Pick<WorldCheckpoint,'id'|'worldId'|'lineId'|'parentCheckpointId'|'sequence'|'title'|'text'|'kind'|'createdAt'>
export interface WorldReadPayload {world:PublicReadWorld;line:PublicReadLine;history:PublicReadCheckpoint[];origins:string[];images?:OfflineImages}
export interface OfflineWorldPackage {schema:1;kind:'world';id:string;ownerId:string;backend:string;downloadedAt:string;sha256:string;payload:WorldReadPayload}
export interface OfflineWorldEntry {id:string;kind:'world';worldId:string;lineId:string;headCheckpointId:string;title:string;lineName:string;chapterCount:number;downloadedAt:string;bytes:number}
export const worldIndexKey='world-offline-index:v1'
export const worldPackageId=(worldId:string,lineId:string,headId:string)=>[worldId,lineId,headId].join(':')
export const worldPackageKey=(id:string)=>'world-offline-package:v1:'+id
const idOK=(id:unknown)=>typeof id==='string'&&/^[a-zA-Z0-9_-]{1,128}$/.test(id)
const canon=(id:string)=>'world-'+id
export function worldReadingScope(){const scope=captureWorldCacheScope(),epoch=authGeneration();return()=>scope()&&epoch===authGeneration()}
function allowed(payload:WorldReadPayload){return !isWorldDeleted(payload.world.id)&&!isWorldLineDeleted(payload.world.id,payload.line.id)}
// Pick each stored field explicitly. Public API replies may grow new state,
// source, definition or director fields; none are copied into a reading pack.
export function publicWorldPayload(world:PublicReadWorld,line:PublicReadLine,history:PublicReadCheckpoint[],origins:string[],images?:OfflineImages):WorldReadPayload{
  if(!idOK(world.id)||!idOK(line.id)||line.worldId!==world.id||!idOK(line.headCheckpointId)||!history.length||[world.name,world.status,line.name,line.status].some(value=>typeof value!=='string')||!Number.isInteger(line.version))throw new Error('世界线版本不一致，下载未完成')
  const seen=new Set<string>()
  for(let i=0;i<history.length;i++){
    const cp=history[i]!
    if(!idOK(cp.id)||cp.worldId!==world.id||!idOK(cp.lineId)||seen.has(cp.id)||cp.parentCheckpointId!==(i?history[i-1]!.id:null)||[cp.text,cp.title,cp.kind,cp.createdAt].some(value=>typeof value!=='string')||!Number.isInteger(cp.sequence)||cp.sequence<0)throw new Error('公开历史不完整，下载未完成')
    seen.add(cp.id)
  }
  if(history.at(-1)!.id!==line.headCheckpointId||!history.some(cp=>cp.text&&cp.kind!=='start'))throw new Error('正文版本发生变化，请重新下载')
  if(images&&images.placements.some(row=>row.scope.kind!=='world'||row.scope.worldId!==world.id||row.scope.lineId!==line.id||!seen.has(row.scope.checkpointId)))throw new Error('图片时间点不属于这份公开历史')
  const position=line.readingPosition||{},checkpointId=typeof position.checkpointId==='string'&&seen.has(position.checkpointId)?position.checkpointId:undefined
  return {
    world:{id:world.id,name:world.name,status:world.status,sourceRemoved:!!world.sourceRemoved,activeLineId:line.id},
    line:{id:line.id,worldId:world.id,name:line.name,status:line.status,version:line.version,headCheckpointId:line.headCheckpointId,readingPosition:checkpointId?{checkpointId,scroll:Number.isFinite(position.scroll)?Math.max(0,position.scroll!):0,...(typeof position.updatedAt==='string'?{updatedAt:position.updatedAt}:{})}:{}},
    history:history.map(cp=>({id:cp.id,worldId:cp.worldId,lineId:cp.lineId,parentCheckpointId:cp.parentCheckpointId,sequence:cp.sequence,title:cp.title,text:cp.text,kind:cp.kind,createdAt:cp.createdAt})),
    origins:[...new Set([canon(world.id),...origins.filter(origin=>/^(book|world)-[a-f0-9]{32}$/.test(origin))])],...(images?{images:publicImages(images)}:{})
  }
}
export async function worldEntries():Promise<OfflineWorldEntry[]>{
  const valid=worldReadingScope();await resumeDeletedWorldCleanup();if(!valid())return []
  const value=await cacheGet<OfflineWorldEntry[]>(worldIndexKey);return valid()&&Array.isArray(value)?value:[]
}
export async function readWorldPackage(id:string):Promise<OfflineWorldPackage|undefined>{
  const valid=worldReadingScope(),value=await cacheGet<OfflineWorldPackage>(worldPackageKey(id))
  try{
    if(!value||!valid()||value.schema!==1||value.kind!=='world'||value.id!==id||value.ownerId!==accountId()||value.backend!==backendBase()||!allowed(value.payload))return
    if(worldPackageId(value.payload.world.id,value.payload.line.id,value.payload.line.headCheckpointId)!==id||await digest(value.payload)!==value.sha256||!valid())return
    if(JSON.stringify(publicWorldPayload(value.payload.world,value.payload.line,value.payload.history,value.payload.origins,value.payload.images))!==JSON.stringify(value.payload))return
    if(value.payload.images){await validateOfflineImages(value.payload.images,{kind:'world',worldId:value.payload.world.id,lineId:value.payload.line.id,checkpointId:value.payload.line.headCheckpointId});if(!valid())return}
    return value
  }catch{return}
}
export async function downloadedWorld(worldId:string,lineId?:string){
  const valid=worldReadingScope()
  for(const entry of (await worldEntries()).slice().reverse()){
    if(!valid())return
    if(entry.worldId!==worldId||lineId&&entry.lineId!==lineId)continue
    const stored=await readWorldPackage(entry.id);if(stored&&valid())return stored
  }
}
export async function commitWorldPackage(data:WorldReadPayload,active:()=>boolean){
  const scope=worldReadingScope(),valid=()=>scope()&&active()&&allowed(data),payload=publicWorldPayload(data.world,data.line,data.history,data.origins,data.images)
  const id=worldPackageId(payload.world.id,payload.line.id,payload.line.headCheckpointId),downloadedAt=new Date().toISOString()
  const value:OfflineWorldPackage={schema:1,kind:'world',id,ownerId:accountId(),backend:backendBase(),downloadedAt,sha256:await digest(payload),payload}
  if(!valid())throw new Error('账号、世界线或下载页面已变化')
  await cacheSet(worldPackageKey(id),value)
  const verified=await readWorldPackage(id);if(!verified||!valid())throw new Error('下载未能完整保存，请重试')
  const entry:OfflineWorldEntry={id,kind:'world',worldId:payload.world.id,lineId:payload.line.id,headCheckpointId:payload.line.headCheckpointId,title:payload.world.name,lineName:payload.line.name,chapterCount:payload.history.filter(cp=>cp.text&&cp.kind!=='start').length,downloadedAt,bytes:new TextEncoder().encode(JSON.stringify(value)).length}
  const result=await cacheUpdate<OfflineWorldEntry[]>(worldIndexKey,items=>[...(items||[]).filter(item=>item.id!==id),entry],valid)
  if(!result||!valid())throw new Error('下载未完成，本机登记未保存')
  return entry
}
export async function removeWorldPackage(id:string,active:()=>boolean=()=>true){
  const scope=worldReadingScope(),valid=()=>scope()&&active();const result=await cacheUpdate<OfflineWorldEntry[]>(worldIndexKey,items=>(items||[]).filter(item=>item.id!==id),valid)
  if(!result||!valid())throw new Error('账号或服务器已变化');await cacheSet(worldPackageKey(id),null)
}
// Only WorldRead opts into this public-only fallback. Play/task recovery must
// never mistake a reading snapshot for a complete actionable world state.
export async function offlineWorldRead<T>(path:string):Promise<T|undefined>{
  const valid=worldReadingScope(),parts=new URL(path,'https://world-offline.invalid').pathname.split('/').filter(Boolean)
  if(parts[0]!=='worlds'||!parts[1]||parts.length>5)return
  const stored=await downloadedWorld(parts[1],parts[2]==='lines'?parts[3]:undefined);if(!stored||!valid())return
  const p=stored.payload
  if(parts.length===2)return {...p.world,offlineSnapshot:true,offlinePackageId:stored.id} as T
  if(parts[2]!=='lines'||parts[3]!==p.line.id)return
  if(parts.length===4)return {...structuredClone(p.line),offlineSnapshot:true,offlinePackageId:stored.id} as T
  if(parts[4]==='history')return {items:structuredClone(p.history),headCheckpointId:p.line.headCheckpointId,offlineSnapshot:true,offlinePackageId:stored.id} as T
}
