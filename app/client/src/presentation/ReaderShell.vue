<script setup lang="ts">
import {ref,onMounted,onUnmounted,nextTick} from 'vue'
import {MoreHorizontal} from 'lucide-vue-next'
const controls=defineModel<boolean>('controls',{default:false})
defineProps<{label:string}>()
const peek=ref<HTMLButtonElement|null>(null)
let pointer:{id:number;x:number;y:number}|null=null
function down(event:PointerEvent){pointer={id:event.pointerId,x:event.clientX,y:event.clientY}}
function toggle(event:MouseEvent){
  const start=pointer;pointer=null
  if(event.defaultPrevented||!start||Math.hypot(event.clientX-start.x,event.clientY-start.y)>8)return
  const target=event.target instanceof Element?event.target:null
  if(!target?.closest('.final-reader-body')||target.closest('button,a,input,textarea,select,[contenteditable=true]')||document.querySelector('[role=dialog]')||!window.getSelection()?.isCollapsed)return
  // The outer edges remain available to the reader's existing page gestures.
  const viewport=target.closest('.reader-page-viewport')?.getBoundingClientRect()
  if(viewport&&viewport.width>64&&(event.clientX<viewport.left+32||event.clientX>viewport.right-32))return
  controls.value=!controls.value
}
function hide(event:KeyboardEvent){if(event.key==='Escape'&&controls.value&&!event.defaultPrevented&&!document.querySelector('[role=dialog]')){controls.value=false;event.preventDefault();void nextTick(()=>peek.value?.focus({preventScroll:true}))}}
onMounted(()=>document.addEventListener('keydown',hide))
onUnmounted(()=>document.removeEventListener('keydown',hide))
</script>
<template>
  <section class="reader-shell final-reader" :aria-label="label" @pointerdown="down" @pointercancel="pointer=null" @click="toggle">
    <div class="reader-shell-header" :class="{'reader-shell-controls-visible':controls}" :inert="!controls" :aria-hidden="controls?undefined:'true'"><slot name="header"/></div>
    <button v-if="!controls" ref="peek" class="reader-shell-peek" aria-label="显示阅读菜单" :aria-expanded="controls" @click="controls=true"><MoreHorizontal :size="20"/></button>
    <slot/>
    <div class="reader-shell-controls final-reader-writing-entry" :class="{'reader-shell-controls-visible':controls}" :inert="!controls" :aria-hidden="controls?undefined:'true'"><nav class="reader-control-capsule" aria-label="阅读控制"><slot name="controls"/></nav></div>
    <slot name="selection"/>
    <slot name="overlays"/>
  </section>
</template>
<style scoped>
.reader-shell{min-height:100dvh}.reader-shell-header{position:sticky;top:0;z-index:20;opacity:0;pointer-events:none;transition:opacity .16s ease}.reader-shell-header :deep(.final-reader-header){position:static;border-bottom:0;background:color-mix(in srgb,var(--canvas) 96%,transparent);padding-top:calc(12px + env(safe-area-inset-top))}.reader-shell-peek{position:fixed;right:16px;top:calc(12px + env(safe-area-inset-top));z-index:21;display:grid;place-items:center;width:44px;height:44px;padding:0!important;border:1px solid var(--edge);border-radius:50%;background:var(--canvas);color:var(--quiet);opacity:.7}.reader-shell-peek:hover,.reader-shell-peek:focus-visible{opacity:1;color:var(--accent)}.reader-shell-controls{position:fixed;bottom:0;left:0;right:0;z-index:18;display:flex;justify-content:center;padding:12px 16px calc(12px + env(safe-area-inset-bottom));pointer-events:none;opacity:0;transition:opacity .16s ease}.reader-shell-controls-visible{opacity:1;pointer-events:auto}.reader-control-capsule{display:flex;gap:4px;padding:5px;border-radius:999px;background:var(--canvas);border:1px solid var(--edge);box-shadow:0 4px 24px color-mix(in srgb,var(--ink) 8%,transparent)}.reader-control-capsule :deep(button){min-height:44px;min-width:60px;padding:8px 12px;border:0;border-radius:999px;background:transparent;display:flex;align-items:center;justify-content:center;gap:6px;font-size:14px;white-space:nowrap;color:var(--quiet)}.reader-control-capsule :deep(button:hover),.reader-control-capsule :deep(button[aria-pressed=true]){color:var(--accent);background:var(--wash)}
@media(max-width:600px){.reader-shell-header :deep(.final-reader-header){padding:calc(10px + env(safe-area-inset-top)) 16px 10px}.reader-control-capsule :deep(button){padding:8px 10px;min-width:62px}}
@media(prefers-reduced-motion:reduce){.reader-shell-header,.reader-shell-controls{transition:none}}
</style>
