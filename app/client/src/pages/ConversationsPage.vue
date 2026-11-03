<script setup lang="ts">
import {computed,ref,onMounted,onUnmounted,nextTick,watch} from 'vue'
import {useRoute} from 'vue-router'
import {rememberContext,sessionContext} from '../api/stories'
import {MessagesSquare} from 'lucide-vue-next'
import {allPages,api} from '../api'
import type {WorldChat} from '../play-controls/types'
import type {RoleSession} from '../api/types'
import ChatPanel from '../components/ChatPanel.vue'
import Sheet from '../components/Sheet.vue'
import {queryField} from '../workspace/query-state'
import {useRequestScope} from '../platform/request-scope'
import '../styles/conversations.css'
const worldSessions=ref<WorldChat[]>([]),sessions=ref<RoleSession[]>([]),selected=ref<RoleSession|null>(null),error=ref(''),listError=ref('')
const search=queryField('q'),archiveQuery=queryField('archived'),worldCursor=ref<string|null>(null),moreBusy=ref(false)
const shownBooks=computed(()=>archiveQuery.value==='1'?[]:sessions.value.filter(s=>(s.characterName+s.bookTitle+s.branchName).toLowerCase().includes(search.value.toLowerCase())))
const mobile=ref(innerWidth<768)
const pageRoot=ref<HTMLElement>(),pageHeight=ref<number>()
const capture=useRequestScope(),route=useRoute();let listEpoch=0,selectionEpoch=0
function closeSelection(){selectionEpoch++;selected.value=null}
function removed(event:Event){const id=(event as CustomEvent<{bookId:string}>).detail?.bookId;listEpoch++;sessions.value=sessions.value.filter(session=>session.bookId!==id);if(selected.value?.bookId===id)closeSelection();void load(true)}
async function choose(session:RoleSession){const selection=++selectionEpoch;selected.value=session;error.value='';const scope=capture(),origin=String(route.query.story||'');const current=()=>scope.valid()&&selection===selectionEpoch&&selected.value?.id===session.id;try{await rememberContext(sessionContext(session,/^(book|world)-[a-f0-9]{32}$/.test(origin)?origin:undefined),current)}catch(e){if(current())error.value=(e as Error).message}}
async function loadWorldChats(epoch:number){const scope=capture();const result=await api<{items:WorldChat[];nextCursor:string|null}>('/play-controls/world-chats?status='+(archiveQuery.value==='1'?'archived':'active')+'&q='+encodeURIComponent(search.value));if(scope.valid()&&epoch===listEpoch)worldCursor.value=result.nextCursor;return result.items}
async function moreWorldChats(){if(!worldCursor.value||moreBusy.value)return;const scope=capture();moreBusy.value=true;try{const result=await api<{items:WorldChat[];nextCursor:string|null}>('/play-controls/world-chats?status='+(archiveQuery.value==='1'?'archived':'active')+'&q='+encodeURIComponent(search.value)+'&cursor='+worldCursor.value);if(scope.valid()){worldSessions.value=[...worldSessions.value,...result.items.filter(s=>!worldSessions.value.some(x=>x.id===s.id))];worldCursor.value=result.nextCursor}}catch(e){if(scope.valid())listError.value=(e as Error).message}finally{if(scope.valid())moreBusy.value=false}}
async function load(preserve=false){const scope=capture(),epoch=++listEpoch,previous=preserve?selected.value:null;error.value='';listError.value='';if(!preserve)closeSelection();const selection=selectionEpoch;try{const [books,worlds]=await Promise.allSettled([allPages<RoleSession>('/role-sessions'),loadWorldChats(epoch)]);if(!scope.valid()||epoch!==listEpoch)return;const items=books.status==='fulfilled'?books.value:sessions.value;sessions.value=items;if(worlds.status==='fulfilled')worldSessions.value=worlds.value;const failed=[books,worlds].find(result=>result.status==='rejected');if(failed?.status==='rejected')listError.value=failed.reason instanceof Error?failed.reason.message:'部分对话暂时无法读取，请稍后重试。';if(selection===selectionEpoch){if(previous){const fresh=items.find(session=>session.id===previous.id);if(fresh)selected.value=fresh;else closeSelection()}else if(route.query.session){const target=items.find(session=>session.id===route.query.session);if(target)await choose(target);else error.value='这段对话暂时无法打开，请选择其他已保存的对话。'}}}catch(e){if(scope.valid()&&epoch===listEpoch&&selection===selectionEpoch)error.value=(e as Error).message}}
let observer:ResizeObserver|undefined,frame=0,disposed=false
function fit(){
  const root=pageRoot.value;if(!root)return
  if(mobile.value){pageHeight.value=undefined;return}
  const top=root.getBoundingClientRect().top+window.scrollY
  const viewport=window.visualViewport?.height||innerHeight
  const safeBottom=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--reader-safe-bottom'))||0
  const container=root.closest?.('main')
  const bottom=container?parseFloat(getComputedStyle(container).paddingBottom)||24:24
  pageHeight.value=Math.max(0,Math.floor(viewport-top-safeBottom-bottom))
}
function scheduleFit(){if(disposed)return;cancelAnimationFrame(frame);frame=requestAnimationFrame(fit)}
const resize=()=>{mobile.value=innerWidth<768;void nextTick().then(scheduleFit)}
onMounted(()=>{window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',scheduleFit);observer=new ResizeObserver(scheduleFit);const app=document.getElementById('app');if(app)observer.observe(app);if(pageRoot.value)observer.observe(pageRoot.value);fit()})
onUnmounted(()=>{disposed=true;observer?.disconnect();cancelAnimationFrame(frame);window.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('resize',scheduleFit)})
onMounted(()=>{void load();window.addEventListener('reader-book-deleted',removed)});onUnmounted(()=>window.removeEventListener('reader-book-deleted',removed));watch(()=>route.fullPath,()=>load())
</script>
<template><div ref="pageRoot" class="conversations-page" :style="{height:pageHeight===undefined?undefined:pageHeight+'px'}"><div class="page-heading"><h1>与故事中的人说说话</h1><p>每次相遇，都留在自己的故事线与时间点。</p></div><div class="workspace-toolbar"><label class="workspace-search">搜索对话<input v-model="search" type="search" placeholder="名称、人物或作品"></label><button :aria-pressed="archiveQuery==='1'" @click="archiveQuery=archiveQuery==='1'?'':'1'">{{archiveQuery==='1'?'返回最近对话':'世界对话归档'}}</button></div><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="listError" class="error" role="alert">{{listError}} <button @click="load(true)">重新载入对话列表</button></p><div v-if="!shownBooks.length&&!worldSessions.length" class="empty"><MessagesSquare :size="40"/><h2>还没有对话</h2><p>在互动世界的人物卡片选择“单独聊聊”，或在小说阅读工具里选择已登场的角色。</p><RouterLink to="/works" class="inline-link">选择一部作品</RouterLink></div><div v-else class="chat-layout"><aside class="chat-sidebar"><RouterLink v-for="s in worldSessions" :key="s.id" :to="'/conversations/world/'+s.id" class="world-chat-list-item"><strong>{{s.name||s.characterName}}</strong><small>{{s.characterName}} · {{s.worldTitle}} · {{s.snapshot.lineName}}<br>{{s.snapshot.timeLabel}} · {{s.pendingTaskId?'回复处理中':'独立对话'}}<br>{{s.lastMessage}}<br>{{s.updatedAt?new Date(s.updatedAt).toLocaleString():''}}</small></RouterLink><button v-for="s in shownBooks" :key="s.id" :class="{active:selected?.id===s.id}" @click="choose(s)"><strong>{{s.characterName}}</strong><small>{{s.bookTitle}} · {{s.branchName}}<br>{{s.pinnedLocation}} · {{s.pendingTaskId?'有正在处理的回复':'已保存'}}</small></button><button v-if="worldCursor" :disabled="moreBusy" @click="moreWorldChats">载入更多世界对话</button></aside><div class="desktop-chat"><ChatPanel v-if="selected&&!mobile" :key="selected.id" :session="selected"/><div v-else class="empty">选择一段已保存的对话。</div></div></div><Sheet v-if="selected&&mobile" :fill="true" class="mobile-chat" :title="selected.characterName+' · '+selected.branchName" @close="closeSelection"><ChatPanel :key="selected.id" :session="selected"/></Sheet></div></template>
<style scoped>.world-chat-list-item{display:flex;flex-direction:column;gap:8px;padding:16px;min-height:72px;border-bottom:1px solid #e8e1d8;text-decoration:none;color:inherit}.world-chat-list-item:hover,.world-chat-list-item:focus-visible{background:#b562290d}.world-chat-list-item small{color:var(--muted,#82776d);line-height:1.7}.mobile-chat{display:none}@media(max-width:767px){.desktop-chat{display:none}.mobile-chat{display:flex}}</style>
