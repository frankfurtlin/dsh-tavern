<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {BookOpen,Download,Trash2,RefreshCw} from 'lucide-vue-next'
import {entries,noteQueue,removePackage,type OfflineEntry,type PendingNote} from '../offline/storage'
import {syncNotes} from '../offline/notes'
import {worldEntries,removeWorldPackage,type OfflineWorldEntry} from '../offline/world-storage'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const capture=useRequestScope(),store=useApp(),items=ref<OfflineEntry[]>([]),worlds=ref<OfflineWorldEntry[]>([]),notes=ref<PendingNote[]>([]),error=ref(''),busy=ref(false)
let generation=0
async function load(){const scope=capture(),request=++generation;try{const [lines,pending,downloaded]=await Promise.all([entries(),noteQueue(),worldEntries()]);if(scope.valid()&&request===generation){items.value=lines;notes.value=pending;worlds.value=downloaded}}catch(e){if(scope.valid()&&request===generation)error.value=(e as Error).message}}
async function remove(id:string){const scope=capture();try{await removePackage(id);if(scope.valid())await load()}catch(e){if(scope.valid())error.value=(e as Error).message}}
async function removeWorld(id:string){const scope=capture();try{await removeWorldPackage(id,scope.valid);if(scope.valid())await load()}catch(e){if(scope.valid())error.value=(e as Error).message}}
async function sync(){const scope=capture();busy.value=true;error.value='';try{const result=await syncNotes();if(scope.valid()){store.message(result.remaining?'仍有笔记待确认':'笔记已同步');await load()}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
function removed(event:Event){const id=(event as CustomEvent<{bookId:string}>).detail?.bookId;generation++;items.value=items.value.filter(item=>item.bookId!==id);notes.value=notes.value.filter(note=>note.anchor.bookId!==id);void load()}
function removedWorld(event:Event){const target=(event as CustomEvent<{worldId:string;lineId?:string}>).detail;if(!target)return;generation++;worlds.value=worlds.value.filter(item=>item.worldId!==target.worldId||!!target.lineId&&item.lineId!==target.lineId);void load()}
onMounted(()=>{void load();window.addEventListener('reader-notes-changed',load);window.addEventListener('reader-book-deleted',removed);window.addEventListener('reader-world-deleted',removedWorld)});onUnmounted(()=>{generation++;window.removeEventListener('reader-notes-changed',load);window.removeEventListener('reader-book-deleted',removed);window.removeEventListener('reader-world-deleted',removedWorld)})
</script>
<template>
  <div class="page-heading"><h1>已下载</h1></div>
  <p v-if="error" class="error" role="alert">{{error}}</p>
  <section v-if="notes.length" class="setting-row"><div class="row between"><span>{{notes.length}} 条笔记待同步</span><button :disabled="busy||!store.online" @click="sync"><RefreshCw :size="18"/>同步笔记</button></div><p v-for="note in notes.filter(item=>item.blocked)" :key="note.id" class="error">{{note.note}} · {{note.blocked}}</p></section>
  <div v-if="!items.length&&!worlds.length" class="empty"><Download :size="32"/><h2>还没有下载的故事线</h2><RouterLink to="/library" class="inline-link">回到书架</RouterLink></div>
  <section v-if="items.length" aria-label="小说阅读下载"><h2>小说阅读</h2><div class="notes-grid"><article v-for="item in items" :key="item.id" class="note-card" data-download-kind="book"><h2>{{item.title}}</h2><p>{{item.lineName}} · {{item.chapterCount}} 章</p><small class="muted">{{item.bytes<1048576?Math.ceil(item.bytes/1024)+' KiB':(item.bytes/1048576).toFixed(1)+' MiB'}} · {{new Date(item.downloadedAt).toLocaleDateString('zh-CN')}}</small><div class="row between divider"><RouterLink class="inline-link" :to="{path:'/books/'+item.bookId+'/read',query:{branch:item.branchId,revision:item.revisionId}}"><BookOpen :size="18"/>阅读</RouterLink><button class="icon-button" aria-label="移除本机下载" @click="remove(item.id)"><Trash2 :size="18"/></button></div></article></div></section>
  <section v-if="worlds.length" class="divider" aria-label="世界故事下载"><h2>世界故事</h2><div class="notes-grid"><article v-for="item in worlds" :key="item.id" class="note-card" data-download-kind="world"><h2>{{item.title}}</h2><p>{{item.lineName}} · {{item.chapterCount}} 章</p><small class="muted">{{item.bytes<1048576?Math.ceil(item.bytes/1024)+' KiB':(item.bytes/1048576).toFixed(1)+' MiB'}} · {{new Date(item.downloadedAt).toLocaleDateString('zh-CN')}}</small><div class="row between divider"><RouterLink class="inline-link" :to="{path:'/worlds/'+item.worldId+'/read',query:{line:item.lineId,checkpoint:item.headCheckpointId,download:item.id}}"><BookOpen :size="18"/>阅读</RouterLink><button class="icon-button" aria-label="移除本机世界下载" @click="removeWorld(item.id)"><Trash2 :size="18"/></button></div></article></div></section>
</template>
