<script setup lang="ts">
import {trackProductEvent} from "../analytics/product-events"
import {computed,ref,watch,onUnmounted,nextTick} from 'vue'
import {Viewer} from '@photo-sphere-viewer/core'
import {MarkersPlugin} from '@photo-sphere-viewer/markers-plugin'
import '@photo-sphere-viewer/core/index.css'
import '@photo-sphere-viewer/markers-plugin/index.css'
import {useWorld} from './store'
import {useApp} from '../stores/app'
import {worldApi} from './api'
import {backendBase} from '../platform/bridge'
import {authorizationHeaders} from '../api'
import {sceneDialogueSegments} from './scene-dialogue'
import {interpretInput} from './action-intent'
import {stageActors,stageSpeaker,expressionLabels,type StageCharacter,type PortraitExpression,type PortraitSceneView} from './portrait-stage'
import PortraitActor from './PortraitActor.vue'
import PortraitMinimap from './PortraitMinimap.vue'
import {imageBound} from './scene-grounding'
import type {ScenePoint} from './scene-types'
import CharacterAvatar from '../components/CharacterAvatar.vue'
import SceneReply from './SceneReply.vue'
import './portrait-stage.css'
const emit=defineEmits<{text:[];details:[]}>(),store=useWorld(),app=useApp()
const data=ref<PortraitSceneView|null>(null),selectedId=ref(''),expressions=ref<Record<string,PortraitExpression>>({})
const canvas=ref<HTMLElement|null>(null),input=ref<HTMLTextAreaElement|null>(null),transcript=ref<HTMLElement|null>(null)
const error=ref(''),backgroundError=ref(''),loading=ref(false),backgroundReady=ref(false),historyOpen=ref(false),settingsOpen=ref(false)
const storyOpen=ref(false)
const cameraYaw=ref(0),travelTarget=ref<{point:ScenePoint;checkpointId:string}|null>(null),travelSubmitting=ref(false),travelOrigin=ref('')
const travelDialog=ref<HTMLDialogElement|null>(null)
const cameraBlocked=computed(()=>!!selected.value||!!travelTarget.value)
let markers:MarkersPlugin|null=null
let dialogueCamera:{yaw:number;pitch:number;zoom:number}|null=null
let epoch=0,disposed=false,viewer:Viewer|null=null,controller:AbortController|undefined,backgroundUrl='',backgroundHash=''
const head=computed(()=>store.line?.head)
const roles=computed(()=>data.value?.characters||[])
const selected=computed(()=>roles.value.find(p=>p.id===selectedId.value))
const actors=computed(()=>stageActors(roles.value,selectedId.value))
const disabled=computed(()=>!app.online||store.running||store.busy||travelSubmitting.value||!!travelOrigin.value||store.world?.status!=='active'||store.line?.status!=='active'||head.value?.state?.ended)
const entries=computed(()=>(data.value?.conversations||[]).filter(e=>e.partnerId===selectedId.value))
const task=computed(()=>store.task?.result.interactionMode==='dialogue'&&store.task.result.partnerId===selectedId.value?store.task:null)
const pending=computed(()=>task.value&&!entries.value.some(e=>e.turnId===task.value?.result.turnId)?task.value:null)
const liveSegments=computed(()=>store.drafts.find(d=>d.taskId===pending.value?.id)?.draftSegments||sceneDialogueSegments(pending.value?.stream.text||'',roles.value,selectedId.value,store.running))
const latest=computed(()=>pending.value?liveSegments.value:entries.value.at(-1)?.segments||sceneDialogueSegments(entries.value.at(-1)?.reply||'',roles.value,selectedId.value))
const activeSpeaker=computed(()=>stageSpeaker(latest.value,roles.value,selectedId.value))
function clearBackground(){viewer?.destroy();viewer=null;markers=null;backgroundReady.value=false;controller?.abort();if(backgroundUrl)URL.revokeObjectURL(backgroundUrl);backgroundUrl='';backgroundHash=''}
function syncMarkers(){
 if(!markers||!backgroundReady.value)return
 markers.clearMarkers();if(selected.value)return
 for(const point of data.value?.exits||[]){
  if(!imageBound(point))continue
  const button=document.createElement('button');button.type='button';button.className='portrait-exit-marker';button.textContent='↗ '+point.direction;button.setAttribute('aria-label',point.direction);button.dataset.exitId=point.id;button.disabled=!!disabled.value
  button.addEventListener('click',event=>{event.stopPropagation();requestTravel(point)})
  markers.addMarker({id:'route-'+point.id,position:{yaw:point.yaw,pitch:point.pitch},element:button,anchor:'center center',size:{width:180,height:48}})
 }
}
function requestTravel(point:ScenePoint){
 if(disabled.value||selected.value||!head.value||!data.value?.exits.some(p=>p.id===point.id))return
 trackProductEvent('route.select',{worldId:store.world?.id,lineId:store.line?.id,routeId:point.id});void viewer?.stopAnimation();travelTarget.value={point,checkpointId:head.value.id}
}
function focusExit(point:ScenePoint){if(!disabled.value&&!selected.value&&imageBound(point))void viewer?.animate({yaw:point.yaw,pitch:point.pitch,speed:'8rpm'})}
async function confirmTravel(){
 const intent=travelTarget.value
 if(!intent||disabled.value)return
 if(intent.checkpointId!==head.value?.id||!data.value?.exits.some(p=>p.id===intent.point.id)){travelTarget.value=null;error.value='当前位置已变化，请重新选择路口。';return}
 travelSubmitting.value=true
 try{await store.act(intent.point.direction,'action',undefined,intent.point.id)
  if(!store.error){trackProductEvent('route.confirm',{worldId:store.world?.id,lineId:store.line?.id,routeId:intent.point.id});travelOrigin.value=intent.checkpointId;travelTarget.value=null}
 }finally{travelSubmitting.value=false}
}
async function paintBackground(value:PortraitSceneView,ticket:number){
 const loadStart=performance.now();const background=value.background
 if(background?.sha256===backgroundHash&&viewer){trackProductEvent('scene.ready',{worldId:store.world?.id,cacheHit:true},0);syncMarkers();return}
 clearBackground();backgroundError.value=''
 if(!background){backgroundError.value='这个地点暂时没有场景图，角色对话仍可继续。';return}
 controller=new AbortController()
 try{
  const response=await fetch(backendBase()+background.url,{credentials:'include',headers:authorizationHeaders(),signal:controller.signal})
  if(!response.ok)throw new Error('场景图尚未载入，角色对话仍可继续。')
  const blob=await response.blob();if(disposed||ticket!==epoch)return
  backgroundUrl=URL.createObjectURL(blob);await nextTick();if(disposed||ticket!==epoch||!canvas.value)return
  const own=new Viewer({container:canvas.value,navbar:false,defaultYaw:background.initialYaw,defaultZoomLvl:20,mousewheelCtrlKey:false,
      plugins:[MarkersPlugin],mousemove:!cameraBlocked.value,mousewheel:!cameraBlocked.value,lang:{loading:'正在展开环境…',loadError:'场景图暂未载入'}})
  viewer=own;markers=own.getPlugin(MarkersPlugin) as MarkersPlugin;backgroundHash=background.sha256
  own.addEventListener('position-updated',()=>{if(!disposed&&viewer===own&&canvas.value){cameraYaw.value=own.getPosition().yaw;canvas.value.dataset.yaw=String(cameraYaw.value);canvas.value.dataset.pitch=String(own.getPosition().pitch)}})
  own.addEventListener('zoom-updated',()=>{if(!disposed&&viewer===own&&canvas.value)canvas.value.dataset.zoom=String(own.getZoomLevel())})
  own.addEventListener('ready',()=>{if(!disposed&&ticket===epoch&&canvas.value){trackProductEvent('scene.ready',{worldId:store.world?.id,cacheHit:false},Math.round(performance.now()-loadStart));backgroundReady.value=true;cameraYaw.value=own.getPosition().yaw;canvas.value.dataset.ready='true';canvas.value.dataset.yaw=String(cameraYaw.value);canvas.value.dataset.pitch=String(own.getPosition().pitch);canvas.value.dataset.zoom=String(own.getZoomLevel());syncMarkers()}})
  await own.setPanorama(backgroundUrl)
 }catch{if(!disposed&&ticket===epoch&&!controller?.signal.aborted)backgroundError.value='场景图尚未载入，角色对话仍可继续。'}
}
async function load(){
 const world=store.world?.id,line=store.line?.id,cp=head.value?.id;if(!world||!line||!cp)return
 const ticket=++epoch;loading.value=true;error.value=''
 try{const next=await worldApi<PortraitSceneView>('/worlds/'+world+'/lines/'+line+'/portrait-scene')
  if(disposed||ticket!==epoch||next.checkpointId!==head.value?.id)return
  data.value=next;if(!next.characters.some(p=>p.id===selectedId.value))selectedId.value=''
  void paintBackground(next,ticket)
 }catch(e){if(!disposed&&ticket===epoch)error.value=(e as Error).message}
 finally{if(!disposed&&ticket===epoch)loading.value=false}
}
function choose(person:StageCharacter){if(travelTarget.value)return;trackProductEvent('dialogue.open',{worldId:store.world?.id,lineId:store.line?.id,characterId:person.id});selectedId.value=person.id;historyOpen.value=false;storyOpen.value=false;void nextTick(()=>input.value?.focus())}
function close(){if(selectedId.value)trackProductEvent('dialogue.close',{worldId:store.world?.id,characterId:selectedId.value});selectedId.value='';historyOpen.value=false;settingsOpen.value=false}
function zoomBy(amount:number){if(viewer)viewer.zoom(Math.max(0,Math.min(100,viewer.getZoomLevel()+amount)))}
function resetView(){viewer?.rotate({yaw:data.value?.background?.initialYaw||0,pitch:0});viewer?.zoom(20)}
function send(){if(disabled.value||!store.input.trim())return
 if(selectedId.value&&!roles.value.some(p=>p.id===selectedId.value)){close();error.value='对方已离开当前场景。';return}
 void store.act(store.input,selected.value?'dialogue':interpretInput(store.input),selected.value?.id)
}
watch(selectedId,(next,previous)=>{
 if(next&&!previous&&viewer){void viewer.stopAnimation();dialogueCamera={...viewer.getPosition(),zoom:viewer.getZoomLevel()}}
 if(!next&&previous&&viewer&&dialogueCamera){viewer.rotate(dialogueCamera);viewer.zoom(dialogueCamera.zoom);dialogueCamera=null}
 syncMarkers()
})
watch(cameraBlocked,blocked=>{viewer?.setOption('mousemove',!blocked);viewer?.setOption('mousewheel',!blocked)})
watch(travelTarget,async(target,previous)=>{if(previous&&!target&&!travelOrigin.value)trackProductEvent('route.cancel',{worldId:store.world?.id,routeId:previous.point.id});});
watch(travelTarget,async target=>{await nextTick();if(target&&travelTarget.value&&travelDialog.value&&!travelDialog.value.open)travelDialog.value.showModal();else if(!travelTarget.value)travelDialog.value?.close()})
watch(disabled,()=>syncMarkers())
watch(backgroundError,value=>{if(value)trackProductEvent('scene.error',{worldId:store.world?.id,errorCode:'SCENE_BACKGROUND_UNAVAILABLE'})})
watch(()=>head.value?.id,()=>{travelTarget.value=null;travelOrigin.value=''})
watch(()=>store.task?.state,state=>{if(state&&['failed','cancelled','succeeded'].includes(state))travelOrigin.value=''})
watch(()=>[store.world?.id,store.line?.id,head.value?.id],()=>{void load()},{immediate:true})
watch(()=>store.input,()=>void store.saveInput())
watch(()=>store.task?.stream.text,async()=>{await nextTick();if(transcript.value)transcript.value.scrollTop=transcript.value.scrollHeight})
onUnmounted(()=>{disposed=true;epoch++;clearBackground()})
</script>
<template><section class="portrait-scene" :class="{'is-talking':!!selected}" aria-label="360度互动场景" :data-checkpoint-id="head?.id" @keydown.esc="close">
 <div ref="canvas" class="portrait-environment" aria-label="拖动查看环境" :data-ready="backgroundReady"></div>
 <div class="portrait-shade" aria-hidden="true"></div>
 <div class="portrait-location"><strong>{{data?.placeName||store.world?.locationName}}</strong><small>{{data?.timeLabel||store.world?.timeLabel}}</small></div>
 <div class="portrait-side-hud"><PortraitMinimap v-if="data" :place="data.placeName" :exits="data.exits" :yaw="cameraYaw" :disabled="!!disabled||!!selected||!!travelTarget" @focus="focusExit" @travel="requestTravel"/><div class="portrait-roster" aria-label="在场人物"><button v-for="person in roles" :key="person.id" :class="{active:person.id===selectedId}" :aria-pressed="person.id===selectedId" @click="choose(person)"><CharacterAvatar :entity-id="person.id" :name="person.name" :size="30"/>{{person.name}}<small v-if="person.visibility==='voice'">传音</small></button></div></div>
 <button class="portrait-details" @click="emit('details')">世界资料</button>
 <div v-if="loading&&!data" class="portrait-loading" role="status">正在载入人物…</div>
 <div v-if="error" class="portrait-load-error" role="alert">{{error}}<button @click="load">重新载入</button></div>

 <template v-if="selected">
  <div class="portrait-camera-lock" aria-hidden="true"></div>
  <div class="portrait-actors" :class="{'has-single':actors.length===1}">
   <PortraitActor v-for="person in actors" :key="person.id" :name="person.name" :active="activeSpeaker===person.id" :src="person.expressions[expressions[person.id]||'neutral']||person.expressions.neutral||''"/>
  </div>
  <div class="portrait-director"><button :aria-expanded="settingsOpen" @click="settingsOpen=!settingsOpen">立绘设置</button><div v-if="settingsOpen" class="portrait-settings-panel"><label v-for="person in actors" :key="person.id">{{person.name}}<select :aria-label="person.name+'表情'" :value="expressions[person.id]||'neutral'" @change="expressions[person.id]=($event.target as HTMLSelectElement).value as PortraitExpression"><option v-for="(label,key) in expressionLabels" :key="key" :value="key" :disabled="!person.expressions[key]">{{label}}</option></select></label></div></div>
 </template>
 <dialog ref="travelDialog" class="portrait-travel-confirm" aria-label="确认前往" @cancel.prevent="!travelSubmitting&&(travelTarget=null)"><template v-if="travelTarget"><h2>{{travelTarget.point.direction}}？</h2><p>沿当前路线前往下一地区，继续这条故事线。</p><p v-if="store.error" class="portrait-message-error" role="alert">{{store.error}}</p><div><button :disabled="travelSubmitting" @click="travelTarget=null">暂时不去</button><button class="portrait-travel-go" :disabled="disabled" @click="confirmTravel">{{travelSubmitting?'正在提交…':'确认前往'}}</button></div></template></dialog>
 <div v-if="selected" class="portrait-dialogue" role="region" aria-label="角色对话">
  <div class="portrait-dialogue-heading"><strong>{{selected?.name||'环顾此处'}}</strong><small>{{selected?'角色对话':'自由探索'}}</small><div><button v-if="selected" @click="historyOpen=!historyOpen">{{historyOpen?'收起记录':'对话记录'}}</button><button v-if="selected" aria-label="结束立绘对话" @click="close">结束对话</button></div></div>
  <div ref="transcript" class="portrait-transcript" aria-live="polite">
   <template v-if="selected">
    <p v-if="!selected.expressions.neutral" class="portrait-missing">{{selected.visibility==='voice'?'你听见了'+selected.name+'的传音。':'这个角色暂时没有透明立绘，可以继续文字对话。'}}</p>
    <SceneReply v-for="entry in historyOpen?entries:pending?[]:entries.slice(-1)" :key="entry.turnId" :segments="entry.segments||sceneDialogueSegments(entry.reply,roles,entry.partnerId)" :user="entry.user" :player-id="store.state?.playerId"/>
    <SceneReply v-if="pending" :segments="liveSegments" :user="pending.result.message" :player-id="store.state?.playerId" :stream="{contentId:pending.stream.contentId||pending.id,version:pending.stream.streamVersion||1}"/>
    <p v-if="!entries.length&&!pending" class="portrait-empty">想和{{selected.name}}聊些什么？</p>
    <p v-if="pending&&!liveSegments.length" role="status">{{store.running?'正在回应…':'这次回复尚未完成，已收到的内容仍会保留。'}}</p>
   </template>
  </div>
  <p v-if="store.error" class="portrait-message-error" role="alert">{{store.error}}</p>
  <form class="portrait-composer" @submit.prevent="send"><textarea ref="input" v-model="store.input" rows="1" maxlength="5000" :disabled="head?.state?.ended" :aria-label="selected?'对'+selected.name+'说的话':'你的行动'" :placeholder="selected?'对'+selected.name+'说些什么…':'写下你想做的事…'" @keydown.ctrl.enter.prevent="send"/><button v-if="store.running" type="button" @click="store.cancel">停止</button><button v-else :disabled="disabled||!store.input.trim()">发送</button></form>
  <p class="portrait-footnote">{{selected?'立绘与表情仅用于呈现对话。':'拖动环顾环境。'}}<span v-if="backgroundError">{{backgroundError}}</span><button v-if="backgroundError" @click="data&&paintBackground(data,epoch)">重载环境</button><button @click="emit('text')">纯故事</button></p>
 </div>
 <div v-else class="portrait-explore" aria-label="360度探索操作">
  <div class="portrait-view-tools" aria-label="360度视角控制">
   <button :disabled="!backgroundReady" aria-label="放大场景" @click="zoomBy(10)">＋</button>
   <button :disabled="!backgroundReady" aria-label="缩小场景" @click="zoomBy(-10)">−</button>
   <button :disabled="!backgroundReady" @click="resetView">重置视角</button>
   <button :disabled="!backgroundReady" @click="viewer?.toggleFullscreen()">全屏</button>
   <button :aria-expanded="storyOpen" @click="storyOpen=!storyOpen">现场情况</button>
  </div>
  <p class="portrait-explore-hint">拖动环视环境，点击画面中的路口前往下一地区；选择人物开始对话。</p>
  <p v-if="travelOrigin||store.running" class="portrait-travel-status" role="status">{{travelOrigin?'正在沿路线前行…':'故事正在回应…'}}</p>
  <div v-if="storyOpen" class="portrait-explore-story"><p>{{head?.text||data?.description}}</p></div>
  <p v-if="store.error" class="portrait-message-error" role="alert">{{store.error}}</p>
  <form class="portrait-composer" @submit.prevent="send"><textarea ref="input" v-model="store.input" rows="1" maxlength="5000" :disabled="head?.state?.ended" aria-label="你的行动" placeholder="写下你想做的事…" @keydown.ctrl.enter.prevent="send"/><button v-if="store.running" type="button" @click="store.cancel">停止</button><button v-else :disabled="disabled||!store.input.trim()">发送</button></form>
  <p v-if="backgroundError" class="portrait-footnote">{{backgroundError}}<button @click="data&&paintBackground(data,epoch)">重载环境</button></p>
 </div>
</section></template>
