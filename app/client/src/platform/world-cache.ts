import {accountId,backendBase,cacheGeneration,purgeWorldCache} from './bridge'

interface WorldReference {worldId:string;lineId?:string}
interface WorldTombstone extends WorldReference {deleted:true;cleanupPending:boolean}
const prefix='reader-world-deleted:v1:',referencesPrefix='reader-world-reference:v1:'
const volatile=new Map<string,WorldTombstone>(),pending=new Map<string,Promise<void>>()
function identity(){return encodeURIComponent(backendBase())+':'+encodeURIComponent(accountId())+':'}
function key(worldId:string,lineId=''){return prefix+identity()+encodeURIComponent(worldId)+':'+encodeURIComponent(lineId)}
function parse<T>(name:string):T|undefined{try{return JSON.parse(localStorage.getItem(name)||'null')||undefined}catch{return undefined}}
function receipt(worldId:string,lineId=''){if(!accountId())return;const name=key(worldId,lineId);return parse<WorldTombstone>(name)||volatile.get(name)}
export const isWorldDeleted=(worldId:string)=>!!receipt(worldId)
export const isWorldLineDeleted=(worldId:string,lineId:string)=>isWorldDeleted(worldId)||!!receipt(worldId,lineId)
export class WorldDeletedError extends Error {
  readonly status=404
  readonly code:string
  constructor(line=false){super(line?'这条世界线已删除':'这个世界已删除');this.code=line?'WORLD_LINE_DELETED':'WORLD_DELETED'}
}
export function captureWorldCacheScope(){const owner=accountId(),base=backendBase(),epoch=cacheGeneration();return()=>!!owner&&owner===accountId()&&base===backendBase()&&epoch===cacheGeneration()}
function save(value:WorldTombstone){const name=key(value.worldId,value.lineId);volatile.set(name,value);localStorage.setItem(name,JSON.stringify(value))}
export function markWorldDeleted(worldId:string,lineId?:string){
  if(!accountId()||!worldId)throw new Error('账号已变化，请重新打开内容')
  const value:WorldTombstone={worldId,...(lineId?{lineId}:{}),deleted:true,cleanupPending:true}
  const previous=receipt(worldId,lineId);save(value);if(!previous)window.dispatchEvent(new CustomEvent('reader-world-deleted',{detail:{worldId,lineId}}));return value
}
function deleted(ref:WorldReference){return isWorldDeleted(ref.worldId)||!!ref.lineId&&isWorldLineDeleted(ref.worldId,ref.lineId)}
const url=(path:string)=>new URL(path,'https://world-cache.invalid')
function requestReference(path:string):WorldReference|undefined{
  const parts=url(path).pathname.split('/').filter(Boolean)
  if(parts[0]==='worlds'&&parts[1])return {worldId:parts[1],...(parts[2]==='lines'&&parts[3]?{lineId:parts[3]}:{})}
  if(parts[0]==='tasks'&&parts[1])return parse<WorldReference>(referencesPrefix+identity()+parts[1])
}
function valueReference(value:unknown):WorldReference|undefined{
  if(!value||typeof value!=='object'||Array.isArray(value))return
  const item=value as Record<string,any>
  if(typeof item.worldId==='string')return {worldId:item.worldId,...(typeof item.headCheckpointId==='string'&&typeof (item.lineId||item.id)==='string'?{lineId:item.lineId||item.id}:{})}
  if(typeof item.id==='string'&&'activeLineId'in item)return {worldId:item.id}
  if(typeof item.result?.worldId==='string')return {worldId:item.result.worldId,...(typeof item.result.lineId==='string'?{lineId:item.result.lineId}:{})}
}
export function recordWorldReferences(path:string,value:unknown){
  if(!accountId()||!value||typeof value!=='object')return
  const item=value as Record<string,any>,base=requestReference(path)||valueReference(value),ref=base&&{...base,...(typeof item.taskId==='string'&&typeof item.lineId==='string'?{lineId:item.lineId}:{})}
  if(!ref)return
  for(const id of [item.taskId,url(path).pathname.startsWith('/tasks/')?item.id:undefined])if(typeof id==='string')localStorage.setItem(referencesPrefix+identity()+id,JSON.stringify(ref))
}
export function worldRequestDeleted(path:string,method='GET',result?:unknown):WorldReference|undefined{
  // A committed world cleanup remains retryable. These requests never restore
  // a snapshot or start a model call.
  if(method==='DELETE'&&/^\/worlds\/[^/]+(?:\/lines\/[^/]+)?$/.test(url(path).pathname))return
  const ref=requestReference(path)
  if(ref&&deleted(ref))return ref
  const value=valueReference(result);if(value&&deleted(value))return value
}
export function worldCacheReference(name:string,value?:unknown):WorldReference|undefined{
  if(name.startsWith('world-api:'))return requestReference(name.slice(10))||valueReference(value)
  const match=name.match(/^(?:world-offline-package:v1|world-reading|world-input|world-pending):([^:]+):([^:]+)/)
  if(match)return {worldId:match[1],lineId:match[2]}
  return valueReference(value)
}
export function filterDeletedWorldCache(name:string,value:unknown):unknown{
  if(value===null||value===undefined||!name.startsWith('world-')||name.startsWith('world-line-deleted:'))return value
  const ref=worldCacheReference(name,value);if(ref&&deleted(ref))return undefined
  if(ref&&value&&typeof value==='object'&&Array.isArray((value as any).lines)&&Array.isArray((value as any).nodes)){
    const graph=value as {lines:{id:string;headCheckpointId:string}[];nodes:{id:string;parentCheckpointId:string|null}[]}
    const lines=graph.lines.filter(line=>!isWorldLineDeleted(ref.worldId,line.id))
    if(lines.length!==graph.lines.length){
      const nodes=new Map(graph.nodes.map(cp=>[cp.id,cp])),reachable=new Set<string>()
      for(const line of lines){let id:string|null=line.headCheckpointId;while(id&&!reachable.has(id)){const cp=nodes.get(id);if(!cp)break;reachable.add(id);id=cp.parentCheckpointId}}
      return {...value,lines,nodes:graph.nodes.filter(cp=>reachable.has(cp.id))}
    }
  }
  if(Array.isArray(value))return value.filter(item=>{const ref=valueReference(item);return !ref||!deleted(ref)})
  if(value&&typeof value==='object'&&Array.isArray((value as any).items))return {...value,items:(value as any).items.filter((item:unknown)=>{const ref=valueReference(item);return !ref||!deleted(ref)})}
  return value
}
export function assertWorldCacheWrite(name:string,value:unknown){if(value!==null&&filterDeletedWorldCache(name,value)===undefined){const ref=worldCacheReference(name,value);throw new WorldDeletedError(!!ref?.lineId&&!isWorldDeleted(ref.worldId))}}
export function shouldPurgeWorldCache(name:string,value:unknown,worldId:string,lineId?:string){
  if(!name.startsWith('world-')||name.startsWith('world-line-deleted:'))return false
  const ref=worldCacheReference(name,value)
  if(ref?.worldId!==worldId)return false
  if(!lineId)return true
  if(ref.lineId===lineId)return true
  // Shared navigation metadata may still point to the deleted entry. Shared
  // checkpoints themselves remain readable from retained descendant lines.
  return name==='world-api:/worlds/'+worldId||name==='world-api:/worlds/'+worldId+'/graph'
}
export function invalidateWorldCache(worldId:string,lineId?:string):Promise<void>{
  const valid=captureWorldCacheScope(),value=markWorldDeleted(worldId,lineId),name=key(worldId,lineId)
  if(pending.has(name))return pending.get(name)!
  const work=(async()=>{await purgeWorldCache(worldId,lineId,valid);if(!valid())throw new Error('账号或服务器已变化');save({...value,cleanupPending:false})})().finally(()=>pending.delete(name))
  pending.set(name,work);return work
}
export async function resumeDeletedWorldCleanup(){
  const valid=captureWorldCacheScope(),start=prefix+identity(),receipts:WorldTombstone[]=[]
  for(let i=0;i<localStorage.length;i++){const name=localStorage.key(i);if(name?.startsWith(start)){const value=parse<WorldTombstone>(name);if(value?.cleanupPending)receipts.push(value)}}
  for(const value of receipts){if(!valid())return;try{await invalidateWorldCache(value.worldId,value.lineId)}catch{/* The durable tombstone still rejects every late snapshot. */}}
}
if(typeof window!=='undefined')window.addEventListener('storage',event=>{const name=(event as StorageEvent).key;if(name?.startsWith(prefix+identity())){const value=parse<WorldTombstone>(name);if(value?.deleted)window.dispatchEvent(new CustomEvent('reader-world-deleted',{detail:{worldId:value.worldId,lineId:value.lineId}}))}})
