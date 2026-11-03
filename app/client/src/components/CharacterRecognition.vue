<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {api,ApiError,newKey} from '../api'
import type {Anchor} from '../api/types'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import Sheet from './Sheet.vue'
import type {RecognizedCard} from './character-recognition-types'
interface Correction {anchor:Anchor;expectedVersion:number;displayName:string;aliases:string[];evidenceIds:string[];identityStatus:'confirmed'|'uncertain';reason:string}
interface Intent {key:string;body:Correction}
const props=defineProps<{bookId:string;anchor:Anchor;card:RecognizedCard}>(),emit=defineEmits<{close:[];saved:[card:RecognizedCard]}>()
const store=useApp(),capture=useRequestScope(()=>[props.bookId,props.anchor,props.card.id]),recognition=ref(props.card.recognition)
const name=ref(props.card.name),aliases=ref(props.card.recognition?.aliases.filter(a=>a!==props.card.name).join('、')||'')
const selected=ref(props.card.recognition?.selectedEvidenceIds||props.card.recognition?.evidence.map(e=>e.id)||[])
const status=ref<'confirmed'|'uncertain'>(props.card.recognition?.identityStatus||'uncertain'),reason=ref(''),busy=ref(false),error=ref(''),conflict=ref(false)
const pending=ref<Intent|null>(null),confirmed=ref<RecognizedCard|null>(null)
const cacheKey=()=>`character-correction:${props.bookId}:${props.anchor.branchId}:${props.anchor.revisionId}:${props.anchor.blockId}:${props.anchor.start}:${props.anchor.end}:${props.card.id}`
function body():Correction{return{anchor:{...props.anchor},expectedVersion:recognition.value?.version||0,displayName:name.value.trim(),aliases:aliases.value.split(/[、,，\n]/).map(a=>a.trim()).filter(Boolean),evidenceIds:[...selected.value],identityStatus:status.value,reason:reason.value.trim()}}
async function saved(value:RecognizedCard,valid:()=>boolean){
  confirmed.value=value
  await cacheSet(cacheKey(),null);if(!valid())return
  pending.value=null;confirmed.value=null;emit('saved',value);emit('close')
}
async function save(){
  if(busy.value||conflict.value||!store.online||!recognition.value)return
  const scope=capture(),valid=()=>scope.valid();busy.value=true;error.value=''
  try{
    if(confirmed.value){await saved(confirmed.value,valid);return}
    if(!pending.value){const value=body();if(!value.displayName||!value.reason||!value.evidenceIds.length){error.value='请选择已读证据，并填写人物名称和纠正原因。';return}pending.value={key:newKey(),body:value}}
    const intent=pending.value;await cacheSet(cacheKey(),intent);if(!valid())return
    const value=await api<RecognizedCard>(`/books/${props.bookId}/character-cards/${props.card.id}/recognition`,{method:'PUT',body:intent.body,key:intent.key});if(!valid())return
    await saved(value,valid)
  }catch(e){if(valid()){
    if(confirmed.value)error.value='纠正已经保存，本机记录暂时未能清理。再次点击即可打开已保存的资料。'
    else if(e instanceof ApiError&&e.code==='CHARACTER_CORRECTION_CONFLICT'){conflict.value=true;pending.value=null;error.value='这张卡已在其他设备纠正。载入最新资料后，你填写的内容仍会保留。';await cacheSet(cacheKey(),null).catch(()=>{})}
    else if(e instanceof ApiError&&e.status===422){pending.value=null;error.value=e.message;await cacheSet(cacheKey(),null).catch(()=>{})}
    else error.value='保存结果暂时未确认。再次保存会使用同一请求，不会重复创建纠正。'
  }}finally{if(valid())busy.value=false}
}
async function refresh(){
  if(busy.value||!store.online)return
  const scope=capture(),valid=()=>scope.valid();busy.value=true
  try{const result=await api<{items:RecognizedCard[]}>(`/books/${props.bookId}/character-cards?branchId=${encodeURIComponent(props.anchor.branchId)}&anchor=${encodeURIComponent(JSON.stringify(props.anchor))}`);if(!valid())return
    const latest=result.items.find(c=>c.id===props.card.id);if(!latest?.recognition)throw new Error('not visible')
    recognition.value=latest.recognition;conflict.value=false;error.value='已载入最新资料，你填写的纠正尚未保存。'
  }catch{if(valid())error.value='未能载入最新资料，你填写的内容已保留。'}finally{if(valid())busy.value=false}
}
onMounted(async()=>{const scope=capture(),value=await cacheGet<Intent>(cacheKey()).catch(()=>null);if(!scope.valid()||!value)return
  if(!['bookId','branchId','revisionId','chapterId','blockId','start','end','quoteHash','offsetUnit'].every(key=>value.body.anchor[key as keyof Anchor]===props.anchor[key as keyof Anchor]))return
  pending.value=value;name.value=value.body.displayName;aliases.value=value.body.aliases.join('、');selected.value=value.body.evidenceIds;status.value=value.body.identityStatus;reason.value=value.body.reason
  error.value='上次保存结果尚未确认。再次保存会继续同一请求。'
})
</script>

