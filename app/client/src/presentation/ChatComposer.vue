<script setup lang="ts">
import {ref,watch,onMounted,onUnmounted} from 'vue'
import {Plus,Send,Square} from 'lucide-vue-next'
import ActionMenu from './ActionMenu.vue'
const model=defineModel<string>({required:true}),props=defineProps<{disabled?:boolean;canSend:boolean;canStop:boolean;label?:string;placeholder?:string}>(),emit=defineEmits<{send:[];stop:[]}>(),input=ref<HTMLTextAreaElement|null>(null)
let frame=0,disposed=false,observer:ResizeObserver|null=null,lastWidth=0
function resize(){frame=0;const el=input.value;if(!el||disposed)return;const viewport=window.visualViewport?.height||innerHeight,max=Math.max(48,Math.min(180,viewport*.3));el.style.height='auto';el.style.height=Math.min(Math.max(48,el.scrollHeight),max)+'px';el.style.overflowY=el.scrollHeight>max?'auto':'hidden'}
function schedule(){if(!frame)frame=requestAnimationFrame(resize)}
function send(event:KeyboardEvent){if(event.isComposing||!(event.ctrlKey||event.metaKey)||event.key!=='Enter')return;event.preventDefault();if(props.canSend)emit('send')}
watch(model,schedule,{flush:'post'})
onMounted(()=>{resize();observer=new ResizeObserver(rows=>{const width=rows[0]?.contentRect.width||0;if(width!==lastWidth){lastWidth=width;schedule()}});if(input.value)observer.observe(input.value);window.addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);void document.fonts?.ready.then(schedule)})
onUnmounted(()=>{disposed=true;cancelAnimationFrame(frame);observer?.disconnect();window.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('resize',schedule)})
</script>
<template><div class="chat-composer"><slot name="above"/><div class="final-composer-row"><ActionMenu class="chat-tools-menu" label="更多聊天工具"><template #trigger><Plus :size="21"/></template><slot name="tools"/></ActionMenu><textarea ref="input" v-model="model" :disabled="disabled" :aria-label="label||'输入消息'" :placeholder="placeholder||'说点什么…'" rows="1" maxlength="20000" @keydown="send"/><button v-if="canStop" class="final-primary" aria-label="停止回复" @click="emit('stop')"><Square :size="20"/></button><button v-else class="final-primary" aria-label="发送消息" :disabled="!canSend" @click="emit('send')"><Send :size="20"/></button></div><slot name="status"/></div></template>
<style scoped>
.chat-composer{width:100%;min-width:0}.final-composer-row{display:flex;align-items:end;gap:10px;min-width:0}.final-composer-row textarea{flex:1;min-width:0;min-height:48px;max-height:180px;padding:12px 14px;resize:none;font:inherit;line-height:1.5;border:1px solid var(--edge);background:var(--paper);border-radius:16px}.final-composer-row>.final-primary{flex:none;width:48px;height:48px;padding:0;display:grid;place-items:center;border-radius:50%}.chat-tools-menu{flex:none}.chat-tools-menu :deep(summary){width:44px;height:48px;padding:10px;border:0;background:transparent;border-radius:50%}.chat-composer :deep(.final-draft-copies){max-height:240px;overflow:auto}.chat-composer :deep(.chat-group-controls){display:grid;gap:12px;min-width:0}.chat-composer :deep(.chat-group-controls input){width:100%}
</style>
