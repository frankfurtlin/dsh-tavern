<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {useRouter} from 'vue-router'
import {ArrowRight,BookOpen,Check,ChevronDown,Compass,FolderOpen,GitBranch,Home,Image,LoaderCircle,MapPin,Play,RotateCcw,Send,Settings2,Sparkles,UserRound,Users} from 'lucide-vue-next'
import {api,authGeneration} from '../api'
import {contextRoute,rememberContext} from '../api/stories'
import type {StoryContext,StoryDetail} from '../api/stories'
import type {Anchor,Chapter} from '../api/types'
import type {ImageScope} from '../api/assets'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import {worldApi} from '../world/api'
import {worldMutation,pendingWorldMutation,clearWorldMutation} from '../world/mutation-intent'
import {worldTaskFinished} from '../world/task-state'
import {defaultRules} from '../world/types'
import type {WorldCheckpoint,WorldInfo,WorldLine,WorldSource,WorldTask} from '../world/types'
import StoryImages from './StoryImages.vue'
import WorldLookLibrary from './WorldLookLibrary.vue'
import '../styles/work-entry.css'

type EntryMode='read'|'explore'
type Opening={id:string;kind:'original'|'saved'|'new';title:string;subtitle:string;worldId?:string;anchor?:Anchor;context?:StoryContext}
type BeginBody={characterId:string|null;customName:string;customIdentity:string;opening:string;lineName:string}
type Creation={worldId:string;taskId:string|null}
type Draft={mode:EntryMode;selected:string;character:string;customName:string;gender:string;age:string;identity:string;skills:string[];opening:string}
const props=defineProps<{story:StoryDetail;imageScope:ImageScope|null;timeline:string|null}>()
const emit=defineEmits<{read:[];chat:[]}>()
const router=useRouter(),store=useApp(),capture=useRequestScope(()=>props.story.id)
const mode=ref<EntryMode>('read'),selected=ref(''),sources=ref<WorldSource[]>([]),world=ref<WorldInfo|null>(null),line=ref<WorldLine|null>(null)
const lookPreviews=ref<Record<string,string>>({})
const previewCheckpoint=ref<WorldCheckpoint|null>(null)
const loading=ref(true),previewLoading=ref(false),busy=ref(false),error=ref(''),previewError=ref(''),phase=ref(''),preview=ref(''),chapterTitle=ref('')
const aboutText=ref('')
const identityTab=ref<'cast'|'custom'>('cast'),castTouched=ref(false),expandedOpening=ref(false)
const character=ref('custom'),customName=ref(store.user?.username||''),gender=ref(''),age=ref(''),identity=ref(''),skills=ref<string[]>([]),opening=ref('')
const creation=ref<Creation|null>(null),lockedBegin=ref<BeginBody|null>(null),lockedCreate=ref(false)
const submittedCreate=ref<Record<string,unknown>|null>(null)
let selectionEpoch=0,timer:ReturnType<typeof setTimeout>|undefined,releaseWait:(()=>void)|undefined
const createSlot='work-entry:create:'+props.story.id,draftKey='work-entry:draft:'+props.story.id
const skillOptions=['观察','交涉','推理','医术','潜行','武艺']
const choices=computed<Opening[]>(()=>{
  const result:Opening[]=[]
  if(props.story.kind==='book'&&props.story.resume.read.available)result.push({id:'original',kind:'original',title:'阅读原文',subtitle:'接着上次的位置',context:props.story.resume.read.context||undefined})
  if(props.story.kind==='world'&&props.story.resume.read.available&&!props.story.worlds.some(w=>w.status==='active'&&w.resumeContext)){
    const context=props.story.resume.read.context
    result.push({id:'original',kind:'original',title:'阅读已保存的故事',subtitle:'回到保留的阅读位置',context:context||undefined,worldId:context?.worldId})
  }
  for(const saved of props.story.worlds){
    if(saved.status==='active'&&saved.resumeContext)result.push({id:'saved:'+saved.id,kind:'saved',title:saved.name,subtitle:[saved.playerName,saved.locationName].filter(Boolean).join(' · ')||'继续存档',worldId:saved.id,context:saved.resumeContext})
  }
  if(props.story.status==='active'){
    if(props.story.kind==='world')result.push({id:'new:'+props.story.id.slice(6),kind:'new',title:'新的开场',subtitle:'选择身份，重新进入',worldId:props.story.id.slice(6)})
    else for(const source of sources.value)for(const branch of source.lines)for(const chapter of branch.chapters)result.push({id:'source:'+branch.id+':'+chapter.anchor.blockId,kind:'new',title:chapter.title,subtitle:branch.name+' · 从已读处开始',anchor:chapter.anchor})
  }
  const submittedAnchor=submittedCreate.value?.anchor as Anchor|undefined
  if(submittedAnchor&&!result.some(c=>c.id===selected.value))result.push({id:selected.value,kind:'new',title:'已提交的开场',subtitle:'恢复上次的准备',anchor:submittedAnchor})
  return result
})
const current=computed(()=>choices.value.find(item=>item.id===selected.value)),isOriginal=computed(()=>current.value?.kind==='original'),isSaved=computed(()=>current.value?.kind==='saved')
const preparationRoute=computed(()=>props.story.kind==='world'&&['preparing','failed'].includes(props.story.status)?'/worlds/new?world='+props.story.id.slice(6):null)
const canSetup=computed(()=>current.value?.kind==='new'),locked=computed(()=>busy.value||lockedCreate.value||!!lockedBegin.value||!!creation.value)
const characters=computed(()=>world.value?.characters||[])
const savedChoices=computed(()=>choices.value.filter(c=>c.kind==='saved'))
const openingChoices=computed(()=>{const items=choices.value.filter(c=>c.kind==='new');return items.length?items:choices.value.filter(c=>c.kind==='original')})
const shownIdentityTab=computed(()=>characters.value.length?identityTab.value:'custom')
const setupVisible=computed(()=>canSetup.value||mode.value==='explore'&&!isSaved.value)
const player=computed(()=>line.value?.head.state?.entities.find(e=>e.id===line.value?.head.state?.playerId))
const location=computed(()=>previewCheckpoint.value?.state?.entities.find(e=>e.id===previewCheckpoint.value?.state?.location)?.name||'')
const previewTime=computed(()=>previewCheckpoint.value?.state?.timeLabel||'')
const introduction=computed(()=>aboutText.value)
const startLabel=computed(()=>busy.value?phase.value||'正在进入…':lockedCreate.value||lockedBegin.value||creation.value?'继续准备开场':mode.value==='explore'?isSaved.value?'继续探索':'开始探索':isOriginal.value?'开始阅读':'进入故事')
const validAge=computed(()=>age.value===''||/^\d{1,4}$/.test(age.value)&&Number(age.value)>=1)
const validIdentity=computed(()=>character.value==='custom'?!!customName.value.trim()&&validAge.value:characters.value.some(c=>c.id===character.value&&c.available))
const canStart=computed(()=>!loading.value&&!previewLoading.value&&!previewError.value&&!busy.value&&!!current.value&&(!setupVisible.value||!!lockedBegin.value||validIdentity.value)&&(isOriginal.value||!world.value||world.value.status==='active'||world.value.status==='preparing')&&(isOriginal.value&&mode.value==='read'||store.online)&&(isOriginal.value&&mode.value==='read'||props.story.status==='active'))
function draft():Draft{return {mode:mode.value,selected:selected.value,character:character.value,customName:customName.value,gender:gender.value,age:age.value,identity:identity.value,skills:[...skills.value],opening:opening.value}}
function restore(value:Draft){mode.value=value.mode;selected.value=value.selected;character.value=value.character;identityTab.value=value.character==='custom'?'custom':'cast';castTouched.value=true;customName.value=value.customName;gender.value=value.gender;age.value=value.age;identity.value=value.identity;skills.value=value.skills;opening.value=value.opening}
function setMode(value:EntryMode){
  if(busy.value)return
  mode.value=value
  if(value==='explore'&&isOriginal.value){const next=choices.value.find(c=>c.kind==='saved')||choices.value.find(c=>c.kind==='new');if(next)void select(next.id)}
}
async function chooseCharacter(id:string){
  if(locked.value)return
  const scope=capture(),choice=choices.value.find(c=>c.kind==='new'&&c.worldId===current.value?.worldId)||choices.value.find(c=>c.kind==='new')
  if(choice&&current.value?.id!==choice.id)await select(choice.id)
  if(!scope.valid()||locked.value)return
  if(id!=='custom'&&!characters.value.some(c=>c.id===id&&c.available))return
  character.value=id;identityTab.value=id==='custom'?'custom':'cast';castTouched.value=true
}
async function setIdentityTab(value:'cast'|'custom'){
  if(locked.value)return
  identityTab.value=value;castTouched.value=true
  if(value==='custom')await chooseCharacter('custom')
  else{const first=characters.value.find(c=>c.id===world.value?.recommendedCharacterId&&c.available)||characters.value.find(c=>c.available);if(first)await chooseCharacter(first.id)}
}
async function continueSaved(id:string){
  if(locked.value)return
  const scope=capture();await select(id);if(scope.valid()&&current.value?.id===id)await start()
}
function useOpening(id:string){if(locked.value)return;expandedOpening.value=false;void select(id)}
function editOpening(){if(!locked.value&&isSaved.value){const choice=openingChoices.value[0];if(choice)void select(choice.id)}}
async function select(id:string){
  if(locked.value)return
  selected.value=id;preview.value='';chapterTitle.value='';previewError.value='';world.value=null;line.value=null;previewCheckpoint.value=null
  const choice=current.value;if(!choice)return
  const scope=capture(),epoch=++selectionEpoch,valid=()=>scope.valid()&&epoch===selectionEpoch&&selected.value===id
  previewLoading.value=true
  try{
    if(choice.worldId){
      const info=await worldApi<WorldInfo>('/worlds/'+choice.worldId);if(!valid())return;world.value=info
      if(choice.kind==='new'&&!castTouched.value&&info.recommendedCharacterId){const recommended=info.characters?.find(c=>c.id===info.recommendedCharacterId&&c.available);if(recommended){character.value=recommended.id;identityTab.value='cast'}}
      if(mode.value==='explore'&&choice.kind==='new'&&!castTouched.value&&character.value==='custom'){const first=info.characters?.find(c=>c.available);if(first){character.value=first.id;identityTab.value='cast'}}
      if(choice.kind==='new'&&character.value!=='custom'&&!info.characters?.some(c=>c.id===character.value&&c.available))character.value='custom'
      if(choice.kind==='saved'&&choice.context?.worldLineId){const saved=await worldApi<WorldLine>('/worlds/'+choice.worldId+'/lines/'+choice.context.worldLineId);if(!valid())return;line.value=saved;previewCheckpoint.value=saved.head;preview.value=saved.head.text||info.description;chapterTitle.value=saved.head.title}
      else if(choice.kind==='original'&&choice.context?.checkpointId){const checkpoint=await worldApi<WorldCheckpoint>('/worlds/'+choice.worldId+'/checkpoints/'+choice.context.checkpointId);if(!valid())return;previewCheckpoint.value=checkpoint;preview.value=checkpoint.text;chapterTitle.value=checkpoint.title}
      else{preview.value=info.description;chapterTitle.value='故事的起点'}
    }else{
      if(choice.kind==='new')character.value='custom'
      const anchor=choice.anchor||choice.context?.anchor
      if(anchor){const chapter=await api<Chapter>(`/branches/${anchor.branchId}/chapters/${anchor.chapterId}?revisionId=${anchor.revisionId}`,{cache:true});if(!valid())return;chapterTitle.value=chapter.title;const boundary=chapter.blocks.findIndex(b=>b.id===anchor.blockId);preview.value=chapter.blocks.slice(0,Math.max(0,boundary)+1).map((block,i)=>i===boundary?block.text.slice(0,anchor.end)||block.text.slice(0,200):block.text).join('\n\n')}
    }
  }catch(e){if(valid())previewError.value=(e as Error).message}
  finally{if(valid()){previewLoading.value=false;if(!aboutText.value)aboutText.value=world.value?.description||preview.value.split('\n\n').filter(Boolean).slice(0,2).join('\n\n')}}
}
function identityText(){return [identity.value.trim()||'一位进入这个故事的旅人',gender.value&&'性别：'+gender.value,age.value&&'年龄：'+age.value,skills.value.length&&'擅长：'+skills.value.join('、')].filter(Boolean).join('；').slice(0,800)}
function beginBody():BeginBody{return {characterId:character.value==='custom'?null:character.value,customName:customName.value.trim(),customIdentity:identityText(),opening:opening.value.trim(),lineName:character.value==='custom'?customName.value.trim()+'的故事':(characters.value.find(c=>c.id===character.value)?.name||'我')+'的故事'}}
function pause(){return new Promise<void>(resolve=>{releaseWait=resolve;timer=setTimeout(()=>{releaseWait=undefined;resolve()},1200)})}
async function prepared(info:WorldInfo,taskId:string|null,valid:()=>boolean){
  let latest=info
  while(valid()&&latest.status==='preparing'){
    phase.value='正在整理开场…'
    if(!taskId)throw new Error('开场尚未准备好，请到世界列表继续准备。')
    const task=await worldApi<WorldTask>('/tasks/'+taskId);if(!valid())return null
    latest=await worldApi<WorldInfo>('/worlds/'+info.id);if(!valid())return null;world.value=latest
    if(worldTaskFinished(task)&&task.state!=='succeeded')throw new Error(task.errorCode==='PROVIDER_STATUS_UNKNOWN'?'模型响应尚未确认，这次开场已保留。':'开场准备未完成，可在世界列表继续处理。')
    if(latest.status==='preparing')await pause()
  }
  if(!valid())return null
  if(latest.status!=='active')throw new Error('这个世界当前无法开始，请到世界列表查看。')
  return latest
}
async function start(){
  if(!canStart.value)return
  if(mode.value==='explore'&&isOriginal.value){error.value='这个作品暂时没有可探索的开场，角色和开场设置已保留。';return}
  const choice=current.value!,scope=capture(),auth=authGeneration(),valid=scope.valid,targetMode=mode.value
  if(choice.kind==='original'){emit('read');return}
  busy.value=true;error.value='';phase.value='正在进入…'
  try{
    if(choice.kind==='saved'){
      if(!choice.context)throw new Error('存档位置尚未确认，请重新载入。')
      await rememberContext(choice.context,valid);if(!valid())return
      const target=contextRoute(choice.context);if(targetMode==='explore')target.path=target.path.replace(/\/play$/,'/explore')
      await router.push(target);return
    }
    await cacheSet(draftKey,draft());if(!valid())return
    let info=world.value
    if(!choice.worldId){
      phase.value='正在准备世界…'
      const body=submittedCreate.value||{anchor:choice.anchor,name:(props.story.title+' · 新的开场').slice(0,160),rules:defaultRules(),fullBookReference:false,maxCalls:8}
      const result=creation.value||await worldMutation<Creation>(createSlot,'/worlds',body,valid,intent=>{if(valid()){lockedCreate.value=true;submittedCreate.value=intent.body as Record<string,unknown>}})
      if(!valid())return;creation.value=result
      info=await worldApi<WorldInfo>('/worlds/'+result.worldId);if(!valid())return;world.value=info
    }
    if(!info)throw new Error('未能载入世界，请重新选择开场。')
    info=await prepared(info,creation.value?.taskId||info.prepareTaskId,valid);if(!info||!valid())return
    phase.value='正在创建开场…'
    const slot='work-entry:begin:'+info.id
    const pending=await pendingWorldMutation(slot);if(!valid())return
    const payload=lockedBegin.value||(pending?.body as BeginBody|undefined)||beginBody()
    const result=await worldMutation<{lineId:string}>(slot,'/worlds/'+info.id+'/begin',payload,valid,()=>{if(valid())lockedBegin.value=payload})
    if(!valid())return
    await router.push({path:'/worlds/'+info.id+(targetMode==='explore'?'/explore':'/play'),query:{line:result.lineId,story:props.story.id}})
    if(authGeneration()!==auth)return
    const sameAuth=()=>authGeneration()===auth
    await clearWorldMutation(slot,sameAuth)
    if(!sameAuth())return
    if(creation.value)await clearWorldMutation(createSlot,sameAuth,info.id)
    if(!sameAuth())return
    await cacheSet(draftKey,null)
  }catch(e){if(valid())error.value=(e as Error).message}
  finally{if(valid()){busy.value=false;phase.value=''}}
}
onMounted(async()=>{
  const scope=capture()
  try{
    if(props.story.kind==='book'&&props.story.status==='active'){
      try{const response=await worldApi<{items:WorldSource[]}>('/sources?bookId='+props.story.id.slice(5));if(!scope.valid())return;sources.value=response.items}
      catch{/* The mode selector remains available while world data is unavailable. */}
    }
    if(!scope.valid())return
    const pending=await pendingWorldMutation(createSlot);if(!scope.valid())return
    const saved=await cacheGet<Draft>(draftKey);if(!scope.valid())return
    if(saved)restore(saved)
    if(pending)submittedCreate.value=pending.body as Record<string,unknown>
    const initial=choices.value.find(c=>c.id===selected.value)||(mode.value==='explore'?choices.value.find(c=>c.kind==='saved')||choices.value.find(c=>c.kind==='new'):null)||choices.value[0];if(initial)await select(initial.id)
    else if(props.story.kind==='world'){const info=await worldApi<WorldInfo>('/worlds/'+props.story.id.slice(6));if(!scope.valid())return;world.value=info;aboutText.value=info.description}
    if(!scope.valid())return
    if(pending){lockedCreate.value=true;error.value='上次提交的开场已保留，可以继续准备。';if(pending.receipt?.worldId){creation.value=pending.receipt as Creation;const info=await worldApi<WorldInfo>('/worlds/'+pending.receipt.worldId);if(!scope.valid())return;world.value=info;if(info.status==='failed')error.value='上次开场准备未完成，请到世界列表继续处理。'}}
    const id=creation.value?.worldId||current.value?.worldId
    if(id){const begin=await pendingWorldMutation('work-entry:begin:'+id);if(!scope.valid())return;if(begin){if(saved)restore(saved);lockedBegin.value=begin.body as BeginBody;character.value=lockedBegin.value.characterId||'custom';customName.value=lockedBegin.value.customName;opening.value=lockedBegin.value.opening}}
  }catch(e){if(scope.valid())error.value=(e as Error).message}
  finally{if(scope.valid())loading.value=false}
})
onUnmounted(()=>{selectionEpoch++;clearTimeout(timer);releaseWait?.();releaseWait=undefined})
</script>

