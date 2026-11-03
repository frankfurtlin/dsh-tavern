import {ref,computed} from 'vue'
import {api,newKey,authGeneration,ApiError} from './index'
import {cacheGet,cacheSet,accountId,backendBase} from '../platform/bridge'
import {isBookDeleted} from '../platform/book-cache'
import {watchGeneration} from './generation-watch'
import {mergeGenerationSnapshot,parseGenerationSnapshot,parseReceivedRaw,acceptablePrefix,generationFinished,generationReadOnly} from './generation-stream'
import {proseProtocol,type GenerationSnapshot,type GenerationIntent,type GenerationRequest,type GenerationReceipt,type GenerationRequestStatus,type GenerationCapabilities,type GenerationAcceptRequest,type GenerationAcceptResult,type GenerationRewriteIntent,type GenerationVersion,type GenerationReceivedRaw} from './generation-types'
import {writingError} from './story-writing'

export function createGenerationController(bookId:string,branchId:string,scope:()=>boolean=()=>true,cacheIdentity=''){
  const auth=authGeneration(),owner=accountId(),base=backendBase(),key='generation-stream:'+branchId+cacheIdentity
  const state=ref<GenerationSnapshot|null>(null),intent=ref<GenerationIntent|null>(null),supported=ref(false),ready=ref(false),busy=ref(false),error=ref(''),conflict=ref(false),cached=ref(false)
  const rewriteIntent=ref<GenerationRewriteIntent|null>(null),lastAccepted=ref<GenerationAcceptResult|null>(null),versions=ref<GenerationVersion[]>([]),preview=ref<GenerationSnapshot|null>(null)
  const receivedRaw=ref<GenerationReceivedRaw[]>([])
  type Unconfirmed={key:string;createdAt:string;intent:GenerationIntent|null;rewriteIntent:GenerationRewriteIntent|null;generationId:string}
  const unconfirmed=ref<Unconfirmed[]>([]),unconfirmedKey='generation-unconfirmed:'+branchId+cacheIdentity
  let attachedId='',previewVersion=0
  let disposed=false,commandVersion=0,readVersion=0,unwatch:(()=>void)|undefined
  const valid=()=>!disposed&&scope()&&auth===authGeneration()&&owner===accountId()&&base===backendBase()&&!isBookDeleted(bookId)
  const active=computed(()=>!!state.value&&!generationFinished(state.value)),prefix=computed(()=>state.value?acceptablePrefix(state.value):0)
  function fail(e:unknown){error.value=e instanceof ApiError?writingError(e):e instanceof Error&&/^(写作记录|写作事件|已保存的)/.test(e.message)?e.message:'暂时无法完成这次操作，已收到的正文与请求仍保留'}
  function stopWatching(){readVersion++;unwatch?.();unwatch=undefined}
  function check(value:unknown){const next=parseGenerationSnapshot(value);if(next.bookId!==bookId||next.scopeAnchor.branchId!==branchId&&next.targetBranchId!==branchId)throw new Error('写作记录属于其他故事线');return next}
  type SavedRecord={bookId?:string;intent?:GenerationIntent;generationId?:string;rewriteIntent?:GenerationRewriteIntent;lastAccepted?:GenerationAcceptResult;snapshot?:GenerationSnapshot;protocol?:typeof proseProtocol;receivedRaw?:GenerationReceivedRaw[]}
  const savedRecord=()=>({bookId,protocol:proseProtocol,intent:intent.value,generationId:attachedId||intent.value?.generationId,rewriteIntent:rewriteIntent.value,lastAccepted:lastAccepted.value,snapshot:state.value,receivedRaw:receivedRaw.value})
  function boundRaw(value:GenerationSnapshot,records:unknown){
    if(!Array.isArray(records)||records.length>10)throw new Error('写作记录中的原返回副本无效')
    return records.map(parseReceivedRaw).filter(record=>record.generationId===value.id&&record.candidateVersion===value.candidateVersion&&value.chapters.some(c=>c.contentId===record.contentId&&c.index===record.rawPresentation.chapterIndex&&c.streamVersion>=record.streamVersion&&c.snapshotVersion>=record.snapshotVersion))
  }
  function collectRaw(){
    const value=state.value;if(!value){receivedRaw.value=[];return}
    const retained=boundRaw(value,receivedRaw.value),byContent=new Map(retained.map(r=>[r.contentId,r]))
    for(const chapter of value.chapters){if(!chapter.rawPresentation)continue;const prior=byContent.get(chapter.contentId)
      if(!prior||chapter.streamVersion>prior.streamVersion||chapter.streamVersion===prior.streamVersion&&chapter.snapshotVersion>=prior.snapshotVersion)byContent.set(chapter.contentId,{generationId:value.id,candidateVersion:value.candidateVersion,contentId:chapter.contentId,streamVersion:chapter.streamVersion,snapshotVersion:chapter.snapshotVersion,rawPresentation:chapter.rawPresentation})
    }
    receivedRaw.value=[...byContent.values()]
  }
  async function archivePending(){
    const pendingKey=rewriteIntent.value?.key||(!intent.value?.generationId?intent.value?.key:null)
    if(!pendingKey)return
    const records=[...unconfirmed.value.filter(r=>r.key!==pendingKey),{key:pendingKey,createdAt:new Date().toISOString(),intent:intent.value,rewriteIntent:rewriteIntent.value,generationId:attachedId}]
    await cacheSet(unconfirmedKey,{bookId,records});if(valid())unconfirmed.value=records
  }
  async function rememberObserved(){
    if(!valid())return
    collectRaw()
    try{await cacheSet(key,savedRecord())}catch{if(valid())error.value='正文已收到，本机副本暂未保存；联网后可载入服务端记录'}
  }
  function observe(id:string){
    stopWatching();const read=readVersion
    if(state.value?.id===id&&generationReadOnly(state.value))return
    unwatch=watchGeneration(id,async(value,current)=>{if(valid()&&read===readVersion&&current()){state.value=mergeGenerationSnapshot(state.value,check(value));cached.value=false;error.value='';await rememberObserved();if(valid()&&read===readVersion&&current()&&state.value&&generationReadOnly(state.value))stopWatching()}},e=>{if(valid()&&read===readVersion)fail(e)},()=>valid()&&read===readVersion)
  }
  async function attach(receipt:GenerationReceipt){
    if(receipt.protocol!==proseProtocol||!receipt.generationId||!receipt.taskId)throw new Error('写作回执暂时不完整')
    attachedId=receipt.generationId
    if(intent.value){intent.value.generationId=receipt.generationId;intent.value.taskId=receipt.taskId}
    // The confirmed receipt remains in memory if persistence fails. Retrying
    // this command must only GET it, never recreate its provider task.
    await cacheSet(key,savedRecord());if(!valid())return
    observe(receipt.generationId)
  }
  async function lookupPending(){
    const pending=intent.value;if(!pending||!valid())return
    const read=++readVersion;unwatch?.();unwatch=undefined
    if(pending.generationId){observe(pending.generationId);return}
    const result=await api<GenerationRequestStatus>('/generations/request-status?requestId='+encodeURIComponent(pending.key)+'&bookId='+encodeURIComponent(bookId))
    if(!valid()||read!==readVersion||intent.value!==pending)return
    if(result.found)await attach(result.receipt)
    else error.value='尚未找到这次写作的回执。输入已保留；确认后可提交同一次请求。'
  }
  async function initialize(){
    const command=++commandVersion
    try{
      const saved=await cacheGet<SavedRecord>(key);if(!valid()||command!==commandVersion)return
      const local=await cacheGet<{bookId:string;records:Unconfirmed[]}>(unconfirmedKey);if(!valid()||command!==commandVersion)return
      if(local?.bookId===bookId&&Array.isArray(local.records))unconfirmed.value=local.records
      const ownedSaved=saved?.bookId===bookId||saved?.intent?.request.originAnchor.bookId===bookId
      if(ownedSaved){
        intent.value=saved.intent||null;rewriteIntent.value=saved.rewriteIntent||null;lastAccepted.value=saved.lastAccepted||null;attachedId=saved.generationId||''
        if(saved.snapshot){const snapshot=check(saved.snapshot);if(snapshot.id!==attachedId)throw new Error('写作记录与本机回执不一致');const raw=boundRaw(snapshot,saved.receivedRaw||[]);state.value=snapshot;receivedRaw.value=raw;collectRaw();cached.value=true;supported.value=true}
      }
      const capability=await api<GenerationCapabilities>('/generations/capabilities');if(!valid()||command!==commandVersion)return
      supported.value=capability.ready===true&&capability.protocol===proseProtocol&&(!cacheIdentity.startsWith(':selection:')||capability.selectionWriting===true)
      if(!supported.value)return
      if(ownedSaved&&saved){
        if(saved.generationId){attachedId=saved.generationId;observe(attachedId)}else if(intent.value)await lookupPending()
        if(rewriteIntent.value){const pending=rewriteIntent.value,result=await api<GenerationRequestStatus>('/generations/request-status?requestId='+encodeURIComponent(pending.key)+'&bookId='+encodeURIComponent(bookId));if(!valid()||command!==commandVersion||rewriteIntent.value!==pending)return;if(result.found){rewriteIntent.value=null;await attach(result.receipt)}else error.value='重写请求的回执尚未确认。原候选保留，只有确认提交才会重发同一次请求。'}
      }
    }catch(e){if(valid()&&command===commandVersion){if(e instanceof ApiError&&e.status===404)supported.value=false;else fail(e)}}
    finally{if(valid()&&command===commandVersion)ready.value=true}
  }
  async function submit(request:GenerationRequest){
    if(!valid()||busy.value||active.value||!supported.value||cached.value)return false
    const command=++commandVersion,still=()=>valid()&&command===commandVersion
    busy.value=true;error.value='';conflict.value=false
    try{
      if(!intent.value){if(request.originAnchor.bookId!==bookId||request.originAnchor.branchId!==branchId)throw new Error('写作起点属于其他故事线');intent.value={key:newKey(),request:JSON.parse(JSON.stringify(request))};state.value=null;receivedRaw.value=[]}
      const pending=intent.value
      if(pending.generationId){observe(pending.generationId);return true}
      await cacheSet(key,savedRecord());if(!still())return false
      const receipt=await api<GenerationReceipt>('/books/'+encodeURIComponent(bookId)+'/generations',{method:'POST',key:pending.key,body:pending.request});if(!still())return false
      await attach(receipt);return still()
    }catch(e){if(still())fail(e);return false}
    finally{if(still())busy.value=false}
  }
  async function refresh(){
    if(!valid())return
    if(rewriteIntent.value){
      const pending=rewriteIntent.value,read=++readVersion;unwatch?.();unwatch=undefined
      try{const result=await api<GenerationRequestStatus>('/generations/request-status?requestId='+encodeURIComponent(pending.key)+'&bookId='+encodeURIComponent(bookId));if(!valid()||read!==readVersion||rewriteIntent.value!==pending)return;if(result.found){rewriteIntent.value=null;await attach(result.receipt);return}}catch(e){if(valid()&&read===readVersion)fail(e);return}
    }
    if(!attachedId&&!intent.value?.generationId){try{await lookupPending()}catch(e){if(valid())fail(e)}return}
    const id=attachedId||intent.value!.generationId!,read=++readVersion;unwatch?.();unwatch=undefined
    try{const next=await api<unknown>('/generations/'+encodeURIComponent(id));if(!valid()||read!==readVersion)return;state.value=mergeGenerationSnapshot(state.value,check(next));cached.value=false;error.value='';conflict.value=false;await rememberObserved();if(valid()&&read===readVersion)observe(id)}catch(e){if(valid()&&read===readVersion)fail(e)}
  }
  async function command(action:'cancel'|'save'){
    const value=state.value;if(!valid()||busy.value||cached.value||!value||generationReadOnly(value))return
    const version=++commandVersion,still=()=>valid()&&version===commandVersion&&state.value?.id===value.id
    busy.value=true;error.value=''
    try{await api('/generations/'+encodeURIComponent(value.id)+'/'+action,{method:'POST',key:action+'-'+value.id+'-'+value.candidateVersion});if(still())await refresh()}catch(e){if(still())fail(e)}finally{if(still())busy.value=false}
  }
  async function accept(count:number,pinned=false):Promise<GenerationAcceptResult|undefined>{
    const value=state.value;if(!valid()||busy.value||cached.value||!value||!Number.isInteger(count)||count<=value.acceptedPrefix||count>acceptablePrefix(value))return
    const version=++commandVersion,still=()=>valid()&&version===commandVersion&&state.value?.id===value.id
    const forkPinned=pinned&&value.acceptedPrefix===0
    const body:GenerationAcceptRequest={candidateVersion:value.candidateVersion,prefixCount:count,expectedAcceptedPrefix:value.acceptedPrefix,expectedHeadRevisionId:value.lastAcceptedRevisionId||value.scopeAnchor.revisionId,target:forkPinned?'child':value.targetBranchId?'append':'child',sourcePolicy:forkPinned?'forkPinned':'requireUnchanged'}
    busy.value=true;error.value='';conflict.value=false
    try{
      const result=await api<GenerationAcceptResult>('/generations/'+encodeURIComponent(value.id)+'/accept',{method:'POST',key:`generation-accept-${value.id}-${value.candidateVersion}-${value.acceptedPrefix}-${count}-${pinned?'pinned':'current'}`,body})
      if(!still())return
      lastAccepted.value=result
      try{await cacheSet(key,savedRecord());if(!still())return;if(result.branchId&&result.branchId!==branchId)await cacheSet('generation-stream:'+result.branchId,savedRecord())}catch(e){if(still())error.value='章节已加入故事线，本机写作记录暂未同步'}
      if(!still())return
      await refresh();if(still())return result
    }catch(e){if(still()){fail(e);conflict.value=e instanceof ApiError&&e.status===409}}
    finally{if(still())busy.value=false}
  }
  async function rewrite(instruction:string){
    const value=state.value;if(!valid()||busy.value||cached.value||active.value||!value||generationReadOnly(value)||value.acceptedPrefix>=value.requestedChapters||!instruction.trim()&&!rewriteIntent.value)return false
    const version=++commandVersion,still=()=>valid()&&version===commandVersion
    busy.value=true;error.value=''
    try{
      if(!rewriteIntent.value)rewriteIntent.value={key:newKey(),generationId:value.id,body:{candidateVersion:value.candidateVersion,expectedAcceptedPrefix:value.acceptedPrefix,instruction:instruction.trim()}}
      const pending=rewriteIntent.value;await cacheSet(key,savedRecord());if(!still())return false
      const receipt=await api<GenerationReceipt>('/generations/'+encodeURIComponent(pending.generationId)+'/rewrite',{method:'POST',key:pending.key,body:pending.body});if(!still())return false
      rewriteIntent.value=null;preview.value=null;await attach(receipt);return still()
    }catch(e){if(still())fail(e);return false}finally{if(still())busy.value=false}
  }
  async function open(id:string){
    if(!valid()||busy.value||!supported.value)return
    const read=++readVersion;unwatch?.();unwatch=undefined
    try{
      const next=check(await api('/generations/'+encodeURIComponent(id)));if(!valid()||read!==readVersion)return
      if(next.id!==id)throw new Error('写作记录属于其他任务')
      const currentId=state.value?.id||attachedId||intent.value?.generationId||rewriteIntent.value?.generationId||''
      const changing=currentId!==id
      if(changing){await archivePending();if(!valid()||read!==readVersion)return;rewriteIntent.value=null;intent.value=null;receivedRaw.value=[]}
      state.value=state.value?.id===id?mergeGenerationSnapshot(state.value,next):next;collectRaw();cached.value=false;attachedId=id;intent.value=null;preview.value=null;lastAccepted.value=null;await cacheSet(key,savedRecord());if(valid()&&read===readVersion)observe(id)
    }catch(e){if(valid()&&read===readVersion)fail(e)}
  }
  async function loadVersions(){const id=state.value?.id,version=++previewVersion;if(!id||!valid())return;try{const result=await api<{items:GenerationVersion[]}>('/generations/'+encodeURIComponent(id)+'/versions');if(valid()&&version===previewVersion&&state.value?.id===id)versions.value=result.items.filter(v=>Number.isSafeInteger(v.candidateVersion)&&v.candidateVersion>0)}catch(e){if(valid()&&version===previewVersion)fail(e)}}
  async function selectVersion(selected:number){
    const id=state.value?.id,version=++previewVersion;if(!id||!valid())return
    if(selected===state.value?.candidateVersion){preview.value=null;return}
    try{const result=check(await api('/generations/'+encodeURIComponent(id)+'?candidateVersion='+selected));if(valid()&&version===previewVersion&&state.value?.id===id&&result.candidateVersion===selected)preview.value=result}catch(e){if(valid()&&version===previewVersion)fail(e)}
  }
  async function newDraft(){
    if(!valid()||busy.value||active.value)return;const version=++commandVersion;stopWatching()
    try{
      await archivePending();if(!valid()||version!==commandVersion)return
      await cacheSet(key,null);if(valid()&&version===commandVersion){state.value=null;receivedRaw.value=[];cached.value=false;intent.value=null;rewriteIntent.value=null;attachedId='';lastAccepted.value=null;preview.value=null;versions.value=[];error.value='';conflict.value=false}
    }catch(e){if(valid()&&version===commandVersion)fail(e)}
  }
  async function restoreUnconfirmed(selected:string){
    const record=unconfirmed.value.find(r=>r.key===selected);if(!record||!valid()||busy.value||active.value)return
    stopWatching();state.value=null;receivedRaw.value=[];cached.value=false;preview.value=null;intent.value=record.intent;rewriteIntent.value=record.rewriteIntent;attachedId=record.generationId
    try{await cacheSet(key,savedRecord());if(valid())await refresh()}catch(e){if(valid())fail(e)}
  }
  function invalidate(){disposed=true;commandVersion++;previewVersion++;stopWatching();state.value=null;receivedRaw.value=[];cached.value=false;intent.value=null;rewriteIntent.value=null;preview.value=null;lastAccepted.value=null;versions.value=[];unconfirmed.value=[];error.value='';busy.value=false;if(typeof window!=='undefined'){window.removeEventListener('reader-private-cleared',invalidate);window.removeEventListener('reader-backend-changed',invalidate)}}
  if(typeof window!=='undefined'){window.addEventListener('reader-private-cleared',invalidate);window.addEventListener('reader-backend-changed',invalidate)}
  return {state,intent,rewriteIntent,lastAccepted,versions,preview,unconfirmed,receivedRaw,supported,ready,busy,error,conflict,cached,active,prefix,initialize,submit,open,refresh,cancel:()=>command('cancel'),save:()=>command('save'),accept,rewrite,newDraft,restoreUnconfirmed,loadVersions,selectVersion,dispose:invalidate}
}
export type GenerationController=ReturnType<typeof createGenerationController>
