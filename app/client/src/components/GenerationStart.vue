<script setup lang="ts">
import {ref,computed,onMounted} from 'vue'
import {Sparkles,ArrowRight} from 'lucide-vue-next'
import {allPages,api} from '../api'
import type {Anchor,Branch,Profile} from '../api/types'
import type {GenerationController} from '../api/generation-controller'
import {proseProtocol,type ChapterLength} from '../api/generation-types'
import {writingChapterCount,writingOperation} from '../api/story-writing'
import {defaultProfileId} from '../api/profiles'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const props=defineProps<{anchor:Anchor;branch:Branch;operation:string;controller:GenerationController}>(),emit=defineEmits<{started:[];legacy:[]}>()
const store=useApp(),capture=useRequestScope(()=>[props.anchor,props.branch.id]),loading=ref(true),profileId=ref(''),localError=ref('')
const pending=props.controller.intent.value,choice=ref<number|'custom'>(pending?.request.chapterCount||1),customCount=ref<number|string>(pending?.request.chapterCount||2),instruction=ref(pending?.request.instruction||'')
if(![1,3,5].includes(Number(choice.value)))choice.value='custom'
const length=ref<ChapterLength>(pending?.request.chapterLength||store.settings.chapterLength||'standard')
const count=computed(()=>writingChapterCount(choice.value==='custom'?customCount.value:choice.value)),locked=computed(()=>props.controller.busy.value||!!props.controller.intent.value)
const records=ref<{id:string;createdAt:string;state:string}[]>([]),historyId=ref('')
onMounted(async()=>{const scope=capture();try{const profiles=await allPages<Profile>('/model-profiles');if(scope.valid())profileId.value=defaultProfileId(profiles)}catch{if(scope.valid())localError.value='暂时无法打开模型设置，请稍后再试'}finally{if(scope.valid())loading.value=false}})
async function start(){
  if(loading.value||!count.value||!profileId.value||!store.online)return
  const scope=capture(),op=writingOperation(props.anchor,props.branch,props.operation,profileId.value,instruction.value,count.value)
  const started=await props.controller.submit({protocol:proseProtocol,originAnchor:{...props.anchor},operation:op.operation,chapterCount:count.value,chapterLength:length.value,instruction:op.instruction||'',profileId:profileId.value,expectedHeadRevisionId:props.anchor.revisionId,target:op.target!})
  if(started&&scope.valid())emit('started')
}
async function history(){const scope=capture();try{const result=await api<{items:{id:string;createdAt:string;state:string}[]}>('/generations?bookId='+encodeURIComponent(props.anchor.bookId)+'&branchId='+encodeURIComponent(props.branch.id));if(scope.valid())records.value=result.items.map(r=>({id:r.id,createdAt:r.createdAt,state:r.state}))}catch{if(scope.valid())localError.value='暂时无法打开写作记录，原请求仍保留'}}
async function selectHistory(){const scope=capture();if(!historyId.value)return;await props.controller.open(historyId.value);if(scope.valid()&&props.controller.state.value?.id===historyId.value)emit('started')}
</script>
<template><section class="story-writing" aria-label="开始小说写作"><p class="muted">{{branch.name}} · 从选中段落之后开始</p><p v-if="loading" role="status">正在打开写作设置…</p><p v-else-if="!profileId" class="hint">先添加一个模型，就能继续写故事。<RouterLink to="/settings/models" class="inline-link">打开模型设置<ArrowRight :size="16"/></RouterLink></p><template v-else>
  <fieldset class="chapter-count" :disabled="locked"><legend>接下来，写几章？</legend><div class="chapter-count-options" role="group" aria-label="选择章节数量"><button v-for="number in [1,3,5]" :key="number" type="button" :aria-pressed="choice===number" :class="{selected:choice===number}" @click="choice=number">{{number}} 章</button><button type="button" :aria-pressed="choice==='custom'" :class="{selected:choice==='custom'}" @click="choice='custom'">自定义</button></div><label v-if="choice==='custom'">章节数量<input v-model="customCount" type="number" min="1" max="10" step="1" inputmode="numeric"></label></fieldset>
  <label>每章篇幅<select v-model="length" :disabled="locked"><option value="short">短篇幅</option><option value="standard">标准篇幅</option><option value="long">长篇幅</option></select></label><label class="writing-wish">希望接下来怎样写？<span class="optional">选填</span><textarea v-model="instruction" :disabled="locked" maxlength="2000" rows="3" placeholder="留空就顺着故事继续写"></textarea></label>
  <button class="primary writing-start" :disabled="props.controller.busy.value||!store.online||!count" @click="start"><Sparkles :size="18"/>{{props.controller.intent.value?.generationId?'查看已提交的写作':props.controller.intent.value?'确认提交同一次请求':'开始写作'}}<span>{{count}} 章</span></button>
  </template><button v-if="props.controller.intent.value&&!props.controller.intent.value.generationId" :disabled="props.controller.busy.value||!store.online" @click="props.controller.refresh()">查询这次请求</button><button v-if="(props.controller.state.value||props.controller.intent.value)&&!props.controller.active.value" :disabled="props.controller.busy.value" @click="props.controller.newDraft()">{{props.controller.intent.value&&!props.controller.intent.value.generationId?'保留未确认记录，另写一次':'开始新的写作'}}</button><details class="writing-history" @toggle="($event.target as HTMLDetailsElement).open&&history()"><summary>写作记录</summary><button v-for="record in props.controller.unconfirmed.value" :key="record.key" :disabled="props.controller.busy.value||props.controller.active.value" @click="props.controller.restoreUnconfirmed(record.key)">未确认请求 · {{new Date(record.createdAt).toLocaleString()}}</button><select v-if="records.length" v-model="historyId" aria-label="选择写作记录" :disabled="props.controller.busy.value||props.controller.active.value" @change="selectHistory"><option value="">选择记录</option><option v-for="record in records" :key="record.id" :value="record.id">{{new Date(record.createdAt).toLocaleString()}} · {{record.state==='succeeded'?'已保存':record.state==='cancelled'?'已停止':'写作记录'}}</option></select><button @click="emit('legacy')">查看旧写作记录</button></details><p v-if="props.controller.error.value||localError" class="error" role="alert">{{props.controller.error.value||localError}}</p></section></template>
