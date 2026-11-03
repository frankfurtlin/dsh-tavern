<script setup lang="ts">
import {computed} from 'vue'
import {BookOpen,ArrowUpRight,AlertCircle} from 'lucide-vue-next'
import type {AcceptedBasis} from './accepted-basis'
import type {AcceptedEventContext} from './accepted-context'
import type {NarrativeEvent} from './events'
const props=defineProps<{basis:AcceptedBasis|AcceptedEventContext}>()
const local=computed(()=>props.basis.schema==='accepted-events-basis-v1'?(props.basis as AcceptedBasis):null)
const batches=computed(()=>local.value?local.value.origins.length:(props.basis as AcceptedEventContext).originCount)
const sources=computed(()=>local.value?local.value.sources.length:(props.basis as AcceptedEventContext).sourceCount)
const order=computed(()=>Array.from(new Set(props.basis.events.map(e=>e.ref.runId))))
const names:Record<NarrativeEvent['kind'],string>={character:'人物',item:'物品',location:'地点',time:'时间',motivation:'动机',knowledge:'知情',relationship:'关系',unresolved:'线索',event:'事件'}
const warnings:Record<string,string>={SOURCE_EVENTS_UNAVAILABLE:'部分前文没有事件记录。',SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE:'读点之外的事件及其依赖已排除。',SOURCE_IDENTITY_UNPROVEN:'部分人物缺少确切身份依据，已分别保留。'}
function sourceLink(paragraph:string,offset:number){
 const b=local.value
 if(b){const a=b.scope.anchor;return {path:'/local/read/'+a.storyId,query:{branch:a.branchId||undefined,paragraph,offset:String(offset)}}}
 const basis=props.basis as AcceptedEventContext,a=basis.scope,chapter=basis.paragraphChapters.find(p=>p.paragraphId===paragraph)?.chapterId
 return {path:'/books/'+a.bookId+'/read',query:{branch:a.branchId,revision:a.revisionId,chapter,block:paragraph,edge:'start'}}
}
function person(runId:string,subjectId:string|null){if(subjectId===null)return '';return props.basis.subjects.find(s=>s.runId===runId&&s.subjectId===subjectId)?.name||'未绑定人物'}
</script>
<template><details class="accepted-basis-panel" aria-label="本次续写依据">
 <summary><span><BookOpen :size="15"/>续写依据</span><small>{{sources}} 章 · {{basis.events.length}} 条{{basis.status==='partial'?' · 部分缺失':''}}</small></summary>
 <div class="basis-scope"><span>{{batches}} 批已接受正文</span><RouterLink :to="local?sourceLink(local.scope.anchor.paragraphId,local.scope.anchor.offset):{path:'/books/'+(basis as AcceptedEventContext).scope.bookId+'/read',query:{branch:(basis as AcceptedEventContext).scope.branchId,revision:(basis as AcceptedEventContext).scope.revisionId,chapter:(basis as AcceptedEventContext).scope.chapterId,anchor:JSON.stringify((basis as AcceptedEventContext).scope)}}">写作起点<ArrowUpRight :size="14"/></RouterLink></div>
 <p v-for="warning in basis.warnings" :key="warning" class="basis-warning"><AlertCircle :size="14"/><span>{{warnings[warning]||'部分依据无法核对。'}}</span></p>
 <p v-if="!basis.events.length" class="basis-empty">{{basis.status==='partial'?'前文正文仍用于续写，缺失事件未补造。':'此起点没有已接受的事件记录。'}}</p>
 <ol v-else><li v-for="event in basis.events" :key="JSON.stringify(event.ref)">
  <div class="basis-event-heading"><span>{{names[event.entry.kind]}}<template v-if="event.entry.subjectId"> · {{person(event.ref.runId,event.entry.subjectId)}}</template></span><small>第 {{order.indexOf(event.ref.runId)+1}} 批 · {{event.entry.certainty==='inferred'?'推测':'正文叙述'}}{{event.temporalFrame==='past'?' · 过去':''}}</small></div>
  <p>{{event.entry.statement}}</p><blockquote>{{event.entry.quote}}</blockquote>
  <div class="basis-evidence"><RouterLink v-for="(proof,index) in event.evidence" :key="index" :to="sourceLink(proof.paragraphId,proof.start)">原句{{event.evidence.length>1?' '+(index+1):''}}<ArrowUpRight :size="13"/></RouterLink></div>
 </li></ol>
</details></template>
<style scoped>
.accepted-basis-panel{margin:1rem 0;font-family:var(--font-ui,system-ui);font-size:.88rem;color:var(--muted,#727b73);border:1px solid var(--border,#e8e7de);border-radius:14px;background:var(--surface,#fffefb)}summary{cursor:pointer;min-height:44px;padding:.7rem .9rem;display:flex;align-items:center;justify-content:space-between;gap:.8rem}summary>span,.basis-scope,.basis-scope a,.basis-warning,.basis-evidence a{display:flex;align-items:center;gap:.45rem}summary>small{font-size:.75rem}.basis-scope{justify-content:space-between;padding:.5rem .9rem;border-top:1px solid var(--border,#e8e7de)}a{color:var(--accent,#1a7868);text-decoration:none;min-height:32px}.basis-warning,.basis-empty{margin:.55rem .9rem;line-height:1.7}.basis-warning{align-items:flex-start;color:var(--ink,#29332d)}.basis-warning svg{flex:none;margin-top:.3rem}ol{padding:0 .9rem;list-style:none;max-height:30rem;overflow:auto}li{padding:.8rem 0;border-top:1px solid var(--border,#e8e7de)}.basis-event-heading{display:flex;gap:.5rem;justify-content:space-between;flex-wrap:wrap}p{margin:.5rem 0;color:var(--ink,#29332d)}blockquote{margin:.4rem 0;padding:.2rem .8rem;border-left:2px solid var(--accent,#1a7868);white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.basis-evidence{display:flex;gap:.8rem;flex-wrap:wrap}small{font-size:.75rem}@media(prefers-reduced-motion:no-preference){a{transition:opacity .16s}a:hover{opacity:.7}}
.accepted-basis-panel{min-width:0;overflow-wrap:anywhere}.accepted-basis-panel ol{max-width:100%;box-sizing:border-box;overflow-x:hidden}.basis-event-heading>span{min-width:0;flex:1 1 0}.basis-event-heading small,summary>span{min-width:0}summary svg{flex:none}
</style>
