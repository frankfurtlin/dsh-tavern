<script setup lang="ts">
import {ref,computed,watch,nextTick,onMounted,onUnmounted} from 'vue'
import {ImagePlus,X,ChevronLeft,ChevronRight,ImageOff} from 'lucide-vue-next'
import Sheet from './Sheet.vue'
import ImageEditor from './ImageEditor.vue'
import {imageCapabilities,imagePlacements,imageSource,preferredVariant,imageScopeKey,sameImageRoot,captureImageScope,type ImageScope,type ImagePurpose,type ImagePlacement,type OfflineImages} from '../api/assets'
import {useApp} from '../stores/app'
import {currentImagePlacements} from '../api/image-changes'
import '../styles/assets.css'
const props=withDefaults(defineProps<{scope:ImageScope|null;purpose:ImagePurpose;contentId?:string;paragraphId?:string;side?:'before'|'after';editable?:boolean;passive?:boolean;alt?:string;offline?:OfflineImages;foldable?:boolean}>(),{editable:false,passive:false,foldable:false}),emit=defineEmits<{changed:[];layout:[]}>(),store=useApp()
const items=ref<ImagePlacement[]>([]),sources=ref<string[]>([]),large=ref(''),index=ref(0),editor=ref(false),viewer=ref(false),ready=ref(false),folded=ref(false)
const closeControl=ref<HTMLButtonElement|null>(null)
let generation=0,previewGeneration=0,touch=0
let previousFocus:HTMLElement|null=null
const chosen=computed(()=>items.value[index.value]||items.value[0]),source=computed(()=>sources.value[index.value]||'')
const remaining=computed(()=>items.value.length>1)
const single=computed(()=>props.purpose==='avatar'||props.purpose==='cover')
async function load(){
  const operation=++generation,scope=props.scope
  items.value=[];sources.value=[];large.value='';editor.value=false;viewer.value=false;index.value=0;ready.value=false;previousFocus=null
  if(!scope)return
  const valid=captureImageScope(scope,()=>operation===generation)
  try{
    const capabilities=await imageCapabilities();if(!valid())return;ready.value=!!capabilities
    if(!capabilities&&!props.offline&&store.online)return
    const all=await currentImagePlacements(props.offline?.placements||await imagePlacements(scope),valid)
    if(!valid())return
    let selected=all.filter(item=>sameImageRoot(item.scope,scope)&&item.purpose===props.purpose&&(!('identityId'in scope)||!scope.identityId||item.scope.identityId===scope.identityId)&&(!(scope.kind==='world'&&scope.entityId)||item.scope.kind==='world'&&item.scope.entityId===scope.entityId)&&(!props.contentId||item.contentId===props.contentId)&&(!props.paragraphId||item.paragraphId===props.paragraphId)&&(!props.side||item.side===props.side))
    if(single.value)selected=selected.slice(-1)
    else if(props.purpose==='scene'&&selected.at(-1)?.scope.kind==='world'){const latest=selected.at(-1)!.scope;selected=selected.filter(item=>item.scope.kind==='world'&&latest.kind==='world'&&item.scope.checkpointId===latest.checkpointId)}
    items.value=selected
    for(const item of selected){const data=await imageSource(item.asset,preferredVariant(item.asset,item.purpose),scope,valid,props.offline);if(!valid())return;sources.value.push(data)}
    if(valid())emit('layout')
  }catch{/* Missing/unauthorized images fall back to text without blocking prose. */}
}
async function preview(){
  if(props.passive||!chosen.value||!props.scope)return
  const operation=generation,preview=++previewGeneration,scope=props.scope,asset=chosen.value.asset.assetId,valid=captureImageScope(scope,()=>operation===generation&&preview===previewGeneration&&viewer.value&&chosen.value?.asset.assetId===asset)
  const opened=viewer.value;viewer.value=true
  if(!opened){if(typeof document!=='undefined')previousFocus=document.activeElement instanceof HTMLElement?document.activeElement:null
    await nextTick();if(valid())closeControl.value?.focus({preventScroll:true})}
  try{const value=await imageSource(chosen.value.asset,preferredVariant(chosen.value.asset,props.purpose,true),scope,valid,props.offline);if(valid())large.value=value}catch{}
}
function closePreview(){previewGeneration++;viewer.value=false;large.value='';if(previousFocus?.isConnected)previousFocus.focus({preventScroll:true});previousFocus=null}
function trap(event:KeyboardEvent){const buttons=Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button'));if(!buttons.length)return;const index=buttons.indexOf(document.activeElement as HTMLButtonElement);event.preventDefault();buttons[(index+(event.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus({preventScroll:true})}
async function move(delta:number){index.value=(index.value+delta+items.value.length)%items.value.length;large.value='';await preview()}
function changed(){editor.value=false;emit('changed');void load()}
function update(event:Event){const scope=(event as CustomEvent<{scope:ImageScope}>).detail?.scope;if(scope&&props.scope&&sameImageRoot(scope,props.scope))void load()}
function removed(){generation++;previewGeneration++;items.value=[];sources.value=[];large.value='';editor.value=false;viewer.value=false;ready.value=false;previousFocus=null}
function imageError(){sources.value[index.value]='';emit('layout')}
watch([()=>JSON.stringify([props.scope&&imageScopeKey(props.scope),props.purpose,props.contentId,props.paragraphId,props.side,props.offline?.placements.map(row=>[row.id,row.version,row.asset.sha256])]),()=>store.user?.id,()=>store.online],load,{immediate:true})
onMounted(()=>{window.addEventListener('reader-images-changed',update);window.addEventListener('reader-private-cleared',removed)})
onUnmounted(()=>{generation++;window.removeEventListener('reader-images-changed',update);window.removeEventListener('reader-private-cleared',removed)})
</script>
<template><div class="story-images" :class="['story-images-'+purpose,{folded}]" @click="!passive&&$event.stopPropagation()">
  <button v-if="foldable&&source" class="image-fold" :aria-expanded="!folded" @click="folded=!folded;emit('layout')"><ImageOff :size="15"/>{{folded?'显示图片':'收起图片'}}</button>
  <template v-if="!folded"><img v-if="source&&passive" :src="source" :alt="chosen?.alt||alt||''" class="story-image" @error="imageError" @load="emit('layout')"><button v-else-if="source" class="image-open" :aria-label="'放大'+(chosen?.alt||alt||({cover:'封面',avatar:'人物图',scene:'场景图',illustration:'插图'}[purpose]))" @click="preview"><img :src="source" :alt="chosen?.alt||alt||''" class="story-image" @error="imageError" @load="emit('layout')"></button><slot v-else name="fallback"></slot></template>
  <button v-if="scope&&editable&&ready&&store.online&&(side!=='before'||items.length)" class="image-edit" :aria-label="(source?'调整':'设置')+({cover:'封面',avatar:'人物图',scene:'场景图',illustration:'段落插图'}[purpose])" @click.stop="editor=true"><ImagePlus :size="16"/><span>{{source?'调整图片':'添加图片'}}</span></button>
  <Sheet v-if="editor&&scope" :title="({cover:'设置封面',avatar:'设置人物图',scene:'设置场景图',illustration:'设置段落插图'}[purpose])" @close="editor=false"><ImageEditor :key="imageScopeKey(scope)+purpose+(contentId||'')" :scope="scope" :purpose="purpose" :placement="chosen" :content-id="contentId" :paragraph-id="paragraphId" :side="side" @saved="changed"/></Sheet>
  <Teleport to="body"><div v-if="viewer" class="image-lightbox" role="dialog" aria-modal="true" aria-label="图片预览" @keydown.esc.stop="closePreview" @keydown.tab="trap($event)" @keydown.left="remaining&&move(-1)" @keydown.right="remaining&&move(1)" @touchstart.passive="touch=$event.changedTouches[0]?.clientX||0" @touchend.passive="remaining&&Math.abs(($event.changedTouches[0]?.clientX||0)-touch)>40&&move(($event.changedTouches[0]?.clientX||0)>touch?-1:1)"><button ref="closeControl" class="image-view-close" aria-label="关闭图片预览" @click="closePreview"><X :size="22"/></button><button v-if="remaining" class="image-view-prev" aria-label="上一张图片" @click="move(-1)"><ChevronLeft/></button><img v-if="large||source" :src="large||source" :alt="chosen?.alt||alt||''" @error="large='';imageError()"><button v-if="remaining" class="image-view-next" aria-label="下一张图片" @click="move(1)"><ChevronRight/></button><p>{{chosen?.alt}}<small>{{chosen?.asset.licenseNote}}</small></p></div></Teleport>
</div></template>
