<script setup lang="ts">
import {ref} from 'vue'
import {Sun,Moon,UserRound,HelpCircle,MessageSquare} from 'lucide-vue-next'
import {useApp} from '../stores/app'
const store=useApp(),busy=ref(false)
async function toggle(){if(busy.value)return;const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';if(!store.user){document.documentElement.dataset.theme=theme;return}busy.value=true;try{await store.saveSettings({...store.settings,theme})}catch(e){store.message((e as Error).message)}finally{busy.value=false}}
</script>
<template><div class="workspace-global-actions"><span class="workspace-header-message">故事之间 · 内测体验</span><button aria-label="切换明暗主题" :disabled="busy" @click="toggle"><Sun v-if="store.settings.theme==='dark'" :size="20"/><Moon v-else :size="20"/></button><RouterLink v-if="store.user" to="/help/feedback" aria-label="反馈与服务通知"><MessageSquare :size="20"/></RouterLink><RouterLink to="/help" aria-label="使用帮助"><HelpCircle :size="20"/></RouterLink><RouterLink :to="store.user?'/product/account':'/login'" :aria-label="store.user?'个人档案':'登录账号'"><UserRound :size="20"/></RouterLink></div></template>
