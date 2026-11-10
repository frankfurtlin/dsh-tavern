<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {Plus,Save,Star,Trash2} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {
  createPreset,deletePreset,listPresets,setDefaultPreset,updatePreset,
  type TavernPreset,TavernPresetInput,
} from './api'

const store=useApp()
const presets=ref<TavernPreset[]>([])
const loading=ref(true)
const loadError=ref('')
const saving=ref(false)
const formError=ref('')
const deletingId=ref('')
const editingId=ref('')
let disposed=false

const blank=()=>({name:'',systemPrompt:'',mainPrompt:'',jailbreakPrompt:'',postHistoryPrompt:'',temperature:0.8,topP:0.95,maxTokens:1024,stop:'',isDefault:false})
const form=ref(blank())

function fill(value:TavernPreset){
  form.value={name:value.name,systemPrompt:value.systemPrompt||'',mainPrompt:value.mainPrompt||'',
    jailbreakPrompt:value.jailbreakPrompt||'',postHistoryPrompt:value.postHistoryPrompt||'',
    temperature:value.temperature??0.8,topP:value.topP??0.95,maxTokens:value.maxTokens??1024,
    stop:(value.stop||[]).join('\n'),isDefault:!!value.isDefault}
}
function startCreate(){editingId.value='';form.value=blank();form.value.name='新预设';formError.value=''}
function startEdit(value:TavernPreset){editingId.value=value.id;fill(value);formError.value=''}
function cancelEdit(){editingId.value='';form.value=blank();formError.value=''}

function validate():string{
  const value=form.value
  if(!value.name.trim())return '请填写预设名称。'
  if(value.name.trim().length>80)return '预设名称不能超过 80 个字。'
  if(!(value.temperature>=0&&value.temperature<=2))return 'temperature 需要在 0–2 之间。'
  if(!(value.topP>=0&&value.topP<=1))return 'top_p 需要在 0–1 之间。'
  if(!(Number.isInteger(value.maxTokens)&&value.maxTokens>=16&&value.maxTokens<=8192))return 'max_tokens 需要在 16–8192 之间。'
  const stop=value.stop.split(/\n/).map(item=>item.trim()).filter(Boolean)
  if(stop.length>4)return '结束词最多 4 个。'
  return ''
}

async function load(){
  loading.value=true;loadError.value=''
  try{
    presets.value=await listPresets()
  }catch(e){
    loadError.value=(e as Error).message||'预设暂时无法读取；服务端可能在后续版本开放。'
  }finally{loading.value=false}
}

async function save(){
  if(saving.value)return
  const problem=validate()
  if(problem){formError.value=problem;return}
  saving.value=true;formError.value=''
  const value=form.value
  const body:TavernPresetInput={
    name:value.name.trim(),systemPrompt:value.systemPrompt,mainPrompt:value.mainPrompt,
    jailbreakPrompt:value.jailbreakPrompt,postHistoryPrompt:value.postHistoryPrompt,
    temperature:value.temperature,topP:value.topP,maxTokens:value.maxTokens,
    stop:value.stop.split(/\n/).map(item=>item.trim()).filter(Boolean),
    isDefault:value.isDefault,
  }
  try{
    const saved=editingId.value?await updatePreset(editingId.value,body):await createPreset(body)
    if(value.isDefault)await setDefaultPreset(saved.id)
    cancelEdit()
    await load()
    store.message(value.isDefault?'预设已保存并设为默认。':'预设已保存。')
  }catch(e){
    formError.value=(e as Error).message||'保存预设失败，请稍后重试。'
  }finally{saving.value=false}
}

async function makeDefault(value:TavernPreset){
  try{
    await setDefaultPreset(value.id)
    await load()
    store.message('已设为默认预设。')
  }catch(e){loadError.value=(e as Error).message||'设置默认预设失败，请稍后重试。'}
}

async function remove(value:TavernPreset){
  if(deletingId.value!==value.id){deletingId.value=value.id;return}
  try{
    await deletePreset(value.id)
    deletingId.value=''
    if(editingId.value===value.id)cancelEdit()
    await load()
    store.message('预设已删除。')
  }catch(e){loadError.value=(e as Error).message||'删除预设失败，请稍后重试。'}
}

onMounted(()=>void load())
onUnmounted(()=>{disposed=true})
</script>

