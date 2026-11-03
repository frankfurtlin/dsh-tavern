import {defaultSpeechPreferences,validateSpeechQueue,validSpeechPreferences,speechChunks,type SpeechDriver,type SpeechSegment,type SpeechState,type SpeechVoices} from './speech-types'

// Only actual synthesis callbacks can mark playing/completed. Timers pause;
// they never manufacture speech or advance an unread paragraph.
export function createWebSpeech(synthesis:SpeechSynthesis|undefined=globalThis.speechSynthesis,Utterance:typeof SpeechSynthesisUtterance|undefined=globalThis.SpeechSynthesisUtterance):SpeechDriver{
  let state:SpeechState={status:'idle',queueLoaded:false,eventVersion:0},prefs={...defaultSpeechPreferences},queue:SpeechSegment[]=[],epoch=0,deadline=0,chunkIndex=0,sleep:ReturnType<typeof setTimeout>|undefined,starting:ReturnType<typeof setTimeout>|undefined
  const listeners=new Set<(value:SpeechState)=>void>(),copy=()=>({...state}),publish=()=>{state.eventVersion=(state.eventVersion||0)+1;for(const listener of listeners)listener(copy());return copy()}
  function invalidate(){epoch++;clearTimeout(starting);synthesis?.cancel()}
  function pause(reason='paused'){invalidate();chunkIndex=0;state.status=queue.length?'paused':'idle';state.reason=reason;clearTimeout(sleep);if(reason==='sleep-timer'){deadline=0;state.sleepRemainingMs=0}return publish()}
  function select(index:number){state.index=index;const s=queue[index];state.currentId=s?.id;state.chapterId=s?.chapterId;state.chapterTitle=s?.chapterTitle;state.currentAnchor=s?.anchor}
  function same(scope:string){if(scope!==state.scopeKey)throw new Error('SPEECH_SCOPE_CHANGED')}
  function voices():SpeechVoices{const values=synthesis?.getVoices()||[];return {voices:values.map(v=>({id:v.voiceURI,name:v.name,language:v.lang,offline:v.localService})),available:!!synthesis&&!!Utterance&&values.length>0,initializing:false,...(!synthesis||!Utterance?{reason:'SYSTEM_SPEECH_UNAVAILABLE'}:!values.length?{reason:'NO_SYSTEM_VOICE'}:{})}}
  function armSleep(){clearTimeout(sleep);if(deadline){const remaining=Math.max(0,deadline-performance.now());state.sleepRemainingMs=remaining;sleep=setTimeout(()=>pause('sleep-timer'),remaining)}}
  function speak(){
    if(!synthesis||!Utterance){state.status='error';state.reason='SYSTEM_SPEECH_UNAVAILABLE';return publish()}
    const segment=queue[state.index||0];if(!segment){state.status='ended';state.reason='queue-end';clearTimeout(sleep);return publish()}
    const available=synthesis.getVoices(),preferred=available.find(v=>v.voiceURI===prefs.voiceId),voice=preferred||available.find(v=>/^zh/i.test(v.lang))
    if(!voice){state.status='error';state.reason=available.length?'NO_CHINESE_SYSTEM_VOICE':'NO_SYSTEM_VOICE';return publish()}
    const chunks=speechChunks(segment.text)
    invalidate();const generation=epoch,utterance=new Utterance(chunks[chunkIndex]);utterance.rate=prefs.rate;utterance.voice=voice;utterance.lang=voice.lang
    state.resolvedVoiceId=voice.voiceURI;state.voiceFallback=!!prefs.voiceId&&!preferred;state.status='starting';state.reason='';publish()
    starting=setTimeout(()=>{if(generation===epoch)pause('SYSTEM_SPEECH_START_TIMEOUT')},15000)
    utterance.onstart=()=>{if(generation!==epoch)return;clearTimeout(starting);state.status='playing';publish();armSleep()}
    utterance.onend=()=>{if(generation!==epoch)return;if(chunkIndex+1<chunks.length){chunkIndex++;speak();return}chunkIndex=0;state.lastCompletedId=segment.id;state.lastCompletedAnchor=segment.anchor;const next=(state.index||0)+1;select(next);publish();if(next>=queue.length){state.status='ended';state.reason='queue-end';clearTimeout(sleep);publish()}else if(prefs.stopAtChapterEnd&&queue[next].chapterId!==segment.chapterId)pause('chapter-end');else if(deadline&&performance.now()>=deadline)pause('sleep-timer');else speak()}
    utterance.onerror=event=>{if(generation!==epoch)return;pause(event.error||'SYSTEM_SPEECH_FAILED');state.status='error';publish()}
    try{synthesis.speak(utterance)}catch{pause('SYSTEM_SPEECH_FAILED');state.status='error';publish()}
    return copy()
  }
  return {
    voices:async()=>voices(),
    loadQueue:async options=>{validateSpeechQueue(options);invalidate();clearTimeout(sleep);chunkIndex=0;queue=options.segments.map(s=>({...s,anchor:{...s.anchor}}));prefs={rate:options.rate,voiceId:options.voiceId,stopAtChapterEnd:options.stopAtChapterEnd,sleepMinutes:options.sleepMinutes};deadline=0;state={scopeKey:options.scopeKey,title:options.title,status:'ready',queueLoaded:true,count:queue.length,eventVersion:state.eventVersion||0,...prefs};select(options.fromId?queue.findIndex(s=>s.id===options.fromId):0);return publish()},
    status:async({scopeKey})=>{if(state.scopeKey&&scopeKey!==state.scopeKey){invalidate();queue=[];state={status:'idle',queueLoaded:false,eventVersion:state.eventVersion||0};publish()}return copy()},
    play:async({scopeKey})=>{same(scopeKey);if(state.status==='ended')return copy();if(!deadline&&prefs.sleepMinutes)deadline=performance.now()+prefs.sleepMinutes*60000;return speak()},
    pause:async({scopeKey})=>{same(scopeKey);return pause()},
    skip:async({scopeKey,direction})=>{same(scopeKey);const playing=['playing','starting'].includes(state.status),next=Math.max(0,Math.min(queue.length-1,(state.index||0)+direction));pause('selected');select(next);publish();return playing?speak():copy()},
    configure:async options=>{same(options.scopeKey);if(!validSpeechPreferences(options))throw new Error('SPEECH_PREFERENCES_INVALID');const playing=['playing','starting'].includes(state.status);prefs={rate:options.rate,voiceId:options.voiceId,stopAtChapterEnd:options.stopAtChapterEnd,sleepMinutes:options.sleepMinutes};state={...state,...prefs};deadline=prefs.sleepMinutes?performance.now()+prefs.sleepMinutes*60000:0;if(playing){pause('preferences');return speak()}return publish()},
    stop:async()=>{invalidate();clearTimeout(sleep);queue=[];deadline=0;state={status:'idle',queueLoaded:false,eventVersion:state.eventVersion||0};return publish()},
    openSettings:async()=>({opened:false,instructions:'请在浏览器或系统的语音设置中安装中文声音，然后重试。'}),
    addListener:async(_event,listener)=>{listeners.add(listener);const changed=()=>{if(state.scopeKey){const reason=state.reason;state.reason='voices-changed';publish();state.reason=reason}};synthesis?.addEventListener('voiceschanged',changed);return {remove:async()=>{listeners.delete(listener);synthesis?.removeEventListener('voiceschanged',changed)}}},
  }
}
