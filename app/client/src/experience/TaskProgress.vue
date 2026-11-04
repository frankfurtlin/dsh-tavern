<script setup lang="ts">
defineProps<{progress?:{label:string;queued:boolean;position:number|null;capacity:number|null;elapsedSeconds:number|null}}>()
</script>
<template><p v-if="progress" class="task-progress" role="status"><strong>{{progress.label}}</strong><span v-if="progress.queued&&progress.position"> · 当前排队第 {{progress.position}} 位</span><span v-if="progress.elapsedSeconds!=null"> · {{progress.elapsedSeconds}} 秒</span><span v-if="progress.queued">。可以离开后回来查看，重复刷新不会重新生成。</span></p></template>
<style scoped>.task-progress{padding:12px 16px;border:1px solid var(--border,#e8e1d8);border-radius:12px;line-height:1.8}.task-progress a{display:inline-block;margin-left:12px;color:var(--accent,#b56229)}</style>
