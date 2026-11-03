<script setup lang="ts">
import {trackProductEvent} from "../analytics/product-events"
import {ref,computed,onMounted} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {Search,BookOpen,Feather,ArrowUpRight,ArrowRight} from 'lucide-vue-next'
import {api,newKey} from '../api'
import {useApp} from '../stores/app'
import {useRequestScope} from '../platform/request-scope'
import PageHeader from './PageHeader.vue'
import PublicCatalog from '../authoring/PublicCatalog.vue'
import {queryField} from './query-state'
import CuratedCover from '../catalog/CuratedCover.vue'
type Curated={key:string;title:string;genre:string;description:string;available:boolean;worldId:string|null;lineId:string|null;publicationId:string|null}
const route=useRoute(),router=useRouter(),store=useApp(),capture=useRequestScope(),search=queryField('q'),items=ref<Curated[]>([]),error=ref(''),loading=ref(true),busy=ref('')
const visible=computed(()=>items.value.filter(s=>(s.title+s.genre+s.description).toLowerCase().includes(search.value.trim().toLowerCase())))
const openKeys=new Map<string,string>()
async function open(item:Curated){if(busy.value)return;const scope=capture();busy.value=item.key;error.value='';try{if(!openKeys.has(item.key))openKeys.set(item.key,newKey());const result=await api<{worldId:string;lineId?:string}>('/experience/curated/'+item.key+'/open',{method:'POST',key:openKeys.get(item.key),body:{}});if(scope.valid()){trackProductEvent('work.open',{worldId:result.worldId,source:'curated'});await router.push(result.lineId?'/worlds/'+result.worldId+'/play?line='+result.lineId:'/worlds/new?world='+result.worldId)}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=''}}
async function load(){const scope=capture();loading.value=true;error.value='';try{const result=await api<{items:Curated[]}>('/experience/curated');if(!scope.valid())return;items.value=result.items;const requested=items.value.find(i=>i.key===route.query.start);if(requested)await open(requested)}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())loading.value=false}}
onMounted(load)
</script>
<template>
 <div class="workspace-page discovery-page">
  <PageHeader :title="store.user?'欢迎回来，'+store.user.username:'欢迎来到酒馆'" description="从一个故事开始，让每次选择都有回响。">
   <RouterLink to="/worlds/scripts" class="discovery-create"><Feather :size="17"/>创作作品</RouterLink>
  </PageHeader>
  <label class="workspace-search discovery-search"><Search :size="19"/><input v-model="search" type="search" aria-label="搜索作品" placeholder="搜索故事、题材或关键词"></label>
  <p v-if="error" class="error" role="alert">{{error}} <button @click="load">重新加载</button> <RouterLink to="/help/feedback">反馈问题</RouterLink></p>
  <aside v-if="!search" class="discovery-intro" aria-label="开始故事的提示"><BookOpen :size="21"/><p><strong>选一个故事，直接开始。</strong>选好身份就能进入，游玩进度会自动保存。</p><RouterLink to="/help" class="inline-link">游玩指南<ArrowUpRight :size="15"/></RouterLink></aside>
  <div class="workspace-section-title"><div><h2>精选互动故事</h2><p class="discovery-section-note">不同的旅程，等你走出自己的故事。</p></div><RouterLink to="/recent" class="inline-link">最近游玩<ArrowRight :size="16"/></RouterLink></div>
  <p v-if="loading" role="status" class="workspace-loading">正在准备故事入口…</p>
  <div class="curated-story-grid">
   <article v-for="item in visible" :key="item.key" class="curated-story-card">
    <RouterLink v-if="item.publicationId" :to="'/published/'+item.publicationId" class="curated-story-art" :aria-label="'查看'+item.title+'作品详情'"><CuratedCover :cover-key="item.key" :title="item.title" priority/></RouterLink>
    <div v-else class="curated-story-art"><CuratedCover :cover-key="item.key" :title="item.title" priority/></div>
    <div class="curated-story-copy"><span class="story-genre">{{item.genre}}</span><h3>{{item.title}}</h3><p>{{item.description}}</p><div class="curated-story-actions"><button class="primary" :disabled="!!busy||!item.available" @click="open(item)">{{busy===item.key?'正在打开…':item.lineId?'继续游玩':item.available?'开始游玩':'正在准备'}}<ArrowRight :size="16"/></button><RouterLink v-if="item.publicationId" :to="'/published/'+item.publicationId" class="story-detail-link">作品详情<ArrowUpRight :size="15"/></RouterLink></div></div>
   </article>
  </div>
  <p v-if="!loading&&!visible.length" class="workspace-loading">没有找到精选故事；你也可以查看下方的公开作品。<button @click="search=''">清除搜索</button></p>
  <div class="workspace-section-title"><h2>公开互动作品</h2></div>
  <PublicCatalog :shared-search="search" :curated-items="items"/>
  <section class="discovery-library"><BookOpen :size="24"/><div><h2>带上你自己的小说</h2><p>导入到账号书架，继续阅读、做笔记或探索故事。</p></div><RouterLink to="/library?examples=1" class="inline-link">前往小说书架<ArrowRight :size="16"/></RouterLink></section>
  <p class="workspace-footnote"><RouterLink to="/data-use">数据与使用说明</RouterLink> · <RouterLink to="/help/feedback">反馈与服务通知</RouterLink></p>
 </div>
