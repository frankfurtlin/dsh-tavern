<script setup lang="ts">
import PageHeader from '../workspace/PageHeader.vue'
import {onMounted,onUnmounted,ref} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import {messages as t,switchLanguage} from './locale'
import ProductNav from './ProductNav.vue'
import type {ProductAccount} from './types'
import {privateObjectBlob} from './private-object'
const avatarUrl=ref('');let avatarRevision=0
function clearAvatar(){if(avatarUrl.value)URL.revokeObjectURL(avatarUrl.value);avatarUrl.value=''}
async function loadAvatar(){if(!info.value?.avatarId)return;const scope=capture(),attempt=++avatarRevision;try{const blob=await privateObjectBlob(info.value.avatarId,()=>scope.valid()&&attempt===avatarRevision);if(scope.valid()&&attempt===avatarRevision){clearAvatar();avatarUrl.value=URL.createObjectURL(blob)}}catch{if(scope.valid())clearAvatar()}}
const info=ref<ProductAccount>(),error=ref(''),notice=ref(''),busy=ref(false),capture=useRequestScope()
async function load(){const scope=capture();try{const value=await api<ProductAccount>('/product/account');if(scope.valid()){info.value=value;await switchLanguage(value.language);void loadAvatar()}}catch(e){if(scope.valid())error.value=(e as Error).message}}
async function save(){if(!info.value)return;const scope=capture();busy.value=true;error.value='';try{await api('/product/account',{method:'PUT',body:{displayName:info.value.displayName,language:info.value.language,avatarId:info.value.avatarId}});if(scope.valid()){await switchLanguage(info.value.language);notice.value='个人资料已保存';void loadAvatar()}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
async function avatar(event:Event){const file=(event.target as HTMLInputElement).files?.[0];if(!file||!info.value)return;const scope=capture(),body=new FormData();body.append('file',file);body.append('purpose','avatar');busy.value=true;try{const value=await api<{id:string}>('/product/objects',{method:'POST',body});if(scope.valid()){info.value.avatarId=value.id;await save()}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
onMounted(load)
onUnmounted(()=>{avatarRevision++;clearAvatar()})
</script>
<template><div class="product-page"><PageHeader title="个人资料" description="管理你的头像、昵称和账号信息。"/><ProductNav :admin="info?.role==='ADMIN'"/><p v-if="error" class="error" role="alert">{{error}}<button @click="load">重试</button></p><p v-if="notice" role="status" class="product-hint">{{notice}}</p><div v-if="info" class="product-grid"><section class="product-card"><h2>{{t.account}}</h2><img v-if="avatarUrl" :src="avatarUrl" alt="我的头像" class="product-avatar"><p>{{info.username}} · {{info.role==='ADMIN'?'管理员':'用户'}}</p><form @submit.prevent="save"><label>{{t.displayName}}<input v-model="info.displayName" maxlength="100" autocomplete="nickname"></label><label>{{t.language}}<select v-model="info.language"><option value="zh-CN">简体中文</option><option value="en">English（账号服务）</option></select></label><label>头像<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="busy" @change="avatar"></label><p v-if="info.avatarId" class="product-note">头像已上传并绑定账号</p><button class="primary" :disabled="busy">{{t.save}}</button></form></section><section class="product-card"><h2>安全与设备</h2><p>登录设备、密码和私人数据备份由账号服务与数据使用说明管理。</p><div class="divider"><h2>进入故事</h2><p>先从《蓝宝石案》开始，试着完成一次调查和善后，再另开一条线选择其他结局。</p><RouterLink to="/works" class="inline-link" data-track="product.open_worlds">打开我的作品</RouterLink></div></section></div><p v-else>{{t.loading}}</p></div></template>
