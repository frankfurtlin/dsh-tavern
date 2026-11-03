function boundary(text:string,offset:number){
  if(!Number.isInteger(offset)||offset<0||offset>text.length)throw new Error('ANCHOR_INVALID')
  const a=text.charCodeAt(offset-1),b=text.charCodeAt(offset)
  if(a>=0xd800&&a<=0xdbff&&b>=0xdc00&&b<=0xdfff)throw new Error('ANCHOR_INVALID')
}
// Sanitized EPUB markup can contribute extra whitespace between inline nodes.
// Match the actual characters; never map a different text revision by proximity.
export function renderedOffset(canonical:string,rendered:string,offset:number):number{
  boundary(canonical,offset)
  if(canonical===rendered)return offset
  const from:{char:string;end:number}[]=[],to:{char:string;start:number;end:number}[]=[]
  let index=0
  for(const char of canonical){index+=char.length;if(!/\s/u.test(char))from.push({char,end:index})}
  index=0
  for(const char of rendered){const start=index;index+=char.length;if(!/\s/u.test(char))to.push({char,start,end:index})}
  if(from.length!==to.length||from.some((entry,i)=>entry.char!==to[i]!.char))throw new Error('ANCHOR_INVALID')
  if(offset===canonical.length)return rendered.length
  const ordinal=from.findIndex(entry=>entry.end>offset)
  return ordinal<0?rendered.length:to[ordinal]!.start
}
export function canonicalOffset(canonical:string,rendered:string,offset:number):number{
  return renderedOffset(rendered,canonical,offset)
}
export function textBoundaries(text:string){const values=[0];let offset=0;for(const char of text){offset+=char.length;values.push(offset)}return values}
