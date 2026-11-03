<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {LockKeyhole,Search,ChevronRight,BookOpen,SlidersHorizontal} from 'lucide-vue-next'
import {useLocal} from '../stores/local'
const store=useLocal(),search=ref(''),agentEnabled=ref(true),error=ref(''),busy=ref(false)
let disposed=false
const groups=[
 {name:'阅读与外观',links:[{name:'外观与阅读',path:'/local/settings/appearance',tags:'主题 配色 日间 夜读 字号 JSON 动效 暖纸'},{name:'阅读笔记',path:'/local/notebook',tags:'划线 标注'},{name:'阅读记录',path:'/local/insights',tags:'时长 目标 统计'}]},
 {name:'模型与创作',links:[{name:'模型',path:'/local/settings/models',tags:'文本 通道 Key API'},{name:'多模态模型',path:'/local/settings/media',tags:'图片 语音 视频 索引'},{name:'创作助手',path:'/local/agent',tags:'Agent 任务 工具'},{name:'技能工坊',path:'/local/agent/skills',tags:'建卡 改写 整理'},{name:'插件中心',path:'/local/plugins',tags:'记忆 上下文'},{name:'MCP 工具与连接',path:'/local/agent/mcp',tags:'工具 权限'}]},
 {name:'素材与数据',links:[{name:'画廊与素材',path:'/local/gallery',tags:'图像 配音 附件 图片'},{name:'内容索引',path:'/local/vectors',tags:'向量 embedding 检索'},{name:'视频创作',path:'/local/video',tags:'动态 生成'},{name:'任务中心',path:'/local/settings/tasks',tags:'停止 请求 正文 回执 恢复'},{name:'备份与迁移',path:'/local/settings/data',tags:'导出 恢复 数据 私人库'}]}
]
const filtered=computed(()=>{const query=search.value.trim().toLocaleLowerCase();return groups.map(group=>({...group,links:group.links.filter(link=>(group.name+' '+link.name+' '+link.tags).toLocaleLowerCase().includes(query))})).filter(group=>group.links.length)})
const commonPaths=new Set(['/local/settings/appearance','/local/settings/models','/local/settings/media','/local/notebook','/local/insights','/local/settings/tasks','/local/settings/data'])
const common=computed(()=>filtered.value.map(group=>({...group,links:group.links.filter(link=>commonPaths.has(link.path))})).filter(group=>group.links.length))
const advanced=computed(()=>filtered.value.map(group=>({...group,links:group.links.filter(link=>!commonPaths.has(link.path))})).filter(group=>group.links.length))
const visibleGroups=computed(()=>search.value.trim()?filtered.value:common.value)
const showAssistant=computed(()=>!search.value.trim()||'创作助手 悬浮 入口 显示 Agent'.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()))
async function saveAgent(){const vault=store.vault;if(!vault||busy.value)return;busy.value=true;error.value='';try{const row=await vault.read('settings:agent-ui');await vault.commit(crypto.randomUUID(),'settings','settings:agent-ui',row?.revision||0,{...(row?.value as Record<string,unknown>||{}),enabled:agentEnabled.value});if(!disposed&&store.vault===vault&&vault.unlocked)window.dispatchEvent(new Event('reader-agent-ui-changed'))}catch(e){if(!disposed&&store.vault===vault&&vault.unlocked)error.value=(e as Error).message}finally{if(!disposed)busy.value=false}}
onMounted(async()=>{const vault=store.vault;if(!vault)return;try{const row=await vault.read<{enabled:boolean}>('settings:agent-ui');if(!disposed&&store.vault===vault&&vault.unlocked)agentEnabled.value=row?.value.enabled!==false}catch(e){if(!disposed&&store.vault===vault&&vault.unlocked)error.value=(e as Error).message}})
onUnmounted(()=>disposed=true)
</script>
<template>
 <section class="local-settings-page">
  <header class="final-page-heading"><h1>我的</h1></header>
  <div v-if="!search" class="settings-identity"><span class="settings-avatar"><BookOpen :size="25"/></span><div><strong>私人故事库</strong><span>当前设备</span></div></div>
  <label class="settings-search"><Search :size="18"/><input v-model="search" aria-label="搜索设置" placeholder="搜索设置" maxlength="100"></label>
  <p v-if="error" class="final-error" role="alert">{{error}}</p>
  <div class="settings-sections"><section v-for="group in visibleGroups" :key="group.name" class="settings-section"><h2>{{group.name}}</h2><RouterLink v-for="link in group.links" :key="link.path" class="settings-link" :to="link.path"><span>{{link.name}}</span><ChevronRight :size="17"/></RouterLink></section></div>
  <details v-if="!search&&advanced.length" class="settings-advanced"><summary><SlidersHorizontal :size="18"/><span>高级工具</span><ChevronRight :size="17"/></summary><div class="settings-sections"><section v-for="group in advanced" :key="group.name" class="settings-section"><h2>{{group.name}}</h2><RouterLink v-for="link in group.links" :key="link.path" class="settings-link" :to="link.path"><span>{{link.name}}</span><ChevronRight :size="17"/></RouterLink></section></div></details>
  <p v-if="!filtered.length&&!showAssistant" class="final-subtle">没有匹配的设置</p>
  <label v-if="showAssistant" class="final-check settings-assistant"><input v-model="agentEnabled" type="checkbox" :disabled="busy" @change="saveAgent">显示创作助手悬浮入口</label>
  <div v-if="!search" class="settings-lock"><button @click="store.lock"><LockKeyhole :size="18"/>锁定私人库</button><RouterLink class="final-text-link" to="/login">打开服务器账号</RouterLink></div>
 </section>
