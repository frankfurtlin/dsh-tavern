<script lang="ts">
import {shallowRef} from 'vue'
interface SheetFrame {id:symbol;root?:HTMLElement;previous:HTMLElement|null}
const sheetStack=shallowRef<SheetFrame[]>([])
let originalOverflow:string|undefined
let originalFocus:HTMLElement|null=null
function focusable(frame:SheetFrame){
  return [...(frame.root?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled):not([type="hidden"]),textarea:not(:disabled),select:not(:disabled),summary,[tabindex="0"],[contenteditable="true"]')||[])]
    .filter(canFocus)
}
function focusFirst(frame:SheetFrame){(focusable(frame)[0]||frame.root)?.focus({preventScroll:true})}
function returnTarget(element:HTMLElement|null){
  // Menu actions can mount their sheet before the bubbling click closes the
  // disclosure. Its summary stays available after that action is hidden.
  return element?.closest('details.action-menu')?.querySelector<HTMLElement>(':scope > summary')||element
}
function canFocus(element:HTMLElement|null|undefined){
  if(!element?.isConnected||!element.getClientRects().length||element.closest('[inert],[aria-hidden="true"]'))return false
  if(typeof element.checkVisibility==='function')return element.checkVisibility({visibilityProperty:true,contentVisibilityAuto:true})
  const style=getComputedStyle(element);if(style.visibility==='hidden'||style.display==='none')return false
  for(let parent=element.parentElement;parent;parent=parent.parentElement)if(parent instanceof HTMLDetailsElement&&!parent.open&&!parent.querySelector('summary')?.contains(element))return false
  return true
}
</script>
<script setup lang="ts">
import {ref,computed,onBeforeMount,onMounted,onUnmounted,nextTick} from 'vue'
import {X} from 'lucide-vue-next'
defineOptions({inheritAttrs:false})
const props=defineProps<{title:string;fill?:boolean;returnFocus?:string}>()
const emit=defineEmits<{close:[]}>(),root=ref<HTMLElement>()
const frame:SheetFrame={id:Symbol('sheet'),previous:null}
const active=computed(()=>sheetStack.value.at(-1)===frame)
const layer=computed(()=>200+Math.max(0,sheetStack.value.indexOf(frame)))
function close(){if(active.value)emit('close')}
function key(event:KeyboardEvent){
  if(!active.value||event.defaultPrevented)return
  if(event.key==='Escape'){
    event.preventDefault();event.stopImmediatePropagation();close();return
  }
  if(event.key!=='Tab')return
  const items=focusable(frame),first=items[0],last=items.at(-1)
  if(!first){event.preventDefault();frame.root?.focus({preventScroll:true});return}
  if(!frame.root?.contains(document.activeElement)){
    event.preventDefault();(event.shiftKey?last:first)?.focus();return
  }
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
}
onBeforeMount(()=>{
  frame.previous=returnTarget(document.activeElement as HTMLElement|null)
  if(!sheetStack.value.length){originalOverflow=document.body.style.overflow;originalFocus=frame.previous}
  sheetStack.value=[...sheetStack.value,frame]
  document.body.style.overflow='hidden'
})
onMounted(async()=>{
  frame.root=root.value;document.addEventListener('keydown',key)
  await nextTick();if(active.value)focusFirst(frame)
})
onUnmounted(()=>{
  const wasActive=active.value
  document.removeEventListener('keydown',key)
  sheetStack.value=sheetStack.value.filter(item=>item!==frame)
  const remaining=sheetStack.value.at(-1)
  let target=frame.previous
  if(!remaining){
    document.body.style.overflow=originalOverflow??''
    target=originalFocus;originalOverflow=undefined;originalFocus=null
  }else document.body.style.overflow='hidden'
  if(wasActive)void nextTick(()=>{
    if(sheetStack.value.at(-1)!==remaining)return
    const available=returnTarget(target)
    if(canFocus(available)&&(!remaining||remaining.root?.contains(available!)))available!.focus({preventScroll:true})
    else if(remaining)focusFirst(remaining)
    else if(props.returnFocus)document.querySelector<HTMLElement>(props.returnFocus)?.focus({preventScroll:true})
  })
})
</script>
<template><Teleport to="body"><div v-bind="$attrs" class="scrim" :style="{zIndex:layer}" @click.self="close"><section ref="root" class="sheet" role="dialog" :aria-modal="active?'true':undefined" :aria-hidden="active?undefined:'true'" :inert="!active" :aria-label="title" tabindex="-1"><header class="sheet-header"><h2>{{title}}</h2><button class="icon-button" aria-label="关闭" @click="close"><X :size="22"/></button></header><div class="sheet-content" :class="{'sheet-content-fill':fill}"><slot/></div></section></div></Teleport></template>
