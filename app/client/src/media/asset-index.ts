import {inject,provide,shallowRef,onMounted,onUnmounted,type InjectionKey,type ShallowRef} from 'vue'
import {useLocal} from '../stores/local'
import type {Document} from '../domain/local-contract'
import type {LocalAsset} from './contract'
type AssetIndex={rows:ShallowRef<Document<LocalAsset>[]>;refresh:()=>Promise<void>}
const key:InjectionKey<AssetIndex>=Symbol('private-asset-index')
/** One decrypt/list per page, shared by all visible paragraph or card attachments. */
export function useAssetIndex():AssetIndex {
  const inherited=inject(key,null);if(inherited)return inherited
  const store=useLocal(),rows=shallowRef<Document<LocalAsset>[]>([]);let disposed=false,epoch=0
  async function refresh(){const vault=store.vault,ticket=++epoch;if(!vault)return;const all=await vault.list<LocalAsset>('asset');if(!disposed&&ticket===epoch&&store.vault===vault&&vault.unlocked)rows.value=all}
  const changed=()=>void refresh().catch(()=>{}),locked=()=>{epoch++;rows.value=[]},index={rows,refresh};provide(key,index)
  onMounted(()=>{changed();window.addEventListener('reader-media-changed',changed);window.addEventListener('reader-local-locked',locked)})
  onUnmounted(()=>{disposed=true;epoch++;rows.value=[];window.removeEventListener('reader-media-changed',changed);window.removeEventListener('reader-local-locked',locked)})
  return index
}
