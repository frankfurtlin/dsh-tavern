<script setup lang="ts">
import {computed,ref,watch,onUnmounted} from 'vue'
import {authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useCharacterAppearance} from '../world/character-appearance'
const props=withDefaults(defineProps<{entityId?:string;name:string;size?:number;imageUrl?:string}>(),{size:36}),context=useCharacterAppearance(),broken=ref(false),local=ref(''),capture=useRequestScope()
let revision=0
function clear(){if(local.value)URL.revokeObjectURL(local.value);local.value=''}
watch(()=>props.imageUrl,async url=>{const n=++revision,scope=capture();clear();if(!url?.startsWith('/api/'))return;try{const response=await fetch(backendBase()+url,{credentials:'include',headers:authorizationHeaders()});if(!response.ok)return;const blob=await response.blob();if(n===revision&&scope.valid())local.value=URL.createObjectURL(blob)}catch{/* Initials remain available. */}},{immediate:true})
onUnmounted(()=>{revision++;clear()})
const source=computed(()=>local.value||(props.entityId?context?.avatars.value[props.entityId]:undefined))
watch(source,()=>broken.value=false)
</script>
<template><span class="role-avatar" :style="{width:size+'px',height:size+'px'}"><img v-if="source&&!broken" :src="source" :alt="name+'头像'" :width="size" :height="size" decoding="async" @error="broken=true"><span v-else aria-hidden="true">{{Array.from(name.trim())[0]||'·'}}</span></span></template>
<style scoped>.role-avatar{display:inline-grid;place-items:center;flex:none;border:1px solid var(--edge);border-radius:50%;overflow:hidden;vertical-align:middle;background:var(--wash);color:var(--accent);font-family:var(--font-ui);font-size:16px;font-weight:600;line-height:1}.role-avatar img{display:block;width:100%;height:100%;object-fit:cover}</style>
