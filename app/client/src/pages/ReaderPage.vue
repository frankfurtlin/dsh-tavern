<script setup lang="ts">
import {readingSelectionPoints,type ReadingSelectionPoints} from '../api/reading-annotations'
import {ref,shallowRef,markRaw,computed,onMounted,onUnmounted,nextTick,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,List,GitBranch,Bookmark as BookmarkIcon,Search,Settings2,Sparkles,MessagesSquare,ChevronLeft,ChevronRight,NotebookPen} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
import SelectionEditor from '../components/SelectionEditor.vue'
import ServerRevisionHistory from '../components/ServerRevisionHistory.vue'
import {selectionEditScope,type SelectionEditScope,type SelectionEditResult} from '../api/selection-edits'
import ReadingExport from '../components/ReadingExport.vue'
import GenerationPanel from '../components/GenerationPanel.vue'
import GenerationStart from '../components/GenerationStart.vue'
import GenerationDraft from '../components/GenerationDraft.vue'
import SpeechBar from '../components/SpeechBar.vue'
import {createSpeechController,type SpeechController} from '../platform/speech-controller'
import {readingSpeechSegments,type SpeechSegment} from '../platform/speech-types'
import type {CloudReadingPreferences} from '../api/playback'
import type {GenerationChapter} from '../api/generation-types'
import {createGenerationController,type GenerationController} from '../api/generation-controller'
import type {GenerationAcceptResult} from '../api/generation-types'
import ChatPanel from '../components/ChatPanel.vue'
import CharacterCards from '../components/CharacterCards.vue'
import StoryBlock from '../components/StoryBlock.vue'
import StoryImages from '../components/StoryImages.vue'
import type {OfflineImages} from '../api/assets'
import NoteEditor from '../components/NoteEditor.vue'
import BookmarkRemoval from '../components/BookmarkRemoval.vue'
import {isBookmarkDeleted} from '../platform/bookmark-cache'
import DownloadReading from '../components/DownloadReading.vue'
import {downloadedOrigin,localNotes,readPackage,packageId} from '../offline/storage'
import ReaderViewport from '../components/ReaderViewport.vue'
import {quoteHash} from '../api/anchors'
import {readingSelection} from '../api/reading-annotations'
import {queueNote,syncNotes} from '../offline/notes'
import {storyId,sessionContext,rememberContext} from '../api/stories'
import {useReadingActivity} from '../platform/reading-activity'
import {api,allPages,newKey,ApiError,authGeneration} from '../api'
import type {Book,Branch,ChapterInfo,Chapter,Block,Anchor,Progress,Bookmark,RoleSession,AcceptResult} from '../api/types'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useApp} from '../stores/app'
import {readingPreference,readingStyle} from '../reader/reading-preference'
const route=useRoute(),router=useRouter(),store=useApp(),book=ref<Book|null>(null),branches=ref<Branch[]>([]),branch=ref<Branch|null>(null),revision=ref(''),chapters=ref<ChapterInfo[]>([]),chapter=ref<Chapter|null>(null),selected=ref<Block|null>(null),current=ref<Anchor|null>(null),observed=ref<Anchor|null>(null),version=ref(0),error=ref(''),loading=ref(true),saving=ref(false),dirty=ref(false),saveStatus=ref(''),sheet=ref(''),operation=ref(''),generationAnchor=ref<Anchor|null>(null),bookmarks=ref<Bookmark[]>([]),query=ref(''),results=ref<{snippet:string;anchor:Anchor}[]>([]),session=ref<RoleSession|null>(null),conflict=ref<{saved:Progress;localAnchor:Anchor}|null>(null)
const textEditRange=ref<ReadingSelectionPoints|null>(null),selectionProblem=ref('')
const selectionRevisionScope=ref<SelectionEditScope|null>(null),selectionRevisionBusy=ref(false),selectionEditor=ref<InstanceType<typeof SelectionEditor>>()
const historyRevisionBusy=ref(false)
async function historyRevisionSaved(result:SelectionEditResult){const scope=captureScope(),loaded=chapter.value;if(!scope.valid()||!loaded)return;await save();if(scope.valid())await router.push({path:'/books/'+scope.bookId+'/read',query:{branch:result.branchId,revision:result.revisionId,chapter:loaded.id,story:returnStory.value||undefined}})}
async function closeSelectionEditor(){if(selectionRevisionBusy.value)return;const scope=captureScope();try{await selectionEditor.value?.flush();if(scope.valid())sheet.value=''}catch(e){if(scope.valid())error.value=(e as Error).message}}
async function editSelection(){const scope=captureScope(),line=branch.value,loaded=chapter.value;if(!scope.valid()||!line||!loaded||line.status!=='active'||book.value?.status!=='active')return;const at=textSelection.value,block=loaded.blocks.find(b=>b.id===(at?.blockId||selected.value?.id||current.value?.blockId))||loaded.blocks[0];if(!block)return;try{if(selectionProblem.value)throw new Error(selectionProblem.value);const range=textEditRange.value;const frozen:SelectionEditScope=range?{bookId:range.startAnchor.bookId,branchId:range.startAnchor.branchId,revisionId:range.startAnchor.revisionId,chapterId:range.startAnchor.chapterId,headRevisionId:line.headRevisionId,startAnchor:{...range.startAnchor},endAnchor:{...range.endAnchor},originalText:range.originalText}:await selectionEditScope(block,line.headRevisionId,at);if(!scope.valid())return;await save();if(!scope.valid())return;selectionRevisionScope.value=frozen;sheet.value='selection-edit'}catch(e){if(scope.valid()&&!selectionProblem.value)error.value=(e as Error).message}}
async function selectionSaved(result:SelectionEditResult){const scope=captureScope(),frozen=selectionRevisionScope.value;if(!frozen||!scope.valid())return;await save();if(scope.valid())await router.push({path:'/books/'+frozen.bookId+'/read',query:{branch:result.branchId,revision:result.revisionId,chapter:frozen.chapterId,story:returnStory.value||undefined}})}
const chapterIndex=computed(()=>chapters.value.findIndex(c=>c.id===chapter.value?.id)),readerElement=ref<HTMLElement|null>(null)
const readerViewport=ref<InstanceType<typeof ReaderViewport>>()
const returnStory=ref('')
const removingBookmark=ref<Bookmark|null>(null)
const readingImages=ref<OfflineImages|undefined>()
const generation=shallowRef<GenerationController|null>(null)
const speech=shallowRef<SpeechController|null>(null)
let speechVersion=0
function closeSpeech(){speechVersion++;speech.value?.dispose();speech.value=null}
function speechPreferences(saved:CloudReadingPreferences){if(store.user&&saved.version>=store.user.settings.version){const {editVersion:_,speech:__,...reading}=saved;store.user.settings={...store.user.settings,...reading}}}
async function listen(){
  const scope=captureScope();if(!scope.valid()||!book.value||!chapter.value)return
  const title=book.value.title,loaded=chapter.value,lineId=branch.value!.id,rev=revision.value,epoch=loadId,auth=authGeneration(),fromId=selected.value?.id||current.value?.blockId||loaded.blocks[0]?.id
  closeSpeech();const speechEpoch=speechVersion,root=returnStory.value||storyId('book',scope.bookId!),valid=()=>epoch===loadId&&auth===authGeneration()&&!loading.value&&book.value?.id===scope.bookId&&branch.value?.id===lineId&&revision.value===rev&&chapter.value?.id===loaded.id&&speechEpoch===speechVersion
  const controller=markRaw(createSpeechController({bookId:scope.bookId,storyId:root,mode:'read',lineId,revisionId:rev,baseContext:{storyId:root,mode:'read',perspective:'reader',entryOrigin:'reader',bookId:scope.bookId,readerBranchId:lineId,anchor:loaded.blocks[0].startAnchor}},title,readingSpeechSegments(loaded),valid,undefined,{progressVersion:()=>version.value,onReadingPreferences:speechPreferences,onReaderProgress:(saved,expected)=>{
    if(!valid()||saved.revisionId!==rev||version.value!==expected)return
    version.value=saved.version
    if(saved.maxObservedAnchor?.chapterId===loaded.id){const next=loaded.blocks.findIndex(b=>b.id===saved.maxObservedAnchor!.blockId),previous=loaded.blocks.findIndex(b=>b.id===observed.value?.blockId);if(next>previous||next===previous&&(saved.maxObservedAnchor.end>(observed.value?.end||0)))observed.value=saved.maxObservedAnchor}
    void cacheSet(`api:/books/${scope.bookId}/progress?branchId=${lineId}&revisionId=${rev}`,saved).catch(()=>{})
  },onReaderObserve:observeSpeechSelection}))
  speech.value=controller;sheet.value='';await controller.initialize();if(scope.valid()&&speechEpoch===speechVersion)await controller.play(fromId)
}
async function listenDraft(candidate:GenerationChapter){
  const scope=captureScope(),batch=generation.value?.state.value
  if(!scope.valid()||!batch||candidate.durability!=='saved'||candidate.completeness!=='complete'||!candidate.contentHash||!batch.chapters.some(c=>c.contentId===candidate.contentId&&c.text===candidate.text))return
  const segment:SpeechSegment={id:candidate.contentId,chapterId:candidate.contentId,chapterTitle:candidate.title||'已保存草稿',text:candidate.text,anchor:{bookId:scope.bookId!,generationId:batch.id,contentId:candidate.contentId,candidateVersion:batch.candidateVersion,start:0,end:candidate.text.length,offsetUnit:'utf16',draft:true}}
  closeSpeech();const version=speechVersion,root=returnStory.value||storyId('book',scope.bookId!),controller=markRaw(createSpeechController({bookId:scope.bookId,storyId:root,mode:'draft',lineId:scope.lineId!,revisionId:batch.id+':'+batch.candidateVersion,baseContext:{storyId:root,mode:'read',perspective:'reader',entryOrigin:'reader',bookId:scope.bookId,readerBranchId:scope.lineId,anchor:batch.scopeAnchor}},book.value!.title,[segment],()=>scope.valid()&&version===speechVersion&&generation.value?.state.value?.id===batch.id&&generation.value?.state.value?.candidateVersion===batch.candidateVersion,undefined,{onReadingPreferences:speechPreferences}))
  speech.value=controller;await controller.initialize();if(scope.valid()&&version===speechVersion)await controller.play(segment.id)
}
async function locateSpeech(segment:SpeechSegment){
  if('draft'in segment.anchor){document.querySelector<HTMLElement>('[data-content-id="'+CSS.escape(segment.id)+'"]')?.scrollIntoView({block:'center'});return}
  if('bookId'in segment.anchor&&'branchId'in segment.anchor)await gotoAnchor(segment.anchor)
}
async function observeSpeechSelection(segment:SpeechSegment,active:()=>boolean){
  const scope=captureScope(),anchor=segment.anchor
  if(!active()||!scope.valid()||!('branchId'in anchor)||anchor.bookId!==scope.bookId||anchor.branchId!==scope.lineId||anchor.revisionId!==scope.revisionId)return
  const block=chapter.value?.blocks.find(block=>block.id===anchor.blockId)
  if(!block||anchor.chapterId!==chapter.value?.id||(['bookId','branchId','revisionId','chapterId','blockId','start','end','quoteHash','offsetUnit'] as const).some(field=>anchor[field]!==block.endAnchor[field]))return
  current.value={...anchor};dirty.value=true
  // Preserve the real completed-speech marker. Explicit user selection uses
  // the ordinary reading-position CAS and its existing conflict controls.
  await saveProgress(()=>active()&&scope.valid())
  if(active()&&scope.valid()&&(conflict.value||dirty.value))throw new Error('阅读位置尚未保存')
}
const legacyWriting=ref(false)
function closeGeneration(){generation.value?.dispose();generation.value=null}
function restoreGeneration(bookId:string,lineId:string,epoch:number,auth:number){
  closeGeneration()
  const controller=markRaw(createGenerationController(bookId,lineId,()=>epoch===loadId&&auth===authGeneration()&&book.value?.id===bookId&&branch.value?.id===lineId))
  generation.value=controller;void controller.initialize().then(()=>{if(controller.supported.value&&route.query.generation&&generation.value===controller)void controller.open(String(route.query.generation))})
}
async function acceptedPrefix(result:GenerationAcceptResult){
  if(!captureScope().valid()||result.readingAnchor.bookId!==book.value?.id)return
  // Keep this batch and its observation alive; another chapter may still stream.
  store.message('完整章节已加入故事线')
}
async function readAccepted(result:GenerationAcceptResult){const scope=captureScope();if(!scope.valid()||result.readingAnchor.bookId!==scope.bookId)return;await save();if(scope.valid())await router.push({path:`/books/${scope.bookId}/read`,query:{branch:result.branchId,revision:result.revisionId,chapter:result.readingAnchor.chapterId,anchor:JSON.stringify(result.readingAnchor),generation:result.generationId,story:returnStory.value}})}
let observationId=0
async function observePage(blockId:string,offset:number){
  if(loading.value||conflict.value||document.hidden)return
  const block=chapter.value?.blocks.find(block=>block.id===blockId);if(!block)return
  const scope=captureScope(),id=++observationId,anchor={...block.endAnchor,start:offset,end:offset}
  anchor.quoteHash=await quoteHash(block.text,anchor.revisionId,blockId,offset)
  if(scope.valid()&&id===observationId){current.value=anchor;observed.value=anchor;dirty.value=true}
}
let observer:IntersectionObserver|null=null,saveTimer:ReturnType<typeof setInterval>|undefined,loadId=0,chapterLoadId=0,readerAuthEpoch=authGeneration(),searchId=0,rolesId=0,bookmarkLoadId=0
const roleAnchor=ref<Anchor|null>(null),noteAnchor=ref<Anchor|null>(null),editingNote=ref<Bookmark|undefined>(undefined),textSelection=ref<Anchor|null>(null),annotationBusy=ref(false),chapterOpening=ref(false)
let selectionId=0
async function selectText(){const request=++selectionId,scope=captureScope();if(!scope.valid()||!readerElement.value){textSelection.value=null;textEditRange.value=null;selectionProblem.value='';return}const selection=window.getSelection?.()||null,results=await Promise.allSettled([readingSelection(readerElement.value,selection,chapter.value?.blocks||[]),readingSelectionPoints(readerElement.value,selection,chapter.value?.blocks||[])]);if(!scope.valid()||request!==selectionId)return;textSelection.value=results[0].status==='fulfilled'?results[0].value:null;textEditRange.value=results[1].status==='fulfilled'?results[1].value:null;selectionProblem.value=results[1].status==='rejected'?((results[1].reason as Error).message==='ANCHOR_INVALID'?'选区边界无法定位，请重新选择完整字符':String((results[1].reason as Error).message)):''}
function clearSelection(){selectionId++;textSelection.value=null;textEditRange.value=null;selectionProblem.value='';window.getSelection?.()?.removeAllRanges()}
useReadingActivity(()=>({bookId:book.value?.id||'',branchId:branch.value?.id||'',revisionId:revision.value,enabled:!loading.value&&!!chapter.value&&store.online&&!sheet.value}))
function writeNote(note?:Bookmark){if(!captureScope().valid())return;const at=note?.anchor||textSelection.value||selected.value?.endAnchor||current.value;if(!at){store.message('请先选择一个正文段落');return}noteAnchor.value={...at};editingNote.value=note;sheet.value='note'}
function clearInteraction(){selectionRevisionScope.value=null;selectionRevisionBusy.value=false;historyRevisionBusy.value=false;selectionId++;textSelection.value=null;textEditRange.value=null;selectionProblem.value='';annotationBusy.value=false;searchId++;rolesId++;bookmarkLoadId++;results.value=[];query.value='';roleAnchor.value=null;session.value=null;generationAnchor.value=null;selected.value=null;sheet.value=''}
function captureScope(strict=true){
  const generation=loadId,location=chapterLoadId,auth=authGeneration(),bookId=book.value?.id,lineId=branch.value?.id,revisionId=revision.value,routeKey=route.fullPath,chapterId=chapter.value?.id
  return {bookId,lineId,revisionId,valid:()=>!!bookId&&!!lineId&&!loading.value&&(!strict||!chapterOpening.value&&chapterId===chapter.value?.id)&&generation===loadId&&location===chapterLoadId&&auth===authGeneration()&&auth===readerAuthEpoch&&bookId===book.value?.id&&lineId===branch.value?.id&&revisionId===revision.value&&routeKey===route.fullPath}
}
const progressKey=()=>`pending-progress:${branch.value?.id}:${revision.value}`
function sequence(){const key='reader-seq:'+store.user?.sessionId;const n=Number(localStorage.getItem(key)||'0')+1;localStorage.setItem(key,String(n));return n}
async function load(){const id=++loadId,auth=authGeneration(),path=route.fullPath,valid=()=>id===loadId&&auth===authGeneration()&&path===route.fullPath;closeSpeech();closeGeneration();chapterLoadId++;chapterOpening.value=false;clearInteraction();bookmarks.value=[];readingImages.value=undefined;observer?.disconnect();loading.value=true;error.value='';try{
  const bookId=String(route.params.bookId),lineId=route.query.branch,pinnedRevision=route.query.revision,requestedChapter=route.query.chapter
  const loadedBook=await api<Book>('/books/'+bookId,{cache:true}),loadedBranches=await allPages<Branch>(`/books/${bookId}/branches`)
  const line=loadedBranches.find(b=>b.id===lineId)||loadedBranches.find(b=>b.id===loadedBook.originalBranchId);if(!line)throw new Error('故事线不存在')
  const rev=String(pinnedRevision||line.headRevisionId),directory=await allPages<ChapterInfo>(`/branches/${line.id}/chapters?revisionId=${rev}`)
  const progress=await api<Progress>(`/books/${bookId}/progress?branchId=${line.id}&revisionId=${rev}`,{cache:true})
  const pending=await cacheGet<{anchor:Anchor;version:number;sequence:number}|null>(`pending-progress:${line.id}:${rev}`)
  const savedAnchor=progress.anchor
  const pendingSaved=!!pending&&!!savedAnchor&&(['bookId','branchId','revisionId','chapterId','blockId','start','end','quoteHash','offsetUnit'] as const).every(key=>pending.anchor[key]===savedAnchor[key])
  let restore=pending&&!pendingSaved?pending.anchor:progress.anchor
  const requested=requestedChapter?String(requestedChapter):restore?.chapterId||directory[0]?.id
  const loadedChapter=await api<Chapter>(`/branches/${line.id}/chapters/${requested}?revisionId=${rev}`,{cache:true})
  const marks=await allPages<Bookmark>(`/books/${bookId}/bookmarks?branchId=${line.id}&revisionId=${rev}`)
  if(!valid())return
  const jump=loadedChapter.blocks.find(block=>block.id===route.query.block);if(jump)restore=route.query.edge==='start'?jump.startAnchor:jump.endAnchor
  let exactJump=false
  if(route.query.anchor){
    const at=JSON.parse(String(route.query.anchor)) as Anchor,block=loadedChapter.blocks.find(block=>block.id===at.blockId)
    if(!block||at.bookId!==bookId||at.branchId!==line.id||at.revisionId!==rev||at.chapterId!==loadedChapter.id||at.offsetUnit!=='utf16'||!Number.isInteger(at.start)||!Number.isInteger(at.end)||await quoteHash(block.text,rev,block.id,at.start,at.end)!==at.quoteHash)throw new Error('保存的阅读位置无效，请从故事入口重新打开')
    restore=at;exactJump=true
  }
  let origin=storyId('book',bookId)
  const requestedOrigin=String(route.query.story||'')
  if(/^(book|world)-[a-f0-9]{32}$/.test(requestedOrigin)&&!store.online){if(await downloadedOrigin(bookId,line.id,rev,requestedOrigin))origin=requestedOrigin}
  if(/^(book|world)-[a-f0-9]{32}$/.test(requestedOrigin)&&store.online){
    try{await api('/stories/'+requestedOrigin+'/context/validate',{method:'POST',body:{storyId:requestedOrigin,mode:'read',perspective:'reader',entryOrigin:'library',bookId,readerBranchId:line.id,anchor:restore||loadedChapter.blocks[0]?.startAnchor}});if(!valid())return;origin=requestedOrigin}catch(e){if(!(e instanceof ApiError&&[400,404,409,422].includes(e.status)))throw e}
  }
  if(!valid())return
  returnStory.value=origin
  const mergedMarks=await localNotes(bookId,line.id,rev,marks);if(!valid())return
  const images=!store.online?(await readPackage(packageId(bookId,line.id,rev)))?.payload.images:undefined;if(!valid())return;readingImages.value=images
  book.value=loadedBook;branches.value=loadedBranches;branch.value=line;revision.value=rev;chapters.value=directory;chapter.value=loadedChapter;bookmarks.value=mergedMarks.filter(note=>!isBookmarkDeleted(bookId,note.id));selected.value=null
  version.value=pending&&!pendingSaved?pending.version:progress.version;current.value=restore;observed.value=progress.maxObservedAnchor;dirty.value=!!jump||exactJump||!!pending&&!pendingSaved
  conflict.value=pending&&!pendingSaved&&pending.version<progress.version?{saved:progress,localAnchor:pending.anchor}:null
  saveStatus.value=conflict.value?'位置冲突':dirty.value?'位置待同步':''
  loading.value=false;await nextTick();if(!valid())return
  await readerViewport.value?.measure();if(!valid())return
  if(restore?.chapterId===requested)await readerViewport.value?.gotoAnchor(restore);else window.scrollTo(0,0)
  observeBlocks()
  if(valid())restoreGeneration(bookId,line.id,id,auth)
}catch(e){
  if(!valid())return
  if(e instanceof ApiError&&e.code==='BOOK_DELETED'){
    const oldBranch=String(route.query.branch||'')
    clearRemovedRendering()
    if(store.online&&oldBranch){try{
      const resolved=await api<{bookId:string;branchId:string;revisionId:string;anchor?:Anchor;storyId:string;migrated:boolean}>('/reading-context/resolve',{method:'POST',body:{bookId:String(route.params.bookId),branchId:oldBranch,...(route.query.revision?{revisionId:String(route.query.revision)}:{}),...(route.query.anchor?{anchor:JSON.parse(String(route.query.anchor))}:{})}})
      if(!valid())return
      const suppliedOrigin=String(route.query.story||''),origin=/^world-[a-f0-9]{32}$/.test(suppliedOrigin)?suppliedOrigin:resolved.storyId
      await router.replace({path:'/books/'+resolved.bookId+'/read',query:{...route.query,branch:resolved.branchId,revision:resolved.revisionId,story:origin,...(resolved.anchor?{chapter:resolved.anchor.chapterId,anchor:JSON.stringify(resolved.anchor)}:{})}})
      return
    }catch(resolveError){if(valid())error.value=(resolveError as Error).message}}
    else error.value='这本小说已经移除。'
  }else error.value=(e as Error).message
}finally{if(valid())loading.value=false}}
function clearRemovedRendering(){closeSpeech();closeGeneration();chapterOpening.value=false;clearInteraction();noteAnchor.value=null;editingNote.value=undefined;observer?.disconnect();dirty.value=false;chapter.value=null;book.value=null;branch.value=null;branches.value=[];chapters.value=[];bookmarks.value=[];current.value=null;observed.value=null;conflict.value=null;returnStory.value='';revision.value='';saveStatus.value=''}
function removed(event:Event){const bookId=(event as CustomEvent<{bookId:string}>).detail?.bookId;if(bookId!==String(route.params.bookId))return;loadId++;chapterLoadId++;observationId++;clearRemovedRendering();void load()}
async function openChapter(id:string,anchor?:Anchor){
  if(!branch.value||!id)return
  if(id!==chapter.value?.id)closeSpeech()
  const generation=++chapterLoadId,line=branch.value.id,rev=revision.value;clearInteraction()
  const scope=captureScope(false);chapterOpening.value=true;observer?.disconnect();let rendered=false
  const valid=()=>generation===chapterLoadId&&scope.valid()
  try{
    const loaded=await api<Chapter>(`/branches/${line}/chapters/${id}?revisionId=${rev}`,{cache:true})
    if(!valid())return
    clearInteraction();chapter.value=loaded;await nextTick();if(!valid())return
    await readerViewport.value?.measure();if(!valid())return
    if(anchor)await readerViewport.value?.gotoAnchor(anchor)
    else{readerViewport.value?.gotoBlock(loaded.blocks[0]?.id||'','start');window.scrollTo(0,0)}
    rendered=true
  }catch(e){if(valid())error.value=(e as Error).message}
  finally{if(valid())chapterOpening.value=false}
  if(rendered&&valid())observeBlocks()
}

