import {parseImportedText} from './local-text-parser'
import type {ContractError} from '../domain/local-contract'
self.addEventListener('message',async(event:MessageEvent<{text:string;suffix:string;parseHash:string}>)=>{
  try{const {text,suffix,parseHash}=event.data;if(typeof text!=='string'||!['txt','md','markdown'].includes(suffix)||!/^[a-f0-9]{64}$/.test(parseHash))throw Error('解析请求无效');const result=await parseImportedText(text,suffix,parseHash,progress=>self.postMessage({type:'progress',progress}));self.postMessage({type:'complete',result})}
  catch(error){self.postMessage({type:'error',code:(error as ContractError).code||'IMPORT_PARSE',message:(error as Error).message})}
})
