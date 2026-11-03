<script setup lang="ts">
import type {EventLedger,NarrativeEvent} from './events'
defineProps<{ledger:EventLedger}>()
const names:Record<NarrativeEvent['kind'],string>={character:'人物',item:'物品',location:'地点',time:'时间',motivation:'动机',knowledge:'知情',relationship:'关系',unresolved:'线索',event:'事件'}
</script>
<template><details v-if="ledger.events.length" class="chapter-events"><summary>事件与线索 · {{ledger.events.length}}</summary><ol><li v-for="entry in ledger.events" :key="entry.id"><div><span>{{names[entry.kind]}}</span><small>{{entry.certainty==='inferred'?'推测':'正文已叙述'}}</small></div><p>{{entry.statement}}</p><blockquote>{{entry.quote}}</blockquote></li></ol></details></template>
<style scoped>
.chapter-events{margin:1rem 0;font-family:var(--font-ui,system-ui);font-size:.88rem;color:var(--muted,#727b73)}summary{cursor:pointer;min-height:36px;display:list-item}ol{padding:0;list-style:none}li{padding:.75rem 0;border-bottom:1px solid var(--border,#e8e7de)}li>div{display:flex;gap:1rem;align-items:center}p{margin:.5rem 0;color:var(--ink,#29332d)}blockquote{margin:.4rem 0;padding:.2rem .8rem;border-left:2px solid var(--accent,#1a7868);white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}small{font-size:.75rem}
</style>
