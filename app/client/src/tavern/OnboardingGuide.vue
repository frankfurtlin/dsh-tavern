<script setup lang="ts">
import {nextTick,onUnmounted,ref,watch} from 'vue'
import {markGuideSeen} from './guide'

const props=defineProps<{open:boolean}>()
const emit=defineEmits<{(e:'close'):void}>()

const steps=[
  {title:'选一张角色卡',body:'点任意角色卡，立即开始聊天。5 张预设卡开箱即用，也可以导入自己的 SillyTavern 角色卡（PNG / JSON）。'},
  {title:'回一句话',body:'角色会按你的话继续演，回复由模型实时生成。想换一个版本就点「‹ n/m ›」，不喜欢也可以重新生成。'},
  {title:'需要时让 DSH 帮忙',body:'聊天页右上角工具条提供「让 DSH 续写 / 角色扮演」，把当前聊天上下文交给 DSH 处理。'},
]

const step=ref(0)
const dialog=ref<HTMLElement|null>(null)

function close(){
  markGuideSeen()
  emit('close')
}
function next(){
  if(step.value>=steps.length-1)close()
  else step.value+=1
}
function previous(){
  if(step.value>0)step.value-=1
}
function goStep(index:number){
  if(index>=0&&index<steps.length)step.value=index
}
function onKeydown(event:KeyboardEvent){
  if(event.key==='Escape'&&props.open){event.preventDefault();close()}
}

watch(()=>props.open,async open=>{
  if(open){
    step.value=0
    if(typeof window!=='undefined'&&window.addEventListener)window.addEventListener('keydown',onKeydown)
    await nextTick()
    dialog.value?.focus?.()
  }else if(typeof window!=='undefined'&&window.removeEventListener){
    window.removeEventListener('keydown',onKeydown)
  }
},{immediate:true})

onUnmounted(()=>{
  if(typeof window!=='undefined'&&window.removeEventListener)window.removeEventListener('keydown',onKeydown)
})
</script>

<template>
 <div v-if="open" class="guide-backdrop">
  <div ref="dialog" class="guide-dialog" role="dialog" aria-modal="true" aria-labelledby="guide-title" tabindex="-1">
   <span class="guide-eyebrow">欢迎来到酒馆</span>
   <h2 id="guide-title">{{steps[step]!.title}}</h2>
   <p class="guide-body">{{steps[step]!.body}}</p>
   <div class="guide-dots" role="group" aria-label="引导进度">
    <button v-for="(item,index) in steps" :key="item.title" type="button" class="guide-dot" :class="{on:index===step}" :aria-label="'第 '+(index+1)+' 步：'+item.title" :aria-current="index===step?'step':undefined" @click="goStep(index)"/>
   </div>
   <div class="guide-actions">
    <button type="button" class="guide-skip" @click="close">跳过</button>
    <span class="guide-spacer"/>
    <button v-if="step>0" type="button" @click="previous">上一步</button>
    <button type="button" class="primary" @click="next">{{step===steps.length-1?'开始体验':'下一步'}}</button>
   </div>
  </div>
 </div>
</template>

<style scoped>
.guide-backdrop{position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(24,20,16,.42)}
.guide-dialog{width:min(440px,100%);max-height:calc(100dvh - 40px);overflow:auto;background:var(--paper);border:1px solid var(--edge);border-radius:16px;box-shadow:0 24px 64px rgba(24,20,16,.24);padding:24px;display:flex;flex-direction:column;gap:12px}
.guide-dialog:focus{outline:none}
.guide-eyebrow{font-size:12px;letter-spacing:.08em;color:var(--accent);text-transform:uppercase}
.guide-dialog h2{font-family:var(--font-serif);font-size:22px;margin:0;color:var(--ink)}
.guide-body{margin:0;color:var(--quiet);font-size:14px;line-height:1.75;min-height:5.25em}
.guide-dots{display:flex;gap:8px;align-items:center}
.guide-dot{width:26px;height:26px;min-height:26px;padding:0;border:0;background:transparent;cursor:pointer;display:flex;align-items:center;justify-content:center}
.guide-dot::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--edge);transition:background .15s,transform .15s}
.guide-dot.on::before{background:var(--accent);transform:scale(1.25)}
.guide-actions{display:flex;gap:8px;align-items:center;margin-top:4px;flex-wrap:wrap}
.guide-actions button{min-height:44px;padding:0 16px;border-radius:10px;border:1px solid var(--edge);background:var(--canvas);color:var(--ink);cursor:pointer;font-size:14px}
.guide-actions .primary{border-color:var(--accent);background:var(--accent);color:#fff}
.guide-skip{border:0!important;background:transparent!important;color:var(--quiet)!important;padding-left:0!important}
.guide-spacer{flex:1}
@media(max-width:767px){
 .guide-backdrop{padding:0;align-items:flex-end}
 .guide-dialog{width:100%;max-width:none;border-radius:16px 16px 0 0;padding:20px 18px calc(18px + env(safe-area-inset-bottom));max-height:82dvh}
 .guide-actions button{flex:1;justify-content:center}
 .guide-actions .guide-skip{flex:0 0 auto}
 .guide-spacer{display:none}
}
</style>
