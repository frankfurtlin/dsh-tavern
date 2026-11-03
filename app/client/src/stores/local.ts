import {defineStore} from 'pinia'
import {shallowRef,ref} from 'vue'
import {LocalVault,listVaults,createVault,unlockVault} from '../repositories/local-vault'
export const useLocal=defineStore('device-private',()=>{
  const vault=shallowRef<LocalVault|null>(null),available=ref<{id:string;createdAt:string}[]>([]),busy=ref(false),error=ref('')
  let generation=0
  async function load(){available.value=await listVaults()}
  function lock(){generation++;vault.value?.lock();vault.value=null;busy.value=false;window.dispatchEvent(new Event('reader-local-locked'))}
  async function adopt(opened:LocalVault){lock();vault.value=opened;await load()}
  async function open(password:string,id?:string){const attempt=++generation;busy.value=true;error.value='';try{const opened=id?await unlockVault(id,password):await createVault(password);if(attempt!==generation){opened.lock();return}vault.value?.lock();vault.value=opened;await load()}catch(e){if(attempt===generation)error.value=(e as Error).message}finally{if(attempt===generation)busy.value=false}}
  return {vault,available,busy,error,load,lock,open,adopt}
})
