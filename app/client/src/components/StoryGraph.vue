<script setup lang="ts">
import {computed,ref,watch,nextTick,onMounted,onUnmounted} from 'vue'
import {BookOpen,Sparkles,MapPin,Archive,ChevronLeft,ChevronRight,MoreHorizontal,Plus,Minus,Maximize2} from 'lucide-vue-next'
import {TIMELINE_BRANCH_PAGE,TIMELINE_CHAPTER_PAGE,timelineRows,timelineChapterPage,timelineChapterLabel,timelinePageLoaded,timelineChapterStart,timelineLanes,timelineOverviewTargets,timelineChapterHitSize} from '../api/story-timeline'
import Sheet from './Sheet.vue'
import type {StoryTimelineData,TimelineBranch,TimelineChapter} from '../api/story-timeline'
import {zoomAround,fitGraph,focusGraph,panGraph,stepSpring,MIN_SCALE,MAX_SCALE} from '../api/graph-camera'
import type {Camera,Point,Bounds} from '../api/graph-camera'

const props=defineProps<{data:StoryTimelineData;activeChapterId?:string;chapterLoading?:boolean;busy?:boolean;online:boolean}>()
const emit=defineEmits<{open:[branch:TimelineBranch,chapter?:TimelineChapter];fork:[branch:TimelineBranch];rename:[branch:TimelineBranch];status:[branch:TimelineBranch];loadChapters:[offset:number]}>()
const showArchived=ref(false),branchOffset=ref(0),chapterOffsets=ref<Record<string,number>>({}),canvas=ref<HTMLElement>(),viewport=ref<HTMLElement>()
const camera=ref<Camera>({x:0,y:0,scale:1}),size=ref({width:1,height:1}),bounds=ref<Bounds>({x:0,y:0,width:1,height:1}),bases=ref<Record<string,Point>>({}),displacements=ref<Record<string,Point>>({}),highlight=ref(''),dragging=ref(false),animated=ref(false),reduced=ref(false)
const ordered=computed(()=>timelineRows(props.data)),available=computed(()=>ordered.value.filter(row=>showArchived.value||row.branch.status!=='archived'||row.branch.id===props.data.currentBranchId)),rows=computed(()=>available.value.slice(branchOffset.value,branchOffset.value+TIMELINE_BRANCH_PAGE))
const archivedCount=computed(()=>props.data.branches.filter(branch=>branch.status==='archived').length),lanes=computed(()=>timelineLanes(rows.value,chapterOffsets.value))
const offset=(branch:TimelineBranch)=>chapterOffsets.value[branch.id]??timelineChapterStart(branch)
const chapters=(branch:TimelineBranch)=>timelineChapterPage(branch,offset(branch))
const nodeKey=(branchId:string,chapterId:string)=>branchId+':'+chapterId
const overview=ref<{branch:TimelineBranch;chapter:TimelineChapter}[]|null>(null)
const point=(key:string)=>{const base=bases.value[key],delta=displacements.value[key]||{x:0,y:0};return base?{x:base.x+delta.x,y:base.y+delta.y}:undefined}
const overviewTargets=computed(()=>camera.value.scale<=.28?timelineOverviewTargets(rows.value.flatMap(row=>chapters(row.branch).flatMap(chapter=>{const id=nodeKey(row.branch.id,chapter.id),p=point(id);return p?[{id,x:p.x,y:p.y,item:{branch:row.branch,chapter}}]:[]})),camera.value.scale):[])
function chooseOverview(branch:TimelineBranch,chapter:TimelineChapter){overview.value=null;if(chapter.readable)emit('open',branch,chapter)}
function hitStyle(branch:TimelineBranch,chapter:TimelineChapter){const {width,height}=timelineChapterHitSize(camera.value.scale),delta=displacements.value[nodeKey(branch.id,chapter.id)];return {width:width+'px',height:height+'px',minHeight:height+'px',left:(168-width)/2+'px',top:(132-height)/2+'px',transform:delta?`translate(${delta.x}px,${delta.y}px)`:undefined,'--face-left':(width-168)/2+'px','--face-top':(height-132)/2+'px','--node-delay':0}}
const highlighted=computed(()=>{
  const result=new Set<string>(),visited=new Set<string>();let id=highlight.value,limit=Infinity
  while(id&&!visited.has(id)){
    visited.add(id);const row=rows.value.find(row=>row.branch.id===id);if(!row)break
    for(const chapter of chapters(row.branch))if(chapter.position<=limit)result.add(nodeKey(id,chapter.id))
    const fork=lanes.value.get(id)?.fork;if(!fork)break
    const parent=rows.value.find(row=>row.branch.id===fork.branchId),chapter=parent?.branch.chapters.find(chapter=>chapter.id===fork.chapterId)
    if(!chapter)break;id=fork.branchId;limit=chapter.position
  }
  return result
})
function curve(a:Point,b:Point,before=false){const bend=before?a.x-76:(a.x+b.x)/2;return `M ${a.x} ${a.y} C ${bend} ${a.y}, ${bend} ${b.y}, ${b.x} ${b.y}`}
const links=computed(()=>{
  const trunks:{id:string;path:string;active:boolean;ai:boolean}[]=[],forks:{id:string;path:string;source:string;target:string;active:boolean}[]=[]
  for(const row of rows.value){
    const list=chapters(row.branch)
    for(let index=1;index<list.length;index++){const from=nodeKey(row.branch.id,list[index-1].id),to=nodeKey(row.branch.id,list[index].id),a=point(from),b=point(to);if(a&&b)trunks.push({id:to,path:curve(a,b),active:highlighted.value.has(from)&&highlighted.value.has(to),ai:list[index].source==='ai'})}
    const fork=lanes.value.get(row.branch.id)?.fork,first=list[0];if(!fork||!first)continue
    const source=nodeKey(fork.branchId,fork.chapterId),target=nodeKey(row.branch.id,first.id),a=point(source),b=point(target)
    if(a&&b)forks.push({id:row.branch.id,path:curve(fork.before?{x:a.x-22,y:a.y}:a,b,fork.before),source,target,active:highlighted.value.has(target)})
  }
  return {trunks,forks}
})
const transform=computed(()=>`translate(${camera.value.x}px,${camera.value.y}px) scale(${camera.value.scale})`),miniView=computed(()=>`${bounds.value.x} ${bounds.value.y} ${bounds.value.width} ${bounds.value.height}`)
function reading(branch:TimelineBranch,chapter:TimelineChapter){const current=props.data.branches.find(line=>line.id===props.data.currentBranchId);return !!current&&chapter.id===props.activeChapterId&&(branch.id===current.id||chapter.position<timelineChapterStart(current))}
function applyCamera(value:Camera,smooth=false){animated.value=smooth&&!reduced.value;camera.value=panGraph(value,0,0,bounds.value,size.value)}
function resetView(){applyCamera(fitGraph(bounds.value,size.value,32),true)}
function currentPoint(id=props.data.currentBranchId){
  const row=rows.value.find(row=>row.branch.id===id),chapter=row?.branch.chapters.find(chapter=>chapter.id===props.activeChapterId),own=chapter&&point(nodeKey(id,chapter.id));if(own)return own
  if(id===props.data.currentBranchId&&props.activeChapterId){for(const row of rows.value){const p=point(nodeKey(row.branch.id,props.activeChapterId));if(p)return p}}
  return row?point(nodeKey(id,chapters(row.branch)[0]?.id||'')):undefined
}
async function locate(id:string){
  const item=props.data.branches.find(branch=>branch.id===id);if(item?.status==='archived')showArchived.value=true
  const index=available.value.findIndex(row=>row.branch.id===id);if(index>=0)branchOffset.value=Math.floor(index/TIMELINE_BRANCH_PAGE)*TIMELINE_BRANCH_PAGE
  await nextTick();measure(false);const p=currentPoint(id);if(p)applyCamera(focusGraph(p,size.value,Math.max(.85,camera.value.scale)),true)
}
function ensureVisible(key:string,event?:FocusEvent){if(gesture||event&&!(event.target as HTMLElement).matches(':focus-visible'))return;const p=point(key);if(!p)return;const x=p.x*camera.value.scale+camera.value.x,y=p.y*camera.value.scale+camera.value.y;if(x<84||x>size.value.width-84||y<70||y>size.value.height-110)applyCamera(focusGraph(p,size.value,Math.max(.85,camera.value.scale)),true)}
function zoom(factor:number,origin={x:size.value.width/2,y:size.value.height/2}){applyCamera(zoomAround(camera.value,factor,origin))}
function page(branch:TimelineBranch,change:number){const next=Math.max(timelineChapterStart(branch),offset(branch)+change*TIMELINE_CHAPTER_PAGE);if(next>=branch.chaptersTotal)return;chapterOffsets.value={...chapterOffsets.value,[branch.id]:next};if(!timelinePageLoaded(branch,next))emit('loadChapters',next)}
function localOffset(element:HTMLElement){let x=0,y=0,current:HTMLElement|null=element;while(current&&current!==canvas.value){x+=current.offsetLeft;y+=current.offsetTop;current=current.offsetParent as HTMLElement|null}return{x,y}}
function measure(reset=true){
  const root=canvas.value,view=viewport.value;if(!root||!view)return;size.value={width:view.clientWidth,height:view.clientHeight}
  const nodes:Record<string,Point>={};for(const element of root.querySelectorAll<HTMLElement>('.timeline-node[data-node-key]')){const p=localOffset(element);nodes[element.dataset.nodeKey!]={x:p.x+element.offsetWidth/2,y:p.y+28}}
  bases.value=nodes;bounds.value={x:0,y:0,width:Math.max(1,root.offsetWidth),height:Math.max(1,root.offsetHeight)}
  if(reset){if(size.value.width<600){const p=currentPoint(),next=p?focusGraph(p,size.value,.9):fitGraph(bounds.value,size.value,24);if(p)next.x+=Math.min(80,size.value.width*.2);applyCamera(next)}else applyCamera(fitGraph(bounds.value,size.value,36))}
}
function menuAction(event:Event,action:()=>void){(event.target as HTMLElement).closest('details')?.removeAttribute('open');action()}
function positionMenu(event:Event){const details=event.target as HTMLDetailsElement;if(!details.open)return;const box=details.querySelector('summary')!.getBoundingClientRect(),menu=details.querySelector<HTMLElement>('.timeline-menu-content')!;const width=Math.min(240,innerWidth-32);menu.style.width=width+'px';menu.style.left=Math.max(16,Math.min(box.left,innerWidth-width-16))+'px';menu.style.top=Math.max(16,Math.min(box.bottom+8,innerHeight-menu.offsetHeight-16))+'px'}
function outsideMenus(event:PointerEvent){const active=(event.target as HTMLElement).closest('details');for(const detail of document.querySelectorAll<HTMLDetailsElement>('.timeline-line-menu[open]'))if(detail!==active)detail.open=false}
function menuEscape(event:KeyboardEvent){if(event.key!=='Escape')return;for(const detail of document.querySelectorAll<HTMLDetailsElement>('.timeline-line-menu[open]')){detail.open=false;detail.querySelector<HTMLElement>('summary')?.focus()}}

