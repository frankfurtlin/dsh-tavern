import {ContractError} from '../domain/local-contract'
import type {ParsedText,ParsingProgress} from './local-text-parser'
export async function parseTextInBackground(text:string,suffix:string,parseHash:string,signal:AbortSignal,progress:(value:ParsingProgress)=>void):Promise<ParsedText>{
  signal.throwIfAborted()
  const worker=new Worker(new URL('./local-text-worker.ts',import.meta.url),{type:'module',name:'reader-private-text-import'})
  return new Promise<ParsedText>((resolve,reject)=>{
    let settled=false
    const finish=(result?:ParsedText,error?:Error)=>{if(settled)return;settled=true;signal.removeEventListener('abort',abort);worker.terminate();if(error)reject(error);else resolve(result!)}
    const abort=()=>finish(undefined,signal.reason instanceof Error?signal.reason:new DOMException('导入已停止','AbortError'))
    worker.addEventListener('error',()=>finish(undefined,new ContractError('IMPORT_WORKER','后台解析未完成，原文件保留')))
    worker.addEventListener('messageerror',()=>finish(undefined,new ContractError('IMPORT_WORKER','后台解析结果无法读取，原文件保留')))
    worker.addEventListener('message',(event:MessageEvent)=>{if(settled)return;if(signal.aborted){abort();return}const data=event.data;if(data.type==='progress'){try{progress(data.progress)}catch(error){finish(undefined,error as Error)}}else if(data.type==='complete')finish(data.result);else if(data.type==='error')finish(undefined,new ContractError(data.code,data.message))})
    signal.addEventListener('abort',abort,{once:true});if(signal.aborted){abort();return}worker.postMessage({text,suffix,parseHash})
  })
}
