<script setup lang="ts">
import type {DialogueSegment} from './scene-dialogue'
import PublicStreamText from '../components/PublicStreamText.vue'
import CharacterAvatar from '../components/CharacterAvatar.vue'
defineProps<{segments:DialogueSegment[];user?:string;playerId?:string;draft?:boolean;stream?:{contentId:string;version:number}}>()
defineEmits<{rendered:[]}>()
</script>
<template>
  <div v-if="user" class="scene-chat-row scene-chat-user"><div class="speaker-label"><CharacterAvatar :entity-id="playerId" name="你" :size="30"/><small>你</small></div><p>{{user}}</p></div>
  <div v-for="(part,index) in segments" :key="index" :class="part.kind==='speech'?'scene-chat-row scene-chat-partner':'scene-chat-narration'" :aria-label="part.kind==='narration'?'旁白':undefined"><div v-if="part.kind==='speech'&&part.speakerId" class="speaker-label" :data-speaker-id="part.speakerId"><CharacterAvatar :entity-id="part.speakerId" :name="part.speakerName||'人物'" :size="30"/><small>{{part.speakerName}}</small></div><PublicStreamText v-if="stream" :text="part.text" :content-id="stream.contentId+'-'+index" :stream-version="stream.version" @rendered="$emit('rendered')"/><p v-else>{{part.text}}</p></div>
  <small v-if="draft" class="scene-reply-receipt">已保留回复 · 世界状态未推进</small>
</template>
<style scoped>.speaker-label{display:flex;gap:8px;align-items:center;margin-bottom:6px}.speaker-label small{margin:0}.scene-chat-narration{white-space:pre-wrap}</style>
