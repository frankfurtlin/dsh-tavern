import type {Anchor} from './types'
export async function quoteHash(text:string,revisionId:string,blockId:string,start:number,end=start){
  if(start<0||end<start||end>text.length||invalidBoundary(text,start)||invalidBoundary(text,end))throw new Error('ANCHOR_INVALID')
  let value:string
  if(start===end){let left=Math.max(0,start-32),right=Math.min(text.length,start+32);if(invalidBoundary(text,left))left--;if(invalidBoundary(text,right))right++;value=JSON.stringify(['anchor-v1',revisionId,blockId,start,text.slice(left,start),text.slice(start,right)])}else value=text.slice(start,end)
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));return Array.from(new Uint8Array(bytes)).map(b=>b.toString(16).padStart(2,'0')).join('')
}
function invalidBoundary(text:string,index:number){const before=text.charCodeAt(index-1),after=text.charCodeAt(index);return before>=0xd800&&before<=0xdbff&&after>=0xdc00&&after<=0xdfff}
export async function selectedAnchor(anchor:Anchor,text:string,start:number,end:number):Promise<Anchor>{return {...anchor,start,end,quoteHash:await quoteHash(text,anchor.revisionId,anchor.blockId,start,end)}}
