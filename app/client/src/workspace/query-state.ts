import {computed,shallowRef,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import type {Router,LocationQueryRaw} from 'vue-router'

const updates=new WeakMap<Router,{path:string;query:LocationQueryRaw;hash:string}>()

/** Route-backed filters survive reload and browser back. */
export function queryField(key:string,fallback=''){
 const route=useRoute(),router=useRouter()
 const local=shallowRef(typeof route.query[key]==='string'?String(route.query[key]):fallback)
 watch(()=>route.query[key],value=>{local.value=typeof value==='string'?value:fallback})
 return computed({get:()=>local.value,set:(value:string)=>{
  local.value=value
  const prior=updates.get(router),target=prior?.path===route.path?prior:{path:route.path,query:{...route.query},hash:route.hash}
  if(value&&value!==fallback)target.query[key]=value;else delete target.query[key]
  updates.set(router,target)
  if(prior===target)return
  queueMicrotask(()=>{if(updates.get(router)!==target)return;updates.delete(router);if(router.currentRoute.value.path===target.path)void router.replace(target)})
 }})
}
