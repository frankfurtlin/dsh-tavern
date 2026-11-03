import type {ChatReplyStream,Message,Task} from './types'
import {reduceDurable,streamSnapshot} from './durable-stream'
import type {DurableEvent} from './durable-stream'

export interface ChatPreview extends ChatReplyStream {taskId:string|null;userStatus?:'sending'|'retry'}
export interface ChatRow {id:string;turnId:string;taskId:string;role:string;text:string;status:string;preview:boolean;reviewWarning:boolean}

export function pendingChatPreview(sessionId:string,turnId:string,userMessage:string,taskId:string|null=null):ChatPreview{
  return {sessionId,turnId,userMessage,taskId,text:'',version:-1,status:'waiting'}
}

// Only server snapshots supply reply text. Older events, another session, and
// another turn cannot replace the active request's already displayed words.
export function mergeChatPreview(current:ChatPreview|null,task:Task,sessionId:string):ChatPreview|null{
  const stream=task.replyStream,taskId=task.id||task.taskId
  if(!stream||stream.sessionId!==sessionId||!stream.turnId||typeof stream.text!=='string'||typeof stream.userMessage!=='string'||!Number.isInteger(stream.version)||stream.version<0)return current
  if(current&&(current.sessionId!==sessionId||current.turnId!==stream.turnId||current.taskId&&current.taskId!==taskId||stream.version<=current.version))return current
  return {...stream,taskId,reviewWarnings:[...(stream.reviewWarnings||[])]}
}

export function chatRows(sessionId:string,messages:Message[],preview:ChatPreview|null):ChatRow[]{
  const rows=messages.map(message=>({id:`${sessionId}:${message.turnId||message.id}:${message.taskId}:${message.role}`,turnId:message.turnId,taskId:message.taskId,role:message.role,text:message.text,status:message.status,preview:false,reviewWarning:message.status==='review_warning'}))
  if(!preview||preview.sessionId!==sessionId)return rows
  const has=(role:string)=>rows.some(row=>row.turnId===preview.turnId&&row.role===role&&(!preview.taskId||!row.taskId||row.taskId===preview.taskId))
  if(!has('user'))rows.push({id:`${sessionId}:${preview.turnId}:${preview.taskId||''}:user`,turnId:preview.turnId,taskId:preview.taskId||'',role:'user',text:preview.userMessage,status:preview.taskId?'pending':preview.userStatus||'sending',preview:true,reviewWarning:false})
  if(preview.text&&!has('assistant'))rows.push({id:`${sessionId}:${preview.turnId}:${preview.taskId||''}:assistant`,turnId:preview.turnId,taskId:preview.taskId||'',role:'assistant',text:preview.text,status:preview.status,preview:true,reviewWarning:!!preview.reviewWarnings?.length})
  return rows
}

export function chatRowNote(row:ChatRow){
  if(row.role==='user')return row.status==='sending'?'正在发送…':row.status==='retry'?'暂未确认发送，请重试同一条消息。':''
  const status=row.status==='stopped'?'已停止，已有文字已保留。':row.status==='interrupted'?'回复中断，已有文字已保留。':row.status==='partial'?'这段回复尚未完成。':''
  return [status,row.reviewWarning?'这段回复可能与故事有出入。':''].filter(Boolean).join(' ')
}

export function chatFollowingScroll(previousTop:number,top:number,height:number,viewport:number,following:boolean){
  if(top<previousTop-2)return false
  if(height-viewport-top<=48)return true
  return following
}

export function confirmedChatDraft(input:string,submitted:string,draftAtSubmit?:string,alreadyCleared=false){
  if(alreadyCleared)return input
  return input===(draftAtSubmit??submitted)||input===submitted?'':input
}

export function chatAccessDenied(error:unknown){
  if(!error||typeof error!=='object')return false
  const {code,status}=error as {code?:string;status?:number}
  if(code==='BUDGET_NOT_AUTHORIZED')return false
  return ['ROLE_SCOPE_CHANGED','ROLE_CHARACTER_CHANGED','ROLE_UNAVAILABLE','SESSION_CHANGED','AUTH_REQUIRED','RESOURCE_NOT_FOUND','SESSION_REVOKED','ACCOUNT_DISABLED','ANCHOR_INVALID'].includes(code||'')||[401,403,404].includes(status||0)
}
export function chatAccessMessage(error:unknown){
  const code=(error as {code?:string}|null)?.code||''
  return ['SESSION_CHANGED','AUTH_REQUIRED','SESSION_REVOKED','ACCOUNT_DISABLED'].includes(code)?'账号或登录状态已改变，请重新登录后打开对话。':'这段对话的阅读范围已改变或已不可用，请关闭后重新选择角色。'
}

export function applyChatEvent(task:Task,event:DurableEvent):Task|null{
  const stream=task.replyStream
  if(!stream||!stream.contentId&&stream.text)return null
  const previous=streamSnapshot(task.id,task.eventCursor,stream),next=reduceDurable(previous,event)
  if(next.resync)return null
  return {...task,eventCursor:next.state.seq,replyStream:next.state.content&&next.state.content!==previous.content?{...stream,...next.state.content,version:Math.max(stream.version+1,next.state.content.snapshotVersion)}:stream}
}
