<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {Pencil,Plus,Trash2,UserRound} from 'lucide-vue-next'
import {api,newKey} from '../api'
import {createPersona,deletePersona,listPersonas,updatePersona,type Persona} from './api'
import {useRequestScope} from '../platform/request-scope'

const props=defineProps<{modelValue?:string|null;sessionId?:string;version?:number;compact?:boolean}>()
const emit=defineEmits<{(event:'update:modelValue',value:string|null):void;(event:'updated',chat:unknown):void}>()
const capture=useRequestScope()
const personas=ref<Persona[]>([]),loading=ref(true),error=ref(''),notice=ref('')
const editing=ref<''|'new'|string>(''),form=ref({name:'',description:''}),saving=ref(false),deletingId=ref(''),bindBusy=ref(false)
let disposed=false
const current=computed(()=>personas.value.find(item=>item.id===props.modelValue)||null)
const selected=computed(()=>props.modelValue||'')
async function load(){
  const scope=capture()
  loading.value=true;error.value=''
  try{
    const items=await listPersonas()
    if(!scope.valid()||disposed)return
    personas.value=items
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message||'人设暂时无法读取。'
  }finally{
    if(scope.valid()&&!disposed)loading.value=false
  }
}
async function choose(event:Event){
  const value=(event.target as HTMLSelectElement).value||null
  if(!props.sessionId){emit('update:modelValue',value);return}
  if(bindBusy.value)return
  const scope=capture()
  bindBusy.value=true;error.value='';notice.value=''
  try{
    const chat=await api<unknown>('/play-controls/world-chats/'+encodeURIComponent(props.sessionId),{method:'PATCH',key:newKey(),body:{personaId:value,...(props.version!==undefined?{expectedVersion:props.version}:{})}})
    if(!scope.valid()||disposed)return
    emit('update:modelValue',value)
    emit('updated',chat)
    notice.value=value?'已切换到「'+(personas.value.find(item=>item.id===value)?.name||'人设')+'」，下一轮生效。':'已关闭人设注入。'
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message
  }finally{
    if(scope.valid()&&!disposed)bindBusy.value=false
  }
}
function startNew(){editing.value='new';form.value={name:'',description:''};error.value='';notice.value=''}
function startEdit(persona:Persona){editing.value=persona.id;form.value={name:persona.name,description:persona.description};error.value='';notice.value=''}
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
      emit('update:modelValue',saved.id)
      notice.value='人设已创建并选中。'
      if(props.sessionId)await bindPersona(saved.id,scope)
    }else notice.value='人设已保存。'
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message
  }finally{
    if(scope.valid()&&!disposed)saving.value=false
  }
}
async function bindPersona(personaId:string|null,scope:{valid:()=>boolean}){
  // The select path already handles binding; this runs after creating a persona.
  if(!props.sessionId)return
  try{
    const chat=await api<unknown>('/play-controls/world-chats/'+encodeURIComponent(props.sessionId),{method:'PATCH',key:newKey(),body:{personaId,...(props.version!==undefined?{expectedVersion:props.version}:{})}})
    if(scope.valid()&&!disposed)emit('updated',chat)
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message
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
    if(props.modelValue===persona.id)emit('update:modelValue',null)
    await load()
    if(scope.valid()&&!disposed)notice.value='人设已删除。'
  }catch(cause){
    if(scope.valid()&&!disposed)error.value=(cause as Error).message
  }
}
onMounted(()=>{void load()})
</script>
<template>
  <section :class="['persona-control',{compact}]" aria-label="我是谁 · 人设">
    <div class="persona-head">
      <strong><UserRound :size="16"/>我是谁</strong>
      <small>会作为「你的对话对象」注入下一轮对话</small>
    </div>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <p v-if="notice" class="product-hint" role="status">{{notice}}</p>
    <p v-if="loading" role="status" class="product-hint">正在读取人设…</p>
    <template v-else>
      <div class="persona-row">
        <label>当前人设<select :value="selected" :disabled="bindBusy" aria-label="选择当前人设" @change="choose"><option value="">不使用人设</option><option v-for="item in personas" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
        <button type="button" :disabled="!!editing" @click="startNew"><Plus :size="15"/>新建</button>
        <button v-if="current" type="button" :disabled="!!editing" @click="startEdit(current)"><Pencil :size="15"/>编辑</button>
        <button v-if="current" type="button" class="danger" :disabled="!!editing" @click="removePersona(current)"><Trash2 :size="15"/>{{deletingId===current.id?'确认删除':'删除'}}</button>
      </div>
      <p v-if="current?.description" class="persona-description">{{current.description}}</p>
      <p v-if="!personas.length&&!editing" class="product-hint">还没有人设。新建一个「我是谁」，聊天时对方就会知道如何称呼你。</p>
      <form v-if="editing" class="persona-editor" @submit.prevent="savePersona">
        <label>{{editing==='new'?'新人设名称':'人设名称'}}<input v-model="form.name" required maxlength="80" placeholder="例如：旅人"></label>
        <label>简介<textarea v-model="form.description" rows="3" maxlength="800" placeholder="例如：一位在雨夜赶路的旅人，话不多。"></textarea></label>
        <div class="actions">
          <button class="primary" :disabled="saving">{{saving?'正在保存…':'保存人设'}}</button>
          <button type="button" :disabled="saving" @click="cancelEdit">取消</button>
        </div>
      </form>
    </template>
  </section>
</template>
<style scoped>
.persona-control{display:grid;gap:10px;padding:16px;border:1px solid var(--edge);border-radius:14px;background:var(--paper)}
.persona-control.compact{padding:12px 14px}
.persona-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.persona-head small{color:var(--quiet);font-size:12px}
.persona-row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}
.persona-row label{flex:1;min-width:200px;margin:0}
.persona-row button{display:inline-flex;align-items:center;gap:6px;min-height:40px;padding:4px 12px;font-size:13px}
.persona-description{margin:0;color:var(--quiet);font-size:13px;line-height:1.7;overflow-wrap:anywhere}
.persona-editor{display:grid;gap:8px}
.persona-editor label{margin:0}
@media(max-width:767px){.persona-row{display:grid;grid-template-columns:1fr 1fr}.persona-row label{grid-column:1/-1}}
</style>
