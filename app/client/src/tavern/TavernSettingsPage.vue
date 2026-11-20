<script setup lang="ts">
import {computed,onMounted,reactive,ref} from 'vue'
import {BookMarked,Brush,Download,HelpCircle,UserRound} from 'lucide-vue-next'
import PersonaControl from '../characters/PersonaControl.vue'
import {listPersonas,type Persona} from '../characters/api'
import TavernPresetsPanel from './TavernPresetsPanel.vue'
import LoadState from './LoadState.vue'
import {listPresets,updatePreset,type TavernPreset} from './api'
import {useApp} from '../stores/app'

const DEFAULT_PERSONA_KEY='tavern:default-persona'
const personaId=ref<string|null>(null)
const personas=ref<Persona[]>([])
const personaError=ref('')
const personasLoading=ref(true)
const defaultPersonaId=ref<string>('')
const defaultPersonaName=computed(()=>personas.value.find(item=>item.id===defaultPersonaId.value)?.name||'')

const store=useApp()
const presets=ref<TavernPreset[]>([])
const presetsError=ref('')
const presetsLoading=ref(true)
const samplingSaving=ref(false)
const samplingNotice=ref('')
const samplingError=ref('')
/** 当前采样参数来自默认预设；没有默认预设时退回内置默认值，仅可查看。 */
const defaultPreset=computed<TavernPreset|null>(()=>presets.value.find(item=>item.isDefault)||presets.value[0]||null)
const sampling=reactive({temperature:0.8,topP:0.95,maxTokens:1024})
const samplingJson=computed(()=>JSON.stringify({
  temperature:sampling.temperature,topP:sampling.topP,maxTokens:sampling.maxTokens,
},null,2))

function applySampling(preset:TavernPreset|null){
  sampling.temperature=preset?.temperature??0.8
  sampling.topP=preset?.topP??0.95
  sampling.maxTokens=preset?.maxTokens??1024
}

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

async function loadPresets(){
  presetsLoading.value=true;presetsError.value=''
  try{
    presets.value=await listPresets()
    applySampling(defaultPreset.value)
  }catch(e){
    presetsError.value=(e as Error).message||'预设暂时无法读取。'
  }finally{presetsLoading.value=false}
}

function chooseDefault(event:Event){
  const value=(event.target as HTMLSelectElement).value
  defaultPersonaId.value=value
  if(value)window.localStorage.setItem(DEFAULT_PERSONA_KEY,value)
  else window.localStorage.removeItem(DEFAULT_PERSONA_KEY)
}

async function saveSampling(){
  samplingError.value='';samplingNotice.value=''
  const target=defaultPreset.value
  if(!target){samplingError.value='还没有预设；先在「预设」组新建一个，再保存采样参数。';return}
  samplingSaving.value=true
  try{
    const saved=await updatePreset(target.id,{
      temperature:sampling.temperature,topP:sampling.topP,maxTokens:sampling.maxTokens,
    })
    const index=presets.value.findIndex(item=>item.id===target.id)
    if(index>=0)presets.value[index]=saved
    applySampling(saved)
    samplingNotice.value='采样参数已保存。'
    store.message('采样参数已保存。')
  }catch(e){
    samplingError.value=(e as Error).message||'采样参数保存失败，请稍后重试。'
  }finally{samplingSaving.value=false}
}

onMounted(()=>{
  try{defaultPersonaId.value=window.localStorage.getItem(DEFAULT_PERSONA_KEY)||''}catch{defaultPersonaId.value=''}
  void loadPersonas()
  void loadPresets()
})
</script>

