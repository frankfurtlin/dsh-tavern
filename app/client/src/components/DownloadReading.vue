<script setup lang="ts">
import {ref,onUnmounted} from 'vue'
import {Download,Check,Square} from 'lucide-vue-next'
import {downloadReading} from '../offline/download'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
const props=defineProps<{bookId:string;branchId:string;revisionId:string;origin:string;lineName:string}>()
const capture=useRequestScope(()=>[props.bookId,props.branchId,props.revisionId]),store=useApp(),busy=ref(false),done=ref(0),total=ref(0),saved=ref(false),error=ref('')
let generation=0
function cancel(){generation++;busy.value=false}
async function start(){const scope=capture(),epoch=++generation,valid=()=>scope.valid()&&epoch===generation;busy.value=true;error.value='';saved.value=false
  try{await downloadReading(props.bookId,props.branchId,props.revisionId,props.origin,(count,size)=>{if(valid()){done.value=count;total.value=size}},valid);if(valid())saved.value=true}
  catch(e){if(valid())error.value=(e as Error).message}finally{if(valid())busy.value=false}
}
onUnmounted(cancel)
</script>
<template><div class="stack"><h2>{{lineName}}</h2><p v-if="busy" role="status">{{done}} / {{total}} 章</p><progress v-if="busy" :value="done" :max="total||1" aria-label="故事线下载进度"></progress><p v-if="saved" class="row" role="status"><Check :size="20"/>已下载，可离线阅读</p><p v-if="error" class="error" role="alert">{{error}}</p><button v-if="busy" @click="cancel"><Square :size="18"/>停止下载</button><button v-else :disabled="!store.online" class="primary" @click="start"><Download :size="19"/>{{saved?'重新下载':'下载这条故事线'}}</button><RouterLink to="/downloads" class="inline-link">已下载的故事线</RouterLink></div></template>
