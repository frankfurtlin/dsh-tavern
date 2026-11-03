<script setup lang="ts">
import {provide,ref,onMounted,onUnmounted} from 'vue'
import WorldLookLibrary from './WorldLookLibrary.vue'
import {characterAppearanceKey,accountAppearanceKey} from '../world/character-appearance'
import {api} from '../api'
import {privateObjectBlob} from '../foundation/private-object'
import {useRequestScope} from '../platform/request-scope'
import type {ProductAccount} from '../foundation/types'
const props=defineProps<{worldId?:string;lineId?:string;disabled?:boolean;account?:boolean}>(),avatars=ref<Record<string,string>>({}),library=ref<InstanceType<typeof WorldLookLibrary>>()
const src=ref(''),name=ref('你'),capture=useRequestScope();let disposed=false
provide(characterAppearanceKey,{avatars,open:entityId=>library.value?.show(entityId)})
provide(accountAppearanceKey,{src,name})
onMounted(async()=>{if(!props.account)return;const scope=capture();try{const value=await api<ProductAccount>('/product/account');if(!scope.valid()||disposed)return;name.value=value.displayName||value.username||'你';if(value.avatarId){const blob=await privateObjectBlob(value.avatarId,()=>scope.valid()&&!disposed);if(scope.valid()&&!disposed)src.value=URL.createObjectURL(blob)}}catch{/* Keep account initials when offline. */}})
onUnmounted(()=>{disposed=true;if(src.value)URL.revokeObjectURL(src.value)})
</script>
<template><WorldLookLibrary v-if="worldId" :key="worldId+':'+(lineId||'')" ref="library" :world-id="worldId" :line-id="lineId" :disabled="disabled" :trigger="false" @previews="avatars=$event"/><slot/></template>
