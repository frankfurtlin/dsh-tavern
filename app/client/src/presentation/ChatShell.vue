<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {Keyboard} from '@capacitor/keyboard'
import type {PluginListenerHandle} from '@capacitor/core'
import {native} from '../platform/bridge'
defineProps<{label:string}>()
const emit=defineEmits<{scroll:[]}>(),scroller=ref<HTMLElement|null>(null),height=ref('100dvh'),top=ref('0px')
let frame=0,disposed=false,keyboardHeight=0,keyboardBase=0,handles:PluginListenerHandle[]=[]
function measure(){frame=0;if(disposed)return;const viewport=window.visualViewport;if(!keyboardHeight)keyboardBase=innerHeight;const available=Math.min(viewport?.height||innerHeight,keyboardHeight?keyboardBase-keyboardHeight:innerHeight);height.value=Math.max(160,available)+'px';top.value=(viewport?.offsetTop||0)+'px'}
function schedule(){if(!frame)frame=requestAnimationFrame(measure)}
onMounted(()=>{measure();window.addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);window.visualViewport?.addEventListener('scroll',schedule);if(native)void Promise.all([Keyboard.addListener('keyboardWillShow',event=>{if(!keyboardHeight)keyboardBase=innerHeight;keyboardHeight=event.keyboardHeight;schedule()}),Keyboard.addListener('keyboardDidShow',event=>{keyboardHeight=event.keyboardHeight;schedule()}),Keyboard.addListener('keyboardWillHide',()=>{keyboardHeight=0;schedule()}),Keyboard.addListener('keyboardDidHide',()=>{keyboardHeight=0;schedule()})]).then(rows=>{if(disposed)for(const row of rows)void row.remove();else handles=rows}).catch(()=>{})})
onUnmounted(()=>{disposed=true;cancelAnimationFrame(frame);window.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('scroll',schedule);for(const handle of handles)void handle.remove()})
defineExpose({scroller})
</script>
<template><section class="chat-shell final-chat" :style="{height,top}" :aria-label="label"><div class="chat-shell-header"><slot name="header"/></div><div class="chat-shell-messages"><div ref="scroller" class="final-chat-scroll" role="log" aria-live="polite" aria-relevant="additions" :aria-label="label+'消息'" @scroll="emit('scroll')"><slot name="messages"/></div><div class="chat-shell-scroll-controls"><slot name="scroll-controls"/></div></div><div class="chat-shell-composer"><slot name="composer"/></div><slot name="overlays"/></section></template>
<style scoped>
.chat-shell{position:fixed;inset:auto 0 auto;display:flex;flex-direction:column;min-height:0;overflow:hidden;background:var(--paper)}.chat-shell-header{flex:none;min-width:0}.chat-shell-messages{flex:1;min-height:0;position:relative}.final-chat-scroll{height:100%;min-height:0;overflow:auto;overscroll-behavior:contain}.chat-shell-scroll-controls{position:absolute;bottom:16px;right:max(20px,calc((100vw - 840px)/2));pointer-events:none}.chat-shell-scroll-controls :deep(button){pointer-events:auto;min-height:44px;border-radius:999px;background:var(--canvas);box-shadow:0 4px 16px color-mix(in srgb,var(--ink) 10%,transparent)}.chat-shell-composer{flex:none;min-width:0;max-height:55%;overflow:auto;overscroll-behavior:contain}.chat-shell-header :deep(.final-chat-header){padding-top:calc(12px + env(safe-area-inset-top))}.chat-shell-composer :deep(.final-chat-composer){border-top:1px solid var(--edge)}
</style>
