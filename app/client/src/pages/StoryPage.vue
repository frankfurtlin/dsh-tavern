<script setup lang="ts">
import {computed,ref,watch,onMounted,onUnmounted} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,BookOpen,MoreHorizontal,Archive,RotateCcw} from 'lucide-vue-next'
import {getStory,contextRoute} from '../api/stories'
import type {StoryDetail,StoryMode} from '../api/stories'
import {api,ApiError} from '../api'
import {worldApi} from '../world/api'
import Sheet from '../components/Sheet.vue'
import ReadingExport from '../components/ReadingExport.vue'
import BookRemoval from '../components/BookRemoval.vue'
import WorkEntry from '../components/WorkEntry.vue'
import PrivateBackup from '../components/PrivateBackup.vue'
import type {ImageScope} from '../api/assets'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import '../styles/story-entry.css'

const route=useRoute(),router=useRouter(),store=useApp(),capture=useRequestScope()
const detail=ref<StoryDetail|null>(null),loading=ref(true),busy=ref(false),error=ref(''),menu=ref(false),name=ref('')
const stale=ref(false),refreshed=ref(false),backup=ref(false),readingExport=ref(false)
let loadEpoch=0,operationEpoch=0,menuEpoch=0
function openMenu(){menuEpoch++;operationEpoch++;busy.value=false;error.value='';stale.value=false;refreshed.value=false;name.value=detail.value?.title||'';menu.value=true}
function closeMenu(){menuEpoch++;operationEpoch++;busy.value=false;menu.value=false;stale.value=false;refreshed.value=false}
function openBackup(){closeMenu();backup.value=true}
function openReadingExport(){if(!imageScope.value)return;closeMenu();readingExport.value=true}
const bookId=computed(()=>detail.value?.kind==='book'?detail.value.id.slice(5):null)
const worldId=computed(()=>detail.value?.kind==='world'?detail.value.id.slice(6):null)
const imageScope=computed<ImageScope|null>(()=>{const d=detail.value;if(!d)return null;const c=d.kind==='book'?d.resume.read.context:d.resume.play.context;if(c?.bookId&&c.readerBranchId&&c.anchor)return{kind:'book',bookId:c.bookId,branchId:c.readerBranchId,revisionId:c.anchor.revisionId};if(c?.worldId&&c.worldLineId&&c.checkpointId)return{kind:'world',worldId:c.worldId,lineId:c.worldLineId,checkpointId:c.checkpointId};return null})
const timeline=computed(()=>bookId.value?`/books/${bookId.value}/branches`:worldId.value?`/worlds/${worldId.value}/lines`:null)
const newWorld=computed(()=>{
  const target=detail.value?.resume.read.context
  return {path:'/worlds/new',query:target?.bookId&&target.readerBranchId&&target.anchor?{book:target.bookId,branch:target.readerBranchId,anchor:JSON.stringify(target.anchor)}:{book:bookId.value||''}}
})
async function load(){
  backup.value=false;readingExport.value=false
  const scope=capture(),epoch=++loadEpoch;operationEpoch++;menuEpoch++;busy.value=false;menu.value=false;stale.value=false;refreshed.value=false;loading.value=true;error.value='';detail.value=null
  try{const result=await getStory(String(route.params.storyId));if(scope.valid()&&epoch===loadEpoch){detail.value=result;name.value=result.title}}
  catch(e){if(scope.valid()&&epoch===loadEpoch)error.value=(e as Error).message}
  finally{if(scope.valid()&&epoch===loadEpoch)loading.value=false}
}
async function enter(mode:StoryMode){
  const target=detail.value?.resume[mode]
  if(target?.available&&target.context){try{await router.push(contextRoute(target.context))}catch(e){error.value=(e as Error).message};return}
  if(mode==='play'&&bookId.value&&detail.value?.canCreateWorld){await router.push({path:'/worlds/new',query:{book:bookId.value}});return}
  if(mode==='play'&&worldId.value){await router.push({path:'/worlds/new',query:{world:worldId.value}});return}
  store.message(mode==='chat'?'先在阅读中选择一位已经登场的角色':'这个故事暂时没有可恢复的位置')
}
async function manage(status?:string){
  const story=detail.value,scope=capture();if(!story||busy.value||stale.value)return
  const operation=++operationEpoch,editor=menuEpoch,valid=()=>scope.valid()&&operation===operationEpoch&&editor===menuEpoch&&menu.value&&detail.value?.id===story.id
  busy.value=true;error.value=''
  const body={expectedVersion:story.version,...(status?{status}:story.kind==='book'?{title:name.value}:{name:name.value})}
  try{
    if(story.kind==='book')await api('/books/'+bookId.value,{method:'PATCH',body})
    else await worldApi('/worlds/'+worldId.value,{method:'PATCH',body})
    if(valid()){closeMenu();await load()}
  }catch(e){if(valid()){error.value=(e as Error).message;stale.value=e instanceof ApiError&&e.status===409}}
  finally{if(scope.valid()&&operation===operationEpoch)busy.value=false}
}
function deletionConflict(){stale.value=true;error.value='故事已在另一处更新，请载入最新信息。'}
async function deleted(){closeMenu();await router.replace('/library')}
async function refreshManagement(){
  const story=detail.value,scope=capture();if(!story||busy.value||!menu.value)return
  const operation=++operationEpoch,editor=menuEpoch,valid=()=>scope.valid()&&operation===operationEpoch&&editor===menuEpoch&&menu.value&&detail.value?.id===story.id
  busy.value=true
  try{const fresh=await api<StoryDetail>('/stories/'+story.id);if(valid()){detail.value=fresh;stale.value=false;refreshed.value=true;error.value=''}}
  catch(e){if(valid())error.value=(e as Error).message}
  finally{if(valid())busy.value=false}
}
function removed(event:Event){const id=(event as CustomEvent<{bookId:string}>).detail?.bookId;if(String(route.params.storyId)==='book-'+id){loadEpoch++;detail.value=null;loading.value=false;closeMenu();void router.replace('/library')}else if(detail.value?.source?.bookId===id){detail.value.source.sourceRemoved=true}}
onMounted(()=>window.addEventListener('reader-book-deleted',removed));onUnmounted(()=>{loadEpoch++;window.removeEventListener('reader-book-deleted',removed)})
watch(()=>route.params.storyId,load,{immediate:true})
</script>

