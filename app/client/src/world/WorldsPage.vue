<script setup lang="ts">
import {ref,computed,onMounted} from 'vue'
import {Compass,Plus,ArrowUpRight,MoreHorizontal,Search,BookOpen,Archive,RotateCcw,Trash2} from 'lucide-vue-next'
import {useRouter} from 'vue-router'
import Sheet from '../components/Sheet.vue'
import {worldApi} from './api'
import {storyPath} from '../api/stories'
import {ApiError} from '../api'
import type {WorldInfo} from './types'
import {useRequestScope} from '../platform/request-scope'
const router=useRouter(),capture=useRequestScope(),items=ref<WorldInfo[]>([]),error=ref(''),loading=ref(true),enabled=ref(true),preview=ref(true),search=ref(''),tab=ref('active'),selected=ref<WorldInfo|null>(null),name=ref(''),busy=ref(false),deleting=ref(false),managementError=ref(''),stale=ref(false),refreshed=ref(false)
let selectionEpoch=0
const visible=computed(()=>items.value.filter(w=>(tab.value==='archived'?w.status==='archived':w.status!=='archived')&&(w.name+w.sourceTitle+w.playerName).includes(search.value.trim())))
const modelReady=ref(false)
async function load(){const scope=capture();try{const config=await worldApi<{enabled:boolean;ready:boolean;releaseStatus?:string}>('/config');if(!scope.valid())return;enabled.value=config.enabled&&config.ready;preview.value=config.releaseStatus!=='stable';if(!enabled.value)return;const result=await worldApi<{items:WorldInfo[]}>('/worlds',{cache:true});if(scope.valid())items.value=result.items}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())loading.value=false}}
function enter(w:WorldInfo){router.push(storyPath('world',w.id))}
function enterMode(w:WorldInfo,scene:boolean){if(!w.activeLineId){enter(w);return}void router.push({path:'/worlds/'+w.id+(scene?'/explore':'/play'),query:{line:w.activeLineId}})}
function manage(w:WorldInfo){selectionEpoch++;selected.value=w;name.value=w.name;deleting.value=false;busy.value=false;managementError.value='';stale.value=false;refreshed.value=false}
function closeManage(){selectionEpoch++;selected.value=null;busy.value=false;managementError.value='';stale.value=false;refreshed.value=false}
async function edit(status?:string){
  const target=selected.value;if(!target)return
  const scope=capture(),epoch=selectionEpoch,valid=()=>scope.valid()&&epoch===selectionEpoch
  busy.value=true;managementError.value=''
  try{
    await worldApi('/worlds/'+target.id,{method:'PATCH',body:{expectedVersion:target.version,name:name.value,...(status?{status}:{})}})
    if(!scope.valid())return;if(valid())closeManage();await load()
  }catch(e){if(valid()){managementError.value=(e as Error).message;stale.value=e instanceof ApiError&&e.code==='VERSION_CONFLICT'}}finally{if(valid())busy.value=false}
}
async function refreshSelected(){
  const target=selected.value;if(!target)return
  const scope=capture(),epoch=selectionEpoch,valid=()=>scope.valid()&&epoch===selectionEpoch
  busy.value=true
  try{const fresh=await worldApi<WorldInfo>('/worlds/'+target.id);if(valid()){selected.value=fresh;stale.value=false;refreshed.value=true;managementError.value='';items.value=items.value.map(w=>w.id===fresh.id?fresh:w)}}catch(e){if(valid())managementError.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function remove(){
  const target=selected.value;if(!target)return
  const scope=capture(),epoch=selectionEpoch,valid=()=>scope.valid()&&epoch===selectionEpoch
  busy.value=true;managementError.value=''
  try{await worldApi('/worlds/'+target.id,{method:'DELETE'});if(!scope.valid())return;if(valid())closeManage();await load()}catch(e){if(valid())managementError.value=(e as Error).message}finally{if(valid())busy.value=false}
}
onMounted(()=>{void load();const scope=capture();void import('../api').then(async({api})=>{try{const models=await api<{defaultProfileId:string|null}>('/model-profiles');if(scope.valid())modelReady.value=!!models.defaultProfileId}catch{}})})
</script>
<template><div class="world-list-heading"><div><span class="eyebrow">走进故事，成为其中的一个人</span><h1>互动世界<span class="world-heading-dot"></span><span v-if="preview" class="world-preview-badge">预览</span></h1></div><RouterLink v-if="enabled" to="/worlds/scripts" class="world-primary-link"><BookOpen :size="18"/>拆解剧本</RouterLink><RouterLink v-if="enabled" to="/worlds/new" class="world-primary-link"><Plus :size="18"/>从小说创建</RouterLink></div><p v-if="error" class="error" role="alert">{{error}}</p><div v-if="loading" class="loading">正在打开世界…</div><section v-else-if="!enabled" class="empty"><Compass :size="36"/><h2>互动世界正在准备</h2><RouterLink to="/library" class="inline-link">回到书架</RouterLink></section><template v-else><section v-if="!items.length&&!search&&tab==='active'" class="world-first-case" aria-labelledby="first-case-title"><div><span class="eyebrow">第一次体验</span><h2 id="first-case-title">先体验蓝宝石案</h2><p>从已校对的案例开始：追索线索，选择结局，再完成交接与善后。准备案例本身不消耗模型调用。</p></div><ol><li><RouterLink to="/settings/models">{{modelReady?'默认模型已就绪，可直接开始':'先检查文本模型与图像通道'}}</RouterLink></li><li><RouterLink to="/worlds/scripts" data-track="onboarding.sample.opened">载入已校对案例</RouterLink></li><li>开始调查，完成一条结局的善后</li></ol><RouterLink to="/cases/blue-carbuncle" class="inline-link">了解三条结局的玩法</RouterLink></section><div class="world-list-tools"><div class="tabs"><button :class="{active:tab==='active'}" @click="tab='active'">我的世界<span v-if="items.length"> {{items.filter(w=>w.status!=='archived').length}}</span></button><button :class="{active:tab==='archived'}" @click="tab='archived'">已归档</button></div><label class="search-field"><Search :size="18"/><input v-model="search" aria-label="搜索世界" placeholder="寻找一个世界"></label></div><section v-if="!visible.length" class="world-empty"><div class="world-empty-orbit"><span></span><span></span><span></span><Compass :size="44"/></div><h2>{{search?'没有找到这个世界':tab==='archived'?'这里还没有归档的世界':'故事里，还有你的位置'}}</h2><RouterLink v-if="!search&&tab==='active'" to="/worlds/new" class="world-primary-link">创建第一个世界<ArrowUpRight :size="18"/></RouterLink></section><div v-else class="world-grid"><article v-for="(w,i) in visible" :key="w.id" class="world-card"><button class="world-card-art" :class="'world-art-'+i%3" :aria-label="'进入'+w.name" @click="enter(w)"><svg viewBox="0 0 400 180" aria-hidden="true"><path d="M0 155 Q80 85 155 128 T400 80"/><path d="M0 174 Q110 120 220 150 T400 130"/><path d="M190 129 Q230 80 295 58" class="orbit"/><circle cx="190" cy="129" r="5"/><circle cx="295" cy="58" r="6"/><circle cx="340" cy="52" r="27" class="sun"/></svg><span>{{w.playerName?'扮演 '+w.playerName:'等待一个新身份'}}</span></button><div class="world-card-copy"><div class="row between"><small><BookOpen :size="14"/>{{w.sourceTitle}}{{w.sourceRemoved?' · 来源已移除':''}}</small><button class="icon-button" :aria-label="'管理'+w.name" @click="manage(w)"><MoreHorizontal :size="20"/></button></div><h2><button @click="enter(w)">{{w.name}}</button></h2><p>{{w.locationName||w.description||'世界正在整理中'}}</p><div class="row between world-card-bottom"><span>{{w.timeLabel||({preparing:'正在准备',failed:'准备未完成',archived:'已归档'} as Record<string,string>)[w.status]||'故事的起点'}}</span><div class="world-card-modes" v-if="w.activeLineId&&w.status==='active'"><button @click="enterMode(w,false)"><BookOpen :size="14"/>纯故事</button><button @click="enterMode(w,true)"><Compass :size="14"/>360° 世界</button></div><button v-else class="world-text-link" @click="enter(w)">进入世界<ArrowUpRight :size="18"/></button></div></div></article></div></template><Sheet v-if="selected" title="管理世界" @close="closeManage"><p v-if="managementError" class="error" role="alert">{{managementError}}</p><button v-if="stale" :disabled="busy" @click="refreshSelected">载入最新信息</button><small v-if="refreshed" role="status">最新名称：{{selected.name}}</small><label>世界名称<input v-model="name" maxlength="160"></label><button class="primary" :disabled="busy||!name.trim()" @click="edit()">保存名称</button><div class="divider actions"><button :disabled="busy" @click="edit(selected.status==='archived'?'active':'archived')"><Archive v-if="selected.status!=='archived'" :size="18"/><RotateCcw v-else :size="18"/>{{selected.status==='archived'?'恢复':'归档'}}</button><button class="danger" :disabled="busy" @click="deleting=true"><Trash2 :size="18"/>删除世界</button></div><div v-if="deleting" class="world-delete-confirm"><p>删除「{{selected.name}}」及其世界线？</p><div class="actions"><button @click="deleting=false">保留</button><button class="danger" :disabled="busy" @click="remove">确认删除</button></div></div></Sheet></template>

<style scoped>.world-first-case{padding:24px;border:1px solid var(--edge);border-radius:16px;background:var(--canvas);margin:20px 0}.world-first-case h2{font-size:24px}.world-first-case p{color:var(--quiet);line-height:1.8}.world-first-case ol{padding-left:24px;line-height:2}.world-first-case a{color:var(--accent)}</style>
