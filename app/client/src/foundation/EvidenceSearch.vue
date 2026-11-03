<script setup lang="ts">
import {ref,watch} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import type {KnowledgeHit} from './types'
const props=defineProps<{worldId:string;lineId:string;checkpointId?:string}>(),query=ref(''),items=ref<KnowledgeHit[]>([]),busy=ref(false),error=ref(''),searched=ref(false)
const capture=useRequestScope(()=>[props.worldId,props.lineId,props.checkpointId]);let revision=0
watch(()=>[props.worldId,props.lineId,props.checkpointId],()=>{revision++;items.value=[];searched.value=false;busy.value=false;error.value=''})
async function search(){const scope=capture(),attempt=++revision;busy.value=true;error.value='';try{const result=await api<{items:KnowledgeHit[]}>('/product/knowledge/search',{method:'POST',body:{query:query.value,scope:'player',worldId:props.worldId,lineId:props.lineId}});if(scope.valid()&&attempt===revision){items.value=result.items;searched.value=true}}catch(e){if(scope.valid()&&attempt===revision)error.value=(e as Error).message}finally{if(scope.valid()&&attempt===revision)busy.value=false}}
</script>
<template><section><p class="product-note">只检索当前角色已知的线索。继续调查后，这里的可见资料会更新。</p><form @submit.prevent="search"><label>线索关键词<input v-model="query" required maxlength="100" placeholder="例如：宝石、帽子、供货记录"></label><button class="primary" :disabled="busy">{{busy?'检索中…':'寻找已知线索'}}</button></form><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="searched&&!items.length" class="product-empty">当前已知资料中没有匹配的线索。</p><article v-for="hit in items" :key="hit.blockId" class="product-search-hit"><blockquote>{{hit.text}}</blockquote><small class="product-note">线索 {{hit.blockId}}</small></article></section></template>
