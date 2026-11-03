<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {Check,ImagePlus,LoaderCircle,Palette,RefreshCw,UserRound} from 'lucide-vue-next'
import {ApiError,authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {worldApi} from '../world/api'
import {worldMutation,pendingWorldMutation,clearWorldMutation} from '../world/mutation-intent'
import type {CharacterArt} from '../world/scene-types'
import type {ImageCrop} from '../api/assets'
import Sheet from './Sheet.vue'
import AvatarCropper from './AvatarCropper.vue'
type Appearance={age:string;hair:string;clothing:string;temperament:string}
type Portrait=CharacterArt & {avatarUrl?:string;avatarCrop?:ImageCrop;createdAt:string;appearance:Partial<Appearance>}
type Look={entityId:string;name:string;description:string;version:number;selectedId:string|null;versions:Portrait[]}
const props=withDefaults(defineProps<{worldId:string;lineId?:string;disabled?:boolean;trigger?:boolean}>(),{trigger:true})
const emit=defineEmits<{previews:[Record<string,string>];busy:[boolean]}>(),capture=useRequestScope(()=>[props.worldId,props.lineId])
const looks=ref<Look[]>([]),open=ref(false),focused=ref(''),urls=ref<Record<string,string>>({}),loading=ref(true),tab=ref<'ai'|'upload'|'history'>('ai'),candidateId=ref('')
const error=ref(''),imageError=ref(''),busy=ref(false),batch=ref(false),stopping=ref(false),progress=ref(''),uncertain=ref(false)
const defaults=():Appearance=>({age:'',hair:'',clothing:'',temperament:''}),appearance=ref<Appearance>(defaults())
const crop=ref<ImageCrop>({x:0,y:0,width:1,height:1}),uploadData=ref(''),uploadWidth=ref(0),uploadHeight=ref(0),license=ref(''),rights=ref(false)
const profile=ref<{enabled:boolean;hasKey:boolean;modelId:string;dailyLimit:number}>()
let epoch=0,timer:ReturnType<typeof setTimeout>|undefined
const fetching=new Set<string>(),active=computed(()=>looks.value.find(c=>c.entityId===focused.value)||looks.value[0])
const pending=(c:Look)=>c.versions.find(v=>['queued','running','saving'].includes(v.status)),selected=(c:Look)=>c.versions.find(v=>v.id===c.selectedId)
const total=computed(()=>looks.value.length),ready=computed(()=>looks.value.filter(c=>c.selectedId).length),missing=computed(()=>looks.value.filter(c=>!c.selectedId&&!pending(c)&&!c.versions.some(v=>v.status==='ready')))
const slots=()=>({generate:'look-generate:'+props.worldId,select:'look-select:'+props.worldId,upload:'look-upload:'+props.worldId})
const preview=computed(()=>active.value?.versions.find(v=>v.id===candidateId.value&&v.status==='ready')||active.value?.versions.find(v=>v.id===active.value?.selectedId)||active.value?.versions.find(v=>v.status==='ready'))
const previewUrl=computed(()=>tab.value==='upload'?uploadData.value:urls.value[preview.value?.id||''])
const dimensions=computed(()=>tab.value==='upload'?{width:uploadWidth.value,height:uploadHeight.value}:{width:preview.value?.width||0,height:preview.value?.height||0})
const busyRole=computed(()=>active.value&&pending(active.value))
function setCrop(w:number,h:number,existing?:ImageCrop){const side=Math.max(1,Math.min(w,Math.floor(h*.3)));crop.value=existing?{...existing}:{x:Math.max(0,Math.floor((w-side)/2)),y:Math.min(h-side,Math.max(0,Math.floor(h*.03))),width:side,height:side}}
function edit(c:Look){focused.value=c.entityId;appearance.value={...defaults(),...(selected(c)?.appearance||{})};candidateId.value=c.selectedId||'';uploadData.value='';rights.value=false;error.value='';const art=selected(c);if(art?.width&&art.height)setCrop(art.width,art.height,art.avatarCrop)}
function publish(){emit('previews',Object.fromEntries(looks.value.flatMap(c=>{const src=urls.value['avatar:'+c.selectedId]||urls.value[c.selectedId||''];return c.selectedId&&src?[[c.entityId,src]]:[]})))}
function reset(){epoch++;clearTimeout(timer);fetching.clear();for(const url of Object.values(urls.value))URL.revokeObjectURL(url);urls.value={};looks.value=[];batch.value=false;busy.value=false;stopping.value=false;progress.value='';error.value='';imageError.value='';uncertain.value=false;uploadData.value='';emit('previews',{});emit('busy',false)}
async function fetchPicture(key:string,url:string,valid:()=>boolean){
 if(urls.value[key]||fetching.has(key))return;fetching.add(key)
 try{const r=await fetch(backendBase()+url,{credentials:'include',headers:authorizationHeaders()});if(!r.ok)throw Error('图片暂时无法加载');const blob=await r.blob();if(valid())urls.value={...urls.value,[key]:URL.createObjectURL(blob)}}catch{if(valid())imageError.value='部分图片未能加载，可以刷新重试。'}finally{fetching.delete(key)}
}
async function images(valid:()=>boolean){
 imageError.value='';const requests:Promise<void>[]=[]
 for(const c of looks.value)for(const art of c.versions.filter(v=>v.status==='ready')){
  if(art.id===c.selectedId&&art.avatarUrl)requests.push(fetchPicture('avatar:'+art.id,art.avatarUrl,valid))
  if(art.imageUrl&&(open.value&&c.entityId===active.value?.entityId||art.id===c.selectedId&&!art.avatarUrl))requests.push(fetchPicture(art.id,art.imageUrl,valid))
 }
 await Promise.all(requests);if(valid())publish()
}
async function load(schedule=true){
 const n=epoch,scope=capture(),valid=()=>n===epoch&&scope.valid();clearTimeout(timer)
 try{const result=await worldApi<{characters:Look[]}>('/worlds/'+props.worldId+'/character-looks'+(props.lineId?'?lineId='+encodeURIComponent(props.lineId):''));if(!valid())return;looks.value=result.characters;if(!focused.value&&looks.value[0])edit(looks.value[0]);await images(valid);if(valid()&&schedule&&looks.value.some(pending))timer=setTimeout(()=>void load(),1800)}catch(e){if(valid()){error.value=(e as Error).message;if(schedule&&looks.value.some(pending))timer=setTimeout(()=>void load(),3500)}}finally{if(valid())loading.value=false}
}
async function mutation<T>(slot:string,path:string,body:unknown,valid:()=>boolean){
 try{const value=await worldMutation<T>(slot,path,body,valid);if(valid())await clearWorldMutation(slot,valid);return value}catch(e){if(valid()){if(e instanceof ApiError&&e.status>=400&&e.status<500&&e.status!==408)await clearWorldMutation(slot,valid);uncertain.value=!!await pendingWorldMutation(slot)}throw e}
}
async function generate(c:Look,overrides:Appearance){
 const n=epoch,scope=capture(),valid=()=>n===epoch&&scope.valid()
 const result=await mutation<CharacterArt>(slots().generate,'/worlds/'+props.worldId+'/character-looks',{entityId:c.entityId,appearance:overrides,regenerate:c.versions.length>0,...(props.lineId?{lineId:props.lineId}:{})},valid)
 if(!valid())return null;candidateId.value=result.id;await load(false);return{id:result.id,valid}
}
async function makeOne(){const c=active.value;if(!c||busy.value||batch.value||pending(c)||props.disabled||uncertain.value)return;const scope=capture();busy.value=true;error.value='';try{await generate(c,{...appearance.value});if(scope.valid())void load()}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
async function fillMissing(){
 if(batch.value||busy.value||props.disabled||uncertain.value||!missing.value.length)return
 const n=epoch,scope=capture(),valid=()=>n===epoch&&scope.valid(),ids=missing.value.map(c=>c.entityId)
 batch.value=true;stopping.value=false;error.value='';emit('busy',true)
 try{for(const [i,id]of ids.entries()){
  if(!valid()||stopping.value)break;const c=looks.value.find(c=>c.entityId===id);if(!c||c.selectedId)continue
  progress.value=(i+1)+' / '+ids.length+' · '+c.name;const submitted=await generate(c,defaults());if(!submitted||!valid())break
  let done=false
  for(let j=0;j<220&&valid();j++){await new Promise(r=>setTimeout(r,1800));if(!valid())break;const art=await worldApi<CharacterArt>('/scenes/'+submitted.id);if(!valid())break;if(art.status==='ready'){done=true;await load(false);break}if(['failed','unknown'].includes(art.status))throw Error(c.name+'的形象未完成，已有版本保留。')}
  if(valid()&&!done)throw Error('图片仍在生成，稍后查看即可。批量队列已停止。')
 }}catch(e){if(valid())error.value=(e as Error).message}finally{if(valid()){batch.value=false;stopping.value=false;progress.value='';emit('busy',false);void load()}}
}
async function choose(art:Portrait,withCrop=false){
 const c=active.value;if(!c||busy.value||batch.value||props.disabled||uncertain.value||!withCrop&&art.id===c.selectedId)return
 const scope=capture();busy.value=true;error.value=''
 try{const result=await mutation<{selectedId:string}>(slots().select,'/worlds/'+props.worldId+'/character-looks/'+c.entityId+'/selection',{sceneId:art.id,expectedVersion:c.version,...(props.lineId?{lineId:props.lineId}:{}),...(withCrop?{crop:{...crop.value}}:{})},scope.valid);if(scope.valid()){candidateId.value=result.selectedId;await load();const updated=looks.value.find(row=>row.entityId===c.entityId);if(updated)edit(updated)}}catch(e){if(scope.valid()){error.value=(e as Error).message;if(e instanceof ApiError&&e.code==='CHARACTER_LOOK_CHANGED')await load()}}finally{if(scope.valid())busy.value=false}
}
async function selectedFile(event:Event){
 const file=(event.target as HTMLInputElement).files?.[0];if(!file)return
 error.value='';if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>10*1024*1024){error.value='请选择不超过 10 MiB 的 PNG、JPEG 或 WebP 图片。';return}
 const scope=capture(),n=epoch,entityId=active.value?.entityId,url=URL.createObjectURL(file)
 try{const image=new Image();await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(Error('图片无法打开'));image.src=url});if(!scope.valid()||n!==epoch||active.value?.entityId!==entityId)return;if(image.naturalWidth*image.naturalHeight>16*1024*1024||Math.max(image.naturalWidth,image.naturalHeight)>8192)throw Error('图片尺寸过大')
  const scale=Math.min(1,1536/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.round(image.naturalWidth*scale);canvas.height=Math.round(image.naturalHeight*scale);const context=canvas.getContext('2d');if(!context)throw Error('无法准备图片');context.drawImage(image,0,0,canvas.width,canvas.height);const data=canvas.toDataURL('image/webp',.94);if(data.length>4*1024*1024)throw Error('图片文件仍然过大');uploadData.value=data;uploadWidth.value=canvas.width;uploadHeight.value=canvas.height;setCrop(canvas.width,canvas.height);rights.value=false
 }catch(e){if(scope.valid())error.value=(e as Error).message}finally{URL.revokeObjectURL(url)}
}
async function useUpload(){
 const c=active.value;if(!c||!uploadData.value||!rights.value||!license.value.trim()||busy.value||props.disabled||uncertain.value)return
 const scope=capture();busy.value=true;error.value=''
 try{await mutation(slots().upload,'/worlds/'+props.worldId+'/character-looks/upload',{entityId:c.entityId,expectedVersion:c.version,...(props.lineId?{lineId:props.lineId}:{}),imageDataUrl:uploadData.value,crop:{...crop.value},licenseNote:license.value.trim(),rightsConfirmed:true},scope.valid);if(scope.valid()){uploadData.value='';tab.value='history';await load();const updated=looks.value.find(r=>r.entityId===c.entityId);if(updated)edit(updated)}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}
}
async function recover(){const scope=capture();busy.value=true;error.value='';try{for(const slot of Object.values(slots())){const intent=await pendingWorldMutation(slot);if(!scope.valid())return;if(intent)await mutation(slot,intent.path,intent.body,scope.valid)}if(scope.valid()){uncertain.value=false;await load()}}catch(e){if(scope.valid())error.value=(e as Error).message}finally{if(scope.valid())busy.value=false}}
function close(){open.value=false;if(batch.value)stopping.value=true}
async function show(entityId?:string){open.value=true;if(entityId){focused.value=entityId;const person=looks.value.find(c=>c.entityId===entityId);if(person)edit(person)}const scope=capture();await Promise.all([load(),worldApi<typeof profile.value>('/scene-profile').then(value=>{if(scope.valid())profile.value=value}).catch(()=>{})])}
defineExpose({show,load})
watch(()=>[props.worldId,props.lineId],async()=>{reset();focused.value='';loading.value=true;const scope=capture();await load();if(!scope.valid())return;for(const slot of Object.values(slots()))if(await pendingWorldMutation(slot))uncertain.value=true},{immediate:true})
watch([focused,open],()=>{const scope=capture();void images(scope.valid)})
watch(()=>[preview.value?.id,tab.value],()=>{const p=preview.value;if(tab.value!=='upload'&&p?.width&&p.height)setCrop(p.width,p.height,p.avatarCrop)})
onUnmounted(reset)
</script>
<template>
 <div v-if="trigger" class="look-toolbar" aria-label="角色形象库"><button type="button" :disabled="disabled||loading" @click="show()"><Palette :size="17"/>角色形象 <span>{{ready}} / {{total}}</span></button><span v-if="batch" role="status">{{progress}}</span></div>
 <Sheet v-if="open" :title="active?active.name+' · 角色形象':'角色形象'" class="look-sheet" @close="close">
  <div class="look-library">
   <p v-if="error" role="alert" class="error">{{error}}</p><p v-if="imageError" role="status">{{imageError}} <button @click="load()">刷新图片</button></p>
   <div v-if="uncertain" class="look-notice">上次操作结果尚未确认。<button :disabled="busy" @click="recover">确认上次请求</button></div>
   <div class="look-roster" role="group" aria-label="选择形象角色"><button v-for="c in looks" :key="c.entityId" :aria-pressed="active?.entityId===c.entityId" :disabled="busy||batch" @click="edit(c)"><img v-if="c.selectedId&&(urls['avatar:'+c.selectedId]||urls[c.selectedId])" :src="urls['avatar:'+c.selectedId]||urls[c.selectedId]" alt=""><UserRound v-else :size="24"/><span>{{c.name}}</span></button></div>
   <p v-if="loading" role="status">正在读取角色形象…</p><p v-else-if="!active">保存角色设定后，可在这里生成或上传形象。</p>
   <template v-if="active">
    <nav class="look-tabs" aria-label="角色形象来源"><button :aria-pressed="tab==='ai'" @click="tab='ai'">AI 生成</button><button :aria-pressed="tab==='upload'" @click="tab='upload'">上传图片</button><button :aria-pressed="tab==='history'" @click="tab='history'">历史版本</button></nav>
    <div v-if="tab==='ai'" class="look-appearance"><h3>{{active.name}}</h3><p>{{active.description}}</p><form @submit.prevent="makeOne"><div class="look-fields"><label>外观年龄<input v-model="appearance.age" maxlength="80" placeholder="沿用角色设定"></label><label>发型与发色<input v-model="appearance.hair" maxlength="180" placeholder="例如：黑色短发"></label><label>衣着<input v-model="appearance.clothing" maxlength="250" placeholder="沿用故事背景与服装"></label><label>气质与特征<input v-model="appearance.temperament" maxlength="180" placeholder="例如：沉稳、清瘦"></label></div><button class="primary" :disabled="disabled||busy||batch||!!busyRole||uncertain"><LoaderCircle v-if="busy||busyRole" class="look-spin" :size="17"/><RefreshCw v-else-if="active.versions.length" :size="17"/><ImagePlus v-else :size="17"/>{{busyRole?'正在生成…':active.versions.length?'生成新版本':'生成形象'}}</button><p class="look-hint">沿用故事画风。每次生成提交 1 个出图任务，头像裁切不额外调用模型；采用前不会替换现有形象。<span v-if="profile?.modelId"> 当前模型：{{profile.modelId}}。{{profile.dailyLimit===0?'不限出图次数':'每日上限 '+profile.dailyLimit+' 次'}}。</span>供应商费用以实际账单为准。</p></form></div>
    <div v-if="tab==='upload'" class="look-upload"><label>选择角色图片<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="busy||uncertain||disabled" @change="selectedFile"></label><label>来源与许可<input v-model="license" maxlength="1000" placeholder="例如：本人创作，可用于本作品"></label><label class="look-check"><input v-model="rights" type="checkbox">我有权使用这张图片</label></div>
    <div v-if="tab==='history'" class="look-version-grid" aria-label="角色形象历史"><button v-for="art in active.versions" :key="art.id" :aria-pressed="preview?.id===art.id" :disabled="art.status!=='ready'" @click="candidateId=art.id"><img v-if="urls[art.id]" :src="urls[art.id]" :alt="active.name+'历史立绘'"><UserRound v-else :size="26"/><span>{{art.id===active.selectedId?'当前形象':art.status==='ready'?'预览此版本':art.status==='unknown'?'结果待核对':['queued','running','saving'].includes(art.status)?'生成中':'未完成'}}</span><small>{{new Date(art.createdAt).toLocaleString('zh-CN')}}</small></button><p v-if="!active.versions.length">还没有形象版本。</p></div>
    <AvatarCropper v-if="previewUrl&&dimensions.width&&dimensions.height" v-model="crop" :src="previewUrl" :width="dimensions.width" :height="dimensions.height"/>
    <p v-else-if="busyRole" class="look-notice" role="status">图片正在生成，关闭后也会继续保存；重新打开可查看进度。</p>
    <div class="look-adopt"><button v-if="tab==='upload'" class="primary" :disabled="busy||disabled||uncertain||!uploadData||!rights||!license.trim()" @click="useUpload">使用这套形象</button><button v-else-if="preview&&previewUrl" class="primary" :disabled="busy||batch||disabled||uncertain" @click="choose(preview,true)"><Check :size="17"/>使用这套形象</button></div>
    <details class="look-batch"><summary>批量准备角色形象</summary><p>为 {{missing.length}} 个尚未采用形象的角色准备候选图。每个角色会新增出图任务，完成后仍需分别预览采用。</p><button v-if="!batch" :disabled="disabled||busy||uncertain||!missing.length" @click="fillMissing">生成缺失形象</button><button v-else :disabled="stopping" @click="stopping=true">{{stopping?'当前图片完成后停止':'停止批量生成'}}</button><p v-if="batch" role="status">{{progress}}</p></details>
    <p class="look-footnote">形象只影响展示。主线时间、物品、关系和结局保持原状。已生成的场景不会自动重绘，下次出图会参考当前形象。</p>
   </template>
  </div>
 </Sheet>
