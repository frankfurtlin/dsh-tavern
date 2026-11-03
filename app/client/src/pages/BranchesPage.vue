<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {GitBranch,ArrowLeft,BookOpen,RefreshCw} from 'lucide-vue-next'
import {api,authGeneration} from '../api'
import type {Book} from '../api/types'
import {mergeTimelinePage,timelineReadLocation} from '../api/story-timeline'
import type {StoryTimelineData,TimelineBranch,TimelineChapter} from '../api/story-timeline'
import Sheet from '../components/Sheet.vue'
import ComparisonLauncher from '../components/ComparisonLauncher.vue'
import StoryTimeline from '../components/StoryGraph.vue'
import {useRequestScope} from '../platform/request-scope'
import {accountId} from '../platform/bridge'
import {useApp} from '../stores/app'
import '../styles/story-graph.css'

const route=useRoute(),router=useRouter(),store=useApp(),capture=useRequestScope()
const book=ref<Book|null>(null),timeline=ref<StoryTimelineData|null>(null),editing=ref<TimelineBranch|null>(null),name=ref(''),error=ref(''),busy=ref(false),loading=ref(true),chapterLoading=ref(false)
let loadGeneration=0,actionGeneration=0,pageGeneration=0,contentAuth=-1,contentOwner=''
const activeChapter=computed(()=>{
  if(!timeline.value)return ''
  if(route.query.chapter)return String(route.query.chapter)
  const saved=book.value?.lastProgress
  return saved?.branchId===timeline.value.currentBranchId&&saved.revisionId===timeline.value.branches.find(branch=>branch.id===saved.branchId)?.headRevisionId?saved.anchor.chapterId:''
})
const currentBranch=computed(()=>timeline.value?.branches.find(branch=>branch.id===timeline.value?.currentBranchId))
const backToReading=computed(()=>({path:`/books/${route.params.bookId}/read`,query:currentBranch.value?{branch:currentBranch.value.id}:{}}))
function contentValid(){return !!book.value&&!!timeline.value&&contentAuth===authGeneration()&&contentOwner===accountId()&&book.value.id===String(route.params.bookId)&&timeline.value.bookId===book.value.id}
function invalidate(){++loadGeneration;++actionGeneration;++pageGeneration;contentAuth=-1;contentOwner='';book.value=null;timeline.value=null;editing.value=null;name.value='';error.value='';busy.value=false;chapterLoading.value=false;loading.value=false}
async function load(){
  const scope=capture(),request=++loadGeneration,id=String(route.params.bookId),valid=()=>scope.valid()&&request===loadGeneration
  ++pageGeneration;chapterLoading.value=false;loading.value=true;error.value=''
  try{
    const loadedBook=await api<Book>('/books/'+id,{cache:true});if(!valid())return
    const selected=String(route.query.branch||loadedBook.lastProgress?.branchId||loadedBook.originalBranchId)
    const data=await api<StoryTimelineData>(`/books/${id}/timeline?branchId=${encodeURIComponent(selected)}`,{cache:true});if(!valid())return
    if(data.bookId!==id)throw new Error('这份时间线不属于当前书籍，请重新打开')
    book.value=loadedBook;timeline.value=data;contentAuth=scope.auth;contentOwner=scope.owner
  }catch(e){if(valid())error.value=(e as Error).message}
  finally{if(valid())loading.value=false}
}
async function loadChapters(offset:number){
  if(chapterLoading.value||!contentValid()||!Number.isInteger(offset)||offset<0)return
  const current=timeline.value!,scope=capture(),request=++pageGeneration,loadedGeneration=loadGeneration,id=current.bookId,selected=current.currentBranchId
  const valid=()=>scope.valid()&&request===pageGeneration&&loadedGeneration===loadGeneration&&contentValid()&&timeline.value?.currentBranchId===selected
  chapterLoading.value=true;error.value=''
  try{
    const page=await api<StoryTimelineData>(`/books/${id}/timeline?branchId=${encodeURIComponent(selected)}&chapterOffset=${offset}&chapterLimit=20`,{cache:true})
    if(!valid())return
    if(page.branches.some(branch=>timeline.value?.branches.find(item=>item.id===branch.id)?.headRevisionId!==branch.headRevisionId)){
      await load();return
    }
    timeline.value=mergeTimelinePage(timeline.value!,page)
  }catch(e){if(valid())error.value=(e as Error).message}
  finally{if(valid())chapterLoading.value=false}
}
function liveBranch(branch:TimelineBranch){return contentValid()?timeline.value?.branches.find(item=>item.id===branch.id&&item.headRevisionId===branch.headRevisionId&&item.version===branch.version):undefined}
function open(branch:TimelineBranch,chapter?:TimelineChapter){
  const current=liveBranch(branch);if(!current)return
  const saved=chapter?current.chapters.find(item=>item.id===chapter.id):undefined
  if(chapter&&(!saved||!saved.readable))return
  router.push(timelineReadLocation(book.value!.id,current,saved))
}
function openFork(branch:TimelineBranch){
  const current=liveBranch(branch),anchor=current?.forkAnchor;if(!anchor||anchor.bookId!==book.value?.id)return
  router.push({path:`/books/${anchor.bookId}/read`,query:{branch:anchor.branchId,revision:anchor.revisionId,chapter:anchor.chapterId,block:anchor.blockId,edge:anchor.end===0?'start':'end'}})
}
function rename(branch:TimelineBranch){const current=liveBranch(branch);if(!current||busy.value||!store.online)return;editing.value=current;name.value=current.name;error.value=''}
function closeEditor(){editing.value=null}
async function update(branch:TimelineBranch,status?:string){
  const current=liveBranch(branch);if(!current||busy.value||!store.online)return
  if(status&&(!current.parentBranchId||current.id===timeline.value?.originalBranchId))return
  const scope=capture(),request=++actionGeneration,id=current.id,version=current.version,text=name.value.trim(),valid=()=>scope.valid()&&request===actionGeneration&&contentValid()
  if(!status&&!text)return
  const body={expectedVersion:version,...(status?{status}:{name:text})}
  busy.value=true;error.value=''
  try{
    await api('/branches/'+id,{method:'PATCH',body});if(!valid())return
    if(editing.value?.id===id)editing.value=null
    store.message(status?(status==='archived'?'故事线已归档，正文仍可阅读':'故事线已恢复'):'故事线名称已保存')
    await load()
  }catch(e){if(valid())error.value=(e as Error).message}
  finally{if(valid())busy.value=false}
}
watch(()=>[route.params.bookId,route.query.branch],()=>{invalidate();load()})
watch(()=>store.user?.id,()=>{invalidate();if(store.user)load()})
onMounted(()=>{window.addEventListener('reader-private-cleared',invalidate);load()})
onUnmounted(()=>{window.removeEventListener('reader-private-cleared',invalidate);++loadGeneration;++actionGeneration;++pageGeneration})
</script>

