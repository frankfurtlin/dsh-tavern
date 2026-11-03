<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {useRouter} from 'vue-router'
import {Heart,ArrowUpRight} from 'lucide-vue-next'
import Sheet from '../components/Sheet.vue'
import {useLocal} from '../stores/local'
import {styles,type CompanionProfile} from './contract'
import type {Document} from '../domain/local-contract'
import type {ChatSession} from '../tavern/chat-contract'
import {promotionPreview,promoteCompanion,type PromotionTarget,type PromotionPreview} from './promotion'
const props=defineProps<{target:PromotionTarget;initial?:Pick<CompanionProfile,'name'|'personality'|'style'>}>(),emit=defineEmits<{close:[]}>(),store=useLocal(),router=useRouter()
const preview=ref<PromotionPreview|null>(null),selected=ref<string[]>([]),name=ref(''),personality=ref(''),style=ref<CompanionProfile['style']>('gentle'),loading=ref(true),busy=ref(false),error=ref(''),controller=new AbortController()
let disposed=false,loadEpoch=0
const chats=ref<Document<ChatSession>[]>([]),chatChoice=ref('')
const selectedCharacters=computed(()=>preview.value?.messages.filter(m=>selected.value.includes(m.messageId)).reduce((n,m)=>n+m.text.length,0)||0)
async function load(){const vault=store.vault;if(!vault)return;const token=++loadEpoch;loading.value=true;error.value='';selected.value=[];try{const chosen=chats.value.find(row=>row.id===chatChoice.value),target=chosen?{kind:'session' as const,id:chosen.id,revision:chosen.revision}:props.target,value=await promotionPreview(vault,target);if(disposed||token!==loadEpoch||store.vault!==vault||!vault.unlocked)return;preview.value=value;name.value=props.initial?.name||value.card.name;personality.value=props.initial?.personality??(value.card.personality||value.card.description);style.value=props.initial?.style||'gentle';if(props.target.kind==='card'&&!chats.value.length){const rows=await vault.list<ChatSession>('session');if(disposed||token!==loadEpoch||store.vault!==vault||!vault.unlocked)return;chats.value=rows.filter(row=>!row.value.companionId&&!row.value.archived&&row.value.cards.length===1&&row.value.cards[0]?.id===props.target.id&&row.value.cards[0]?.value.identityId===value.source.identityId).sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt))}}catch(e){if(!disposed&&token===loadEpoch)error.value=(e as Error).message}finally{if(token===loadEpoch)loading.value=false}}
async function save(){const vault=store.vault,value=preview.value;if(!vault||!value||busy.value||loading.value)return;const token=loadEpoch;busy.value=true;error.value='';try{const id=value.existingId||await promoteCompanion(vault,value,{name:name.value,personality:personality.value,style:style.value},selected.value,controller.signal);if(disposed||token!==loadEpoch||controller.signal.aborted||store.vault!==vault||!vault.unlocked)return;emit('close');await router.push('/local/companions/'+id)}catch(e){if(!disposed&&token===loadEpoch&&store.vault===vault)error.value=(e as Error).message}finally{busy.value=false}}
function close(){controller.abort();disposed=true;emit('close')}
onMounted(()=>void load())
onUnmounted(()=>{disposed=true;controller.abort()})
</script>
<template>
 <Sheet title="让 TA 留在身边" @close="close">
  <div class="promotion-panel">
   <p v-if="loading" role="status">正在打开…</p>
   <p v-if="error" class="final-error" role="alert">{{error}}</p>
   <template v-if="preview">
    <div class="promotion-identity"><span><Heart :size="23"/></span><div><h2>{{preview.card.name}}</h2><small>{{preview.source.kind==='session'?'从这段相识开始':'从这个角色开始'}}</small></div></div>
    <template v-if="preview.existingId"><p>TA 已经在你的伴侣中。</p><button class="final-primary" :disabled="busy" @click="save">打开伴侣<ArrowUpRight :size="17"/></button></template>
    <form v-else class="final-card-form" @submit.prevent="save"><fieldset class="promotion-fields" :disabled="busy||loading">
     <label v-if="props.target.kind==='card'&&chats.length">相识记录<select v-model="chatChoice" @change="load"><option value="">从角色设定开始</option><option v-for="chat in chats" :key="chat.id" :value="chat.id">{{chat.value.title}} · {{new Date(chat.updatedAt).toLocaleDateString()}}</option></select></label>
     <label>名字<input v-model="name" maxlength="200" required></label>
     <label>相处风格<select v-model="style"><option v-for="(label,key) in styles" :key="key" :value="key">{{label}}</option></select></label>
     <details><summary>人格设定</summary><label>想认识怎样的 TA<textarea v-model="personality" maxlength="20000" rows="5"></textarea></label></details>
     <fieldset v-if="preview.messages.length" class="promotion-memories"><legend>带上共同记忆 <span>{{selected.length}} / 50</span></legend><p class="final-subtle">只带走你勾选的内容。</p><label v-for="message in preview.messages" :key="message.messageId" class="promotion-memory"><input v-model="selected" type="checkbox" :value="message.messageId" :disabled="!selected.includes(message.messageId)&&(selected.length>=50||selectedCharacters+message.text.length>128000)"><span><small>{{message.role==='user'?preview.persona.name:preview.card.name}} · {{new Date(message.at).toLocaleDateString()}}</small><span>{{message.text}}</span></span></label></fieldset>
     <button class="final-primary" :disabled="busy||loading"><Heart :size="17"/>{{busy?'正在保存…':'成为伴侣'}}</button>
    </fieldset></form>
   </template>
  </div>
 </Sheet>
</template>
<style scoped>
.promotion-panel{display:grid;gap:22px}.promotion-identity{display:flex;align-items:center;gap:16px}.promotion-identity>span{display:grid;place-items:center;width:56px;height:56px;flex:none;border-radius:50%;background:var(--wash);color:var(--accent)}.promotion-identity h2{font:26px/1.3 var(--font-serif,serif);margin:0 0 5px}.promotion-identity small,.promotion-memory small{color:var(--quiet);font-size:12px}.promotion-memories{border:0;padding:0;min-width:0}.promotion-memories legend{display:flex;gap:14px;align-items:center;font-weight:600}.promotion-memories legend span{font-size:12px;color:var(--quiet);font-weight:400}.promotion-memory{display:flex;align-items:flex-start;gap:12px;padding:14px 0;border-bottom:1px solid var(--edge);cursor:pointer}.promotion-memory input{width:18px;height:18px;flex:none;margin-top:3px}.promotion-memory>span{display:grid;gap:7px;min-width:0}.promotion-memory>span>span{font-size:14px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto}.promotion-panel details label{margin-top:14px}
.promotion-fields{display:grid;gap:20px;border:0;padding:0;margin:0;min-width:0}
</style>
