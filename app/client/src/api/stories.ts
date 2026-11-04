import {api,authGeneration,ApiError} from './index'
import type {Anchor,RoleSession} from './types'
import type {WorldInfo} from '../world/types'

export type StoryMode='read'|'chat'|'play'
export interface StoryContext {
  storyId:string
  mode:StoryMode
  perspective:'reader'|'actor'
  entryOrigin:'library'|'worlds'|'reader'|'graph'|'conversation'
  bookId?:string
  readerBranchId?:string
  worldId?:string
  worldLineId?:string
  anchor?:Anchor
  checkpointId?:string
  characterId?:string
  sessionId?:string
}
export interface StoryResume {version:number;context:StoryContext|null;updatedAt:string|null;available:boolean}
export interface StoryWorld {
  id:string;name:string;status:string;version:number;activeLineId:string|null;prepareTaskId:string|null
  sourceRemoved:boolean;updatedAt:string;playerName?:string;locationName?:string;timeLabel?:string
  resumeContext:StoryContext|null
}
export interface StorySummary {
  id:string;kind:'book'|'world';title:string;status:string;version:number
  coverUrl?:string|null;updatedAt?:string|null
  source?:{bookId:string|null;bookTitle:string;sourceRemoved:boolean;readerBranchId?:string;anchor?:Anchor}|null
}
export interface StoryDetail extends StorySummary {
  resume:Record<StoryMode,StoryResume>
  worlds:StoryWorld[]
  canCreateWorld:boolean
}
const opaqueId=/^[a-f0-9]{32}$/
export function storyId(kind:'book'|'world',id:string){
  if(!opaqueId.test(id))throw new Error('故事标识无效')
  return `${kind}-${id}`
}
export function storyPath(kind:'book'|'world',id:string){return '/stories/'+storyId(kind,id)}
export function getStory(id:string){return api<StoryDetail>('/stories/'+encodeURIComponent(id),{cache:true})}

// Only validated server contexts reach navigation. Never guess a missing line,
// actor identity, or conversation from another mode's saved position.
export function contextRoute(context:StoryContext):{path:string;query:Record<string,string>} {
  const reader=context.perspective==='reader',actor=context.perspective==='actor'
  if([context.bookId,context.readerBranchId,context.worldId,context.worldLineId,context.sessionId,context.checkpointId].some(value=>value!==undefined&&!opaqueId.test(value)))throw new Error('故事位置标识无效')
  if((context.worldId||context.worldLineId||context.checkpointId)&&(context.bookId||context.readerBranchId||context.anchor))throw new Error('阅读位置与游玩位置不能混用')
  if(context.mode==='play'&&actor&&context.worldId&&context.worldLineId&&context.checkpointId&&context.characterId&&!context.sessionId){
    return {path:`/worlds/${context.worldId}/play`,query:{line:context.worldLineId,story:context.storyId}}
  }
  if(context.mode==='read'&&reader&&context.bookId&&context.readerBranchId&&context.anchor){
    const at=context.anchor
    if(at.bookId!==context.bookId||at.branchId!==context.readerBranchId||at.offsetUnit!=='utf16')throw new Error('阅读位置不属于这条故事线')
    return {path:`/books/${context.bookId}/read`,query:{branch:context.readerBranchId,
      revision:at.revisionId,chapter:at.chapterId,anchor:JSON.stringify(at),story:context.storyId}}
  }
  if(context.mode==='read'&&reader&&context.worldId&&context.worldLineId&&context.checkpointId){
    return {path:`/worlds/${context.worldId}/read`,query:{line:context.worldLineId,checkpoint:context.checkpointId,story:context.storyId}}
  }
  if(context.mode==='chat'&&reader&&context.sessionId&&context.characterId&&context.anchor&&context.bookId&&context.readerBranchId){
    if(context.anchor.bookId!==context.bookId||context.anchor.branchId!==context.readerBranchId)throw new Error('对话位置不属于这条故事线')
    return {path:'/conversations/world/'+context.sessionId,query:{story:context.storyId}}
  }
  throw new Error('这个入口缺少有效的位置，请返回故事重新选择')
}
export async function rememberContext(context:StoryContext,valid:()=>boolean=()=>true){
  const epoch=authGeneration()
  const check=()=>{if(epoch!==authGeneration()||!valid())throw new ApiError('SESSION_CHANGED','页面或账号已变化',401)}
  const detail=await getStory(context.storyId);check()
  const result=await api<StoryResume>('/stories/'+encodeURIComponent(context.storyId)+'/resume',{
    method:'PUT',body:{expectedVersion:detail.resume[context.mode].version,context}})
  check();return result
}
export function sessionContext(session:RoleSession,origin?:string):StoryContext {
  if(origin&&!/^(book|world)-[a-f0-9]{32}$/.test(origin))throw new Error('故事标识无效')
  return {storyId:origin||storyId('book',session.bookId),mode:'chat',perspective:'reader',entryOrigin:'conversation',
    bookId:session.bookId,readerBranchId:session.branchId,anchor:session.pinnedAnchor,
    sessionId:session.id,characterId:session.characterId}
}
export function worldContext(world:WorldInfo,lineId:string,checkpointId:string,characterId:string):StoryContext {
  return {storyId:storyId('world',world.id),mode:'play',perspective:'actor',entryOrigin:'worlds',
    worldId:world.id,worldLineId:lineId,checkpointId,characterId}
}
