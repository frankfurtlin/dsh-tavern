<script setup lang="ts">
import {computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {Library,MessagesSquare,UserRound,BookOpen,WifiOff,X,NotebookPen,Compass,SlidersHorizontal,Database,Wallet,HelpCircle,Plus} from 'lucide-vue-next'
import {Capacitor,registerPlugin,SystemBars,SystemBarsStyle} from '@capacitor/core'
import {useApp} from './stores/app'
import {lifecycle,native} from './platform/bridge'
import {refreshAuth} from './api'
import {syncNotes} from './offline/notes'
import {resumeDeletedBookCleanup} from './platform/book-cache'
import AppShell from './presentation/AppShell.vue'
import './presentation/aligned.css'
import './presentation/workspace.css'
import AccountPanel from './foundation/AccountPanel.vue'
import RecoveryNotice from './foundation/RecoveryNotice.vue'
import StartupPlaceholder from './components/StartupPlaceholder.vue'
import {workspaceNavigation,workspaceGroups} from './workspace/navigation'
import HeaderActions from './workspace/HeaderActions.vue'
import './workspace/workspace.css'
import './workspace/secondary-pages.css'
import './catalog/catalog.css'
document.documentElement.dataset.design='aligned-v2'
const store=useApp(),route=useRoute(),router=useRouter();const reading=computed(()=>route.path.endsWith('/read')||route.meta.immersive);let cleanup:()=>void=()=>{}
const systemTheme=matchMedia('(prefers-color-scheme: dark)')
const nativeDisplay=Capacitor.getPlatform()==='android'?registerPlugin<{setTheme(options:{theme:string}):Promise<void>}>('ReaderDisplay'):null
let themeGeneration=0,themeSync:Promise<void>=Promise.resolve()
function applyTheme(){
  const theme=store.settings.theme==='system'?(systemTheme.matches?'dark':'light'):store.settings.theme
  document.documentElement.dataset.theme=theme
  const generation=++themeGeneration
  if(!native)return
  const synchronize=async()=>{
    if(generation!==themeGeneration)return
    await SystemBars.setStyle({style:theme==='dark'?SystemBarsStyle.Dark:SystemBarsStyle.Light}).catch(()=>{})
    if(generation!==themeGeneration)return
    if(nativeDisplay)await nativeDisplay.setTheme({theme}).catch(()=>{})
  }
  themeSync=themeSync.then(synchronize,synchronize)
}
watch(()=>store.settings.theme,applyTheme,{immediate:true});systemTheme.addEventListener('change',applyTheme)
let localRecovery=0
watch(()=>[store.online,store.user?.id],async([online,owner])=>{const attempt=++localRecovery;if(!online||!owner)return;await resumeDeletedBookCleanup().catch(()=>{});if(attempt===localRecovery&&store.online&&store.user?.id===owner)await syncNotes().catch(()=>{})},{immediate:true})
watch(()=>store.user,user=>{if(!user&&store.booted&&!route.meta.public&&!['/login','/register'].includes(route.path))router.replace(store.exitingAccount?'/login':{path:'/login',query:{next:route.fullPath}})})
onMounted(async()=>{cleanup=await lifecycle(async()=>{if(await refreshAuth()){store.online=true;await store.loadWorldConfig()}window.dispatchEvent(new Event('reader-resume'))},()=>window.dispatchEvent(new Event('reader-save')),()=>{if(document.querySelector('[role="dialog"]'))document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));else router.back()},online=>{store.online=online;if(online)window.dispatchEvent(new Event('reader-resume'))})});onUnmounted(()=>{cleanup();systemTheme.removeEventListener('change',applyTheme)})
</script>
<template>
 <StartupPlaceholder v-if="!store.booted"/>
 <AppShell v-else-if="store.user&&!['/login','/register','/recover'].includes(route.path)" :navigation="workspaceNavigation" :groups="workspaceGroups" :immersive="!!reading" :work-entry="route.path.startsWith('/stories/')" main-id="main" scope-label="故事之间">
  <template #sidebar-footer><AccountPanel/></template>
  <template #header-actions><HeaderActions/></template>
  <div v-if="!store.online" class="offline"><WifiOff :size="16"/>离线阅读 · 仅显示此账号已缓存内容</div>
  <div v-if="store.user?.aiMode==='mock'" class="mock-banner">测试模式 · AI 使用确定性 mock，不能作为真实模型验收</div>
  <div v-if="store.notice" class="notice" role="status">{{store.notice}}<button class="icon-button" aria-label="关闭提示" @click="store.notice=''"><X :size="18"/></button></div>
  <RecoveryNotice v-if="!reading"/>
  <RouterView/>
 </AppShell>
 <main v-else id="main" class="account-shell"><RouterView/></main>
</template>