type Gesture={start:Point;camera:Camera;node:string|null;delta:Point;moved:boolean}
let gesture:Gesture|null=null,pinch:{distance:number;mid:Point;camera:Camera}|null=null,pointers=new Map<number,Point>(),suppressed=false,suppressionTimer:ReturnType<typeof setTimeout>|undefined,frame=0,previousFrame=0,disposed=false,observer:ResizeObserver|undefined,motionQuery:MediaQueryList|undefined
const velocities=new Map<string,Point>(),midpoint=(a:Point,b:Point)=>({x:(a.x+b.x)/2,y:(a.y+b.y)/2}),distance=(a:Point,b:Point)=>Math.hypot(a.x-b.x,a.y-b.y)
function eventPoint(event:PointerEvent|WheelEvent){const rect=viewport.value!.getBoundingClientRect();return{x:event.clientX-rect.left,y:event.clientY-rect.top}}
function stopMotion(){cancelAnimationFrame(frame);frame=0;previousFrame=0;velocities.clear();displacements.value={}}
function spring(time:number){
  if(disposed||document.hidden){frame=0;previousFrame=0;return}
  const dt=previousFrame?(time-previousFrame)/1000:1/60;previousFrame=time;const next={...displacements.value}
  for(const [key,velocity] of velocities){const value=stepSpring(next[key]||{x:0,y:0},velocity,{x:0,y:0},dt);if(Math.hypot(value.position.x,value.position.y)<.08&&Math.hypot(value.velocity.x,value.velocity.y)<.4){delete next[key];velocities.delete(key)}else{next[key]=value.position;velocities.set(key,value.velocity)}}
  displacements.value=next;frame=velocities.size?requestAnimationFrame(spring):0;if(!frame)previousFrame=0
}
function releaseNode(){if(!gesture?.node)return;const key=gesture.node;if(reduced.value){const next={...displacements.value};delete next[key];displacements.value=next}else if(displacements.value[key]){velocities.set(key,{x:0,y:0});if(!frame)frame=requestAnimationFrame(spring)}}
function pointerDown(event:PointerEvent){
  if(event.button!==0)return;const target=event.target as HTMLElement,button=target.closest<HTMLElement>('.timeline-chapter,.timeline-overview-target'),interactive=target.closest('button,a,summary,input,select,details');if(interactive&&!button)return
  const p=eventPoint(event);pointers.set(event.pointerId,p);suppressed=false
  if(pointers.size===2){releaseNode();const[a,b]=[...pointers.values()];pinch={distance:Math.max(1,distance(a,b)),mid:midpoint(a,b),camera:{...camera.value}};gesture=null;dragging.value=true}
  else if(pointers.size===1){const node=button?.dataset.nodeKey||null;if(node)velocities.delete(node);gesture={start:p,camera:{...camera.value},node,delta:node?displacements.value[node]||{x:0,y:0}:{x:0,y:0},moved:false}}
  ;(button||viewport.value)?.setPointerCapture(event.pointerId)
}
function pointerMove(event:PointerEvent){
  if(!pointers.has(event.pointerId))return;pointers.set(event.pointerId,eventPoint(event))
  if(pinch&&pointers.size>=2){const[a,b]=[...pointers.values()],mid=midpoint(a,b),next=zoomAround(pinch.camera,distance(a,b)/pinch.distance,pinch.mid);applyCamera(panGraph(next,mid.x-pinch.mid.x,mid.y-pinch.mid.y,bounds.value,size.value));event.preventDefault();return}
  if(!gesture)return;const p=pointers.get(event.pointerId)!,dx=p.x-gesture.start.x,dy=p.y-gesture.start.y;if(!gesture.moved&&Math.hypot(dx,dy)<6)return;gesture.moved=true;dragging.value=true;animated.value=false
  if(gesture.node)displacements.value={...displacements.value,[gesture.node]:{x:Math.max(-54,Math.min(54,gesture.delta.x+dx/camera.value.scale)),y:Math.max(-64,Math.min(64,gesture.delta.y+dy/camera.value.scale))}}
  else applyCamera(panGraph(gesture.camera,dx,dy,bounds.value,size.value));event.preventDefault()
}
function pointerUp(event:PointerEvent){
  if(!pointers.has(event.pointerId))return;const moved=gesture?.moved||!!pinch;releaseNode();pointers.delete(event.pointerId);gesture=null;pinch=null;dragging.value=false
  if(moved){suppressed=true;clearTimeout(suppressionTimer);suppressionTimer=setTimeout(()=>suppressed=false,350)}
  if(pointers.size){const p=[...pointers.values()][0];gesture={start:p,camera:{...camera.value},node:null,delta:{x:0,y:0},moved:true};dragging.value=true}
}
function preventDragClick(event:MouseEvent){if(suppressed){suppressed=false;if(event.detail!==0){event.preventDefault();event.stopPropagation()}}}
function wheel(event:WheelEvent){if(event.ctrlKey||event.metaKey)return;event.preventDefault();if(Math.abs(event.deltaX)>Math.abs(event.deltaY))applyCamera(panGraph(camera.value,-event.deltaX,-event.deltaY,bounds.value,size.value));else zoom(Math.exp(-Math.max(-120,Math.min(120,event.deltaY))*.0025),eventPoint(event))}
function keyboard(event:KeyboardEvent){
  if(event.target!==viewport.value)return;const movements:Record<string,Point>={ArrowLeft:{x:80,y:0},ArrowRight:{x:-80,y:0},ArrowUp:{x:0,y:80},ArrowDown:{x:0,y:-80}}
  if(movements[event.key]){event.preventDefault();applyCamera(panGraph(camera.value,movements[event.key].x,movements[event.key].y,bounds.value,size.value))}
  else if(['+','=','-','Home','0','Escape'].includes(event.key)){event.preventDefault();if(['+','=','-'].includes(event.key))zoom(event.key==='-'?1/1.2:1.2);else if(event.key==='Escape'){stopMotion();pointers.clear();gesture=null;pinch=null;dragging.value=false}else resetView()}
}
function minimap(event:PointerEvent){const svg=(event.currentTarget as HTMLElement).querySelector('svg'),matrix=svg?.getScreenCTM();if(!svg||!matrix)return;const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;const world=p.matrixTransform(matrix.inverse());applyCamera(focusGraph(world,size.value,camera.value.scale),true)}
function motionChange(){reduced.value=!!motionQuery?.matches;if(reduced.value){stopMotion();animated.value=false}}
function pause(){if(gesture?.moved||pinch){suppressed=true;clearTimeout(suppressionTimer)}stopMotion();pointers.clear();gesture=null;pinch=null;dragging.value=false;animated.value=false}
function visibility(){if(document.hidden)pause()}
watch(()=>props.data.bookId,()=>{overview.value=null;chapterOffsets.value={};showArchived.value=false;branchOffset.value=0;pause()})
watch(()=>props.data.currentBranchId,id=>{const index=available.value.findIndex(row=>row.branch.id===id);if(index>=0)branchOffset.value=Math.floor(index/TIMELINE_BRANCH_PAGE)*TIMELINE_BRANCH_PAGE},{immediate:true})
watch(showArchived,()=>{branchOffset.value=0})
watch(()=>props.data.branches.map(branch=>branch.id+':'+branch.headRevisionId).join('|'),(current,previous)=>{const prior=new Set((previous||'').split('|')),ids=new Set(props.data.branches.filter(branch=>prior.has(branch.id+':'+branch.headRevisionId)).map(branch=>branch.id));chapterOffsets.value=Object.fromEntries(Object.entries(chapterOffsets.value).filter(([id])=>ids.has(id)));if(branchOffset.value>=available.value.length)branchOffset.value=Math.max(0,Math.floor((available.value.length-1)/TIMELINE_BRANCH_PAGE)*TIMELINE_BRANCH_PAGE)})
watch(()=>[rows.value,chapterOffsets.value,props.data.branches],async()=>{overview.value=null;pause();await nextTick();if(!disposed)measure()})
onMounted(()=>{motionQuery=matchMedia('(prefers-reduced-motion: reduce)');motionQuery.addEventListener('change',motionChange);motionChange();observer=new ResizeObserver(()=>measure());if(viewport.value)observer.observe(viewport.value);document.addEventListener('visibilitychange',visibility);document.addEventListener('pointerdown',outsideMenus);document.addEventListener('keydown',menuEscape);window.addEventListener('reader-save',pause);measure()})
onUnmounted(()=>{disposed=true;pause();observer?.disconnect();motionQuery?.removeEventListener('change',motionChange);document.removeEventListener('visibilitychange',visibility);document.removeEventListener('pointerdown',outsideMenus);document.removeEventListener('keydown',menuEscape);window.removeEventListener('reader-save',pause);clearTimeout(suppressionTimer)})
</script>

