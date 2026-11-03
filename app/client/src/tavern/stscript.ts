import {ContractError} from '../domain/local-contract'
import {expandMacros,type MacroContext} from '../prompt-runtime/macros'
export const scriptCommands=['send','sendas','sys','trigger','continue','setinput','getvar','setvar','addvar','getglobalvar','setglobalvar','addglobalvar','if','while','times','break','abort','run','input','buttons','echo','delay','inject','flushinjects','imagine','pass','return'] as const
export interface ScriptToken {text:string;closure:boolean}
export interface ScriptCommand {name:string;named:Record<string,ScriptToken>;args:ScriptToken[]}
export interface ScriptHost {
 macros:MacroContext;variables:Record<string,string|number>;globals:Record<string,string|number>
 setInput(text:string):Promise<void>;append(role:'user'|'assistant'|'system',text:string,speaker?:string):Promise<void>
 generate(kind:'trigger'|'continue'):Promise<string>;setVariable(key:string,value:string|number,global:boolean):Promise<void>
 input(text:string):Promise<string>;buttons(text:string,labels:string[]):Promise<string>;echo(text:string):Promise<void>
 inject(id:string,text:string):Promise<void>;flushInjects(id?:string):Promise<void>;imagine(text:string):Promise<string>
 namedScript(name:string):Promise<string|null>
}
function fail(code:string,text:string):never{throw new ContractError(code,text)}
/** Lexical parsing only. Quotes and closures never become JavaScript. */
function split(source:string,pipe:boolean):string[]{
 const parts:string[]=[],stack:string[]=[];let start=0,quote='',escaped=false
 for(let i=0;i<source.length;i++){const c=source[i]!,pair=source.slice(i,i+2);if(escaped){escaped=false;continue}if(c==='\\'){escaped=true;continue}if(quote){if(c===quote)quote='';continue}if(c==='"'||c==="'"){quote=c;continue}if(pair==='{:'){stack.push(':}');i++;continue}if(pair===':}'){if(stack.pop()!==':}')fail('SCRIPT_SYNTAX','闭包结束位置无效');i++;continue}if(!pipe&&c==='['){stack.push(']');continue}if(!pipe&&c==='{'&&pair!=='{{'){stack.push('}');continue}if(!pipe&&(c===']'||c==='}')&&stack.at(-1)===c){stack.pop();continue}if(stack.length>8)fail('SCRIPT_DEPTH','脚本嵌套超过 8 层');if(!stack.length&&(pipe?c==='|':/\s/.test(c))){const value=source.slice(start,i).trim();if(value)parts.push(value);else if(pipe&&i>0&&source[i-1]==='|')parts.push('/pass');start=i+1}}
 if(quote||stack.length||escaped)fail('SCRIPT_SYNTAX','脚本引号、转义或闭包未闭合');const last=source.slice(start).trim();if(last)parts.push(last);return parts
}
function token(text:string):ScriptToken{if(text.startsWith('{:')&&text.endsWith(':}'))return {text:text.slice(2,-2).trim(),closure:true};if((text.startsWith('"')&&text.endsWith('"'))||(text.startsWith("'")&&text.endsWith("'")))text=text.slice(1,-1);return {text:text.replace(/\\([\\|"'\s])/g,'$1'),closure:false}}
export function parseScript(source:string):ScriptCommand[]{
 if(source.length>50000)fail('SCRIPT_LIMIT','脚本超过 50,000 字符');const commands=split(source,true).map(part=>{
  if(part.startsWith('//')||part.startsWith('/#'))return {name:'#',named:{},args:[]} as ScriptCommand
  const match=part.match(/^\/(\w[\w-]*|:)(?:\s+|$)([\s\S]*)$/);if(!match)fail('SCRIPT_SYNTAX','每个动作需要以 /命令 开始');const name=match[1]=== ':'?'run':match[1]!.toLowerCase(),named:Record<string,ScriptToken>={},args:ScriptToken[]=[]
  for(const value of split(match[2]!,false)){const assignment=value.match(/^([\w-]+)=([\s\S]*)$/);if(assignment){const key=assignment[1]!;if(['__proto__','constructor','prototype'].includes(key))fail('SCRIPT_PARAMETER','参数名称无效');if(Object.hasOwn(named,key))fail('SCRIPT_PARAMETER','参数重复：'+key);named[key]=token(assignment[2]!)}else args.push(token(value))}
  return {name,named,args}
 });if(commands.length>512)fail('SCRIPT_LIMIT','脚本动作超过 512 次');return commands
}
class Flow {constructor(public kind:'break'|'abort'|'return',public value:string){}}
const parameters:Record<string,string[]>={sendas:['name'],setvar:['key'],setglobalvar:['key'],addvar:['key'],addglobalvar:['key'],if:['left','right','rule','else'],while:['left','right','rule','guard'],buttons:['labels'],inject:['id']}
export async function executeScript(source:string,host:ScriptHost,signal:AbortSignal,limits={steps:512,calls:1,milliseconds:30000}){
 let steps=0,calls=0,iterations=0,pipe='',index=0;const trace:{command:string;step:number;calls:number}[]=[],started=performance.now()
 const check=(count=true)=>{signal.throwIfAborted();if(count)steps++;if(steps>limits.steps||performance.now()-started>limits.milliseconds)fail('SCRIPT_LIMIT','脚本达到动作或时间上限，已保存内容仍然保留')}
 const expand=(value:string)=>expandMacros(value.replace(/\\\{\\?\{/g,'\uE100'),{...host.macros,pipe,variables:host.variables,globalVariables:host.globals}).text.replaceAll('{{timesIndex}}',String(index)).replaceAll('\uE100','{{')
 const variable=(value:string)=>{const text=expand(value);return Number.isFinite(Number(text))&&text.trim()!==''?Number(text):Object.hasOwn(host.variables,text)?host.variables[text]!:Object.hasOwn(host.globals,text)?host.globals[text]!:text}
 const compare=(row:ScriptCommand)=>{const a=variable(row.named.left?.text||''),b=variable(row.named.right?.text||''),rule=row.named.rule?.text||'eq';switch(rule){case'eq':return a===b;case'neq':return a!==b;case'lt':return Number(a)<Number(b);case'gt':return Number(a)>Number(b);case'lte':return Number(a)<=Number(b);case'gte':return Number(a)>=Number(b);case'not':return !a;case'in':return String(a).toLowerCase().includes(String(b).toLowerCase());case'nin':return !String(a).toLowerCase().includes(String(b).toLowerCase());default:return fail('SCRIPT_RULE','不支持的比较规则：'+rule)}}
 const sub=(value:ScriptToken|undefined)=>{if(!value)fail('SCRIPT_CLOSURE','需要一个脚本闭包');return value.closure?value.text:expand(value.text)}
 const charged=async(work:()=>Promise<string>)=>{if(++calls>limits.calls)fail('SCRIPT_CALL_LIMIT','脚本已达到允许的模型调用数');return work()}
 const run=async(text:string,depth:number)=>{if(depth>8)fail('SCRIPT_DEPTH','脚本调用超过 8 层');const commands=parseScript(text);let first=true;for(const row of commands){check();if(row.name==='#')continue;if(!scriptCommands.includes(row.name as typeof scriptCommands[number]))fail('SCRIPT_UNSUPPORTED','未支持的命令 /'+row.name+'；原脚本保留，尚未执行此动作');for(const key of Object.keys(row.named))if(!(parameters[row.name]||[]).includes(key))fail('SCRIPT_PARAMETER','未支持 /'+row.name+' 的参数 '+key+'；尚未执行此动作');const args=row.args.map(value=>value.closure?value.text:expand(value.text)),value=args.length?args.join(' '):depth>0&&first?'':pipe,named=(key:string)=>expand(row.named[key]?.text||'');first=false;trace.push({command:row.name,step:steps,calls})
  switch(row.name){
   case'pass':pipe=value;break
   case'echo':await host.echo(value);break
   case'setinput':await host.setInput(value);pipe=value;break
   case'send':await host.append('user',value);pipe=value;break
   case'sys':await host.append('system',value);pipe=value;break
   case'sendas':if(!named('name'))fail('SCRIPT_SPEAKER','/sendas 需要 name=角色');await host.append('assistant',value,named('name'));pipe=value;break
   case'trigger':case'continue':pipe=await charged(()=>host.generate(row.name as 'trigger'|'continue'));break
   case'imagine':pipe=await charged(()=>host.imagine(value));break
   case'getvar':case'getglobalvar':pipe=String(Object.hasOwn(row.name==='getvar'?host.variables:host.globals,value)?(row.name==='getvar'?host.variables:host.globals)[value]:'');break
   case'setvar':case'setglobalvar':case'addvar':case'addglobalvar':{const key=named('key');if(!key||key.length>160||['__proto__','prototype','constructor'].includes(key))fail('SCRIPT_VARIABLE','变量名称无效');const global=row.name.includes('global'),values=global?host.globals:host.variables;if(Object.keys(values).length>=1000&&!Object.hasOwn(values,key))fail('SCRIPT_LIMIT','变量超过 1,000 个');let result:string|number=value;if(row.name.startsWith('add')){const old=values[key]??0;result=Number.isFinite(Number(old))&&Number.isFinite(Number(value))?Number(old)+Number(value):String(old)+value}if(String(result).length>20000||typeof result==='number'&&!Number.isSafeInteger(result))fail('SCRIPT_LIMIT','变量值超过上限');await host.setVariable(key,result,global);values[key]=result;pipe=String(result);break}
   case'if':{const body=compare(row)?row.args[0]:row.named.else;if(body)await run(sub(body),depth+1);break}
   case'times':case'while':{const count=row.name==='times'?Number(args[0]):100;if(!Number.isInteger(count)||count<0||count>100)fail('SCRIPT_LIMIT','循环次数需要在 0—100');const body=row.name==='times'?row.args[1]:row.args[0];let broken=false;for(let n=0;n<count;n++){if(row.name==='while'&&!compare(row))break;if(++iterations>100)fail('SCRIPT_LIMIT','脚本累计循环达到 100 次');index=n;try{await run(sub(body),depth+1)}catch(e){if(e instanceof Flow&&e.kind==='break'){pipe=e.value;broken=true;break}throw e}}if(row.name==='while'&&!broken&&compare(row))fail('SCRIPT_LIMIT','循环未结束，已在 100 次内停止');break}
   case'run':{const target=row.args[0];if(!target)fail('SCRIPT_CLOSURE','需要闭包或快捷按钮名称');const content=target.closure?target.text:await host.namedScript(expand(target.text));if(content===null)fail('SCRIPT_REFERENCE','快捷按钮不存在或已停用');try{await run(content,depth+1)}catch(e){if(e instanceof Flow&&(e.kind==='break'||e.kind==='return'))pipe=e.value;else throw e}break}
   case'break':case'abort':case'return':throw new Flow(row.name,value)
   case'input':pipe=await host.input(value);break
   case'buttons':{let labels:unknown;try{const argument=named('labels'),text=Object.hasOwn(host.variables,argument)?String(host.variables[argument]):Object.hasOwn(host.globals,argument)?String(host.globals[argument]):argument;labels=JSON.parse(text||'[]')}catch{fail('SCRIPT_BUTTONS','按钮 labels 需要 JSON 字符串数组')}if(!Array.isArray(labels)||!labels.length||labels.length>12||labels.some(label=>typeof label!=='string'||label.length>200))fail('SCRIPT_BUTTONS','按钮最多 12 项');pipe=await host.buttons(value,labels as string[]);break}
   case'delay':{const n=Number(value);if(!Number.isFinite(n)||n<0||n>5000)fail('SCRIPT_DELAY','单次等待限于 0—5,000 ms');await new Promise<void>((resolve,reject)=>{const finish=()=>{signal.removeEventListener('abort',abort);resolve()},timer=setTimeout(finish,n),abort=()=>{clearTimeout(timer);signal.removeEventListener('abort',abort);reject(signal.reason)};signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort()});break}
   case'inject':if(!named('id'))fail('SCRIPT_INJECT','注入需要 id=名称');await host.inject(named('id'),value);break
   case'flushinjects':await host.flushInjects(value||undefined);break
  }
  check(false);if(pipe.length>200000)fail('SCRIPT_LIMIT','管道输出超过长度上限')
 }}
 try{await run(source,0)}catch(e){if(!(e instanceof Flow))throw e;pipe=e.value}
 check(false);return {pipe,steps,calls,iterations,trace}
}
