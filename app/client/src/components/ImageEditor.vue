<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue'
import {api,newKey,ApiError} from '../api'
import {imageCapabilities,imagePlacements,imageScopeKey,captureImageScope,imagesChanged,type ImageScope,type ImagePurpose,type ImageCrop,type StoryImage,type ImagePlacement} from '../api/assets'
import {cacheGet,cacheSet} from '../platform/bridge'
import {recordImageChange} from '../api/image-changes'
import {useApp} from '../stores/app'
const props=defineProps<{scope:ImageScope;purpose:ImagePurpose;placement?:ImagePlacement;contentId?:string;paragraphId?:string;side?:'before'|'after'}>(),emit=defineEmits<{saved:[];close:[]}>(),store=useApp()
interface Intent {phase:'upload'|'place'|'delete';key:string;scope:ImageScope;purpose:ImagePurpose;crop?:ImageCrop;fileHash?:string;licenseNote?:string;asset?:StoryImage;body?:Record<string,unknown>;placementId?:string;rejected?:boolean}
const file=ref<File|null>(null),preview=ref(''),width=ref(0),height=ref(0),crop=ref<ImageCrop>({x:0,y:0,width:0,height:0}),license=ref(''),rights=ref(false),busy=ref(false),error=ref(''),ready=ref(false),intent=ref<Intent|null>(null),lookupMissing=ref(false),alt=ref(props.placement?.alt||'')
const currentPlacement=ref<ImagePlacement|undefined>(),uploaded=ref<StoryImage|null>(null),stale=ref(false)
let version=0,url=''
const ratio=computed(()=>props.purpose==='cover'?2/3:props.purpose==='avatar'?1:0)
// Preserve the legacy after key. A before placement owns an independent
// unknown request, so reopening the other side never resumes it accidentally.
const key=()=> 'image-intent:'+encodeURIComponent(imageScopeKey(props.scope))+':'+props.purpose+':'+(props.contentId||'')+':'+(props.paragraphId||'')+((props.placement?.side||props.side)==='before'?':before':'')
const validCrop=computed(()=>Object.values(crop.value).every(Number.isInteger)&&crop.value.width>0&&crop.value.height>0&&crop.value.x>=0&&crop.value.y>=0&&crop.value.x+crop.value.width<=width.value&&crop.value.y+crop.value.height<=height.value)
const canSave=computed(()=>ready.value&&store.online&&!busy.value&&!stale.value&&intent.value?.phase!=='delete'&&!!(intent.value||uploaded.value||file.value&&rights.value&&license.value.trim()&&validCrop.value))
const previewScale=computed(()=>Math.min(280/Math.max(1,crop.value.width),420/Math.max(1,crop.value.height)))
const previewStyle=computed(()=>{const scale=previewScale.value;return {width:width.value*scale+'px',height:height.value*scale+'px',transform:'translate('+(-crop.value.x*scale)+'px,'+(-crop.value.y*scale)+'px)'}})
const previewBox=computed(()=>({width:(crop.value.width*previewScale.value)+'px',height:(crop.value.height*previewScale.value)+'px'}))
const exactExisting=computed(()=>currentPlacement.value&&imageScopeKey(currentPlacement.value.scope)===imageScopeKey(props.scope)?currentPlacement.value:undefined)
const title=computed(()=>({cover:'封面',avatar:'人物图',scene:'场景图',illustration:'段落插图'})[props.purpose])
function release(){if(url){URL.revokeObjectURL(url);url=''}preview.value='';file.value=null}
function changeSize(axis:'width'|'height',value:string){
  const size=Math.max(1,Math.round(Number(value)||1));crop.value[axis]=size
  if(ratio.value)crop.value[axis==='width'?'height':'width']=Math.round(axis==='width'?size/ratio.value:size*ratio.value)
}
async function selected(event:Event){
  if(intent.value||stale.value)return
  const selected=(event.target as HTMLInputElement).files?.[0];if(!selected)return
  const operation=++version,valid=captureImageScope(props.scope,()=>version===operation)
  release();uploaded.value=null;error.value=''
  if(!['image/jpeg','image/png','image/webp'].includes(selected.type)||selected.size>10*1024*1024){error.value='请选择不超过 10 MiB 的 JPEG、PNG 或 WebP 图片。';return}
  url=URL.createObjectURL(selected);const local=url,image=new Image()
  try{await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(new Error('这张图片无法打开'));image.src=local});if(!valid()){release();return}
    width.value=image.naturalWidth;height.value=image.naturalHeight
    if(Math.max(width.value,height.value)>8192||width.value*height.value>32*1024*1024)throw new Error('图片最长边须不超过 8192 像素。')
    const w=ratio.value?Math.min(width.value,Math.floor(height.value*ratio.value)):width.value,h=ratio.value?Math.floor(w/ratio.value):height.value
    crop.value={x:Math.floor((width.value-w)/2),y:Math.floor((height.value-h)/2),width:w,height:h};file.value=selected;preview.value=local
  }catch(e){if(valid()){error.value=(e as Error).message;release()}}
}
async function remember(value:Intent|null,valid:()=>boolean){await cacheSet(key(),value);if(!valid())throw new ApiError('SESSION_CHANGED','故事或账号已变化',401);intent.value=value}
async function find(valid:()=>boolean){
  const pending=intent.value;if(!pending)return
  if(pending.rejected){stale.value=true;return}
  lookupMissing.value=false
  if(pending.phase==='upload'){
    const found=await api<{found:boolean;deleted?:boolean;asset?:StoryImage}>('/assets/requests?requestId='+encodeURIComponent(pending.key));if(!valid())return
    if(!found.found){lookupMissing.value=true;return}
    if(found.deleted||!found.asset)throw new Error('原上传图片已移除，请关闭后重新选择图片。')
    await remember({...pending,phase:'place',key:newKey(),asset:found.asset,body:placementBody(found.asset)},valid)
  }else{
    const path=pending.phase==='delete'?'/asset-placements/'+pending.placementId+'/deletion':'/asset-placements/requests'
    const found=await api<{found:boolean;deleted?:boolean;placementId?:string;receipt?:ImagePlacement|{placementId:string}}>(path+'?requestId='+encodeURIComponent(pending.key));if(!valid())return
    if(!found.found){lookupMissing.value=true;return}
    if(found.deleted&&pending.phase==='place'){
      if(!found.placementId)throw new Error('图片移除回执不完整，请稍后确认。')
      await recordImageChange(props.scope,found.placementId,0,true,valid);if(!valid())return
      await remember(null,valid);if(valid()){imagesChanged(props.scope);emit('saved')}return
    }
    if(pending.phase==='delete')await recordImageChange(props.scope,pending.placementId!,0,true,valid)
    else if(found.receipt&&'id'in found.receipt)await recordImageChange(props.scope,found.receipt.id,found.receipt.version,false,valid)
    else throw new Error('图片回执不完整，请稍后确认。')
    if(!valid())return
    await remember(null,valid);if(valid()){imagesChanged(props.scope);emit('saved')}
  }
}
async function rejected(e:unknown,valid:()=>boolean){
  if(!valid())return
  if(e instanceof ApiError&&e.code==='ASSET_CONFLICT'&&e.status===409){
    stale.value=true;error.value='图片已在另一设备变化。载入最新信息后再保存，本机说明和已上传图片会保留。'
    if(intent.value){try{await remember({...intent.value,rejected:true},valid)}catch{if(valid())error.value+=' 本机请求记录尚未更新，请先载入最新信息。'}}
  }else if(e instanceof ApiError&&[400,413,415,422].includes(e.status)){
    // A definitive validation rejection has no committed image receipt. Keep
    // the selected file/crop/description editable and require another click.
    const asset=intent.value?.phase==='place'?intent.value.asset:null
    try{await remember(null,valid);if(valid()){uploaded.value=asset||null;error.value=e.message}}
    catch{if(valid())error.value=e.message+' 本机请求记录尚未清除，请先确认提交结果。'}
  }else error.value=(e as Error).message
}
async function refresh(){
  if(busy.value||!stale.value||!store.online)return
  const operation=++version,valid=captureImageScope(props.scope,()=>operation===version);busy.value=true
  try{
    const pending=intent.value,rows=await imagePlacements(props.scope,false);if(!valid())return
    const selected=exactExisting.value?.id||pending?.placementId||String(pending?.body?.placementId||'')
    const row=rows.find(value=>value.id===selected&&imageScopeKey(value.scope)===imageScopeKey(props.scope))
    const asset=pending?.phase==='place'?pending.asset:uploaded.value
    await remember(null,valid);if(!valid())return
    currentPlacement.value=row;uploaded.value=asset||null;stale.value=false;lookupMissing.value=false
    error.value=row?'最新信息已载入。确认本机说明后再次保存。':'这处图片已移除。可明确保存为一处新图片。'
  }catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
function placementBody(asset:StoryImage){
  const row=exactExisting.value
  return {assetId:asset.assetId,scope:props.scope,purpose:props.purpose,contentId:props.contentId||null,paragraphId:props.paragraphId||null,side:row?.side||props.side||'after',order:row?.order||0,alt:alt.value,
    ...(row?{placementId:row.id,expectedVersion:row.version}:{})}
}
async function query(){
  const operation=++version,valid=captureImageScope(props.scope,()=>operation===version);busy.value=true;error.value=''
  try{await find(valid)}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
async function save(){
  if(!canSave.value)return
  const operation=++version,valid=captureImageScope(props.scope,()=>operation===version);busy.value=true;error.value=''
  try{
    if(intent.value){await find(valid);if(!valid())return;if(!intent.value)return;if(intent.value.phase==='delete')throw new Error('请先确认移除结果。')}
    if(!intent.value){
      if(uploaded.value)await remember({phase:'place',key:newKey(),scope:props.scope,purpose:props.purpose,asset:uploaded.value,body:placementBody(uploaded.value)},valid)
      else{
        if(!file.value||!rights.value||!license.value.trim()||!validCrop.value)throw new Error('请确认裁切与图片使用权。')
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.value.arrayBuffer())),n=>n.toString(16).padStart(2,'0')).join('');if(!valid())return
        await remember({phase:'upload',key:newKey(),scope:props.scope,purpose:props.purpose,crop:{...crop.value},fileHash:hash,licenseNote:license.value.trim()},valid)
      }
    }
    let pending=intent.value!
    if(pending.phase==='upload'){
      if(!file.value)throw new Error('本机没有保存原图片。请先确认提交结果，未找到时可重新选择同一文件。')
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.value.arrayBuffer())),n=>n.toString(16).padStart(2,'0')).join('');if(!valid())return
      if(hash!==pending.fileHash)throw new Error('所选文件与待确认的图片不同，请先处理原提交。')
      const form=new FormData();form.append('file',file.value);form.append('scope',JSON.stringify(pending.scope));form.append('purpose',pending.purpose);form.append('crop',JSON.stringify(pending.crop));form.append('licenseNote',pending.licenseNote||'');form.append('rightsConfirmed','true')
      const asset=await api<StoryImage>('/assets',{method:'POST',key:pending.key,body:form});if(!valid())return
      pending={...pending,phase:'place',key:newKey(),asset,body:placementBody(asset)};await remember(pending,valid)
    }
    if(pending.phase==='place'){
      const saved=await api<ImagePlacement>('/asset-placements',{method:'POST',key:pending.key,body:pending.body});if(!valid())return
      await recordImageChange(props.scope,saved.id,saved.version,false,valid);if(!valid())return
      await remember(null,valid);if(valid()){imagesChanged(props.scope);emit('saved')}
    }
  }catch(e){await rejected(e,valid)}
  finally{if(valid())busy.value=false}
}
async function remove(){
  const row=exactExisting.value;if(!row||busy.value||!store.online||stale.value)return
  const operation=++version,valid=captureImageScope(props.scope,()=>operation===version);busy.value=true;error.value=''
  try{
    if(intent.value){await find(valid);if(!valid()||!intent.value)return;if(intent.value.phase!=='delete')throw new Error('请先确认上传结果。')}
    else await remember({phase:'delete',key:newKey(),scope:props.scope,purpose:props.purpose,placementId:row.id,body:{expectedVersion:row.version}},valid)
    const pending=intent.value!;await api('/asset-placements/'+pending.placementId,{method:'DELETE',key:pending.key,body:pending.body});if(!valid())return
    await recordImageChange(props.scope,pending.placementId!,0,true,valid);if(!valid())return
    await remember(null,valid);if(valid()){imagesChanged(props.scope);emit('saved')}
  }catch(e){await rejected(e,valid)}finally{if(valid())busy.value=false}
}
async function reselect(event:Event){const same=(event.target as HTMLInputElement).files?.[0];if(same)file.value=same}
watch([()=>imageScopeKey(props.scope),()=>props.purpose,()=>props.contentId,()=>props.paragraphId,()=>props.side,()=>props.placement?.side,()=>store.user?.id],async()=>{
  const operation=++version,valid=captureImageScope(props.scope,()=>operation===version);release();ready.value=false;intent.value=null;uploaded.value=null;stale.value=false;currentPlacement.value=props.placement;alt.value=props.placement?.alt||'';error.value='';busy.value=false
  try{const capabilities=await imageCapabilities();if(!valid())return;ready.value=!!capabilities;const saved=await cacheGet<Intent>(key());if(!valid())return;intent.value=saved||null;if(saved){if(typeof saved.body?.alt==='string')alt.value=saved.body.alt;await find(valid)}}
  catch(e){if(valid())error.value=(e as Error).message}
},{immediate:true})
function cleared(){version++;release();intent.value=null;uploaded.value=null;currentPlacement.value=undefined;ready.value=false;error.value='';stale.value=false}
onMounted(()=>window.addEventListener('reader-private-cleared',cleared))
onUnmounted(()=>{version++;release();window.removeEventListener('reader-private-cleared',cleared)})
</script>
<template><section class="image-editor" :aria-label="title+'设置'"><p v-if="!ready" class="muted">图片服务暂不可用，正文仍然可读。</p><template v-else><p v-if="intent&&!stale" class="hint">有一份图片提交待确认。不会自动重复上传。</p><p v-if="error" class="error" role="alert">{{error}}</p><template v-if="!intent"><label class="image-file-label">选择{{title}}<input type="file" accept="image/jpeg,image/png,image/webp" :disabled="busy" @change="selected"></label><template v-if="preview"><div class="image-crop-preview" :style="previewBox"><img :src="preview" alt="裁切预览" :style="previewStyle"></div><div class="image-crop-fields"><label>左边<input v-model.number="crop.x" type="number" min="0" :max="Math.max(0,width-crop.width)"></label><label>上边<input v-model.number="crop.y" type="number" min="0" :max="Math.max(0,height-crop.height)"></label><label>宽度<input :value="crop.width" type="number" min="1" :max="width" @input="changeSize('width',($event.target as HTMLInputElement).value)"></label><label>高度<input :value="crop.height" type="number" min="1" :max="height" @input="changeSize('height',($event.target as HTMLInputElement).value)"></label></div><small>{{ratio?'裁切比例已固定，移动图片边界以保留人物和画面。':'图片会保留当前裁切比例。'}}</small></template><label>图片说明<input v-model="alt" maxlength="300" placeholder="简短描述画面"></label><label>来源与许可<input v-model="license" maxlength="1000" placeholder="例如：本人拍摄，可用于私人阅读"></label><label class="image-rights"><input v-model="rights" type="checkbox">我有权使用这张图片</label></template><label v-else-if="intent.phase==='upload'&&lookupMissing&&!file">重新选择同一文件<input type="file" accept="image/jpeg,image/png,image/webp" @change="reselect"></label><div class="actions"><button v-if="stale" :disabled="busy||!store.online" @click="refresh">载入最新信息</button><button v-if="intent&&!stale" :disabled="busy||!store.online" @click="query">确认图片结果</button><button v-if="intent?.phase!=='delete'" class="primary" :disabled="!canSave" @click="save">{{busy?'正在保存…':intent?'继续保存图片':uploaded?'保存图片':'确认裁切并保存'}}</button><button v-if="exactExisting&&(!intent||intent.phase==='delete')" class="danger" :disabled="busy||!store.online||stale" @click="remove">移除这处图片</button></div></template></section></template>
