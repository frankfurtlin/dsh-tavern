const PRIVATE_FIELDS = /password|token|secret|api.?key|authorization|content|prompt|email|phone|card.?code|stack|message/i
export function sanitizeMetadata(value, depth = 0) {
  if (depth > 4 || value == null) return null
  if (Array.isArray(value)) return value.slice(0,20).map(item => sanitizeMetadata(item,depth+1))
  if (typeof value === 'object') return Object.fromEntries(Object.entries(value).slice(0,40)
    .filter(([key]) => !PRIVATE_FIELDS.test(key)).map(([key,item]) => [key,sanitizeMetadata(item,depth+1)]))
  if (typeof value === 'string') return value.replace(/Bearer\s+[\w.-]+|\bsk-[\w-]+/gi,'[redacted]').slice(0,256)
  return ['number','boolean'].includes(typeof value) ? value : null
}
export function createTracker({ productId, endpoint='/api/v1/telemetry/track', session,
  fetchImpl=globalThis.fetch, storage=globalThis.sessionStorage, persistentStorage=globalThis.localStorage,
  appVersion='0.1.0', intervalMs=5000, maxQueue=200 } = {}) {
  if (!productId || !/^[A-Za-z0-9_-]{1,64}$/.test(productId)) throw new Error('A stable productId is required')
  const id = () => globalThis.crypto.randomUUID(), key = `foundation.${productId}.events`
  const remembered = (store,name) => { try { let value=store?.getItem(name); if (!value) {value=id();store?.setItem(name,value)} return value } catch { return id() } }
  const anonId=remembered(persistentStorage,`foundation.${productId}.anon`), sessionId=remembered(storage,`foundation.${productId}.session`)
  let queue=[],sending=false,timer,dispose=[]
  try { queue=JSON.parse(storage?.getItem(key)||'[]').slice(-maxQueue) } catch {}
  const owner=()=>{const user=session?.get()?.user;return user?.userId ?? user?.user_id ?? user?.id ?? null}
  const save=()=>{try{storage?.setItem(key,JSON.stringify(queue))}catch{}}
  function track(type,name,fields={}) {
    queue.push({eventId:id(),eventVersion:2,appVersion,ownerUserId:owner(),type,name,
      path:String(fields.path??globalThis.location?.pathname??'/').split(/[?#]/)[0],target:fields.target,
      metadata:sanitizeMetadata(fields.metadata),durationMs:fields.durationMs,statusCode:fields.statusCode,
      anonId,sessionId,deviceType:/Mobile|Android|iPhone|iPad/i.test(globalThis.navigator?.userAgent||'')?'mobile':'desktop',ts:Date.now()})
    queue=queue.slice(-maxQueue);save();if(queue.length>=20)void flush()
  }
  async function flush() {
    if(sending||!queue.length)return
    sending=true
    try {
      // Events from a previous authenticated account remain queued until that account returns.
      const batchOwner=queue.find(event=>event.ownerUserId===null || event.ownerUserId===owner())?.ownerUserId
      if(batchOwner===undefined)return
      const batch=queue.filter(event=>event.ownerUserId===batchOwner).slice(0,50)
      const response=await fetchImpl(endpoint,{method:'POST',keepalive:true,headers:{'Content-Type':'application/json',
        ...(session?.get()?.accessToken?{Authorization:`Bearer ${session.get().accessToken}`}:{})},
        body:JSON.stringify({eventVersion:2,expectedUserId:batchOwner,events:batch})})
      const result=await response.json()
      if(response.ok&&result.code===200&&result.data?.persisted&&result.data.accepted===batch.length){
        const accepted=new Set(batch.map(event=>event.eventId));queue=queue.filter(event=>!accepted.has(event.eventId));save()
      }
    }catch{}finally{sending=false}
  }
  function start() {
    if(timer)return
    timer=setInterval(()=>void flush(),intervalMs)
    if(globalThis.document){
      const click=event=>{const element=event.target?.closest?.('[data-track]');if(element)track('click',element.dataset.track,{target:element.dataset.track})}
      const hide=()=>{if(document.visibilityState==='hidden')void flush()}
      document.addEventListener('click',click,true);document.addEventListener('visibilitychange',hide)
      dispose.push(()=>document.removeEventListener('click',click,true),()=>document.removeEventListener('visibilitychange',hide))
    }
    if(globalThis.window){
      const error=()=>track('error','frontend.runtime_error',{metadata:{errorCode:'RUNTIME_ERROR'}})
      window.addEventListener('error',error);window.addEventListener('unhandledrejection',error)
      dispose.push(()=>window.removeEventListener('error',error),()=>window.removeEventListener('unhandledrejection',error))
    }
  }
  const api=event=>track('api',`${event.method} ${event.path}`,{durationMs:event.durationMs,statusCode:event.status,
    metadata:{outcome:event.outcome,errorCode:event.code}})
  const attachRouter=router=>router.afterEach(to=>track('page_view',to.name?String(to.name):to.path,{path:to.matched?.at(-1)?.path||to.path}))
  return {track,flush,start,api,attachRouter,pending:()=>queue.length,clear:()=>{queue=[];save()},stop:()=>{clearInterval(timer);timer=undefined;dispose.forEach(fn=>fn());dispose=[]}}
}