function order(anchor:Anchor){const c=chapters.value.findIndex(c=>c.id===anchor.chapterId);const b=chapter.value?.blocks.findIndex(b=>b.id===anchor.blockId)??0;return[c,b,anchor.end]}
function observeBlocks(){observer?.disconnect();if(store.settings.readingMode&&store.settings.readingMode!=='scroll'){readerViewport.value?.observe();return}observer=new IntersectionObserver(entries=>{if(document.hidden||loading.value||conflict.value)return;for(const entry of entries){if(entry.isIntersecting){const b=chapter.value?.blocks.find(b=>b.id===entry.target.getAttribute('data-block'));if(b){current.value=b.endAnchor;observed.value=b.endAnchor;dirty.value=true}}}},{threshold:[0]});readerElement.value?.querySelectorAll('.anchor-end').forEach(e=>observer?.observe(e))}
let progressSaving:Promise<void>|undefined
function save(){return saveProgress()}
async function saveProgress(active:()=>boolean=()=>true){while(progressSaving){await progressSaving;if(!active())return}if(!active())return;const pending=persistProgress(active);progressSaving=pending;try{await pending}finally{if(progressSaving===pending)progressSaving=undefined}}
async function persistProgress(active:()=>boolean){if(!active()||readerAuthEpoch!==authGeneration()||!dirty.value||!current.value||!branch.value||saving.value||conflict.value)return;saving.value=true;const local=current.value,expected=version.value,key=progressKey(),generation=loadId,seq=sequence(),auth=readerAuthEpoch,deviceId=store.user?.sessionId;const unchanged=()=>active()&&auth===authGeneration()&&generation===loadId&&branch.value?.id===local.branchId&&revision.value===local.revisionId;try{
  await cacheSet(key,{anchor:local,version:expected,sequence:seq})
  if(!active()||auth!==authGeneration())return
  const saved=await api<Progress>(`/books/${local.bookId}/progress`,{method:'PUT',body:{branchId:local.branchId,revisionId:local.revisionId,anchor:local,expectedProgressVersion:expected,deviceId,clientSeq:seq}})
  if(!active()||auth!==authGeneration())return
  await cacheSet(`api:/books/${local.bookId}/progress?branchId=${local.branchId}&revisionId=${local.revisionId}`,saved)
  if(unchanged()){version.value=saved.version;observed.value=saved.maxObservedAnchor;if(current.value===local)dirty.value=false;await cacheSet(key,dirty.value?{anchor:current.value,version:saved.version,sequence:seq}:null);saveStatus.value='位置已保存'}
}catch(e){if(unchanged()){if(e instanceof ApiError&&e.code==='PROGRESS_CONFLICT'){conflict.value=e.details as unknown as {saved:Progress;localAnchor:Anchor};saveStatus.value='位置冲突'}else saveStatus.value='位置待同步'}}finally{saving.value=false}}
async function resolveProgress(local:boolean){if(!conflict.value)return;const scope=captureScope(),key=progressKey(),saved=conflict.value.saved,at=local?conflict.value.localAnchor:saved.anchor;if(!scope.valid())return;version.value=saved.version;current.value=at;dirty.value=local;conflict.value=null;if(at)await openChapter(at.chapterId,at);if(scope.bookId!==book.value?.id||scope.lineId!==branch.value?.id||scope.revisionId!==revision.value||readerAuthEpoch!==authGeneration())return;if(local)await save();else await cacheSet(key,null)}
async function leave(){if(!book.value||!branch.value){await router.push('/library');return}const scope=captureScope();await save();if(scope.valid())await router.push('/stories/'+(returnStory.value||storyId('book',scope.bookId!)))}
async function show(name:string){if(name==='roles'){const scope=captureScope(),anchor=selected.value?.endAnchor||current.value;if(!anchor||!scope.valid())return;await save();if(!scope.valid())return;roleAnchor.value={...anchor}}sheet.value=name;if(name==='bookmarks')await loadBookmarks()}
async function enterWorld(){const scope=captureScope(),anchor=selected.value?.endAnchor||current.value;if(!anchor||!scope.valid())return;await save();if(scope.valid())await router.push({path:"/worlds/new",query:{book:scope.bookId,branch:scope.lineId,anchor:JSON.stringify(anchor)}})}
async function timeline(){const scope=captureScope();await save();if(scope.valid())await router.push({path:`/books/${scope.bookId}/branches`,query:{branch:scope.lineId}})}
async function openedRole(value:RoleSession){
  if(sheet.value!=='roles'||!roleAnchor.value||value.bookId!==book.value?.id||value.branchId!==branch.value?.id)return
  const scope=captureScope();if(!scope.valid())return
  session.value=value;sheet.value='chat'
  const valid=()=>scope.valid()&&sheet.value==='chat'&&session.value?.id===value.id
  try{await rememberContext(sessionContext(value,returnStory.value||undefined),valid)}
  catch(e){if(valid())error.value=(e as Error).message}
}
async function loadBookmarks(){const scope=captureScope(),request=++bookmarkLoadId;if(!scope.valid())return;try{const marks=await allPages<Bookmark>(`/books/${scope.bookId}/bookmarks?branchId=${scope.lineId}&revisionId=${scope.revisionId}`);const merged=await localNotes(scope.bookId!,scope.lineId!,scope.revisionId,marks);if(scope.valid()&&request===bookmarkLoadId)bookmarks.value=merged.filter(note=>!isBookmarkDeleted(scope.bookId!,note.id))}catch(e){if(scope.valid()&&request===bookmarkLoadId)error.value=(e as Error).message}}
async function addAnnotation(anchor:Anchor,label:string){const scope=captureScope();if(!scope.valid()||annotationBusy.value)return;annotationBusy.value=true;try{const saved=await queueNote(anchor,'');if(!scope.valid())return;bookmarks.value=[...bookmarks.value,saved];clearSelection();store.message(store.online?label+'已保存在本机，正在同步':label+'已保存在本机');if(store.online)await syncNotes();if(scope.valid())await loadBookmarks()}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())annotationBusy.value=false}}
async function addBookmark(){const anchor=selected.value?.endAnchor||current.value;if(anchor)await addAnnotation(anchor,'书签')}
async function addHighlight(){if(!textSelection.value){store.message('请先选中同一段里的文字');return}await addAnnotation({...textSelection.value},'划线')}
function removeBookmark(item:Bookmark){const scope=captureScope();if(!scope.valid()||item.anchor.branchId!==scope.lineId||item.anchor.revisionId!==scope.revisionId)return;removingBookmark.value=item;sheet.value='remove-bookmark'}
function bookmarkRemoved(event:Event){const item=(event as CustomEvent<{bookId:string;bookmarkId:string}>).detail;if(!item||item.bookId!==String(route.params.bookId))return;bookmarkLoadId++;bookmarks.value=bookmarks.value.filter(note=>note.id!==item.bookmarkId);void loadBookmarks()}
async function gotoAnchor(anchor:Anchor){if(anchor.branchId!==branch.value?.id||anchor.revisionId!==revision.value){error.value='书签属于其他版本，请打开对应故事线';return}sheet.value='';await openChapter(anchor.chapterId,anchor);if(anchor.branchId!==branch.value?.id||anchor.revisionId!==revision.value||chapter.value?.id!==anchor.chapterId||readerAuthEpoch!==authGeneration())return;current.value=anchor;dirty.value=true}
async function search(){const scope=captureScope(),request=++searchId,text=query.value;if(!scope.valid())return;try{const data=await api<{items:{snippet:string;anchor:Anchor}[]}>(`/books/${scope.bookId}/search?branchId=${scope.lineId}&revisionId=${scope.revisionId}&query=${encodeURIComponent(text)}`);if(scope.valid()&&request===searchId)results.value=data.items}catch(e){if(scope.valid()&&request===searchId)error.value=(e as Error).message}}
async function generate(op:string,atEnd=false){legacyWriting.value=false;const scope=captureScope();if(!scope.valid()||!chapter.value)return;const block=atEnd?chapter.value.blocks.at(-1):selected.value||chapter.value.blocks.find(b=>b.id===current.value?.blockId)||chapter.value.blocks[0];if(!block){store.message('请先选择一个段落，或使用“从本章末尾写”');sheet.value='choose-anchor';operation.value=op;return}const tail=chapterIndex.value===chapters.value.length-1&&block.id===chapter.value.blocks.at(-1)?.id;await save();if(!scope.valid())return;generationAnchor.value=block.endAnchor;operation.value=op==='continue_from_anchor'&&tail?'continue_at_tail':op;sheet.value='generation'}
async function accepted(result:AcceptResult){const scope=captureScope();if(!scope.valid()||result.readingAnchor.bookId!==scope.bookId)return;sheet.value='';try{await cacheSet('generation:'+scope.lineId,{taskId:null})}catch{if(scope.valid())store.message('新章节已保存，本机写作记录暂未同步。')}if(scope.valid())await router.push({path:`/books/${scope.bookId}/read`,query:{branch:result.branchId,chapter:result.readingAnchor.chapterId}})}
async function switchBranch(id:string){const scope=captureScope();if(!scope.valid())return;await save();if(!scope.valid())return;sheet.value='';await router.push({path:`/books/${scope.bookId}/read`,query:{branch:id}})}
async function readChapter(index:number){const scope=captureScope(),target=chapters.value[index]?.id;if(!target||!scope.valid())return;await save();if(scope.valid())await openChapter(target)}
function notesChanged(){if(chapter.value&&!loading.value)void loadBookmarks()}
function resume(){save();window.dispatchEvent(new Event('reader-task-resume'))}
watch(()=>route.fullPath,()=>load())
onMounted(()=>{load();saveTimer=setInterval(save,3000);window.addEventListener('reader-book-deleted',removed);window.addEventListener('reader-bookmark-deleted',bookmarkRemoved);document.addEventListener?.('selectionchange',selectText);window.addEventListener('reader-notes-changed',notesChanged);window.addEventListener('reader-save',save);window.addEventListener('reader-resume',resume)})
watch(()=>store.user?.id,()=>{if(generation.value)closeGeneration();closeSpeech()})
onUnmounted(()=>{save();closeSpeech();closeGeneration();loadId++;chapterLoadId++;observer?.disconnect();clearInterval(saveTimer);window.removeEventListener('reader-book-deleted',removed);window.removeEventListener('reader-bookmark-deleted',bookmarkRemoved);document.removeEventListener?.('selectionchange',selectText);window.removeEventListener('reader-notes-changed',notesChanged);window.removeEventListener('reader-save',save);window.removeEventListener('reader-resume',resume)})
</script>
<template><header class="reader-header"><button class="icon-button" aria-label="返回故事入口并保存进度" @click="leave"><ArrowLeft :size="22"/></button><div class="reader-title"><strong>{{book?.title||'打开故事'}}</strong><small>{{branch?.name||'原文'}} · {{saveStatus||'阅读位置自动保存'}}</small></div><button class="icon-button" aria-label="故事时间线" @click="timeline"><GitBranch :size="20"/></button><button class="icon-button" aria-label="打开目录" @click="show('directory')"><List :size="22"/></button><button class="icon-button" aria-label="阅读工具" @click="show('tools')"><Settings2 :size="20"/></button></header><div v-if="loading" class="loading">正在打开正文…</div><div v-if="error" class="error" role="alert">{{error}}<button @click="error='';load()">重新加载</button><RouterLink v-if="!chapter" to="/library" class="inline-link">回到书架</RouterLink></div><div v-if="conflict" class="progress-conflict"><h2>另一设备保存了新的位置</h2><p>本机位置已保留，请选择继续阅读的位置。</p><div class="actions"><button @click="resolveProgress(false)">使用已保存位置</button><button class="primary" @click="resolveProgress(true)">继续本机位置并保存</button></div></div><div v-if="chapter&&!loading" class="reader-layout"><aside class="desktop-directory" aria-label="章节目录"><h3>目录</h3><button v-for="(c,i) in chapters" :key="c.id" class="chapter-link" :class="{active:c.id===chapter.id}" @click="readChapter(i)">{{c.title}}</button></aside><article ref="readerElement" class="reader-body" :style="{...readingStyle,'--reading-width':(store.settings.readingWidth||36)+'em',maxWidth:((store.settings.readingWidth||36)*readingPreference.fontSize+64)+'px'}"><ReaderViewport ref="readerViewport" :mode="store.settings.readingMode||'scroll'" :anchor="current?.chapterId===chapter.id?current:undefined" :blocks="chapter.blocks" :layout-key="[chapter.id,store.settings.fontSize,store.settings.lineHeight,store.settings.readingWidth,generation?.state.value?.id||''].join(':')" @layout-start="observer?.disconnect();observationId++" @observed="observePage" @changed="observeBlocks"><h1>{{chapter.title}}</h1><template v-for="block in chapter.blocks" :key="block.id"><StoryImages :scope="book&&branch?{kind:'book',bookId:book.id,branchId:branch.id,revisionId:revision}:null" purpose="illustration" side="before" :offline="readingImages" :content-id="block.id" :paragraph-id="block.id" :editable="selected?.id===block.id&&branch?.status==='active'&&book?.status==='active'" :foldable="true" @layout="readerViewport?.measure()"/><div :id="'block-'+block.id" class="paragraph" :data-content-source="block.source" :data-reading-block="block.id" :class="{selected:selected?.id===block.id,'speech-playing':speech?.state.value.status==='playing'&&speech.state.value.currentId===block.id,'speech-completed':speech?.lastCompleted.value?.id===block.id}" tabindex="0" role="button" :aria-label="'选择段落：'+block.text.slice(0,30)" @click="selected=selected?.id===block.id?null:block" @keydown.enter="selected=block" @keydown.space.prevent="selected=block"><StoryBlock :block="block" :book-id="book!.id" :highlights="bookmarks"/><span class="anchor-end" :data-block="block.id" aria-hidden="true" style="display:block;height:1px;width:100%"></span></div><StoryImages :scope="book&&branch?{kind:'book',bookId:book.id,branchId:branch.id,revisionId:revision}:null" purpose="illustration" side="after" :offline="readingImages" :content-id="block.id" :paragraph-id="block.id" :editable="selected?.id===block.id&&branch?.status==='active'&&book?.status==='active'" :foldable="true" @layout="readerViewport?.measure()"/><GenerationDraft v-if="generation?.state.value?.scopeAnchor.blockId===block.id&&(generation.state.value.scopeAnchor.revisionId===revision||generation.state.value.targetBranchId===branch?.id)" :controller="generation" @accepted="acceptedPrefix" @read="readAccepted" @listen="listenDraft"/></template><div class="reading-end"><p>{{chapterIndex+1}} / {{chapters.length}} 章</p><div class="actions" style="justify-content:center"><button :disabled="chapterIndex<=0" @click="readChapter(chapterIndex-1)"><ChevronLeft :size="18"/>上一章</button><button :disabled="chapterIndex>=chapters.length-1" @click="readChapter(chapterIndex+1)">下一章<ChevronRight :size="18"/></button></div><button style="margin-top:16px" @click="generate('continue_from_anchor',true)">从本章末尾写</button><p class="save-state" style="margin-top:16px">{{saveStatus}}</p></div></ReaderViewport></article></div><div v-if="textSelection&&chapter" class="reading-selection-actions" role="toolbar" aria-label="文字批注"><button :disabled="annotationBusy" @pointerdown.prevent @click="addHighlight">划线</button><button @pointerdown.prevent @click="writeNote()">写笔记</button><button class="icon-button" aria-label="取消选中文字" @pointerdown.prevent @click="clearSelection">×</button></div><SpeechBar v-if="speech&&chapter" :controller="speech" @locate="locateSpeech" @close="closeSpeech" @resize="readerViewport?.measure()"/><footer v-if="chapter" class="reader-toolbar"><button @click="generate('continue_from_anchor')"><Sparkles :size="19"/>从这里写</button><button @click="show('roles')"><MessagesSquare :size="19"/>聊角色</button></footer><Sheet v-if="sheet==='directory'" title="目录" @close="sheet=''"><button v-for="(c,i) in chapters" :key="c.id" class="chapter-link" :class="{active:c.id===chapter?.id}" @click="readChapter(i)">{{c.title}}</button><p class="muted"><small>未读章隐藏标题；点击即明确跳转阅读。</small></p></Sheet><Sheet v-if="sheet==='tools'" title="阅读工具" @close="sheet=''"><p v-if="selectionProblem" class="error" role="alert">{{selectionProblem}}</p><button v-if="selectionProblem" @click="clearSelection();sheet=''">重新选择正文</button><div class="stack"><button v-if="store.worldEnabled" :disabled="!store.online" @click="enterWorld">从这里进入故事</button><button :disabled="loading||branch?.status!=='active'||book?.status!=='active'" @click="editSelection">编辑正文</button><button :disabled="!store.online" @click="sheet='revision-history'">历史版本</button><button @click="listen">播放当前正文 · 系统听书</button><button @click="addBookmark"><BookmarkIcon :size="18"/>为当前位置加书签</button><button :disabled="!textSelection||annotationBusy" @click="addHighlight">划线选中文字</button><button @click="show('bookmarks')">查看书签</button><button @click="writeNote()"><NotebookPen :size="18"/>写阅读笔记</button><button :disabled="!store.online" @click="sheet='download'">下载这条故事线</button><button :disabled="!store.online||!book||!branch||!revision" @click="sheet='export'">导出阅读文件 · TXT / EPUB</button><RouterLink to="/downloads" class="inline-link">已下载的故事线</RouterLink><button @click="show('search')"><Search :size="18"/>搜索已读内容</button><span class="inline-link">字号、行距与主题</span><RouterLink :to="`/books/${book?.id}/branches`" class="inline-link">故事时间线</RouterLink></div></Sheet><Sheet v-if="sheet==='selection-edit'&&selectionRevisionScope" title="编辑正文" @close="closeSelectionEditor"><SelectionEditor ref="selectionEditor" :scope="selectionRevisionScope" :offline="!store.online" @busy="selectionRevisionBusy=$event" @saved="selectionSaved"/></Sheet><Sheet v-if="sheet==='revision-history'&&book&&branch" title="历史版本" @close="!historyRevisionBusy&&(sheet='')"><ServerRevisionHistory :book-id="book.id" :branch-id="branch.id" :read-only="branch.status!=='active'||book.status!=='active'" @busy="historyRevisionBusy=$event" @saved="historyRevisionSaved" @close="!historyRevisionBusy&&(sheet='')"/></Sheet><Sheet v-if="sheet==='bookmarks'" title="段落书签" @close="sheet=''"><p v-if="!bookmarks.length" class="muted">此版本还没有书签。</p><div v-for="item in bookmarks" :key="item.id" class="list-item"><small v-if="item.status==='pending_sync'">待同步</small><small v-if="item.status==='conflict_copy'">冲突副本</small><small v-if="item.status==='deleted_original'">原笔记已删除 · 本机文字</small><button @click="gotoAnchor(item.anchor)">{{item.note||(item.anchor.start<item.anchor.end?'返回划线的文字':'返回标记的段落')}}</button><button @click="writeNote(item)">{{item.note?'编辑笔记':'添加笔记'}}</button><button :disabled="!store.online||['pending_sync','deleted_original'].includes(item.status)" class="icon-button danger" aria-label="删除书签" @click="removeBookmark(item)">×</button></div></Sheet><Sheet v-if="sheet==='remove-bookmark'&&removingBookmark" title="删除这条笔记？" @close="sheet='';removingBookmark=null"><BookmarkRemoval :key="removingBookmark.anchor.bookId+':'+removingBookmark.id" :note="removingBookmark" @cancel="sheet='bookmarks';removingBookmark=null" @deleted="sheet='bookmarks';removingBookmark=null;loadBookmarks()"/></Sheet><Sheet v-if="sheet==='note'&&noteAnchor" :title="editingNote?'编辑阅读笔记':'写阅读笔记'" @close="sheet=''"><NoteEditor :key="[noteAnchor.bookId,noteAnchor.branchId,noteAnchor.revisionId,noteAnchor.blockId,noteAnchor.start,noteAnchor.end,noteAnchor.quoteHash,editingNote?.id||'new'].join(':')" :anchor="noteAnchor" :note="editingNote" @saved="sheet='';loadBookmarks()"/></Sheet><Sheet v-if="sheet==='export'&&book&&branch&&revision" title="导出阅读文件" @close="sheet=''"><ReadingExport :scope="{kind:'book',bookId:book.id,branchId:branch.id,revisionId:revision}"/></Sheet><Sheet v-if="sheet==='download'&&book&&branch" title="下载故事线" @close="sheet=''"><DownloadReading :book-id="book.id" :branch-id="branch.id" :revision-id="revision" :origin="returnStory" :line-name="branch.name"/></Sheet><Sheet v-if="sheet==='search'" title="搜索已读内容" @close="sheet=''"><form @submit.prevent="search"><label>关键词<input v-model="query" required maxlength="100"></label><button class="primary" :disabled="!store.online">搜索</button></form><div class="divider"><button v-for="item in results" :key="item.anchor.blockId" class="search-result" @click="gotoAnchor(item.anchor)">{{item.snippet}}</button><p v-if="!results.length" class="muted">只检索此线已解锁的正文。</p></div></Sheet><Sheet v-if="sheet==='branches'" title="选择故事线" @close="sheet=''"><button v-for="line in branches" :key="line.id" class="chapter-link" :class="{active:line.id===branch?.id}" @click="switchBranch(line.id)">{{line.name}} <span v-if="line.status==='archived'" class="badge">归档 · 只读</span></button><button v-if="branch?.parentBranchId" class="primary" @click="switchBranch(book!.originalBranchId)">返回原文</button></Sheet><Sheet v-if="sheet==='choose-anchor'" title="选择写作起点" @close="sheet=''"><p>可以点选正文段落，从该段结束后开始。</p><button class="primary" @click="generate(operation,true)">从本章末尾开始</button></Sheet><Sheet v-if="sheet==='generation'&&generationAnchor&&branch" :title="operation==='analysis'?'准备当前范围':'从这里写'" @close="sheet=''"><GenerationStart v-if="!legacyWriting&&operation!=='analysis'&&generation?.ready.value&&generation.supported.value" :anchor="generationAnchor" :branch="branch" :operation="operation" :controller="generation" @started="sheet=''" @legacy="legacyWriting=true"/><p v-else-if="operation!=='analysis'&&generation&&!generation.ready.value" role="status">正在查看写作服务…</p><GenerationPanel v-else :key="generationAnchor.blockId+operation" :anchor="generationAnchor" :branch="branch" :operation="operation" @accepted="accepted" /></Sheet><Sheet v-if="sheet==='roles'&&book&&roleAnchor" title="故事里的人" @close="sheet=''"><CharacterCards :key="roleAnchor.branchId+roleAnchor.blockId+roleAnchor.end" :book-id="book.id" :anchor="roleAnchor" @opened="openedRole"/></Sheet><Sheet v-if="sheet==='chat'&&session" :fill="true" :title="session.characterName+' · '+session.branchName" @close="sheet=''"><ChatPanel :key="session.id" :session="session"/></Sheet></template>
