<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {useRoute} from 'vue-router'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'
import PageHeader from '../workspace/PageHeader.vue'
import PublicCatalog from './PublicCatalog.vue'
const id=String(useRoute().params.authorId),capture=useRequestScope(),author=ref<{id:string;displayName:string}>(),error=ref('')
onMounted(async()=>{const scope=capture();try{const result=await api<{id:string;displayName:string}>('/authoring/authors/'+id);if(scope.valid())author.value=result}catch(e){if(scope.valid())error.value=(e as Error).message}})
</script>
<template><div class="workspace-page"><PageHeader :title="author?.displayName||'作者作品'" eyebrow="作者主页" description="这个作者通过审核的公开作品。"></PageHeader><p v-if="error" role="alert" class="error">{{error}}</p><PublicCatalog v-if="author" :author-id="id"/></div></template>