<template>
 <section class="presets-panel" aria-label="预设管理">
  <div class="panel-head">
   <h2>预设</h2>
   <button type="button" class="panel-add" :disabled="saving" @click="startCreate"><Plus :size="16"/>新建预设</button>
  </div>
  <p class="panel-hint">预设保存主提示词、越狱/后置提示词与采样参数；设为默认后用于新聊天。</p>
  <p v-if="loadError" class="error" role="alert">{{loadError}}</p>
  <p v-if="loading" class="panel-muted" role="status">正在读取预设…</p>
  <ul v-else-if="presets.length" class="preset-list">
   <li v-for="preset in presets" :key="preset.id" class="preset-item">
    <div class="preset-copy">
     <p class="preset-name">{{preset.name}}<span v-if="preset.isDefault" class="badge">默认</span></p>
     <p class="preset-meta">temperature {{preset.temperature??0.8}} · top_p {{preset.topP??0.95}} · max_tokens {{preset.maxTokens??1024}}</p>
    </div>
    <div class="preset-actions">
     <button type="button" :aria-label="'编辑预设 '+preset.name" @click="startEdit(preset)">编辑</button>
     <button v-if="!preset.isDefault" type="button" :aria-label="'设为默认 '+preset.name" @click="makeDefault(preset)"><Star :size="14"/>设为默认</button>
     <template v-if="deletingId===preset.id">
      <button type="button" class="danger-text" aria-label="确认删除预设" @click="remove(preset)">确认删除</button>
      <button type="button" @click="deletingId=''">取消</button>
     </template>
     <button v-else type="button" class="danger" aria-label="删除预设" @click="remove(preset)"><Trash2 :size="14"/>删除</button>
    </div>
   </li>
  </ul>
  <p v-else class="panel-muted">还没有预设；新建一个来统一角色扮演的提示词与采样参数。</p>

  <form class="preset-form" @submit.prevent="save">
   <h3>{{editingId?'编辑预设':'新建预设'}}</h3>
   <p v-if="formError" class="error" role="alert">{{formError}}</p>
   <label>名称<input v-model="form.name" aria-label="预设名称" maxlength="80"></label>
   <label>系统提示词<textarea v-model="form.systemPrompt" rows="3" aria-label="预设系统提示词" maxlength="20000"></textarea></label>
   <label>主提示词<textarea v-model="form.mainPrompt" rows="3" aria-label="预设主提示词" maxlength="20000"></textarea></label>
   <label>越狱提示词<textarea v-model="form.jailbreakPrompt" rows="2" aria-label="预设越狱提示词" maxlength="20000"></textarea></label>
   <label>后置提示词<textarea v-model="form.postHistoryPrompt" rows="2" aria-label="预设后置提示词" maxlength="20000"></textarea></label>
   <div class="form-row">
    <label>temperature<input v-model.number="form.temperature" type="number" min="0" max="2" step="0.05" aria-label="预设 temperature"></label>
    <label>top_p<input v-model.number="form.topP" type="number" min="0" max="1" step="0.01" aria-label="预设 top_p"></label>
    <label>max_tokens<input v-model.number="form.maxTokens" type="number" min="16" max="8192" aria-label="预设 max_tokens"></label>
   </div>
   <label>结束词（每行一个，最多 4 个）<textarea v-model="form.stop" rows="2" aria-label="预设结束词"></textarea></label>
   <label class="check"><input v-model="form.isDefault" type="checkbox">设为默认预设</label>
   <div class="form-actions">
    <button type="submit" class="primary" :disabled="saving"><Save :size="16"/>{{saving?'保存中…':'保存预设'}}</button>
    <button type="button" @click="cancelEdit">取消</button>
   </div>
  </form>
 </section>
</template>

<style scoped>
.presets-panel{display:flex;flex-direction:column;gap:10px}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.panel-head h2{font-size:17px;margin:0;font-family:var(--font-serif)}
.panel-add{display:inline-flex;align-items:center;gap:6px;min-height:44px}
.panel-hint{margin:0;color:var(--quiet);font-size:13px;line-height:1.6}
.panel-muted{color:var(--quiet);font-size:14px;margin:0}
.preset-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.preset-item{display:flex;gap:12px;justify-content:space-between;align-items:center;border:1px solid var(--edge);border-radius:12px;padding:12px}
.preset-name{margin:0 0 4px;font-size:15px;display:flex;gap:8px;align-items:center}
.preset-meta{margin:0;font-size:12px;color:var(--quiet)}
.preset-actions{display:flex;gap:6px;flex-wrap:wrap;flex:none}
.preset-actions button{display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:40px;padding:0 12px;border:1px solid var(--edge);border-radius:9px;background:var(--canvas);color:var(--quiet);font-size:13px;cursor:pointer}
.danger-text{color:#b42318}
.preset-form{display:flex;flex-direction:column;gap:10px;border:1px dashed var(--edge);border-radius:12px;padding:14px;margin-top:6px}
.preset-form h3{margin:0;font-size:15px;font-family:var(--font-serif)}
.preset-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--quiet)}
.form-row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.form-actions{display:flex;gap:8px}
.check{flex-direction:row!important;align-items:center;gap:8px!important}
@media(max-width:767px){
 .preset-item{flex-direction:column;align-items:stretch;gap:10px}
 .preset-actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}
 .preset-actions button{min-height:44px;width:100%}
 .preset-form input,.preset-form select,.preset-form textarea{width:100%}
 .preset-form .form-actions{position:sticky;bottom:calc(10px + env(safe-area-inset-bottom));background:var(--canvas);border-top:1px solid var(--edge);margin:2px -12px -6px;padding:8px 12px 4px}
 .preset-form .form-actions button{flex:1;min-height:44px}
}
@media(max-width:620px){.form-row{grid-template-columns:1fr}.preset-item{flex-direction:column;align-items:stretch}}
</style>
