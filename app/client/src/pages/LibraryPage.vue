<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {Plus,BookOpen,Archive,RotateCcw,MoreHorizontal,Search,Star,ArrowUpRight,Clock3} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
import StoryImages from '../components/StoryImages.vue'
import OriginalImageStories from '../components/OriginalImageStories.vue'
import {api,allPages,newKey} from '../api'
import {storyPath} from '../api/stories'
import type {Book,ImportStatus,AcceptResult,ReadingStats} from '../api/types'
import {pickFile,cacheSet,cacheGet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const store=useApp(),router=useRouter(),books=ref<Book[]>([]),status=ref('active'),error=ref(''),busy=ref(false),loading=ref(true),importing=ref(false),preview=ref<ImportStatus|null>(null),title=ref(''),encoding=ref(''),manage=ref<Book|null>(null),rename=ref(''),importTask=ref('')
const originalImages=ref(useRoute().query.examples==='1')
const lastRead=computed(()=>status.value==='active'?[...books.value].filter(book=>book.lastProgress).sort((a,b)=>b.lastProgress!.updatedAt.localeCompare(a.lastProgress!.updatedAt))[0]:undefined)
const capture=useRequestScope(),searchQuery=ref(''),sortBy=ref('recent'),onlyFavorites=ref(false),tagFilter=ref(''),tagInput=ref(''),readingStats=ref<ReadingStats|null>(null)
const tags=computed(()=>[...new Set(books.value.flatMap(book=>book.tags||[]))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const visibleBooks=computed(()=>{const query=searchQuery.value.trim().toLocaleLowerCase();return books.value.filter(book=>(!onlyFavorites.value||book.favorite)&&(!tagFilter.value||book.tags?.includes(tagFilter.value))&&(!query||(book.title+' '+(book.tags||[]).join(' ')).toLocaleLowerCase().includes(query))).sort((a,b)=>sortBy.value==='title'?a.title.localeCompare(b.title,'zh-CN'):sortBy.value==='added'?(b.createdAt||'').localeCompare(a.createdAt||''):(b.lastProgress?.updatedAt||b.createdAt||'').localeCompare(a.lastProgress?.updatedAt||a.createdAt||''))})
async function loadStats(){const scope=capture();try{const data=await api<ReadingStats>('/reading-stats?offsetMinutes='+(-new Date().getTimezoneOffset()),{cache:true});if(scope.valid())readingStats.value=data}catch{/* Reading remains available if statistics cannot load. */}}
async function favorite(book:Book){const scope=capture();if(busy.value)return;busy.value=true;try{const result=await api<Book>('/books/'+book.id,{method:'PATCH',body:{expectedVersion:book.version,favorite:!book.favorite}});if(scope.valid()){books.value=books.value.map(item=>item.id===result.id?result:item);store.message(result.favorite?'已收藏这本书':'已取消收藏')}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
function manageBook(book:Book){manage.value=book;rename.value=book.title;tagInput.value=(book.tags||[]).join('，')}
let pollTimer:ReturnType<typeof setTimeout>|undefined,loadGeneration=0,importGeneration=0,actionGeneration=0
async function load(){
  const scope=capture(),generation=++loadGeneration,tab=status.value,valid=()=>scope.valid()&&generation===loadGeneration&&tab===status.value
  try{const result=await allPages<Book>('/books?status='+tab);if(valid())books.value=result}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())loading.value=false}
}
function read(book:Book){router.push(storyPath('book',book.id))}
async function demo(){
  const scope=capture(),action=++actionGeneration,valid=()=>scope.valid()&&action===actionGeneration;busy.value=true
  try{const result=await api<AcceptResult&{bookId:string}>('/books/demo',{method:'POST',body:{},key:'private-demo-v1'});if(!valid())return;await load();if(valid())await router.push(`/books/${result.bookId}/read?branch=${result.branchId}`)}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function importFile(){
  const scope=capture(),generation=++importGeneration,valid=()=>scope.valid()&&generation===importGeneration;actionGeneration++;clearTimeout(pollTimer);error.value='';busy.value=false
  try{
    const file=await pickFile();if(!valid()||!file)return;importing.value=true;preview.value=null
    const form=new FormData();form.append('file',file)
    const response=await api<{importTaskId:string}>('/books/imports',{method:'POST',body:form,key:newKey()});if(!valid())return
    importTask.value=response.importTaskId;await cacheSet('pending-import',response.importTaskId);if(valid())await checkImport()
  }catch(e){if(valid())error.value=(e as Error).message}
}
async function checkImport(){
  const scope=capture(),generation=importGeneration,id=importTask.value,valid=()=>scope.valid()&&generation===importGeneration&&id===importTask.value
  if(!id)return
  try{
    const result=await api<ImportStatus>('/books/imports/'+id);if(!valid())return
    if(result.state==='failed'||result.state==='cancelled'){error.value='导入失败：'+result.errorCode;return}
    if(result.importStatus==='awaiting_confirmation'){preview.value=result;title.value=result.title;encoding.value=result.encoding;return}
    if(result.importStatus==='reading_ready'){if(await finishImport(valid))await load();return}
    clearTimeout(pollTimer);pollTimer=setTimeout(()=>{if(valid())void checkImport()},1500)
  }catch(e){if(valid())error.value=(e as Error).message}
}
async function finishImport(valid:()=>boolean){
  if(!valid())return false
  importing.value=false;preview.value=null
  try{await cacheSet('pending-import','')}catch{if(valid())store.message('书籍已保存，本机导入记录暂未同步。')}
  return valid()
}
async function previewEncoding(){
  const scope=capture(),generation=importGeneration,id=importTask.value,action=++actionGeneration,selectedEncoding=encoding.value,valid=()=>scope.valid()&&generation===importGeneration&&id===importTask.value&&action===actionGeneration
  busy.value=true;error.value=''
  try{const result=await api<ImportStatus>('/books/imports/'+id+'/preview',{method:'POST',body:{encoding:selectedEncoding}});if(valid())preview.value=result}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function confirm(reuse=false){
  if(!preview.value)return
  const scope=capture(),generation=importGeneration,id=importTask.value,action=++actionGeneration,valid=()=>scope.valid()&&generation===importGeneration&&id===importTask.value&&action===actionGeneration
  const body={parserVersion:preview.value.parserVersion,title:title.value,encoding:encoding.value||null,reuseDuplicate:reuse,prepareCharacters:true,chapterOrder:preview.value.chapters.map(c=>c.id)}
  busy.value=true
  try{const result=await api<{bookId:string;branchId:string}>('/books/imports/'+id+'/confirm',{method:'POST',key:'confirm-'+id,body});if(await finishImport(valid))await router.push(`/books/${result.bookId}/read?branch=${result.branchId}`)}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function edit(action:string){
  if(!manage.value)return
  const scope=capture(),selected=manage.value,request=++actionGeneration,valid=()=>scope.valid()&&request===actionGeneration;busy.value=true
  try{await api('/books/'+selected.id,{method:'PATCH',body:{expectedVersion:selected.version,...(action==='rename'?{title:rename.value,tags:tagInput.value.split(/[,，]/).map(tag=>tag.trim()).filter(Boolean)}:{status:action})}});if(!valid())return;manage.value=null;await load()}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
function removed(event:Event){const id=(event as CustomEvent<{bookId:string}>).detail?.bookId;loadGeneration++;books.value=books.value.filter(book=>book.id!==id);if(manage.value?.id===id)manage.value=null;void load()}
onMounted(async()=>{window.addEventListener('reader-book-deleted',removed);const scope=capture(),generation=importGeneration;await load();void loadStats();if(!scope.valid()||generation!==importGeneration)return;const pending=await cacheGet<string>('pending-import');if(!scope.valid()||generation!==importGeneration)return;importTask.value=pending||'';if(pending){importing.value=true;await checkImport()}})
onUnmounted(()=>{clearTimeout(pollTimer);loadGeneration++;importGeneration++;actionGeneration++;window.removeEventListener('reader-book-deleted',removed)})
</script>
<template><div class="page-heading row between"><div><span class="eyebrow">私人藏书 · 随时续读</span><h1>你的书架</h1><p>从上次停下的地方，继续读下去。</p></div><div class="actions"><button :disabled="busy||!store.online" @click="demo">加入示例书</button><button :disabled="!store.online" @click="originalImages=true">原创图文故事</button><button class="primary" :disabled="!store.online" @click="importFile"><Plus :size="18"/>导入小说</button></div></div>
<div class="library-overview"><section v-if="lastRead&&!loading" class="reading-hero"><img :src="lastRead.coverUrl" alt="" class="hero-cover" width="88" height="125"><div class="reading-hero-copy"><span class="eyebrow">上次读到的故事</span><strong>{{lastRead.title}}</strong><p>阅读位置已保存，故事在这里等你。</p></div><button class="primary" @click="read(lastRead)">接着读这本<BookOpen :size="18"/></button></section><RouterLink v-if="readingStats" to="/insights" class="reading-daily"><div class="row between"><span class="eyebrow"><Clock3 :size="16"/>今日阅读</span><ArrowUpRight :size="18"/></div><p><strong>{{Number((readingStats.todaySeconds/60).toFixed(1))}}</strong><span> / {{readingStats.goalMinutes}} 分钟</span></p><progress :value="Math.min(readingStats.todaySeconds,readingStats.goalMinutes*60)" :max="readingStats.goalMinutes*60" aria-label="今日阅读目标进度"/><small>{{readingStats.streakDays?'已连续阅读 '+readingStats.streakDays+' 天':'每一页，都是新的开始'}}</small></RouterLink></div>
<div class="tabs"><button :class="{active:status==='active'}" @click="status='active';load()">正在阅读</button><button :class="{active:status==='archived'}" @click="status='archived';load()">已归档</button><button :class="{active:onlyFavorites}" :aria-pressed="onlyFavorites" @click="onlyFavorites=!onlyFavorites"><Star :size="16"/>只看收藏</button></div>
<div class="collection-tools"><label class="search-field"><Search :size="18"/><input v-model="searchQuery" type="search" aria-label="搜索书架" placeholder="搜索书名或标签" maxlength="100"></label><label class="compact-select">排序<select v-model="sortBy" aria-label="书架排序"><option value="recent">最近阅读</option><option value="added">最近加入</option><option value="title">书名顺序</option></select></label></div><div v-if="tags.length" class="tag-filters"><button :class="{active:!tagFilter}" @click="tagFilter=''">全部标签</button><button v-for="tag in tags" :key="tag" :class="{active:tagFilter===tag}" @click="tagFilter=tag">{{tag}}</button></div>
<p v-if="error&&!importing" class="error" role="alert">{{error}}</p><div v-if="loading" class="loading">正在打开书架…</div><div v-else-if="!books.length" class="empty library-empty"><span class="empty-mark"><BookOpen :size="36"/></span><h2>{{status==='archived'?'这里还没有归档故事':'第一本故事，等你打开'}}</h2><p>导入 TXT 或无 DRM EPUB，或先读《雾港来信》。<br>阅读无需配置模型。</p></div><div v-else-if="!visibleBooks.length" class="empty"><h2>没有找到这本故事</h2><p>试试其他书名，或清除收藏与标签筛选。</p><button @click="searchQuery='';onlyFavorites=false;tagFilter=''">清除筛选</button></div><div v-else class="book-grid"><article v-for="book in visibleBooks" :key="book.id" class="book-item"><RouterLink :to="storyPath('book',book.id)" class="book-cover-entry" :aria-label="'打开'+book.title"><StoryImages passive class="image-library-cover" :scope="{kind:'book',bookId:book.id,branchId:book.originalBranchId,revisionId:book.originalRevisionId}" purpose="cover" :alt="book.title+'封面'"><template #fallback><img :src="book.coverUrl" class="book-cover" :alt="book.title+'封面'" width="96" height="136"></template></StoryImages></RouterLink><div class="book-info"><div class="row between"><h2><RouterLink :to="storyPath('book',book.id)" class="book-title-entry">{{book.title}}</RouterLink></h2><button class="icon-button favorite-button" :class="{saved:book.favorite}" :aria-label="(book.favorite?'取消收藏':'收藏')+book.title" :aria-pressed="!!book.favorite" :disabled="busy||!store.online" @click="favorite(book)"><Star :size="19" :fill="book.favorite?'currentColor':'none'"/></button></div><small>{{book.source}}</small><div v-if="book.tags?.length" class="book-tags"><span v-for="tag in book.tags" :key="tag" class="badge">{{tag}}</span></div><small>{{book.lastProgress?'有已保存的阅读位置':'还未开始阅读'}}</small><div class="actions"><button class="primary" @click="read(book)">进入作品</button><button class="icon-button" :aria-label="'管理'+book.title" @click="manageBook(book)"><MoreHorizontal :size="20"/></button></div></div></article></div><Sheet v-if="originalImages" title="原创图文故事" @close="originalImages=false"><OriginalImageStories @installed="originalImages=false;load()"/></Sheet><Sheet v-if="manage" title="管理书籍" @close="manage=null"><label>书名<input v-model="rename" maxlength="200"></label><label>书籍标签<input v-model="tagInput" maxlength="168" placeholder="例如：悬疑，想重读"><small>用逗号分隔，最多8个标签，每个不超过20字。</small></label><button :disabled="busy" @click="edit('rename')">保存书名</button><div class="divider"><p class="muted">归档后仍能阅读，恢复后可以继续互动。</p><button :disabled="busy" @click="edit(manage.status==='active'?'archived':'active')"><Archive v-if="manage.status==='active'" :size="18"/><RotateCcw v-else :size="18"/>{{manage.status==='active'?'归档':'恢复到书架'}}</button></div></Sheet><Sheet v-if="importing" title="导入小说" @close="importing=false"><p v-if="error" class="error" role="alert">{{error}}</p><template v-if="preview"><label>书名<input v-model="title" maxlength="200"></label><p>{{preview.chapters.length}} 章 · {{preview.encoding}}</p><p v-for="warning in preview.warnings" :key="warning" class="hint">{{warning}}</p><label v-if="preview.sourceFormat==='txt'">编码<select v-model="encoding" :disabled="busy" @change="previewEncoding"><option v-for="code in ['utf-8-sig','utf-16','utf-16-le','utf-16-be','gb18030','gbk','big5']" :key="code">{{code}}</option></select></label><div class="file-preview">{{preview.preview}}</div><details class="divider"><summary>确认阅读顺序</summary><div v-for="(c,i) in preview.chapters" :key="c.id" class="row list-item"><span>{{i+1}}. {{c.title}}</span><button v-if="i>0" class="icon-button" aria-label="向前移动章节" @click="[preview.chapters[i-1],preview.chapters[i]]=[preview.chapters[i],preview.chapters[i-1]]">↑</button></div></details><p v-if="preview.duplicateBookId" class="hint">书架中已有相同材料，可以直接复用。</p><div class="actions"><button v-if="preview.duplicateBookId" :disabled="busy" @click="confirm(true)">打开已有书籍</button><button class="primary" :disabled="busy||!title.trim()" @click="confirm()">{{busy?'正在保存…':'确认并开始阅读'}}</button></div></template><p v-else-if="!error" class="loading">正在检查文件并解析目录…<br>导入后会在后台整理角色卡，你可以先开始阅读。</p></Sheet></template>
