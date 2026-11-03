import {canonical,ContractError,sha256,unicodeOffset,type Document,type Paragraph,type ProgressAnchor} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {readingLine} from '../writing/runtime'
import {validateCharacterScope,type CharacterScope,type CharacterSource} from './contract'

export interface CharacterCursor {paragraphId:string;offset:number}
export interface CharacterChunk {scope:CharacterScope;next:CharacterCursor|null;characters:number}

/** A finite slice is mapped back to original UTF16 offsets, including long paragraphs. */
export async function characterChunk(vault:LocalVault,storyId:string,branchId:string|null,anchor:ProgressAnchor,cursor?:CharacterCursor):Promise<CharacterChunk|null>{
 const {story,branch,line}=await readingLine(vault,storyId,branchId),ids=line.value.paragraphIds,end=ids.indexOf(anchor.paragraphId)
 if(anchor.storyId!==storyId||anchor.branchId!==branchId||end<0)throw new ContractError('CHARACTER_CHUNK','人物整理终点不属于这条故事线')
 const last=await vault.read<Paragraph>(anchor.paragraphId);if(!last||anchor.offset<0||anchor.offset>last.value.text.length||unicodeOffset(last.value.text,anchor.offset)!==anchor.offset)throw new ContractError('CHARACTER_CHUNK','人物整理终点无效')
 if(cursor!==undefined&&(!cursor||typeof cursor.paragraphId!=='string'||!cursor.paragraphId||!Number.isSafeInteger(cursor.offset)||cursor.offset<0))throw new ContractError('CHARACTER_CHUNK','人物整理游标无效')
 let index=cursor!==undefined?ids.indexOf(cursor.paragraphId):0,offset=cursor!==undefined?cursor.offset:0,budget=48000
 if(index<0||index>end||!Number.isSafeInteger(offset)||offset<0)throw new ContractError('CHARACTER_CHUNK','人物整理游标无效')
 const sources:CharacterSource[]=[]
 while(index<=end&&budget>0&&sources.length<16){
  const row=await vault.read<Paragraph>(ids[index]!);if(!row||row.kind!=='paragraph')throw new ContractError('CHARACTER_CHUNK','原文段落缺失')
  const limit=index===end?anchor.offset:row.value.text.length
  if(offset>limit||unicodeOffset(row.value.text,offset)!==offset)throw new ContractError('CHARACTER_CHUNK','人物整理游标越过原文或终点')
  if(offset===limit){index++;offset=0;continue}
  const finish=unicodeOffset(row.value.text,Math.min(limit,offset+budget))
  if(finish===offset)break
  sources.push(await characterSlice(row,offset,finish));budget-=finish-offset;offset=finish
  if(offset===limit){index++;offset=0}
 }
 if(!sources.length)return null
 const scope:CharacterScope={schema:'novel-character-scope-v2',storyId,storyRevision:story.revision,branchId,branchRevision:branch?.revision||null,anchor:{...anchor},sources}
 validateCharacterScope(scope)
 return {scope,next:index>end?null:{paragraphId:ids[index]!,offset},characters:48000-budget}
}

export async function characterSlice(row:Document<Paragraph>,start:number,end:number):Promise<CharacterSource>{
 const text=row.value.text.slice(start,end),inputId=start===0&&end===row.value.text.length?row.id:'character-source:'+await sha256(canonical({id:row.id,revision:row.revision,start,limit:end,schema:'novel-character-scope-v2'}))
 return {id:row.id,revision:row.revision,inputId,start,limit:end,text,sliceHash:await sha256(text)}
}
