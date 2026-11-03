<script setup lang="ts">
import {ref,shallowRef,markRaw,computed,onMounted,onBeforeUnmount,onUnmounted,watch,nextTick} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,List,GitBranch,Compass,Download} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
import ReadingExport from '../components/ReadingExport.vue'
import StoryImages from '../components/StoryImages.vue'
import type {OfflineImages} from '../api/assets'
import {worldImagesAt} from '../api/offline-images'
import DownloadWorldReading from '../components/DownloadWorldReading.vue'
import SpeechBar from '../components/SpeechBar.vue'
import {createSpeechController,type SpeechController} from '../platform/speech-controller'
import {worldSpeechSegments,type SpeechSegment} from '../platform/speech-types'
import {ApiError,authGeneration} from '../api'
import {storyId} from '../api/stories'
import {worldApi} from './api'
import type {WorldInfo,WorldLine,WorldCheckpoint} from './types'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import {cacheGet,cacheSet,accountId} from '../platform/bridge'
import {readWorldPackage,downloadedWorld,type PublicReadWorld,type PublicReadLine,type PublicReadCheckpoint,type OfflineWorldPackage} from '../offline/world-storage'
import {isWorldDeleted} from '../platform/world-cache'

const route=useRoute(),router=useRouter(),capture=useRequestScope(),app=useApp()
const world=ref<PublicReadWorld|null>(null),line=ref<PublicReadLine|null>(null),history=ref<PublicReadCheckpoint[]>([])
const error=ref(''),loading=ref(true),directory=ref(false),current=ref(''),saved=ref(''),viewport=ref<HTMLElement|null>(null)
const download=ref(false),snapshot=ref(false),readingExport=ref(false)
const readingImages=ref<OfflineImages|undefined>()
const speech=shallowRef<SpeechController|null>(null),selectedSpeech=ref('')
const speechParagraphs=computed(()=>{const result=new Map<string,SpeechSegment[]>();for(const segment of worldSpeechSegments(world.value?.id||'',line.value?.id||'',history.value)){const previous=result.get(segment.chapterId)||[];previous.push(segment);result.set(segment.chapterId,previous)}return result})
let speechVersion=0
function closeSpeech(){speechVersion++;speech.value?.dispose();speech.value=null}
async function listen(){
  const scope=capture(),version=loadVersion,loaded=line.value,info=world.value;if(!scope.valid()||loading.value||!loaded||!info||!history.value.length)return
  closeSpeech();const request=speechVersion,segments=worldSpeechSegments(info.id,loaded.id,history.value),fromId=selectedSpeech.value||speechParagraphs.value.get(current.value)?.[0]?.id||segments[0]?.id
  const root=returnStory.value||storyId('world',info.id)
  const controller=markRaw(createSpeechController({worldId:info.id,storyId:root,mode:'read',lineId:loaded.id,revisionId:loaded.headCheckpointId,baseContext:{storyId:root,mode:'read',perspective:'reader',entryOrigin:'reader',worldId:info.id,worldLineId:loaded.id,checkpointId:loaded.headCheckpointId}},info.name,segments,()=>!disposed&&scope.valid()&&version===loadVersion&&request===speechVersion&&line.value?.id===loaded.id&&line.value?.headCheckpointId===loaded.headCheckpointId,undefined,{onReadingPreferences:saved=>{if(app.user&&saved.version>=app.user.settings.version){const {editVersion:_,speech:__,...reading}=saved;app.user.settings={...app.user.settings,...reading}}}}))
  speech.value=controller;directory.value=false;await controller.initialize();if(scope.valid()&&request===speechVersion&&version===loadVersion)await controller.play(fromId)
}
async function locateSpeech(segment:SpeechSegment){if('checkpointId'in segment.anchor){await jump(segment.anchor.checkpointId);document.getElementById('world-speech-'+segment.id)?.scrollIntoView({block:'center'})}}
const fontSize=computed(()=>app.settings.fontSize)
const returnStory=ref('')
const returnLink=computed(()=>'/stories/'+(returnStory.value||storyId('world',String(route.params.worldId))))
const playLink=computed(()=>line.value?{path:`/worlds/${route.params.worldId}/play`,query:{line:line.value.id,...(returnStory.value?{story:returnStory.value}:{})}}:'/worlds')
const graphLink=computed(()=>({path:`/worlds/${route.params.worldId}/lines`,query:{...(line.value?{line:line.value.id}:{}),...(returnStory.value?{story:returnStory.value}:{})}}))
let observer:IntersectionObserver|null=null,timer:ReturnType<typeof setTimeout>|undefined,restoring=false,disposed=false,loadVersion=0,saveVersion=0
const deletedKey=(worldId:string,lineId:string)=>`world-line-deleted:${worldId}:${lineId}`
const isDeleted=(e:unknown)=>e instanceof ApiError&&e.code==='WORLD_LINE_DELETED'&&e.status===404
const readable=(id:string)=>history.value.some(checkpoint=>checkpoint.id===id)
let readingIdentity:{auth:number;owner:string}|null=null

