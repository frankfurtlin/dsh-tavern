<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {ArrowLeft,Plus,Save,Trash2} from 'lucide-vue-next'
import {useApp} from '../stores/app'
import {
  createRegexScript,deleteRegexScript,listRegexScripts,updateRegexScript,
  type RegexScript,
} from '../characters/api'

const store=useApp()
const rules=ref<RegexScript[]>([])
const loading=ref(true)
const loadError=ref('')
const formError=ref('')
const saving=false
const busy=ref(false)
const editingId=ref('')
const deletingId=ref('')
let disposed=false

const blank=()=>({name:'',pattern:'',replacement:'',enabled:true,order:0})
const form=ref(blank())

function sortRules(){rules.value=[...rules.value].sort((a,b)=>(a.order??0)-(b.order??0)||a.name.localeCompare(b.name,'zh-CN'))}
function startCreate(){editingId.value='';form.value=blank();formError.value=''}
function startEdit(rule:RegexScript){editingId.value=rule.id;form.value={name:rule.name,pattern:rule.pattern,replacement:rule.replacement,enabled:rule.enabled,order:rule.order??0};formError.value=''}
function cancelEdit(){editingId.value='';form.value=blank();formError.value=''}

function validate():string{
  if(!form.value.name.trim())return '请填写规则名称。'
  if(!form.value.pattern)return '请填写查找正则。'
  if(form.value.pattern.length>500)return '查找正则不能超过 500 个字符。'
  if(form.value.replacement.length>2000)return '替换内容不能超过 2000 个字符。'
  try{new RegExp(form.value.pattern)}catch(e){return '正则表达式无效：'+(e as Error).message}
  return ''
}

async function load(){
  loading.value=true;loadError.value=''
  try{
    rules.value=await listRegexScripts();sortRules()
  }catch(e){loadError.value=(e as Error).message||'替换规则加载失败，请稍后重试。'}
  finally{loading.value=false}
}

async function save(){
  const problem=validate()
  if(problem){formError.value=problem;return}
  if(busy.value)return
  busy.value=true;formError.value=''
  const body={name:form.value.name.trim(),pattern:form.value.pattern,replacement:form.value.replacement,enabled:form.value.enabled,order:form.value.order}
  try{
    if(editingId.value)await updateRegexScript(editingId.value,body)
    else await createRegexScript(body)
    cancelEdit();await load();store.message('替换规则已保存。')
  }catch(e){formError.value=(e as Error).message||'保存替换规则失败，请稍后重试。'}
  finally{busy.value=false}
}

async function toggle(rule:RegexScript){
  if(busy.value)return
  try{await updateRegexScript(rule.id,{enabled:!rule.enabled});await load()}
  catch(e){loadError.value=(e as Error).message||'切换失败，请稍后重试。'}
}

async function remove(rule:RegexScript){
  if(deletingId.value!==rule.id){deletingId.value=rule.id;return}
  try{await deleteRegexScript(rule.id);deletingId.value='';await load();store.message('替换规则已删除。')}
  catch(e){loadError.value=(e as Error).message||'删除失败，请稍后重试。'}
}

onMounted(()=>void load())
onUnmounted(()=>{disposed=true})
</script>

