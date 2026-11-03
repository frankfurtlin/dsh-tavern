<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {useRouter} from 'vue-router'
import {BookOpen,Clock3} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
import {useLocal} from '../stores/local'
import type {Document,LocalStory} from '../domain/local-contract'
import type {LocalBranch} from './contract'
import {readingLine} from './runtime'
import {historyRevisionPage,readingRevisionTarget} from './line-history'
const props=defineProps<{storyId:string;branchId:string|null}>(),emit=defineEmits<{close:[];continue:[point:{revision:number;paragraphId:string;offset:number}]} >(),store=useLocal(),router=useRouter()
const rows=ref<Document<LocalStory|LocalBranch>[]>([]),head=ref(0),page=ref(0),busy=ref(false),error=ref('')
let epoch=0,disposed=false
async function load(){const vault=store.vault,token=++epoch;if(!vault)return;busy.value=true;error.value='';rows.value=[];try{
 const scope=await readingLine(vault,props.storyId,props.branchId),source=scope.branch||scope.story;head.value=source.revision
 const values=await Promise.all(historyRevisionPage(source.revision,page.value).map(version=>vault.read<LocalStory|LocalBranch>(source.id,version)))
 if(disposed||token!==epoch||store.vault!==vault||!vault.unlocked)return
 if(values.some(row=>!row||row.kind!==source.kind||props.branchId&&(row.value as LocalBranch).storyId!==props.storyId))throw new Error('历史记录不完整，当前正文仍保留')
 rows.value=values as Document<LocalStory|LocalBranch>[]
 }catch(e){if(!disposed&&token===epoch){error.value=(e as Error).message;rows.value=[]}}finally{if(!disposed&&token===epoch)busy.value=false}}
const link=(row:Document<LocalStory|LocalBranch>)=>readingRevisionTarget(props.storyId,props.branchId,row.revision,row.value.paragraphIds[0],0)
const beforeLink=(row:Document<LocalStory|LocalBranch>)=>{const branch=row.value as LocalBranch;return readingRevisionTarget(props.storyId,branch.parentBranchId,branch.parentRevision,branch.fork.paragraphId,branch.fork.offset)}
async function afterLink(row:Document<LocalStory|LocalBranch>){const vault=store.vault;if(!vault)return;const id=row.value.paragraphIds.at(-1),paragraph=id?await vault.read<{text:string}>(id):null;if(!id||paragraph?.kind!=='paragraph')throw new Error('版本末尾缺少正文');return {...readingRevisionTarget(props.storyId,props.branchId,row.revision,id,paragraph.value.text.length),query:{...readingRevisionTarget(props.storyId,props.branchId,row.revision,id,paragraph.value.text.length).query,write:'1'}}}
async function continueFrom(row:Document<LocalStory|LocalBranch>){const token=epoch,vault=store.vault;try{const destination=await afterLink(row);if(disposed||token!==epoch||store.vault!==vault||!vault?.unlocked)return;if(destination){if(router.resolve(destination).fullPath===router.currentRoute.value.fullPath)emit('continue',{revision:row.revision,paragraphId:destination.query.paragraph!,offset:Number(destination.query.offset)});else await router.push(destination);emit('close')}}catch(e){if(!disposed)error.value=(e as Error).message}}
function label(row:Document<LocalStory|LocalBranch>){return row.kind==='story'?'原文':(row.value as LocalBranch).edition?'正文修订':'故事版本'}
watch(()=>[props.storyId,props.branchId],()=>{page.value=0;void load()},{immediate:true})
watch(page,()=>void load())
watch(()=>store.vault,()=>{epoch++;rows.value=[];busy.value=false})
onUnmounted(()=>{disposed=true;epoch++})
</script>
<template><Sheet title="历史版本" @close="emit('close')"><div class="line-history"><p v-if="error" class="final-error" role="alert">{{error}}<button @click="load">重试</button></p><p v-if="busy" role="status">正在打开历史…</p><ol v-else aria-label="故事版本记录"><li v-for="row in rows" :key="row.revision"><div class="history-dot"><Clock3 :size="17"/></div><article><div><strong>{{label(row)}} · v{{row.revision}}</strong><span v-if="row.revision===head">当前版本</span></div><small>{{new Date(row.updatedAt).toLocaleString()}} · {{row.value.chapters.length}} 章</small><RouterLink :to="link(row)" @click="emit('close')"><BookOpen :size="16"/>阅读这个版本</RouterLink><div class="history-actions"><RouterLink v-if="row.kind==='branch'" :to="beforeLink(row)" @click="emit('close')">回到分叉前</RouterLink><button @click="continueFrom(row)">从版本末尾写</button></div></article></li></ol><div class="final-page-actions"><button :disabled="busy||page===0" @click="page--">上一页</button><span>{{page+1}}</span><button :disabled="busy||(page+1)*20>=head" @click="page++">下一页</button></div></div></Sheet></template>
<style scoped>.line-history{display:grid;gap:20px}.line-history ol{list-style:none;padding:0;margin:0}.line-history li{display:grid;grid-template-columns:36px minmax(0,1fr);gap:16px;position:relative;padding-bottom:28px}.line-history li:not(:last-child):before{content:'';position:absolute;left:17px;top:34px;bottom:0;border-left:1px solid var(--edge)}.history-dot{height:34px;display:grid;place-items:center;border:1px solid var(--edge);border-radius:50%;background:var(--wash);color:var(--accent)}.line-history article{display:grid;gap:12px;min-width:0}.line-history article>div{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.line-history strong{font:20px var(--font-serif,serif)}.line-history small,.line-history article span{color:var(--quiet);font-size:12px}.line-history a{display:flex;gap:8px;align-items:center;min-height:44px;width:fit-content;color:var(--accent);font-size:14px}.history-actions{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.history-actions button{font-size:13px}.line-history a:focus-visible{outline:2px solid var(--accent);outline-offset:4px;border-radius:4px}</style>
