<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {BookOpen} from 'lucide-vue-next'
import {bundledCover} from './cover-assets'
const props=defineProps<{id?:string;title:string}>(),broken=ref(false)
const source=computed(()=>bundledCover(props.id))
watch(source,()=>{broken.value=false})
</script>
<template><img v-if="source&&!broken" class="final-book-cover" :src="source" :alt="title+'封面'" width="400" height="600" loading="lazy" @error="broken=true"><div v-else class="final-book-cover final-book-placeholder" aria-hidden="true"><BookOpen :size="42"/></div></template>
