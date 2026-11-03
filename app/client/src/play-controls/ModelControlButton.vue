<script setup lang="ts">
import {ref,watch,onMounted} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import type {PlayControl} from './types'
const props=defineProps<{kind:'lines'|'chats';identity:string}>(),emit=defineEmits<{open:[]}>(),model=ref('文本模型'),capture=useRequestScope();let epoch=0
async function refresh(){const current=++epoch,scope=capture();try{const result=await api<PlayControl>('/play-controls/'+props.kind+'/'+props.identity+'/settings');if(scope.valid()&&current===epoch)model.value=result.effectiveModel?.name||'模型不可用'}catch{if(scope.valid()&&current===epoch)model.value='文本模型'}}
watch(()=>props.identity,refresh);onMounted(refresh);defineExpose({refresh})
</script>
<template><button class="model-control-button" aria-label="模型与记忆" @click="emit('open')"><span>{{model}}</span><small>模型与记忆</small></button></template>
<style scoped>.model-control-button{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;flex-shrink:0;min-width:112px;min-height:44px;max-width:220px;padding:6px 10px;white-space:nowrap}.model-control-button span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.model-control-button small{font-size:10px;white-space:nowrap;color:var(--muted,#82776d)}</style>
