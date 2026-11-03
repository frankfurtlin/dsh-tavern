import {api,ApiError} from '../api'
import {worldApi} from '../world/api'
import type {WorldInfo,WorldLine,WorldCheckpoint} from '../world/types'
import {commitWorldPackage,publicWorldPayload,worldReadingScope} from './world-storage'
import {isWorldLineDeleted} from '../platform/world-cache'
import {downloadImages,type OfflineImages,type ImagePlacement} from '../api/assets'

export async function downloadWorldReading(worldId:string,lineId:string,headId:string,origin:string,active:()=>boolean=()=>true){
  const scope=worldReadingScope(),valid=()=>scope()&&active()&&!isWorldLineDeleted(worldId,lineId)
  const check=()=>{if(!valid())throw new Error('账号、世界线或下载页面已变化')}
  async function get<T>(path:string){check();const value=await worldApi<T>(path);check();return value}
  const root='/worlds/'+worldId,linePath=root+'/lines/'+lineId
  const world=await get<WorldInfo>(root),line=await get<WorldLine>(linePath)
  if(world.id!==worldId||line.worldId!==worldId||line.id!==lineId||line.headCheckpointId!==headId||line.head.id!==headId)throw new Error('正文版本发生变化，请重新打开后下载')
  const history=await get<{items:WorldCheckpoint[];headCheckpointId:string}>(linePath+'/history')
  if(history.headCheckpointId!==headId)throw new Error('正文版本发生变化，请重新打开后下载')
  // Project before any local write; source/definition/state/GM never enter the
  // explicit reading package, even if the server returns extra fields.
  publicWorldPayload(world,line,history.items,[])
  const origins:string[]=[]
  if(origin&&origin!=='world-'+worldId&&/^(book|world)-[a-f0-9]{32}$/.test(origin)){
    try{await api('/stories/'+origin+'/context/validate',{method:'POST',body:{storyId:origin,mode:'read',perspective:'reader',entryOrigin:'worlds',worldId,worldLineId:lineId,checkpointId:history.items.find(cp=>cp.text&&cp.kind!=='start')!.id}});check();origins.push(origin)}
    catch(e){if(!(e instanceof ApiError&&[400,403,404,409,422].includes(e.status)))throw e}
  }
  let images:OfflineImages|undefined
  const imageRows=new Map<string,ImagePlacement>(),imageBytes:Record<string,string>={}
  for(const cp of history.items){check();const pack=await downloadImages({kind:'world',worldId,lineId,checkpointId:cp.id},valid);check();if(pack){for(const row of pack.placements)imageRows.set(row.id,row);Object.assign(imageBytes,pack.bytes);images={schema:1,placements:[...imageRows.values()],bytes:imageBytes}}}
  const finalWorld=await get<WorldInfo>(root),finalLine=await get<WorldLine>(linePath)
  if(finalWorld.id!==worldId||finalLine.worldId!==worldId||finalLine.id!==lineId||finalLine.headCheckpointId!==headId||finalLine.head.id!==headId)throw new Error('正文版本发生变化，请重新打开后下载')
  check();return commitWorldPackage(publicWorldPayload(finalWorld,finalLine,history.items,origins,images),valid)
}
