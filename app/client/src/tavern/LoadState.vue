<script setup lang="ts">
// 共享的加载 / 失败 / 空态展示：各页统一文案与「重试」行为，避免重复实现。
defineProps<{
  loading?:boolean
  error?:string
  empty?:boolean
  loadingText?:string
  emptyTitle?:string
  emptyText?:string
}>()

defineEmits<{retry:[]}>()
</script>

<template>
 <p v-if="loading" class="load-state-loading" role="status">
  <span class="load-state-dot" aria-hidden="true"></span>{{loadingText||'正在加载…'}}
 </p>
 <div v-else-if="error" class="load-state-error" role="alert">
  <p class="load-state-title">加载失败</p>
  <p class="load-state-text">{{error}}</p>
  <button type="button" class="load-state-action" @click="$emit('retry')">重试</button>
 </div>
 <div v-else-if="empty" class="load-state-empty">
  <p class="load-state-title">{{emptyTitle||'暂无内容'}}</p>
  <p v-if="emptyText" class="load-state-text">{{emptyText}}</p>
  <div v-if="$slots.action" class="load-state-actions"><slot name="action"/></div>
 </div>
</template>

<style scoped>
.load-state-loading{display:flex;align-items:center;justify-content:center;gap:8px;padding:48px 16px;color:var(--quiet);font-size:14px;text-align:center;margin:0}
.load-state-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);animation:load-state-pulse 1s ease-in-out infinite}
@keyframes load-state-pulse{0%,100%{opacity:.25}50%{opacity:1}}
.load-state-error,.load-state-empty{display:flex;flex-direction:column;align-items:center;gap:8px;padding:52px 16px;text-align:center;border:1px dashed var(--edge);border-radius:16px;margin:0 0 8px}
.load-state-error{border-color:rgba(180,35,24,.35);background:rgba(180,35,24,.04)}
.load-state-title{margin:0;font-size:17px;color:var(--ink);font-family:var(--font-serif)}
.load-state-text{margin:0;max-width:440px;color:var(--quiet);font-size:14px;line-height:1.7;overflow-wrap:anywhere}
.load-state-action{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 22px;margin-top:6px;border:1px solid var(--accent);border-radius:10px;background:var(--accent);color:#fff;font-size:14px;cursor:pointer}
.load-state-action:hover{filter:brightness(1.05)}
.load-state-actions{display:flex;flex-wrap:wrap;justify-content:center;gap:10px;margin-top:6px}
.load-state-actions :deep(button),.load-state-actions :deep(.tavern-import),.load-state-actions :deep(.tavern-empty-import){min-height:44px}
@media(max-width:767px){
 .load-state-error,.load-state-empty{padding:40px 14px}
 .load-state-action{width:100%;max-width:280px}
}
</style>
