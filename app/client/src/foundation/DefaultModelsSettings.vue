<script setup lang="ts">
import VisionModelSettings from './VisionModelSettings.vue'
import {ref,onMounted} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import {operationKey,completeOperation} from './operation-key'
import type {Profile} from '../api/types'
const capture=useRequestScope(),version=ref(0),profiles=ref<Profile[]>([]),selected=ref(''),imageEnabled=ref(false),busy=ref(false),error=ref(''),notice=ref('')
async function load(){const scope=capture();try{const [policy,models]=await Promise.all([api<any>('/product/admin/default-models'),api<{items:Profile[]}>('/model-profiles')]);if(scope.valid()){version.value=policy.version;imageEnabled.value=policy.image.enabled;profiles.value=models.items.filter(x=>x.status==='active'&&x.hasKey!==false&&x.source!=='platform');selected.value=profiles.value.find(x=>x.modelId===policy.text.modelId)?.id||''}}catch(e){if(scope.valid())error.value=(e as Error).message}}
async function save(){if(busy.value)return;const scope=capture();busy.value=true;error.value='';notice.value='';try{await api('/product/admin/default-models',{method:'PUT',body:{requestKey:operationKey('default-models'),expectedVersion:version.value,textProfileId:selected.value||null,imageEnabled:imageEnabled.value}});if(scope.valid()){completeOperation('default-models');notice.value='默认模型已保存；新的账号会自动获得配置。已有通道与预算保持原样。';await load()}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
onMounted(load)
</script>
<template><section class="product-card"><h2>平台默认模型</h2><p class="product-note">为新账号提供可直接使用的文本与场景图像通道。密钥只保存在服务器，每个账号独立统计预算。更新设置仅影响后续配置，不重置已有账号。</p><p v-if="error" class="error" role="alert">{{error}}<button @click="load">重新加载</button></p><p v-if="notice" role="status">{{notice}}</p><form @submit.prevent="save"><label>新账号的默认文本模型<select v-model="selected"><option value="">不提供默认文本通道</option><option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{profile.name}} · {{profile.modelId}}</option></select></label><label class="row"><input v-model="imageEnabled" type="checkbox">提供当前管理员配置的 Agnes 图像通道</label><p class="product-note">文本调用及 token 预算、图像每日次数会从选定配置复制。管理员自己的已使用次数不会转移到新账号。</p><button class="primary" :disabled="busy">{{busy?'正在保存…':'保存平台默认模型'}}</button></form><RouterLink to="/features?group=账号与权益" class="inline-link">查看模型能力说明</RouterLink></section><VisionModelSettings admin/></template>
