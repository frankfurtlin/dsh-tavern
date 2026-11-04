<script setup lang="ts">
import {ref,reactive,onMounted} from 'vue'
import {RouterLink} from 'vue-router'
import {ArrowDown,ArrowLeft,ArrowUp,Pencil,Plus,Replace,Trash2} from 'lucide-vue-next'
import PageHeader from '../workspace/PageHeader.vue'
import {useRequestScope} from '../platform/request-scope'
import {createRegexScript,deleteRegexScript,listRegexScripts,updateRegexScript,type RegexScript} from './api'

const capture=useRequestScope()
const rules=ref<RegexScript[]>([]),loading=ref(true),error=ref(''),notice=ref('')
const editing=ref(''),saving=ref(false),formError=ref(''),deleting=ref(''),busy=ref('')
const form=reactive({name:'',pattern:'',replacement:'',enabled:true,order:0})
let epoch=0

function sortRules(items:RegexScript[]){
  return [...items].sort((a,b)=>a.order-b.order||a.name.localeCompare(b.name,'zh-CN'))
}
async function load(){
  const scope=capture(),version=++epoch
  loading.value=true;error.value=''
  try{
    const items=await listRegexScripts()
    if(scope.valid()&&version===epoch)rules.value=sortRules(items)
  }catch(cause){
    if(scope.valid()&&version===epoch)error.value=(cause as Error).message
  }finally{
    if(scope.valid()&&version===epoch)loading.value=false
  }
}
function newRule(){
  editing.value='new'
  Object.assign(form,{name:'',pattern:'',replacement:'',enabled:true,order:(rules.value.at(-1)?.order||0)+10})
  formError.value='';deleting.value=''
}
function editRule(rule:RegexScript){
  editing.value=rule.id
  Object.assign(form,{name:rule.name,pattern:rule.pattern,replacement:rule.replacement,enabled:rule.enabled,order:rule.order})
  formError.value='';deleting.value=''
}
function cancel(){editing.value='';formError.value=''}
function validate():string{
  if(!form.name.trim())return '请填写规则名称。'
  if(!form.pattern)return '请填写匹配正则。'
  try{new RegExp(form.pattern)}catch(cause){return '正则表达式无效：'+(cause as Error).message}
  return ''
}
async function save(){
  if(saving.value)return
  const invalid=validate()
  if(invalid){formError.value=invalid;return}
  const scope=capture(),target=editing.value
  saving.value=true;formError.value=''
  const payload={name:form.name.trim(),pattern:form.pattern,replacement:form.replacement,enabled:form.enabled,order:Number(form.order)||0}
  try{
    if(target==='new')await createRegexScript(payload)
    else await updateRegexScript(target,payload)
    if(!scope.valid())return
    editing.value=''
    await load()
    if(scope.valid())notice.value='替换规则已保存，下一条回复开始生效。'
  }catch(cause){
    if(scope.valid())formError.value=(cause as Error).message
  }finally{
    if(scope.valid())saving.value=false
  }
}
async function toggle(rule:RegexScript){
  if(busy.value)return
  const scope=capture()
  busy.value=rule.id;error.value=''
  try{
    const updated=await updateRegexScript(rule.id,{enabled:!rule.enabled})
    if(!scope.valid())return
    const index=rules.value.findIndex(item=>item.id===rule.id)
    if(index>=0)rules.value[index]={...rules.value[index],...updated}
  }catch(cause){
    if(scope.valid())error.value=(cause as Error).message
  }finally{
    if(scope.valid())busy.value=''
  }
}
async function remove(rule:RegexScript){
  if(busy.value)return
  const scope=capture()
  busy.value=rule.id;error.value=''
  try{
    await deleteRegexScript(rule.id)
    if(!scope.valid())return
    deleting.value=''
    rules.value=rules.value.filter(item=>item.id!==rule.id)
    notice.value='替换规则已删除。'
  }catch(cause){
    if(scope.valid())error.value=(cause as Error).message
  }finally{
    if(scope.valid())busy.value=''
  }
}
async function move(rule:RegexScript,delta:number){
  if(busy.value)return
  const list=sortRules(rules.value),index=list.findIndex(item=>item.id===rule.id),targetIndex=index+delta
  if(index<0||targetIndex<0||targetIndex>=list.length)return
  const other=list[targetIndex]
  const scope=capture()
  busy.value=rule.id;error.value=''
  try{
    // Orders are rewritten for the two swapped rules so the new position is
    // stable even when both carried the default order 0.
    await updateRegexScript(rule.id,{order:(targetIndex+1)*10})
    await updateRegexScript(other.id,{order:(index+1)*10})
    if(!scope.valid())return
    await load()
  }catch(cause){
    if(scope.valid())error.value=(cause as Error).message
  }finally{
    if(scope.valid())busy.value=''
  }
}
onMounted(load)
</script>
<template>
  <div class="workspace-page regex-page">
    <PageHeader title="输出替换规则" eyebrow="角色卡扩展" description="在回复写入前按顺序做正则替换，例如把「。」换成「！」。规则对当前账号的所有角色卡聊天生效。">
      <RouterLink class="inline-link" to="/characters"><ArrowLeft :size="17"/>角色卡库</RouterLink>
      <button class="primary" :disabled="!!editing" @click="newRule"><Plus :size="17"/>新增规则</button>
    </PageHeader>
    <p v-if="error" class="error" role="alert">{{error}} <button @click="load">重新加载</button></p>
    <p v-if="notice" class="product-hint" role="status">{{notice}}</p>
    <section v-if="editing" class="workspace-card">
      <div class="workspace-section-title"><h2><Replace :size="18"/>{{editing==='new'?'新增替换规则':'编辑替换规则'}}</h2></div>
      <form @submit.prevent="save">
        <label>规则名称<input v-model="form.name" required maxlength="80" :disabled="saving" placeholder="例如：句号改感叹号"></label>
        <label>匹配正则<input v-model="form.pattern" required maxlength="500" :disabled="saving" placeholder="例如：。$" aria-describedby="regex-pattern-hint"></label>
        <small id="regex-pattern-hint">JavaScript 正则语法，不加首尾斜杠；可用 $1 引用分组。</small>
        <label>替换为<input v-model="form.replacement" maxlength="2000" :disabled="saving" placeholder="例如：！"></label>
        <div class="form-grid">
          <label>顺序<input v-model.number="form.order" type="number" step="10" :disabled="saving"></label>
          <label class="check-line"><input v-model="form.enabled" type="checkbox" :disabled="saving">启用这条规则</label>
        </div>
        <p v-if="formError" class="error" role="alert">{{formError}}</p>
        <div class="actions">
          <button class="primary" :disabled="saving">{{saving?'正在保存…':'保存规则'}}</button>
          <button type="button" :disabled="saving" @click="cancel">取消</button>
        </div>
      </form>
    </section>
    <p v-if="loading" class="workspace-loading" role="status">正在加载替换规则…</p>
    <section v-else-if="!rules.length&&!editing" class="workspace-empty">
      <Replace :size="42"/>
      <h2>还没有替换规则</h2>
      <p>新增一条规则后，角色说话的回复会在写入前按顺序做替换。例如把结尾的句号换成感叹号。</p>
      <button @click="newRule">新增第一条规则</button>
    </section>
    <ul v-else class="rule-list">
      <li v-for="rule in rules" :key="rule.id" class="workspace-card rule-item">
        <div class="row between">
          <strong>{{rule.name}} <span class="badge">{{rule.enabled?'已启用':'已停用'}}</span> <small>顺序 {{rule.order}}</small></strong>
          <span class="row rule-actions">
            <button :disabled="!!busy" aria-label="上移规则" @click="move(rule,-1)"><ArrowUp :size="15"/></button>
            <button :disabled="!!busy" aria-label="下移规则" @click="move(rule,1)"><ArrowDown :size="15"/></button>
            <button :disabled="!!busy" @click="toggle(rule)">{{rule.enabled?'停用':'启用'}}</button>
            <button :disabled="!!busy" @click="editRule(rule)"><Pencil :size="15"/>编辑</button>
            <template v-if="deleting===rule.id">
              <button class="danger" :disabled="busy===rule.id" @click="remove(rule)">确认删除</button>
              <button @click="deleting=''">取消</button>
            </template>
            <button v-else class="danger" aria-label="删除规则" @click="deleting=rule.id"><Trash2 :size="15"/></button>
          </span>
        </div>
        <p class="rule-body"><code>{{rule.pattern}}</code> <span aria-hidden="true">→</span> <code>{{rule.replacement||'（删除匹配内容）'}}</code></p>
      </li>
    </ul>
    <p class="workspace-footnote">替换在服务端保存回复时执行；流式气泡显示的是替换前文本，落库后刷新即为替换结果。</p>
  </div>
</template>
<style scoped>
.regex-page>section,.regex-page>p{margin-bottom:16px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 18px}
.check-line{display:flex;align-items:center;gap:10px}
.check-line input{width:20px;min-height:20px;margin:0}
.rule-list{list-style:none;margin:0;padding:0;display:grid;gap:14px}
.rule-item{padding:18px 20px}
.rule-item .row{gap:8px;flex-wrap:wrap}
.rule-item strong{display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap}
.rule-item strong small{color:var(--quiet);font-weight:400}
.rule-actions button{font-size:12px;min-height:36px;padding:6px 10px}
.rule-body{margin:10px 0 0;display:flex;flex-direction:column;gap:4px;font-size:13px;line-height:1.7;color:var(--quiet)}
.rule-body code{background:var(--wash);border:1px solid var(--edge);border-radius:6px;padding:2px 8px;font-size:12px;overflow-wrap:anywhere;color:var(--ink)}
@media(max-width:767px){.form-grid{grid-template-columns:1fr}.rule-item{padding:16px}.rule-actions button{min-height:44px}}
</style>
