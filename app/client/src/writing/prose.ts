/** Parse presentation from a preserved provider stream. Never render JSON or thought scaffolding as prose. */
export interface ProsePresentation {text:string;title:string|null;format:'prose'|'json'|'unrecognized';thoughtOmitted:boolean;pending:boolean}
const bodyKeys=new Set(['text','body','content','正文'])
const titleKeys=new Set(['title','标题'])
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)

function visibleText(raw:string){
 let text='',cursor=0,hidden:string|null=null,omitted=false,pending=false
 while(cursor<raw.length){
  if(raw[cursor]!=='<'){if(!hidden)text+=raw[cursor];cursor++;continue}
  const rest=raw.slice(cursor),tag=rest.match(/^<\s*(\/?)\s*(think|analysis)\s*>/i)
  if(tag){const closing=!!tag[1],name=tag[2]!.toLowerCase();omitted=true;if(!closing&&!hidden)hidden=name;else if(closing&&hidden===name)hidden=null;cursor+=tag[0].length;continue}
  // A split tag is held until its next chunk, preventing a visible <thi flash.
  if(/^<\s*\/?\s*[a-z]*\s*$/i.test(rest)&&['think','analysis'].some(name=>name.startsWith(rest.replace(/^<\s*\/?\s*/,'').trim().toLowerCase()))){pending=true;break}
  if(!hidden)text+='<';cursor++
 }
 return {text,thoughtOmitted:omitted,pending:pending||hidden!==null}
}

/** Decodes only complete escaped characters of one JSON string; an incomplete escape stays buffered. */
function jsonString(raw:string,start:number){
 let value='',index=start+1,closed=false
 for(;index<raw.length;index++){
  const char=raw[index]!
  if(char==='"'){closed=true;index++;break}
  if(char==='\\'){
   const escaped=raw[index+1];if(escaped===undefined)break
   if(escaped==='u'){
    const hex=raw.slice(index+2,index+6);if(hex.length!==4)break;if(!/^[0-9a-f]{4}$/i.test(hex))return null
    const point=parseInt(hex,16);if(point>=0xd800&&point<=0xdbff){const pair=raw.slice(index+6,index+12);if(pair.length<6)break;if(!/^\\u[dD][c-fC-F][0-9a-f]{2}$/.test(pair))return null;value+=String.fromCharCode(point,parseInt(pair.slice(2),16));index+=11;continue}
    if(point>=0xdc00&&point<=0xdfff)return null
    value+=String.fromCharCode(point);index+=5;continue
   }
   const replacements:Record<string,string>={'"':'"','\\':'\\','/':'/','b':'\b','f':'\f','n':'\n','r':'\r','t':'\t'}
   if(!Object.hasOwn(replacements,escaped))return null;value+=replacements[escaped];index++;continue
  }
  if(char<' ')return null
  const point=char.charCodeAt(0);if(point>=0xd800&&point<=0xdbff){if(index+1>=raw.length)break;const low=raw.charCodeAt(index+1);if(low<0xdc00||low>0xdfff)return null;value+=char+raw[++index];continue}if(point>=0xdc00&&point<=0xdfff)return null
  value+=char
 }
 return {value,end:index,closed}
}

function completeObject(value:unknown):{text:string;title:string|null}|null{
 if(!record(value))return null
 const body=[...bodyKeys].filter(key=>typeof value[key]==='string')
 if(body.length!==1)return null
 const text=String(value[body[0]!]);for(let index=0;index<text.length;index++){const point=text.charCodeAt(index);if(point>=0xd800&&point<=0xdbff){const low=text.charCodeAt(++index);if(!(low>=0xdc00&&low<=0xdfff))return null}else if(point>=0xdc00&&point<=0xdfff)return null}
 const title=[...titleKeys].find(key=>typeof value[key]==='string')
 return {text,title:title?String(value[title]).slice(0,1000):null}
}