<template>
  <div class="work-entry">
    <div class="work-entry-layout">
      <div class="work-entry-stage">
        <header class="work-entry-bar"><span>{{story.title}}</span><nav aria-label="作品工具"><RouterLink to="/library" aria-label="返回书架"><Home :size="17"/></RouterLink><RouterLink to="/worlds" aria-label="我的世界与存档"><FolderOpen :size="17"/></RouterLink><RouterLink to="/settings/models" aria-label="模型设置"><Settings2 :size="17"/></RouterLink><RouterLink v-if="timeline" :to="timeline" aria-label="故事线"><GitBranch :size="17"/></RouterLink></nav></header>
        <div class="work-entry-scroll" tabindex="0" aria-label="作品设置">
        <div class="work-mode-switch" role="group" aria-label="体验模式">
          <button :class="{selected:mode==='read'}" :aria-pressed="mode==='read'" :disabled="busy" @click="setMode('read')"><BookOpen :size="22"/><span><strong>阅读模式</strong><small>沉浸文字，走进故事</small></span><Check v-if="mode==='read'" :size="17"/></button>
          <button :class="{selected:mode==='explore'}" :aria-pressed="mode==='explore'" :disabled="busy" @click="setMode('explore')"><Compass :size="22"/><span><strong>探索模式 <em>360°</em></strong><small>环顾场景，自由探索</small></span><Check v-if="mode==='explore'" :size="17"/></button>
        </div>
        <div v-if="loading" class="loading" role="status">正在载入开场…</div>
        <div v-else-if="mode==='explore'" class="work-explore" aria-label="探索开局">
          <section class="work-explore-hero">
            <div class="work-explore-emblem"><Compass :size="38"/></div>
            <div class="work-explore-summary"><span class="work-eyebrow"><Compass :size="14"/>作品探索模式</span><h2>{{story.title}}</h2><p>{{introduction||'选择一个身份，走进故事里的世界。'}}</p><div class="work-explore-steps"><span><b>1</b>选择角色</span><span><b>2</b>设置旅程</span><span><b>3</b>开始探索</span></div></div>
          </section>
          <section v-if="savedChoices.length" class="work-explore-resume" aria-label="继续探索存档">
            <span class="work-section-icon"><FolderOpen :size="22"/></span><div><small>已有存档</small><strong>{{savedChoices[0]!.title}}</strong><p>{{savedChoices[0]!.subtitle}}</p></div><button class="primary" :disabled="locked||previewLoading" @click="continueSaved(savedChoices[0]!.id)"><Play :size="16"/>继续</button>
          </section>
          <section class="work-explore-card" aria-label="角色选择">
            <div class="work-explore-heading"><span class="work-section-icon"><UserRound :size="20"/></span><div><small>选择角色</small><h2>你想成为谁？</h2></div></div>
            <WorldLookLibrary v-if="world" :key="world.id" :world-id="world.id" :disabled="locked" @previews="lookPreviews=$event"/>
            <div class="work-identity-tabs" role="group" aria-label="角色类型"><button :class="{selected:shownIdentityTab==='cast'}" :aria-pressed="shownIdentityTab==='cast'" :disabled="locked||!characters.length" @click="setIdentityTab('cast')"><Users :size="16"/>作品角色</button><button :class="{selected:shownIdentityTab==='custom'}" :aria-pressed="shownIdentityTab==='custom'" :disabled="locked" @click="setIdentityTab('custom')"><UserRound :size="16"/>自定义角色</button></div>
            <div v-if="shownIdentityTab==='cast'" class="work-cast-grid" role="group" aria-label="可选角色"><button v-for="c in characters" :key="c.id" :class="{selected:character===c.id&&!isSaved}" :aria-pressed="character===c.id&&!isSaved" :disabled="locked||!c.available" @click="chooseCharacter(c.id)"><span class="work-cast-avatar"><img v-if="lookPreviews[c.id]" :src="lookPreviews[c.id]" alt="" style="width:100%;height:100%;object-fit:cover"><UserRound v-else :size="21"/></span><span><strong>{{c.name}}</strong><small>{{c.description||c.traits||'故事中的角色'}}</small></span><Check v-if="character===c.id&&!isSaved" :size="15"/></button></div>
            <form v-else class="work-start-form work-explore-form" @submit.prevent="start"><fieldset :disabled="locked"><label>角色姓名 <span class="work-required">*</span><input v-model="customName" required maxlength="80" autocomplete="off" placeholder="你在故事中的名字"></label><div class="work-form-pair"><label>性别<select v-model="gender"><option value="">不设定</option><option>女</option><option>男</option><option>其他</option></select></label><label>年龄<input v-model="age" type="number" min="1" max="9999" placeholder="选填"></label></div><label class="work-form-wide">角色背景<textarea v-model="identity" maxlength="650" rows="2" placeholder="你是谁，又为何来到这里？"></textarea></label><div class="work-skill-field work-form-wide"><span>擅长的能力</span><div class="work-skill-options"><label v-for="skill in skillOptions" :key="skill" :class="{selected:skills.includes(skill)}"><input v-model="skills" type="checkbox" :value="skill">{{skill}}</label></div></div></fieldset></form>
          </section>
          <section class="work-explore-card" aria-label="旅程设置">
            <div class="work-explore-heading"><span class="work-section-icon"><Compass :size="20"/></span><div><small>设置旅程</small><h2>旅程设置</h2></div></div>
            <h3 class="work-travel-label"><BookOpen :size="17"/>选择开场</h3>
            <div class="work-travel-options" role="group" aria-label="探索开场列表"><button v-for="(choice,index) in openingChoices" :key="choice.id" :class="{selected:current?.id===choice.id}" :aria-pressed="current?.id===choice.id" :disabled="locked" @click="useOpening(choice.id)"><span class="work-travel-radio"><Check v-if="current?.id===choice.id" :size="12"/></span><span><strong>{{index+1}}. {{choice.title}}</strong><small>{{choice.subtitle}}</small></span><span v-if="current?.id===choice.id" class="work-selected-note">已选择</span></button></div>
            <article v-if="current" class="work-travel-preview" :aria-busy="previewLoading"><div class="work-travel-preview-heading"><span>{{isSaved?'当前存档':chapterTitle||current.title}}</span><span v-if="location"><MapPin :size="13"/>{{location}}</span></div><p v-if="previewLoading" class="muted" role="status">正在载入…</p><p v-else-if="previewError" class="error" role="alert">{{previewError}}<button :disabled="locked" @click="select(selected)">重新载入</button></p><template v-else><p class="work-travel-text" :class="{expanded:expandedOpening}">{{preview||introduction}}</p><button v-if="preview" class="work-expand-opening" :aria-expanded="expandedOpening" @click="expandedOpening=!expandedOpening"><ChevronDown :size="14" :class="{rotated:expandedOpening}"/>{{expandedOpening?'收起':'展开全文'}}</button></template></article>
            <RouterLink v-if="preparationRoute" :to="preparationRoute" class="work-about-link">继续准备世界<ArrowRight :size="15"/></RouterLink>
          </section>
          <section class="work-explore-card" aria-label="探索记录"><div class="work-explore-heading"><span class="work-section-icon"><FolderOpen :size="20"/></span><div><small>已有存档</small><h2>探索记录</h2></div><span class="work-record-count">{{savedChoices.length}}</span></div><div v-if="savedChoices.length" class="work-record-grid"><article v-for="save in savedChoices" :key="save.id" class="work-record"><span class="work-cast-avatar"><BookOpen :size="20"/></span><div><strong>{{save.title}}</strong><p>{{save.subtitle}}</p><button :disabled="locked||previewLoading" @click="continueSaved(save.id)"><Play :size="14"/>继续探索</button></div></article></div><p v-else class="work-record-empty">还没有探索记录</p></section>
        </div>
        <div v-else class="work-entry-columns">
          <section class="work-opening-panel" aria-label="选择开场">
            <div class="work-section-heading"><span>01</span><h2>选择开场</h2></div>
            <div class="work-opening-options" role="group" aria-label="开场列表"><button v-for="choice in choices" :key="choice.id" :aria-pressed="selected===choice.id" :class="{selected:selected===choice.id}" :disabled="locked" @click="select(choice.id)"><BookOpen v-if="choice.kind==='original'" :size="17"/><RotateCcw v-else-if="choice.kind==='saved'" :size="17"/><Sparkles v-else :size="17"/><span><strong>{{choice.title}}</strong><small>{{choice.subtitle}}</small></span><Check v-if="selected===choice.id" :size="16"/></button></div>
            <article class="work-opening-preview" :aria-busy="previewLoading">
              <div class="work-preview-caption"><span>{{isSaved?'当前存档':isOriginal?'原文选读':'故事起点'}}</span><MapPin v-if="location" :size="14"/></div>
              <h3>{{chapterTitle||current?.title||'等待一个开场'}}</h3>
              <div v-if="location||previewTime" class="work-scene-meta"><span v-if="location"><MapPin :size="13"/>{{location}}</span><span>{{previewTime}}</span></div>
              <p v-if="previewLoading" class="muted" role="status">正在载入…</p>
              <p v-else-if="previewError" class="error" role="alert">{{previewError}}<button :disabled="locked" @click="select(selected)">重新载入</button></p>
              <div v-else class="work-excerpt">{{preview.slice(0,2400)||'从一个新的身份开始，让故事在你的选择中展开。'}}</div>
            </article>
          </section>
          <section class="work-setup-panel" aria-label="开局设置">
            <div class="work-section-heading"><span>02</span><h2>开局设置</h2><UserRound :size="18"/></div>
            <template v-if="isSaved"><div class="work-saved-identity"><span class="work-person-mark">{{(player?.name||world?.playerName||'你').slice(0,1)}}</span><div><strong>{{player?.name||world?.playerName||'当前角色'}}</strong><small>{{location||current?.subtitle}}</small></div><span class="work-status-dot"></span></div><dl class="work-save-details"><div><dt>故事进度</dt><dd>{{chapterTitle||'当前存档'}}</dd></div><div><dt>时间</dt><dd>{{previewTime||'故事当前时点'}}</dd></div><div><dt>体验</dt><dd>纯文字故事</dd></div></dl><p v-if="player?.description" class="work-identity-copy">{{player.description}}</p><button v-if="choices.some(c=>c.kind==='new')" class="work-new-start" :disabled="locked" @click="select(choices.find(c=>c.kind==='new')!.id)"><Sparkles :size="16"/>换一个身份开场<ArrowRight :size="16"/></button></template>
            <template v-else-if="isOriginal"><div class="work-reader-mark"><BookOpen :size="32"/></div><h3>接着读下去</h3><p class="work-identity-copy">{{chapterTitle||story.title}}</p><div class="work-reader-settings"><BookOpen :size="17"/><span>原文阅读</span><Check :size="16"/></div><div v-if="choices.some(c=>c.kind==='new')" class="work-other-start"><span>想成为故事中的人？</span><button @click="select(choices.find(c=>c.kind==='new')!.id)">选择一个新开场<ArrowRight :size="15"/></button></div><RouterLink v-else-if="store.worldEnabled" class="inline-link" :to="'/worlds/new?book='+story.id.slice(5)">从已读处创建世界</RouterLink></template>
            <form v-else-if="canSetup" class="work-start-form" @submit.prevent="start">
              <fieldset :disabled="locked"><label v-if="characters.length">你的身份<select v-model="character" aria-label="你的身份"><option value="custom">自定义角色</option><option v-for="c in characters" :key="c.id" :value="c.id" :disabled="!c.available">{{c.name}}{{c.available?'':' · 当前不可扮演'}}</option></select></label>
                <template v-if="character==='custom'"><div class="work-character-intro"><span class="work-person-mark">{{customName.trim().slice(0,1)||'你'}}</span><span>以你的身份进入故事</span></div><label>角色姓名 <span class="work-required">*</span><input v-model="customName" required maxlength="80" autocomplete="off" placeholder="你在故事中的名字"></label><div class="work-form-pair"><label>性别<select v-model="gender"><option value="">不设定</option><option>女</option><option>男</option><option>其他</option></select></label><label>年龄<input v-model="age" type="number" min="1" max="9999" placeholder="选填"></label></div><label>角色背景<textarea v-model="identity" maxlength="650" rows="3" placeholder="你是谁，又为何来到这里？"></textarea></label><div class="work-skill-field"><span>擅长的能力</span><div class="work-skill-options"><label v-for="skill in skillOptions" :key="skill" :class="{selected:skills.includes(skill)}"><input v-model="skills" type="checkbox" :value="skill">{{skill}}</label></div></div></template>
                <div v-else class="work-character-profile"><span class="work-person-mark">{{characters.find(c=>c.id===character)?.name.slice(0,1)}}</span><h3>{{characters.find(c=>c.id===character)?.name}}</h3><p>{{characters.find(c=>c.id===character)?.description}}</p><small>{{characters.find(c=>c.id===character)?.traits}}</small></div>
                <label>如何开场？<textarea v-model="opening" maxlength="1200" rows="4" placeholder="可以自由描述，也可以让故事自然开始。"></textarea></label>
              </fieldset>
            </form>
            <div v-else-if="preparationRoute"><p class="work-identity-copy">这个世界还未完成开场准备。</p><RouterLink :to="preparationRoute" class="inline-link">继续准备世界<ArrowRight :size="16"/></RouterLink></div>
            <p v-else class="muted">{{story.status==='archived'?'恢复这个世界后，就能开始新的故事。':'先选择一个开场。'}}</p>
          </section>
        </div>
        </div>
        <footer class="work-start-bar" :class="{'work-start-bar-explore':mode==='explore'}">
          <label v-if="mode==='explore'" class="work-opening-composer"><span class="sr-only">探索开场</span><textarea v-model="opening" :disabled="locked" maxlength="1200" rows="2" placeholder="写下你的开场，或直接开始探索…" @focus="editOpening"></textarea></label>
          <div v-if="error" class="work-entry-error" role="alert">{{error}}<RouterLink v-if="lockedCreate||creation||lockedBegin" to="/worlds">查看我的世界<ArrowRight :size="15"/></RouterLink></div>
          <div class="work-start-actions">
            <div v-if="mode==='explore'" class="work-model-links"><RouterLink to="/settings/models"><Sparkles :size="14"/>文字模型<ChevronDown :size="12"/></RouterLink><RouterLink to="/settings/models"><Image :size="14"/>图像模型<ChevronDown :size="12"/></RouterLink></div>
            <div v-else class="work-start-caption"><span class="work-mode-label"><BookOpen :size="16"/>文字阅读</span><small>{{current?.title}}</small></div>
            <button class="primary" :disabled="!canStart" @click="start"><LoaderCircle v-if="busy" class="work-spin" :size="19"/><Compass v-else-if="mode==='explore'" :size="18"/><Send v-else :size="18"/>{{startLabel}}</button>
          </div>
        </footer>
      </div>
      <aside class="work-about" aria-label="作品介绍">
        <StoryImages class="work-about-cover" :scope="imageScope" purpose="cover" :editable="story.status==='active'" :alt="story.title+'封面'"><template #fallback><img v-if="story.coverUrl" :src="story.coverUrl" :alt="story.title+'封面'"><div v-else class="work-cover-art"><div class="work-cover-orbit"></div><Compass :size="44"/><span>{{story.title}}</span><small>STORIES BETWEEN</small></div></template></StoryImages>
        <div class="work-about-copy"><span class="work-about-eyebrow">{{story.kind==='book'?'小说':'互动世界'}}</span><h1>{{story.title}}</h1><p v-if="story.source" class="work-source">{{story.source.sourceRemoved?'来源已移除':'来自'}} · {{story.source.bookTitle}}</p><div class="work-about-tags"><span><BookOpen :size="13"/>阅读</span><span><Compass :size="13"/>360° 探索</span></div><div class="work-about-divider"></div><h2>作品介绍</h2><p class="work-introduction">{{introduction||'故事将在这里展开。'}}</p><template v-if="mode==='explore'&&characters.length"><div class="work-about-divider"></div><h2>作品角色 <span class="muted">({{characters.length}})</span></h2><div class="work-about-cast"><div v-for="c in characters" :key="c.id"><span class="work-person-mark">{{c.name.slice(0,1)}}</span><div><strong>{{c.name}}</strong><p>{{c.description||c.traits}}</p></div></div></div></template><RouterLink v-if="timeline" :to="timeline" class="work-about-link"><GitBranch :size="16"/>故事线<ArrowRight :size="15"/></RouterLink><button v-if="story.resume.chat.available" class="work-about-link" @click="emit('chat')"><UserRound :size="16"/>继续角色对话<ArrowRight :size="15"/></button></div>
      </aside>
    </div>
  </div>
</template>
