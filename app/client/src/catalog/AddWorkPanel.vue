<script setup lang="ts">
import {BookOpen,Globe,Users,Heart,Upload,Compass} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
defineEmits<{close:[];import:[]}>()
const choices=[{label:'新建故事',icon:BookOpen,to:'/local/new-story'},{label:'角色',icon:Users,to:'/local/characters?create=1'},{label:'伴侣',icon:Heart,to:'/local/companions?create=1'},{label:'文游',icon:Globe,to:'/local/new-world'}]
</script>
<template><Sheet title="添加作品" @close="$emit('close')"><div class="add-work-panel final-app"><div class="add-work-choices"><RouterLink v-for="choice in choices" :key="choice.label" :to="choice.to" @click="$emit('close')"><component :is="choice.icon" :size="26"/><strong>{{choice.label}}</strong></RouterLink></div><button @click="$emit('import')"><Upload :size="20"/>导入文件</button><RouterLink to="/local/discover" @click="$emit('close')"><Compass :size="20"/>看看原创短篇</RouterLink></div></Sheet></template>
<style scoped>.add-work-panel{display:grid;gap:12px}.add-work-choices{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px}.add-work-choices a{display:grid;align-content:start;gap:12px;padding:24px;border:1px solid var(--edge);border-radius:16px;background:var(--canvas);min-width:0}.add-work-choices svg{color:var(--accent)}.add-work-choices small{color:var(--quiet);font-size:13px}.add-work-panel>button,.add-work-panel>a{display:flex;align-items:center;gap:12px;min-height:48px;padding:12px 16px;border-radius:12px}.add-work-panel>button{justify-content:flex-start}.add-work-panel>a{color:var(--quiet)}</style>
