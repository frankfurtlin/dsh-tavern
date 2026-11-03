<script setup lang="ts">
import {ref,watch,computed,onUnmounted} from 'vue'
import {queryField} from '../workspace/query-state'
import {Search,BookOpen} from 'lucide-vue-next'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import PublicCover from './PublicCover.vue'
import type {PublicList} from './types'
import './authoring.css'
import {useCuratedArtwork,type CuratedArtworkItem} from '../catalog/curated-artwork'
const props=defineProps<{authorId?:string;favorites?:boolean;sharedSearch?:string;curatedItems?:CuratedArtworkItem[]}>(),capture=useRequestScope(),result=ref<PublicList>(),search=queryField('catalogQ'),genre=queryField('catalogGenre'),page=queryField('catalogOffset','0'),busy=ref(false),error=ref('');let version=0
const {publications}=useCuratedArtwork(()=>props.curatedItems)
const offset=computed({get:()=>Math.max(0,Math.min(100000,Number(page.value)||0)),set:(v:number)=>page.value=String(v)}),effectiveSearch=computed(()=>props.sharedSearch??search.value)
async function load(){const epoch=++version,scope=capture();busy.value=true;error.value='';try{const params=new URLSearchParams({q:effectiveSearch.value,genre:genre.value,offset:String(offset.value),limit:'12',...(props.authorId?{authorId:props.authorId}:{}),...(props.favorites?{favorites:'true'}:{})});const data=await api<PublicList>('/authoring/catalog?'+params);if(scope.valid()&&epoch===version)result.value=data}catch(e){if(scope.valid()&&epoch===version)error.value=(e as Error).message}finally{if(scope.valid()&&epoch===version)busy.value=false}}
function submit(){offset.value=0;void load()}
let timer:ReturnType<typeof setTimeout>|undefined
watch(()=>[props.authorId,props.favorites,effectiveSearch.value,genre.value,offset.value],(_current,previous)=>{clearTimeout(timer);if(!previous)void load();else timer=setTimeout(()=>void load(),200)},{immediate:true});onUnmounted(()=>clearTimeout(timer))
</script>
<template>
 <section aria-label="公开作品目录">
  <form class="workspace-toolbar public-catalog-toolbar" @submit.prevent="submit">
   <label v-if="sharedSearch===undefined" class="workspace-search"><Search :size="18"/><input v-model="search" type="search" aria-label="搜索公开作品" placeholder="搜索公开作品、故事设定"></label>
   <label><span>作品分类</span><select aria-label="公开作品分类" v-model="genre" @change="submit"><option value="">全部分类</option><option value="mystery">悬疑调查</option><option value="fantasy">奇幻冒险</option><option value="romance">人物情感</option><option value="general">综合故事</option><option value="survival">生存探索</option><option value="political">势力权谋</option></select></label><button type="submit" :disabled="busy">搜索</button>
  </form>
  <p v-if="busy" role="status">正在读取公开作品…</p><p v-if="error" role="alert" class="error">{{error}} <button @click="load">重试</button></p>
  <div v-if="result?.items.length" class="public-story-grid">
   <article v-for="item in result.items" :key="item.id" class="public-story-card">
    <RouterLink :to="'/published/'+item.id" class="public-card-link"><PublicCover :id="item.id" :title="item.title" :theme="item.coverTheme" :has-cover="item.hasCover" :cover-key="publications.get(item.id)"/><div class="public-story-copy"><h3>{{item.title}}</h3><p>{{item.summary}}</p></div></RouterLink>
    <footer><RouterLink :to="'/authors/'+item.authorId" class="public-author">{{item.authorName}}</RouterLink><small>{{item.favorites}} 人收藏 · 版本 {{item.version}}</small></footer>
   </article>
  </div>
  <section v-else-if="!busy&&!error" class="workspace-empty"><BookOpen :size="30"/><h3>{{effectiveSearch||genre?'没有找到符合条件的公开作品':favorites?'还没有收藏公开作品':'暂时没有已发布作品'}}</h3><p>{{favorites?'在公开作品页收藏后，会显示在这里。':'作者提交的作品通过审核后，会出现在目录中。'}}</p></section>
  <nav v-if="result&&result.total>12" class="public-pagination" aria-label="公开作品分页"><button :disabled="busy||offset===0" @click="offset=Math.max(0,offset-12)">上一页</button><span>{{offset+1}}—{{Math.min(offset+12,result.total)}} / {{result.total}}</span><button :disabled="busy||offset+12>=result.total" @click="offset+=12">下一页</button></nav>
 </section>
</template>
