<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {Search,CheckCircle2,Clock3,ArrowUpRight} from 'lucide-vue-next'
import PageHeader from './PageHeader.vue'
import {capabilities,statusLabels,type CapabilityStatus} from './capabilities'
const route=useRoute(),search=ref(''),status=ref<CapabilityStatus|''>(''),group=ref(String(route.query.group||''))
const groups=[...new Set(capabilities.map(c=>c.group))]
watch(()=>route.query.group,value=>{group.value=groups.includes(String(value))?String(value):''})
const visible=computed(()=>capabilities.filter(c=>(!group.value||c.group===group.value)&&(!status.value||c.status===status.value)&&(c.name+c.detail).toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())))
</script>
<template><div class="workspace-page"><PageHeader title="功能一览" eyebrow="所有功能与开放状态" description="从发现故事到创作、游玩和账号权益，在这里查看当前可以使用的能力。"/><div class="workspace-metrics"><div v-for="s in (['ready','partial','planned'] as const)" :key="s"><span>{{statusLabels[s]}}</span><strong>{{capabilities.filter(c=>c.status===s).length}}</strong></div></div><div class="workspace-toolbar"><label class="workspace-search"><Search :size="18"/><input v-model="search" type="search" aria-label="搜索功能" placeholder="搜索功能或使用范围"></label><label>开放状态<select v-model="status"><option value="">全部状态</option><option v-for="s in (['ready','partial','planned'] as const)" :key="s" :value="s">{{statusLabels[s]}}</option></select></label></div><nav class="workspace-pills" aria-label="功能分类"><button :aria-pressed="!group" @click="group=''">全部</button><button v-for="g in groups" :key="g" :aria-pressed="group===g" @click="group=g">{{g}}</button></nav><p v-if="!visible.length" class="workspace-empty">没有匹配的功能，试试其他关键词。</p><div class="workspace-feature-grid"><article v-for="c in visible" :key="c.id" class="workspace-card"><div class="row between"><small>{{c.group}}</small><span class="capability-badge" :data-status="c.status"><CheckCircle2 v-if="c.status==='ready'" :size="14"/><Clock3 v-else :size="14"/>{{statusLabels[c.status]}}</span></div><h2>{{c.name}}</h2><p>{{c.detail}}</p><RouterLink v-if="!c.path.startsWith('/features')" :to="c.path" class="inline-link">{{c.status==='planned'?'查看开放说明':'前往使用'}}<ArrowUpRight :size="16"/></RouterLink></article></div></div></template>
