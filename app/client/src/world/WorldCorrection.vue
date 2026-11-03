<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {worldApi} from './api'
import {newKey} from '../api'
import {useRequestScope} from '../platform/request-scope'
import type {WorldState,WorldLine} from './types'
const props=defineProps<{worldId:string;checkpointId:string;state:WorldState}>(),emit=defineEmits<{saved:[lineId:string]}>()
const entityId=ref(props.state.playerId),field=ref('description'),value=ref(''),reason=ref(''),itemId=ref(''),relatedId=ref(''),customKey=ref(''),valueType=ref('text'),newType=ref('character'),newName=ref(''),newDescription=ref(''),stackable=ref(false),busy=ref(false),error=ref(''),capture=useRequestScope()
const routeFrom=ref(props.state.location),routeTo=ref<string[]>([])
watch(routeFrom,()=>{routeTo.value=[...(props.state.rules.routes?.[routeFrom.value]||[])]},{immediate:true})
const limit=computed(()=>({name:100,traits:500,goal:500,relation:300,'rule:style':500,'rule:customRules':1500} as Record<string,number>)[field.value]||1000)
const entity=computed(()=>props.state.entities.find(e=>e.id===entityId.value)),items=computed(()=>props.state.entities.filter(e=>e.type==='item')),places=computed(()=>props.state.entities.filter(e=>e.type==='place')),characters=computed(()=>props.state.entities.filter(e=>e.type==='character'&&e.id!==entityId.value))
watch([field,entityId,itemId,relatedId],()=>{
  const e=entity.value,s=e?.state,f=field.value
  let current:unknown=f.startsWith('rule:')?props.state.rules[f.slice(5) as keyof typeof props.state.rules]:f==='inventory'?(entityId.value===props.state.playerId?s?.inventory[itemId.value]??0:undefined):f==='relation'?s?.relations[relatedId.value]:f.startsWith('field:')?s?.fields[f.slice(6)]:f==='custom'?undefined:f==='name'||f==='description'||f==='traits'?e?.[f]:s?.[f as 'alive'|'location'|'goal']
  value.value=current==null?'':typeof current==='object'?JSON.stringify(current,null,2):String(current)
  error.value=''
},{immediate:true})
async function save(){
  const scope=capture();busy.value=true;error.value=''
  try{
    let target=field.value,content:unknown=value.value
    if(target==='entity:add')content={type:newType.value,name:newName.value.trim(),description:newDescription.value.trim(),stackable:newType.value==='item'&&stackable.value}
    else if(target==='inventory'){if(!itemId.value||!value.value.trim()||!Number.isInteger(Number(value.value))||Number(value.value)<0||Number(value.value)>100000)throw Error('请选择物品，并填写 0–100000 的整数数量');target='inventory:'+itemId.value;content=Number(value.value)}
    else if(target==='relation'){if(!relatedId.value)throw Error('请选择关系中的另一位人物');target='relation:'+relatedId.value}
    else if(target==='custom'){if(!customKey.value.trim())throw Error('请填写属性名称');target='field:'+customKey.value.trim();content=valueType.value==='unknown'?null:valueType.value==='boolean'?value.value==='true':valueType.value==='number'?Number(value.value):value.value;if(valueType.value==='number'&&(!value.value.trim()||!Number.isFinite(content as number)))throw Error('请填写有效的数值')}
    else if(target==='rule:routes'){target='route:'+routeFrom.value;content=routeTo.value}
    else if(['alive','rule:relationshipNumbers','rule:randomChecks'].includes(target))content=value.value==='true'
    else if(['rule:actionCosts','rule:abilityRequirements','rule:routes'].includes(target))content=JSON.parse(value.value)
    else if(target.startsWith('field:')&&value.value.trim()&&Number.isFinite(Number(value.value)))content=Number(value.value)
    const result=await worldApi<WorldLine>('/worlds/'+props.worldId+'/corrections',{method:'POST',key:newKey(),body:{expectedCheckpointId:props.checkpointId,entityId:entityId.value,field:target,value:content,reason:reason.value.trim(),name:'调整后的故事'}})
    if(scope.valid())emit('saved',result.id)
  }catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}
}
</script>
<template>
<form class="world-correction-form" @submit.prevent="save">
  <label>调整内容<select aria-label="调整内容" v-model="field"><option value="description">公开资料</option><option value="name">名称</option><option value="traits">性格与外在特征</option><option value="goal">人物目标</option><option value="alive">生命状态</option><option value="location">所在地点</option><option value="inventory">物品数量</option><option value="relation">人物关系</option><option value="custom">自定义属性</option><option value="entity:add">补充人物、地点或物品</option><option value="field:money">钱财</option><option value="field:health">体力与健康</option><option value="field:ability">能力</option><option value="rule:genre">故事类型</option><option value="rule:pace">叙事节奏</option><option value="rule:deathPolicy">死亡规则</option><option value="rule:style">写作风格</option><option value="rule:customRules">补充世界规则</option><option value="rule:relationshipNumbers">数字关系</option><option value="rule:randomChecks">随机判定</option><option value="rule:actionCosts">行动代价</option><option value="rule:abilityRequirements">行动能力要求</option><option value="rule:routes">已知地点连接</option></select></label>
  <label v-if="!field.startsWith('rule:')&&field!=='entity:add'">人物或实体<select aria-label="人物或实体" v-model="entityId"><option v-for="e in state.entities" :key="e.id" :value="e.id">{{e.name}}</option></select></label>
  <template v-if="field==='entity:add'"><label>资料类型<select aria-label="资料类型" v-model="newType"><option value="character">人物</option><option value="place">地点</option><option value="item">物品</option><option value="faction">组织</option></select></label><label>名称<input v-model="newName" maxlength="100" required></label><label>公开资料<textarea v-model="newDescription" maxlength="1000"></textarea></label><label v-if="newType==='item'" class="world-checkbox-row"><input v-model="stackable" type="checkbox">可持有多件</label></template>
  <template v-else-if="field==='rule:routes'"><label>起点<select aria-label="起点" v-model="routeFrom"><option v-for="e in places" :key="e.id" :value="e.id">{{e.name}}</option></select></label><fieldset class="world-route-editor"><legend>从这里可以前往</legend><label v-for="e in places.filter(e=>e.id!==routeFrom)" :key="e.id"><input v-model="routeTo" type="checkbox" :value="e.id">{{e.name}}</label></fieldset></template>
  <template v-else>
    <label v-if="field==='inventory'">物品<select aria-label="物品" v-model="itemId" required><option disabled value="">选择已知物品</option><option v-for="e in items" :key="e.id" :value="e.id">{{e.name}}</option></select></label>
    <label v-if="field==='relation'">另一位人物<select aria-label="另一位人物" v-model="relatedId" required><option disabled value="">选择人物</option><option v-for="e in characters" :key="e.id" :value="e.id">{{e.name}}</option></select></label>
    <template v-if="field==='custom'"><label>属性名称<input v-model="customKey" maxlength="80" required placeholder="如：勇气"></label><label>属性类型<select aria-label="属性类型" v-model="valueType"><option value="text">文字</option><option value="number">数值</option><option value="boolean">是 / 否</option><option value="unknown">未知 / 清空</option></select></label></template>
    <label v-if="!(field==='custom'&&valueType==='unknown')">{{field==='inventory'?'新的数量':'新的内容'}}
      <select v-if="field==='rule:genre'" v-model="value" aria-label="新的内容" required><option v-for="[v,t] in [['general','跟随原作'],['mystery','悬疑'],['romance','情感'],['fantasy','奇幻'],['political','权谋'],['survival','生存']]" :key="v" :value="v">{{t}}</option></select>
      <select v-else-if="field==='rule:pace'" v-model="value" aria-label="新的内容" required><option value="short">简短</option><option value="standard">标准</option><option value="detailed">细腻</option></select>
      <select v-else-if="field==='rule:deathPolicy'" v-model="value" aria-label="新的内容" required><option value="end">死亡终局</option><option value="rescue">等待救援</option><option value="rebirth">有代价重生</option></select>
      <select v-else-if="['alive','rule:relationshipNumbers','rule:randomChecks'].includes(field)||field==='custom'&&valueType==='boolean'" v-model="value" aria-label="新的内容" required><option value="true">{{field==='alive'?'存活':'是'}}</option><option value="false">{{field==='alive'?'已死亡':'否'}}</option></select>
      <select v-else-if="field==='location'" v-model="value" aria-label="新的内容" required><option v-for="e in places" :key="e.id" :value="e.id">{{e.name}}</option></select>
      <input v-else-if="field==='inventory'||field==='custom'&&valueType==='number'" :value="value" @input="value=($event.target as HTMLInputElement).value" type="number" :min="field==='inventory'?0:undefined" :max="field==='inventory'?100000:undefined" :step="field==='inventory'?1:'any'" required>
      <textarea v-else v-model="value" :maxlength="limit" :required="!['description','traits','goal','rule:style','rule:customRules'].includes(field)"></textarea>
    </label>
  </template>
  <label>调整原因<input v-model="reason" maxlength="800" required></label>
  <p v-if="error" class="error" role="alert">{{error}}</p>
  <button class="primary" :disabled="busy||!reason.trim()">{{busy?'正在保存…':'保存并另开世界线'}}</button>
</form>
</template>