<template>
  <section class="story-timeline" aria-label="故事线与章节图">
    <div class="timeline-controls" @toggle.capture="positionMenu">
      <div class="timeline-lines" aria-label="故事线">
        <div v-for="row in rows" :key="row.branch.id" class="story-line-chip" :class="{'is-original':row.branch.id===data.originalBranchId,'is-current':row.branch.id===data.currentBranchId}" :data-line-id="row.branch.id" @mouseenter="highlight=row.branch.id" @mouseleave="highlight=''" @focusin="highlight=row.branch.id" @focusout="highlight=''">
          <button class="timeline-line-name" :aria-current="row.branch.id===data.currentBranchId?'true':undefined" @click="emit('open',row.branch)"><BookOpen v-if="row.branch.id===data.originalBranchId" :size="15" aria-hidden="true"/><Sparkles v-else :size="15" aria-hidden="true"/>{{row.branch.name}}<MapPin v-if="row.branch.id===data.currentBranchId" :size="13" aria-label="当前故事线"/><Archive v-if="row.branch.status==='archived'" :size="13" aria-label="已归档"/></button>
          <details class="timeline-line-menu">
            <summary :aria-label="row.branch.name+'的故事线操作'"><MoreHorizontal :size="18" aria-hidden="true"/></summary>
            <div class="timeline-menu-content">
              <button @click="menuAction($event,()=>locate(row.branch.id))">定位此线</button><button v-if="row.branch.forkAnchor" class="timeline-fork-link" @click="menuAction($event,()=>emit('fork',row.branch))">{{row.branch.forkLabel||'分叉位置'}}</button>
              <nav v-if="row.branch.chaptersTotal-timelineChapterStart(row.branch)>TIMELINE_CHAPTER_PAGE" class="graph-menu-pages" :aria-label="row.branch.name+'章节分页'"><span aria-live="polite">{{offset(row.branch)+1}}–{{Math.min(offset(row.branch)+TIMELINE_CHAPTER_PAGE,row.branch.chaptersTotal)}} / {{row.branch.chaptersTotal}}</span><div><button :disabled="offset(row.branch)===timelineChapterStart(row.branch)||chapterLoading" @click="menuAction($event,()=>page(row.branch,-1))"><ChevronLeft :size="16"/>前 20 章</button><button :disabled="offset(row.branch)+TIMELINE_CHAPTER_PAGE>=row.branch.chaptersTotal||chapterLoading" @click="menuAction($event,()=>page(row.branch,1))">后 20 章<ChevronRight :size="16"/></button></div></nav>
              <button :disabled="busy||!online" @click="menuAction($event,()=>emit('rename',row.branch))">重命名</button><button v-if="row.branch.id!==data.originalBranchId&&row.branch.parentBranchId" :disabled="busy||!online" @click="menuAction($event,()=>emit('status',row.branch))">{{row.branch.status==='archived'?'恢复':'归档'}}</button>
            </div>
          </details>
        </div>
      </div>
      <label v-if="archivedCount" class="timeline-archive-toggle"><input v-model="showArchived" type="checkbox">显示归档</label>
      <select v-if="available.length>TIMELINE_BRANCH_PAGE" aria-label="快速定位故事线" :value="data.currentBranchId" @change="locate(($event.target as HTMLSelectElement).value)"><option v-for="row in available" :key="row.branch.id" :value="row.branch.id">{{row.branch.name}}</option></select>
    </div>
    <div ref="viewport" class="timeline-viewport" :class="{'is-dragging':dragging,'has-highlight':!!highlight,'reduced-motion':reduced}" role="region" aria-label="故事图" tabindex="0" :data-camera-x="camera.x" :data-camera-y="camera.y" :data-camera-scale="camera.scale" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp" @lostpointercapture="pointerUp" @click.capture="preventDragClick" @wheel="wheel" @keydown="keyboard" @dblclick="!($event.target as HTMLElement).closest('button')&&resetView()">
      <div ref="canvas" class="timeline-canvas" :class="{'camera-animated':animated,'timeline-overview':camera.scale<=.28}" :style="{transform}">
        <svg class="timeline-trunk-edges" :width="bounds.width" :height="bounds.height" aria-hidden="true"><path v-for="link in links.trunks" :key="link.id" :d="link.path" :class="{'is-highlighted':link.active,'is-ai':link.ai}"/></svg>
        <svg class="timeline-edges" :width="bounds.width" :height="bounds.height" aria-hidden="true"><path v-for="link in links.forks" :key="link.id" :d="link.path" :data-source="link.source" :data-target="link.target" :class="{'is-highlighted':link.active}" pathLength="1"/></svg>
        <article v-for="(row,rowIndex) in rows" :key="row.branch.id" class="timeline-row" :class="{'is-original':row.branch.id===data.originalBranchId,'is-current':row.branch.id===data.currentBranchId,'is-archived':row.branch.status==='archived'}" :data-line-id="row.branch.id" :aria-label="row.branch.name">
          <div class="timeline-chapter-area" :style="{'--timeline-indent':lanes.get(row.branch.id)?.column||0}">
            <div class="timeline-chapters" role="list" :aria-label="row.branch.name+'的章节'">
              <div v-for="(chapter,index) in chapters(row.branch)" :key="chapter.id" class="timeline-node" :data-node-key="nodeKey(row.branch.id,chapter.id)" role="listitem">
                <button :data-chapter-id="chapter.id" :data-node-key="nodeKey(row.branch.id,chapter.id)" :disabled="!chapter.readable" class="timeline-chapter" :class="{'is-ai':chapter.source==='ai','is-reading':reading(row.branch,chapter),'is-highlighted':highlighted.has(nodeKey(row.branch.id,chapter.id))}" :style="hitStyle(row.branch,chapter)" :tabindex="camera.scale<=.28?-1:0" :aria-hidden="camera.scale<=.28?true:undefined" :aria-current="reading(row.branch,chapter)?'location':undefined" :aria-label="row.branch.name+'，'+timelineChapterLabel(chapter)+'，'+(chapter.source==='ai'?'AI 创作':'原文')+'，'+(chapter.readable?'打开阅读':'暂不可阅读')" @mouseenter="highlight=row.branch.id" @mouseleave="highlight=''" @focus="highlight=row.branch.id;ensureVisible(nodeKey(row.branch.id,chapter.id),$event)" @blur="highlight=''" @click="emit('open',row.branch,chapter)">
                  <span class="timeline-chapter-face"><span class="timeline-node-orbit" aria-hidden="true"><span class="timeline-node-core"><Sparkles v-if="chapter.source==='ai'" :size="17"/><BookOpen v-else :size="17"/></span></span>
                  <strong>{{timelineChapterLabel(chapter)}}</strong><span class="timeline-node-footer"><span>{{String(chapter.position+1).padStart(2,'0')}}</span><MapPin v-if="reading(row.branch,chapter)" :size="12" aria-label="阅读位置"/></span>
                  </span>
                </button>
              </div>
              <div v-if="!chapters(row.branch).length" class="timeline-chapter-empty"><span>{{chapterLoading?'正在加载…':row.branch.chaptersTotal?'章节未加载':'暂无章节'}}</span><button v-if="row.branch.chaptersTotal&&!chapterLoading" @click="emit('loadChapters',offset(row.branch))">重新加载</button></div>
            </div>
          </div>
        </article>
      </div>
      <div v-if="camera.scale<=.28" class="timeline-overview-layer" :style="{transform:`translate(${camera.x}px,${camera.y}px)`}">
        <button v-for="target in overviewTargets" :key="target.id" class="timeline-overview-target" :style="{left:(target.x-22)+'px',top:(target.y-22)+'px'}" :aria-label="target.items.length+' 个章节，选择阅读'" @click="overview=target.items.map(point=>point.item)"><span aria-hidden="true">{{target.items.length}}</span></button>
      </div>
      <button class="graph-minimap" aria-label="故事图总览，点击定位" @pointerup.stop="minimap" @click="$event.detail===0&&resetView()"><svg :viewBox="miniView" aria-hidden="true"><path v-for="link in [...links.trunks,...links.forks]" :key="link.id" :d="link.path"/><circle v-for="(p,key) in bases" :key="key" :cx="p.x" :cy="p.y" r="8"/><rect :x="-camera.x/camera.scale" :y="-camera.y/camera.scale" :width="size.width/camera.scale" :height="size.height/camera.scale" rx="12"/></svg></button>
      <div class="graph-tools" @pointerdown.stop><button class="icon-button" aria-label="缩小故事图" title="缩小" :disabled="camera.scale<=MIN_SCALE" @click="zoom(1/1.2)"><Minus :size="18"/></button><output aria-label="图谱缩放">{{Math.round(camera.scale*100)}}%</output><button class="icon-button" aria-label="放大故事图" title="放大" :disabled="camera.scale>=MAX_SCALE" @click="zoom(1.2)"><Plus :size="18"/></button><span class="graph-tool-divider"/><button class="icon-button" aria-label="重置故事图" title="重置视图" @click="resetView"><Maximize2 :size="18"/></button><button class="icon-button" aria-label="定位当前线" title="定位当前线" @click="locate(data.currentBranchId)"><MapPin :size="18"/></button></div>
    </div>
    <nav v-if="available.length>TIMELINE_BRANCH_PAGE" class="timeline-branch-pagination" aria-label="故事线分页"><button :disabled="branchOffset===0" @click="branchOffset=Math.max(0,branchOffset-TIMELINE_BRANCH_PAGE)"><ChevronLeft :size="16"/>上一组</button><span aria-live="polite">{{branchOffset+1}}–{{Math.min(branchOffset+TIMELINE_BRANCH_PAGE,available.length)}} / {{available.length}}</span><button :disabled="branchOffset+TIMELINE_BRANCH_PAGE>=available.length" @click="branchOffset+=TIMELINE_BRANCH_PAGE">下一组<ChevronRight :size="16"/></button></nav>
    <Sheet v-if="overview" title="选择章节" @close="overview=null"><div class="world-graph-choice-list"><button v-for="entry in overview" :key="nodeKey(entry.branch.id,entry.chapter.id)" class="world-graph-choice" :disabled="!entry.chapter.readable" :aria-label="entry.branch.name+'，'+timelineChapterLabel(entry.chapter)+'，打开阅读'" @click="chooseOverview(entry.branch,entry.chapter)"><strong>{{timelineChapterLabel(entry.chapter)}}</strong><small>{{entry.branch.name}} · {{entry.chapter.position+1}}</small></button></div></Sheet>
  </section>
</template>
