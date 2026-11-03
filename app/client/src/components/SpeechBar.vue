<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {Play,Pause,SkipBack,SkipForward,MoreHorizontal,X} from 'lucide-vue-next'
import Sheet from './Sheet.vue'
import {speechReason,type SpeechController} from '../platform/speech-controller'
import type {SpeechSegment,SpeechPreferences} from '../platform/speech-types'
import '../styles/system-speech.css'
const props=defineProps<{controller:SpeechController}>(),emit=defineEmits<{locate:[SpeechSegment];close:[];resize:[]}>(),more=ref(false),element=ref<HTMLElement>()
let observer:ResizeObserver|undefined,lastSize=''
onMounted(()=>{observer=new ResizeObserver(entries=>{const box=entries[0]?.contentRect;if(!box)return;const size=box.width+':'+box.height;if(size!==lastSize){lastSize=size;emit('resize')}});if(element.value)observer.observe(element.value)})
onUnmounted(()=>observer?.disconnect())
const c=computed(()=>props.controller),current=computed(()=>c.value.state.value),position=computed(()=>Math.min((current.value.index||0)+1,current.value.count||0)),sleep=computed(()=>c.value.preferences.value.sleepMinutes)
function configure(changes:Partial<SpeechPreferences>){void c.value.configure({...c.value.preferences.value,...changes})}
</script>
<template>
  <section ref="element" class="speech-bar" aria-label="系统听书" :data-speech-status="current.status">
    <div class="speech-title"><strong>{{current.chapterTitle||c.lastCompleted.value?.chapterTitle||'系统听书'}}</strong><small>{{c.loading.value?'正在打开系统声音…':current.status==='starting'?'正在启动系统声音…':current.status==='ended'?'本页已听完':`${position} / ${current.count||0} 段`}}</small></div>
    <button class="icon-button" aria-label="听书上一段" :disabled="c.busy.value||!current.queueLoaded||current.index===0" @click="c.skip(-1)"><SkipBack :size="18"/></button>
    <button class="icon-button primary" :aria-label="c.playing.value?'暂停听书':'播放系统听书'" :disabled="c.busy.value||c.loading.value||current.status==='ended'" @click="c.playing.value?c.pause():c.play()"><Pause v-if="c.playing.value" :size="20"/><Play v-else :size="20"/></button>
    <button class="icon-button" aria-label="听书下一段" :disabled="c.busy.value||!current.queueLoaded||(current.index??0)>=((current.count||0)-1)" @click="c.skip(1)"><SkipForward :size="18"/></button>
    <button class="icon-button" aria-label="听书更多设置" @click="more=true"><MoreHorizontal :size="20"/></button>
    <p v-if="c.error.value||speechReason(current.reason)" class="speech-message" role="status">{{c.error.value||speechReason(current.reason)}}</p>
  </section>
  <Sheet v-if="more" title="系统听书" @close="more=false"><div class="speech-settings">
    <p class="muted">{{c.cloud?.supported.value?'可按账号保存偏好与已听位置。':'偏好与已听位置保存在这台设备。'}}不使用收费语音。</p>
    <div v-if="c.cloud?.supported.value" class="speech-cloud-controls">
      <p v-if="c.cloud.preferenceMessage.value" role="status">{{c.cloud.preferenceMessage.value}}</p>
      <div class="actions"><button v-if="['conflict','unknown'].includes(c.cloud.preferenceStatus.value)" :disabled="c.cloud.preferenceBusy.value" @click="c.cloud.refreshPreferences()">载入最新信息</button><button v-if="c.cloud.preferenceStatus.value==='local'" :disabled="c.cloud.preferenceBusy.value" @click="c.cloud.savePreferences()">保存本机偏好</button></div>
      <p v-if="c.cloud.message.value" role="status">{{c.cloud.message.value}}</p>
      <div class="actions"><button v-if="c.cloud.pending.value" :disabled="c.cloud.busy.value" @click="c.cloud.recover()">确认原听书请求</button><button v-else-if="['conflict','error'].includes(c.cloud.status.value)" :disabled="c.cloud.busy.value" @click="c.cloud.refreshPosition()">载入账号听书位置</button><button v-if="c.cloud.completion.value&&!c.cloud.pending.value&&!['conflict','unknown'].includes(c.cloud.status.value)" :disabled="c.cloud.busy.value" @click="c.cloud.saveLatest()">保存本机已听位置</button></div>
    </div>
    <p v-if="current.voiceFallback" class="hint">偏好声音在此设备不可用，当前使用 {{c.voices.value.find(v=>v.id===current.resolvedVoiceId)?.name||'可用系统声音'}}。</p>
    <label>系统声音<select :value="c.preferences.value.voiceId" :disabled="c.busy.value" @change="configure({voiceId:($event.target as HTMLSelectElement).value})"><option value="">可用中文系统声音</option><option v-for="voice in c.voices.value" :key="voice.id" :value="voice.id">{{voice.name}} · {{voice.language}}{{voice.offline?' · 离线':''}}</option><option v-if="c.preferences.value.voiceId&&!c.voices.value.some(v=>v.id===c.preferences.value.voiceId)" :value="c.preferences.value.voiceId">此设备缺少偏好声音</option></select></label>
    <label>语速<select :value="c.preferences.value.rate" :disabled="c.busy.value" @change="configure({rate:Number(($event.target as HTMLSelectElement).value)})"><option v-for="rate in [.75,1,1.25,1.5,1.75,2]" :key="rate" :value="rate">{{rate}} 倍</option></select></label>
    <label>定时停止<select :value="sleep" :disabled="c.busy.value" @change="configure({sleepMinutes:Number(($event.target as HTMLSelectElement).value) as SpeechPreferences['sleepMinutes']})"><option :value="0">不定时</option><option v-for="minutes in [5,15,30,60]" :key="minutes" :value="minutes">{{minutes}} 分钟</option></select></label>
    <label class="speech-check"><input type="checkbox" :checked="c.preferences.value.stopAtChapterEnd" :disabled="c.busy.value" @change="configure({stopAtChapterEnd:($event.target as HTMLInputElement).checked})">本章结束后停止</label>
    <button :disabled="!c.lastCompleted.value" @click="emit('locate',c.lastCompleted.value!);more=false">回到已听段落</button><button :disabled="c.busy.value" @click="c.retryVoices()">重新读取系统声音</button><button @click="c.openSettings()">打开系统语音设置</button><p v-if="c.instructions.value" class="hint">{{c.instructions.value}}</p><button :disabled="c.busy.value" @click="c.stop()">停止听书，保留已听位置</button><button @click="emit('close');more=false"><X :size="17"/>关闭播放条</button>
  </div></Sheet>
</template>