<template>
 <section class="tavern-settings" aria-label="设置">
  <p class="muted">酒馆 2.0 的全局设置。所有数据都保存在你自己的账号下；采样参数写入默认预设，只影响之后的新对话。</p>

  <section class="settings-group" aria-label="默认对话">
   <h2 class="group-title">默认对话</h2>
   <div class="settings-card">
    <LoadState v-if="personasLoading||personaError" :loading="personasLoading" :error="personaError" loading-text="正在读取人设…" @retry="loadPersonas"/>
    <template v-else>
     <PersonaControl v-model="personaId"/>
     <p class="section-hint">人设会在对话里作为「你的身份」注入；在这里创建和管理多个人设。</p>
     <label class="default-persona">默认人设
      <select :value="defaultPersonaId" aria-label="默认人设" @change="chooseDefault">
       <option value="">不设置</option>
       <option v-for="persona in personas" :key="persona.id" :value="persona.id">{{persona.name}}</option>
      </select>
      <small class="default-hint">默认人设会作为新聊天的初始选择，聊天里仍可随时切换。</small>
     </label>
    </template>
   </div>
  </section>

  <section class="settings-group" aria-label="采样参数">
   <h2 class="group-title">采样参数</h2>
   <div class="settings-card">
    <LoadState v-if="presetsLoading" :loading="true" loading-text="正在读取采样参数…"/>
    <p v-else-if="presetsError" class="error-text">{{presetsError}}</p>
    <template v-else>
     <div class="sampling-grid">
      <label>温度
       <input v-model.number="sampling.temperature" type="number" min="0" max="2" step="0.05" aria-label="温度">
       <small class="default-hint">默认 0.80 · 越高越天马行空</small>
      </label>
      <label>核采样
       <input v-model.number="sampling.topP" type="number" min="0" max="1" step="0.01" aria-label="核采样">
       <small class="default-hint">默认 0.95 · 越低越保守</small>
      </label>
      <label>最大长度
       <input v-model.number="sampling.maxTokens" type="number" min="16" max="8192" step="1" aria-label="最大长度">
       <small class="default-hint">默认 1024 · 单条回复的 token 上限</small>
      </label>
      <label>重复惩罚
       <input :value="1" type="number" disabled aria-label="重复惩罚">
       <small class="default-hint">默认 1.00 · 当前生成通道暂不支持</small>
      </label>
     </div>
     <details class="sampling-advanced">
      <summary>高级</summary>
      <pre>{{samplingJson}}</pre>
      <p class="section-hint">原始 JSON 只读，用来核对实际提交到网关的字段。</p>
     </details>
     <p v-if="samplingNotice" class="notice-text">{{samplingNotice}}</p>
     <p v-if="samplingError" class="error-text">{{samplingError}}</p>
     <div class="group-actions">
      <button type="button" class="primary save-sampling" :disabled="samplingSaving" @click="saveSampling">
       {{samplingSaving?'保存中…':'保存采样参数'}}
      </button>
      <small class="default-hint">写入默认预设，保存后不会跳转。</small>
     </div>
    </template>
   </div>
  </section>

  <section class="settings-group" aria-label="预设">
   <h2 class="group-title">预设</h2>
   <div class="settings-card">
    <TavernPresetsPanel/>
   </div>
  </section>

  <section class="settings-group" aria-label="本地数据">
   <h2 class="group-title">本地数据</h2>
   <div class="settings-card">
    <ul class="import-list">
     <li><RouterLink to="/" class="settings-link">角色卡库</RouterLink>：导入 SillyTavern PNG/JSON 角色卡。</li>
     <li>角色详情：导出角色卡 PNG、导入酒馆聊天 JSONL。</li>
     <li>聊天页：菜单里导出当前聊天（JSONL / JSON）。</li>
    </ul>
    <div class="data-links">
     <RouterLink to="/settings/regex" class="settings-link"><Brush :size="16"/>输出替换规则</RouterLink>
     <RouterLink to="/settings/help" class="settings-link"><HelpCircle :size="16"/>使用帮助</RouterLink>
    </div>
   </div>
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
.settings-group{display:flex;flex-direction:column;gap:10px}
.group-title{font-size:13px;font-weight:600;letter-spacing:.02em;color:var(--accent-ink);margin:0}
.settings-card{display:flex;flex-direction:column;gap:12px;background:var(--canvas);border:1px solid var(--edge);border-radius:var(--r-card);padding:16px}
.section-hint{color:var(--quiet);font-size:13px;line-height:1.6;margin:0}
.default-hint{color:var(--quiet);font-size:12px;line-height:1.5}
.default-persona{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--quiet);max-width:360px}
.sampling-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.sampling-grid label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--quiet)}
.sampling-grid input{min-height:44px;padding:0 12px;font-size:16px}
.sampling-grid input:disabled{opacity:.6}
.sampling-advanced{border-top:1px dashed var(--edge);padding-top:10px;font-size:13px;color:var(--quiet)}
.sampling-advanced summary{cursor:pointer;min-height:32px;display:flex;align-items:center}
.sampling-advanced pre{margin:8px 0 0;white-space:pre-wrap;font-size:12px;background:var(--wash);border-radius:var(--r-control);padding:10px}
.group-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.save-sampling{min-height:44px;padding:0 20px;border:1px solid var(--accent);border-radius:var(--r-control);background:var(--accent);color:var(--on-accent);font-size:14px;cursor:pointer}
.notice-text{color:var(--accent-ink);font-size:13px;margin:0}
.error-text{color:var(--danger);font-size:13px;margin:0}
.import-list{margin:0;padding-left:18px;color:var(--quiet);font-size:14px;line-height:1.9}
.data-links{display:flex;flex-direction:column;gap:8px}
.settings-link{display:inline-flex;align-items:center;gap:6px;min-height:44px;color:var(--accent-ink);text-decoration:underline}
.settings-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.settings-back{display:inline-flex;min-height:44px;align-items:center;color:var(--accent-ink)}
.settings-footer small{color:var(--quiet)}
@media(max-width:767px){
 .tavern-settings{padding-bottom:96px;gap:22px}
 .settings-grid,.sampling-grid{grid-template-columns:1fr}
 .default-persona{max-width:none;width:100%}
 .default-persona select,.sampling-grid input{width:100%}
 .save-sampling{width:100%}
 .settings-footer{position:sticky;bottom:0;background:var(--canvas);border-top:1px solid var(--edge);padding:10px 0 calc(6px + env(safe-area-inset-bottom));margin:0 -14px;padding-left:14px;padding-right:14px}
 .settings-back{flex:1;justify-content:center;min-height:44px;border:1px solid var(--accent);border-radius:10px;background:var(--accent);color:#fff;text-decoration:none}
}
</style>
