import {ContractError} from '../domain/local-contract'
import {parseWritingProse} from './prose'
import type {WritingChapter} from './contract'
/** The entire readable chapter, in order, is the only admissible acceptance projection. */
export function acceptedWritingParagraphs(chapter:Pick<WritingChapter,'text'|'raw'>){
 const body=(chapter.raw!==undefined?chapter.text:parseWritingProse(chapter.text).text).replace(/^\s*(?:#{1,3}\s*)?(?:第[\d零〇一二三四五六七八九十百]+章|第\s*\d+\s*章)[^\n]{0,100}\n/,'').trim()
 const parts=body.split(/\n\s*\n|\n/).map(p=>p.trim()).filter(Boolean)
 if(!parts.length)throw new ContractError('NO_OUTPUT','本章没有正文，原返回保留')
 let start=0
 return parts.map(text=>{const paragraph={text,sourceStart:start,sourceEnd:start+text.length};start+=text.length+2;return paragraph})
}
