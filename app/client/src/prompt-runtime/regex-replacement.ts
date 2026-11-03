/** Replacement evaluation runs in a disposable worker. No eval or Function. */
export function boundedReplace(pattern:string,flags:string,text:string,replacement:string,trim:string[]){
 const expression=new RegExp(pattern,flags),limit=200000;let count=0,size=0,cursor=0;const output:string[]=[]
 const append=(value:string)=>{size+=value.length;if(size>limit)throw new Error('output-limit');output.push(value)}
 const once=(match:RegExpExecArray)=>{if(++count>20000)throw new Error('match-limit');const start=match.index;append(text.slice(cursor,start));let full=match[0];for(const value of trim)if(value)full=full.split(value).join('')
  let offset=0;for(const token of replacement.matchAll(/\{\{match\}\}|\$(\$|&|`|'|<[^>]+>|\d{1,2})/gi)){append(replacement.slice(offset,token.index));const literal=token[0],key=token[1];let value=literal;if(!key||key==='&')value=full;else if(key==='$')value='$';else if(key==='`')value=text.slice(0,start);else if(key==="'")value=text.slice(start+match[0].length);else if(key.startsWith('<'))value=match.groups?(match.groups[key.slice(1,-1)]||''):literal;else {let index=Number(key);if(index>0&&index<match.length)value=match[index]||'';else if(key.length===2){index=Number(key[0]);if(index>0&&index<match.length)value=(match[index]||'')+key[1]}}append(value);offset=token.index+literal.length}append(replacement.slice(offset));cursor=start+match[0].length
 }
 if(expression.global){let match:RegExpExecArray|null;while((match=expression.exec(text))){once(match);if(!match[0].length){const point=text.codePointAt(expression.lastIndex);expression.lastIndex+=expression.unicode&&point!==undefined&&point>65535?2:1}}}
 else {const match=expression.exec(text);if(match)once(match)}append(text.slice(cursor));return {text:output.join(''),count}
}
