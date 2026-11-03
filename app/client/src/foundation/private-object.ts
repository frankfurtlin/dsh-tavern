import {authorizationHeaders,authGeneration,refreshAuth} from '../api'
import {backendBase} from '../platform/bridge'
export async function privateObjectBlob(id:string,valid:()=>boolean){
  const base=backendBase(),epoch=authGeneration(),same=()=>valid()&&base===backendBase()&&epoch===authGeneration()
  let response=await fetch(base+'/api/reader/v1/product/objects/'+id+'/content',{headers:authorizationHeaders(),credentials:'include'})
  if(!same())throw new Error('账号或页面已变化')
  if(response.status===401&&await refreshAuth()){if(!same())throw new Error('账号已变化');response=await fetch(base+'/api/reader/v1/product/objects/'+id+'/content',{headers:authorizationHeaders(),credentials:'include'})}
  if(!response.ok)throw new Error('文件无法读取，请刷新后重试')
  const blob=await response.blob();if(!same())throw new Error('账号或页面已变化');return blob
}
