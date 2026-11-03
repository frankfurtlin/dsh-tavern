import type {ImageScope,ImagePlacement,OfflineImages,StoryImage} from './assets'
import {validatePlacement,verifiedImageData,imageScopeKey,preferredVariant,imageByteKey} from './assets'
const fields=(scope:ImageScope):ImageScope=>scope.kind==='book'?{kind:'book',bookId:scope.bookId,branchId:scope.branchId,revisionId:scope.revisionId,...(scope.identityId?{identityId:scope.identityId}:{}),...(scope.anchor?{anchor:{bookId:scope.anchor.bookId,branchId:scope.anchor.branchId,revisionId:scope.anchor.revisionId,chapterId:scope.anchor.chapterId,blockId:scope.anchor.blockId,start:scope.anchor.start,end:scope.anchor.end,offsetUnit:'utf16',quoteHash:scope.anchor.quoteHash}}:{})}:{kind:'world',worldId:scope.worldId,lineId:scope.lineId,checkpointId:scope.checkpointId,...(scope.entityId?{entityId:scope.entityId}:{}),...(scope.identityId?{identityId:scope.identityId}:{})}
export function publicImages(value:OfflineImages):OfflineImages{
  if(value.schema!==1||!Array.isArray(value.placements)||!value.bytes||typeof value.bytes!=='object')throw new Error('图片包不完整')
  const references=new Set<string>(),placements=value.placements.map(row=>{
    const scope=fields(row.scope),a=row.asset
    // A reused picture belongs to this authorized placement in the downloaded
    // story, not to its upload's original book which may later be removed.
    const variants=Object.fromEntries(Object.entries(a.variants).map(([name,v])=>{const key=a.assetId+':'+name+':'+v.sha256;if(value.bytes[key])references.add(key);return[name,{width:v.width,height:v.height,mime:v.mime,sha256:v.sha256,bytes:v.bytes,path:'/assets/'+a.assetId+'/variants/'+name+'?scope='+encodeURIComponent(imageScopeKey(scope))}]}))
    const asset:StoryImage={assetId:a.assetId,ownerId:a.ownerId,sourceKind:a.sourceKind,licenseNote:a.licenseNote,sha256:a.sha256,mime:a.mime,width:a.width,height:a.height,scope,createdAt:a.createdAt,version:a.version,referenceCount:a.referenceCount,variants}
    return {id:row.id,scope,purpose:row.purpose,contentId:row.contentId,paragraphId:row.paragraphId,side:row.side,order:row.order,assetId:row.assetId,alt:row.alt,version:row.version,asset}
  })
  return {schema:1,placements,bytes:Object.fromEntries([...references].map(key=>[key,value.bytes[key]!]))}
}
export async function validateOfflineImages(value:OfflineImages,scope:ImageScope){
  const clean=publicImages(value)
  if(JSON.stringify(clean)!==JSON.stringify(value))throw new Error('图片包含有未授权字段')
  for(const row of value.placements){
    validatePlacement(row,scope)
    if(!['cover','avatar','scene','illustration'].includes(row.purpose))throw new Error('图片用途无效')
    for(const name of new Set([preferredVariant(row.asset,row.purpose),preferredVariant(row.asset,row.purpose,true)]))if(!value.bytes[imageByteKey(row.asset,name)])throw new Error('图片显示资源尚未完整保存')
    for(const [name,variant] of Object.entries(row.asset.variants)){const data=value.bytes[row.assetId+':'+name+':'+variant.sha256];if(data)await verifiedImageData(data,variant)}
  }
  return value
}
export function remapBookImages(images:OfflineImages|undefined,oldId:string,newId:string,branchId:string):OfflineImages|undefined{
  if(!images)return
  const remap=(scope:ImageScope):ImageScope=>scope.kind==='book'&&scope.bookId===oldId&&scope.branchId===branchId?{...scope,bookId:newId,...(scope.anchor?{anchor:{...scope.anchor,bookId:newId}}:{})}:scope
  return publicImages({...images,placements:images.placements.filter(row=>row.scope.kind==='book'&&row.scope.bookId===oldId&&row.scope.branchId===branchId).map(row=>({...row,scope:remap(row.scope),asset:{...row.asset,scope:remap(row.asset.scope)}}))})
}
export function worldImagesAt(images:OfflineImages|undefined,worldId:string,lineId:string,checkpointId:string,ancestry:string[]):OfflineImages|undefined{
  if(!images)return
  const allowed=new Set(ancestry.slice(0,ancestry.indexOf(checkpointId)+1))
  return {...images,placements:images.placements.filter(row=>row.scope.kind==='world'&&row.scope.worldId===worldId&&row.scope.lineId===lineId&&allowed.has(row.scope.checkpointId))}
}
