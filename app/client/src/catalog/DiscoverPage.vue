<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {Check,Plus,Search} from 'lucide-vue-next'
import {useLocal} from '../stores/local'
import {importLocalText} from '../repositories/local-import'
import type {LocalStory} from '../domain/local-contract'
import {originalStories} from './original-seeds'
import OriginalCover from './OriginalCover.vue'
import {sha256} from '../domain/local-contract'
const store=useLocal(),busy=ref(''),installed=ref(new Set<string>()),error=ref(''),loading=ref(true),search=ref(''),genre=ref('')
let disposed=false
const lifecycle=new AbortController(),current=(vault:typeof store.vault)=>!disposed&&store.vault===vault&&!!vault?.unlocked
const category=(value:string)=>value.split('·').at(-1)!.trim()
const genres=[...new Set(originalStories.map(story=>category(story.genre)))]
const visible=computed(()=>originalStories.filter(story=>(!genre.value||category(story.genre)===genre.value)&&(story.title+' '+story.genre).includes(search.value.trim())))
const featured=computed(()=>!search.value.trim()&&!genre.value?originalStories[0]:null)
const shelf=computed(()=>visible.value.filter(story=>story.id!==featured.value?.id))
const excerpt=computed(()=>featured.value?.text.split('\n').filter(Boolean)[1]||'')
onMounted(async()=>{const vault=store.vault;if(!vault)return;try{const stories=await vault.list<LocalStory>('story'),ids=new Set<string>();for(const seed of originalStories){const hash=await sha256(seed.text);if(stories.some(row=>row.value.sourceHash===hash&&(!seed.cover||!!row.value.coverAssetId)))ids.add(seed.id)};if(current(vault))installed.value=ids}catch(e){if(current(vault))error.value=(e as Error).message}finally{if(current(vault))loading.value=false}})
async function install(seed:typeof originalStories[number]){const vault=store.vault;if(!vault||busy.value)return;busy.value=seed.id;error.value='';try{await importLocalText(vault,new File([seed.text],seed.title+'.txt',{type:'text/plain'}),'utf-8',()=>{},lifecycle.signal,undefined,seed.cover?{coverAssetId:seed.cover}:undefined);if(current(vault))installed.value=new Set([...installed.value,seed.id])}catch(e){if(current(vault))error.value=(e as Error).message}finally{if(current(vault))busy.value=''}}
onUnmounted(()=>{disposed=true;lifecycle.abort()})
</script>
<template>
 <section class="discover-page">
  <header class="final-page-heading"><h1>发现</h1></header>
  <label class="discover-search"><Search :size="18"/><input v-model="search" type="search" aria-label="搜索原创故事" placeholder="寻找下一本故事" maxlength="200"></label>
  <nav class="discover-genres" aria-label="故事分类"><button :aria-pressed="!genre" @click="genre=''">全部</button><button v-for="item in genres" :key="item" :aria-pressed="genre===item" @click="genre=item">{{item}}</button></nav>
  <p v-if="error" class="final-error" role="alert">{{error}}</p><p v-if="loading" class="final-subtle" role="status">正在打开书单…</p>
  <article v-if="featured" class="discover-feature"><div class="discover-feature-cover"><OriginalCover :id="featured.cover" :title="featured.title"/></div><div class="discover-feature-copy"><small>本期选读 · {{featured.genre}}</small><h2>{{featured.title}}</h2><p>{{excerpt}}</p><button class="final-primary" :disabled="!!busy||installed.has(featured.id)" @click="install(featured)"><Check v-if="installed.has(featured.id)" :size="17"/><Plus v-else :size="17"/>{{installed.has(featured.id)?'已加入书架':busy===featured.id?'正在保存…':'加入书架'}}</button></div></article>
  <div class="final-section-title"><h2>{{genre||'原创书单'}}</h2><span class="final-subtle">{{visible.length}} 篇</span></div>
  <div v-if="!visible.length" class="final-empty"><Search :size="34"/><h2>没有找到故事</h2><button @click="search='';genre=''">清除搜索</button></div>
  <div v-else class="final-book-grid"><article v-for="story in shelf" :key="story.id" class="final-book"><OriginalCover :id="story.cover" :title="story.title"/><h2>{{story.title}}</h2><span class="final-book-meta">{{story.genre}}</span><button :disabled="!!busy||installed.has(story.id)" @click="install(story)"><Check v-if="installed.has(story.id)" :size="17"/><Plus v-else :size="17"/>{{installed.has(story.id)?'已加入书架':busy===story.id?'正在保存…':'加入书架'}}</button></article></div>
 </section>
</template>
<style scoped>
.discover-search{position:relative;max-width:560px;margin:0 0 18px}.discover-search svg{position:absolute;left:14px;top:16px;color:var(--quiet)}.discover-search input{padding-left:44px}.discover-genres{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:28px}.discover-genres button{border-radius:999px;background:transparent;color:var(--quiet);min-height:44px}.discover-genres button[aria-pressed=true]{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}.discover-feature{display:grid;grid-template-columns:150px minmax(0,1fr);gap:32px;padding:28px;background:var(--canvas);border:1px solid var(--edge);border-radius:16px;align-items:center}.discover-feature-cover{min-width:0}.discover-feature-copy{display:grid;gap:16px;min-width:0}.discover-feature-copy small{color:var(--quiet);font-size:13px}.discover-feature-copy h2{font-size:30px;overflow-wrap:anywhere}.discover-feature-copy p{max-width:620px;color:var(--quiet);line-height:1.85;font-family:var(--font-serif)}.discover-feature-copy button{justify-self:start}.discover-page .final-book button{width:100%;min-width:0;padding:10px 8px;font-size:14px}.discover-page .final-book h2{font-size:19px;line-height:1.6}
@media(max-width:600px){.discover-feature{grid-template-columns:90px minmax(0,1fr);padding:18px;gap:18px;align-items:start}.discover-feature-copy{gap:12px}.discover-feature-copy h2{font-size:22px}.discover-feature-copy p{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;font-size:14px}.discover-feature-copy small{font-size:12px}.discover-feature-copy button{font-size:14px;padding:10px 12px}.discover-page .final-book h2{font-size:18px}.discover-genres{gap:6px}.discover-genres button{font-size:14px;padding:10px 12px}}
</style>
