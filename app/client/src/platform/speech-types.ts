import type {PluginListenerHandle} from '@capacitor/core'
import type {Anchor,Block,Chapter} from '../api/types'
import type {PublicReadCheckpoint} from '../offline/world-storage'
import type {StoryContext} from '../api/stories'

export type SpeechAnchor=Anchor|{worldId:string;worldLineId:string;checkpointId:string;paragraph:number;start:number;end:number;offsetUnit:'utf16'}|{bookId:string;generationId:string;contentId:string;candidateVersion:number;start:number;end:number;offsetUnit:'utf16';draft:true}
export interface SpeechSegment {id:string;text:string;chapterId:string;chapterTitle:string;anchor:SpeechAnchor}
export interface SpeechPreferences {rate:number;voiceId:string;stopAtChapterEnd:boolean;sleepMinutes:0|5|15|30|60}
export interface SpeechVoice {id:string;name:string;language:string;offline:boolean}
export interface SpeechVoices {voices:SpeechVoice[];available:boolean;initializing:boolean;reason?:string}
export interface SpeechState {
  scopeKey?:string;title?:string;status:'idle'|'ready'|'starting'|'playing'|'paused'|'ended'|'error';reason?:string
  index?:number;count?:number;eventVersion?:number;currentId?:string;chapterId?:string;chapterTitle?:string;currentAnchor?:SpeechAnchor
  lastCompletedId?:string;lastCompletedAnchor?:SpeechAnchor;queueLoaded:boolean;rate?:number;voiceId?:string;resolvedVoiceId?:string;voiceFallback?:boolean;stopAtChapterEnd?:boolean;sleepRemainingMs?:number
}
export interface SpeechDriver {
  voices(options?:{retry?:boolean}):Promise<SpeechVoices>
  loadQueue(options:SpeechPreferences&{scopeKey:string;title:string;segments:SpeechSegment[];fromId?:string}):Promise<SpeechState>
  status(options:{scopeKey:string}):Promise<SpeechState>
  play(options:{scopeKey:string}):Promise<SpeechState>
  pause(options:{scopeKey:string}):Promise<SpeechState>
  skip(options:{scopeKey:string;direction:-1|1}):Promise<SpeechState>
  configure(options:SpeechPreferences&{scopeKey:string}):Promise<SpeechState>
  stop(options:{clearPosition?:boolean}):Promise<unknown>
  openSettings():Promise<{opened:boolean;instructions?:string}>
  addListener(event:'speechState',listener:(state:SpeechState)=>void):Promise<PluginListenerHandle>
}
export interface SpeechContext {storyId:string;mode:'read'|'draft';lineId:string;revisionId:string;bookId?:string;worldId?:string;baseContext?:StoryContext}
export const defaultSpeechPreferences:SpeechPreferences={rate:1,voiceId:'',stopAtChapterEnd:false,sleepMinutes:0}
export function validSpeechPreferences(value:SpeechPreferences){return Number.isFinite(value.rate)&&value.rate>=.75&&value.rate<=2&&typeof value.voiceId==='string'&&value.voiceId.length<=500&&typeof value.stopAtChapterEnd==='boolean'&&[0,5,15,30,60].includes(value.sleepMinutes)}
export function validateSpeechQueue(options:SpeechPreferences&{scopeKey:string;title:string;segments:SpeechSegment[];fromId?:string}){
  if(!validSpeechPreferences(options)||!options.scopeKey||options.scopeKey.length>200||!Array.isArray(options.segments)||!options.segments.length||options.segments.length>20000)throw new Error('SPEECH_QUEUE_INVALID')
  let size=0,anchorSize=0;const ids=new Set<string>()
  for(const s of options.segments){
    if(!s.id||s.id.length>200||ids.has(s.id)||typeof s.text!=='string'||!s.text.trim()||s.text.length>200000||!s.chapterId||typeof s.chapterTitle!=='string'||!s.anchor||s.anchor.offsetUnit!=='utf16'||!Number.isSafeInteger(s.anchor.start)||!Number.isSafeInteger(s.anchor.end)||s.anchor.start<0||s.anchor.start>s.anchor.end||JSON.stringify(s.anchor).length>8192)throw new Error('SPEECH_QUEUE_INVALID')
    const fields=new Set('bookId branchId revisionId chapterId blockId start end offsetUnit quoteHash worldId worldLineId checkpointId paragraph generationId contentId candidateVersion draft'.split(' '))
    if(Object.keys(s.anchor).some(key=>!fields.has(key)))throw new Error('SPEECH_QUEUE_INVALID')
    ids.add(s.id);size+=s.text.length;anchorSize+=JSON.stringify(s.anchor).length
    for(let i=0;i<s.text.length;i++){const unit=s.text.charCodeAt(i);if(unit>=0xd800&&unit<=0xdbff){const next=s.text.charCodeAt(++i);if(!(next>=0xdc00&&next<=0xdfff))throw new Error('SPEECH_QUEUE_INVALID')}else if(unit>=0xdc00&&unit<=0xdfff)throw new Error('SPEECH_QUEUE_INVALID')}
  }
  if(size>2000000||anchorSize>8000000||options.fromId&&!ids.has(options.fromId))throw new Error('SPEECH_QUEUE_INVALID')
}
export function speechChunks(text:string,limit=1800){const chunks:string[]=[];for(let at=0;at<text.length;){let end=Math.min(text.length,at+limit);if(end<text.length&&text.charCodeAt(end-1)>=0xd800&&text.charCodeAt(end-1)<=0xdbff)end--;const part=text.slice(at,end);const punctuation=Math.max(part.lastIndexOf('。'),part.lastIndexOf('！'),part.lastIndexOf('？'),part.lastIndexOf('\n'));if(end<text.length&&punctuation>limit*.4)end=at+punctuation+1;chunks.push(text.slice(at,end));at=end}return chunks}
const publicAnchor=(a:Anchor):Anchor=>({bookId:a.bookId,branchId:a.branchId,revisionId:a.revisionId,chapterId:a.chapterId,blockId:a.blockId,start:a.start,end:a.end,offsetUnit:'utf16',quoteHash:a.quoteHash})
export function readingSpeechSegments(chapter:Pick<Chapter,'id'|'title'|'blocks'>):SpeechSegment[]{return chapter.blocks.filter(b=>b.text.trim()).map((b:Block)=>({id:b.id,text:b.text,chapterId:chapter.id,chapterTitle:chapter.title,anchor:publicAnchor(b.endAnchor)}))}
export function worldSpeechSegments(worldId:string,lineId:string,history:PublicReadCheckpoint[]):SpeechSegment[]{
  return history.flatMap(cp=>{let offset=0,index=0;return cp.text.split(/\n\s*\n/).flatMap(text=>{const start=cp.text.indexOf(text,offset);offset=start+text.length;if(!text.trim())return [];return [{id:cp.id+':'+index,chapterId:cp.id,chapterTitle:cp.title,text,anchor:{worldId,worldLineId:lineId,checkpointId:cp.id,paragraph:index++,start,end:offset,offsetUnit:'utf16' as const}}]})})
}
