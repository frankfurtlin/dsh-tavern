<script setup lang="ts">
import {computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {WifiOff,X} from 'lucide-vue-next'
import {useApp} from './stores/app'
import {lifecycle} from './platform/bridge'
import {refreshAuth} from './api'
import StartupPlaceholder from './components/StartupPlaceholder.vue'
import TavernShell from './tavern/TavernShell.vue'
document.documentElement.dataset.design='aligned-v2'
document.documentElement.dataset.theme='light'
const store=useApp(),route=useRoute(),router=useRouter()
const immersive=computed(()=>!!route.meta.immersive)
const authPage=computed(()=>['/login','/register','/recover'].includes(route.path))
let cleanup:()=>void=()=>{}
watch(()=>store.user,user=>{
  if(!user&&store.booted&&!route.meta.public&&!authPage.value)router.replace(store.exitingAccount?'/login':{path:'/login',query:{next:route.fullPath}})
})
onMounted(async()=>{
  cleanup=await lifecycle(
    async()=>{if(await refreshAuth()){store.online=true}window.dispatchEvent(new Event('reader-resume'))},
    ()=>window.dispatchEvent(new Event('reader-save')),
    ()=>{if(document.querySelector('[role="dialog"]'))document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));else router.back()},
    online=>{store.online=online;if(online)window.dispatchEvent(new Event('reader-resume'))},
  )
})
onUnmounted(()=>cleanup())
</script>

<template>
 <StartupPlaceholder v-if="!store.booted"/>
 <template v-else-if="authPage"><main id="main" class="account-shell"><RouterView/></main></template>
 <RouterView v-else-if="immersive"/>
 <TavernShell v-else>
  <div class="tavern-banners">
   <p v-if="!store.online" class="tavern-offline"><WifiOff :size="16"/>离线模式 · 只能查看已缓存内容</p>
   <p v-if="store.notice" class="tavern-toast" role="status">{{store.notice}}<button type="button" aria-label="关闭提示" @click="store.notice=''"><X :size="16"/></button></p>
  </div>
  <RouterView/>
 </TavernShell>
</template>

<style scoped>
.tavern-banners{display:flex;flex-direction:column;gap:6px}
.tavern-banners:empty{display:none}
.tavern-offline,.tavern-toast{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0;padding:8px clamp(14px,3vw,28px);font-size:13px;background:var(--wash);color:var(--quiet)}
.tavern-toast{background:var(--canvas);border-bottom:1px solid var(--edge);color:var(--ink)}
.tavern-toast button{min-height:32px;display:flex;align-items:center;justify-content:center;border:0;background:transparent;color:var(--quiet);cursor:pointer}
</style>
