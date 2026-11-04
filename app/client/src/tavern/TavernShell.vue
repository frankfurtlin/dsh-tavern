<script setup lang="ts">
import {computed} from 'vue'
import {useRoute} from 'vue-router'
import {ArrowLeft,BookOpen,Settings} from 'lucide-vue-next'
const route=useRoute()
const title=computed(()=>typeof route.meta.title==='string'?route.meta.title:'')
const home=computed(()=>route.path==='/')
const showSettings=computed(()=>route.path!=='/settings')
</script>

<template>
 <div class="tavern-shell">
  <header class="tavern-top">
   <div class="tavern-top-side">
    <RouterLink v-if="!home" to="/" class="tavern-back" aria-label="返回角色卡列表"><ArrowLeft :size="18"/><span>角色卡</span></RouterLink>
    <RouterLink v-else to="/" class="tavern-brand"><BookOpen :size="22"/><span>酒馆</span></RouterLink>
   </div>
   <p class="tavern-title">{{title}}</p>
   <div class="tavern-top-side tavern-top-right">
    <RouterLink v-if="showSettings" to="/settings" class="tavern-icon" aria-label="设置"><Settings :size="19"/><span>设置</span></RouterLink>
   </div>
  </header>
  <main id="main" class="tavern-main"><slot/></main>
 </div>
</template>

<style scoped>
.tavern-shell{min-height:100dvh;display:flex;flex-direction:column;background:var(--paper);color:var(--ink)}
.tavern-top{position:sticky;top:0;z-index:30;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;min-height:56px;padding:6px clamp(12px,3vw,24px);padding-top:calc(6px + env(safe-area-inset-top));background:color-mix(in srgb,var(--paper) 92%,transparent);backdrop-filter:blur(12px);border-bottom:1px solid var(--edge)}
.tavern-top-side{display:flex;align-items:center;min-width:0}.tavern-top-right{justify-content:flex-end}
.tavern-brand,.tavern-back,.tavern-icon{display:flex;align-items:center;gap:6px;min-height:44px;padding:6px 8px;border-radius:10px;color:var(--ink);text-decoration:none;font-size:14px}
.tavern-brand{font-family:var(--font-serif);font-size:20px;color:var(--accent);gap:8px}
.tavern-back,.tavern-icon{color:var(--quiet)}.tavern-back:hover,.tavern-icon:hover{color:var(--ink);background:var(--wash)}
.tavern-title{font-size:15px;font-weight:600;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:38vw;margin:0}
.tavern-main{flex:1;min-height:0;display:flex;flex-direction:column}
@media(max-width:520px){.tavern-back span,.tavern-icon span{display:none}}
</style>
