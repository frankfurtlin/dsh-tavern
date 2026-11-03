import {ref,computed} from 'vue'
import {authGeneration} from '../api'
import {accountId,backendBase,cacheGet,cacheSet,native} from './bridge'
import {isBookDeleted} from './book-cache'
import {isWorldLineDeleted} from './world-cache'
import {systemSpeech} from './system-speech'
import {createSpeechCloud,type SpeechCloudOptions} from './speech-cloud'
import {samePlaybackAnchor} from '../api/playback'
import {defaultSpeechPreferences,validateSpeechQueue,validSpeechPreferences,type SpeechContext,type SpeechSegment,type SpeechPreferences,type SpeechState,type SpeechDriver,type SpeechVoice} from './speech-types'

const leases=new WeakMap<SpeechDriver,number>(),work=new WeakMap<SpeechDriver,Promise<unknown>>()
export interface SpeechControllerOptions extends SpeechCloudOptions {
  onReaderObserve?:(segment:SpeechSegment,active:()=>boolean)=>void|Promise<void>
}
export function speechReason(reason:string|undefined){return ({NO_SYSTEM_VOICE:'设备还没有可用声音，请安装系统语音后重试。',NO_CHINESE_SYSTEM_VOICE:'设备还没有可用中文声音，请在系统设置中安装。',SYSTEM_SPEECH_UNAVAILABLE:'当前浏览器没有系统语音能力。',SYSTEM_SPEECH_START_TIMEOUT:'语音尚未开始，请检查系统声音后重试。',SPEECH_POSITION_SAVE_FAILED:'听书位置暂未保存，已听段落仍保留在本页。',SPEECH_POSITION_READ_FAILED:'暂时无法恢复本机听书位置。',SYSTEM_SPEECH_FAILED:'系统语音暂时不可用，可以重试。',NATIVE_SPEECH_PROTOCOL_UNAVAILABLE:'此安装包尚未提供安全的原生听书通道。','process-restored':'已恢复本机位置，点击播放继续。','chapter-end':'本章已听完。','sleep-timer':'定时时间已到。','page-hidden':'已暂停，返回前台后可以继续。'} as Record<string,string>)[reason||'']|| (reason&& !['paused','selected','preferences','queue-end','stopped'].includes(reason)?'系统语音已暂停，请重试或检查系统设置。':'')}
export function createSpeechController(context:SpeechContext,title:string,segments:SpeechSegment[],scope:()=>boolean=()=>true,driver:SpeechDriver=systemSpeech(),cloudOptions:SpeechControllerOptions={}){
  const owner=accountId(),base=backendBase(),auth=authGeneration(),lease=(leases.get(driver)||0)+1;leases.set(driver,lease)
  const state=ref<SpeechState>({status:'idle',queueLoaded:false}),preferences=ref<SpeechPreferences>({...defaultSpeechPreferences}),voices=ref<SpeechVoice[]>([]),available=ref(false),loading=ref(true),busy=ref(false),error=ref(''),instructions=ref(''),lastCompleted=ref<SpeechSegment|null>(null)
  const byId=new Map(segments.map(s=>[s.id,s])),playing=computed(()=>['starting','playing'].includes(state.value.status))
  let disposed=false,key='',command=0,eventVersion=-1,listener:{remove:()=>Promise<void>}|undefined,rememberedCurrent='',rememberedEnded=false,completedId='',voiceRequest:Promise<void>|undefined
  const own=()=>leases.get(driver)===lease
  const valid=()=>!disposed&&own()&&scope()&&!!owner&&owner===accountId()&&base===backendBase()&&auth===authGeneration()&&(!context.bookId||!isBookDeleted(context.bookId))&&(!context.worldId||!isWorldLineDeleted(context.worldId,context.lineId))
  const cloud=context.baseContext?createSpeechCloud(context,valid,cloudOptions):null
  const positionKey=()=>`${context.worldId?'world-speech-position':'speech-position'}:${key}`
  const prefKey='system-speech-preferences:'+base
  function enqueue<T>(operation:()=>Promise<T>,guard:()=>boolean=valid):Promise<T|undefined>{const next=(work.get(driver)||Promise.resolve()).catch(()=>{}).then(()=>guard()?operation():undefined);work.set(driver,next);return next}
  function metadata(){return context.worldId?{kind:'world',worldId:context.worldId,lineId:context.lineId,headCheckpointId:context.revisionId}:{bookId:context.bookId}}
  async function remember(){
    if(!valid()||!key)return
    try{await cacheSet(positionKey(),{...metadata(),scopeKey:key,lastCompletedId:lastCompleted.value?.id||'',currentId:state.value.currentId||rememberedCurrent,ended:state.value.status==='ended',anchor:lastCompleted.value?.anchor||null})}catch{if(valid())error.value='本机听书位置暂未保存，已听段落仍保留在本页。'}
  }
  function receive(input:SpeechState,live=false){
    if(!valid()||input.scopeKey!==key)return
    if(input.eventVersion!==undefined&&input.eventVersion<eventVersion)return
    eventVersion=input.eventVersion??eventVersion
    const current=input.currentId?byId.get(input.currentId):undefined,completed=input.lastCompletedId?byId.get(input.lastCompletedId):undefined,endedChanged=input.status==='ended'&&state.value.status!=='ended'
    const statuses=['idle','ready','starting','playing','paused','ended','error']
    if(!statuses.includes(input.status))return
    state.value={scopeKey:key,title,status:input.status,reason:input.reason,index:current?segments.findIndex(s=>s.id===current.id):input.status==='ended'?segments.length:undefined,count:segments.length,queueLoaded:input.queueLoaded===true,eventVersion:input.eventVersion,currentId:current?.id,currentAnchor:current?.anchor,chapterId:current?.chapterId,chapterTitle:current?.chapterTitle,lastCompletedId:completed?.id,lastCompletedAnchor:completed?.anchor,rate:input.rate,voiceId:input.voiceId,resolvedVoiceId:input.resolvedVoiceId,voiceFallback:input.voiceFallback,stopAtChapterEnd:input.stopAtChapterEnd,sleepRemainingMs:input.sleepRemainingMs}
    if(current)rememberedCurrent=current.id
    if(input.reason==='voices-changed')void retryVoices(false)
    const completedChanged=!!completed&&completed.id!==completedId
    if(completed&&completedChanged){completedId=completed.id;lastCompleted.value=completed;void remember()}
    // Web synthesis emits the completed paragraph and terminal state in one
    // callback. Read the settled state in a microtask. Native terminal events
    // may arrive later; retain that metadata update even for the same paragraph.
    if(live&&completed&&(completedChanged||endedChanged))queueMicrotask(()=>{if(valid()&&lastCompleted.value?.id===completed.id)cloud?.completed(completed,state.value.status==='ended')})
    if(['paused','ended','error'].includes(input.status))void remember()
  }
  function retryVoices(restart=true):Promise<void>{
    if(!valid())return Promise.resolve()
    if(voiceRequest)return voiceRequest
    let expired=false,timer:ReturnType<typeof setTimeout>
    const current=()=>valid()&&!expired
    const reading=(async()=>{
      try{
        const deadline=Date.now()+10000
        let result=await enqueue(()=>driver.voices({retry:restart}),current)
        // Native initialization is asynchronous. Repeatedly restarting it can
        // prevent an installed voice from ever becoming ready. Only an explicit
        // repair restarts the engine; subsequent reads share that initialization.
        while(current()&&result?.initializing&&Date.now()<deadline){
          await new Promise<void>(resolve=>setTimeout(resolve,Math.min(100,deadline-Date.now())))
          if(!current())return
          result=await enqueue(()=>driver.voices({retry:false}),current)
        }
        if(current()&&result){
          voices.value=result.voices;available.value=result.available
          error.value=result.available?'':result.initializing?'语音正在初始化，请稍后重试。':speechReason(result.reason)||'系统语音暂时不可用，可以重试。'
        }
      }catch{if(current())error.value='暂时无法读取系统声音，请重试。'}
    })()
    // The bridge has its own transport timeout. This deadline also covers a
    // queued or stalled response; its eventual completion cannot revive voices.
    const timeout=new Promise<void>(resolve=>{timer=setTimeout(()=>{
      expired=true
      if(valid()){available.value=false;error.value='语音正在初始化，请稍后重试。'}
      resolve()
    },10000)})
    const request=Promise.race([reading,timeout]).finally(()=>{clearTimeout(timer)})
    voiceRequest=request
    void request.finally(()=>{if(voiceRequest===request)voiceRequest=undefined})
    return request
  }
  async function initialize(){
    try{
      if(!context.storyId||!context.lineId||!context.revisionId||!segments.length)throw new Error('没有可播放的正文')
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([base,owner,context.storyId,context.mode,context.lineId,context.revisionId])))
      if(!valid())return;key=Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('')
      validateSpeechQueue({...preferences.value,scopeKey:key,title,segments})
      const savedPrefs=await cacheGet<SpeechPreferences>(prefKey);if(!valid())return;if(savedPrefs&&validSpeechPreferences(savedPrefs))preferences.value={rate:savedPrefs.rate,voiceId:savedPrefs.voiceId,sleepMinutes:savedPrefs.sleepMinutes,stopAtChapterEnd:savedPrefs.stopAtChapterEnd}
      const saved=await cacheGet<{scopeKey:string;lastCompletedId:string;currentId:string;ended:boolean}>(positionKey());if(!valid())return
      if(saved?.scopeKey===key){lastCompleted.value=byId.get(saved.lastCompletedId)||null;completedId=lastCompleted.value?.id||'';rememberedCurrent=byId.has(saved.currentId)?saved.currentId:'';rememberedEnded=saved.ended===true}
      if(cloud){
        await cloud.initialize();if(!valid())return
        if(cloud.preferences.value&&validSpeechPreferences(cloud.preferences.value))preferences.value={...cloud.preferences.value}
        const local=cloud.completion.value,position=cloud.position.value.position,marker=local?{paragraphId:local.paragraphId,lastCompletedAnchor:local.anchor,ended:local.ended}:position,completed=marker?byId.get(marker.paragraphId):undefined
        if(completed&&samePlaybackAnchor(completed.anchor,marker!.lastCompletedAnchor)&&(!lastCompleted.value||local||!cloud.pending.value)){lastCompleted.value=completed;completedId=completed.id;rememberedCurrent=completed.id;rememberedEnded=marker!.ended}
      }
      const handle=await enqueue(()=>driver.addListener('speechState',input=>receive(input,true)));if(!valid()){await handle?.remove();return}listener=handle
      const restored=await enqueue(()=>driver.status({scopeKey:key}));if(!valid())return
      if(restored?.scopeKey===key){receive(restored);if(restored.currentId&&byId.has(restored.currentId))rememberedCurrent=restored.currentId;rememberedEnded=restored.status==='ended'}
      else if(rememberedCurrent||rememberedEnded)state.value={scopeKey:key,title,status:rememberedEnded?'ended':'paused',reason:'process-restored',queueLoaded:false,count:segments.length,currentId:rememberedCurrent,chapterTitle:byId.get(rememberedCurrent)?.chapterTitle}
      await retryVoices(false)
    }catch(e){if(valid())error.value=e instanceof Error&&e.message==='没有可播放的正文'?e.message:(e as {code?:string})?.code==='NATIVE_SPEECH_PROTOCOL_UNAVAILABLE'?speechReason('NATIVE_SPEECH_PROTOCOL_UNAVAILABLE'):'无法打开系统听书，正文阅读不受影响。'}finally{if(valid())loading.value=false}
  }
  async function run(fn:()=>Promise<void>){if(!valid()||loading.value||busy.value)return;const version=++command;busy.value=true;error.value='';try{await fn()}catch{if(valid()&&version===command)error.value='系统语音暂未完成这次操作，可以重试。'}finally{if(valid()&&version===command)busy.value=false}}
  async function observeSelection(segment:SpeechSegment|undefined){
    if(!valid()||!segment||context.mode!=='read'||context.worldId||!context.bookId||!cloudOptions.onReaderObserve)return
    const anchor=segment.anchor
    if(!('branchId'in anchor)||anchor.bookId!==context.bookId||anchor.branchId!==context.lineId||anchor.revisionId!==context.revisionId)return
    const request=command,active=()=>valid()&&request===command
    // This is an explicit reading jump, never a speech completion callback.
    // The Reader owns its Progress CAS; world/draft queues do not receive it.
    try{await cloudOptions.onReaderObserve(segment,active)}
    catch{if(active())error.value='阅读位置尚未保存，本机跳转已保留，请重试或处理位置冲突。'}
  }
  async function play(fromId?:string){return run(async()=>{
    if(fromId&&!byId.has(fromId))throw new Error('不可读的段落')
    if(!available.value||voiceRequest){await retryVoices(false);if(!available.value||!valid())return}
    if(!state.value.queueLoaded||fromId){if(!fromId&&(rememberedEnded||state.value.status==='ended')){error.value='本页已听完，可以选择一个段落重新开始。';return}
      // A durable marker may carry the exited service's counter. A fresh queue
      // starts its own callback epoch; never compare it against that old value.
      if(!state.value.queueLoaded)eventVersion=-1
      const loaded=await enqueue(()=>driver.loadQueue({...preferences.value,scopeKey:key,title,segments,fromId:fromId||rememberedCurrent||undefined}));if(!valid())return;if(loaded)receive(loaded);rememberedEnded=false
      if(fromId&&loaded?.scopeKey===key&&loaded.currentId===fromId)await observeSelection(byId.get(fromId));if(!valid())return}
    const next=await enqueue(()=>driver.play({scopeKey:key}));if(next&&valid())receive(next)
  })}
  async function pause(){return run(async()=>{const next=await enqueue(()=>driver.pause({scopeKey:key}));if(next&&valid())receive(next)})}
  async function skip(direction:-1|1){return run(async()=>{if(!state.value.queueLoaded)return;const next=await enqueue(()=>driver.skip({scopeKey:key,direction}));if(next&&valid()){receive(next);if(next.scopeKey===key&&next.currentId===state.value.currentId)await observeSelection(byId.get(next.currentId||''))}})}
  async function configure(value:SpeechPreferences){if(!validSpeechPreferences(value)||!valid())return;return run(async()=>{preferences.value={...value};await cacheSet(prefKey,preferences.value);if(!valid())return;void cloud?.configure({...value});if(state.value.queueLoaded){const next=await enqueue(()=>driver.configure({...value,scopeKey:key}));if(next&&valid())receive(next)}})}
  async function stop(){return run(async()=>{await enqueue(()=>driver.stop({clearPosition:false}));if(!valid())return;state.value={scopeKey:key,title,status:'paused',reason:'stopped',queueLoaded:false,count:segments.length};rememberedEnded=false})}
  async function openSettings(){if(!valid())return;try{const result=await enqueue(()=>driver.openSettings());if(valid()&&result)instructions.value=result.instructions||(!result.opened?'请在系统语音设置中安装中文声音。':'')}catch{if(valid())instructions.value='请在系统语音设置中安装中文声音，然后重试。'}}
  function hide(){if(!native&&valid()&&playing.value)void enqueue(async()=>{const next=await driver.pause({scopeKey:key});if(valid())receive({...next,reason:'page-hidden'})})}
  function visibility(){if(document.hidden)hide()}
  function removed(event:Event){const info=(event as CustomEvent<{bookId?:string;worldId?:string;lineId?:string}>).detail;if(info?.bookId&&info.bookId!==context.bookId)return;if(info?.worldId&&(info.worldId!==context.worldId||info.lineId&&info.lineId!==context.lineId))return;dispose()}
  function dispose(){if(disposed)return;disposed=true;cloud?.dispose();command++;loading.value=false;busy.value=false;state.value={status:'idle',queueLoaded:false};lastCompleted.value=null;error.value='';void listener?.remove().catch(()=>{});void enqueue(()=>driver.stop({clearPosition:true}),()=>own()&&(!native||owner===accountId()&&base===backendBase()&&auth===authGeneration())).catch(()=>{if(own())error.value='语音已停止，本机恢复标记暂未清除。'});if(typeof window!=='undefined'){for(const event of ['reader-private-cleared','reader-backend-changed','reader-book-deleted','reader-world-deleted'])window.removeEventListener(event,removed);document.removeEventListener('visibilitychange',visibility)}}
  if(typeof window!=='undefined'){for(const event of ['reader-private-cleared','reader-backend-changed','reader-book-deleted','reader-world-deleted'])window.addEventListener(event,removed);document.addEventListener('visibilitychange',visibility)}
  return {state,preferences,voices,available,loading,busy,error,instructions,lastCompleted,playing,initialize,play,pause,skip,configure,stop,openSettings,retryVoices,dispose,context,cloud}
}
export type SpeechController=ReturnType<typeof createSpeechController>
