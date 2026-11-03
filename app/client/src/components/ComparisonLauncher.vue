<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {useRoute} from 'vue-router'
import {api,ApiError,newKey} from '../api'
import {cacheGet,cacheSet} from '../platform/bridge'
import {useRequestScope} from '../platform/request-scope'
import {useApp} from '../stores/app'
import Sheet from './Sheet.vue'
import {comparisonPath,createComparisonController,type ComparisonKind,type ComparisonTarget,type ComparisonCapabilities,type ComparisonQuery,type ComparisonSnapshot,type ComparisonRevealIntent,type ComparisonRevealReceipt,type ComparisonRevealStatus} from '../api/comparison'
const props=defineProps<{storyId:string;kind:ComparisonKind;resourceId:string;targets:ComparisonTarget[];currentLineId?:string}>()
const route=useRoute(),store=useApp(),capture=useRequestScope(()=>[props.storyId,props.kind,props.resourceId,props.targets.map(t=>[t.id,t.nodeId])])
const capabilities=ref<ComparisonCapabilities|null>(null),opened=ref(false),left=ref(''),right=ref(''),alignment=ref<'latest'|'same-time'>('latest'),minute=ref('0'),view=ref<ComparisonSnapshot|null>(null),error=ref(''),notice=ref(''),busy=ref(false),pending=ref<ComparisonRevealIntent|null>(null),confirm=ref<'left'|'right'|null>(null),confirmed=ref(false)
let controller:ReturnType<typeof createComparisonController>|null=null,scope:ReturnType<typeof capture>|null=null,version=0,disposed=false
const available=computed(()=>capabilities.value?.ready===true&&props.targets.length>=2)
const intentKey=()=>`comparison-reveal:${props.storyId}:${props.kind}:${props.resourceId}`
const active=()=>!disposed&&opened.value&&scope?.valid()===true
const pendingPath=computed(()=>pending.value?props.targets.find(t=>t.id===pending.value?.lineId)?.name||'原路径':'')
function close(){version++;controller?.dispose();controller=null;scope=null;opened.value=false;view.value=null;pending.value=null;error.value='';notice.value='';busy.value=false;confirm.value=null;confirmed.value=false}
async function capabilitiesLoad(){const own=capture();try{const value=await api<ComparisonCapabilities>('/comparison/capabilities');if(!disposed&&own.valid())capabilities.value=value.protocol==='reader-compare-v1'&&value.ready===true?value:null}catch{if(own.valid())capabilities.value=null}}
function query(leftOffset=0,rightOffset=0):ComparisonQuery|null{
 const a=props.targets.find(t=>t.id===left.value),b=props.targets.find(t=>t.id===right.value);if(!a||!b||a.id===b.id)return null
 const atMinute=Number(minute.value);if(alignment.value==='same-time'&&(!Number.isSafeInteger(atMinute)||atMinute<0))return null
 return {storyId:props.storyId,kind:props.kind,resourceId:props.resourceId,left:a,right:b,alignment:alignment.value,...(alignment.value==='same-time'?{atMinute}:{}),leftOffset,rightOffset}
}
async function load(leftOffset=0,rightOffset=0){
 const request=++version,c=controller,q=query(leftOffset,rightOffset);confirm.value=null;confirmed.value=false;error.value='';if(!c||!q){view.value=null;return}
 busy.value=true
 try{const value=await c.load(q);if(active()&&request===version&&value)view.value=value}
 catch(e){if(active()&&request===version)error.value=(e as Error).message}
 finally{if(active()&&request===version)busy.value=false}
}
async function open(){
 if(!available.value||!store.online)return
 close();scope=capture();opened.value=true;left.value=props.targets.find(t=>t.id===props.currentLineId)?.id||props.targets[0]!.id;right.value=props.targets.find(t=>t.id!==left.value)!.id;alignment.value='latest';minute.value='0'
 const localScope=scope,key=intentKey(),valid=()=>active()&&scope===localScope
 controller=createComparisonController({active:valid,
  get:q=>api<ComparisonSnapshot>(comparisonPath(q)),
  reveal:value=>api<ComparisonRevealReceipt>(`/stories/${value.storyId}/compare/reveal`,{method:'POST',key:value.requestId,body:{confirmed:true,kind:value.kind,resourceId:value.resourceId,lineId:value.lineId,nodeId:value.nodeId,expectedVersion:value.expectedVersion}}),
  lookup:value=>api<ComparisonRevealStatus>(`/stories/${value.storyId}/compare/reveal/requests?requestId=${encodeURIComponent(value.requestId)}`),
  persist:async value=>{if(!valid())return;await cacheSet(key,value);if(!valid())throw new Error('账号或路径已经变化')},
  unknown:e=>e instanceof ApiError&&e.status===0||e instanceof ApiError&&e.status>=500})
 try{const saved=await cacheGet<ComparisonRevealIntent|null>(key);if(!valid())return;if(saved&&saved.storyId===props.storyId&&saved.kind===props.kind&&saved.resourceId===props.resourceId){controller.restore(saved);pending.value=saved;notice.value='正在查询原查看请求…';await recover()}}
 catch(e){if(valid())error.value=(e as Error).message}
 if(valid())await load()
}
async function recover(){
 const c=controller,request=++version;if(!c||!active())return;busy.value=true;error.value=''
 try{const receipt=await c.recover();if(active()&&request===version){pending.value=c.pending;notice.value=receipt?'查看范围已确认':'原请求尚未查到，保留等待确认';if(receipt)await load()}}
 catch(e){if(active()&&request===version)error.value=(e as Error).message}
 finally{if(active()&&request===version)busy.value=false}
}
async function reveal(){
 const side=confirm.value,c=controller,value=side&&view.value?.[side];if(!c||!value||!confirmed.value||pending.value||busy.value||!active())return
 const request=++version,intent:ComparisonRevealIntent={requestId:newKey(),storyId:props.storyId,kind:props.kind,resourceId:props.resourceId,lineId:value.lineId,nodeId:value.nodeId,expectedVersion:value.viewVersion};busy.value=true;error.value=''
 try{const receipt=await c.reveal(intent);if(active()&&request===version){pending.value=c.pending;confirm.value=null;confirmed.value=false;if(receipt)await load()}}
 catch(e){if(active()&&request===version){pending.value=c.pending;error.value=(e as Error).message;notice.value=pending.value?'查看请求尚待确认，请查询原请求':''}}
 finally{if(active()&&request===version)busy.value=false}
}
function text(value:unknown,known:boolean){return !known?'未知':typeof value==='string'?value:JSON.stringify(value)}
watch([()=>props.storyId,()=>props.kind,()=>props.resourceId,()=>props.targets.map(t=>t.id+':'+t.nodeId).join('|'),()=>route.fullPath,()=>store.user?.id],()=>{close();capabilities.value=null;void capabilitiesLoad()})
onMounted(()=>{void capabilitiesLoad()});onUnmounted(()=>{close();disposed=true})
</script>
<template>
 <button v-if="available" class="comparison-launch" :disabled="!store.online" @click="open">比较路径</button>
 <Sheet v-if="opened" title="比较路径" class="comparison-dialog" @close="close">
  <div class="comparison-selectors"><label>左侧路径<select v-model="left" :disabled="busy||!!pending" @change="load()"><option v-for="target in targets" :key="target.id" :value="target.id">{{target.name}} · {{target.id.slice(0,6)}}</option></select></label><label>右侧路径<select v-model="right" :disabled="busy||!!pending" @change="load()"><option v-for="target in targets" :key="target.id" :value="target.id">{{target.name}} · {{target.id.slice(0,6)}}</option></select></label></div>
  <div v-if="kind==='world'" class="comparison-time"><label>对齐<select v-model="alignment" :disabled="busy||!!pending" @change="load()"><option value="latest">所选节点</option><option v-if="capabilities?.alignments.world.includes('same-time')" value="same-time">同一世界时间</option></select></label><form v-if="alignment==='same-time'" @submit.prevent="load()"><label>第几分钟<input v-model="minute" type="number" min="0" step="1" :disabled="busy||!!pending"></label><button :disabled="busy||!!pending">对齐查看</button></form></div>
  <div v-if="error" class="error" role="alert">{{error}}<button v-if="!pending" :disabled="busy" @click="load()">载入最新信息</button></div><p v-if="notice" class="muted" role="status">{{notice}}</p>
  <div v-if="pending" class="comparison-pending"><span>{{pendingPath}}：待确认</span><button :disabled="busy" @click="recover">查询结果</button></div>
  <p v-if="busy&&!view" class="muted" role="status">正在读取…</p><p v-if="!query()" class="muted">请选择两条不同路径。</p>
  <template v-if="view">
   <div class="comparison-common"><small>共同起点</small><strong>{{view.common.visible?(view.common.title||'同一来源'):'尚未查看'}}</strong></div>
   <div class="comparison-panes"><article v-for="side in (['left','right'] as const)" :key="side" :aria-label="side==='left'?'左侧正文':'右侧正文'">
    <header><h3>{{view[side].name}}</h3><small v-if="alignment==='same-time'">第 {{view.atMinute}} 分钟之前</small></header>
    <section v-for="item in view[side].items" :key="item.id" class="comparison-prose"><h4>{{item.title}}</h4><small v-if="item.minute!==undefined">第 {{item.minute}} 分钟 · {{item.action}}</small><p>{{item.text}}</p><small v-if="item.partial">仅已读部分</small></section>
    <p v-if="!view[side].items.length" class="muted">暂无已读正文。</p>
    <button v-if="view[side].nextOffset!==null" :disabled="busy||!!pending" @click="load(side==='left'?view.left.nextOffset||0:0,side==='right'?view.right.nextOffset||0:0)">查看下一页</button>
    <button v-if="view[side].hasUnseen&&capabilities?.revealReady" :disabled="busy||!!pending" @click="confirm=side;confirmed=false">查看这条线后续</button>
   </article></div>
   <div v-if="confirm" class="comparison-confirm"><label><input v-model="confirmed" type="checkbox">确认查看{{view[confirm].name}}的后续</label><div class="actions"><button :disabled="busy" @click="confirm=null">取消</button><button class="primary" :disabled="!confirmed||busy" @click="reveal">确认查看</button></div></div>
   <details v-if="view.differences.length" class="comparison-differences"><summary>公开状态差异 · {{view.differences.length}}{{view.differencesTruncated?'＋':''}}</summary><div v-for="difference in view.differences" :key="difference.path"><strong>{{difference.path}}</strong><p>{{text(difference.before,difference.beforeKnown)}}</p><p>{{text(difference.after,difference.afterKnown)}}</p></div></details>
  </template>
 </Sheet>
