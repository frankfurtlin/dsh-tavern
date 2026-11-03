<script setup lang="ts">
import {computed} from 'vue'
import type {DomainKind} from '../domain/local-contract'
import type {AssetLink,LocalAsset} from './contract'
import AssetView from './AssetView.vue'
import {useAssetIndex} from './asset-index'
const props=defineProps<{targetId:string;targetKind:DomainKind;targetRevision:number;paragraphId?:string;role:AssetLink['role']}>(),{rows}=useAssetIndex()
const matched=computed(()=>rows.value.filter(row=>!row.value.retired&&row.value.links.some(link=>link.targetId===props.targetId&&link.targetKind===props.targetKind&&link.targetRevision===props.targetRevision&&link.role===props.role&&link.paragraphId===props.paragraphId)))
</script>
<template><div v-if="matched.length" class="final-attached-assets" :class="'role-'+role"><RouterLink v-for="row in matched" :key="row.id" :to="{path:'/local/gallery',query:{asset:row.id}}" :aria-label="'查看素材 '+row.value.name"><AssetView :asset="row"/></RouterLink></div></template>
<style scoped>.final-attached-assets{display:grid;gap:16px;margin:22px 0}.final-attached-assets a{display:block;min-height:44px}.role-cover,.role-portrait{max-width:260px}.role-background{position:relative;width:100%;height:100%}.role-background a{height:100%}</style>
