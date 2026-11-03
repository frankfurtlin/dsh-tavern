import {watch} from 'vue'
import type {Router} from 'vue-router'
import {useApp} from '../stores/app'
import {authorizationHeaders,authGeneration,refreshAuth} from '../api'
import {backendBase} from '../platform/bridge'
import {createTracker} from './vendor/telemetry.js'
import {applySeo,seoMetadata} from './vendor/seo.js'
import {resetLanguage,language} from './locale'
import {clearProductOperations} from './operation-key'
import {activeDuration} from '../analytics/product-events'

export function installProductFoundation(router:Router){
  const store=useApp()
  const tracker=createTracker({productId:'ainovel',appVersion:'analytics-20261026',endpoint:'/api/reader/v1/product/telemetry/track',
    session:{get:()=>({user:store.user})},
    fetchImpl:async(path:string,options:RequestInit)=>{
      const epoch=authGeneration(),base=backendBase(),owner=store.user?.id
      const anonymous=JSON.parse(String(options.body)).expectedUserId===null
      if(anonymous)path=path.replace('/track','/public')
      let response=await fetch(base+path,{...options,credentials:'include',headers:{...options.headers,...(anonymous?{}:authorizationHeaders())}})
      if(response.status===401&&epoch===authGeneration()&&owner===store.user?.id&&await refreshAuth())response=await fetch(base+path,{...options,credentials:'include',headers:{...options.headers,...authorizationHeaders()}})
      if(epoch!==authGeneration()||base!==backendBase()||owner!==store.user?.id)throw new Error('Session changed')
      return response
    }})
  watch(()=>[store.user?.id,router.currentRoute.value.meta.local],([_owner,local])=>{if(!local)tracker.start();else tracker.stop()},{immediate:true})
  let engagedAt=performance.now(),engagedOwner=store.user?.id,engagedPath=router.currentRoute.value.matched.at(-1)?.path||'/other'
  function engagement(){
    const now=performance.now()
    if(engagedOwner&&engagedOwner===store.user?.id&&!router.currentRoute.value.meta.local&&document.visibilityState==='visible'){
      const durationMs=activeDuration(engagedAt,now)
      if(durationMs>=1000)tracker.track('click','page.engaged',{path:engagedPath,durationMs})
    }
    engagedAt=now;engagedOwner=store.user?.id
  }
  setInterval(engagement,30000)
  // Flush elapsed foreground time on hide; reset the clock on return.
  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='hidden'&&engagedOwner===store.user?.id&&engagedOwner&&!router.currentRoute.value.meta.local){
      const durationMs=activeDuration(engagedAt)
      if(durationMs>=1000)tracker.track('click','page.engaged',{path:engagedPath,durationMs})
      void tracker.flush()
    }
    engagedAt=performance.now()
  })
  window.addEventListener('product-behavior',event=>{
    if(!store.user||router.currentRoute.value.meta.local)return
    const {name,metadata,durationMs}=(event as CustomEvent).detail
    tracker.track('click',name,{path:router.currentRoute.value.matched.at(-1)?.path||'/other',metadata,durationMs})
  })
  router.afterEach(to=>{
    engagement();engagedPath=to.matched.at(-1)?.path||to.path
    const publicPage=!!to.meta.public
    applySeo(seoMetadata({origin:import.meta.env.VITE_PUBLIC_ORIGIN||location.origin,path:publicPage?to.path:'/',
      title:typeof to.meta.title==='string'?to.meta.title+' · 故事之间':'故事之间',
      description:'阅读、进入故事世界，并保留每次选择的后果。',language:to.path.startsWith('/product')?language.value:'zh-CN',indexable:publicPage&&!!import.meta.env.VITE_PUBLIC_ORIGIN}))
    if(store.user&&!to.meta.local&&!publicPage){
      const path=to.matched.at(-1)?.path||'/other'
      tracker.track('page_view',path,{path})
    }
    if(!store.user&&['/login','/register','/recover','/help','/data-use'].includes(to.path))tracker.track('page_view','public.visit',{path:to.path})
    requestAnimationFrame(()=>{if(!to.meta.immersive)document.getElementById('main')?.focus({preventScroll:true})})
  })
  window.addEventListener('reader-private-cleared',()=>{
    tracker.stop();tracker.clear();sessionStorage.removeItem('foundation.ainovel.events');clearProductOperations();resetLanguage()
  })
  window.addEventListener('ainovel-api-metric',event=>{
    if(!store.user||router.currentRoute.value.meta.local)return
    const data=(event as CustomEvent).detail
    tracker.track('api',data.method+' '+data.path,{path:data.path,statusCode:data.statusCode,durationMs:data.durationMs,
      metadata:{errorCode:data.errorCode,outcome:data.statusCode>=400?'error':'ok'}})
    if(data.statusCode>=200&&data.statusCode<300){
      const key=data.method+' '+data.path
      const behavior=[[/^POST \/books\/imports$/,'book.import.started'],[/^POST \/books\/imports\/:id\/confirm$/,'book.import.confirmed'],
        [/^PUT \/authoring\/worlds\/:id$/,'author.saved'],[/^POST \/authoring\/worlds\/:id\/submit$/,'publication.submitted'],
        [/^POST \/play-controls\/world-chats$/,'chat.created'],[/^POST \/play-controls\/world-chats\/:id\/send$/,'chat.sent']] as const
      const match=behavior.find(([pattern])=>pattern.test(key))
      if(match)tracker.track('click',match[1],{path:router.currentRoute.value.matched.at(-1)?.path||'/other'})
    }
  })
}
