import type {User} from './types'
export interface DefaultModels {text:{enabled:boolean;modelId:string|null};image:{enabled:boolean;modelId:string|null}}
export interface SessionBootstrap {version:1;user:User;world:{enabled:boolean;ready:boolean};defaultModels:DefaultModels}
export function parseSessionBootstrap(data:any):SessionBootstrap|undefined{
  const value=data?.bootstrap,user=value?.user
  if(value?.version!==1||!user||typeof user.id!=='string'||!user.id||typeof data.sessionId!=='string'||!data.sessionId||user.id!==data.user?.id||user.sessionId!==data.sessionId||typeof user.username!=='string'||!user.settings||typeof user.settings.version!=='number'||typeof user.settings.fontSize!=='number'||!['real','mock'].includes(user.aiMode)||typeof value.world?.enabled!=='boolean'||typeof value.world?.ready!=='boolean'||typeof value.defaultModels?.text?.enabled!=='boolean'||typeof value.defaultModels?.image?.enabled!=='boolean')return
  return value
}
