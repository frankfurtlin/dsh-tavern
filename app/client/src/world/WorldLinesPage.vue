<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ArrowLeft,Minus,Plus,LocateFixed,Maximize,GitBranch,Compass,BookOpen,CornerUpLeft,Archive,Trash2} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
import ComparisonLauncher from '../components/ComparisonLauncher.vue'
import {worldApi} from './api'
import {worldMutation,clearWorldMutation,clearConfirmedWorldMutation} from './mutation-intent'
import type {WorldGraph,WorldInfo,WorldLine,WorldCheckpoint} from './types'
import {layoutWorldGraph,projectWorldGraph,worldTargetEdges,viewportWorldGraph} from './graph'
import type {GraphNode,WorldGraphTarget} from './graph'
import {currentGraphLine,checkpointReadingLine} from './line-navigation'
import {ApiError,api} from '../api'
import {useRequestScope} from '../platform/request-scope'
const route=useRoute(),router=useRouter(),capture=useRequestScope(),world=ref<WorldInfo|null>(null),graph=ref<WorldGraph>({lines:[],nodes:[]}),expanded=ref(new Set<string>()),camera=ref({x:30,y:80,zoom:1}),container=ref<HTMLElement|null>(null),selected=ref<WorldCheckpoint|null>(null),error=ref(''),managing=ref(''),name=ref(''),busy=ref(false),managementError=ref(''),stale=ref(false),refreshed=ref(false)
const showArchived=ref(false)
const displayedGraph=computed(()=>{const lines=graph.value.lines.filter(l=>showArchived.value||l.status!=='archived'||l.id===currentLine.value);const ids=new Set(lines.map(l=>l.id));return {lines,nodes:graph.value.nodes.filter(n=>ids.has(n.lineId))}})
const nodes=computed(()=>layoutWorldGraph(displayedGraph.value,expanded.value)),targets=computed(()=>projectWorldGraph(nodes.value,camera.value.zoom)),edges=computed(()=>worldTargetEdges(nodes.value,targets.value)),currentLine=computed(()=>currentGraphLine(graph.value,String(route.query.line||''),world.value?.activeLineId||null)),activeHeads=computed(()=>new Set(graph.value.lines.filter(l=>l.id===currentLine.value).map(l=>l.headCheckpointId)))
const overviewSelection=ref<WorldGraphTarget|null>(null)
const viewportSize=ref({width:600,height:500}),visibleGraph=computed(()=>viewportWorldGraph(targets.value,edges.value,camera.value,viewportSize.value))
const listOpen=ref(false),listPage=ref(0),listFilter=ref(''),listLine=ref('')
const filteredCheckpoints=computed(()=>displayedGraph.value.nodes.filter(cp=>(!listLine.value||cp.lineId===listLine.value)&&(!listFilter.value||`${cp.title} ${cp.action} ${cp.sequence}`.toLocaleLowerCase().includes(listFilter.value.toLocaleLowerCase()))))
const listPages=computed(()=>Math.max(1,Math.ceil(filteredCheckpoints.value.length/50))),listedCheckpoints=computed(()=>filteredCheckpoints.value.slice(listPage.value*50,listPage.value*50+50))
watch([listFilter,listLine],()=>{listPage.value=0})
function closeList(){selectionVersion++;listOpen.value=false}
function openList(){overviewSelection.value=null;closeHistory();closeManagement();listOpen.value=true}
const returnStory=ref('')
function viewLink(view:'play'|'read',lineId:string,checkpoint?:string){return returnStory.value?{path:`/worlds/${route.params.worldId}/${view}`,query:{line:lineId,story:returnStory.value,...(checkpoint?{checkpoint}:{})}}:`/worlds/${route.params.worldId}/${view}?line=${lineId}${checkpoint?'&checkpoint='+checkpoint:''}`}
const backTo=computed(()=>currentLine.value?viewLink('play',currentLine.value):world.value?.status==='active'?`/worlds/new?world=${route.params.worldId}`:'/worlds'),readingLine=computed(()=>selected.value?checkpointReadingLine(graph.value,selected.value.id,currentLine.value):''),canBranch=computed(()=>world.value?.status==='active')
const managementLine=computed(()=>graph.value.lines.find(line=>line.id===managing.value))
let selectionVersion=0,loadVersion=0,managementEpoch=0,managementBusy=false
function closeHistory(){selectionVersion++;selected.value=null}
function closeManagement(){selectionVersion++;managementEpoch++;managing.value='';if(managementBusy)busy.value=false;managementBusy=false;managementError.value='';stale.value=false;refreshed.value=false}
function openManagement(line:WorldGraph['lines'][number]){overviewSelection.value=null;closeList();closeHistory();closeManagement();clearTimeout(timer);cameraGeneration++;managing.value=line.id;name.value=line.name}
function nodeLabel(node:GraphNode){return node.title+(node.kind==='group'?'，展开回合':'，查看历史节点')}
function targetCount(target:WorldGraphTarget){return target.nodes.reduce((count,node)=>count+node.members.length,0)}
function targetLabel(target:WorldGraphTarget){return target.nodes.length>1?`${targetCount(target)} 个历史节点，选择查看`:nodeLabel(target.nodes[0]!)}
function isCurrent(target:WorldGraphTarget){return target.nodes.some(node=>node.members.some(id=>activeHeads.value.has(id)))}
function chooseNode(node:GraphNode){overviewSelection.value=null;if(node.kind==='group'){closeHistory();closeManagement();expanded.value.add(node.id)}else void select(node.id)}
function openTarget(target:WorldGraphTarget){closeList();if(target.nodes.length===1){chooseNode(target.nodes[0]!);return}closeHistory();closeManagement();overviewSelection.value=target}
function focusTarget(target:WorldGraphTarget,event?:FocusEvent){
  if(event&&!(event.target as HTMLElement).matches(':focus-visible'))return
  if(!container.value)return
  const left=camera.value.x+target.x-target.width/2,top=camera.value.y+target.y-target.height/2
  const dx=left<12?12-left:Math.min(0,container.value.clientWidth-12-left-target.width)
  const dy=top<12?12-top:Math.min(0,container.value.clientHeight-12-top-target.height)
  if(dx||dy)pan(dx,dy)
}
let timer:ReturnType<typeof setTimeout>|undefined
const pointers=new Map<number,{x:number;y:number}>()
let gesture:{x:number;y:number;camera:typeof camera.value;distance:number;pinch:boolean}|null=null,moved=false,suppressClickUntil=0,resizeObserver:ResizeObserver|undefined
let cameraGeneration=0,cameraSave:Promise<void>|null=null
function resetGesture(){const [a,b]=[...pointers.values()];if(!a){gesture=null;return}gesture={x:b?(a.x+b.x)/2:a.x,y:b?(a.y+b.y)/2:a.y,camera:{...camera.value},distance:b?Math.hypot(a.x-b.x,a.y-b.y):0,pinch:!!b}}
function down(e:PointerEvent){if(e.button!==0||pointers.size>=2)return;if(!pointers.size){moved=false;suppressClickUntil=0}pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});resetGesture();if(pointers.size===2){moved=true;for(const id of pointers.keys())container.value?.setPointerCapture?.(id)}else{const target=(e.target as HTMLElement).closest?.('button');(target||container.value)?.setPointerCapture?.(e.pointerId)}}
function move(e:PointerEvent){if(!pointers.has(e.pointerId)||!gesture)return;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});const [a,b]=[...pointers.values()];if(!a)return
  const x=b?(a.x+b.x)/2:a.x,y=b?(a.y+b.y)/2:a.y,dx=x-gesture.x,dy=y-gesture.y
  if(!moved&&Math.hypot(dx,dy)<4)return;moved=true;e.preventDefault();container.value?.setPointerCapture?.(e.pointerId)
  if(b&&gesture.pinch&&gesture.distance>0){const rect=container.value?.getBoundingClientRect?.(),ox=gesture.x-(rect?.left||0),oy=gesture.y-(rect?.top||0),z=Math.min(1.8,Math.max(.12,gesture.camera.zoom*Math.hypot(a.x-b.x,a.y-b.y)/gesture.distance)),ratio=z/gesture.camera.zoom
    camera.value={x:ox-(ox-gesture.camera.x)*ratio+dx,y:oy-(oy-gesture.camera.y)*ratio+dy,zoom:z}
  }else camera.value={...gesture.camera,x:gesture.camera.x+dx,y:gesture.camera.y+dy}
}
function pan(x:number,y:number){camera.value.x+=x;camera.value.y+=y;saveCamera()}
function up(e:PointerEvent){if(!pointers.has(e.pointerId))return;pointers.delete(e.pointerId);if(container.value?.hasPointerCapture?.(e.pointerId))container.value.releasePointerCapture(e.pointerId);if(moved){suppressClickUntil=Date.now()+500;saveCamera()}resetGesture()}
function lostPointer(e:PointerEvent){if(!container.value?.hasPointerCapture?.(e.pointerId))up(e)}
function preventReleaseClick(e:MouseEvent){if(e.detail!==0&&(moved||Date.now()<suppressClickUntil)){e.preventDefault();e.stopImmediatePropagation()}}
function zoom(factor:number,x?:number,y?:number){const c=camera.value,w=container.value?.clientWidth||600,h=container.value?.clientHeight||500,px=x??w/2,py=y??h/2,z=Math.min(1.8,Math.max(.12,c.zoom*factor));camera.value={x:px-(px-c.x)*z/c.zoom,y:py-(py-c.y)*z/c.zoom,zoom:z};saveCamera()}
function wheel(e:WheelEvent){e.preventDefault();const rect=container.value?.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.0015),e.clientX-(rect?.left||0),e.clientY-(rect?.top||0))}
function fit(){if(!nodes.value.length||!container.value)return;const xs=nodes.value.map(n=>n.x),ys=nodes.value.map(n=>n.y),w=Math.max(...xs)-Math.min(...xs)+200,h=Math.max(...ys)-Math.min(...ys)+180,z=Math.min(1,(container.value.clientWidth-80)/w,(container.value.clientHeight-80)/h);camera.value={x:40-Math.min(...xs)*z+80*z,y:40-Math.min(...ys)*z+30*z,zoom:Math.max(.12,z)};saveCamera()}
function locate(){const node=nodes.value.find(n=>n.members.some(id=>activeHeads.value.has(id)));if(node&&container.value){camera.value={x:container.value.clientWidth/2-node.x,y:container.value.clientHeight/2-node.y-50,zoom:1};saveCamera()}}
function saveCamera(){
  clearTimeout(timer);const generation=++cameraGeneration;if(managing.value)return
  timer=setTimeout(async()=>{
    const scope=capture();await cameraSave
    if(!scope.valid()||generation!==cameraGeneration||managing.value)return
    const line=graph.value.lines.find(l=>l.id===currentLine.value),worldId=world.value?.id;if(!line||!worldId)return
    const pending=worldApi<WorldLine>('/worlds/'+worldId+'/lines/'+line.id,{method:'PATCH',body:{expectedVersion:line.version,camera:{...camera.value}}})
      .then(saved=>{if(scope.valid()&&graph.value.lines.includes(line)){line.version=saved.version;line.camera=saved.camera}})
      .catch(()=>{/* A concurrent head change must never be overwritten by graph movement. */})
    cameraSave=pending;await pending;if(cameraSave===pending)cameraSave=null
  },700)
}
async function select(id:string){overviewSelection.value=null;closeList();closeManagement();const version=++selectionVersion,scope=capture(),worldId=world.value?.id,lineId=currentLine.value;if(!worldId)return;const valid=()=>scope.valid()&&version===selectionVersion&&worldId===world.value?.id&&lineId===currentLine.value;error.value='';try{const cp=await worldApi<WorldCheckpoint>('/worlds/'+worldId+'/checkpoints/'+id,{cache:true});if(valid())selected.value=cp}catch(e){if(valid())error.value=(e as Error).message}}
async function fork(mode:string){if(busy.value||!selected.value||!world.value)return;selectionVersion++;const scope=capture();busy.value=true;try{const slot='fork:'+world.value.id;await clearConfirmedWorldMutation(slot,scope.valid);if(!scope.valid())return;const line=await worldMutation<WorldLine>(slot,'/worlds/'+world.value.id+'/fork',{checkpointId:selected.value.id,mode,name:mode==='rechoose'?'另一种选择':'从这里继续'},scope.valid);if(scope.valid()){await clearWorldMutation(slot,scope.valid);if(scope.valid())await router.push(viewLink('play',line.id))}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
async function refreshManagement(){
  const worldId=world.value?.id,lineId=managing.value;if(!worldId||!lineId||busy.value||!stale.value)return
  const scope=capture(),epoch=++managementEpoch,valid=()=>scope.valid()&&epoch===managementEpoch&&worldId===world.value?.id&&lineId===managing.value
  busy.value=true;managementBusy=true
  try{const fresh=await worldApi<WorldLine>('/worlds/'+worldId+'/lines/'+lineId);if(valid()){graph.value.lines=graph.value.lines.map(line=>line.id===lineId?{...line,name:fresh.name,status:fresh.status,version:fresh.version,camera:fresh.camera,headCheckpointId:fresh.headCheckpointId}:line);managementError.value='';stale.value=false;refreshed.value=true}}
  catch(e){if(valid())managementError.value=(e as Error).message}finally{if(valid()){busy.value=false;managementBusy=false}}
}
async function manage(action:string){
  const worldId=world.value?.id,lineId=managing.value;if(!worldId||!managementLine.value||busy.value||stale.value)return
  selectionVersion++;const scope=capture(),epoch=++managementEpoch,valid=()=>scope.valid()&&epoch===managementEpoch&&worldId===world.value?.id&&lineId===managing.value
  busy.value=true;managementBusy=true;error.value='';managementError.value=''
  try{
    // A local camera save may have committed while its response is still in flight.
    // Use its returned version before the explicit management write.
    if(cameraSave)await cameraSave;if(!valid())return
    const line=managementLine.value;if(!line)return
    await worldApi('/worlds/'+worldId+'/lines/'+line.id,{method:action==='delete'?'DELETE':'PATCH',body:action==='delete'?undefined:{expectedVersion:line.version,...(action==='rename'?{name:name.value}:{status:line.status==='archived'?'active':'archived'})}})
    if(!valid())return;closeManagement();const closedEpoch=managementEpoch,loaded=await load();if(!loaded||!scope.valid()||closedEpoch!==managementEpoch)return
    if(action==='delete'&&selected.value&&!readingLine.value)selected.value=null
    if(route.query.line&&String(route.query.line)!==currentLine.value)await router.replace({path:route.path,query:{...route.query,line:currentLine.value||undefined}})
  }catch(e){if(valid()){managementError.value=(e as Error).message;stale.value=e instanceof ApiError&&e.code==='VERSION_CONFLICT'}}finally{if(valid()){busy.value=false;managementBusy=false}}
}
async function load(){returnStory.value='';const version=++loadVersion,scope=capture(),valid=()=>scope.valid()&&version===loadVersion;try{const [info,data]=await Promise.all([worldApi<WorldInfo>('/worlds/'+route.params.worldId,{cache:true}),worldApi<WorldGraph>('/worlds/'+route.params.worldId+'/graph',{cache:true})]);if(!valid())return false;world.value=info;graph.value=data;const saved=data.lines.find(l=>l.id===currentLine.value)?.camera;if(saved?.zoom&&saved.zoom>=.65)camera.value={x:saved.x||0,y:saved.y||0,zoom:saved.zoom};else requestAnimationFrame(locate);const origin=String(route.query.story||''),line=data.lines.find(item=>item.id===currentLine.value)
  if(/^(book|world)-[a-f0-9]{32}$/.test(origin)&&line){try{await api('/stories/'+origin+'/context/validate',{method:'POST',body:{storyId:origin,mode:'read',perspective:'reader',entryOrigin:'worlds',worldId:info.id,worldLineId:line.id,checkpointId:line.headCheckpointId}});if(!valid())return false;returnStory.value=origin}catch{if(!valid())return false}}
  return true}catch(e){if(valid())error.value=(e as Error).message;return false}}
watch(()=>[route.params.worldId,route.query.line,route.query.story],()=>{closeList();overviewSelection.value=null;pointers.clear();gesture=null;closeHistory();closeManagement();void load()})
onMounted(()=>{const measure=()=>{if(container.value)viewportSize.value={width:container.value.clientWidth,height:container.value.clientHeight}};measure();if(typeof ResizeObserver!=='undefined'&&container.value){resizeObserver=new ResizeObserver(measure);resizeObserver.observe(container.value)}void load()})
onUnmounted(()=>{loadVersion++;selectionVersion++;managementEpoch++;cameraGeneration++;clearTimeout(timer);resizeObserver?.disconnect();pointers.clear()})
</script>
<template>
  <div class="world-lines">
    <div class="world-lines-heading">
      <RouterLink :to="backTo" class="world-back"><ArrowLeft :size="18"/>回到故事</RouterLink>
      <h1>{{world?.name||'世界'}} · 故事线</h1>
    </div>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <div class="world-graph-shell">
      <div class="world-graph-toolbar">
        <span><GitBranch :size="18"/>{{graph.lines.length}} 条世界线</span>
        <div>
          <button :aria-pressed="showArchived" @click="showArchived=!showArchived">{{showArchived?'隐藏归档':'显示归档'}}</button>
          <button class="icon-button" aria-label="缩小图谱" @click="zoom(.8)"><Minus :size="18"/></button>
          <span>{{Math.round(camera.zoom*100)}}%</span>
          <button class="icon-button" aria-label="放大图谱" @click="zoom(1.25)"><Plus :size="18"/></button>
          <button class="icon-button" aria-label="查看全部世界线" @click="fit"><Maximize :size="18"/></button>
          <button class="icon-button" aria-label="定位当前世界线" @click="locate"><LocateFixed :size="19"/></button>
          <button class="world-graph-list-toggle" @click="openList">节点列表</button>
          <ComparisonLauncher v-if="world" :story-id="returnStory||'world-'+world.id" kind="world" :resource-id="world.id" :targets="graph.lines.map(line=>({id:line.id,name:line.name,nodeId:line.headCheckpointId}))" :current-line-id="currentLine"/>
        </div>
      </div>
      <div ref="container" class="world-graph-viewport" tabindex="0" aria-label="可拖动和缩放的世界线图谱"
        @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up" @lostpointercapture="lostPointer" @click.capture="preventReleaseClick" @wheel="wheel"
        @keydown.left.prevent="pan(60,0)" @keydown.right.prevent="pan(-60,0)"
        @keydown.up.prevent="pan(0,60)" @keydown.down.prevent="pan(0,-60)" @keydown.home.prevent="fit">
        <div class="world-graph-camera" :style="{transform:`translate(${camera.x}px,${camera.y}px)`}">
          <svg class="world-graph-links" aria-hidden="true"><path v-for="edge in visibleGraph.edges" :key="edge.id" :d="edge.path"/></svg>
          <button v-for="target in visibleGraph.targets" :key="target.id" class="world-graph-node world-graph-hit-target"
            :class="{current:isCurrent(target),overview:target.overview,group:target.nodes.length>1||target.nodes[0]?.kind==='group',fork:target.nodes[0]?.kind==='fork'||target.nodes[0]?.kind==='correction'}"
            :style="{left:(target.x-target.width/2)+'px',top:(target.y-target.height/2)+'px',width:target.width+'px',height:target.height+'px'}"
            :data-world-target-kind="target.nodes.length>1?'cluster':target.overview?'overview':'node'"
            :aria-label="targetLabel(target)" :title="targetLabel(target)" @click="openTarget(target)" @focus="focusTarget(target,$event)">
            <template v-if="target.overview">
              <span v-if="target.nodes.length>1" class="world-overview-count" aria-hidden="true">{{targetCount(target)}}</span>
              <span v-else class="world-overview-turn" aria-hidden="true">{{target.nodes[0]?.sequence===0?'始':target.nodes[0]?.sequence}}</span>
            </template>
            <span v-else class="world-graph-node-face" aria-hidden="true" :style="{transform:`translate(-50%,-50%) scale(${camera.zoom})`}">
              <span class="world-node-dot"></span>
              <small>{{graph.lines.find(l=>l.id===target.nodes[0]?.lineId)?.name||'共享前文'}}</small>
              <strong>{{target.nodes[0]?.title}}</strong>
              <span>{{target.nodes[0]?.kind==='group'?target.nodes[0]?.members.length+' 个回合':target.nodes[0]?.sequence===0?'起点':'回合 '+target.nodes[0]?.sequence}}</span>
            </span>
          </button>
        </div>
        <div v-if="!nodes.length" class="empty">这里还没有世界线</div>
      </div>
      <div class="world-graph-line-list">
        <button v-for="l in graph.lines" :key="l.id" :class="{current:l.id===currentLine}" @click="openManagement(l)">
          <span class="world-node-dot"></span>{{l.name}}<small v-if="l.status==='archived'">已归档</small>
        </button>
      </div>
    </div>
    <Sheet v-if="listOpen" title="历史节点" @close="closeList">
      <div class="world-graph-list-filter"><label>查找节点<input v-model="listFilter" type="search"></label><label>故事线<select v-model="listLine"><option value="">全部故事线</option><option v-for="line in graph.lines" :key="line.id" :value="line.id">{{line.name}}</option></select></label></div>
      <p class="muted" role="status">{{filteredCheckpoints.length}} 个节点 · {{listPage+1}} / {{listPages}}</p>
      <div class="world-graph-choice-list"><button v-for="cp in listedCheckpoints" :key="cp.id" class="world-graph-choice" :aria-label="cp.title+'，查看历史节点'" :aria-current="activeHeads.has(cp.id)?'step':undefined" @click="select(cp.id)"><strong>{{cp.title}}</strong><small>{{graph.lines.find(line=>line.id===cp.lineId)?.name||'共享前文'}} · 回合 {{cp.sequence}}<template v-if="activeHeads.has(cp.id)"> · 当前</template></small></button></div>
      <nav class="world-graph-list-pages" aria-label="节点列表分页"><button :disabled="listPage===0" @click="listPage--">上一页</button><button :disabled="listPage>=listPages-1" @click="listPage++">下一页</button></nav>
    </Sheet>
    <Sheet v-if="overviewSelection" title="选择历史节点" @close="overviewSelection=null">
      <div class="world-graph-choice-list">
        <button v-for="node in overviewSelection.nodes" :key="node.id" class="world-graph-choice" :aria-label="nodeLabel(node)" @click="chooseNode(node)">
          <strong>{{node.title}}</strong>
          <small>{{graph.lines.find(l=>l.id===node.lineId)?.name||'共享前文'}} · {{node.kind==='group'?node.members.length+' 个回合':'回合 '+node.sequence}}</small>
        </button>
      </div>
    </Sheet>
    <Sheet v-if="selected" :title="selected.title" @close="closeHistory"><small>{{selected.state?.timeLabel}} · 回合 {{selected.sequence}}</small><p v-if="selected.action" class="world-player-action">{{selected.action}}</p><div class="world-prose">{{selected.text||selected.summary}}</div><div class="stack divider"><RouterLink v-if="readingLine" :to="viewLink('read',readingLine,selected.id)" class="inline-link"><BookOpen :size="18"/>阅读这里</RouterLink><button :disabled="busy||!canBranch" @click="fork('continue')"><Compass :size="18"/>从这里继续</button><button v-if="selected.parentCheckpointId" :disabled="busy||!canBranch" @click="fork('rechoose')"><CornerUpLeft :size="18"/>改选这一步</button></div></Sheet><Sheet v-if="managing" title="管理世界线" @close="closeManagement"><p v-if="managementError" class="error" role="alert">{{managementError}}</p><button v-if="stale" :disabled="busy" @click="refreshManagement">载入最新信息</button><small v-if="refreshed" role="status">最新名称：{{managementLine?.name}} · {{managementLine?.status==='archived'?'已归档':'未归档'}}</small><label>名称<input v-model="name" maxlength="120"></label><button :disabled="busy||stale||!name.trim()" @click="manage('rename')">保存名称</button><div class="stack divider"><RouterLink :to="viewLink('play',managing)" class="inline-link">打开这条线</RouterLink><button :disabled="busy||stale" @click="manage('archive')"><Archive :size="18"/>{{graph.lines.find(l=>l.id===managing)?.status==='archived'?'恢复':'归档'}}</button><details><summary class="danger">删除世界线</summary><p>其他世界线的共享历史会保留。</p><button class="danger" :disabled="busy||stale" @click="manage('delete')"><Trash2 :size="18"/>确认删除</button></details></div></Sheet></div></template>
