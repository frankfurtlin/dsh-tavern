<script setup lang="ts">
import {ref} from 'vue'
import {fieldImage} from './field-image'
import type {CustomField} from './types'
const props=defineProps<{field:CustomField;modelValue:string|number|boolean|string[]|null}>()
const emit=defineEmits<{'update:modelValue':[value:string|number|boolean|string[]|null]}>(),error=ref('')
async function image(event:Event){const file=(event.target as HTMLInputElement).files?.[0];if(!file)return;try{error.value='';emit('update:modelValue',await fieldImage(file))}catch(e){error.value=(e as Error).message}}
function toggle(option:string,checked:boolean){const current=Array.isArray(props.modelValue)?props.modelValue:[];emit('update:modelValue',checked?[...new Set([...current,option])]:current.filter(v=>v!==option))}
</script>
<template>
<div class="author-runtime-field" :class="{half:field.width==='half'}" :style="{minHeight:(field.minHeight||44)+'px'}">
 <fieldset v-if="field.type==='multiselect'||field.type==='select'&&field.presentation==='radio'" class="runtime-choices"><legend>{{field.label}}{{field.required?'（必填）':''}}</legend><label v-for="option in field.options" :key="option"><input v-if="field.type==='multiselect'" type="checkbox" :checked="Array.isArray(modelValue)&&modelValue.includes(option)" @change="toggle(option,($event.target as HTMLInputElement).checked)"><input v-else type="radio" :name="field.id" :value="option" :checked="modelValue===option" @change="emit('update:modelValue',option)">{{option}}</label><small v-if="field.required">{{field.type==='multiselect'?'至少选择一项':'请选择一项'}}</small></fieldset>
 <label v-else>{{field.label}}<span v-if="field.required">（必填）</span>
  <template v-if="field.type==='image'"><img v-if="typeof modelValue==='string'&&/^data:image\/(png|jpeg|webp);base64,/.test(modelValue)" :src="modelValue" :alt="field.label"><input type="file" accept="image/png,image/jpeg,image/webp" :aria-label="field.label" @change="image"><small v-if="error" role="alert">{{error}}</small></template>
  <span v-else-if="field.type==='random'">开局时从 {{field.options.join('、')}} 中随机决定，刷新不会重抽。</span>
  <select v-else-if="field.type==='select'" :aria-label="field.label+(field.required?'（必填）':'')" :required="field.required" :value="modelValue" @change="emit('update:modelValue',($event.target as HTMLSelectElement).value)"><option value="">请选择</option><option v-for="option in field.options" :key="option" :value="option">{{option}}</option></select>
  <select v-else-if="field.type==='boolean'" :aria-label="field.label+(field.required?'（必填）':'')" :required="field.required" :value="modelValue===null?'':String(modelValue)" @change="emit('update:modelValue',($event.target as HTMLSelectElement).value===''?null:($event.target as HTMLSelectElement).value==='true')"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>
  <input v-else-if="field.type==='number'" type="number" :aria-label="field.label+(field.required?'（必填）':'')" :value="modelValue as number" :min="field.minimum??undefined" :max="field.maximum??undefined" :required="field.required" @input="emit('update:modelValue',($event.target as HTMLInputElement).value===''?null:Number(($event.target as HTMLInputElement).value))">
  <textarea v-else-if="field.type==='longtext'||field.type==='list'" :aria-label="field.label+(field.required?'（必填）':'')" :value="Array.isArray(modelValue)?modelValue.join('\n'):String(modelValue??'')" :required="field.required" :maxlength="field.type==='list'?1620:2000" :rows="field.type==='list'?4:5" :placeholder="field.type==='list'?'每行一项，最多20项':''" @change="emit('update:modelValue',field.type==='list'?($event.target as HTMLTextAreaElement).value.split('\n').map(v=>v.trim()).filter(Boolean):($event.target as HTMLTextAreaElement).value)"></textarea>
  <input v-else :aria-label="field.label+(field.required?'（必填）':'')" :value="modelValue as string" :required="field.required" maxlength="500" @input="emit('update:modelValue',($event.target as HTMLInputElement).value)">
 </label>
</div>
</template>
<style scoped>.author-runtime-field{min-width:0}.author-runtime-field>label{display:block}.author-runtime-field img{display:block;max-width:160px;max-height:160px;object-fit:contain;border-radius:10px}.author-runtime-field input:not([type=checkbox]):not([type=radio]),.author-runtime-field select,.author-runtime-field textarea{width:100%;max-width:100%;min-height:44px}.runtime-choices{border:0;margin:0;padding:0}.runtime-choices label{display:flex;align-items:center;gap:8px;min-height:44px}.runtime-choices input{width:18px;height:18px}.runtime-choices small{display:block}</style>