</template>
<style>
.comparison-launch{min-width:44px;min-height:44px;font-size:13px}.comparison-dialog .sheet{width:min(1120px,100vw);height:100%;max-height:100%}.comparison-selectors{display:grid;grid-template-columns:1fr 1fr;gap:20px}.comparison-selectors label{min-width:0}.comparison-selectors select{max-width:100%}.comparison-time{display:flex;align-items:end;gap:16px;flex-wrap:wrap}.comparison-time>label{width:220px;max-width:100%}.comparison-time form{display:flex;gap:12px;align-items:center}.comparison-time form label{max-width:180px}.comparison-common{border-block:1px solid var(--line);padding:18px 0;display:grid;gap:8px;margin:20px 0}.comparison-common strong{font:500 20px var(--serif)}.comparison-common small{color:var(--muted)}.comparison-panes{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:32px}.comparison-panes>article{min-width:0}.comparison-panes>article+article{border-left:1px solid var(--line);padding-left:24px}.comparison-panes header{padding-bottom:12px}.comparison-panes h3,.comparison-prose h4{font:500 20px/1.6 var(--serif);overflow-wrap:anywhere}.comparison-prose h4{font-size:18px;margin:20px 0 8px}.comparison-prose p{font:18px/1.9 var(--serif);white-space:pre-wrap;overflow-wrap:anywhere}.comparison-prose small{color:var(--muted)}.comparison-panes article>button{margin:12px 8px 0 0}.comparison-confirm{margin:24px 0;padding:20px;background:var(--tint);border-radius:8px}.comparison-confirm label{display:flex;align-items:center;min-height:44px;gap:10px}.comparison-confirm input{width:18px;height:18px;min-height:18px;flex-shrink:0}.comparison-pending{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.comparison-differences{margin-top:32px;border-top:1px solid var(--line);padding-top:16px}.comparison-differences summary{min-height:44px;cursor:pointer}.comparison-differences>div{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}.comparison-differences strong{grid-column:1/-1;font-size:13px}.comparison-differences p{margin:0;font-size:14px;white-space:pre-wrap}@media(max-width:700px){.comparison-selectors{grid-template-columns:1fr;gap:0}.comparison-panes{grid-template-columns:1fr;gap:24px}.comparison-panes>article+article{border-left:0;border-top:1px solid var(--line);padding:24px 0 0}.comparison-time>label{width:100%}.comparison-time form{width:100%}.comparison-time form label{min-width:0;flex:1}}
</style>
