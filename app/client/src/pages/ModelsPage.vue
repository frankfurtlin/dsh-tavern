<script setup lang="ts">
import {ref,computed,onMounted,defineAsyncComponent} from 'vue'
import {Check,MessageSquare,Type,Image,ShieldCheck} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import UsageRecords from '../components/UsageRecords.vue'
import {api} from '../api'
import type {Profile} from '../api/types'
import {useRequestScope} from '../platform/request-scope'
const AdvancedSettings=defineAsyncComponent(()=>import('./ModelAdvancedSettings.vue'))
const capture=useRequestScope(),loading=ref(true),error=ref(''),advanced=ref(false),refresh=ref(0)
const text=ref<Profile>(),image=ref<{enabled:boolean}>(),vision=ref<{enabled:boolean;modelId:string|null}>()
const ready=computed(()=>!!text.value&&image.value?.enabled&&vision.value?.enabled)
let attempt=0
async function load(){
 const scope=capture(),generation=++attempt;loading.value=true;error.value=''
 const results=await Promise.allSettled([api<{items:(Profile&{purpose?:string})[]}>('/model-profiles'),api<{enabled:boolean}>('/scene-profile',{namespace:'world'}),api<{enabled:boolean;modelId:string|null}>('/product/vision-model')])
 if(!scope.valid()||generation!==attempt)return
 const [models,scene,visual]=results
 text.value=models.status==='fulfilled'?models.value.items.find(p=>p.purpose!=='vision'&&p.isDefault&&p.status==='active'&&p.hasKey!==false):undefined
 image.value=scene.status==='fulfilled'?scene.value:undefined;vision.value=visual.status==='fulfilled'?visual.value:undefined
 if(results.some(r=>r.status==='rejected'))error.value='部分模型状态暂时无法读取，请重试或联系管理员。'
 loading.value=false;refresh.value++
}
onMounted(load)
</script>
<template><div class="workspace-page model-settings-page">
 <PageHeader title="模型与用量" description="内测期间，模型由平台准备，登录后即可使用，无需填写密钥。"/>
 <p v-if="loading" role="status" class="hint">正在确认模型状态…</p>
 <section v-else class="product-card beta-models" aria-label="内测默认模型">
  <h2><Check v-if="ready" :size="20"/>{{ready?'模型已准备好':'部分模型暂不可用'}}</h2>
  <p>{{ready?'继续阅读、进入故事或生成角色形象，都可以直接使用。':'请重试或联系管理员，我们会为你处理模型配置。'}}</p>
  <dl><div><dt><Type :size="19"/>故事文本</dt><dd>{{text?.modelId||'等待平台准备'}}</dd></div><div><dt><Image :size="19"/>图像生成</dt><dd>{{image?.enabled?'Agnes Image 2.5 Flash':'等待平台准备'}}</dd></div><div><dt><ShieldCheck :size="19"/>画面校验</dt><dd>{{vision?.enabled?vision.modelId:'等待平台准备'}}</dd></div></dl>
 </section>
 <p v-if="error" class="error" role="alert">{{error}}</p>
 <div v-if="!loading&&(!ready||error)" class="actions"><button @click="load">重新检查</button><RouterLink to="/help/feedback" class="inline-link"><MessageSquare :size="17"/>联系管理员</RouterLink></div>
 <details class="product-card optional-models" @toggle="advanced=($event.target as HTMLDetailsElement).open"><summary>高级设置：使用个人模型通道（可选）</summary><AdvancedSettings v-if="advanced" @updated="load"/></details>
 <UsageRecords :refresh="refresh"/>
</div></template>
<style scoped>
.beta-models h2,.beta-models dt{display:flex;align-items:center;gap:9px}.beta-models h2{font-size:18px}.beta-models>p{font-size:14px;color:var(--quiet);line-height:1.8}.beta-models dl{margin:24px 0 0;display:grid;gap:0}.beta-models dl>div{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:58px;border-top:1px solid var(--edge)}.beta-models dt{font-size:14px;white-space:nowrap}.beta-models dd{margin:0;font-size:14px;overflow-wrap:anywhere;text-align:right;color:var(--quiet)}.optional-models{margin:24px 0}.optional-models>summary{cursor:pointer;min-height:44px;display:flex;align-items:center;font-size:14px}.optional-models[open]>summary{margin-bottom:20px}
</style>
