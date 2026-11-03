// One untrusted expression per disposable worker. It never receives credentials.
import {boundedReplace} from './regex-replacement'
self.onmessage=(event:MessageEvent<{action?:'replace';pattern:string;flags:string;text:string;replacement?:string;trim?:string[]}>)=>{
  try{if(event.data.action==='replace')self.postMessage({...boundedReplace(event.data.pattern,event.data.flags,event.data.text,event.data.replacement||'',event.data.trim||[]),error:null});else {const expression=new RegExp(event.data.pattern,event.data.flags);self.postMessage({matched:expression.test(event.data.text),error:null})}}
  catch(error){self.postMessage({matched:false,text:event.data.text,count:0,error:(error as Error).message.endsWith('-limit')?(error as Error).message:'invalid'})}
}
self.postMessage({ready:true})
