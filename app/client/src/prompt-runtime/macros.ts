import {ContractError} from '../domain/local-contract'
export interface MacroContext {char:string;user:string;persona:string;description:string;personality:string;scenario:string;mesExamples:string;input:string;lastMessage:string;lastUserMessage:string;lastCharMessage:string;date:string;time:string;seed:string;variables?:Record<string,string|number>;globalVariables?:Record<string,string|number>;pipe?:string}
export function seededFraction(seed:string,key:string){let hash=2166136261;for(const character of seed+'\0'+key){hash^=character.charCodeAt(0);hash=Math.imul(hash,16777619)}hash^=hash>>>16;hash=Math.imul(hash,0x85ebca6b);hash^=hash>>>13;return (hash>>>0)/4294967296}
/** Unknown macros stay visible. Expansion cannot execute commands or read keys. */
export function expandMacros(source:string,context:MacroContext){
  let text=source;const unknown=new Set<string>(),randomResults:Record<string,string>={};let serial=0
  for(let depth=0;depth<4;depth++){
    let changed=false
    const next=text.replace(/\{\{([^{}]{1,1000})\}\}/g,(original,body:string)=>{let [name,...arguments_]=body.split('::');if(!arguments_.length&&/^(?:random|pick|roll|comment|hidden_key|reverse):/i.test(body)){const colon=body.indexOf(':');name=body.slice(0,colon);arguments_=[body.slice(colon+1)]}const key=(name||'').toLowerCase(),argument=arguments_.join('::');let value:string|undefined
      if((key==='random'||key==='pick')&&arguments_.length===1&&body.includes(':')&&!body.includes('::'))arguments_=argument.split(/(?<!\\),/).map(value=>value.replace(/\\,/g,','))
      if((key==='random'||key==='pick')&&arguments_.length){const occurrence=key==='pick'||!body.includes('::')?0:serial++;const index=Math.min(arguments_.length-1,Math.floor(seededFraction(context.seed,original+':'+occurrence)*arguments_.length));value=arguments_[index]!;randomResults[original+':'+occurrence]=value}
      else if(key==='roll'){const match=(/^\d+$/.test(argument)?'d'+argument:argument).match(/^(\d{1,2})?d(\d{1,5})([+-]\d{1,5})?$/i);if(match){const count=Number(match[1]||1),sides=Number(match[2]),modifier=Number(match[3]||0);if(count<=20&&sides>=1&&sides<=10000){let total=modifier;for(let n=0;n<count;n++)total+=1+Math.floor(seededFraction(context.seed,original+':'+serial++)*sides);value=String(total);randomResults[original+':'+serial]=value}}}
      else if(key==='getvar'||key==='var')value=String(context.variables&&Object.hasOwn(context.variables,argument)?context.variables[argument]:original)
      else if(key==='getglobalvar'||key==='globalvar')value=String(context.globalVariables&&Object.hasOwn(context.globalVariables,argument)?context.globalVariables[argument]:original)
      else if(key==='pipe')value=context.pipe||''
      else if(key.startsWith('//')||key==='comment'||key==='hidden_key')value=''
      else if(key==='reverse')value=Array.from(argument).reverse().join('')
      else {const field=Object.keys(context).find(field=>field.toLowerCase()===key);if(field&&!['variables','globalVariables','seed'].includes(field))value=String(context[field as keyof MacroContext]??'')}
      if(value===undefined){unknown.add(original);return original}if(value!==original)changed=true;return value
    })
    if(next.length>200000)throw new ContractError('MACRO_LIMIT','宏展开超过长度上限；未发送请求');text=next;if(!changed)break
  }
  if(/\{\{(?:char|user|persona|description|personality|scenario|mesExamples)\}\}/.test(text))unknown.add('展开达到深度上限，保留未展开原文')
  return {text,unknown:[...unknown],randomResults}
}
