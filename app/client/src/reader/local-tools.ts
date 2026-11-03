import {unicodeOffset,type Document,type Paragraph,type LocalStory,type ProgressAnchor} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
export interface LocalNote {anchor:ProgressAnchor;text:string;quote?:string;group?:string;createdAt:string;retired?:boolean;conflictOf?:string}
export interface SearchHit {anchor:ProgressAnchor;chapter:string;excerpt:string}
export async function searchStory(vault:LocalVault,story:Document<LocalStory>,query:string,signal:AbortSignal,onProgress:(fraction:number)=>void,branchId:string|null=null){
  const hits:SearchHit[]=[],chapters=new Map(story.value.chapters.map(chapter=>[chapter.id,chapter.title]));let total=0
  if(!query||query.length>500)return {hits,total}
  for(let index=0;index<story.value.paragraphIds.length;index+=128){
    signal.throwIfAborted();const rows=await vault.readMany<Paragraph>(story.value.paragraphIds.slice(index,index+128));signal.throwIfAborted()
    for(const row of rows){let start=0,offset:number;while((offset=row.value.text.indexOf(query,start))!==-1){total++;if(hits.length<200)hits.push({anchor:{storyId:story.id,branchId,paragraphId:row.id,offset:unicodeOffset(row.value.text,offset)},chapter:chapters.get(row.value.chapterId)||'',excerpt:row.value.text.slice(unicodeOffset(row.value.text,Math.max(0,offset-24)),unicodeOffset(row.value.text,Math.min(row.value.text.length,offset+query.length+60)))});start=offset+query.length}}
    onProgress(Math.min(1,(index+rows.length)/story.value.paragraphIds.length));await new Promise<void>(resolve=>setTimeout(resolve,0))
  }
  signal.throwIfAborted();return {hits,total}
}
export type PagingMode='scroll'|'curl'|'cover'|'slide'|'none'
export interface ReadingPreferences {theme:'light'|'dark';fontSize:number;paper:'warm'|'white'|'cream'|'green'|'rose'|'theme';font:'serif'|'sans'|'system';lineHeight:number;paragraphGap:number;lineWidth:number;pagingMode:PagingMode;doublePage:boolean}
export const readingDefaults:ReadingPreferences={theme:'light',fontSize:19,paper:'warm',font:'serif',lineHeight:1.85,paragraphGap:1.5,lineWidth:720,pagingMode:'scroll',doublePage:false}
const bound=(value:unknown,min:number,max:number,fallback:number)=>typeof value==='number'&&Number.isFinite(value)?Math.max(min,Math.min(max,value)):fallback
export function readingPreferences(value:unknown):ReadingPreferences{
  const v=(value&&typeof value==='object'?value:{}) as Record<string,unknown>
  return {theme:v.theme==='dark'?'dark':'light',fontSize:bound(v.fontSize,14,36,19),paper:['warm','white','cream','green','rose','theme'].includes(String(v.paper))?v.paper as ReadingPreferences['paper']:'warm',font:['serif','sans','system'].includes(String(v.font))?v.font as ReadingPreferences['font']:'serif',lineHeight:bound(v.lineHeight,1.3,2.8,1.85),paragraphGap:bound(v.paragraphGap,.5,3,1.5),lineWidth:bound(v.lineWidth,400,1100,720),pagingMode:['scroll','curl','cover','slide','none'].includes(String(v.pagingMode))?v.pagingMode as PagingMode:'scroll',doublePage:v.doublePage===true}
}
export const readingPaper={warm:'#F6EFDF',white:'#FFFCF5',cream:'#F4E5C5',green:'#E4EDDF',rose:'#F4E3DF',theme:'var(--paper)'}
export const readingFonts={serif:'"Noto Serif SC","Songti SC",SimSun,serif',sans:'Inter,"PingFang SC","Microsoft YaHei",sans-serif',system:'system-ui,sans-serif'}
