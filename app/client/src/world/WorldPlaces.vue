<script setup lang="ts">
import {computed,ref} from 'vue'
import {MapPin} from 'lucide-vue-next'
import type {WorldState} from './types'
const props=defineProps<{state:WorldState}>(),selected=ref('')
const places=computed(()=>props.state.entities.filter(e=>e.type==='place')),current=computed(()=>places.value.find(e=>e.id===(selected.value||props.state.location))),connections=computed(()=>places.value.filter(e=>props.state.rules.routes?.[current.value?.id||'']?.includes(e.id)))
</script>
<template><section v-if="places.length" class="world-place-map" aria-label="已知地点连接"><div class="world-place-map-nodes"><button v-for="e in places" :key="e.id" :class="{active:e.id===current?.id}" :aria-pressed="e.id===current?.id" @click="selected=e.id"><MapPin :size="14"/>{{e.name}}<span v-if="e.id===state.location" class="world-node-dot" aria-label="当前地点"></span></button></div><div v-if="connections.length" class="world-place-map-links"><strong>{{current?.name}}</strong><span aria-hidden="true">→</span><div><span v-for="e in connections" :key="e.id">{{e.name}}</span></div></div></section></template>