<template>
  <section class="story-entry" aria-label="故事入口">
    <div class="story-entry-top"><RouterLink to="/explore" class="story-back"><ArrowLeft :size="19"/>返回探索</RouterLink><button v-if="detail" class="icon-button" aria-label="管理故事" @click="openMenu"><MoreHorizontal :size="22"/></button></div>
    <p v-if="error&&!menu" class="error" role="alert">{{error}}<button :disabled="busy" @click="load">重新加载</button></p>
    <div v-if="loading" class="loading" role="status">正在打开故事…</div>
    <div v-else-if="!detail" class="empty"><BookOpen :size="32"/><h1>暂时无法打开故事</h1><RouterLink to="/library" class="inline-link">回到书架</RouterLink></div>
    <template v-else>
      <RouterLink v-if="worldId" :to="'/worlds/'+worldId+'/author'" class="inline-link">作者配置与公开发布</RouterLink>
      <WorkEntry :key="detail.id" :story="detail" :image-scope="imageScope" :timeline="timeline" @read="enter('read')" @chat="enter('chat')"/>
    </template>
    <Sheet v-if="menu&&detail" title="管理故事" @close="closeMenu">
      <p v-if="error" class="error" role="alert">{{error}}</p><button v-if="stale" :disabled="busy||!store.online" @click="refreshManagement">载入最新信息</button><small v-if="refreshed" role="status">最新名称：{{detail.title}}</small>
      <label>故事名称<input v-model="name" :maxlength="detail.kind==='world'?160:200"></label><button :disabled="busy||stale||!store.online||!name.trim()" @click="manage()">保存名称</button>
      <div class="divider"><button :disabled="busy||stale||!store.online" @click="manage(detail.status==='archived'?'active':'archived')"><Archive v-if="detail.status!=='archived'" :size="18"/><RotateCcw v-else :size="18"/>{{detail.status==='archived'?'恢复故事':'归档故事'}}</button><RouterLink v-if="bookId&&store.worldEnabled&&detail.canCreateWorld" :to="newWorld" class="inline-link">从这个时间点另开世界</RouterLink></div>
      <div class="divider actions"><button :disabled="!store.online||!imageScope" @click="openReadingExport">导出阅读文件 · TXT / EPUB</button><button :disabled="!store.online" @click="openBackup">导出私人完整备份</button></div>
      <details v-if="bookId" class="divider"><summary class="danger">移除小说</summary><BookRemoval :key="bookId" :book-id="bookId" :version="detail.version" :blocked="stale" @conflict="deletionConflict" @deleted="deleted"/></details>
    </Sheet>
    <Sheet v-if="readingExport&&detail&&imageScope" title="导出阅读文件" @close="readingExport=false"><ReadingExport :scope="imageScope"/></Sheet>
    <Sheet v-if="backup&&detail" title="导出私人完整备份" @close="backup=false"><PrivateBackup :key="detail.id" mode="export" :story-id="detail.id"/></Sheet>
  </section>
</template>
