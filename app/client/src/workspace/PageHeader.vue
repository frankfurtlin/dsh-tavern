<script setup lang="ts">
defineProps<{title:string;description?:string;eyebrow?:string}>()
</script>
<template><header class="workspace-page-header"><div class="workspace-heading-copy"><small v-if="eyebrow">{{eyebrow}}</small><h1>{{title}}</h1><p v-if="description">{{description}}</p></div><div class="workspace-heading-actions"><slot/></div></header></template>