</template>
<style scoped>
:global(.look-sheet .sheet){width:min(780px,100vw)}.look-toolbar{display:flex;align-items:center;gap:12px;margin:14px 0}.look-toolbar button{gap:8px;font-size:13px}.look-toolbar span{color:var(--quiet);font-size:12px}.look-library{color:var(--ink)}.look-library button{min-height:44px;display:inline-flex;align-items:center;justify-content:center;gap:7px}.look-library :is(input,button):focus-visible{outline:2px solid var(--accent);outline-offset:3px}.look-roster{display:flex;gap:8px;overflow-x:auto;padding:4px 2px 14px}.look-roster button{display:flex;flex-direction:column;min-width:76px;gap:6px;padding:10px;background:var(--canvas);color:var(--quiet);border:1px solid var(--edge);border-radius:10px;font-size:12px}.look-roster button[aria-pressed=true],.look-version-grid button[aria-pressed=true]{border-color:var(--accent);background:var(--wash);color:var(--accent)}.look-roster img{width:40px;height:40px;border-radius:50%;object-fit:cover}.look-tabs{display:flex;gap:20px;border-bottom:1px solid var(--edge);margin-bottom:20px}.look-tabs button{border:0;border-radius:0;padding:10px 0;background:transparent;color:var(--quiet);border-bottom:2px solid transparent;font-size:14px}.look-tabs button[aria-pressed=true]{border-bottom-color:var(--accent);color:var(--accent)}.look-appearance h3{font-size:18px;margin:0 0 8px}.look-appearance>p{font-size:13px;color:var(--quiet);line-height:1.7;margin-bottom:18px}.look-fields{display:grid;grid-template-columns:1fr 1fr;gap:14px}.look-fields label,.look-upload>label{display:grid;gap:8px;margin:0;font-size:13px}.look-fields input,.look-upload input:not([type=checkbox]){width:100%;font-size:14px;margin:0;min-height:44px}.look-appearance form>button{margin-top:18px}.look-hint,.look-footnote,.look-batch p{font-size:12px;line-height:1.8;color:var(--quiet)}.look-upload{display:grid;gap:18px;margin-bottom:22px}.look-upload .look-check{display:flex;align-items:center;gap:10px}.look-check input{width:20px;min-height:20px;margin:0;accent-color:var(--accent)}.look-version-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0;max-height:320px;overflow:auto}.look-version-grid button{flex-direction:column;border:1px solid var(--edge);background:var(--canvas);border-radius:10px;padding:10px;min-width:0;color:var(--quiet);font-size:12px}.look-version-grid img{width:100%;height:110px;object-fit:contain}.look-version-grid small{font-size:10px}.look-adopt{display:flex;justify-content:flex-end;margin:22px 0}.look-batch{margin-top:20px;border-top:1px solid var(--edge);padding-top:12px}.look-batch summary{cursor:pointer;min-height:44px;font-size:13px;display:flex;align-items:center}.look-notice{padding:14px;background:var(--wash);border:1px solid var(--edge);border-radius:10px;font-size:13px;line-height:1.8}.look-notice button{margin-left:8px}.look-spin{animation:look-spin 1.2s linear infinite}@keyframes look-spin{to{transform:rotate(360deg)}}@media(max-width:500px){.look-fields{grid-template-columns:1fr}.look-version-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(prefers-reduced-motion:reduce){.look-spin{animation:none}}
</style>

