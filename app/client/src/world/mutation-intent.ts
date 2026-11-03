import {newKey} from '../api'
import {worldApi} from './api'

export type WorldMutationIntent={key:string;path:string;body:unknown;receipt?:{worldId?:string;checkpointId?:string}}
type Intent=WorldMutationIntent
const storageKey=(slot:string)=>'world-mutation:'+slot
export async function pendingWorldMutation(slot:string):Promise<Intent|undefined>{
  const {cacheGet}=await import('../platform/bridge')
  return await cacheGet<Intent|null>(storageKey(slot))||undefined
}
// Persist the entire intent before a request can reach the server. An uncertain
// response must keep its key and exact payload, including across a page reload.
export async function worldMutation<T>(slot:string,path:string,body:unknown,valid:()=>boolean,onIntent?:(intent:WorldMutationIntent)=>void):Promise<T>{
  const {cacheUpdate}=await import('../platform/bridge')
  const snapshot=JSON.parse(JSON.stringify(body))
  const intent=await cacheUpdate<Intent|null>(storageKey(slot),saved=>saved||{key:newKey(),path,body:snapshot},valid)
  if(!intent||!valid())throw new Error('账号或页面已变化，请重新打开。')
  if(intent.path!==path||JSON.stringify(intent.body)!==JSON.stringify(snapshot))throw new Error('上次操作的响应尚未确认，请先恢复原来的操作。')
  onIntent?.(intent)
  if(Object.hasOwn(intent,'receipt'))return intent.receipt as T
  const result=await worldApi<T>(intent.path,{method:'POST',key:intent.key,body:intent.body})
  await cacheUpdate<Intent|null>(storageKey(slot),saved=>saved?.key===intent.key?{...saved,receipt:result as Intent['receipt']}:saved||null,valid)
  return result
}
export async function clearWorldMutation(slot:string,valid:()=>boolean,worldId?:string,expectedKey?:string){
  const {cacheUpdate}=await import('../platform/bridge')
  await cacheUpdate<Intent|null>(storageKey(slot),saved=>expectedKey&&saved?.key!==expectedKey||worldId&&saved?.receipt?.worldId!==worldId?saved||null:null,valid)
}
// Earlier clients could retain a successful receipt after navigation. A new
// explicit command may retire that receipt; an uncertain intent stays intact.
export async function clearConfirmedWorldMutation(slot:string,valid:()=>boolean){
  const {cacheUpdate}=await import('../platform/bridge')
  await cacheUpdate<Intent|null>(storageKey(slot),saved=>saved&&Object.hasOwn(saved,'receipt')?null:saved||null,valid)
}