/** Only top-level whitelisted string fields are streamed. Nested analysis/tool fields are never projected. */
function partialObject(raw:string){
 let index=1,text='',title:string|null=null,pending=true,seenBody=false
 while(index<raw.length){
  while(/\s/.test(raw[index]||'')&&index<raw.length)index++
  if(raw[index]==='}')return {text,title,pending:false}
  if(raw[index]!== '"')break
  const key=jsonString(raw,index);if(!key?.closed)break;index=key.end
  while(/\s/.test(raw[index]||'')&&index<raw.length)index++
  if(raw[index++]!==':')break
  while(/\s/.test(raw[index]||'')&&index<raw.length)index++
  if(raw[index]!== '"')break
  const value=jsonString(raw,index);if(!value)break
  if(bodyKeys.has(key.value)){if(seenBody)return {text:'',title:null,pending:true};seenBody=true;text=value.value}
  if(titleKeys.has(key.value))title=value.value.slice(0,1000)
  if(!value.closed)break;index=value.end
  while(/\s/.test(raw[index]||'')&&index<raw.length)index++
  if(raw[index]===','){index++;continue}
  if(raw[index]==='}'){pending=false;break}
  break
 }
 return {text,title,pending}
}

/** Metadata uses complete, unique, bounded objects; invalid metadata never invents facts. */
export function receivedProseObject(raw:string):Record<string,unknown>|null{
 const stripped=visibleText(raw);if(stripped.pending)return null
 const source=stripped.text.trim().replace(/^```(?:json)?[ \t]*\r?\n/i,'').replace(/\r?\n```\s*$/,'')
 if(!source.startsWith('{')||source.length>1_000_000)return null
 const stack:(Set<string>|null)[]=[]
 for(let i=0;i<source.length;i++){
  const c=source[i]
  if(c==='"'){const str=jsonString(source,i);if(!str?.closed)return null;let next=str.end;while(/\s/.test(source[next]||'')&&next<source.length)next++;const keys=stack.at(-1);if(source[next]===':'&&keys){if(keys.has(str.value))return null;keys.add(str.value)}i=str.end-1}
  else if(c==='{'||c==='['){stack.push(c==='{'?new Set():null);if(stack.length>64)return null}
  else if(c==='}'||c===']'){if(!stack.length)return null;stack.pop()}
 }
 if(stack.length)return null
 try{const value:unknown=JSON.parse(source);return record(value)&&completeObject(value)?value:null}catch{return null}
}

export function parseWritingProse(raw:string):ProsePresentation{
 const stripped=visibleText(raw),leading=stripped.text.trimStart(),fenced=leading.startsWith('```'),fence=fenced?leading.match(/^```(?:json)?[ \t]*\r?\n/i):null
 // A split code-fence header never reaches the reading flow.
 if(fenced&&!fence)return {text:'',title:null,format:'unrecognized',thoughtOmitted:stripped.thoughtOmitted,pending:true}
 const body=fence?leading.slice(fence[0].length).replace(/\r?\n```\s*$/,''):stripped.text
 const structured=body.trimStart()
 if(structured.startsWith('{')||structured.startsWith('[')){
  let parsed:{text:string;title:string|null}|null=null,pending=true
  try{parsed=completeObject(JSON.parse(structured));pending=false}catch{if(structured.startsWith('{')){const partial=partialObject(structured);parsed={text:partial.text,title:partial.title};pending=partial.pending}}
  const clean=visibleText(parsed?.text||'')
  return {text:clean.text,title:parsed?.title||null,format:parsed?'json':'unrecognized',thoughtOmitted:stripped.thoughtOmitted||clean.thoughtOmitted,pending:pending||stripped.pending||clean.pending}
 }
 const heading=body.match(/^\s*(?:#{1,3}\s*)?((?:第[\d零〇一二三四五六七八九十百]+章|第\s*\d+\s*章)[^\n]{0,100})\n/)
 return {text:heading?body.slice(heading[0].length):body,title:heading?.[1]?.trim()||null,format:'prose',thoughtOmitted:stripped.thoughtOmitted,pending:stripped.pending}
}
