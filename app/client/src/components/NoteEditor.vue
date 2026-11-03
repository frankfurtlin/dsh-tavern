<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {api,newKey,ApiError} from '../api'
import type {Anchor,Bookmark} from '../api/types'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import {queueNote} from '../offline/notes'
import {isBookmarkDeleted} from '../platform/bookmark-cache'
const props=defineProps<{anchor:Anchor;note?:Bookmark}>(),emit=defineEmits<{saved:[note:Bookmark]}>()
const store=useApp(),text=ref(props.note?.note||''),busy=ref(false),error=ref(''),ready=ref(false),capture=useRequestScope(()=>[props.anchor,props.note?.id])
type PendingSave={key:string;text:string;attempted?:boolean}
const pending=ref<PendingSave|null>(null)
const confirmed=ref<Bookmark|null>(null),remote=ref<Bookmark|undefined>(props.note),baseVersion=ref(props.note?.version||1)
const deleted=ref(props.note?.status==='deleted_original')
const draftConflict=computed(()=>!!remote.value&&baseVersion.value!==(remote.value.version||1))
const draftKey='note-draft:'+props.anchor.bookId+':'+props.anchor.branchId+':'+props.anchor.revisionId+':'+props.anchor.blockId+':'+(props.note?.id||'new')+(props.anchor.start<props.anchor.end?':'+props.anchor.start+':'+props.anchor.end+':'+props.anchor.quoteHash:'')
const persistDraft=()=>cacheSet(draftKey,{text:text.value,pending:pending.value,baseVersion:baseVersion.value,confirmed:confirmed.value,deleted:deleted.value})
onMounted(async()=>{const scope=capture();try{const saved=await cacheGet<string|{text:string;pending:PendingSave|null;baseVersion?:number;confirmed?:Bookmark|null;deleted?:boolean}>(draftKey);if(!scope.valid())return;if(typeof saved==='string'){text.value=saved;if(props.note)baseVersion.value=0}else if(saved){text.value=saved.text;pending.value=saved.pending;baseVersion.value=saved.baseVersion??(props.note?0:1);confirmed.value=saved.confirmed||null;deleted.value=deleted.value||!!saved.deleted}ready.value=true;if(props.note&&isBookmarkDeleted(props.anchor.bookId,props.note.id))removed(new CustomEvent('reader-bookmark-deleted',{detail:{bookId:props.anchor.bookId,bookmarkId:props.note.id}}));else if(deleted.value){pending.value=null;confirmed.value=null;error.value='这条笔记已删除。本机文字仍保留，可以复制后另存。'}}catch(e){if(scope.valid())error.value=(e as Error).message}})
watch(text,()=>{if(ready.value){const scope=capture();void persistDraft().catch(()=>{if(scope.valid())error.value='本机草稿暂时无法保存，请保留文字后重试'})}},{flush:'sync'})
async function resolveDraft(keepLocal:boolean){if(!remote.value)return;const scope=capture();baseVersion.value=remote.value.version||1;if(!keepLocal)text.value=remote.value.note;pending.value=null;error.value='';try{await persistDraft()}catch(e){if(scope.valid())error.value=(e as Error).message}}
async function save(){
  const scope=capture()
  if(busy.value||deleted.value||!text.value.trim()||draftConflict.value&&!confirmed.value)return
  busy.value=true;error.value=''
  async function finish(saved:Bookmark){
    await cacheSet(draftKey,null);if(!scope.valid())return
    pending.value=null
    store.message(saved.status==='pending_sync'?'笔记已保存在本机，联网后同步':'阅读笔记已保存')
    emit('saved',saved)
  }
  try{
    // A confirmed save only needs local cleanup, even after going offline.
    if(confirmed.value){await finish(confirmed.value);return}
    const submitted=pending.value||{key:newKey(),text:text.value.trim()}
    if(!store.online||props.note?.status==='pending_sync'){
      if(!props.note&&!pending.value){pending.value={...submitted,attempted:false};await persistDraft();if(!scope.valid())return}
      const saved=await queueNote(props.anchor,submitted.text,props.note,pending.value?.key,!!pending.value&&pending.value.attempted!==false)
      if(!scope.valid())return
      confirmed.value=saved
      await persistDraft();if(!scope.valid())return
      await finish(saved);return
    }
    if(!props.note){pending.value={...submitted,attempted:true};await persistDraft();if(!scope.valid())return}
    const result=props.note?await api<Bookmark>(`/books/${props.anchor.bookId}/bookmarks/${props.note.id}`,{method:'PATCH',body:{expectedVersion:baseVersion.value,note:submitted.text}}):await api<Bookmark>(`/books/${props.anchor.bookId}/bookmarks`,{method:'POST',key:submitted.key,body:{anchor:props.anchor,note:submitted.text}})
    if(!scope.valid())return
    confirmed.value=result
    await finish(result)
  }catch(e){
    if(!scope.valid())return
    if(e instanceof ApiError&&e.code==='BOOKMARK_DELETED'){deleted.value=true;pending.value=null;confirmed.value=null;error.value='这条笔记已删除。本机文字仍保留，可以复制后另存。';try{await persistDraft()}catch{if(scope.valid())error.value+=' 本机保存失败，请立即复制文字。'}return}
    if(e instanceof ApiError&&e.code==='NOTE_CONFLICT'&&props.note){try{const latest=await api<Bookmark>(`/books/${props.anchor.bookId}/bookmarks/${props.note.id}`);if(scope.valid())remote.value=latest}catch{}if(!scope.valid())return}
    error.value=confirmed.value?(confirmed.value.status==='pending_sync'?'笔记已保存在本机，草稿清理失败；重试只完成清理，不会创建新笔记。':'笔记已保存到账号，本机草稿清理失败；重试只完成清理，不会创建新笔记。'):(e as Error).message
    if(!confirmed.value&&e instanceof ApiError&&e.status>0&&e.status<500){pending.value=null;try{await persistDraft()}catch{}}
  }finally{if(scope.valid())busy.value=false}
}
async function copy(){const scope=capture();try{await navigator.clipboard.writeText(text.value);if(scope.valid())store.message('本机文字已复制')}catch{if(scope.valid())error.value='复制未完成，请选中文字后复制。'}}
function removed(event:Event){const detail=(event as CustomEvent<{bookId:string;bookmarkId:string}>).detail;if(detail?.bookId!==props.anchor.bookId||detail?.bookmarkId!==props.note?.id)return;deleted.value=true;pending.value=null;confirmed.value=null;error.value='这条笔记已删除。本机文字仍保留，可以复制后另存。';if(ready.value){const scope=capture();void persistDraft().catch(()=>{if(scope.valid())error.value+=' 本机保存失败，请立即复制文字。'})}}
onMounted(()=>window.addEventListener('reader-bookmark-deleted',removed))
onUnmounted(()=>window.removeEventListener('reader-bookmark-deleted',removed))
</script>
<template><p class="muted">记下这一刻的想法，随时回到原来的段落。</p><div v-if="draftConflict&&!confirmed&&!deleted" class="hint"><strong>这份草稿基于旧版本，账号里的笔记已有更新。</strong><p class="note-copy">{{remote?.note}}</p><div class="actions"><button @click="resolveDraft(false)">使用账号里的笔记</button><button @click="resolveDraft(true)">用这份草稿替换当前笔记</button></div></div><form @submit.prevent="save"><label>我的阅读笔记<textarea v-model="text" :readonly="deleted" :disabled="!ready||busy||!!pending||!!confirmed" maxlength="2000" placeholder="这段故事让你想到了什么？" autofocus/></label><div class="row between"><small class="muted">{{text.length}} / 2000 · 草稿自动保存在本机</small><button v-if="deleted" type="button" @click="copy">复制本机文字</button><button v-else class="primary" :disabled="busy||!ready||!text.trim()||draftConflict&&!confirmed">{{busy?'正在保存…':confirmed?'完成草稿清理':pending?'重试保存同一条笔记':'保存笔记'}}</button></div><p v-if="error" class="error" role="alert">{{error}}</p></form></template>
