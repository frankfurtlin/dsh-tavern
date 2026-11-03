<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {BookOpen} from 'lucide-vue-next'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import {curatedCover} from '../catalog/curated-artwork'
const props=defineProps<{id:string;title:string;theme:string;hasCover:boolean;coverKey?:string}>(),data=ref(''),broken=ref(false),capture=useRequestScope();let attempt=0
const source=computed(()=>data.value||curatedCover(props.coverKey))
watch(source,()=>{broken.value=false})
watch(()=>[props.id,props.hasCover],async()=>{const epoch=++attempt,scope=capture();data.value='';if(!props.hasCover)return;try{const result=await api<{dataUrl:string}>('/authoring/publications/'+props.id+'/cover');if(scope.valid()&&epoch===attempt)data.value=result.dataUrl}catch{}},{immediate:true})
</script>
<template><div class="author-cover" :class="{'has-art':source&&!broken}" :data-theme="theme"><img v-if="source&&!broken" :src="source" :alt="title+'封面'" width="640" height="960" loading="lazy" decoding="async" @error="broken=true"><template v-else><BookOpen :size="42" aria-hidden="true"/><strong>{{title}}</strong></template></div></template>
