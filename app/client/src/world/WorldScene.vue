<script setup lang="ts">
import {ref,computed,watch,onUnmounted,nextTick} from 'vue'
import {useRouter} from 'vue-router'
import {Compass,MapPin,Users,Package,MoveUpRight,ArrowUp,Square,Plus,Minus,Maximize,MessageCircle,X,BookOpen,Clock3,ChevronDown,ChevronUp,LoaderCircle} from 'lucide-vue-next'
import {Viewer} from '@photo-sphere-viewer/core'
import {MarkersPlugin} from '@photo-sphere-viewer/markers-plugin'
import '@photo-sphere-viewer/core/index.css'
import '@photo-sphere-viewer/markers-plugin/index.css'
import './scene.css'
import './progressive.css'
import {useWorld} from './store'
import {useApp} from '../stores/app'
import {worldApi,exportWorldCharacterCard} from './api'
import {authorizationHeaders,newKey} from '../api'
import {backendBase,cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {worldMutation,clearWorldMutation,pendingWorldMutation} from './mutation-intent'
import {interpretInput} from './action-intent'
import {sceneDialogueSegments} from './scene-dialogue'
import {sceneFeedback,sceneCooldownRemaining} from './scene-feedback'
import {imageBound,localPoints,picturePoints,pictureExits,mapExits,inspectingPicture} from './scene-grounding'
import {PanoramaMediaController} from './panorama-media'
import SceneReply from './SceneReply.vue'
import SceneSavedImages from './SceneSavedImages.vue'
import CharacterAvatar from '../components/CharacterAvatar.vue'
import PublicStreamText from '../components/PublicStreamText.vue'
import type {SceneView,ScenePoint} from './scene-types'
import type {WorldEntity} from './types'

const emit=defineEmits<{text:[];details:[];draft:[id:string];history:[]}>()
const store=useWorld(),app=useApp(),capture=useRequestScope(()=>[store.world?.id,store.line?.id]),router=useRouter()
const scene=ref<SceneView|null>(null),canvas=ref<HTMLElement|null>(null),prose=ref<HTMLElement|null>(null),input=ref<HTMLTextAreaElement|null>(null)
const loading=ref(false),moving=ref(false),error=ref(''),selected=ref<WorldEntity|null>(null),expanded=ref(false),tab=ref<'map'|'people'|'items'>('map'),configured=ref(true),viewReady=ref(false)
const interactionsReady=ref(false),mediaError=ref('')
const region=ref<ScenePoint|null>(null),cameraYaw=ref(0),partnerId=ref('')
const bindingNotice=computed(()=>scene.value?.image?.missingCharacterIds?.length?'部分人物仍在确认':'部分通路仍在确认，可通过小地图前往')
const visualPending=computed(()=>loading.value||Boolean(scene.value&&inspectingPicture(scene.value)))
const feedback=computed(()=>sceneFeedback(scene.value,visualPending.value,progressClock.value))
const cooldownRemaining=computed(()=>sceneCooldownRemaining(scene.value,progressClock.value))
const pendingInspection=ref(false),confirmRegenerate=ref(false),exportingCard=ref(false),cardNotice=ref('')
const sourceSceneId=ref('')
const stoppingScene=ref(false),progressClock=ref(Date.now())
const progressTicker=setInterval(()=>{progressClock.value=Date.now()},1000)
const elapsedLabel=computed(()=>{
  const started=Date.parse(scene.value?.progress?.startedAt||'')
  const seconds=Number.isFinite(started)?Math.max(0,Math.floor((progressClock.value-started)/1000)):scene.value?.progress?.elapsedSeconds||0
  return seconds>=60?`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`:`${seconds} 秒`
})
const progressSteps=[['environment-image','绘制环境'],['environment-review','确认环境'],['actor-image','绘制人物'],['actor-review','确认交互']]
async function stopScene(){
  if(stoppingScene.value||!scene.value||!store.world||!store.line||!head.value)return
  const valid=viewScope(),world=store.world.id,line=store.line.id,cp=head.value.id,sceneId=scene.value.id
  stoppingScene.value=true;error.value=''
  try{
    const value=await worldApi<SceneView>('/worlds/'+world+'/lines/'+line+'/scene/stop',{method:'POST',key:newKey(),body:{sceneId,expectedCheckpointId:cp}})
    if(!valid())return
    clearTimeout(timer);acceptScene(value);loading.value=false
    if(value.preview)await display(value,valid)
  }catch(e){if(valid()){error.value=(e as Error).message;poll(sceneId,valid)}}
  finally{stoppingScene.value=false}
}
const chatting=computed(()=>selected.value?.type==='character')
const conversations=computed(()=>(scene.value?.conversations||[]).filter(entry=>entry.partnerId===selected.value?.id).map(entry=>({...entry,segments:entry.segments?.length?entry.segments:sceneDialogueSegments(entry.reply,characters.value,entry.partnerId)})))
const chatTask=computed(()=>store.task?.result.interactionMode==='dialogue'&&store.task.result.partnerId===selected.value?.id?store.task:null)
const pendingChat=computed(()=>chatTask.value&&!conversations.value.some(entry=>entry.turnId===chatTask.value?.result.turnId)?chatTask.value:null)
const liveSegments=computed(()=>store.drafts.find(d=>d.taskId===pendingChat.value?.id)?.draftSegments||sceneDialogueSegments(pendingChat.value?.stream.text||'',characters.value,selected.value?.id,store.running))
const responses=computed(()=>{
  const entries=[...(scene.value?.responses||[])]
  for(const draft of store.drafts.filter(d=>d.parentCheckpointId===head.value?.id&&d.draftText)){
    if(!entries.some(e=>e.id===draft.id))entries.push({id:draft.id,taskId:draft.taskId,text:draft.draftText,segments:draft.draftSegments||undefined,draft:true})
  }
  const task=store.task
  if(task?.stream.text&&task.state!=='succeeded'&&task.result.interactionMode!=='dialogue'&&!entries.some(e=>e.taskId===task.id))entries.push({id:task.id,taskId:task.id,text:task.stream.text,draft:true})
  return entries.map(e=>({...e,segments:('segments' in e&&e.segments?.length)?e.segments:sceneDialogueSegments(e.text,characters.value,e.partnerId,store.running&&e.taskId===task?.id)})).filter(e=>e.segments.some(p=>p.kind==='speech'))
})
const ambientReplies=computed(()=>responses.value.filter(e=>e.segments.some(p=>p.speakerId===selected.value?.id)&&(!pendingChat.value||e.id!==pendingChat.value.result.turnId&&(!e.taskId||e.taskId!==pendingChat.value.id))&&!conversations.value.some(c=>c.turnId===e.id)))
const seenResponses=new Set<string>()
const narrativeSegments=computed(()=>{
  if(store.running&&store.task?.stream.text)return sceneDialogueSegments(store.task.stream.text,characters.value,undefined,true)
  const saved=scene.value?.responses?.find(entry=>entry.id===head.value?.id)?.segments
    ||scene.value?.conversations?.find(entry=>entry.checkpointId===head.value?.id)?.segments
  return saved?.length?saved:sceneDialogueSegments(head.value?.text||'环顾四周，迈出你的第一步。',characters.value)
})
function showReply(id:string,focusInput=true){const entry=responses.value.find(e=>e.id===id),speaker=entry?.segments.filter(p=>p.kind==='speech').at(-1)?.speakerId,person=characters.value.find(e=>e.id===speaker);if(person)choose(person,focusInput);else emit('draft',id)}
function autoReply(){/* Replies never change the player's explicit conversation target. */}
async function exportCharacterCard(){
  const character=selected.value,worldId=store.world?.id
  if(!character||character.type!=='character'||!worldId||exportingCard.value)return
  const scope=capture()
  exportingCard.value=true;cardNotice.value=''
  try{
    const result=await exportWorldCharacterCard(worldId,character.id)
    if(!scope.valid())return
    app.message('已存成角色卡，可在角色卡库继续编辑。')
    await router.push('/characters/'+result.cardId)
  }catch(e){
    if(scope.valid())cardNotice.value=(e as Error).message||'暂时无法存成角色卡。'
  }finally{
    if(scope.valid())exportingCard.value=false
  }
}
function closeTarget(){selected.value=null;partnerId.value=''}
function acceptScene(value:SceneView){
  if(value.invalidPreviewHashes?.some(hash=>shownRevision===hash||shownRevision.startsWith(hash+':')))disposeViewer()
  scene.value=value
  if(partnerId.value){selected.value=value.characters.find(e=>e.id===partnerId.value)||null;if(!selected.value)partnerId.value=''}
}
function travel(point:ScenePoint){if(disabled.value)return;closeTarget();region.value=null;void store.act(point.direction,'action',undefined,point.id)}
function observeSelected(){if(selected.value&&selected.value.type!=='character'){store.input='我仔细观察'+selected.value.name+'。';send()}}

const currentPoint=computed(()=>scene.value?.points.find(p=>p.id===scene.value?.pointId))
const mapRoutes=computed(()=>scene.value?mapExits(scene.value).map(exit=>({...exit,x:50+Math.sin(exit.yaw)*34,y:50-Math.cos(exit.yaw)*34})):[])
function edgePath(edge:{from:string;to:string}){const a=scene.value?.layout.nodes.find(p=>p.id===edge.from),b=scene.value?.layout.nodes.find(p=>p.id===edge.to);return a&&b?`M ${a.x} ${a.y} L ${b.x} ${b.y}`:''}
function focusRegion(point:ScenePoint){region.value=point;closeTarget();if(imageBound(point)&&viewer&&viewReady.value)void viewer.animate({yaw:point.yaw,pitch:point.pitch,speed:'4rpm'})}
function pointExit(point:ScenePoint){return scene.value?.exits.find(exit=>exit.portalId===point.id&&exit.name===point.name)}
function activatePoint(point:ScenePoint){const exit=pointExit(point);if(exit)travel(exit);else focusRegion(point)}
const pendingMove=ref<{targetId:string;targetKind:'point'|'place'}|null>(null)
const head=computed(()=>store.line?.head),disabled=computed(()=>!interactionsReady.value||!viewReady.value||store.running||store.busy||moving.value||!app.online||head.value?.state?.ended||store.line?.status!=='active'||store.world?.status!=='active')
const characters=computed(()=>scene.value?.characters||[]),items=computed(()=>scene.value?.items||[])
const phase=computed(()=>store.recovering?'正在保存…':store.task?.phase==='writing'?'故事正在展开…':'故事正在回应…')
let viewer:Viewer|null=null,markers:MarkersPlugin|null=null,generation=0,timer:ReturnType<typeof setTimeout>|undefined,cameraTimer:ReturnType<typeof setTimeout>|undefined,disposed=false,imageId='',shownRevision='',following=true,focusPartnerOnReady=false,rendererReady=false
const personLabels=new Map<string,{button:HTMLButtonElement;left:boolean;width:number}>()
let mediaContext:{value:SceneView;valid:()=>boolean}|null=null
const media=new PanoramaMediaController(createSurface,state=>{
  const context=mediaContext
  shownRevision=state.shown?.revision||''
  interactionsReady.value=Boolean(context?.valid()&&state.phase==='ready'&&state.interactionsEnabled&&context.value.status==='ready')
  if(canvas.value){canvas.value.dataset.mediaRevision=state.shown?.revision||'';canvas.value.dataset.interactive=String(interactionsReady.value)}
  if(state.phase==='ready'&&context?.valid())imageId=context.value.id
  viewReady.value=Boolean(viewer&&rendererReady&&state.shown)
  mediaError.value=state.phase==='retained'?'新图片暂未载入，已保留原图；交互将在图片确认后恢复。':state.phase==='failed'?'场景图片暂时无法显示，可以切换文字继续游玩。':''
  updateMarkers()
},async(url,signal)=>{
  const context=mediaContext
  const response=await fetch(backendBase()+url,{headers:authorizationHeaders(),credentials:'include',signal})
  if(!response.ok)throw new Error('场景图片暂时无法读取。')
  const data=await response.blob()
  if(!context?.valid())throw new Error('场景已变化')
  return data
})
function cancelView(){clearTimeout(timer);generation++}
function disposeViewer(){media.dispose();viewReady.value=false;interactionsReady.value=false;personLabels.clear();markers=null;viewer=null;imageId='';rendererReady=false}
function viewScope(){const scope=capture(),g=++generation,cp=head.value?.id;return()=>scope.valid()&&g===generation&&!disposed&&cp===head.value?.id}
function choose(entity:WorldEntity,focusInput=true){
  const available=[...characters.value,...items.value].find(e=>e.id===entity.id)
  if(!available){closeTarget();return}
  region.value=null;selected.value=available;partnerId.value=available.type==='character'?available.id:'';tab.value=available.type==='character'?'people':'items'
  const position=available.type==='character'?scene.value?.characterPresentation?.[available.id]:undefined
  if(!viewReady.value&&available.type==='character')focusPartnerOnReady=true
  if(position?.mode==='embedded'&&position.yaw!==undefined&&position.pitch!==undefined&&viewer&&viewReady.value){
    // The transcript sits in the lower centre. On a wide screen frame the
    // face to its left; on a narrow screen frame it above the transcript.
    const wide=viewer.state.size.width>=1100
    void viewer.animate({yaw:position.yaw+(wide?.62:0),pitch:Math.max(-Math.PI/2,position.pitch-(wide?.15:.48)),zoom:Math.min(viewer.getZoomLevel(),25),speed:window.matchMedia('(prefers-reduced-motion: reduce)').matches?0:800})
  }
  void nextTick(()=>{if(focusInput)input.value?.focus();void follow()})
}
function characterMarker(entity:WorldEntity){
  const button=element(entity.name,'person',()=>choose(entity))
  button.classList.add('scene-character-anchor');button.dataset.entityId=entity.id
  return button
}

function element(label:string,type:string,activate:()=>void){const b=document.createElement('button');b.type='button';b.className='scene-marker scene-marker-'+type;b.textContent=label;b.setAttribute('aria-label',type==='person'?'与'+label+'交谈':type==='item'?'查看'+label:label);b.addEventListener('click',e=>{e.stopPropagation();activate()});return b}
function fitPersonLabels(){
  if(!viewer||!markers||!viewReady.value)return
  for(const [id,label] of personLabels){
    const spot=scene.value?.characterPresentation?.[id]
    if(spot?.labelYaw===undefined||spot.labelLeftYaw===undefined||spot.labelPitch===undefined)continue
    const project=(yaw:number)=>viewer!.dataHelper.vector3ToViewerCoords(viewer!.dataHelper.sphericalCoordsToVector3({yaw,pitch:spot.labelPitch!})).x
    const right=project(spot.labelYaw),left=project(spot.labelLeftYaw)
    const width=Math.max(112,label.button.offsetWidth),edge=14
    // Use the actual opposite edge of the measured head, never slide a badge
    // across its face or clamp an unrelated point into the viewport.
    const useLeft=right+width+edge>viewer.state.size.width-8&&left-width-edge>=8
    if(useLeft===label.left&&width===label.width)continue
    label.left=useLeft;label.width=width;label.button.classList.toggle('scene-character-anchor-left',useLeft)
    markers.updateMarker({id:'person-'+id,position:{yaw:useLeft?spot.labelLeftYaw:spot.labelYaw,pitch:spot.labelPitch},anchor:useLeft?'center right':'center left',size:{width,height:44}})
  }
}
function updateMarkers(){
  if(!markers)return
  markers.clearMarkers()
  personLabels.clear()
  if(!scene.value||!viewReady.value||!interactionsReady.value||imageId!==scene.value.id)return
  const current=scene.value
  const points=picturePoints(current)
  const entries:Parameters<MarkersPlugin['addMarker']>[0][]=[]
  points.forEach(p=>entries.push({id:'move-'+p.id,position:{yaw:p.yaw,pitch:p.pitch},element:element(p.name,'move',()=>focusRegion(p)),anchor:'bottom center',size:{width:100,height:44}}))
  characters.value.slice(0,8).forEach(e=>{
    const spot=current.characterPresentation?.[e.id]
    if(spot?.mode!=='embedded'||spot.yaw===undefined||spot.pitch===undefined)return
    const button=characterMarker(e);personLabels.set(e.id,{button,left:false,width:112})
    entries.push({id:'person-'+e.id,position:{yaw:spot.labelYaw??spot.yaw,pitch:spot.labelPitch??spot.pitch},element:button,anchor:'center left',size:{width:112,height:44},hoverScale:false})
  })
  pictureExits(current).forEach(p=>entries.push({id:'exit-'+p.id,position:{yaw:p.yaw,pitch:p.pitch},element:element(p.name,'exit',()=>travel(p)),anchor:'bottom center',size:{width:150,height:50}}))
  entries.forEach(m=>markers!.addMarker(m))
  fitPersonLabels()
}
async function display(value:SceneView,valid:()=>boolean){
  const url=value.status==='ready'?value.imageUrl:value.preview?.url
  if(!valid()||!url)return
  mediaContext={value,valid}
  const revision=value.status==='ready'?(value.image?.sha256||value.imageUrl||value.id):value.preview!.revision
  await media.show({scope:[store.world?.id,store.line?.id,head.value?.id,value.sceneHash].join(':'),revision,url,
    interactionsVerified:value.status==='ready'&&(!value.image?.anchorStatus||value.image.anchorStatus==='complete')})
}
async function createSurface(url:string){
  const {value,valid}=mediaContext!
  const surfaceScope=capture(),checkpointId=head.value?.id
  const surfaceValid=()=>surfaceScope.valid()&&!disposed&&checkpointId===head.value?.id&&scene.value?.sceneHash===value.sceneHash
  await nextTick();if(!valid()||scene.value?.id!==value.id||!canvas.value)throw new Error('场景已变化')
  const camera=await cacheGet<{yaw:number;pitch:number;zoom:number}>('scene-camera:'+value.sceneHash).catch(()=>undefined)
  if(!valid()||scene.value?.id!==value.id)throw new Error('场景已变化')
  const created=new Viewer({container:canvas.value,panorama:url,navbar:false,defaultYaw:camera?.yaw??value.descriptor.viewpoint?.initialYaw??0,defaultPitch:camera?.pitch||0,defaultZoomLvl:camera?.zoom??35,mousewheelCtrlKey:false,touchmoveTwoFingers:false,plugins:[MarkersPlugin],lang:{loading:'正在展开场景…',loadError:'场景图片暂时无法显示'}})
  viewer=created;markers=created.getPlugin(MarkersPlugin) as MarkersPlugin;imageId=value.id;rendererReady=false
  created.addEventListener('ready',()=>{if(surfaceValid()&&viewer===created){rendererReady=true;viewReady.value=true;updateMarkers();if(focusPartnerOnReady&&selected.value&&interactionsReady.value){focusPartnerOnReady=false;choose(selected.value,false)}}})
  created.addEventListener('position-updated',()=>{cameraYaw.value=created.getPosition().yaw;fitPersonLabels();if(canvas.value){canvas.value.dataset.yaw=String(created.getPosition().yaw);canvas.value.dataset.pitch=String(created.getPosition().pitch)}clearTimeout(cameraTimer);cameraTimer=setTimeout(()=>{if(surfaceValid()&&viewer===created)void cacheSet('scene-camera:'+value.sceneHash,{...created.getPosition(),zoom:created.getZoomLevel()})},300)})
  created.addEventListener('zoom-updated',()=>{fitPersonLabels();if(surfaceValid()&&canvas.value)canvas.value.dataset.zoom=String(created.getZoomLevel())})
  created.addEventListener('size-updated',fitPersonLabels)
  created.addEventListener('panorama-error',()=>{if(surfaceValid()&&viewer===created){mediaError.value='场景暂时无法显示，可以切换文字继续游玩。';viewReady.value=false;interactionsReady.value=false}})
  return {replace:async(nextUrl:string)=>{
    const position=created.getPosition(),zoom=created.getZoomLevel()
    const ok=await created.setPanorama(nextUrl,{position,zoom,transition:window.matchMedia('(prefers-reduced-motion: reduce)').matches?false:{speed:200,rotation:false}})
    if(ok){created.rotate(position);created.zoom(zoom)}
    return ok
  },destroy:()=>{created.destroy();if(viewer===created){viewer=null;markers=null;rendererReady=false;viewReady.value=false;imageId='';personLabels.clear()}}}
}
async function load(regenerate=false){
  confirmRegenerate.value=false
  sourceSceneId.value=''
  clearTimeout(timer);const valid=viewScope(),world=store.world,line=store.line,cp=head.value?.id;if(!world||!line||!cp)return
  loading.value=true;error.value='';selected.value=null;region.value=null
  // A panorama from another checkpoint is never shown as this location.
  scene.value=null;disposeViewer()
  try{
    const [profile,pending,inspection]=await Promise.all([worldApi<{enabled:boolean}>('/scene-profile'),pendingWorldMutation('scene-move:'+line.id),pendingWorldMutation('scene-inspect:'+line.id)]);if(!valid())return;configured.value=profile.enabled
    pendingInspection.value=Boolean(inspection&&!inspection.receipt)
    if(inspection?.receipt?.worldId===world.id)await clearWorldMutation('scene-inspect:'+line.id,valid,world.id,inspection.key)
    if(!valid())return
    if(pending?.receipt?.worldId===world.id&&pending.receipt.checkpointId===cp){
      await clearWorldMutation('scene-move:'+line.id,valid,world.id,pending.key);if(!valid())return
      pendingMove.value=null
    }else pendingMove.value=pending?.body as typeof pendingMove.value||null
    if(!profile.enabled){loading.value=false;return}
    const path='/worlds/'+world.id+'/lines/'+line.id+'/scene',slot='scene-regenerate:'+line.id
    let value:SceneView
    if(regenerate){
      const pending=await pendingWorldMutation(slot);if(!valid())return
      let intentKey=''
      value=await worldMutation<SceneView>(slot,path,pending?.body||{expectedCheckpointId:cp,regenerate:true},valid,intent=>{intentKey=intent.key})
      if(!valid())return
      await clearWorldMutation(slot,valid,world.id,intentKey)
      if(value.checkpointId!==cp){await store.reloadHead();if(valid())void load();return}
    }else value=await worldApi<SceneView>(path,{method:'POST',key:newKey(),body:{expectedCheckpointId:cp,regenerate:false}})
    if(!valid())return;acceptScene(value)
    if(value.preview)await display(value,valid)
    if(value.status==='ready'){await display(value,valid);if(valid()){loading.value=false;autoReply();if(inspectingPicture(value))poll(value.id,valid)}}
    else if(['queued','running','saving'].includes(value.status))poll(value.id,valid)
    else loading.value=false
  }catch(e){if(valid()){loading.value=false;error.value=(e as Error).message}}
}
async function inspectReceived(){
  if(!scene.value||!store.world||!store.line||!head.value||loading.value||!app.online||cooldownRemaining.value>0&&!pendingInspection.value)return
  const valid=viewScope(),world=store.world.id,line=store.line.id,cp=head.value.id,sceneId=scene.value.id
  const slot='scene-inspect:'+line,path='/worlds/'+world+'/lines/'+line+'/scene/inspect'
  let intentKey=''
  loading.value=true;error.value=''
  try{
    const pending=await pendingWorldMutation(slot);if(!valid())return
    const value=await worldMutation<SceneView>(slot,path,pending?.body||{sceneId,expectedCheckpointId:cp,independent:true,...(sourceSceneId.value?{sourceSceneId:sourceSceneId.value}:{})},valid,intent=>{intentKey=intent.key})
    if(!valid())return
    await clearWorldMutation(slot,valid,world,intentKey);if(!valid())return
    pendingInspection.value=false;acceptScene(value)
    if(value.preview||value.status==='ready')await display(value,valid)
    if(!valid())return
    if(value.status==='ready'&&!inspectingPicture(value))loading.value=false
    else poll(value.id,valid)
  }catch(e){if(valid()){
    const rejected=['SCENE_INSPECTION_RUNNING','SCENE_INSPECTION_COMPLETE','SCENE_INSPECTION_UNAVAILABLE','WORLD_CHECKPOINT_CONFLICT','SCENE_CONTEXT_CHANGED','BUDGET_NOT_AUTHORIZED'].includes((e as {code?:string}).code||'')
    if(rejected&&intentKey)await clearWorldMutation(slot,valid,undefined,intentKey)
    if(valid()){loading.value=false;pendingInspection.value=!rejected;error.value=(e as Error).message}
  }}
}
function regenerate(){confirmRegenerate.value=false;void load(true)}
function poll(id:string,valid:()=>boolean){
  timer=setTimeout(async()=>{if(!valid())return;try{const value=await worldApi<SceneView>('/scenes/'+id+'?lineId='+encodeURIComponent(store.line!.id));if(!valid())return;acceptScene(value);if(value.preview)await display(value,valid);if(value.status==='ready'){await display(value,valid);if(valid()){loading.value=false;autoReply();if(inspectingPicture(value))poll(value.id,valid)}}else if(['queued','running','saving'].includes(value.status))poll(id,valid);else loading.value=false}catch(e){if(valid()){loading.value=false;error.value=(e as Error).message}}},1200)
}
async function move(point:ScenePoint,recover=false){
  if(disabled.value||!head.value||!store.world||!store.line)return
  const scope=capture(),worldId=store.world.id,lineId=store.line.id,cp=head.value.id,slot='scene-move:'+lineId;moving.value=true;error.value=''
  try{
    const pending=await pendingWorldMutation(slot);if(!scope.valid())return
    const old=pending?.body as {targetId?:string;targetKind?:string}|undefined
    if(old&&!recover&&(old.targetId!==point.id||old.targetKind!==(point.kind||'point'))){pendingMove.value=old as typeof pendingMove.value;throw new Error('上次移动的结果尚未确认，请先点击“恢复上次移动”。')}
    const path='/worlds/'+worldId+'/lines/'+lineId+'/scene/move'
    const body=pending?.body||{expectedCheckpointId:cp,targetKind:point.kind||'point',targetId:point.id}
    if(pending&&pending.path!==path)throw new Error('请先恢复上一条世界线的移动。')
    let intentKey=''
    await worldMutation(slot,path,body,scope.valid,intent=>{intentKey=intent.key});if(!scope.valid())return
    await store.reloadHead();if(!scope.valid())return
    await clearWorldMutation(slot,scope.valid,worldId,intentKey)
    if(scope.valid())pendingMove.value=null
  }catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())moving.value=false}
}
function recoverMove(){if(pendingMove.value)void move({id:pendingMove.value.targetId,name:'上次移动',direction:'恢复上次移动',kind:pendingMove.value.targetKind==='place'?'place':undefined,yaw:0,pitch:0},true)}
function send(){if(!disabled.value&&store.input.trim()){
  if(selected.value&&![...characters.value,...items.value].some(e=>e.id===selected.value?.id)){closeTarget();error.value='对方已离开这个场景。';return}
  following=true;void store.act(store.input,chatting.value?'dialogue':interpretInput(store.input,selected.value?.type),selected.value?.id)
}}
function scroll(){const el=prose.value;if(el)following=el.scrollHeight-el.scrollTop-el.clientHeight<60}
async function follow(){await nextTick();if(following&&prose.value)prose.value.scrollTop=prose.value.scrollHeight}
function zoom(delta:number){if(viewer)viewer.zoom(Math.min(100,Math.max(0,viewer.getZoomLevel()+delta)))}
watch(()=>[store.world?.id,store.line?.id,head.value?.id],()=>{cancelView();if(head.value)void load()},{immediate:true})
watch(()=>store.task?.stream.text,follow)
watch(()=>[characters.value,items.value],updateMarkers)
watch(()=>conversations.value,follow)
watch(responses,()=>{autoReply();void follow()})
watch([()=>store.world?.id,()=>store.line?.id],()=>{closeTarget();seenResponses.clear()})
onUnmounted(()=>{disposed=true;clearInterval(progressTicker);cancelView();clearTimeout(cameraTimer);disposeViewer()})
</script>

