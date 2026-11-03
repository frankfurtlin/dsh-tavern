<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'
const props=defineProps<{items:{sceneId:string;generation:number;createdAt:string;previewUrl:string}[];modelValue:string;disabled:boolean}>()
const emit=defineEmits<{'update:modelValue':[value:string]}>()
const preview=ref(''),error=ref('');let version=0
function clear(){if(preview.value)URL.revokeObjectURL(preview.value);preview.value=''}
watch(()=>[props.modelValue,props.items] as const,async()=>{
 const current=++version;clear();error.value=''
 const image=props.items.find(i=>i.sceneId===props.modelValue);if(!image)return
 try{
  const response=await fetch(backendBase()+image.previewUrl,{headers:authorizationHeaders(),credentials:'include'})
  if(!response.ok)throw new Error('图片预览暂时无法读取。')
  const data=await response.blob();if(current!==version)return
  preview.value=URL.createObjectURL(data)
 }catch(e){if(current===version)error.value=(e as Error).message}
},{immediate:true})
onUnmounted(()=>{version++;clear()})
</script>
<template>
  <details class="scene-saved-images"><summary>选择其他已保存图片</summary>
    <div class="scene-image-choice"><label for="scene-saved-image-choice">已保存图片</label><select id="scene-saved-image-choice" :value="modelValue" :disabled="disabled" @change="emit('update:modelValue',($event.target as HTMLSelectElement).value)"><option value="">继续当前画面</option><option v-for="image in items" :key="image.sceneId" :value="image.sceneId">第 {{image.generation}} 张底图</option></select></div>
    <img v-if="preview" :src="preview" alt="已保存的场景底图预览，尚未完成校验" width="520" height="260"/>
    <p v-if="error" role="alert">{{error}}</p><small>预览供选择；校验完成后才能用于沉浸场景。选择图片不会发起模型调用。</small>
  </details>
</template>
<style scoped>
.scene-saved-images{margin:16px auto 0;max-width:520px;width:100%;font-size:14px;text-align:left}.scene-saved-images summary{cursor:pointer;min-height:44px;display:flex;align-items:center;justify-content:center;text-decoration:underline;text-underline-offset:4px}.scene-image-choice{display:flex;align-items:center;justify-content:center;gap:12px}.scene-saved-images select{min-height:44px;max-width:65%}.scene-saved-images img{display:block;max-width:100%;height:auto;max-height:190px;object-fit:contain;margin:12px auto;border-radius:8px}.scene-saved-images small{display:block;text-align:center;line-height:1.6;margin-top:8px}
</style>
