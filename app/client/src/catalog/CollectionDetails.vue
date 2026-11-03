<script setup lang="ts">
import {ref,onUnmounted} from 'vue'
import Sheet from '../components/Sheet.vue'
import {useLocal} from '../stores/local'
import {ContractError} from '../domain/local-contract'
import {itemMetadata,updateCollectionMetadata,collectionLabels,type CollectionItem} from './collection-index'
const props=defineProps<{item:CollectionItem}>(),emit=defineEmits<{close:[];saved:[]}>(),store=useLocal(),initial=itemMetadata(props.item.document),favorite=ref(initial.favorite),pinned=ref(initial.pinned),tags=ref(initial.tags.join('，')),groups=ref(initial.groups.join('，')),busy=ref(false),error=ref(''),conflict=ref(false);let intent:{id:string;patch:Partial<typeof initial>}|null=null
const split=(text:string)=>text.split(/[，,\n]/).map(v=>v.trim()).filter(Boolean)
let disposed=false;onUnmounted(()=>disposed=true)
async function save(){const vault=store.vault;if(!vault||busy.value||conflict.value)return;busy.value=true;error.value='';if(!intent)intent={id:crypto.randomUUID(),patch:{favorite:favorite.value,pinned:pinned.value,tags:split(tags.value),groups:split(groups.value)}};try{await updateCollectionMetadata(vault,intent.id,[props.item.document],intent.patch);if(!disposed&&store.vault===vault&&vault.unlocked){emit('saved');emit('close')}}catch(e){if(!disposed&&store.vault===vault){error.value=(e as Error).message;conflict.value=e instanceof ContractError&&e.code==='REVISION_CONFLICT';if(e instanceof ContractError&&['BOOK_METADATA','LIBRARY_METADATA','LIBRARY_SELECTION'].includes(e.code))intent=null}}finally{if(!disposed)busy.value=false}}
</script>
<template><Sheet :title="item.title" @close="!busy&&emit('close')"><form class="final-card-form" @submit.prevent="save"><p class="final-subtle">{{collectionLabels[item.kind]}}</p><p v-if="error" class="final-error" role="alert">{{error}}</p><fieldset :disabled="busy||!!intent"><label class="final-check"><input v-model="favorite" type="checkbox">收藏</label><label class="final-check"><input v-model="pinned" type="checkbox">置顶</label><label>标签<input v-model="tags" maxlength="12000" placeholder="用逗号分隔"></label><label>分组<input v-model="groups" maxlength="12000" placeholder="用逗号分隔"></label></fieldset><button v-if="conflict" type="button" @click="emit('saved');emit('close')">关闭并载入当前版本</button><button v-else class="final-primary" :disabled="busy">{{intent?'重试保存':'保存'}}</button></form></Sheet></template>
<style scoped>fieldset{border:0;margin:0;padding:0;display:grid;gap:18px}</style>
