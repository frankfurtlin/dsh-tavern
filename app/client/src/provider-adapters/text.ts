import {ContractError,type PromptMessage,type ProviderSnapshot} from '../domain/local-contract'

export type ProviderEvent={type:'text';text:string}|{type:'request';id:string}|{type:'usage';input:number|null;output:number|null}|{type:'finish';reason:string}
export interface Timeouts {firstPacketMs:number;idleMs:number;totalMs:number}
export class ProviderFailure extends ContractError {
  constructor(code:string,message:string,readonly uncertain:boolean,readonly httpStatus:number|null=null){super(code,message)}
}
export function endpoint(config:ProviderSnapshot){
  let url:URL;try{url=new URL(config.baseUrl)}catch{throw new ContractError('PROVIDER_URL','模型地址无效')}
  if(url.protocol==='http:'&&url.hostname==='[::1]')throw new ContractError('PROVIDER_URL','浏览器暂不支持 IPv6 本机模型地址；请使用 http://localhost 或 http://127.0.0.1，并保留原端口和路径')
  if(url.username||url.password||url.search||url.hash||url.protocol!=='https:'&&!(url.protocol==='http:'&&['localhost','127.0.0.1'].includes(url.hostname)))throw new ContractError('PROVIDER_URL','请使用 HTTPS，或本机模型的 HTTP 地址；密钥单独填写')
  const base=url.href.replace(/\/+$/,'')
  return config.protocol==='gemini'?base+'/models/'+encodeURIComponent(config.modelId)+':streamGenerateContent?alt=sse':base+({anthropic:'/messages',responses:'/responses','chat-completions':'/chat/completions'}[config.protocol])
}
export function requestBody(config:ProviderSnapshot,messages:PromptMessage[]){
  if(config.protocol==='chat-completions')return {model:config.modelId,messages,stream:true,[config.outputParameter||'max_tokens']:config.outputLimit,...(config.includeUsage?{stream_options:{include_usage:true}}:{})}
  const system=messages.filter(m=>m.role==='system').map(m=>m.content).join('\n\n'),history=messages.filter(m=>m.role!=='system')
  if(config.protocol==='responses')return {model:config.modelId,instructions:system,input:history,stream:true,max_output_tokens:config.outputLimit,store:config.queryStoredResponse===true}
  if(config.protocol==='anthropic')return {model:config.modelId,system,messages:history,stream:true,max_tokens:config.outputLimit}
  return {systemInstruction:{parts:[{text:system}]},contents:history.map(m=>({role:m.role==='assistant'?'model':'user',parts:[{text:m.content}]})),generationConfig:{maxOutputTokens:config.outputLimit}}
}
export function requestHeaders(config:ProviderSnapshot,sessionId:string):Record<string,string>{
  const headers:Record<string,string>={'Content-Type':'application/json','Accept':'text/event-stream'}
  if(config.protocol==='gemini')headers['x-goog-api-key']=config.key
  else if(config.protocol==='anthropic'){headers['x-api-key']=config.key;headers['anthropic-version']='2023-06-01';headers['anthropic-dangerous-direct-browser-access']='true'}
  else headers.Authorization='Bearer '+config.key
  if(new URL(config.baseUrl).hostname==='opencode.ai')headers['x-opencode-session']=sessionId
  return headers
}
const tokens=(n:unknown):number|null=>typeof n==='number'&&Number.isSafeInteger(n)&&n>=0?n:null
/** Protocol content fields only. Reasoning, signatures, tools and markup are never text deltas. */
export function normalizeEvent(config:ProviderSnapshot,data:Record<string,any>):ProviderEvent[]{
  const result:ProviderEvent[]=[]
  if(data.error||data.type==='error'||data.type==='response.failed')throw new ProviderFailure('PROVIDER_EVENT_ERROR','供应商报告请求失败；已收到的文字保留',true)
  const id=typeof data.id==='string'?data.id:typeof data.responseId==='string'?data.responseId:data.response?.id
  if(typeof id==='string')result.push({type:'request',id})
  if(config.protocol==='chat-completions'){
    const choice=data.choices?.find((c:any)=>c.index===0)||data.choices?.[0]
    if(typeof choice?.delta?.content==='string')result.push({type:'text',text:choice.delta.content})
    if(typeof choice?.message?.content==='string')result.push({type:'text',text:choice.message.content})
    if(choice?.finish_reason)result.push({type:'finish',reason:String(choice.finish_reason)})
    if(data.usage)result.push({type:'usage',input:tokens(data.usage.prompt_tokens),output:tokens(data.usage.completion_tokens)})
  }else if(config.protocol==='responses'){
    if(data.type==='response.output_text.delta'&&typeof data.delta==='string')result.push({type:'text',text:data.delta})
    if(data.type==='response.completed'||data.type==='response.incomplete'){
      if(data.response?.usage)result.push({type:'usage',input:tokens(data.response.usage.input_tokens),output:tokens(data.response.usage.output_tokens)})
      result.push({type:'finish',reason:data.type==='response.completed'?'stop':String(data.response?.incomplete_details?.reason||'incomplete')})
    }
  }else if(config.protocol==='anthropic'){
    if(data.type==='message_start'){if(typeof data.message?.id==='string')result.push({type:'request',id:data.message.id});if(data.message?.usage)result.push({type:'usage',input:tokens(data.message.usage.input_tokens),output:tokens(data.message.usage.output_tokens)})}
    if(data.type==='content_block_delta'&&data.delta?.type==='text_delta'&&typeof data.delta.text==='string')result.push({type:'text',text:data.delta.text})
    if(data.type==='message_delta'){if(data.usage)result.push({type:'usage',input:null,output:tokens(data.usage.output_tokens)});if(data.delta?.stop_reason)result.push({type:'finish',reason:String(data.delta.stop_reason)})}
  }else{
    const choice=data.candidates?.find((c:any)=>c.index===0)||data.candidates?.[0]
    for(const part of choice?.content?.parts||[])if(typeof part.text==='string'&&!part.thought)result.push({type:'text',text:part.text})
    if(choice?.finishReason)result.push({type:'finish',reason:choice.finishReason==='STOP'?'stop':String(choice.finishReason)})
    if(data.promptFeedback?.blockReason)throw new ProviderFailure('PROVIDER_BLOCKED','供应商未返回正文；可查看模型设置',false)
    if(data.usageMetadata)result.push({type:'usage',input:tokens(data.usageMetadata.promptTokenCount),output:tokens(data.usageMetadata.candidatesTokenCount)===null?null:data.usageMetadata.candidatesTokenCount+(tokens(data.usageMetadata.thoughtsTokenCount)||0)})
  }
  return result
}
/** Incremental SSE parser handles arbitrary UTF8/network and CRLF boundaries. */
export class SSEParser {
  private pending='';private data:string[]=[]
  feed(chunk:string,final=false):string[]{
    this.pending+=chunk;if(this.pending.length>1_048_576)throw new ProviderFailure('STREAM_FRAME_LIMIT','模型单个响应片段过大，已保留正文',true)
    const frames:string[]=[]
    while(true){const index=this.pending.search(/[\r\n]/);if(index<0)break;const delimiter=this.pending[index]!;if(delimiter==='\r'&&index===this.pending.length-1&&!final)break;const line=this.pending.slice(0,index);this.pending=this.pending.slice(index+(delimiter==='\r'&&this.pending[index+1]==='\n'?2:1));if(!line){if(this.data.length){frames.push(this.data.join('\n'));this.data=[]}}else if(line==='data'||line.startsWith('data:'))this.data.push(line==='data'?'':line.slice(5).replace(/^ /,''))}
    if(final){if(this.pending.startsWith('data:'))this.data.push(this.pending.slice(5).replace(/^ /,''));this.pending='';if(this.data.length){frames.push(this.data.join('\n'));this.data=[]}}
    return frames
  }
}
export async function streamText(config:ProviderSnapshot,messages:PromptMessage[],sessionId:string,sink:(event:ProviderEvent)=>Promise<void>,signal:AbortSignal,timeouts:Timeouts,transport:typeof fetch=fetch):Promise<{finishReason:string}> {
  const url=endpoint(config),body=JSON.stringify(requestBody(config,messages)),controller=new AbortController()
  let timeoutCode='',finishReason='',first=false,totalBytes=0
  const abort=()=>controller.abort(signal.reason),expire=(code:string)=>{timeoutCode=code;controller.abort()}
  signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort()
  let idle:ReturnType<typeof setTimeout>|undefined
  const firstTimer=setTimeout(()=>expire('FIRST_PACKET_TIMEOUT'),timeouts.firstPacketMs),totalTimer=setTimeout(()=>expire('TOTAL_TIMEOUT'),timeouts.totalMs)
  function activity(){if(idle)clearTimeout(idle);idle=setTimeout(()=>expire('IDLE_TIMEOUT'),timeouts.idleMs)}
  async function frame(raw:string){if(raw==='[DONE]')return;let event:Record<string,any>;try{event=JSON.parse(raw)}catch{throw new ProviderFailure('STREAM_INVALID','模型响应格式异常，已收到的文字保留',true)}if(!first){first=true;clearTimeout(firstTimer)}for(const normalized of normalizeEvent(config,event)){if(normalized.type==='finish')finishReason=normalized.reason;await sink(normalized)}}
  try{
    const response=await transport(url,{method:'POST',headers:requestHeaders(config,sessionId),body,signal:controller.signal,redirect:'error',credentials:'omit',cache:'no-store'})
    if(!response.ok){const definite=[400,401,403,404,422,429].includes(response.status);throw new ProviderFailure('PROVIDER_HTTP_'+response.status,'模型请求返回 HTTP '+response.status+'；请检查鉴权、地址和额度',!definite,response.status)}
    const requestId=response.headers.get('x-request-id')||response.headers.get('request-id');if(requestId)await sink({type:'request',id:requestId})
    if(!response.body)throw new ProviderFailure('STREAM_EMPTY','模型响应为空',true)
    const reader=response.body.getReader(),decoder=new TextDecoder('utf-8',{fatal:true}),parser=new SSEParser();activity()
    try{while(true){const part=await reader.read();if(part.done)break;activity();totalBytes+=part.value.byteLength;if(totalBytes>8*1024*1024)throw new ProviderFailure('STREAM_LIMIT','模型响应超过本次上限，已收到的文字保留',true);for(const event of parser.feed(decoder.decode(part.value,{stream:true})))await frame(event)}for(const event of parser.feed(decoder.decode(),true))await frame(event)}finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
    if(!finishReason)throw new ProviderFailure('STREAM_INTERRUPTED','响应没有结束回执；已收到的文字保留，可保存片段或查看请求记录',true)
    return {finishReason}
  }catch(error){
    if(error instanceof ProviderFailure||error instanceof ContractError)throw error
    if(timeoutCode)throw new ProviderFailure(timeoutCode,({FIRST_PACKET_TIMEOUT:'等待首个响应超时',IDLE_TIMEOUT:'响应中断超时',TOTAL_TIMEOUT:'本次请求达到总时限'} as Record<string,string>)[timeoutCode]+'；已收到的文字保留，不会自动重发',true)
    if(signal.aborted)throw new ProviderFailure('REQUEST_STOPPED','已停止等待；供应商费用与结束状态可能仍待确认',true)
    throw new ProviderFailure('CONNECTION_UNCONFIRMED','连接未确认；请检查网络、代理或浏览器跨域权限。已收到的文字保留，不会自动重发',true)
  }finally{clearTimeout(firstTimer);clearTimeout(totalTimer);if(idle)clearTimeout(idle);signal.removeEventListener('abort',abort)}
}
