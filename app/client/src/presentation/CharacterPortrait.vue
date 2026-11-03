<script setup lang="ts">
import {computed,ref,watch} from 'vue'
const props=defineProps<{name:string;src?:string|null}>(),broken=ref(false)
const initial=computed(()=>Array.from(props.name.trim())[0]||'·')
watch(()=>props.src,()=>broken.value=false)
</script>
<template><div class="character-portrait"><img v-if="src&&!broken" :src="src" :alt="name+'的形象'" width="480" height="640" loading="lazy" @error="broken=true"><div v-else class="portrait-placeholder" aria-hidden="true"><svg viewBox="0 0 240 320" fill="none"><path d="M-40 270C50 170 135 300 275 165M-25 296C90 180 160 310 280 190"/><circle cx="169" cy="80" r="38"/></svg><span>{{initial}}</span></div></div></template>
<style scoped>
.character-portrait{width:100%;aspect-ratio:3/4;border:1px solid var(--edge);background:var(--wash);border-radius:14px;overflow:hidden;position:relative}.character-portrait img{display:block;width:100%;height:100%;object-fit:cover;object-position:50% 30%}.portrait-placeholder{position:relative;display:grid;place-items:center;width:100%;height:100%;background:linear-gradient(160deg,var(--canvas),var(--wash));color:var(--accent)}.portrait-placeholder span{font-family:var(--font-serif);font-size:clamp(40px,5vw,72px);font-weight:400;opacity:.8}.portrait-placeholder svg{position:absolute;width:100%;height:100%;inset:0;stroke:var(--accent);stroke-width:.6;opacity:.3}
</style>
