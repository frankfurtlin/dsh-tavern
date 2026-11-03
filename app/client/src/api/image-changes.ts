import {cacheGet,cacheUpdate} from '../platform/bridge'
import type {ImageScope,ImagePlacement} from './assets'
interface Change {scope:ImageScope;placementId:string;minVersion:number;deleted:boolean}
const key='image-placement-changes:v1'
export async function currentImagePlacements(rows:ImagePlacement[],active:()=>boolean){
  const changes=await cacheGet<Change[]>(key);if(!active())throw new Error('故事或账号已变化')
  return rows.filter(row=>!(changes||[]).some(change=>change.placementId===row.id&&(change.deleted||row.version<change.minVersion)))
}
// Confirmed metadata mutations expire just the old image in downloaded stories.
// Never replay an upload or modify prose, notes, progress, a revision or a head.
export async function recordImageChange(scope:ImageScope,placementId:string,version:number,deleted:boolean,active:()=>boolean){
  if(!active())throw new Error('故事或账号已变化')
  const result=await cacheUpdate<Change[]>(key,previous=>{
    const old=(previous||[]).find(change=>change.placementId===placementId)
    return [...(previous||[]).filter(change=>change.placementId!==placementId),{scope,placementId,minVersion:Math.max(old?.minVersion||0,version),deleted:!!old?.deleted||deleted}]
  },active)
  if(!result||!active())throw new Error('图片已保存，本机图片记录尚未更新')
  const clean=(images:import('./assets').OfflineImages)=>{
    const remaining=images.placements.filter(row=>row.id!==placementId||!deleted&&row.version>=version)
    return {schema:1 as const,placements:remaining,bytes:images.bytes}
  }
  const {publicImages}=await import('./offline-images');if(!active())throw new Error('故事或账号已变化')
  if(scope.kind==='book'){
    const {entries,readPackage,commitPackage}=await import('../offline/storage')
    for(const entry of await entries()){
      if(!active())throw new Error('故事或账号已变化')
      if(entry.bookId!==scope.bookId||entry.branchId!==scope.branchId||entry.revisionId!==scope.revisionId)continue
      const value=await readPackage(entry.id);if(!active())throw new Error('故事或账号已变化')
      if(value?.payload.images?.placements.some(row=>row.id===placementId))await commitPackage({...value.payload,images:publicImages(clean(value.payload.images))},active)
    }
  }else{
    const {worldEntries,readWorldPackage,commitWorldPackage}=await import('../offline/world-storage')
    for(const entry of await worldEntries()){
      if(!active())throw new Error('故事或账号已变化')
      if(entry.worldId!==scope.worldId||entry.lineId!==scope.lineId)continue
      const value=await readWorldPackage(entry.id);if(!active())throw new Error('故事或账号已变化')
      if(value?.payload.images?.placements.some(row=>row.id===placementId))await commitWorldPackage({...value.payload,images:publicImages(clean(value.payload.images))},active)
    }
  }
}
