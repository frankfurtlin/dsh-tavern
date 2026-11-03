<script setup lang="ts">
import {inject,ref} from 'vue'
import {accountAppearanceKey} from '../world/character-appearance'
const account=inject(accountAppearanceKey,null),src=account?.src||ref(''),name=account?.name||ref('你'),broken=ref(false)
</script>
<template><span class="account-chat-avatar"><img v-if="src&&!broken" :src="src" alt="我的账号头像" @error="broken=true"><span v-else>{{Array.from(name)[0]}}</span></span></template>
<style scoped>.account-chat-avatar{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;flex:none;border-radius:50%;overflow:hidden;background:var(--wash);border:1px solid var(--edge);color:var(--accent);font-size:14px}.account-chat-avatar img{width:100%;height:100%;object-fit:cover}</style>
