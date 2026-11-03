<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import PageHeader from '../workspace/PageHeader.vue'
import TaskProgress from './TaskProgress.vue'
const capture=useRequestScope(),items=ref<any[]>([]),offset=ref(0),error=ref('');let timer:ReturnType<typeof setTimeout>|undefined,disposed=false
async function load(){const scope=capture();try{const data=await api<any>('/experience/tasks?offset='+offset.value);if(scope.valid()&&!disposed){items.value=data.items;error.value=''}}catch(e){if(scope.valid()&&!disposed)error.value=(e as Error).message}finally{if(!disposed)timer=setTimeout(load,5000)}}
function paginate(n:number){clearTimeout(timer);offset.value=Math.max(0,offset.value+n);void load()}
onMounted(load);onUnmounted(()=>{disposed=true;clearTimeout(timer)})
</script>
<template><div class="workspace-page"><PageHeader title="我的任务进度" description="查看原任务和已接收的结果。查询进度不会重新生成。"><RouterLink to="/help/feedback" class="inline-link">反馈问题</RouterLink></PageHeader><p v-if="error" role="alert" class="error">{{error}}</p><p v-if="!items.length">暂时没有任务。</p><article v-for="item in items" :key="item.id" class="workspace-card"><h2>{{item.chatId?'人物独立对话':item.worldId?'世界游玩与准备':'阅读与创作'}}</h2><p v-if="item.removed">相关内容已删除，仅保留任务与用量核查记录。</p><small>{{new Date(item.createdAt).toLocaleString()}} · {{item.id}}</small><TaskProgress :progress="item.progress"/><p v-if="item.errorCode">问题编号：{{item.errorCode}}。请回到原页面查询或恢复，结果未知时不要另发相同行动。</p><RouterLink v-if="item.chatId" :to="'/conversations/world/'+item.chatId">返回这段对话</RouterLink><RouterLink v-else-if="item.worldId" :to="'/worlds/'+item.worldId+'/play'">查看原世界</RouterLink><RouterLink v-else-if="item.bookId" :to="'/stories/book-'+item.bookId">查看原故事</RouterLink><RouterLink :to="{path:'/help/feedback',query:{task:item.id,from:'/tasks/activity'}}">反馈此任务</RouterLink></article><div class="actions"><button :disabled="!offset" @click="paginate(-30)">上一页</button><button :disabled="items.length<30" @click="paginate(30)">下一页</button></div></div></template>
<style scoped>article{margin:20px 0}article a{display:inline-block;margin:8px 16px 8px 0;min-height:44px;padding-top:12px}button{min-height:44px}</style>
