import {ref} from 'vue'
import {api,newKey} from '../api'
import type {Progress} from '../api/types'
import {playbackPath,playbackAnchorMatches,samePlaybackAnchor,validatePlayback,validatePlaybackReceipt,validateCloudPreferences,type PlaybackScope,type PlaybackSnapshot,type PlaybackIntent,type PlaybackReceipt,type CloudReadingPreferences,type PlaybackRequest} from '../api/playback'
import {cacheGet,cacheSet} from './bridge'
import {validSpeechPreferences,type SpeechAnchor,type SpeechContext,type SpeechPreferences,type SpeechSegment} from './speech-types'
export interface SpeechCloudOptions {request?:PlaybackRequest;progressVersion?:()=>number;onReaderProgress?:(value:Progress,expectedVersion:number)=>void;onReadingPreferences?:(value:CloudReadingPreferences)=>void}
type SaveStatus='local'|'ready'|'saving'|'saved'|'unknown'|'conflict'|'error'
interface CloudCompletion {paragraphId:string;anchor:SpeechAnchor;ended:boolean}
export function createSpeechCloud(context:SpeechContext,active:()=>boolean,options:SpeechCloudOptions={}){
 const scope:PlaybackScope={context:context.baseContext!,kind:context.mode==='draft'?'draft':context.worldId?'world':'reader',mode:context.mode,lineId:context.lineId,contentRevision:context.revisionId}
 const request:PlaybackRequest=options.request||api
 const supported=ref(false),status=ref<SaveStatus>('local'),preferenceStatus=ref<SaveStatus>('local'),busy=ref(false),preferenceBusy=ref(false),message=ref(''),preferenceMessage=ref(''),preferences=ref<SpeechPreferences|null>(null),position=ref<PlaybackSnapshot>({version:0,position:null}),pending=ref<PlaybackIntent|null>(null),completion=ref<CloudCompletion|null>(null)
 const key=`speech-cloud-position:${scope.kind}:${scope.lineId}:${scope.contentRevision}`,prefKey='speech-cloud-preferences'
 const metadata=context.worldId?{worldId:context.worldId,lineId:context.lineId}:{bookId:context.bookId}
 let disposed=false,readEpoch=0,writeEpoch=0,prefEpoch=0,settingsVersion=0,latest:CloudCompletion|null=null,dirtyPreferences:SpeechPreferences|null=null,persistence:Promise<unknown>=Promise.resolve()
 const valid=()=>!disposed&&active(),unknown=(error:unknown)=>(error as {status?:number})?.status===0||!(typeof(error as {status?:unknown})?.status==='number')||(error as {status:number}).status>=500
 const persist=(value:PlaybackIntent|null)=>{const work=persistence.catch(()=>{}).then(async()=>{if(valid())await cacheSet(key,JSON.parse(JSON.stringify({...metadata,intent:value,completion:latest})))});persistence=work;return work}
 function matchesPending(value:PlaybackIntent){return !!value.requestId&&value.body?.kind===scope.kind&&value.body.mode===scope.mode&&value.body.lineId===scope.lineId&&value.body.contentRevision===scope.contentRevision&&value.body.context?.mode==='read'&&playbackAnchorMatches(value.body.lastCompletedAnchor,value.body)}
 async function loadPreferences(){const version=++prefEpoch;const value=validateCloudPreferences(await request<CloudReadingPreferences>('/settings/reading'));if(!valid()||version!==prefEpoch)return;settingsVersion=value.editVersion;options.onReadingPreferences?.(value);preferences.value=dirtyPreferences||{...value.speech};preferenceStatus.value=dirtyPreferences?'local':'saved';preferenceMessage.value=dirtyPreferences?'本机偏好待保存':'偏好已按账号保存'}
 async function refreshPosition(){
  if(!valid()||!supported.value||busy.value||pending.value)return
  const version=++readEpoch;busy.value=true
  try{
   const value=validatePlayback(await request<PlaybackSnapshot>(playbackPath(scope)),scope);if(!valid()||version!==readEpoch)return
   if(scope.kind==='reader'&&options.progressVersion){const expected=options.progressVersion(),progress=await request<Progress>(`/books/${scope.context.bookId}/progress?branchId=${scope.lineId}&revisionId=${scope.contentRevision}`);if(!valid()||version!==readEpoch)return;if(!Number.isSafeInteger(progress.version)||progress.version<0||progress.revisionId&&progress.revisionId!==scope.contentRevision||progress.anchor&&!playbackAnchorMatches(progress.anchor,scope)||progress.maxObservedAnchor&&!playbackAnchorMatches(progress.maxObservedAnchor,scope))throw new Error('progress scope');options.onReaderProgress?.(progress,expected)}
   position.value=value;status.value=latest?'ready':'saved';message.value=latest?'本机已听位置保留，请明确保存':'账号听书位置已载入'
  }
  catch{if(valid()&&version===readEpoch){status.value='error';message.value='暂时无法读取账号听书位置'}}finally{if(valid()&&version===readEpoch)busy.value=false}
 }
 async function initialize(){
  if(!valid())return
  const version=++readEpoch
  try{
   const caps=await request<{ready:boolean;protocol:string}>('/playback/capabilities');if(!valid()||version!==readEpoch)return
   if(caps.ready!==true||caps.protocol!=='reader-playback-v1')return;supported.value=true
   const local=await cacheGet<{intent:PlaybackIntent|null;completion?:CloudCompletion}>(key);if(!valid()||version!==readEpoch)return
   if(local?.completion&&typeof local.completion.paragraphId==='string'&&typeof local.completion.ended==='boolean'&&playbackAnchorMatches(local.completion.anchor,scope)){latest=local.completion;completion.value=latest}
   if(local?.intent&&matchesPending(local.intent)){pending.value=local.intent;status.value='unknown';message.value='正在确认原听书保存请求'}
   const draft=await cacheGet<SpeechPreferences>(prefKey);if(!valid()||version!==readEpoch)return
   if(draft&&validSpeechPreferences(draft)){dirtyPreferences=draft;preferences.value={...draft}}
   await loadPreferences();if(!valid()||version!==readEpoch)return
   const value=validatePlayback(await request<PlaybackSnapshot>(playbackPath(scope)),scope);if(!valid()||version!==readEpoch)return;position.value=value
   if(pending.value)await recover();else{status.value='ready';if(latest)message.value='本机已听位置保留，请明确保存'}
  }catch{if(valid()&&version===readEpoch){message.value='云端暂不可用，已听位置仍保存在本机';if(!pending.value)status.value='local'}}
 }
 async function finish(receipt:PlaybackReceipt,intent:PlaybackIntent,version:number){
  validatePlaybackReceipt(receipt,intent)
  if(latest?.paragraphId===intent.body.paragraphId&&latest.ended===intent.body.ended&&samePlaybackAnchor(latest.anchor,intent.body.lastCompletedAnchor)){latest=null;completion.value=null}
  await persist(null);if(!valid()||version!==writeEpoch)return false
  pending.value=null;if(receipt.version>=position.value.version)position.value=receipt;status.value='saved';message.value='已听位置已按账号保存'
  if(receipt.readerProgress&&intent.body.expectedProgressVersion!==undefined)options.onReaderProgress?.(receipt.readerProgress,intent.body.expectedProgressVersion)
  return true
 }
 async function saveLatest(){
  if(!valid()||!supported.value||busy.value||pending.value||!latest)return
  const value=latest,version=++writeEpoch,body={...scope,expectedVersion:position.value.version,paragraphId:value.paragraphId,lastCompletedAnchor:value.anchor,completed:true as const,ended:value.ended,...(scope.kind==='reader'&&options.progressVersion?{expectedProgressVersion:options.progressVersion()}: {})},intent={requestId:newKey(),body}
  busy.value=true;status.value='saving';message.value='正在保存已听位置';pending.value=intent
  let sent=false
  try{
   await persist(intent);if(!valid()||version!==writeEpoch)return
   sent=true;const receipt=await request<PlaybackReceipt>('/playback/position',{method:'PUT',body,key:intent.requestId});if(!valid()||version!==writeEpoch)return
   if(await finish(receipt,intent,version)&&latest===value)latest=null
  }catch(error){
   if(!valid()||version!==writeEpoch)return
   if(!sent){pending.value=null;status.value='error';message.value='本机保存请求失败，尚未发送；已听段落仍保留'}
   else if(unknown(error)){status.value='unknown';message.value='保存结果尚未确认，已听段落保留；请确认原请求'}
   else{await persist(null).catch(()=>{});if(!valid()||version!==writeEpoch)return;pending.value=null;status.value=(error as {status?:number}).status===409?'conflict':'error';message.value=status.value==='conflict'?'另一设备更新了位置，本机已听段落保留':'这次已听位置未保存，本机段落仍保留'}
  }finally{if(valid()&&version===writeEpoch){busy.value=false;if(!pending.value&&(status.value as SaveStatus)==='saved'&&latest)void saveLatest()}}
 }
 function completed(segment:SpeechSegment,ended:boolean){if(!valid()||!playbackAnchorMatches(segment.anchor,scope))return;latest={paragraphId:segment.id,anchor:segment.anchor,ended};completion.value=latest;if(status.value!=='conflict'&&status.value!=='error'&&!busy.value&&!pending.value)void saveLatest();else void persist(pending.value).catch(()=>{if(valid())message.value='本机已听标记暂未写入，段落仍保留在本页'})}
 async function recover(){
  const intent=pending.value;if(!valid()||!supported.value||busy.value||!intent)return
  const version=++writeEpoch;busy.value=true
  try{const result=await request<{found:boolean;receipt?:PlaybackReceipt}>('/playback/request-status?'+new URLSearchParams({requestId:intent.requestId,storyId:intent.body.context.storyId}));if(!valid()||version!==writeEpoch)return;if(!result.found){status.value='unknown';message.value='原请求尚未找到，请稍后再次确认';return}if(!result.receipt)throw new Error('invalid receipt');await finish(result.receipt,intent,version)}
  catch{if(valid()&&version===writeEpoch){status.value='unknown';message.value='暂时无法确认原请求，未再次提交'}}finally{if(valid()&&version===writeEpoch)busy.value=false}
 }
 async function configure(value:SpeechPreferences){
  if(!valid())return;dirtyPreferences={...value};preferences.value={...value};preferenceStatus.value='local';preferenceMessage.value='本机偏好待保存'
  try{await cacheSet(prefKey,value)}catch{if(valid()){preferenceStatus.value='error';preferenceMessage.value='本机偏好未保存，当前声音设置仍保留'}return}
  if(!valid()||!supported.value)return;await savePreferences()
 }
 async function savePreferences(){
  if(!valid()||!supported.value||!dirtyPreferences||preferenceBusy.value)return
  const draft=dirtyPreferences,version=++prefEpoch;preferenceBusy.value=true;preferenceStatus.value='saving'
  try{const value=validateCloudPreferences(await request<CloudReadingPreferences>('/settings/reading',{method:'PATCH',body:{expectedVersion:settingsVersion,speech:draft}}));if(!valid()||version!==prefEpoch)return;settingsVersion=value.version;options.onReadingPreferences?.(value);if(dirtyPreferences===draft){dirtyPreferences=null;await cacheSet(prefKey,null);if(!valid()||version!==prefEpoch)return};preferenceStatus.value=dirtyPreferences?'local':'saved';preferenceMessage.value=dirtyPreferences?'本机偏好待保存':'偏好已按账号保存'}
  catch(error){if(valid()&&version===prefEpoch){preferenceStatus.value=(error as {status?:number}).status===409?'conflict':'unknown';preferenceMessage.value=preferenceStatus.value==='conflict'?'另一设备更新了设置，本机偏好保留':'偏好保存结果尚未确认，请载入最新信息'}}
  finally{if(valid()&&version===prefEpoch){preferenceBusy.value=false;if(dirtyPreferences&&(preferenceStatus.value as SaveStatus)==='local')void savePreferences()}}
 }
 async function refreshPreferences(){if(!valid()||!supported.value||preferenceBusy.value)return;try{await loadPreferences()}catch{if(valid())preferenceMessage.value='暂时无法读取账号偏好，本机设置保留'}}
 function dispose(){disposed=true;readEpoch++;writeEpoch++;prefEpoch++;busy.value=false;preferenceBusy.value=false;supported.value=false;position.value={version:0,position:null};pending.value=null;completion.value=null}
 return{supported,status,preferenceStatus,busy,preferenceBusy,message,preferenceMessage,preferences,position,pending,completion,initialize,completed,saveLatest,recover,refreshPosition,configure,savePreferences,refreshPreferences,dispose}
}
export type SpeechCloud=ReturnType<typeof createSpeechCloud>
