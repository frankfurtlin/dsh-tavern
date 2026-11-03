<script setup lang="ts">
import {ref,computed,onUnmounted} from 'vue'
import Sheet from '../components/Sheet.vue'
import {useLocal} from '../stores/local'
import {canonical,type Document} from '../domain/local-contract'
import type {CharacterCard} from './card-format'
import {updateCardCollection,type CardCollectionPatch} from './card-operations'
const props=defineProps<{rows:Document<CharacterCard>[]}>(),emit=defineEmits<{close:[];changed:[]}>(),store=useLocal(),adding=ref(''),removing=ref(''),groupMode=ref('keep'),groupName=ref(''),archiveMode=ref('keep'),busy=ref(false),error=ref(''),controller=new AbortController()
let disposed=false
const actionable=computed(()=>!!adding.value.trim()||!!removing.value.trim()||groupMode.value!=='keep'||archiveMode.value!=='keep')
const labels=(v:string)=>v.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean)
async function save(){const vault=store.vault;if(!vault||busy.value||!actionable.value)return;const selected=props.rows.map(row=>({...row})),scope=canonical(selected.map(row=>[row.id,row.revision])),patch:CardCollectionPatch={addTags:labels(adding.value),removeTags:labels(removing.value),...(groupMode.value==='keep'?{}:{group:{mode:groupMode.value as 'add'|'remove'|'clear',name:groupName.value}}),...(archiveMode.value==='keep'?{}:{archived:archiveMode.value==='archive'})};busy.value=true;error.value='';try{await updateCardCollection(vault,selected,patch,crypto.randomUUID(),controller.signal);if(disposed||controller.signal.aborted||store.vault!==vault||!vault.unlocked||scope!==canonical(props.rows.map(row=>[row.id,row.revision])))return;emit('changed');emit('close')}catch(e){if(!disposed&&store.vault===vault)error.value=(e as Error).message}finally{busy.value=false}}
function close(){disposed=true;controller.abort();emit('close')}
onUnmounted(()=>{disposed=true;controller.abort()})
</script>
<template><Sheet title="整理所选角色" @close="close"><div class="card-collection-panel"><p class="final-subtle">{{rows.length}} 张角色卡</p><div class="collection-names"><span v-for="row in rows" :key="row.id">{{row.value.name}}</span></div><p v-if="error" class="final-error" role="alert">{{error}}</p><form class="final-card-form" @submit.prevent="save"><fieldset :disabled="busy"><label>添加标签<input v-model="adding" maxlength="8000" placeholder="用逗号分开"></label><label>移除标签<input v-model="removing" maxlength="8000"></label><label>分组操作<select v-model="groupMode"><option value="keep">保留分组</option><option value="add">加入分组</option><option value="remove">移出分组</option><option value="clear">清空分组</option></select></label><label v-if="['add','remove'].includes(groupMode)">分组名称<input v-model="groupName" maxlength="120" required></label><label>显示状态<select v-model="archiveMode"><option value="keep">保持当前状态</option><option value="archive">归档</option><option value="restore">恢复到角色库</option></select></label><button class="final-primary" :disabled="!actionable">{{busy?'正在保存…':'保存修改'}}</button></fieldset></form></div></Sheet></template>
<style scoped>.card-collection-panel{display:grid;gap:18px}.collection-names{display:flex;flex-wrap:wrap;gap:8px}.collection-names span{padding:5px 10px;font-size:12px;border:1px solid var(--edge);border-radius:7px;color:var(--quiet)}.card-collection-panel fieldset{display:grid;gap:18px;min-width:0;border:0;padding:0;margin:0}</style>
