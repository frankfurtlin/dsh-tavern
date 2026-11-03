import {ContractError} from '../domain/local-contract'
import {mediaBase} from './contract'
import {validVector,type VectorRecord} from './vector-contract'
export class VectorFailure extends ContractError {constructor(code:string,message:string,public uncertain=false){super(code,message)}}
export interface VectorResult {vector:number[];requestId:string|null;inputTokens:number|null}
/** Single explicit embedding POST, bounded reads, independent credentials and split timeouts. */
export async function requestVector(record:VectorRecord,signal:AbortSignal,transport:typeof fetch=fetch,observed?:(requestId:string)=>Promise<void>):Promise<VectorResult>{
  const config=record.configSnapshot,control=new AbortController(),abort=()=>control.abort(signal.reason);signal.throwIfAborted();signal.addEventListener('abort',abort,{once:true})
  let first:ReturnType<typeof setTimeout>|null=null,idle:ReturnType<typeof setTimeout>|null=null,timeout=false,wire=false
  const expired=()=>{timeout=true;control.abort()},total=setTimeout(expired,config.totalMs),resetIdle=()=>{if(idle)clearTimeout(idle);idle=setTimeout(expired,config.idleMs)}
  try{
    first=setTimeout(expired,config.firstPacketMs);wire=true
    const response=await transport(mediaBase(config.baseUrl)+'/embeddings',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json',...(config.key?{Authorization:'Bearer '+config.key}:{})},body:JSON.stringify({model:config.modelId,input:record.input,encoding_format:'float'}),signal:control.signal,credentials:'omit',redirect:'error',referrerPolicy:'no-referrer',cache:'no-store'})
    clearTimeout(first);first=null;resetIdle()
    const rawId=response.headers.get('x-request-id')||response.headers.get('request-id'),requestId=rawId&&rawId.length<=1000&&(!config.key||!rawId.includes(config.key))?rawId:null
    if(requestId&&observed)try{await observed(requestId)}catch{throw new VectorFailure('VECTOR_RECEIPT','供应商已返回请求编号，但设备记录未保存，原请求不会重发',true)}
    if(!response.ok)throw new VectorFailure('VECTOR_HTTP_'+response.status,'向量通道报告 HTTP '+response.status+'，原请求不会重发',response.status>=500)
    if((response.headers.get('content-type')||'').split(';')[0]!.trim().toLowerCase()!=='application/json'||!response.body)throw new VectorFailure('VECTOR_MIME','向量通道没有返回 JSON，原请求不会重发')
    if(Number(response.headers.get('content-length'))>1024*1024)throw new VectorFailure('VECTOR_LIMIT','向量结果超过 1 MiB，原请求不会重发')
    const reader=response.body.getReader(),parts:Uint8Array[]=[];let size=0
    try{for(;;){const chunk=await reader.read();control.signal.throwIfAborted();if(chunk.done)break;resetIdle();size+=chunk.value.length;if(size>1024*1024)throw new VectorFailure('VECTOR_LIMIT','向量结果超过 1 MiB，原请求不会重发');parts.push(chunk.value)}}finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
    const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length}
    let result:{data?:{embedding?:unknown;index?:unknown}[];usage?:{prompt_tokens?:unknown}};try{result=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))}catch{throw new VectorFailure('VECTOR_RESULT','向量结果无法解析，原请求不会重发')}
    if(!result||!Array.isArray(result.data)||result.data.length!==1||result.data[0]?.index!==0||!validVector(result.data[0]?.embedding))throw new VectorFailure('VECTOR_RESULT','供应商没有返回与单条输入对应的有限非零向量，原请求不会重发')
    const tokens=result.usage?.prompt_tokens
    return {vector:result.data[0].embedding,requestId,inputTokens:typeof tokens==='number'&&Number.isSafeInteger(tokens)&&tokens>=0?tokens:null}
  }catch(error){if(error instanceof VectorFailure)throw error;throw new VectorFailure(timeout?'VECTOR_TIMEOUT':signal.aborted?'VECTOR_CANCELLED':'VECTOR_CONNECTION',timeout?'向量响应超时，原请求保留':signal.aborted?'已停止向量请求，原请求不会重发':'向量连接中断，原请求保留，不会自动重发',wire)}
  finally{control.abort();if(first)clearTimeout(first);if(idle)clearTimeout(idle);clearTimeout(total);signal.removeEventListener('abort',abort)}
}
