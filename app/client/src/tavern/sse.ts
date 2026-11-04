// Server-sent events for Tavern 2.0 chat streaming (docs/step6-native-tavern.md §4).
// Kept independent from Vue so the framing/decoding logic can be unit tested with
// plain ReadableStreams.
import {authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'

export const TAVERN_PREFIX='tavern'

export interface SSEEvent{event:string;data:string;id?:string}

export interface ChatStreamPayload{text?:string;message?:unknown;chat?:unknown;code?:string;messageText?:string}

export interface ChatStreamHandlers{
  onMessage?(payload:ChatStreamPayload):void
  onDelta?(text:string):void
  onDone?(payload:ChatStreamPayload):void
  onError?(error:{code?:string;message:string}):void
}

// Incremental SSE framer. Feed decoded chunks; complete event blocks are handed
// to the callback. Handles \r\n, multi-line data fields, comments and BOM.
export function createSSEParser(onEvent:(event:SSEEvent)=>void):(chunk:string)=>void{
  let buffer=''
  return (chunk:string)=>{
    buffer+=chunk.replace(/\r\n?/g,'\n')
    let boundary=buffer.indexOf('\n\n')
    while(boundary>=0){
      const block=buffer.slice(0,boundary)
      buffer=buffer.slice(boundary+2)
      const parsed=parseEventBlock(block)
      if(parsed)onEvent(parsed)
      boundary=buffer.indexOf('\n\n')
    }
  }
}

export function parseEventBlock(block:string):SSEEvent|null{
  let event='message',id:string|undefined
  const data:string[]=[]
  for(const rawLine of block.split('\n')){
    const line=rawLine.replace(/^\uFEFF/,'')
    if(!line||line.startsWith(':'))continue
    const colon=line.indexOf(':')
    const field=colon>=0?line.slice(0,colon):line
    let value=colon>=0?line.slice(colon+1):''
    if(value.startsWith(' '))value=value.slice(1)
    if(field==='event')event=value
    else if(field==='data')data.push(value)
    else if(field==='id')id=value
  }
  if(!data.length)return null
  return {event,data:data.join('\n'),...(id?{id}:{})}
}

export async function readSSEStream(
  stream:ReadableStream<Uint8Array>|null,
  onEvent:(event:SSEEvent)=>void,
  signal?:AbortSignal,
):Promise<void>{
  if(!stream)return
  const reader=stream.getReader()
  const decoder=new TextDecoder()
  const parser=createSSEParser(onEvent)
  try{
    while(true){
      if(signal?.aborted)break
      const {done,value}=await reader.read()
      if(done)break
      if(value)parser(decoder.decode(value,{stream:true}))
    }
    parser(decoder.decode())
    parser('\n\n')
  }finally{
    try{reader.releaseLock()}catch{/* already released */}
  }
}

function readJSON(data:string):ChatStreamPayload|string{
  if(!data||data==='[DONE]')return data||'[DONE]'
  try{return JSON.parse(data) as ChatStreamPayload}catch{return data}
}

export function dispatchSSEEvent(event:SSEEvent,handlers:ChatStreamHandlers):void{
  if(event.data==='[DONE]'){
    handlers.onDone?.({})
    return
  }
  const payload=readJSON(event.data)
  if(typeof payload==='string'){
    if(event.event==='error')handlers.onError?.({message:payload})
    return
  }
  if(event.event==='delta')handlers.onDelta?.(payload.text??'')
  else if(event.event==='message')handlers.onMessage?.(payload)
  else if(event.event==='done')handlers.onDone?.(payload)
  else if(event.event==='error')handlers.onError?.({code:payload.code,message:String(payload.message??payload.messageText??'生成失败，请重试')})
}

export async function streamChat(
  path:string,
  body:unknown,
  handlers:ChatStreamHandlers,
  options:{signal?:AbortSignal}={},
):Promise<void>{
  const response=await fetch(backendBase()+'/api/reader/v1/'+TAVERN_PREFIX+path,{
    method:'POST',
    credentials:'include',
    headers:{...authorizationHeaders(),'Content-Type':'application/json',Accept:'text/event-stream'},
    body:JSON.stringify(body??{}),
    signal:options.signal,
  })
  if(!response.ok){
    const problem=await response.json().catch(()=>null) as {code?:string;message?:string}|null
    if(response.status===401)throw new Error('登录已过期，请重新登录。')
    if(response.status===404)throw new Error(problem?.message||'服务端暂不支持流式聊天，请稍后重试。')
    throw new Error(problem?.message||('生成失败（'+response.status+'），请重试。'))
  }
  await readSSEStream(response.body,event=>dispatchSSEEvent(event,handlers),options.signal)
}