<template>
 <Sheet :title="`${card.name}的识别资料`" @close="emit('close')">
  <div class="character-recognition">
   <p>这些证据来自当前阅读时间点。纠正只影响这条线、这一阅读位置的角色识别，已有对话和原文会保留。</p>
   <p v-if="recognition?.identityStatus==='uncertain'" class="notice" role="status">身份线索存在歧义，同名人物不会自动合并。</p>
   <h3>已读识别证据</h3>
   <fieldset :disabled="busy||!!pending||conflict||!store.online"><legend class="sr-only">选择支持纠正的已读原文</legend><label v-for="e in recognition?.evidence" :key="e.id" class="character-evidence"><input v-model="selected" type="checkbox" :value="e.id"><span>{{e.quote}}</span></label></fieldset>
   <p>当前别名：{{recognition?.aliases.join('、')||'尚未确认'}}</p>
   <h3>纠正这张角色卡</h3>
   <fieldset :disabled="busy||!!pending||conflict||!store.online">
    <label>人物名称<input v-model="name" maxlength="100"></label>
    <label>已读别名<input v-model="aliases" placeholder="用顿号分隔，只填写证据中出现的别名"></label>
    <label>身份判断<select v-model="status"><option value="confirmed">确认此卡所指人物</option><option value="uncertain">保留身份歧义</option></select></label>
    <label>纠正原因<textarea v-model="reason" maxlength="600" rows="3" placeholder="说明所选原文为什么支持这次纠正"></textarea></label>
   </fieldset>
   <p v-if="error" role="alert" class="error">{{error}}</p>
   <button v-if="conflict" class="secondary" :disabled="busy||!store.online" @click="refresh">载入最新资料，保留填写内容</button>
   <button class="primary" :disabled="busy||conflict||!store.online||!recognition" @click="save">{{busy?'正在保存…':confirmed?'查看已保存的纠正':pending?'再次保存（同一请求）':'保存本次纠正'}}</button>
   <section v-if="recognition?.history.length" aria-label="本时间点的纠正记录"><h3>纠正记录</h3><article v-for="item in recognition.history" :key="item.version"><strong>第{{item.version}}版 · {{item.displayName}}</strong><p>{{item.reason}}</p><small>{{item.identityStatus==='confirmed'?'确认此卡人物':'保留身份歧义'}} · {{new Date(item.createdAt).toLocaleString()}}</small></article></section>
  </div>
 </Sheet>
</template>

<style scoped>
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}
.character-recognition{display:grid;gap:16px;line-height:1.8}.character-recognition p{margin:0;color:var(--muted);overflow-wrap:anywhere}.character-recognition h3{margin:0;font-family:var(--serif);font-size:20px}.character-recognition fieldset{margin:0;padding:0;border:0;display:grid;gap:12px;min-width:0}.character-recognition label{display:grid;gap:6px}.character-recognition input:not([type=checkbox]),.character-recognition select,.character-recognition textarea{width:100%;min-height:44px}.character-recognition .character-evidence{display:flex;align-items:flex-start;gap:12px;min-height:44px;padding:12px;border:1px solid var(--line);border-radius:10px;cursor:pointer}.character-evidence input{width:44px;min-width:44px;max-width:44px;height:44px;min-height:44px;margin:0;padding:0;flex:0 0 44px;accent-color:var(--primary)}.character-evidence span{min-width:0;flex:1;overflow-wrap:anywhere}.character-recognition button{min-height:44px;min-width:44px}.character-recognition article{padding:12px 0;border-bottom:1px solid var(--line)}.character-recognition small{color:var(--muted)}
</style>