<template>
  <div class="timeline-page">
    <RouterLink :to="backToReading" class="inline-link"><ArrowLeft :size="18" aria-hidden="true"/>返回阅读</RouterLink>
    <div class="page-heading timeline-page-heading">
      <div><span class="graph-book-title">{{book?.title}}</span><h1>故事时间线</h1></div>
      <div v-if="timeline" class="row wrap"><ComparisonLauncher :story-id="'book-'+timeline.bookId" kind="book" :resource-id="timeline.bookId" :targets="timeline.branches.map(line=>({id:line.id,name:line.name,nodeId:line.headRevisionId}))" :current-line-id="timeline.currentBranchId"/><span class="graph-line-count" :aria-label="timeline.branches.filter(branch=>branch.status!=='archived').length+' 条故事线'"><GitBranch :size="17" aria-hidden="true"/>{{timeline.branches.filter(branch=>branch.status!=='archived').length}}</span></div>
    </div>
    <p v-if="error" class="error" role="alert">{{error}}<button :disabled="loading" @click="load"><RefreshCw :size="16" aria-hidden="true"/>重新加载</button></p>
    <div v-if="loading&&!timeline" class="loading timeline-loading" role="status">正在加载…</div>
    <StoryTimeline v-else-if="timeline&&timeline.branches.length" :data="timeline" :active-chapter-id="activeChapter" :busy="busy" :chapter-loading="chapterLoading" :online="store.online" @open="open" @fork="openFork" @rename="rename" @status="branch=>update(branch,branch.status==='archived'?'active':'archived')" @load-chapters="loadChapters"/>
    <div v-else-if="!loading&&!error" class="empty"><BookOpen :size="36" aria-hidden="true"/><h2>暂无故事线</h2><RouterLink :to="backToReading" class="inline-link">打开正文<ArrowLeft :size="16" aria-hidden="true"/></RouterLink></div>
    <Sheet v-if="editing" title="为故事线命名" @close="closeEditor"><p v-if="error" class="error" role="alert">{{error}}</p><form @submit.prevent="update(editing!)"><label>故事线名称<input v-model="name" maxlength="100" required :disabled="busy" autocomplete="off"></label><button class="primary" :disabled="busy||!name.trim()">{{busy?'正在保存…':'保存名称'}}</button></form></Sheet>
  </div>
</template>
