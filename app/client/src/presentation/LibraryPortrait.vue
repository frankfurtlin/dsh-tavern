<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {useLocal} from '../stores/local'
import type {Document} from '../domain/local-contract'
import type {CharacterCard} from '../tavern/card-format'
import type {CompanionProfile} from '../companion/contract'
import {cardAvatar} from '../tavern/card-container'
import CharacterPortrait from './CharacterPortrait.vue'
const props=defineProps<{document:Document;name:string}>(),store=useLocal(),source=ref<string|null>(null)
let generation=0,disposed=false,lifecycle=new AbortController()
watch(()=>[props.document.id,props.document.revision],async()=>{
 const ticket=++generation,vault=store.vault,row=props.document;lifecycle.abort();lifecycle=new AbortController();const signal=lifecycle.signal
 if(source.value)URL.revokeObjectURL(source.value);source.value=null;if(!vault?.unlocked)return
 try{let card:Document<CharacterCard>|null=null;if(row.kind==='card')card=row as Document<CharacterCard>;else if(row.kind==='companion'){const ref=(row as Document<CompanionProfile>).value.sourceCard;if(ref){const original=await vault.read<CharacterCard>(ref.id,ref.revision);if(original?.kind==='card')card=original}}
  const url=card?await cardAvatar(vault,card,signal):null;if(disposed||ticket!==generation||signal.aborted||store.vault!==vault||!vault.unlocked){if(url)URL.revokeObjectURL(url);return}source.value=url
 }catch{/* The saved name remains usable when an image is unavailable. */}
},{immediate:true})
onUnmounted(()=>{disposed=true;generation++;lifecycle.abort();if(source.value)URL.revokeObjectURL(source.value)})
</script>
<template><CharacterPortrait :name="name" :src="source"/></template>
