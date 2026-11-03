<script setup lang="ts">
import {computed,ref,watch,nextTick,onMounted,onUnmounted} from 'vue'
import {ChevronLeft,ChevronRight} from 'lucide-vue-next'
import {unicodeOffset} from '../domain/local-contract'
import type {PagingMode} from './local-tools'
interface Point {paragraphId:string;offset:number}
const props=defineProps<{mode:PagingMode;doublePage:boolean;lineWidth:number;layoutKey:string;contentKey:string;anchor:Point|null;busy:boolean;hasPrevious:boolean;hasNext:boolean}>()
const emit=defineEmits<{anchor:[Point];boundary:[-1|1]}>(),root=ref<HTMLElement|null>(null),viewport=ref<HTMLElement|null>(null),page=ref(0),count=ref(1),width=ref(1),height=ref(400),wide=ref(false),turning=ref(false),direction=ref(1),drag=ref(0)
const paged=computed(()=>props.mode!=='scroll'),columns=computed(()=>props.doublePage&&wide.value?2:1),gap=36,step=computed(()=>width.value+gap)
let disposed=false,frame=0,ticket=0,animationTimer:ReturnType<typeof setTimeout>|undefined,resize:ResizeObserver|undefined,mutation:MutationObserver|undefined,pointer:{id:number;x:number;y:number;horizontal:boolean}|null=null,oldX=0,retainedPoint:Point|null=null
const body=()=>viewport.value?.querySelector<HTMLElement>('.final-reader-body')||null
const styles=computed(()=>({'--reader-page-height':height.value+'px','--reader-page-width':props.lineWidth+'px','--reader-columns':columns.value,'--reader-column-gap':gap+'px','--reader-page-x':(-page.value*step.value+drag.value)+'px','--reader-old-x':oldX+'px'}))
function measure(){const v=viewport.value,b=body();if(!v||!b||!paged.value)return;wide.value=innerWidth>=900;const bottom=document.querySelector('.final-reader-writing-entry')?.getBoundingClientRect().height||64;const top=v.getBoundingClientRect().top;height.value=Math.max(120,(window.visualViewport?.height||innerHeight)-top-bottom-66);width.value=v.clientWidth;const number=Math.max(1,Math.ceil((b.scrollWidth+gap-1)/(width.value+gap)));count.value=number;page.value=Math.min(page.value,number-1)}
function pointRect(point:Point){const el=body()?.querySelector<HTMLElement>('#'+CSS.escape(point.paragraphId)),node=el?.firstChild;if(!node||node.nodeType!==Node.TEXT_NODE)return null;const text=node.textContent||'',at=unicodeOffset(text,Math.max(0,Math.min(text.length,point.offset))),range=document.createRange();range.setStart(node,at);range.setEnd(node,at<text.length?unicodeOffset(text,Math.min(text.length,at+(text.codePointAt(at)!>0xffff?2:1))):at);const rects=range.getClientRects();return rects[0]||range.getBoundingClientRect()}
function firstAnchor():Point|null{
 const v=viewport.value,b=body();if(!v||!b||!paged.value)return null;const view=v.getBoundingClientRect(),d=document as Document&{caretRangeFromPoint?:(x:number,y:number)=>Range|null}
 for(const el of b.querySelectorAll<HTMLElement>('.final-reader-paragraph')){const r=document.createRange();r.selectNodeContents(el);const rect=[...r.getClientRects()].find(r=>r.width>0&&r.right>view.left+1&&r.left<view.right-1&&r.bottom>view.top&&r.top<view.bottom);if(!rect)continue;const caret=d.caretRangeFromPoint?.(Math.max(view.left+2,rect.left+2),Math.max(view.top+2,rect.top+rect.height/2));if(caret&&el.contains(caret.startContainer)){const prefix=document.createRange();prefix.selectNodeContents(el);prefix.setEnd(caret.startContainer,caret.startOffset);return {paragraphId:el.id,offset:unicodeOffset(el.textContent||'',prefix.toString().length)}}return {paragraphId:el.id,offset:0}}
 return null
}
function record(){if(props.busy||disposed)return;const anchor=firstAnchor();if(anchor)emit('anchor',anchor)}
function isVisible(point:Point){const r=pointRect(point),v=viewport.value?.getBoundingClientRect();return !!(r&&v&&r.right>v.left&&r.left<v.right&&r.bottom>v.top&&r.top<v.bottom)}
function retain(){if(props.busy||disposed)return;const point=retainedPoint;if(point&&isVisible(point)){if(props.anchor?.paragraphId!==point.paragraphId||props.anchor.offset!==point.offset)emit('anchor',{...point})}else record()}
async function show(point:Point|null=props.anchor,edge:'first'|'last'='first'){
 const epoch=++ticket;cancelAnimationFrame(frame);clearTimeout(animationTimer);drag.value=0;turning.value=false;await nextTick();if(disposed||epoch!==ticket||!paged.value)return;measure();await nextTick();if(disposed||epoch!==ticket)return;measure();await nextTick();if(disposed||epoch!==ticket)return;if(edge==='last')page.value=count.value-1;else if(point){const r=pointRect(point),v=viewport.value?.getBoundingClientRect();page.value=r&&v?Math.max(0,Math.min(count.value-1,Math.floor((r.left-v.left+page.value*step.value+1)/step.value))):0}else page.value=0;retainedPoint=edge==='last'?null:point?{...point}:null;await nextTick();if(!disposed&&epoch===ticket)retain()
}
function schedule(){if(disposed||!paged.value)return;const anchor=props.anchor||firstAnchor();cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>void show(anchor))}
async function turn(delta:-1|1){if(disposed||props.busy||turning.value)return;retainedPoint=null;const next=page.value+delta;if(next<0||next>=count.value){emit('boundary',delta);return}oldX=-page.value*step.value;direction.value=delta;page.value=next;drag.value=0;if(props.mode!=='none'&&!matchMedia('(prefers-reduced-motion: reduce)').matches){turning.value=true;clearTimeout(animationTimer);animationTimer=setTimeout(()=>{turning.value=false;record()},260)}else{await nextTick();record()}}
function key(event:KeyboardEvent){if(!paged.value||event.defaultPrevented||document.hidden||document.querySelector('[role=dialog]')||(event.target as Element|null)?.closest('input,textarea,select,[contenteditable=true]')||!window.getSelection()?.isCollapsed)return;const delta=['ArrowRight','PageDown'].includes(event.key)||event.key===' '&&!event.shiftKey?1:['ArrowLeft','PageUp'].includes(event.key)||event.key===' '&&event.shiftKey?-1:0;if(delta){event.preventDefault();void turn(delta as -1|1)}}
function down(event:PointerEvent){if(!paged.value||props.busy||event.button!==0||(event.target as Element).closest('button,a,input,textarea,select,[contenteditable=true]'))return;pointer={id:event.pointerId,x:event.clientX,y:event.clientY,horizontal:false}}
function move(event:PointerEvent){if(!pointer||pointer.id!==event.pointerId||!paged.value||!window.getSelection()?.isCollapsed)return;const dx=event.clientX-pointer.x,dy=event.clientY-pointer.y;if(Math.abs(dx)>24&&Math.abs(dx)>Math.abs(dy)*2){pointer.horizontal=true;if(event.pointerType!=='mouse'&&viewport.value){viewport.value.setPointerCapture(event.pointerId);event.preventDefault()}drag.value=props.mode==='none'?0:Math.max(-width.value*.3,Math.min(width.value*.3,dx*.5))}}
function up(event:PointerEvent){const start=pointer;pointer=null;drag.value=0;if(!start||start.id!==event.pointerId||!paged.value||!window.getSelection()?.isCollapsed)return;const dx=event.clientX-start.x,dy=event.clientY-start.y;if(start.horizontal&&Math.abs(dx)>45&&Math.abs(dx)>Math.abs(dy)*2){void turn(dx<0?1:-1);return}if(Math.hypot(dx,dy)<8&&viewport.value){const r=viewport.value.getBoundingClientRect();if(event.clientX-r.left<32)void turn(-1);else if(r.right-event.clientX<32)void turn(1)}}
function cancel(){pointer=null;drag.value=0}
watch(()=>props.contentKey,()=>{page.value=0;void show(props.anchor)},{flush:'post'})
watch(()=>[props.mode,props.doublePage,props.lineWidth,props.layoutKey],()=>void show(props.anchor),{flush:'post'})
watch(()=>props.busy,value=>{if(!value)retain()},{flush:'post'})
onMounted(()=>{resize=new ResizeObserver(schedule);if(viewport.value)resize.observe(viewport.value);mutation=new MutationObserver(schedule);if(body())mutation.observe(body()!,{subtree:true,childList:true,characterData:true});window.addEventListener('keydown',key);window.addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);root.value?.addEventListener('load',schedule,true);document.fonts?.ready.then(schedule);void show()})
onUnmounted(()=>{disposed=true;ticket++;cancelAnimationFrame(frame);clearTimeout(animationTimer);resize?.disconnect();mutation?.disconnect();window.removeEventListener('keydown',key);window.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('resize',schedule);root.value?.removeEventListener('load',schedule,true)})
defineExpose({show,firstAnchor,isVisible})
</script>
<template><div ref="root" class="reader-pages" :class="{'reader-pages-paged':paged,'reader-pages-double':columns===2,'reader-page-turning':turning,'reader-page-back':direction<0}" :data-mode="mode" :style="styles"><div ref="viewport" class="reader-page-viewport" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="cancel"><slot/></div><nav v-if="paged" class="reader-page-controls" aria-label="翻页"><button :disabled="busy||turning||page===0&&!hasPrevious" aria-label="上一页" aria-keyshortcuts="ArrowLeft PageUp" @click="turn(-1)"><ChevronLeft :size="19"/></button><span role="status">{{page+1}} / {{count}}<small v-if="columns===2">双页</small></span><button :disabled="busy||turning||page===count-1&&!hasNext" aria-label="下一页" aria-keyshortcuts="ArrowRight PageDown" @click="turn(1)"><ChevronRight :size="19"/></button></nav></div></template>
<style scoped>
.reader-pages:not(.reader-pages-paged),.reader-pages:not(.reader-pages-paged)>.reader-page-viewport{display:contents}
.reader-pages-paged{max-width:calc(var(--reader-page-width) * var(--reader-columns) + 36px);padding:24px 24px 0;margin:0 auto;perspective:1600px}
.reader-pages-paged .reader-page-viewport{height:var(--reader-page-height);overflow:hidden;position:relative;touch-action:pan-y;isolation:isolate}
.reader-pages-paged :deep(.final-reader-body){height:100%;width:100%;max-width:none!important;box-sizing:border-box;margin:0!important;padding:0!important;column-count:var(--reader-columns);column-gap:var(--reader-column-gap);column-fill:auto;transform:translateX(var(--reader-page-x));transform-origin:center;will-change:transform}
.reader-pages-paged :deep(.final-reader-body h1){margin:0 0 1.2em;font-size:1.5em}
.reader-pages-paged :deep(.final-reader-paragraph){orphans:2;widows:2;overflow-wrap:anywhere}
.reader-pages-double .reader-page-viewport:after{content:'';position:absolute;top:0;bottom:0;left:50%;width:1px;background:var(--edge);pointer-events:none}
.reader-page-controls{display:flex;align-items:center;justify-content:center;gap:28px;min-height:60px;font:13px system-ui,sans-serif;color:var(--quiet)}
.reader-page-controls button{display:flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;background:transparent;border:0}
.reader-page-controls span{min-width:80px;text-align:center;font-variant-numeric:tabular-nums}.reader-page-controls small{margin-left:8px}
.reader-page-turning[data-mode=slide] :deep(.final-reader-body){animation:reader-slide .24s ease both}
.reader-page-turning[data-mode=cover] :deep(.final-reader-body){animation:reader-cover .24s ease both}
.reader-page-turning[data-mode=curl] :deep(.final-reader-body){animation:reader-curl .24s ease both;backface-visibility:hidden}
.reader-page-turning[data-mode=curl] .reader-page-viewport:before{content:'';position:absolute;inset:0;z-index:2;pointer-events:none;background:linear-gradient(105deg,transparent 35%,#29241913 58%,#fff7 61%,transparent 68%);animation:reader-fold .24s ease both}
@keyframes reader-slide{from{transform:translateX(var(--reader-old-x))}to{transform:translateX(var(--reader-page-x))}}
@keyframes reader-cover{from{transform:translateX(var(--reader-old-x));filter:brightness(.95)}to{transform:translateX(var(--reader-page-x));filter:brightness(1)}}
@keyframes reader-curl{0%{transform:translateX(var(--reader-old-x));filter:brightness(1)}45%{transform:translateX(var(--reader-old-x)) rotateY(-10deg);filter:brightness(.92)}46%{transform:translateX(var(--reader-page-x)) rotateY(10deg);filter:brightness(.96)}100%{transform:translateX(var(--reader-page-x));filter:brightness(1)}}
@keyframes reader-fold{from{opacity:0;transform:translateX(50%)}45%{opacity:1}to{opacity:0;transform:translateX(-50%)}}
@media(max-width:767px){.reader-pages-paged{padding:20px 20px 0}}
@media(prefers-reduced-motion:reduce){.reader-page-turning :deep(.final-reader-body),.reader-page-turning .reader-page-viewport:before{animation:none!important}}
</style>