<template>
  <section class="scene-workspace" :class="{'scene-prose-expanded':expanded,'scene-night':['深夜','夜'].includes(scene?.descriptor.lighting||'')}" :style="{'--actor-brightness':['深夜','夜','傍晚','黄昏'].includes(scene?.descriptor.lighting||'')?'.62':'.91'}" aria-label="沉浸场景" :data-scene-id="scene?.id" :data-scene-status="scene?.status">
    <div ref="canvas" class="scene-panorama" aria-label="拖动查看 360 度场景"></div>
    <div v-if="!viewReady||!interactionsReady||scene?.status!=='ready'||mediaError" :class="viewReady?'scene-progress-state':'scene-empty-state'" role="status">
      <template v-if="!viewReady"><LoaderCircle v-if="loading||moving" class="scene-loading-icon" :size="32"/><Compass v-else :size="38"/></template>
      <h2>{{moving?'正在移动…':!configured?'场景服务暂不可用':viewReady&&visualPending?'环境已展开，正在完善场景':feedback.title}}</h2>
      <p v-if="error||mediaError" class="error" role="alert">{{error||mediaError}}</p><p v-else-if="!configured">平台场景服务暂不可用，可以先继续文字游玩。</p>
      <p v-else-if="viewReady&&visualPending">可以先环顾环境；人物与交互确认后会自动显示，不推进故事。</p>
      <p v-else-if="configured">{{feedback.description}}</p>
      <div v-if="visualPending&&scene?.progress" class="scene-generation-progress" aria-label="场景生成进度">
        <ol><li v-for="[id,label] in progressSteps" :key="id" :aria-current="scene.progress.phase===id?'step':undefined">{{label}}</li></ol>
        <p>{{scene.progress.label}} · 已用 {{elapsedLabel}}</p>
        <small v-if="scene.progress.slow">等待较久，可先继续文字游玩。再次进入会读取已有进度，不会因切换页面重新出图。</small>
      </div>
      <div v-if="visualPending&&!moving" class="scene-recovery-actions">
        <button class="scene-light-button scene-recovery-primary" @click="emit('text')"><BookOpen :size="16"/>先继续文字游玩</button>
        <button v-if="scene?.progress?.canStop" class="scene-light-button" :disabled="!app.online||stoppingScene" @click="stopScene">{{stoppingScene?'正在停止…':'停止场景生成'}}</button>
      </div>
      <div v-if="!visualPending&&!moving" class="scene-recovery-actions">
        <button v-if="configured&&(scene?.recovery?.canInspect||pendingInspection)" :disabled="!app.online||cooldownRemaining>0&&!pendingInspection" class="scene-light-button scene-recovery-primary" @click="inspectReceived">{{pendingInspection?'恢复上次场景请求':'继续完成场景'}}</button>
        <button class="scene-light-button" @click="emit('text')"><BookOpen :size="16"/>文字游玩</button>
        <button v-if="mediaError&&scene" class="scene-light-button" :disabled="!app.online" @click="display(scene,viewScope())">重新载入图片</button>
        <button v-if="configured" :disabled="!app.online||pendingInspection||cooldownRemaining>0" class="scene-light-button" @click="confirmRegenerate=true">重新出图</button>
      </div>
      <small v-if="!loading&&scene?.recovery?.imageRetained" class="scene-recovery-note">继续时复用已保存图片；后续校验和必要画面修复会使用额度，不推进故事。{{scene.recovery.usageState==='unknown'?'上次调用的费用尚未确认。':''}}</small>
      <SceneSavedImages v-if="!loading&&scene?.recovery?.savedImages?.length" v-model="sourceSceneId" :items="scene.recovery.savedImages" :disabled="pendingInspection||!app.online"/>
      <div v-if="confirmRegenerate&&!loading" class="scene-regenerate-confirm" role="group" aria-label="重新出图说明"><p>重新出图将创建新的图片请求并使用额度，已有图片仍会保留。</p><button class="scene-light-button" :disabled="!app.online||pendingInspection||cooldownRemaining>0" @click="regenerate">确认重新出图</button><button class="scene-light-button" @click="confirmRegenerate=false">取消</button></div>
    </div>
    <div class="scene-topline"><span><MapPin :size="15"/>{{scene?.descriptor.placeName||store.world?.locationName}}</span><small><Clock3 :size="13"/>{{scene?.timeLabel||store.state?.timeLabel}}</small><small v-if="scene?.image?.anchorStatus==='repairing'">正在补全场景…</small><small v-else-if="scene?.image?.anchorStatus==='incomplete'">{{bindingNotice}}</small></div>
    <aside v-if="scene" class="scene-minimap" aria-label="场景小地图">
      <div class="scene-map-tabs"><button :class="{active:tab==='map'}" aria-label="小地图" @click="tab='map'"><Compass :size="17"/></button><button :class="{active:tab==='people'}" aria-label="场景人物" @click="tab='people'"><Users :size="17"/><small>{{characters.length}}</small></button><button :class="{active:tab==='items'}" aria-label="场景物品" @click="tab='items'"><Package :size="17"/><small>{{items.length}}</small></button></div>
      <button v-if="pendingMove" class="scene-exit" :disabled="disabled" @click="recoverMove">恢复上次移动</button><template v-if="tab==='map'"><strong>{{scene.descriptor.placeName}}</strong><div class="scene-map-drawing scene-map-connections"><svg viewBox="0 0 100 100" aria-hidden="true"><path v-for="point in mapRoutes" :key="point.id" :d="`M50 50 L${point.x} ${point.y}`"/><g :transform="`translate(50 50) rotate(${cameraYaw*180/Math.PI})`"><path class="scene-map-gaze" d="M0 0 L-10 -20 Q0 -25 10 -20 Z"/></g></svg><span class="scene-map-here"><i></i><small>当前位置</small></span><button v-for="point in mapRoutes" :key="point.id" class="scene-map-point route" :style="{left:point.x+'%',top:point.y+'%'}" :disabled="disabled" :aria-label="'前往 '+point.name" @click="travel(point)"><span></span><small>{{point.name}}</small></button></div><button v-for="point in scene.exits" :key="point.id" class="scene-exit" :disabled="disabled" @click="travel(point)"><MoveUpRight :size="16"/>{{point.name}}</button><button v-for="point in localPoints(scene)" :key="'local-'+point.id" class="scene-exit" :disabled="disabled" @click="focusRegion(point)"><MapPin :size="15"/>{{point.name}}</button></template>
      <div v-else class="scene-entity-list"><button v-for="entity in tab==='people'?characters:items" :key="entity.id" :disabled="!interactionsReady" @click="choose(entity)"><CharacterAvatar v-if="entity.type==='character'" :entity-id="entity.id" :name="entity.name"/><span v-else class="scene-avatar">{{entity.name.slice(0,1)}}</span>{{entity.name}}<MessageCircle v-if="entity.type==='character'" :size="15"/><Package v-else :size="15"/></button><p v-if="!(tab==='people'?characters:items).length">{{tab==='people'?'此处没有遇见其他人':'还没有可查看的物品'}}</p></div>
    </aside>
    <div class="scene-view-tools"><button :disabled="!viewReady" aria-label="放大场景" @click="zoom(10)"><Plus :size="18"/></button><button :disabled="!viewReady" aria-label="缩小场景" @click="zoom(-10)"><Minus :size="18"/></button><button :disabled="!viewReady" aria-label="全屏场景" @click="viewer?.toggleFullscreen()"><Maximize :size="18"/></button><button aria-label="世界资料" @click="emit('details')"><Users :size="18"/></button></div>
    <div v-if="region" class="scene-entity-card scene-region-card"><button class="scene-card-close" aria-label="关闭区域详情" @click="region=null"><X :size="16"/></button><MapPin :size="24"/><div><strong>{{region.name}}</strong><p v-if="region.source?.quote">{{region.source.quote}}</p><small v-if="region.id===scene?.pointId">你在这里</small><button v-else :disabled="disabled" @click="move(region)">{{region.direction}} <MoveUpRight :size="14"/></button><button v-for="exit in scene?.exits.filter(e=>e.kind==='exit'&&e.id==='exit:'+region?.id)" :key="exit.id" :disabled="disabled" @click="travel(exit)">{{exit.name}} <MoveUpRight :size="14"/></button></div></div>
    <div v-if="selected&&!chatting" class="scene-entity-card"><button class="scene-card-close" aria-label="关闭人物或物品详情" @click="closeTarget"><X :size="16"/></button><CharacterAvatar v-if="selected.type==='character'" :entity-id="selected.id" :name="selected.name"/><span v-else class="scene-avatar">{{selected.name.slice(0,1)}}</span><div><strong>{{selected.name}}</strong><small>{{selected.type==='character'?'与你交谈':'查看物品'}}</small><p>{{selected.description||'你可以进一步观察。'}}</p><button v-if="selected.type==='character'&&store.world?.id" :disabled="exportingCard" @click="exportCharacterCard">{{exportingCard?'正在保存…':'把 TA 存成角色卡'}}</button><small v-if="cardNotice" class="error" role="alert">{{cardNotice}}</small><button v-if="selected.type!=='character'" :disabled="disabled" @click="observeSelected">仔细观察 <MoveUpRight :size="14"/></button></div></div>
    <div class="scene-bottom-hud">
      <div v-if="chatting" class="scene-chat" aria-label="角色对话">
        <div class="scene-chat-heading"><CharacterAvatar :entity-id="selected?.id" :name="selected?.name||'人物'"/><strong>{{selected?.name}}</strong><small v-if="scene?.characterPresentation?.[selected?.id||'']?.mode==='voice'">传音</small><button aria-label="结束角色对话" @click="closeTarget"><X :size="18"/></button></div>
        <div ref="prose" class="scene-chat-scroll" @scroll="scroll">
          <p v-if="!conversations.length&&!pendingChat&&!ambientReplies.length" class="scene-chat-empty">想和{{selected?.name}}聊些什么？</p>
          <SceneReply v-for="entry in conversations" :key="entry.turnId" :segments="entry.segments" :user="entry.user" :player-id="store.state?.playerId"/>
          <SceneReply v-for="entry in ambientReplies" :key="entry.id" :segments="entry.segments" :user="entry.user" :draft="entry.draft" :player-id="store.state?.playerId"/>
          <template v-if="pendingChat"><SceneReply :segments="liveSegments" :user="pendingChat.result.message" :player-id="store.state?.playerId" :stream="{contentId:pendingChat.stream.contentId||pendingChat.id,version:pendingChat.stream.streamVersion||1}" @rendered="follow"/><p v-if="!liveSegments.length" class="scene-chat-empty" role="status">{{store.running?'正在回复…':'这次对话未完成'}}</p><small v-if="!store.running&&pendingChat.state!=='succeeded'">{{liveSegments.length?'已保留回复 · 世界状态未推进':'尚未收到回复 · 世界状态未推进'}}</small></template>
        </div>
      </div>
      <div v-else class="scene-narrative"><button class="scene-narrative-heading" @click="expanded=!expanded"><span>{{store.running?phase:head?.title||'这一刻'}}</span><ChevronDown v-if="expanded" :size="18"/><ChevronUp v-else :size="18"/></button><div ref="prose" class="scene-narrative-scroll" @scroll="scroll"><SceneReply :segments="narrativeSegments" :stream="store.running&&store.task?{contentId:store.task.stream.contentId||store.task.id,version:store.task.stream.streamVersion||1}:undefined" @rendered="follow"/><span v-if="store.running" class="scene-writing-dot"></span></div><div class="scene-choices"><button v-for="option in head?.options" :key="option.text" :disabled="disabled" @click="store.act(option.text,option.kind)">{{option.text}}</button><button v-for="draft in store.drafts" :key="draft.id" @click="showReply(draft.id)">查看保留的正文</button><button @click="emit('history')">故事记录</button></div></div>
      <div v-if="error&&viewReady||store.error" class="scene-action-error" role="alert">{{error||store.error}}<button v-if="store.retryMessage" class="scene-light-button" :disabled="disabled" @click="store.restoreAction();input?.focus()">重新填写上次行动</button></div>
      <form class="scene-composer" @submit.prevent="send"><div v-if="selected" class="scene-input-target"><span>{{selected.type==='character'?'与 '+selected.name+' 交谈':'查看 '+selected.name}}</span><button type="button" aria-label="取消交互对象" @click="closeTarget"><X :size="14"/></button></div><div class="scene-input-row"><textarea ref="input" v-model="store.input" rows="1" maxlength="5000" :disabled="head?.state?.ended" :placeholder="selected?.type==='character'?'对'+selected.name+'说些什么…':'写下想说的话，或想做的事…'" aria-label="场景行动或对话" @keydown.ctrl.enter.prevent="send" @keydown.meta.enter.prevent="send"></textarea><button v-if="store.running" type="button" class="scene-send" aria-label="停止这一轮" @click="store.cancel"><Square :size="19"/></button><button v-else class="scene-send" :disabled="disabled||!store.input.trim()" aria-label="提交场景行动"><ArrowUp :size="21"/></button></div><small v-if="!app.online">离线 · 输入已保留</small></form>
    </div>
  </section>
</template>
