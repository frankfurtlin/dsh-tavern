<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {BookOpen} from 'lucide-vue-next'
import {curatedCover} from './curated-artwork'
const props=defineProps<{coverKey?:string;title:string;priority?:boolean}>(),broken=ref(false)
const source=computed(()=>curatedCover(props.coverKey))
watch(source,()=>{broken.value=false})
</script>
<template>
 <img v-if="source&&!broken" class="curated-cover-image" :src="source" :alt="title+'封面'" width="640" height="960" :loading="priority?'eager':'lazy'" decoding="async" @error="broken=true">
 <div v-else class="curated-cover-fallback" aria-hidden="true"><BookOpen :size="36"/></div>
</template>
<style scoped>
.curated-cover-image{display:block;width:100%;height:100%;object-fit:cover;object-position:center 48%}
.curated-cover-fallback{display:grid;place-items:center;width:100%;height:100%;min-height:160px;background:var(--paper);color:var(--quiet)}
</style>
