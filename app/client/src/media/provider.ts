import {ContractError} from '../domain/local-contract'
import {mediaBase,type MediaJob} from './contract'
import {mediaMime} from './assets'
const MAX_BYTES=25*1024*1024,MAX_JSON=36*1024*1024
export class MediaFailure extends ContractError {constructor(code:string,message:string,public uncertain:boolean){super(code,message)}}
export interface MediaResult {bytes:Uint8Array|null;mime:string;text:string|null;requestId:string|null;inputTokens:number|null;outputTokens:number|null}
function token(value:unknown){return typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?value:null}
function containsSecret(bytes:Uint8Array,key:string){
  if(!key)return false
  const needle=new TextEncoder().encode(key),prefix=new Uint32Array(needle.length)
  for(let i=1,j=0;i<needle.length;i++){while(j&&needle[i]!==needle[j])j=prefix[j-1]!;if(needle[i]===needle[j])j++;prefix[i]=j}
  for(let i=0,j=0;i<bytes.length;i++){while(j&&bytes[i]!==needle[j])j=prefix[j-1]!;if(bytes[i]===needle[j])j++;if(j===needle.length)return true}
  return false
}
function secretResult(){throw new MediaFailure('MEDIA_SECRET_RESULT','媒体供应商在结果中回显了凭据，已拒绝保存；原请求不会重发',false)}
function jsonContainsSecret(value:unknown,key:string):boolean{return !!key&&(typeof value==='string'?value.includes(key):Array.isArray(value)?value.some(item=>jsonContainsSecret(item,key)):!!value&&typeof value==='object'&&Object.entries(value).some(([field,item])=>field.includes(key)||jsonContainsSecret(item,key)))}
function outputMime(bytes:Uint8Array,declared:string,kind:'image'|'audio'){
  let mime:string;try{mime=mediaMime(bytes,declared)}catch{throw new MediaFailure('MEDIA_OUTPUT_FORMAT','供应商返回的原文件格式无法核实，原请求不会重发',false)}
  if(!mime.startsWith(kind+'/'))throw new MediaFailure('MEDIA_OUTPUT_KIND','供应商结果与请求的媒体类型不符，未保存为成功；原请求不会重发',false)
  return mime
}
function base64(value:unknown){if(typeof value!=='string'||value.length>Math.ceil(MAX_BYTES/3)*4||!value.length||!/^[A-Za-z0-9+/]*={0,2}$/.test(value))throw new MediaFailure('MEDIA_RESULT','供应商图片不是有效的有界 base64，原请求不会重发',false);try{const bytes=Uint8Array.from(atob(value),c=>c.charCodeAt(0));if(!bytes.length||bytes.length>MAX_BYTES)throw new Error();return bytes}catch{throw new MediaFailure('MEDIA_RESULT','图片原文件无法解码，原请求不会重发',false)}}
/** One explicit POST. No retry, redirects, credentials, or fetching URLs returned by an untrusted provider. */
export async function requestMedia(job:MediaJob,reference:{bytes:Uint8Array;mime:string;name:string}|null,signal:AbortSignal,callbacks:{request?:(id:string)=>Promise<void>;chunk?:(bytes:Uint8Array,total:number)=>void}={},transport:typeof fetch=fetch):Promise<MediaResult>{
  const config=job.configSnapshot,controller=new AbortController(),cancel=()=>controller.abort(signal.reason)
  signal.throwIfAborted();signal.addEventListener('abort',cancel,{once:true})
  let firstTimer:ReturnType<typeof setTimeout>|null=null,idleTimer:ReturnType<typeof setTimeout>|null=null,timeout=false,wire=false,requestId:string|null=null
  const expired=()=>{timeout=true;controller.abort()},totalTimer=setTimeout(expired,config.totalMs),idle=()=>{if(idleTimer)clearTimeout(idleTimer);idleTimer=setTimeout(expired,config.idleMs)}
  try{
    const headers:Record<string,string>={Accept:job.type==='tts'?'audio/*':'application/json'};if(config.key)headers.Authorization='Bearer '+config.key
    let path:string,body:BodyInit
    if(job.type==='image'){path='/images/generations';headers['Content-Type']='application/json';body=JSON.stringify({model:config.modelId,prompt:job.prompt,n:1,...(config.imageFormat==='gpt-image'?{output_format:'png'}:{response_format:'b64_json'})})}
    else if(job.type==='edit'){if(!reference||!reference.mime.startsWith('image/'))throw new MediaFailure('MEDIA_REFERENCE','参考图无效，未发送',false);path='/images/edits';const form=new FormData();form.set('model',config.modelId);form.set('prompt',job.prompt);form.set('n','1');form.set(config.imageFormat==='gpt-image'?'output_format':'response_format',config.imageFormat==='gpt-image'?'png':'b64_json');form.set('image',new Blob([Uint8Array.from(reference.bytes)],{type:reference.mime}),reference.name);body=form}
    else if(job.type==='tts'){path='/audio/speech';headers['Content-Type']='application/json';body=JSON.stringify({model:config.modelId,voice:config.voice,input:job.prompt,response_format:'mp3'})}
    else {if(!reference||!reference.mime.startsWith('audio/'))throw new MediaFailure('MEDIA_REFERENCE','转录需要可播放的音频，未发送',false);path='/audio/transcriptions';const form=new FormData();form.set('model',config.modelId);form.set('response_format','json');form.set('file',new Blob([Uint8Array.from(reference.bytes)],{type:reference.mime}),reference.name);body=form}
    firstTimer=setTimeout(expired,config.firstPacketMs);wire=true
    const response=await transport(mediaBase(config.baseUrl)+path,{method:'POST',headers,body,signal:controller.signal,credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'})
    if(firstTimer){clearTimeout(firstTimer);firstTimer=null}idle()
    requestId=response.headers.get('x-request-id')||response.headers.get('request-id');if(requestId&&requestId.length<=1000&&(!config.key||!requestId.includes(config.key)))await callbacks.request?.(requestId);else requestId=null
    if(!response.ok)throw new MediaFailure('MEDIA_HTTP_'+response.status,response.status===401||response.status===403?'媒体通道鉴权失败，请检查独立配置':response.status===429?'媒体通道额度或频率受限，原请求不会重发':'媒体供应商报告 HTTP '+response.status+'，原请求不会重发',response.status>=500)
    const mime=(response.headers.get('content-type')||'').split(';')[0]!.trim().toLowerCase(),max=job.type==='tts'?MAX_BYTES:MAX_JSON,length=Number(response.headers.get('content-length'))
    if(Number.isFinite(length)&&length>max)throw new MediaFailure('MEDIA_LIMIT','供应商结果超过本地上限，原请求不会重发',false)
    if(!response.body)throw new MediaFailure('MEDIA_EMPTY','供应商没有返回结果，原请求不会重发',true)
    const reader=response.body.getReader(),parts:Uint8Array[]=[];let size=0
    try{for(;;){const chunk=await reader.read();controller.signal.throwIfAborted();if(chunk.done)break;idle();size+=chunk.value.length;if(size>max)throw new MediaFailure('MEDIA_LIMIT','供应商结果超过本地上限，原请求不会重发',false);parts.push(chunk.value);if(job.type==='tts')callbacks.chunk?.(chunk.value,size)}}finally{if(controller.signal.aborted||size>max)await reader.cancel().catch(()=>{});reader.releaseLock()}
    const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length}
    if(containsSecret(bytes,config.key))secretResult()
    if(job.type==='tts'){if(!size||!['audio/mpeg','audio/mp3','audio/wav','audio/x-wav','audio/ogg','application/octet-stream'].includes(mime))throw new MediaFailure('MEDIA_MIME','配音返回不是支持的音频，原请求不会重发',false);return {bytes,mime:outputMime(bytes,mime,'audio'),text:null,requestId,inputTokens:null,outputTokens:null}}
    if(mime!=='application/json')throw new MediaFailure('MEDIA_MIME','媒体通道没有返回 JSON，原请求不会重发',false)
    let result:Record<string,unknown>;try{const parsed:unknown=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error();result=parsed as Record<string,unknown>}catch{throw new MediaFailure('MEDIA_RESULT','供应商结果无法解析，原请求不会重发',false)}
    if(jsonContainsSecret(result,config.key))secretResult()
    const usage=result.usage&&typeof result.usage==='object'?result.usage as Record<string,unknown>:{}
    if(job.type==='asr'){if(typeof result.text!=='string'||result.text.length>1_000_000)throw new MediaFailure('MEDIA_TRANSCRIPT','供应商没有返回可编辑的转录文本，原请求不会重发',false);return {bytes:null,mime:'text/plain',text:result.text,requestId,inputTokens:token(usage.input_tokens),outputTokens:token(usage.output_tokens)}}
    const item=Array.isArray(result.data)?result.data[0] as Record<string,unknown>|undefined:undefined
    if(!item?.b64_json&&item?.url)throw new MediaFailure('MEDIA_URL_RESULT','供应商只返回临时链接。此通道需配置 base64 图片返回；原请求不会重发。',false)
    const image=base64(item?.b64_json);if(containsSecret(image,config.key))secretResult()
    return {bytes:image,mime:outputMime(image,'image/png','image'),text:null,requestId,inputTokens:token(usage.input_tokens),outputTokens:token(usage.output_tokens)}
  }catch(error){if(error instanceof MediaFailure||error instanceof ContractError)throw error;throw new MediaFailure(timeout?'MEDIA_TIMEOUT':signal.aborted?'MEDIA_CANCELLED':'MEDIA_CONNECTION',timeout?'媒体响应超时，原请求保留，不会自动重发':signal.aborted?'已停止等待，原请求不会重发':'媒体连接中断，原请求保留，不会自动重发',wire)}
  finally{controller.abort();if(firstTimer)clearTimeout(firstTimer);if(idleTimer)clearTimeout(idleTimer);clearTimeout(totalTimer);signal.removeEventListener('abort',cancel)}
}
