<script setup lang="ts">
import {ref,computed,watch,onUnmounted} from 'vue'
import {offlineAsset} from '../offline/storage'
import type {Block,Bookmark} from '../api/types'
import {blockHighlights,paintHighlights,type HighlightRange} from '../api/reading-annotations'
import {authorizationHeaders,authGeneration} from '../api'
import {backendBase,cacheGet,cacheSet} from '../platform/bridge'
const props=defineProps<{block:Block;bookId:string;highlights?:Bookmark[]}>(),markup=ref(''),ranges=ref<HighlightRange[]>([])
const segments=computed(()=>{const values:{text:string;highlight:boolean}[]=[];let offset=0;for(const range of ranges.value){if(range.start>offset)values.push({text:props.block.text.slice(offset,range.start),highlight:false});values.push({text:props.block.text.slice(range.start,range.end),highlight:true});offset=range.end}if(offset<props.block.text.length)values.push({text:props.block.text.slice(offset),highlight:false});return values})
let generation=0,disposed=false
async function prepare(){
  const version=++generation,epoch=authGeneration(),bookId=props.bookId,html=props.block.html
  const valid=()=>!disposed&&version===generation&&epoch===authGeneration()
  markup.value=html||'';ranges.value=[]
  const highlights=await blockHighlights(props.block,bookId,props.highlights||[])
  if(!valid())return
  ranges.value=highlights;if(!html)return
  const document=new DOMParser().parseFromString(html,'text/html')
  for(const img of document.querySelectorAll('img[data-asset]')){
    const asset=img.getAttribute('data-asset')||'';let data=await cacheGet<string>('asset:'+bookId+':'+asset)||await offlineAsset(bookId,asset)
    if(!valid())return
    if(!data)try{const response=await fetch(backendBase()+`/api/reader/v1/books/${bookId}/assets/${asset}`,{headers:authorizationHeaders(),credentials:'include'});if(!valid())return;if(!response.ok)continue;const blob=await response.blob();if(!valid())return;data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(blob)});if(!valid())return;await cacheSet('asset:'+bookId+':'+asset,data)}catch{}
    if(!valid())return
    if(data){img.setAttribute('src',data);img.setAttribute('loading','lazy')}
  }
  if(valid()){try{paintHighlights(document.body,props.block.text,highlights)}catch{/* Keep readable original markup if its text mapping cannot be verified. */}markup.value=document.body.innerHTML}
}
watch(()=>[props.block,props.bookId,props.highlights],prepare,{immediate:true,deep:true});onUnmounted(()=>{disposed=true;generation++})
</script>
<template><div v-if="block.html" data-reader-content v-html="markup"></div><span v-else data-reader-content><template v-for="(segment,index) in segments" :key="index"><mark v-if="segment.highlight" class="reading-highlight" data-reader-highlight="true">{{segment.text}}</mark><template v-else>{{segment.text}}</template></template></span></template>
