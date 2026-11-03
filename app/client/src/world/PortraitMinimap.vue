<script setup lang="ts">
import {computed,ref} from 'vue'
import {Compass,ChevronDown,ChevronUp,MoveUpRight} from 'lucide-vue-next'
import {imageBound} from './scene-grounding'
import type {ScenePoint} from './scene-types'
const props=defineProps<{place:string;exits:ScenePoint[];yaw:number;disabled:boolean}>()
const emit=defineEmits<{focus:[point:ScenePoint];travel:[point:ScenePoint]}>()
const expanded=ref(true)
const points=computed(()=>props.exits.filter(imageBound).map(p=>({...p,x:50+Math.sin(p.yaw)*32,y:50-Math.cos(p.yaw)*32})))
</script>
<template><aside class="portrait-minimap" aria-label="场景小地图">
 <button class="portrait-map-heading" :aria-label="expanded?'收起小地图':'展开小地图'" :aria-expanded="expanded" @click="expanded=!expanded"><Compass :size="17"/><strong>小地图</strong><ChevronUp v-if="expanded" :size="15"/><ChevronDown v-else :size="15"/></button>
 <template v-if="expanded"><p class="portrait-map-place">{{place}}</p><div class="portrait-map-drawing">
  <svg viewBox="0 0 100 100" aria-hidden="true"><path v-for="point in points" :key="point.id" :d="`M50 50 L${point.x} ${point.y}`"/><g :transform="`translate(50 50) rotate(${yaw*180/Math.PI})`"><path class="portrait-map-gaze" d="M0 0 L-10 -21 Q0 -25 10 -21 Z"/></g></svg>
  <span class="portrait-map-here"><i></i><small>当前位置</small></span>
  <button v-for="point in points" :key="point.id" class="portrait-map-point" :style="{left:point.x+'%',top:point.y+'%'}" :disabled="disabled" :aria-label="'查看通往'+point.name+'的路口'" @click="emit('focus',point)"><i></i><small>{{point.name}}</small></button>
 </div><div class="portrait-map-routes"><button v-for="point in exits" :key="point.id" :disabled="disabled" :aria-label="imageBound(point)?'查看通往'+point.name+'的路口':'前往'+point.name" @click="imageBound(point)?emit('focus',point):emit('travel',point)"><MoveUpRight :size="14"/>{{point.name}}<small v-if="imageBound(point)">查看路口</small></button><p v-if="!exits.length">暂未发现可通行的路。</p></div></template>
</aside></template>
