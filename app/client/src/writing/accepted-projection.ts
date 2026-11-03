import {ContractError} from '../domain/local-contract'

export type AcceptanceStyle='local-v1'|'server-v1'
export interface AcceptedTextSegment {text:string;sourceStart:number;sourceEnd:number;targetStart:0;targetEnd:number}
export interface AcceptedTextProjection {schema:'accepted-projection-v1';style:AcceptanceStyle;segments:AcceptedTextSegment[]}
// Match the existing acceptance algorithms explicitly, including their different
// whitespace and line-break rules. All positions refer to the original body.
const jsSpace='\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
const pythonSpace='\t\n\v\f\r\u001c-\u001f \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000'
const localTrim=new RegExp('^['+jsSpace+']+|['+jsSpace+']+$','g')
const localLeading=new RegExp('^['+jsSpace+']+')
const emptyServer=new RegExp('^['+pythonSpace+']*$')
export function projectionGapIsWhitespace(text:string,style:AcceptanceStyle){return (style==='server-v1'?emptyServer:new RegExp('^['+jsSpace+']*$')).test(text)}
const heading=new RegExp('^['+jsSpace+']*(?:#{1,3}['+jsSpace+']*)?(?:第[0-9零〇一二三四五六七八九十百]+章|第['+jsSpace+']*[0-9]+['+jsSpace+']*章)([^\n]*)\n')
function fail():never{throw new ContractError('ACCEPTED_PROJECTION','接受正文的位置映射不一致，原文仍保留')}
function readable(text:unknown):asserts text is string{
 if(typeof text!=='string'||text.length>6_000_000)fail()
 for(let i=0;i<text.length;i++){const n=text.charCodeAt(i);if(n>=0xd800&&n<=0xdbff){const next=text.charCodeAt(++i);if(!(next>=0xdc00&&next<=0xdfff))fail()}else if(n>=0xdc00&&n<=0xdfff)fail()}
}
/** A pure, reproducible map. This function neither accepts nor reads a book. */
export function projectAcceptedText(text:string,style:AcceptanceStyle):AcceptedTextProjection{
 readable(text);if(style!=='local-v1'&&style!=='server-v1')fail()
 const title=style==='local-v1'?text.match(heading):null,base=title&&title[1]!.length<=100?title[0].length:0,body=text.slice(base)
 const delimiter=style==='local-v1'?new RegExp('\n['+jsSpace+']*\n|\n','g'):/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/g
 const segments:AcceptedTextSegment[]=[]
 const add=(start:number,end:number)=>{
  const raw=body.slice(start,end),value=style==='local-v1'?raw.replace(localTrim,''):raw
  if(style==='local-v1'?!value:emptyServer.test(value))return
  const sourceStart=base+start+(style==='local-v1'?(raw.match(localLeading)?.[0].length||0):0)
  segments.push({text:value,sourceStart,sourceEnd:sourceStart+value.length,targetStart:0,targetEnd:value.length})
 }
 let start=0
 for(const match of body.matchAll(delimiter)){add(start,match.index!);start=match.index!+match[0].length}
 add(start,body.length)
 return {schema:'accepted-projection-v1',style,segments}
}
export function validateAcceptedProjection(value:unknown,text:string,style:AcceptanceStyle):asserts value is AcceptedTextProjection{
 const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
 const expected=projectAcceptedText(text,style)
 if(!record(value)||Object.keys(value).length!==3||value.schema!==expected.schema||value.style!==style||!Array.isArray(value.segments)||value.segments.length!==expected.segments.length)fail()
 for(const [index,part] of value.segments.entries()){
  const wanted=expected.segments[index]!
  if(!record(part)||Object.keys(part).length!==5||Object.entries(wanted).some(([key,item])=>!Object.hasOwn(part,key)||part[key]!==item))fail()
 }
}