<template>
 <section class="tavern-regex" aria-label="输出替换规则">
  <header class="regex-head">
   <RouterLink to="/settings" class="regex-back"><ArrowLeft :size="18"/>返回设置</RouterLink>
   <h1>输出替换规则</h1>
  </header>
  <p class="regex-hint">回复保存前按正则改写文字；替换在服务端执行，流式气泡显示的是替换前文本。</p>
  <p v-if="loadError" class="error" role="alert">{{loadError}}</p>
  <p v-if="loading" class="regex-muted" role="status">正在读取规则…</p>
  <template v-else>
   <ul v-if="rules.length" class="regex-list">
    <li v-for="rule in rules" :key="rule.id" class="regex-item">
     <div class="regex-copy">
      <p class="regex-name">{{rule.name}}<span class="badge">{{rule.enabled?'已启用':'已停用'}}</span></p>
      <p class="regex-pattern"><code>{{rule.pattern}}</code> → <code>{{rule.replacement}}</code></p>
     </div>
     <div class="regex-actions">
      <button type="button" :aria-label="'编辑规则 '+rule.name" @click="startEdit(rule)">编辑</button>
      <button type="button" :aria-label="rule.enabled?'停用':'启用'" @click="toggle(rule)">{{rule.enabled?'停用':'启用'}}</button>
      <template v-if="deletingId===rule.id">
       <button type="button" class="danger-text" aria-label="确认删除规则" @click="remove(rule)">确认删除</button>
       <button type="button" @click="deletingId=''">取消</button>
      </template>
      <button v-else type="button" class="danger" aria-label="删除规则" @click="remove(rule)"><Trash2 :size="14"/>删除</button>
     </div>
    </li>
   </ul>
   <p v-else class="regex-muted">还没有替换规则；新增第一条规则。</p>

   <form class="regex-form" @submit.prevent="save">
    <h2>{{editingId?'编辑规则':'新增规则'}}</h2>
    <p v-if="formError" class="error" role="alert">{{formError}}</p>
    <label>名称<input v-model="form.name" aria-label="规则名称" maxlength="80"></label>
    <label>查找正则<input v-model="form.pattern" aria-label="查找正则" maxlength="500" spellcheck="false"></label>
    <label>替换为<textarea v-model="form.replacement" rows="2" aria-label="替换内容" maxlength="2000"></textarea></label>
    <div class="form-row">
     <label>顺序<input v-model.number="form.order" type="number" aria-label="规则顺序"></label>
     <label class="check"><input v-model="form.enabled" type="checkbox">启用</label>
    </div>
    <div class="form-actions">
     <button type="submit" class="primary" :disabled="busy"><Save :size="16"/>{{busy?'保存中…':'保存规则'}}</button>
     <button v-if="editingId" type="button" @click="cancelEdit">取消</button>
     <button v-else type="button" @click="startCreate"><Plus :size="16"/>清空</button>
    </div>
   </form>
  </template>
 </section>
</template>

<style scoped>
.tavern-regex{width:100%;max-width:760px;margin:0 auto;padding:24px clamp(14px,3vw,28px) 72px;display:flex;flex-direction:column;gap:14px}
.regex-head{display:flex;flex-direction:column;gap:8px}
.regex-back{display:inline-flex;align-items:center;gap:6px;color:var(--accent);min-height:40px}
h1{font-family:var(--font-serif);font-size:26px;margin:0}
.regex-hint{color:var(--quiet);font-size:13px;line-height:1.7;margin:0}
.regex-muted{color:var(--quiet);font-size:14px}
.regex-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.regex-item{display:flex;gap:12px;justify-content:space-between;border:1px solid var(--edge);border-radius:12px;padding:12px}
.regex-name{margin:0 0 4px;display:flex;gap:8px;align-items:center;font-size:15px}
.regex-pattern{margin:0;font-size:12px;color:var(--quiet);overflow-wrap:anywhere}
code{background:var(--wash);padding:1px 4px;border-radius:4px}
.regex-actions{display:flex;gap:6px;flex-wrap:wrap;flex:none}
.regex-actions button{min-height:36px;font-size:13px}
.regex-form{display:flex;flex-direction:column;gap:10px;border:1px dashed var(--edge);border-radius:12px;padding:14px;margin-top:8px}
.regex-form h2{margin:0;font-size:15px;font-family:var(--font-serif)}
.regex-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--quiet)}
.form-row{display:flex;gap:12px;align-items:flex-end}
.form-actions{display:flex;gap:8px}
.check{flex-direction:row!important;align-items:center;gap:8px!important}
.danger-text{color:#b42318}
@media(max-width:620px){.regex-item{flex-direction:column;align-items:stretch}}
</style>
