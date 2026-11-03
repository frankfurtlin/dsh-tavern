<script setup lang="ts">
import {ref,onUnmounted,watch} from 'vue'
import {Download,Check,Square} from 'lucide-vue-next'
import {downloadWorldReading} from '../offline/download-world'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const props=defineProps<{worldId:string;lineId:string;headId:string;origin:string;lineName:string}>()
const capture=useRequestScope(()=>[props.worldId,props.lineId,props.headId,props.origin]),app=useApp(),busy=ref(false),saved=ref(false),error=ref('')
let generation=0
function stop(){generation++;busy.value=false}
async function start(){
  if(!app.online||busy.value)return
  const scope=capture(),version=++generation,valid=()=>scope.valid()&&generation===version
  busy.value=true;saved.value=false;error.value=''
  try{await downloadWorldReading(props.worldId,props.lineId,props.headId,props.origin,valid);if(valid())saved.value=true}
  catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
onUnmounted(stop)
watch(()=>[props.worldId,props.lineId,props.headId,props.origin],()=>{stop();saved.value=false;error.value=''})
</script>
<template><div class="stack"><h2>{{lineName}}</h2><p v-if="busy" role="status">正在保存公开正文…</p><p v-if="saved" class="row" role="status"><Check :size="20"/>已下载，可离线阅读</p><p v-if="error" class="error" role="alert">{{error}}</p><button v-if="busy" @click="stop"><Square :size="18"/>停止下载</button><button v-else class="primary" :disabled="!app.online" @click="start"><Download :size="19"/>{{saved?'重新下载':'下载这条世界线'}}</button><RouterLink to="/downloads" class="inline-link">已下载的故事线</RouterLink></div></template>
