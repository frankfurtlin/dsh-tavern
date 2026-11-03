<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {onBeforeRouteLeave,useRoute,useRouter} from 'vue-router'
import {ArrowLeft,BookOpen} from 'lucide-vue-next'
import {useLocal} from '../stores/local'
import {canonical,type Document} from '../domain/local-contract'
import {createOriginalStory,newOriginalStoryDraft,readyOriginalStoryDraft,saveOriginalStoryDraft,validateOriginalStoryDraft,type OriginalStoryDraft} from './original-story'
const store=useLocal(),route=useRoute(),router=useRouter(),row=ref<Document<OriginalStoryDraft>|null>(null),title=ref(''),chapterTitle=ref('第一章'),text=ref(''),loading=ref(true),busy=ref(false),error=ref(''),conflict=ref(false),saved=ref(false)
const fields=()=>({title:title.value,chapterTitle:chapterTitle.value,text:text.value})
const dirty=computed(()=>row.value?.value.status==='editing'&&canonical(fields())!==canonical({title:row.value.value.title,chapterTitle:row.value.value.chapterTitle,text:row.value.value.text}))
const editing=computed(()=>row.value?.value.status==='editing')
let disposed=false,initializing=true,timer:ReturnType<typeof setTimeout>|null=null,work=Promise.resolve(),createdReturn=false,openingDraft:ReturnType<typeof newOriginalStoryDraft>|null=null
function show(value:Document<OriginalStoryDraft>){row.value=value;title.value=value.value.title;chapterTitle.value=value.value.chapterTitle;text.value=value.value.text;saved.value=true}
async function persist(){
  if(timer){clearTimeout(timer);timer=null}
  const vault=store.vault,values=fields();if(!vault||!row.value||!editing.value||conflict.value||disposed)return
  saved.value=false
  work=work.catch(()=>{}).then(async()=>{
    const current=row.value;if(!current||current.value.status!=='editing'||disposed||store.vault!==vault||!vault.unlocked||conflict.value)return
    if(canonical({title:current.value.title,chapterTitle:current.value.chapterTitle,text:current.value.text})===canonical(values)){saved.value=!dirty.value;return}
    try{const next=await saveOriginalStoryDraft(vault,current.id,current.revision,{...current.value,...values});if(!disposed&&store.vault===vault&&row.value?.id===current.id){row.value=next;saved.value=!dirty.value;error.value=''}}catch(e){if(!disposed&&store.vault===vault){error.value=(e as Error).message;conflict.value=(e as {code?:string}).code==='REVISION_CONFLICT'}}
  });await work
}
watch([title,chapterTitle,text],()=>{if(initializing||!editing.value||disposed)return;saved.value=false;if(timer)clearTimeout(timer);timer=setTimeout(()=>void persist(),250)})
async function initialize(){
 const vault=store.vault;if(!vault||busy.value||disposed)return;busy.value=true;error.value=''
 try{const id=route.query.draft;if(typeof id==='string'){
   const current=await vault.read<OriginalStoryDraft>(id);if(!current||current.kind!=='settings')throw Error('这份草稿已不可用，请返回书架');validateOriginalStoryDraft(current.value,id);if(current.value.status==='discarded')throw Error('这份草稿已结束，请返回书架');if(!disposed&&store.vault===vault)show(current)
 }else{
   openingDraft??=newOriginalStoryDraft();const draft=openingDraft,existing=await vault.read<OriginalStoryDraft>(draft.id);if(existing){if(existing.kind!=='settings')throw Error('创建草稿的身份已被占用');validateOriginalStoryDraft(existing.value,draft.id)}const current=existing||await saveOriginalStoryDraft(vault,draft.id,0,draft.value);if(!disposed&&store.vault===vault){show(current);await router.replace({path:'/local/new-story',query:{draft:current.id}})}
 }}catch(e){if(!disposed)error.value=(e as Error).message}finally{initializing=false;loading.value=false;busy.value=false}
}
async function recheck(){
 const vault=store.vault,current=row.value;if(!vault||!current||busy.value)return;busy.value=true;initializing=true
 try{const latest=await vault.read<OriginalStoryDraft>(current.id);if(!latest||latest.kind!=='settings')throw Error('这份草稿已不可用');validateOriginalStoryDraft(latest.value,latest.id);if(!disposed&&store.vault===vault){show(latest);conflict.value=false;error.value=''}}catch(e){error.value=(e as Error).message}finally{initializing=false;busy.value=false}
}
async function create(){
 const vault=store.vault;if(!vault||!row.value||busy.value||conflict.value)return;busy.value=true;error.value=''
 try{await persist();await work;const current=row.value;if(!current||dirty.value||conflict.value)throw Error('草稿尚未保存，请先重试保存');if(current.value.status==='editing'){
   const next=await saveOriginalStoryDraft(vault,current.id,current.revision,readyOriginalStoryDraft(current.value));if(disposed||store.vault!==vault||!vault.unlocked)return;row.value=next
 }
 const frozen=row.value!;const id=frozen.value.status==='completed'?frozen.value.storyId:await createOriginalStory(vault,frozen)
 if(!disposed&&store.vault===vault&&vault.unlocked){createdReturn=true;busy.value=false;await router.push('/local/read/'+id)}
 }catch(e){if(!disposed&&store.vault===vault){error.value=(e as Error).message;conflict.value=(e as {code?:string}).code==='REVISION_CONFLICT'}}finally{busy.value=false}
}
function beforeUnload(event:BeforeUnloadEvent){if(dirty.value||busy.value){event.preventDefault();event.returnValue=''}}
onMounted(()=>{window.addEventListener('beforeunload',beforeUnload);void initialize()})
onBeforeRouteLeave(async()=>{if(createdReturn)return;if(busy.value)return false;await persist();await work;if(dirty.value){error.value='草稿尚未保存，请重试保存后再离开';return false}})
onUnmounted(()=>{disposed=true;window.removeEventListener('beforeunload',beforeUnload);if(timer)clearTimeout(timer)})
</script>
<template><section class="original-story-page"><div class="final-page-heading"><div class="final-heading-copy"><span class="final-eyebrow">A NEW STORY</span><h1>{{route.query.copy==='1'?'保存为故事':'新建故事'}}</h1></div><RouterLink to="/local/library" aria-label="返回书架" class="final-icon-button"><ArrowLeft :size="20"/></RouterLink></div><p v-if="loading" role="status">正在打开草稿…</p><p v-if="error" class="final-error" role="alert">{{error}}</p><button v-if="!row&&!loading" :disabled="busy" @click="initialize">重试打开草稿</button><form v-if="row" class="original-story-form" @submit.prevent="create"><fieldset :disabled="busy||!editing"><label>书名<input v-model="title" maxlength="512" autocomplete="off" required placeholder="为你的故事起个名字"></label><label>章节标题<input v-model="chapterTitle" maxlength="512" autocomplete="off" required></label><label>{{route.query.copy==='1'?'阅读稿正文':'故事开头'}}<textarea v-model="text" maxlength="200000" required placeholder="从你想写的第一句话开始"></textarea></label></fieldset><div class="original-story-state"><span v-if="editing" class="final-subtle" role="status">{{saved&&!dirty?'草稿已保存':'正在保存草稿…'}}</span><span v-else class="final-subtle" role="status">{{row.value.status==='completed'?'故事已保存':'开头已保存'}}</span><button v-if="dirty&&!conflict" type="button" :disabled="busy" @click="persist">重试保存草稿</button></div><button v-if="conflict" type="button" :disabled="busy" @click="recheck">载入当前草稿</button><button v-else class="final-primary" :disabled="busy"><BookOpen :size="19"/>{{busy?'正在保存…':row.value.status==='ready'?'重试创建故事':row.value.status==='completed'?'打开故事':'创建并阅读'}}</button></form></section></template>
<style scoped>.original-story-page{max-width:840px}.original-story-page .final-page-heading>a{min-height:44px}.original-story-form,.original-story-form fieldset{display:grid;gap:24px}.original-story-form fieldset{margin:0;padding:0;border:0;min-width:0}.original-story-form textarea{min-height:320px;resize:vertical;line-height:1.85}.original-story-form>.final-primary{justify-self:start;min-height:48px}.original-story-state{display:flex;align-items:center;gap:12px;flex-wrap:wrap}</style>
