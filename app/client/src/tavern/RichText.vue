<script setup lang="ts">
import {computed} from 'vue'
import {richText} from './rich-text'
const props=defineProps<{text:string}>(),content=computed(()=>richText(props.text))
</script>
<template><div class="final-chat-prose" v-if="content.html" v-html="content.html"></div><p v-else class="final-chat-prose">{{content.plain}}</p></template>
