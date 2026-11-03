<script setup lang="ts">
import {ref,onMounted,onUnmounted,computed,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useApp} from '../stores/app'
import {deviceEntryDestination} from '../platform/entry-policy'
import {BookOpen,Library,Users,Heart,Compass,UserRound,LockKeyhole,Sparkles,SlidersHorizontal,History,Database,HelpCircle} from 'lucide-vue-next'
import {useLocal} from '../stores/local'
import './final-product.css'
import {applyRootTheme} from './themes'
import {resolvedAppearance} from './appearance'
import AgentDock from '../agent/AgentDock.vue'
import AppShell from './AppShell.vue'
const store=useLocal(),route=useRoute(),router=useRouter(),app=useApp(),password=ref(''),selected=ref(''),initializing=ref(true),gateError=ref('')
const themeStyle=ref<Record<string,string>>({});let themeGeneration=0,disposed=false
async function loadTheme(){const generation=++themeGeneration,vault=store.vault;if(!vault){themeStyle.value=applyRootTheme(null);document.documentElement.dataset.theme='light';delete document.documentElement.dataset.motion;return}try{const appearance=await resolvedAppearance(vault);if(!disposed&&generation===themeGeneration&&store.vault===vault&&vault.unlocked){themeStyle.value=applyRootTheme(appearance.theme);document.documentElement.dataset.theme=appearance.mode;document.documentElement.dataset.motion=appearance.selection.motion}}catch(e){if(!disposed&&generation===themeGeneration&&store.vault===vault&&vault.unlocked){themeStyle.value=applyRootTheme(null);document.documentElement.dataset.theme='light';delete document.documentElement.dataset.motion;gateError.value=(e as Error).message}}}
function themeChanged(event:Event){const detail=(event as CustomEvent).detail;if(detail?.vaultId===store.vault?.id&&Array.isArray(detail.ids)&&detail.ids.some((id:string)=>id==='settings:reading'||id.startsWith('theme:')))void loadTheme()}
watch(()=>store.vault,()=>void loadTheme(),{flush:'sync'})
const immersive=computed(()=>!!route.meta.immersive)
const nav=[{path:'/local/library',name:'书架',icon:Library},{path:'/local/characters',name:'角色',icon:Users},{path:'/local/companions',name:'伴侣',icon:Heart},{path:'/local/discover',name:'发现',icon:Compass},{path:'/local/settings',name:'我的',icon:UserRound}]
const tools=[{path:'/local/new-world',name:'互动文游',icon:Compass},{path:'/local/agent',name:'创作助手',icon:Sparkles},{path:'/local/settings/models',name:'私人模型',icon:SlidersHorizontal},{path:'/local/settings/tasks',name:'任务中心',icon:History},{path:'/local/settings/data',name:'备份与迁移',icon:Database},{path:'/help',name:'使用帮助',icon:HelpCircle}]
onMounted(async()=>{window.addEventListener('reader-private-commit',themeChanged);try{await store.load();if(disposed)return;selected.value=store.available[0]?.id||'';const destination=deviceEntryDestination(!!selected.value,!!app.user);if(destination)await router.replace(destination)}catch(e){if(!disposed)gateError.value=(e as Error).message}finally{if(!disposed)initializing.value=false}})
async function open(){if(!selected.value||!store.available.some(v=>v.id===selected.value))return;await store.open(password.value,selected.value);password.value='';const vault=store.vault;if(vault){selected.value=vault.id;await loadTheme()}}
document.documentElement.dataset.runtime='local'
onUnmounted(()=>{disposed=true;themeGeneration++;window.removeEventListener('reader-private-commit',themeChanged);applyRootTheme(null);store.lock();delete document.documentElement.dataset.runtime;delete document.documentElement.dataset.motion})
</script>
<template>
  <div class="final-app" :style="themeStyle">
    <template v-if="!store.vault&&initializing"><div class="legacy-device-status" role="status">正在检查历史设备资料…</div></template><template v-else-if="!store.vault&&store.available.length">
      <div class="final-unlock"><div class="final-wordmark"><BookOpen :size="26"/><span>酒馆</span></div>
        <form class="final-vault-card" @submit.prevent="open"><span class="final-eyebrow">历史设备资料</span><h1>恢复这台设备上的资料</h1>
          <p>这里仅用于打开之前保存在这台设备上的加密资料。继续使用产品，请返回账号工作区。</p>
          <label v-if="store.available.length">历史资料<select v-model="selected"><option v-for="(vault,index) in store.available" :key="vault.id" :value="vault.id">资料 {{index+1}} · {{new Date(vault.createdAt).toLocaleDateString()}}</option></select></label>
          <label>原设备解锁口令<input v-model="password" type="password" autocomplete="current-password" minlength="8" maxlength="1024" required :disabled="store.busy||initializing" autofocus></label>

          <p v-if="store.error||gateError" class="final-error" role="alert">{{store.error||gateError}}</p><button class="final-primary" :disabled="store.busy||initializing">{{store.busy?'正在解锁…':'打开历史资料'}}</button>
          <RouterLink class="final-text-link" :to="app.user?'/explore':'/login'">返回账号工作区</RouterLink>
        </form><div class="final-unlock-art" aria-hidden="true"></div>
      </div>
    </template>
    <template v-else-if="store.vault">
      <AppShell :navigation="nav" :secondary="tools" :immersive="immersive" main-id="local-main" scope-label="历史设备资料">
        <template #sidebar-footer><RouterLink to="/worlds" class="scope-switch"><Compass :size="18"/><span>返回账号工作区<small>我的作品与存档</small></span></RouterLink><button class="final-lock" @click="store.lock"><LockKeyhole :size="18"/>锁定私人库</button></template>
        <template #header-actions><RouterLink to="/local/agent" aria-label="打开创作助手"><Sparkles :size="21"/></RouterLink></template>
        <RouterView :key="route.fullPath"/>
        <template #overlay><AgentDock/></template>
      </AppShell>
    </template>
    <section v-else class="legacy-device-status"><p v-if="gateError" role="alert">{{gateError}}</p><RouterLink :to="app.user?'/explore':'/login'">返回账号工作区</RouterLink></section>
  </div>
</template>
<style scoped>.legacy-device-status{display:grid;min-height:70dvh;place-content:center;gap:20px;padding:24px;color:var(--quiet)}</style>
