<script setup lang="ts">
import {ref,watch,onMounted,onBeforeUnmount} from 'vue'
const props=defineProps<{text:string;contentId:string;streamVersion:number}>(),emit=defineEmits<{rendered:[]}>()
const body=ref<HTMLElement>(),snapshot=ref<string|null>(null)
const segmenter=new Intl.Segmenter(undefined,{granularity:'grapheme'})
let key='',target='',rendered='',nodes:Text[]=[],timer:ReturnType<typeof setTimeout>|undefined,disposed=false
function pieces(text:string){return Array.from(segmenter.segment(text),v=>v.segment)}
function lastPiece(text:string){return pieces(text).at(-1)||''}
function joins(text:string,previous:string){const last=lastPiece(previous);return !!last&&pieces(last+text)[0]!.length>last.length}
function append(delta:string){
  const element=body.value;if(!element)return
  if(typeof element.ownerDocument?.createElement!=='function'){snapshot.value=target;rendered=target;emit('rendered');return}
  for(const piece of pieces(delta)){
    const tail=nodes.at(-1)
    if(tail&&(tail.length+piece.length<=128||joins(piece,tail.data)))tail.appendData(piece)
    else{const span=element.ownerDocument.createElement('span');span.dataset.publicStreamBlock=String(nodes.length);const text=element.ownerDocument.createTextNode(piece);span.append(text);element.append(span);nodes.push(text)}
  }
  rendered=target;emit('rendered')
}
function flush(){timer=undefined;if(!disposed)append(target.slice(rendered.length))}
function update(){
  if(disposed||!body.value)return
  const identity=props.contentId+':'+props.streamVersion
  if(identity!==key||!props.text.startsWith(target)){
    clearTimeout(timer);timer=undefined;key=identity;target=props.text;rendered='';nodes=[]
    if(typeof body.value.ownerDocument?.createElement==='function')body.value.replaceChildren()
    append(target);return
  }
  target=props.text
  if(target!==rendered&&timer===undefined)timer=setTimeout(flush,16)
}
watch(()=>[props.text,props.contentId,props.streamVersion],update,{flush:'sync'})
onMounted(update)
onBeforeUnmount(()=>{disposed=true;clearTimeout(timer);nodes=[]})
</script>
<template><span ref="body" data-public-stream-text><template v-if="snapshot!==null">{{snapshot}}</template></span></template>