function clearReading(){
  closeSpeech();selectedSpeech.value=''
  clearTimeout(timer);timer=undefined;observer?.disconnect();observer=null;saveVersion++
  readingIdentity=null
  line.value=null;history.value=[];directory.value=false;download.value=false;readingExport.value=false;snapshot.value=false;current.value='';saved.value='';restoring=false
  if(viewport.value)viewport.value.scrollTop=0
}

async function deletedFallback(worldId:string,lineId:string,valid:()=>boolean,remember:boolean){
  if(!valid())return
  clearReading();loading.value=true
  if(remember){
    await cacheSet(deletedKey(worldId,lineId),true)
    if(!valid())return
  }
  // A deleted-line cache is authoritative, but its old world pointer is not.
  // Require a fresh owned-world response before choosing a retained line.
  const info=await worldApi<WorldInfo>('/worlds/'+worldId)
  if(!valid())return
  world.value=info
  if(info.activeLineId&&info.activeLineId!==lineId){
    await router.replace({path:route.path,query:{...route.query,line:info.activeLineId}})
  }else{
    loading.value=false
  }
}

function saveExitPosition(){
  // Route navigation may already have changed fullPath. This local-only flush
  // belongs to the identity that loaded the prose, never the next account.
  if(disposed||loading.value||restoring||!world.value||!line.value||!readable(current.value)||!readingIdentity)return
  if(!readingIdentity.owner||readingIdentity.auth!==authGeneration()||readingIdentity.owner!==accountId())return
  const key=`world-reading:${world.value.id}:${line.value.id}`
  const position={checkpointId:current.value,scroll:viewport.value?.scrollTop||0}
  // cacheSet synchronously captures owner/epoch and serializes this snapshot;
  // no response or network action may affect the unmounted reader.
  void cacheSet(key,position).catch(()=>{})
}

async function save(){
  if(disposed||loading.value||restoring||!world.value||!line.value||!readable(current.value))return
  const scope=capture(),version=loadVersion,request=++saveVersion,info=world.value,loaded=line.value
  const position={checkpointId:current.value,scroll:viewport.value?.scrollTop||0}
  const valid=()=>!disposed&&scope.valid()&&version===loadVersion&&request===saveVersion&&line.value?.id===loaded.id&&world.value?.id===info.id
  if(await cacheGet<boolean>(deletedKey(info.id,loaded.id))){if(valid())await load();return}
  if(!valid())return
  await cacheSet(`world-reading:${info.id}:${loaded.id}`,position)
  if(!valid()||!app.online||snapshot.value)return
  try{
    const result=await worldApi<WorldLine>('/worlds/'+info.id+'/lines/'+loaded.id,{method:'PATCH',body:{expectedVersion:loaded.version,readingPosition:position}})
    if(valid()){line.value=result;saved.value='位置已保存'}
  }catch(e){
    if(!valid())return
    if(isDeleted(e)){
      const recoveryValid=()=>!disposed&&scope.valid()&&version===loadVersion
      try{await deletedFallback(info.id,loaded.id,recoveryValid,true)}catch(failure){if(recoveryValid()){error.value=(failure as Error).message;loading.value=false}}
    }else saved.value='位置已保存在本机'
  }
}
function scrolling(){if(restoring||loading.value||!line.value)return;clearTimeout(timer);timer=setTimeout(()=>void save(),700)}
async function jump(id:string){
  if(!line.value||!readable(id)||loading.value)return
  const scope=capture(),version=loadVersion
  current.value=id;directory.value=false
  await nextTick()
  if(disposed||!scope.valid()||version!==loadVersion||!readable(id))return
  document.getElementById('world-read-'+id)?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})
}