</template>
<style scoped>
.settings-identity{display:flex;align-items:center;gap:16px;margin:0 0 28px;padding-bottom:28px;border-bottom:1px solid var(--edge)}.settings-avatar{display:grid;place-items:center;width:60px;height:60px;border:1px solid var(--edge);border-radius:50%;color:var(--accent);background:var(--canvas)}.settings-identity>div{display:grid;gap:2px}.settings-identity strong{font-family:var(--font-serif);font-size:22px;font-weight:500}.settings-identity>div>span{font-size:13px;color:var(--quiet)}.settings-advanced{margin-top:28px;border-top:1px solid var(--edge);padding-top:16px}.settings-advanced>summary{display:flex;align-items:center;gap:10px;min-height:52px;cursor:pointer;list-style:none;color:var(--quiet)}.settings-advanced>summary::-webkit-details-marker{display:none}.settings-advanced>summary>span{flex:1}.settings-advanced>summary>svg:last-child{transition:transform .18s}.settings-advanced[open]>summary>svg:last-child{transform:rotate(90deg)}.settings-advanced[open]>.settings-sections{margin-top:20px}

.local-settings-page{max-width:1080px}.settings-search{position:relative;max-width:560px;margin:0 0 32px}.settings-search svg{position:absolute;left:14px;top:15px;color:var(--quiet)}.settings-search input{padding-left:44px}.settings-sections{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:32px}.settings-section h2{font-size:20px;margin:0 0 18px}.settings-link{min-height:52px;display:flex;align-items:center;justify-content:space-between;gap:14px;border-bottom:1px solid var(--edge);padding:12px 0}.settings-link span{overflow-wrap:anywhere}.settings-link svg{flex:none;color:var(--quiet)}.settings-link:hover{color:var(--accent)}.settings-assistant{min-height:48px;margin:32px 0 24px;max-width:420px}.settings-lock{display:flex;gap:20px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--edge);padding-top:24px}.settings-lock button{display:flex;align-items:center;gap:8px}
@media(max-width:900px){.settings-sections{grid-template-columns:1fr 1fr}}@media(max-width:600px){.settings-sections{grid-template-columns:1fr;gap:28px}.settings-search{margin-bottom:28px}}
</style>
