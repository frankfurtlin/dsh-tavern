<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'
const props=defineProps<{name:string;src:string;active:boolean}>()
const url=ref(''),error=ref(false),retry=ref(0)
let controller:AbortController|undefined,epoch=0
function clear(){if(url.value)URL.revokeObjectURL(url.value);url.value=''}
watch(()=>[props.src,retry.value],async()=>{
 const current=++epoch;controller?.abort();controller=new AbortController();clear();error.value=false
 if(!props.src)return
 try{const r=await fetch(backendBase()+props.src,{credentials:'include',headers:authorizationHeaders(),signal:controller.signal});if(!r.ok)throw new Error('portrait unavailable');const blob=await r.blob();if(current===epoch)url.value=URL.createObjectURL(blob)}
 catch(e){if(current===epoch&&!controller?.signal.aborted)error.value=true}
},{immediate:true})
onUnmounted(()=>{epoch++;controller?.abort();clear()})
</script>
<template><div class="portrait-actor" :class="{'is-speaking':active}" :aria-label="name+'立绘'" :data-speaker="active">
 <img v-if="url" :src="url" :alt="name+'透明立绘'" draggable="false" @error="error=true;clear()" />
 <div v-else class="portrait-actor-placeholder"><span>{{name}}</span><button v-if="error" @click="retry++">重载立绘</button><small v-else>正在载入立绘…</small></div>
 <span class="portrait-actor-name">{{name}}</span>
</div></template>
