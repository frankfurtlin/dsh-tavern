<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const mode=ref<'progress'|'full'>('progress'),version=ref(1),busy=ref(false),error=ref(''),loaded=ref(false),capture=useRequestScope(),store=useApp()
onMounted(async()=>{const scope=capture();try{const result=await api<{mode:'progress'|'full';version:number}>('/character-preferences');if(scope.valid()){mode.value=result.mode;version.value=result.version;loaded.value=true}}catch(e){if(scope.valid())error.value=(e as Error).message}})
async function save(){const scope=capture();busy.value=true;error.value='';try{const result=await api<{mode:'progress'|'full';version:number}>('/character-preferences',{method:'PUT',body:{mode:mode.value,expectedVersion:version.value}});if(scope.valid()){version.value=result.version;store.message('角色阅读范围已保存')}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
</script>
<template><section class="setting-row"><h2>角色与剧透</h2><p class="muted">角色卡和聊天默认跟随你的阅读进度。</p><label>角色阅读范围<select v-model="mode" :disabled="!loaded||busy"><option value="progress">跟随已读进度 · 隐藏未来剧情</option><option value="full">全书模式 · 允许显示完整故事</option></select></label><p v-if="mode==='full'" class="hint">全书模式会展示尚未阅读的人物和剧情。切回已读模式后，新对话继续隐藏未来剧情。</p><p v-if="error" class="error" role="alert">{{error}}</p><button class="primary" :disabled="!loaded||busy||!store.online" @click="save">保存角色设置</button></section></template>
