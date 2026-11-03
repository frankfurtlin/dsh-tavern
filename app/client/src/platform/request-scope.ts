import {onUnmounted} from 'vue'
import {useRoute} from 'vue-router'
import {authGeneration} from '../api'
import {accountId} from './bridge'

// A consumer must recheck after its own await, even when api() checked before
// resolving: an account change can run between those two continuations.
export function useRequestScope(context:()=>unknown=()=>null,navigation?:()=>string){
  const route=useRoute();let disposed=false
  onUnmounted(()=>{disposed=true})
  return()=>{
    const currentPath=()=>navigation?navigation():route.fullPath
    const auth=authGeneration(),owner=accountId(),path=currentPath(),key=JSON.stringify(context())
    return {auth,owner,valid:(identity=true)=>!disposed&&path===currentPath()&&key===JSON.stringify(context())&&(!identity||(auth===authGeneration()&&owner===accountId()))}
  }
}
