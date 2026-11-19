<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {BookMarked,Brush,Download,HelpCircle,UserRound} from 'lucide-vue-next'
import PersonaControl from '../characters/PersonaControl.vue'
import {listPersonas,type Persona} from '../characters/api'
import TavernPresetsPanel from './TavernPresetsPanel.vue'
import LoadState from './LoadState.vue'

const DEFAULT_PERSONA_KEY='tavern:default-persona'
const personaId=ref<string|null>(null)
const personas=ref<Persona[]>([])
const personaError=ref('')
const personasLoading=ref(true)
const defaultPersonaId=ref<string>('')

const defaultPersonaName=computed(()=>personas.value.find(item=>item.id===defaultPersonaId.value)?.name||'')

async function loadPersonas(){
  personasLoading.value=true;personaError.value=''
  try{
    personas.value=await listPersonas()
    if(defaultPersonaId.value&&!personas.value.some(item=>item.id===defaultPersonaId.value)){
      defaultPersonaId.value=''
      window.localStorage.removeItem(DEFAULT_PERSONA_KEY)
    }
  }catch(e){
    personaError.value=(e as Error).message||'人设暂时无法读取。'
  }finally{personasLoading.value=false}
}

function chooseDefault(event:Event){
  const value=(event.target as HTMLSelectElement).value
  defaultPersonaId.value=value
  if(value)window.localStorage.setItem(DEFAULT_PERSONA_KEY,value)
  else window.localStorage.removeItem(DEFAULT_PERSONA_KEY)
}

onMounted(()=>{
  try{defaultPersonaId.value=window.localStorage.getItem(DEFAULT_PERSONA_KEY)||''}catch{defaultPersonaId.value=''}
  void loadPersonas()
})
</script>

<template>
 <section class="tavern-settings" aria-label="设置">
  <p class="muted">酒馆 2.0 的全局设置：你的身份、提示词预设与导入导出。所有数据都保存在你自己的账号下。</p>

  <section class="settings-section" aria-label="人设（我是谁）">
   <h2><UserRound :size="18"/>人设（我是谁）</h2>
   <LoadState v-if="personasLoading||personaError" :loading="personasLoading" :error="personaError" loading-text="正在读取人设…" @retry="loadPersonas"/>
   <template v-else>
    <PersonaControl v-model="personaId"/>
    <p class="section-hint">人设会在对话中以「你的对话对象」注入；在这里创建和管理多个人设。</p>
    <label class="default-persona">默认人设
     <select :value="defaultPersonaId" aria-label="默认人设" @change="chooseDefault">
      <option value="">不设置</option>
      <option v-for="persona in personas" :key="persona.id" :value="persona.id">{{persona.name}}</option>
     </select>
    </label>
    <p class="section-hint">默认人设会作为新聊天的初始选择（当前版本在聊天页仍可随时切换）。</p>
   </template>
  </section>

  <section class="settings-section" aria-label="预设">
   <h2><BookMarked :size="18"/>预设</h2>
   <TavernPresetsPanel/>
  </section>

  <section class="settings-section" aria-label="输出替换规则">
   <h2><Brush :size="18"/>输出替换规则</h2>
   <p class="section-hint">角色回复保存前按正则改写文字。规则按角色卡保存，可在对应角色卡里维护。</p>
   <RouterLink to="/settings/regex" class="settings-link">打开输出替换规则</RouterLink>
  </section>

  <section class="settings-section" aria-label="使用帮助">
   <h2><HelpCircle :size="18"/>使用帮助</h2>
   <p class="section-hint">新手引导、角色卡导入、导出备份、正则与世界书说明都在帮助页。</p>
   <RouterLink to="/settings/help" class="settings-link">打开使用帮助</RouterLink>
  </section>

  <section class="settings-section" aria-label="导入与导出">
   <h2><Download :size="18"/>导入与导出</h2>
   <ul class="import-list">
    <li><RouterLink to="/" class="settings-link">角色卡库</RouterLink>：导入 SillyTavern PNG/JSON 角色卡。</li>
    <li>角色详情：导出角色卡 PNG、导入酒馆聊天 JSONL。</li>
    <li>聊天页：菜单里导出当前聊天（JSONL / JSON）。</li>
   </ul>
  </section>

  <div class="settings-footer">
   <RouterLink to="/" class="settings-back">返回角色卡</RouterLink>
   <small v-if="defaultPersonaName">默认人设：{{defaultPersonaName}}</small>
  </div>
 </section>
</template>

<style scoped>
.tavern-settings{width:100%;max-width:760px;margin:0 auto;padding:24px clamp(16px,3vw,28px) 72px;display:flex;flex-direction:column;gap:24px}
.tavern-settings :focus-visible{outline:2px solid var(--accent-ink);outline-offset:2px}
.muted{color:var(--quiet);font-size:14px;line-height:1.7;margin:0}
.settings-section{display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--edge);padding-top:22px}
.settings-section h2{display:flex;align-items:center;gap:8px;font-size:17px;margin:0;font-family:var(--font-serif)}
.settings-section h2 svg{color:var(--accent-ink)}
.section-hint{color:var(--quiet);font-size:13px;line-height:1.6;margin:0}
.default-persona{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--quiet);max-width:320px}
.import-list{margin:0;padding-left:18px;color:var(--quiet);font-size:14px;line-height:1.9}
.settings-link{color:var(--accent-ink);text-decoration:underline}
.settings-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.settings-back{display:inline-flex;min-height:44px;align-items:center;color:var(--accent-ink)}
.settings-footer small{color:var(--quiet)}
@media(max-width:767px){
 .tavern-settings{padding-bottom:96px;gap:22px}
 .settings-section h2{font-size:16px}
 .default-persona{max-width:none;width:100%}
 .default-persona select,.settings-section input,.settings-section textarea,.settings-section select{width:100%}
 .settings-footer{position:sticky;bottom:0;background:var(--canvas);border-top:1px solid var(--edge);padding:10px 0 calc(6px + env(safe-area-inset-bottom));margin:0 -14px;padding-left:14px;padding-right:14px}
 .settings-back{flex:1;justify-content:center;min-height:44px;border:1px solid var(--accent);border-radius:10px;background:var(--accent);color:#fff;text-decoration:none}
}
</style>
