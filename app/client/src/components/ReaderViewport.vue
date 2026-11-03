<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {ChevronLeft,ChevronRight} from 'lucide-vue-next'
import type {Anchor,Block} from '../api/types'
import {renderedOffset,canonicalOffset,textBoundaries} from '../api/reading-layout'

const props=defineProps<{mode:'scroll'|'slide'|'page';anchor?:Anchor;blocks:Block[];layoutKey:string}>()
const emit=defineEmits<{changed:[];'layout-start':[];observed:[blockId:string,offset:number]}>()
const root=ref<HTMLElement>(),windowElement=ref<HTMLElement>(),content=ref<HTMLElement>()
const width=ref(0),height=ref(360),page=ref(0),pageCount=ref(1),measuring=ref(false)
const paged=computed(()=>props.mode!=='scroll'),gap=32
const offset=computed(()=>page.value*(width.value+gap))
let observer:ResizeObserver|undefined,frame=0,generation=0,disposed=false,settled:ReturnType<typeof setTimeout>|undefined,startPointer:{id:number;x:number;y:number;selection:string}|null=null
function textModel(blockId:string){
  const element=content.value?.querySelector<HTMLElement>('[id="block-'+CSS.escape(blockId)+'"]')
  if(!element)return null
  const nodes:Text[]=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT)
  while(walker.nextNode())nodes.push(walker.currentNode as Text)
  return {element,nodes,text:nodes.map(node=>node.data).join('')}
}
function rangeAt(model:NonNullable<ReturnType<typeof textModel>>,logicalText:string,logicalOffset:number){
  const target=renderedOffset(logicalText,model.text,logicalOffset),range=document.createRange();let consumed=0
  for(const node of model.nodes){if(target<=consumed+node.length){range.setStart(node,target-consumed);range.collapse(true);return range}consumed+=node.length}
  range.selectNodeContents(model.element);range.collapse(false);return range
}
function updatePage(at:Anchor){
  const block=props.blocks.find(block=>block.id===at.blockId),model=textModel(at.blockId)
  if(!block||!model)return false
  const range=rangeAt(model,block.text,at.end),rect=range.getClientRects()[0]||model.element.getClientRects()[0]
  if(!rect)return false
  if(!paged.value){window.scrollBy({top:rect.top-innerHeight/2,behavior:'instant'});return true}
  const viewport=windowElement.value?.getBoundingClientRect();if(!viewport||!width.value)return false
  const transform=getComputedStyle(content.value!).transform,actualOffset=transform==='none'?0:-new DOMMatrixReadOnly(transform).m41
  page.value=Math.max(0,Math.min(pageCount.value-1,Math.floor((rect.left-viewport.left+actualOffset+1)/(width.value+gap))))
  return true
}
async function gotoAnchor(at:Anchor){
  const epoch=++generation;emit('layout-start');measuring.value=true;clearTimeout(settled)
  await nextTick();if(disposed||epoch!==generation)return false
  const result=updatePage(at);await nextTick();if(disposed||epoch!==generation)return false
  measuring.value=false;emit('changed');return result
}
function go(index:number){
  const target=Math.max(0,Math.min(pageCount.value-1,index));if(target===page.value)return
  emit('layout-start');clearTimeout(settled);page.value=target
  const animation=props.mode==='slide'&&!matchMedia('(prefers-reduced-motion: reduce)').matches?200:0
  void nextTick().then(()=>{if(!disposed)settled=setTimeout(()=>{if(!disposed)emit('changed')},animation)})
}
async function gotoBlock(id:string,edge:'start'|'end'='end'){
  const block=props.blocks.find(block=>block.id===id)
  return block?gotoAnchor(edge==='start'?block.startAnchor:block.endAnchor):false
}
async function measure(){
  if(disposed||!root.value)return
  const epoch=++generation,at=props.anchor?{...props.anchor}:undefined;emit('layout-start');clearTimeout(settled);measuring.value=true
  if(!paged.value){page.value=0;pageCount.value=1;await nextTick();if(disposed||epoch!==generation)return;if(at)updatePage(at);measuring.value=false;emit('changed');return}
  const header=document.querySelector('.reader-header')?.getBoundingClientRect()
  const toolbar=document.querySelector('.reader-toolbar')?.getBoundingClientRect()
  const speech=document.querySelector('.speech-bar')?.getBoundingClientRect()
  const viewportHeight=window.visualViewport?.height||innerHeight
  height.value=Math.max(120,Math.floor(viewportHeight-(header?.bottom||64)-(toolbar?.height||68)-Math.max(84,(speech?.height||0)+36)))
  width.value=Math.floor(windowElement.value?.clientWidth||root.value.clientWidth)
  await nextTick();if(disposed||epoch!==generation)return
  pageCount.value=Math.max(1,Math.ceil(((content.value?.scrollWidth||width.value)+gap)/(width.value+gap)))
  page.value=Math.min(page.value,pageCount.value-1)
  if(at)updatePage(at)
  await nextTick();if(disposed||epoch!==generation)return
  measuring.value=false;emit('changed')
}
function schedule(){cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{void measure()})}
function observe(){
  if(!paged.value||measuring.value||disposed||document.hidden)return
  const viewport=windowElement.value?.getBoundingClientRect();if(!viewport)return
  for(const block of [...props.blocks].reverse()){
    const model=textModel(block.id);if(!model)continue
    if(![...model.element.getClientRects()].some(rect=>rect.right>viewport.left&&rect.left<viewport.right&&rect.bottom>viewport.top&&rect.top<viewport.bottom))continue
    const bounds=textBoundaries(model.text);let low=1,high=bounds.length-1,found=0
    while(low<=high){
      const mid=Math.floor((low+high)/2),range=document.createRange()
      const start=canonicalOffset(block.text,model.text,bounds[mid-1]!),end=canonicalOffset(block.text,model.text,bounds[mid]!)
      const first=rangeAt(model,block.text,start),last=rangeAt(model,block.text,end)
      range.setStart(first.startContainer,first.startOffset);range.setEnd(last.startContainer,last.startOffset)
      const rect=range.getClientRects()[0]
      if(rect&&rect.right<=viewport.right+1){found=mid;low=mid+1}else high=mid-1
    }
    if(!found)continue
    const at=canonicalOffset(block.text,model.text,bounds[found]!),range=rangeAt(model,block.text,at),rect=range.getClientRects()[0]
    if(rect&&rect.left>=viewport.left-1&&rect.left<=viewport.right+1&&rect.top<viewport.bottom&&rect.bottom>viewport.top){emit('observed',block.id,at);return}
  }
}
function key(event:KeyboardEvent){
  if(!paged.value||event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey)return
  if(event.target instanceof HTMLElement&&event.target.closest('input,textarea,select,button,a,[role="button"]'))return
  if(event.key==='ArrowRight'||event.key==='PageDown'){event.preventDefault();go(page.value+1)}
  if(event.key==='ArrowLeft'||event.key==='PageUp'){event.preventDefault();go(page.value-1)}
}
function pointerDown(event:PointerEvent){
  if(!paged.value||event.pointerType==='mouse'||event.target instanceof HTMLElement&&event.target.closest('button,a,input,textarea'))return
  startPointer={id:event.pointerId,x:event.clientX,y:event.clientY,selection:getSelection()?.toString()||''}
}
function pointerUp(event:PointerEvent){
  const start=startPointer;startPointer=null;if(!start||start.id!==event.pointerId)return
  const dx=event.clientX-start.x,dy=event.clientY-start.y
  if((getSelection()?.toString()||'')!==start.selection||Math.abs(dx)<64||Math.abs(dx)<Math.abs(dy)*1.5)return
  go(page.value+(dx<0?1:-1))
}
watch(()=>[props.mode,props.layoutKey],()=>void measure(),{flush:'post'})
onMounted(()=>{observer=new ResizeObserver(schedule);if(root.value)observer.observe(root.value);window.addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);void measure();void document.fonts.ready.then(schedule)})
onUnmounted(()=>{disposed=true;generation++;observer?.disconnect();cancelAnimationFrame(frame);clearTimeout(settled);window.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('resize',schedule)})
defineExpose({gotoBlock,gotoAnchor,measure,observe,page,pageCount})
</script>

