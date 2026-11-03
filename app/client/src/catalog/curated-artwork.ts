import {withBase} from '../platform/base'
import {computed,onMounted,ref} from 'vue'
import {api} from '../api'
import {useRequestScope} from '../platform/request-scope'

export interface CuratedArtworkItem {key:string;publicationId:string|null;worldId:string|null}
const covers:Record<string,string>={
 'blue-carbuncle':'blue-carbuncle-v1.jpg',
 'original-mystery-v1':'clocktower-promise-v1.jpg',
 'original-fantasy-v1':'snowpass-companions-v1.jpg',
 'douluo-forest-v1':'douluo-forest-v1.svg'
}
export function curatedCover(key:string|null|undefined){return key&&Object.hasOwn(covers,key)?withBase('/assets/covers/'+covers[key]):null}

// Associate artwork by the account's canonical IDs, never by a user-editable title.
export function useCuratedArtwork(seed?:()=>CuratedArtworkItem[]|undefined){
 const loaded=ref<CuratedArtworkItem[]>([]),capture=useRequestScope()
 const items=computed(()=>seed?.()??loaded.value)
 onMounted(async()=>{
  if(seed?.()!==undefined)return
  const scope=capture()
  try{const result=await api<{items:CuratedArtworkItem[]}>('/experience/curated');if(scope.valid())loaded.value=result.items}catch{/* Artwork is optional; the catalog remains usable. */}
 })
 return {
  publications:computed(()=>new Map(items.value.filter(i=>i.publicationId&&curatedCover(i.key)).map(i=>[i.publicationId!,i.key]))),
  worlds:computed(()=>new Map(items.value.filter(i=>i.worldId&&curatedCover(i.key)).map(i=>[i.worldId!,i.key])))
 }
}