</template>
<style scoped>
.discovery-page :deep(.workspace-page-header){border:0;background:transparent;padding:0;margin-bottom:24px}.discovery-page :deep(.workspace-heading-copy h1){font-size:28px!important;letter-spacing:-.02em}.discovery-create{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:10px 16px;border:1px solid var(--edge);border-radius:10px;background:var(--canvas);color:var(--ink);font-size:14px;text-decoration:none;white-space:nowrap}.discovery-create:hover{border-color:var(--accent);color:var(--accent)}.discovery-search{max-width:none;margin-bottom:20px}.discovery-intro{display:flex;align-items:center;gap:14px;padding:16px 20px;border:1px solid var(--edge);border-radius:12px;background:var(--canvas)}.discovery-intro>svg{color:var(--accent);flex:none}.discovery-intro p{flex:1;margin:0;color:var(--quiet);font-size:13px;line-height:1.8}.discovery-intro strong{color:var(--ink);font-weight:600}.discovery-intro a{font-size:13px;white-space:nowrap}.discovery-section-note{margin:6px 0 0;color:var(--quiet);font-size:13px}.curated-story-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}.curated-story-card{display:flex;flex-direction:column;overflow:hidden;min-width:0;background:var(--canvas);border:1px solid var(--edge);border-radius:14px;transition:border-color .18s,box-shadow .18s}.curated-story-card:hover{border-color:color-mix(in srgb,var(--accent) 30%,var(--edge));box-shadow:0 8px 24px #18202c08}.curated-story-art{display:block;aspect-ratio:4/5;max-height:350px;overflow:hidden;background:var(--paper);flex:none}.curated-story-copy{display:flex;flex:1;flex-direction:column;align-items:start;padding:20px;min-width:0}.story-genre{font-size:11px;line-height:1.6;color:var(--quiet);letter-spacing:.03em}.curated-story-copy h3{font-size:21px;font-weight:650;line-height:1.45;margin:7px 0 10px}.curated-story-copy>p{color:var(--quiet);font-size:13px;line-height:1.8;margin:0 0 22px;overflow-wrap:anywhere}.curated-story-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;align-self:stretch;margin-top:auto}.curated-story-actions button{min-height:44px;gap:10px;padding:11px 16px;font-size:14px;white-space:nowrap}.story-detail-link{display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:44px;font-size:13px;text-decoration:none;color:var(--quiet);white-space:nowrap}.story-detail-link:hover{color:var(--accent)}.discovery-library{display:flex;align-items:center;gap:18px;padding:24px 0;border-block:1px solid var(--edge);margin-top:36px}.discovery-library>svg{color:var(--quiet);flex:none}.discovery-library>div{flex:1}.discovery-library h2{margin:0;font-family:var(--font-ui);font-size:16px}.discovery-library p{margin:6px 0 0;font-size:13px;color:var(--quiet);line-height:1.7}.discovery-library a{font-size:13px;white-space:nowrap}
@media(max-width:1100px){.curated-story-grid{gap:16px}.curated-story-copy{padding:16px}.curated-story-actions{gap:8px}.curated-story-actions button{padding:10px 12px;font-size:13px}.story-detail-link{font-size:12px}}
@media(max-width:900px) and (min-width:768px){.curated-story-actions{flex-wrap:wrap}.curated-story-actions button{width:100%}.story-detail-link{width:100%}}
@media(max-width:600px){.curated-story-grid{grid-template-columns:1fr;gap:20px}.curated-story-art{aspect-ratio:4/3;max-height:300px}.curated-story-actions button{flex:1}.curated-story-copy{padding:20px}.discovery-intro{align-items:start;gap:10px;flex-wrap:wrap;padding:16px}.discovery-intro p{min-width:180px}.discovery-intro a{margin-left:31px}.discovery-library{flex-wrap:wrap;gap:12px}.discovery-library a{margin-left:36px}.discovery-page :deep(.workspace-heading-actions){width:auto}}
@media(prefers-reduced-motion:reduce){.curated-story-card{transition:none}}
</style>