async function load(fallbackId=''){
  const scope=capture(),version=++loadVersion,worldId=String(route.params.worldId),requested=String(route.query.line||''),requestedCheckpoint=String(route.query.checkpoint||'')
  const valid=()=>!disposed&&scope.valid()&&version===loadVersion
  clearReading();readingImages.value=undefined;world.value=null;error.value='';loading.value=true
  returnStory.value=''
  let lineId=''
  try{
    if(isWorldDeleted(worldId))throw new ApiError('WORLD_DELETED','这个世界已删除',404)
    const packageId=String(route.query.download||fallbackId||'')
    let stored:OfflineWorldPackage|undefined=packageId?await readWorldPackage(packageId):!app.online?await downloadedWorld(worldId,requested||undefined):undefined
    if(!valid())return
    if(packageId&&(!stored||stored.payload.world.id!==worldId||requested&&stored.payload.line.id!==requested))throw new Error('这份下载已失效，请联网重新下载')
    if(!app.online&&!stored)throw new Error('这条世界线尚未下载，请联网下载后再阅读')
    let info:PublicReadWorld
    if(stored)info=stored.payload.world
    else{
      const response=await worldApi<WorldInfo&{offlineSnapshot?:boolean;offlinePackageId?:string}>('/worlds/'+worldId,{cache:true,readOnlyWorld:true})
      if(!valid())return
      if(response.offlineSnapshot){stored=await readWorldPackage(response.offlinePackageId||'');if(!valid())return;if(!stored)throw new Error('这份下载已失效，请联网重新下载');info=stored.payload.world}else info=response
    }
    world.value=info;lineId=requested||info.activeLineId||''
    if(!lineId){loading.value=false;return}
    const deleted=await cacheGet<boolean>(deletedKey(worldId,lineId))
    if(!valid())return
    if(deleted){await deletedFallback(worldId,lineId,valid,false);return}
    if(stored&&stored.payload.line.id!==lineId){stored=await downloadedWorld(worldId,lineId);if(!valid())return;if(!stored)throw new Error('这条世界线尚未下载，请联网下载后再阅读')}
    const loaded:PublicReadLine&{offlineSnapshot?:boolean;offlinePackageId?:string}=stored?stored.payload.line:await worldApi<WorldLine&{offlineSnapshot?:boolean;offlinePackageId?:string}>('/worlds/'+worldId+'/lines/'+lineId,{cache:true,readOnlyWorld:true})
    if(!valid())return
    if(loaded.offlineSnapshot){if(!loaded.offlinePackageId)throw new Error('下载资料不完整');await load(loaded.offlinePackageId);return}
    const [result,local]=await Promise.all([
      stored?Promise.resolve<{items:PublicReadCheckpoint[];offlineSnapshot?:boolean;offlinePackageId?:string}>({items:stored.payload.history}):worldApi<{items:WorldCheckpoint[];offlineSnapshot?:boolean;offlinePackageId?:string}>('/worlds/'+worldId+'/lines/'+lineId+'/history',{cache:true,readOnlyWorld:true}),
      cacheGet<{checkpointId:string;scroll:number}>(`world-reading:${worldId}:${lineId}`)
    ])
    if(!valid())return
    if(result.offlineSnapshot){if(!result.offlinePackageId)throw new Error('下载资料不完整');await load(result.offlinePackageId);return}
    line.value=loaded;history.value=result.items.filter(checkpoint=>checkpoint.text&&checkpoint.kind!=='start');snapshot.value=!!stored;readingImages.value=stored?.payload.images
    readingIdentity={auth:scope.auth,owner:scope.owner}
    const restore=[requestedCheckpoint,local?.checkpointId,loaded.readingPosition.checkpointId,history.value[0]?.id].find(id=>id&&readable(id))||''
    const origin=String(route.query.story||'')
    if(stored){
      if(stored.payload.origins.includes(origin)){if(origin.startsWith('book-')){const {isBookDeleted}=await import('../platform/book-cache');if(!valid())return;if(!isBookDeleted(origin.slice(5)))returnStory.value=origin}else returnStory.value=origin}
    }else if(restore&&/^(book|world)-[a-f0-9]{32}$/.test(origin)){
      try{
        const {api}=await import('../api');if(!valid())return
        await api('/stories/'+origin+'/context/validate',{method:'POST',body:{storyId:origin,mode:'read',perspective:'reader',entryOrigin:'worlds',worldId,worldLineId:lineId,checkpointId:restore}})
        if(!valid())return;returnStory.value=origin
      }catch(e){if(!(e instanceof ApiError&&[0,400,403,404,409,422].includes(e.status)))throw e}
    }
    if(!valid())return
    current.value=restore;loading.value=false;restoring=true
    await nextTick()
    if(!valid())return
    if(requestedCheckpoint&&restore===requestedCheckpoint)await jump(restore)
    else if(viewport.value)viewport.value.scrollTop=local?.checkpointId===restore?local.scroll||0:loaded.readingPosition.checkpointId===restore?loaded.readingPosition.scroll||0:0
    if(!valid())return
    observer=new IntersectionObserver(entries=>{
      if(!valid()||restoring||loading.value)return
      for(const entry of entries){
        const id=(entry.target as HTMLElement).dataset.checkpoint||''
        if(entry.isIntersecting&&readable(id))current.value=id
      }
    },{root:viewport.value,rootMargin:'-10% 0px -65% 0px',threshold:0})
    viewport.value?.querySelectorAll('[data-checkpoint]').forEach(element=>observer?.observe(element))
    restoring=false
  }catch(e){
    if(!valid())return
    if(lineId&&isDeleted(e)){
      try{await deletedFallback(worldId,lineId,valid,true)}catch(failure){if(valid()){error.value=(failure as Error).message;loading.value=false}}
    }else{error.value=(e as Error).message;loading.value=false;restoring=false}
  }
}
async function leave(){const scope=capture();await save();if(scope.valid()&&!disposed)await router.push(returnLink.value)}
function removed(event:Event){const target=(event as CustomEvent<{worldId:string;lineId?:string}>).detail;if(target?.worldId!==String(route.params.worldId)||target.lineId&&target.lineId!==line.value?.id&&target.lineId!==String(route.query.line||''))return;loadVersion++;clearReading();world.value=null;if(target.lineId)void load();else{loading.value=false;error.value='这个世界已删除'}}
function removedOrigin(event:Event){const id=(event as CustomEvent<{bookId:string}>).detail?.bookId;if(returnStory.value==='book-'+id){closeSpeech();returnStory.value=''}}
watch(()=>[route.params.worldId,route.query.line,route.query.checkpoint,route.query.story,route.query.download],()=>void load())
onMounted(()=>{void load();window.addEventListener('reader-world-deleted',removed);window.addEventListener('reader-book-deleted',removedOrigin)})
onBeforeUnmount(saveExitPosition)
onUnmounted(()=>{disposed=true;loadVersion++;clearReading();window.removeEventListener('reader-world-deleted',removed);window.removeEventListener('reader-book-deleted',removedOrigin)})
</script>
<template>
  <div class="world-read">
    <header class="world-play-header">
      <button class="icon-button" aria-label="返回故事入口" @click="leave"><ArrowLeft :size="21"/></button>
      <div class="world-play-title"><strong>{{world?.name||'世界阅读'}}</strong><small>{{line?.name}} · {{saved||(snapshot?'已下载':'阅读模式')}}</small></div>
      <nav aria-label="世界视图"><RouterLink :to="playLink"><Compass :size="17"/>游玩</RouterLink><span class="active">阅读</span><RouterLink :to="graphLink"><GitBranch :size="17"/>世界线</RouterLink></nav>
      <button class="icon-button" aria-label="世界正文目录" :disabled="!line||!history.length" @click="directory=true"><List :size="21"/></button>
    </header>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <div v-if="loading" class="loading">正在打开正文…</div>
    <div v-else-if="!line" class="empty">
      <p v-if="!error">这里已没有可阅读的世界线。</p>
      <RouterLink v-if="world?.status==='active'" :to="`/worlds/new?world=${world.id}`" class="world-primary-link">选择身份，进入世界</RouterLink>
      <RouterLink to="/worlds" class="world-primary-link">返回世界列表</RouterLink>
    </div>
    <div v-else class="world-read-layout">
      <aside class="world-read-directory" aria-label="世界正文目录"><h3>{{line.name}}</h3><button v-for="c in history" :key="c.id" :class="{active:current===c.id}" @click="jump(c.id)">{{c.title}}</button></aside>
      <div ref="viewport" class="world-read-scroll" @scroll="scrolling">
        <article v-for="c in history" :id="'world-read-'+c.id" :key="c.id" :data-checkpoint="c.id" class="world-read-chapter" :style="{fontSize:fontSize+'px',lineHeight:app.settings.lineHeight}"><h1>{{c.title}}</h1><StoryImages :scope="line?{kind:'world',worldId:String(route.params.worldId),lineId:line.id,checkpointId:c.id}:null" purpose="scene" :offline="line?worldImagesAt(readingImages,String(route.params.worldId),line.id,c.id,history.map(c=>c.id)):undefined" :editable="!snapshot&&c.id===line?.headCheckpointId&&line?.status==='active'&&world?.status==='active'" :foldable="true"/><div class="world-prose"><template v-for="(paragraph,paragraphIndex) in speechParagraphs.get(c.id)||[]" :key="paragraph.id"><StoryImages :scope="line?{kind:'world',worldId:String(route.params.worldId),lineId:line.id,checkpointId:c.id}:null" purpose="illustration" side="before" :content-id="c.id" :paragraph-id="String(paragraphIndex)" :offline="line?worldImagesAt(readingImages,String(route.params.worldId),line.id,c.id,history.map(row=>row.id)):undefined" :editable="selectedSpeech===paragraph.id&&!snapshot&&app.online&&c.id===line?.headCheckpointId&&line?.status==='active'&&world?.status==='active'" :foldable="true"/><p :id="'world-speech-'+paragraph.id" class="world-speech-paragraph" :class="{'speech-playing':speech?.state.value.status==='playing'&&speech.state.value.currentId===paragraph.id,'speech-completed':speech?.lastCompleted.value?.id===paragraph.id}" @click="selectedSpeech=paragraph.id">{{paragraph.text}}</p><StoryImages :scope="line?{kind:'world',worldId:String(route.params.worldId),lineId:line.id,checkpointId:c.id}:null" purpose="illustration" side="after" :content-id="c.id" :paragraph-id="String(paragraphIndex)" :offline="line?worldImagesAt(readingImages,String(route.params.worldId),line.id,c.id,history.map(row=>row.id)):undefined" :editable="selectedSpeech===paragraph.id&&!snapshot&&app.online&&c.id===line?.headCheckpointId&&line?.status==='active'&&world?.status==='active'" :foldable="true"/></template></div></article>
        <div v-if="!history.length" class="empty"><p>完成一个场景后，正文就会出现在这里。</p></div>
        <div v-else class="world-read-end"><RouterLink v-if="app.online" :to="playLink" class="world-primary-link">回到故事，继续行动<Compass :size="17"/></RouterLink><small v-else class="muted">联网后可继续故事</small></div>
      </div>
    </div>
    <Sheet v-if="directory&&line" title="目录" @close="directory=false"><div class="stack divider"><button :disabled="!app.online||!history.length" @click="directory=false;download=true"><Download :size="18"/>下载这条世界线</button><button :disabled="!app.online||snapshot||!line.headCheckpointId" @click="directory=false;readingExport=true">导出阅读文件 · TXT / EPUB</button><button :disabled="!history.length" @click="listen">播放公开正文 · 系统听书</button><RouterLink to="/downloads" class="inline-link">已下载的故事线</RouterLink></div><button v-for="c in history" :key="c.id" class="chapter-link" :class="{active:current===c.id}" @click="jump(c.id)">{{c.title}}</button></Sheet>
    <SpeechBar v-if="speech&&line" :controller="speech" @locate="locateSpeech" @close="closeSpeech"/>
    <Sheet v-if="readingExport&&world&&line&&!snapshot" title="导出阅读文件" @close="readingExport=false"><ReadingExport :scope="{kind:'world',worldId:world.id,lineId:line.id,checkpointId:line.headCheckpointId}"/></Sheet>
    <Sheet v-if="download&&world&&line" title="下载世界线" @close="download=false"><DownloadWorldReading :world-id="world.id" :line-id="line.id" :head-id="line.headCheckpointId" :origin="returnStory" :line-name="line.name"/></Sheet>
  </div>
</template>
