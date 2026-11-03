import type {StoryContext} from './stories'
import type {Progress,ReadingSettings} from './types'
import type {SpeechAnchor,SpeechPreferences} from '../platform/speech-types'
import {validSpeechPreferences} from '../platform/speech-types'

export type PlaybackKind='reader'|'world'|'draft'
export interface PlaybackScope {context:StoryContext;kind:PlaybackKind;mode:'read'|'draft';lineId:string;contentRevision:string}
export interface PlaybackPosition extends PlaybackScope {paragraphId:string;lastCompletedAnchor:SpeechAnchor;maxHeardAnchor:SpeechAnchor;ended:boolean;completedAt:string;updatedAt:string}
export interface PlaybackSnapshot {version:number;position:PlaybackPosition|null}
export interface PlaybackInput extends PlaybackScope {expectedVersion:number;paragraphId:string;lastCompletedAnchor:SpeechAnchor;completed:true;ended:boolean;expectedProgressVersion?:number}
export interface PlaybackIntent {requestId:string;body:PlaybackInput}
export interface PlaybackReceipt extends PlaybackSnapshot {requestId:string;readAdvance:'not-requested'|'advanced'|'already-observed';readerProgress?:Progress}
export interface CloudReadingPreferences extends Partial<ReadingSettings> {version:number;editVersion:number;speech:SpeechPreferences}
export type PlaybackRequest=<T>(path:string,options?:{method?:string;body?:unknown;key?:string})=>Promise<T>
const integer=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0
export const samePlaybackAnchor=(a:SpeechAnchor,b:SpeechAnchor)=>{const left=a as unknown as Record<string,unknown>,right=b as unknown as Record<string,unknown>;return Object.keys(left).length===Object.keys(right).length&&Object.keys(left).every(key=>left[key]===right[key])}
const fail=()=>{throw new Error('听书回执与当前正文不符')}
export function playbackPath(scope:PlaybackScope){return '/playback/position?'+new URLSearchParams({storyId:scope.context.storyId,kind:scope.kind,mode:scope.mode,lineId:scope.lineId,contentRevision:scope.contentRevision})}
export function playbackAnchorMatches(value:SpeechAnchor,scope:PlaybackScope){
 if(!value||value.offsetUnit!=='utf16'||!integer(value.start)||!integer(value.end)||value.start>value.end)return false
 if(scope.kind==='reader')return 'branchId'in value&&value.bookId===scope.context.bookId&&value.branchId===scope.lineId&&value.revisionId===scope.contentRevision
 if(scope.kind==='world')return 'worldId'in value&&value.worldId===scope.context.worldId&&value.worldLineId===scope.lineId&&integer(value.paragraph)
 const [generation,version]=scope.contentRevision.split(':');return 'draft'in value&&value.draft===true&&value.bookId===scope.context.bookId&&value.generationId===generation&&value.candidateVersion===Number(version)
}
export function validatePlayback(value:PlaybackSnapshot,scope:PlaybackScope){
 if(!value||!integer(value.version))return fail()
 const p=value.position
 if(p===null){if(value.version!==0)return fail();return value}
 if(!p||p.kind!==scope.kind||p.mode!==scope.mode||p.lineId!==scope.lineId||p.contentRevision!==scope.contentRevision||!p.context||p.context.storyId!==scope.context.storyId||p.context.mode!=='read'||p.context.perspective!=='reader'||!p.paragraphId||typeof p.ended!=='boolean'||typeof p.completedAt!=='string'||typeof p.updatedAt!=='string')return fail()
 if(scope.kind==='world'?(p.context.worldId!==scope.context.worldId||p.context.worldLineId!==scope.lineId):(p.context.bookId!==scope.context.bookId||p.context.readerBranchId!==scope.lineId))return fail()
 if(!playbackAnchorMatches(p.lastCompletedAnchor,scope)||!playbackAnchorMatches(p.maxHeardAnchor,scope))return fail()
 if(scope.kind==='reader'&&'blockId'in p.lastCompletedAnchor&&p.paragraphId!==p.lastCompletedAnchor.blockId)return fail()
 if(scope.kind==='world'&&'checkpointId'in p.lastCompletedAnchor&&p.paragraphId!==p.lastCompletedAnchor.checkpointId+':'+p.lastCompletedAnchor.paragraph)return fail()
 if(scope.kind==='draft'&&'contentId'in p.lastCompletedAnchor&&p.paragraphId!==p.lastCompletedAnchor.contentId)return fail()
 return value
}
export function validatePlaybackReceipt(value:PlaybackReceipt,intent:PlaybackIntent){
 validatePlayback(value,intent.body)
 if(!value.position||value.requestId!==intent.requestId||value.version!==intent.body.expectedVersion+1||value.position.paragraphId!==intent.body.paragraphId||!samePlaybackAnchor(value.position.lastCompletedAnchor,intent.body.lastCompletedAnchor)||value.position.ended!==intent.body.ended)return fail()
 if(!['not-requested','advanced','already-observed'].includes(value.readAdvance))return fail()
 if(intent.body.expectedProgressVersion===undefined){if(value.readAdvance!=='not-requested'||value.readerProgress!==undefined)return fail()}
 else{
   const p=value.readerProgress
   if(!p||p.version!==intent.body.expectedProgressVersion+(value.readAdvance==='advanced'?1:0)||p.revisionId!==intent.body.contentRevision||!p.anchor||!p.maxObservedAnchor||!playbackAnchorMatches(p.anchor,intent.body)||!playbackAnchorMatches(p.maxObservedAnchor,intent.body)||value.readAdvance==='not-requested')return fail()
 }
 return value
}
export function validateCloudPreferences(value:CloudReadingPreferences){
 if(!value||!integer(value.version)||value.version<1||value.editVersion!==value.version||!value.speech||!validSpeechPreferences(value.speech))throw new Error('听书偏好格式无效')
 return value
}
