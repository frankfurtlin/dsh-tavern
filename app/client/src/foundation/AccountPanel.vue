<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {UserRound,ChevronRight,Shield,Wallet} from 'lucide-vue-next'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import type {ProductAccount,ProductWallet} from './types'
import {privateObjectBlob} from './private-object'
const store=useApp(),capture=useRequestScope(),profile=ref<ProductAccount>(),wallet=ref<ProductWallet>(),failed=ref(false),avatar=ref('');let attempt=0
async function load(){const scope=capture(),version=++attempt;failed.value=false;try{const [account,balance]=await Promise.all([api<ProductAccount>('/product/account'),api<ProductWallet>('/product/wallet')]);if(!scope.valid()||version!==attempt)return;profile.value=account;wallet.value=balance;if(account.avatarId){const blob=await privateObjectBlob(account.avatarId,()=>scope.valid()&&version===attempt);if(scope.valid()&&version===attempt){if(avatar.value)URL.revokeObjectURL(avatar.value);avatar.value=URL.createObjectURL(blob)}}}catch{if(scope.valid()&&version===attempt)failed.value=true}}
onMounted(load);onUnmounted(()=>{attempt++;if(avatar.value)URL.revokeObjectURL(avatar.value)})
</script>
<template><div class="workspace-account"><RouterLink class="workspace-identity" to="/product/account"><img v-if="avatar" :src="avatar" alt="" class="workspace-avatar"><span v-else class="workspace-avatar">{{(profile?.displayName||store.user?.username||'我').slice(0,1).toUpperCase()}}</span><span class="workspace-identity-copy"><strong>{{profile?.displayName||store.user?.username}}</strong><small>{{profile?.role==='ADMIN'?'管理员':'我的账号'}}</small></span><ChevronRight :size="16"/></RouterLink><RouterLink v-if="wallet" to="/product/wallet" class="workspace-balance"><Wallet :size="16"/><span>{{wallet.unlimitedUsage?'内测不限额度':wallet.billingEnabled?wallet.available.toLocaleString()+' 积分可用':'内测体验 · 查看权益与记录'}}</span></RouterLink><button v-if="failed" class="text-action" @click="load">重新加载账号信息</button><RouterLink v-if="profile?.role==='ADMIN'" class="workspace-admin" to="/product/admin"><Shield :size="15"/>运营管理</RouterLink></div></template>
