<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {Pencil,Plus,Trash2} from 'lucide-vue-next'
import {createPersona,deletePersona,listPersonas,updatePersona,type Persona} from './api'
import {readPersonaId,writePersonaId} from './persona-store'
import {useRequestScope} from '../platform/request-scope'

/**
 * 设置页「我是谁」：一个下拉选当前人设 + 就地增删改，选中值由 persona-store 保存。
 * 不再区分「当前人设 / 默认人设」——聊天按消息读取同一个选择，语义只有一份。
 */
const capture=useRequestScope()
const personas=ref<Persona[]>([])
const loading=ref(true),error=ref(''),notice=ref('')
const selected=ref<string>(readPersonaId()||'')
const editing=ref<''|'new'|string>(''),form=ref({name:'',description:''})
const saving=ref(false),deletingId=ref('')
let disposed=false
const current=computed(()=>personas.value.find(item=>item.id===selected.value)||null)

async function load(){
  const scope=capture()
  loading.value=true;error.value=''
  try{
    const items=await listPersonas()
    if(!scope.valid()||disposed)return
    personas.value=items
    // 选中的人设可能已在别处删除：清掉本机记忆，避免一直发送一个不存在的 id。
    if(selected.value&&!items.some(item=>item.id===selected.value)){
      selected.value=''
      writePersonaId(null)
    }
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message||'人设暂时无法读取。'
  }finally{
    if(scope.valid()&&!disposed)loading.value=false
  }
}

function choose(event:Event){
  const value=(event.target as HTMLSelectElement).value||null
  selected.value=value||''
  writePersonaId(value)
  error.value=''
  notice.value=value?`已切换为「${personas.value.find(item=>item.id===value)?.name||'人设'}」，下一轮消息生效。`:'已关闭人设，之后按默认的「用户」称呼你。'
}

function startNew(){editing.value='new';form.value={name:'',description:''};error.value='';notice.value='';deletingId.value=''}
function startEdit(persona:Persona){editing.value=persona.id;form.value={name:persona.name,description:persona.description};error.value='';notice.value='';deletingId.value=''}
function cancelEdit(){editing.value='';form.value={name:'',description:''}}

async function savePersona(){
  if(saving.value)return
  if(!form.value.name.trim()){error.value='请填写人设名称。';return}
  const scope=capture(),target=editing.value
  saving.value=true;error.value='';notice.value=''
  const payload={name:form.value.name.trim().slice(0,80),description:form.value.description.slice(0,800)}
  try{
    const saved=target==='new'?await createPersona(payload):await updatePersona(target,payload)
    if(!scope.valid()||disposed)return
    await load()
    if(!scope.valid()||disposed)return
    editing.value=''
    if(target==='new'){
      selected.value=saved.id
      writePersonaId(saved.id)
      notice.value='人设已创建，并作为你的身份。'
    }else notice.value='人设已保存。'
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message
  }finally{
    if(scope.valid()&&!disposed)saving.value=false
  }
}

async function removePersona(persona:Persona){
  if(deletingId.value!==persona.id){deletingId.value=persona.id;return}
  const scope=capture()
  error.value='';notice.value=''
  try{
    await deletePersona(persona.id)
    if(!scope.valid()||disposed)return
    deletingId.value=''
    if(selected.value===persona.id){
      selected.value=''
      writePersonaId(null)
    }
    await load()
    if(scope.valid()&&!disposed)notice.value='人设已删除。'
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message
  }
}

onMounted(()=>{void load()})
</script>

<template>
 <div class="persona-block">
  <p v-if="error" class="persona-error" role="alert">{{error}}</p>
  <p v-if="notice" class="persona-notice" role="status">{{notice}}</p>
  <p v-if="loading" class="persona-hint" role="status">正在读取人设…</p>
  <template v-else>
   <div class="persona-row">
    <label class="persona-select">人设
     <select :value="selected" aria-label="我是谁的人设" @change="choose">
      <option value="">不使用人设</option>
      <option v-for="item in personas" :key="item.id" :value="item.id">{{item.name}}</option>
     </select>
    </label>
    <div class="persona-manage">
     <button type="button" :disabled="!!editing" @click="startNew"><Plus :size="15"/>新建</button>
     <button v-if="current" type="button" :disabled="!!editing" @click="startEdit(current)"><Pencil :size="15"/>编辑</button>
     <button v-if="current" type="button" class="persona-danger" :disabled="!!editing" @click="removePersona(current)">
      <Trash2 :size="15"/>{{deletingId===current.id?'确认删除':'删除'}}
     </button>
    </div>
   </div>
   <p class="persona-hint">会作为「你的角色」写进每一轮对话；不选时按默认的「用户」称呼你。</p>
   <p v-if="current?.description" class="persona-description">{{current.description}}</p>
   <p v-if="!personas.length&&!editing" class="persona-empty">还没有人设。新建一个，对方就会知道怎么称呼你。</p>
   <form v-if="editing" class="persona-editor" @submit.prevent="savePersona">
    <label>{{editing==='new'?'新名字':'名字'}}<input v-model="form.name" required maxlength="80" placeholder="例如：旅人"></label>
    <label>简介<textarea v-model="form.description" rows="3" maxlength="800" placeholder="例如：一位在雨夜赶路的旅人，话不多。"></textarea></label>
    <div class="persona-editor-actions">
     <button type="submit" class="primary" :disabled="saving">{{saving?'正在保存…':'保存'}}</button>
     <button type="button" :disabled="saving" @click="cancelEdit">取消</button>
    </div>
   </form>
  </template>
 </div>
</template>

<style scoped>
.persona-block{display:flex;flex-direction:column;gap:10px}
.persona-row{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap}
.persona-select{flex:1;min-width:220px;margin:0;display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--quiet)}
.persona-manage{display:flex;gap:8px;flex-wrap:wrap}
.persona-manage button{min-height:44px;padding:0 14px;display:inline-flex;align-items:center;gap:6px;font-size:13px;border:1px solid var(--edge);border-radius:10px;background:var(--canvas);color:var(--ink)}
.persona-manage button:hover:not(:disabled){border-color:var(--accent)}
.persona-danger{color:var(--quiet)}
.persona-danger:hover:not(:disabled){color:var(--danger)!important}
.persona-hint{margin:0;font-size:12px;line-height:1.6;color:var(--quiet)}
.persona-description{margin:0;font-size:13px;line-height:1.7;color:var(--quiet);overflow-wrap:anywhere}
.persona-empty{margin:0;font-size:13px;line-height:1.7;color:var(--quiet)}
.persona-error{margin:0;font-size:13px;color:var(--danger)}
.persona-notice{margin:0;font-size:13px;color:var(--accent-ink)}
.persona-editor{display:flex;flex-direction:column;gap:10px;margin-top:4px}
.persona-editor label{margin:0;display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--quiet)}
.persona-editor-actions{display:flex;gap:10px;flex-wrap:wrap}
.persona-editor-actions button{min-height:44px}
@media(max-width:767px){
 .persona-row{flex-direction:column;align-items:stretch}
 .persona-select{min-width:0}
 .persona-manage{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr))}
 .persona-manage button{width:100%;justify-content:center}
 .persona-editor input,.persona-editor textarea{width:100%}
}
</style>