<template>
  <div ref="root" class="reader-reading-viewport" :class="{'reader-paged':paged,'reader-slide':mode==='slide','reader-measuring':measuring}" :style="{'--page-width':width+'px','--page-height':height+'px','--page-gap':gap+'px','--page-offset':offset+'px'}" tabindex="-1" @keydown="key">
    <div ref="windowElement" class="reader-page-window" @pointerdown="pointerDown" @pointerup="pointerUp" @pointercancel="startPointer=null"><div ref="content" class="reader-page-content"><slot/></div></div>
    <nav v-if="paged" class="reader-page-controls" aria-label="阅读翻页"><button class="icon-button" aria-label="上一页" :disabled="page===0" @click="go(page-1)"><ChevronLeft :size="20"/></button><span aria-live="polite">{{page+1}} / {{pageCount}} 页</span><button class="icon-button" aria-label="下一页" :disabled="page>=pageCount-1" @click="go(page+1)"><ChevronRight :size="20"/></button></nav>
  </div>
</template>

<style>
.reader-reading-viewport{min-width:0;width:100%}
.reader-paged .reader-page-window{width:100%;height:var(--page-height);overflow:hidden;touch-action:pan-y pinch-zoom}
.reader-paged .reader-page-content{column-width:var(--page-width);column-gap:var(--page-gap);column-fill:auto;width:var(--page-width);height:var(--page-height);transform:translateX(calc(-1 * var(--page-offset)))}
.reader-reading-viewport{max-width:var(--reading-width,40em);margin-inline:auto;min-width:0}
.reader-paged .paragraph{margin-left:0;margin-right:0;box-decoration-break:clone}
.reader-paged .reading-end{break-inside:avoid}
.reader-slide:not(.reader-measuring) .reader-page-content{transition:transform 180ms ease-out}
.reader-page-controls{display:flex;align-items:center;justify-content:center;gap:24px;margin:12px 0;font:14px/1.5 system-ui,sans-serif;color:var(--muted)}
.reader-page-controls button{min-width:44px;min-height:44px}
.reader-body:has(.reader-paged){padding-top:24px;padding-bottom:100px;width:100%}
@media(prefers-reduced-motion:reduce){.reader-slide .reader-page-content{transition:none!important}}
</style>
