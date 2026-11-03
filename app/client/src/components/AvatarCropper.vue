<script setup lang="ts">
import {computed,onUnmounted} from 'vue'
import type {ImageCrop} from '../api/assets'
const props=defineProps<{src:string;width:number;height:number;modelValue:ImageCrop}>()
const emit=defineEmits<{ 'update:modelValue':[value:ImageCrop] }>()
const scale=computed(()=>Math.min(260/props.width,370/props.height)),side=computed(()=>props.modelValue.width)
const frame=computed(()=>({width:props.width*scale.value+'px',height:props.height*scale.value+'px'}))
const square=computed(()=>({left:props.modelValue.x*scale.value+'px',top:props.modelValue.y*scale.value+'px',width:side.value*scale.value+'px',height:side.value*scale.value+'px'}))
const face=computed(()=>{const s=96/Math.max(1,side.value);return{width:props.width*s+'px',height:props.height*s+'px',maxWidth:'none',transform:`translate(${-props.modelValue.x*s}px,${-props.modelValue.y*s}px)`}})
function update(x:number,y:number,size=side.value){size=Math.max(1,Math.min(Math.round(size),props.width,props.height));emit('update:modelValue',{x:Math.max(0,Math.min(Math.round(x),props.width-size)),y:Math.max(0,Math.min(Math.round(y),props.height-size)),width:size,height:size})}
function resize(event:Event){const size=Number((event.target as HTMLInputElement).value);update(props.modelValue.x+(side.value-size)/2,props.modelValue.y+(side.value-size)/2,size)}
let cleanup=()=>{}
function drag(event:PointerEvent){
 if(event.button!==0)return;event.preventDefault();cleanup()
 const start={...props.modelValue},s=scale.value
 const move=(e:PointerEvent)=>update(start.x+(e.clientX-event.clientX)/s,start.y+(e.clientY-event.clientY)/s)
 const done=()=>cleanup()
 cleanup=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',done);window.removeEventListener('pointercancel',done)}
 window.addEventListener('pointermove',move);window.addEventListener('pointerup',done);window.addEventListener('pointercancel',done)
}
function key(event:KeyboardEvent){const n=event.shiftKey?10:1,delta:Record<string,[number,number]>={ArrowLeft:[-n,0],ArrowRight:[n,0],ArrowUp:[0,-n],ArrowDown:[0,n]};if(delta[event.key]){event.preventDefault();const [x,y]=delta[event.key]!;update(props.modelValue.x+x,props.modelValue.y+y)}}
onUnmounted(()=>cleanup())
</script>
<template><section class="avatar-cropper" aria-label="头像与立绘预览"><div class="avatar-crop-stage" :style="frame"><img :src="src" alt="完整角色立绘"><button type="button" class="avatar-crop-region" :style="square" aria-label="移动头像取景框，可使用方向键微调" @pointerdown="drag" @keydown="key"></button></div><div class="avatar-crop-controls"><span>对话头像</span><div class="avatar-crop-face"><img :src="src" :style="face" alt="头像裁切预览"></div><p>拖动取景框选择面部，或用方向键微调。</p><label>头像取景大小<input type="range" aria-label="头像取景大小" :min="Math.max(1,Math.floor(Math.min(width,height)*.08))" :max="Math.min(width,height)" :value="side" @input="resize"></label></div></section></template>
<style scoped>
.avatar-cropper{display:flex;gap:18px;align-items:start;justify-content:center;min-width:0}.avatar-crop-stage{position:relative;overflow:hidden;flex:none;background:var(--paper);border-radius:10px}.avatar-crop-stage>img{width:100%;height:100%;display:block}.avatar-crop-region{position:absolute;border:2px solid white!important;border-radius:0!important;background:transparent!important;min-width:0!important;min-height:0!important;padding:0!important;cursor:move;touch-action:none;box-shadow:0 0 0 999px #11182738}.avatar-crop-region:focus-visible{outline:3px solid var(--accent);outline-offset:2px}.avatar-crop-controls{width:130px;min-width:0;color:var(--quiet);font-size:12px}.avatar-crop-controls>span{display:block;margin-bottom:10px;color:var(--ink);font-weight:600}.avatar-crop-face{position:relative;overflow:hidden;width:96px;height:96px;border-radius:50%;border:1px solid var(--edge);background:var(--paper)}.avatar-crop-face img{position:absolute;left:0;top:0;object-fit:fill}.avatar-crop-controls p{font-size:12px;line-height:1.7;margin:12px 0}.avatar-crop-controls label{display:grid;gap:8px;font-size:12px}.avatar-crop-controls input{width:100%;padding:0;accent-color:var(--accent)}@media(max-width:500px){.avatar-cropper{gap:12px;flex-wrap:wrap}.avatar-crop-controls{width:100%;display:grid;grid-template-columns:96px minmax(0,1fr);gap:6px 14px}.avatar-crop-controls>span{grid-column:2}.avatar-crop-face{grid-column:1;grid-row:1/4}.avatar-crop-controls p{grid-column:2;margin:0}.avatar-crop-controls label{grid-column:2}}
</style>
