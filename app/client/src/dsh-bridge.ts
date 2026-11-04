/**
 * Story context bridge between the embedded app and the DSH panel (contract §3).
 *
 * The app runs inside an iframe owned by the DSH plugin host, which relays a
 * small postMessage protocol. Every message from the parent is validated on
 * origin AND source before it is answered, and the app only reports state
 * changes when it is actually embedded (never as a top-level tab).
 */
import type {Router} from 'vue-router'

export interface StoryCharacter{name:string;identity:string;speech:string}
export interface StoryContext{
  appPath:string
  worldTitle:string
  lineTitle:string
  mode:string
  characters:StoryCharacter[]
  activeCharacter:string
  recentText:string
}
export type StoryContextKind='continue'|'roleplay'

export interface DshBridgeEnv{
  parent:Pick<Window,'postMessage'>
  origin:string
  appPath():string
  hasContext():boolean
  /** Whether the current page is a story page that will be able to answer once loaded. */
  expectContext?():boolean
  readContext(kind:StoryContextKind,target?:string):Promise<StoryContext|null>
  subscribe(listener:()=>void):()=>void
  /** Test seam; defaults to window.addEventListener('message', ...). */
  listen?(handler:(event:MessageEvent)=>void):()=>void
  stateDelayMs?:number
}

export interface DshBridge{
  install():()=>void
  handle(event:MessageEvent):void
}

export const BRIDGE_STATE_DELAY_MS=400
/**
 * When a story page has just been opened (or restored) its world/reader data is
 * still loading, while the panel may ask for the context immediately. Keep
 * retrying for this long instead of answering "no context" too early.
 */
export const CONTEXT_READY_TIMEOUT_MS=6000
const CONTEXT_READY_POLL_MS=250

function listenToWindow(handler:(event:MessageEvent)=>void):()=>void{
  window.addEventListener('message',handler)
  return ()=>window.removeEventListener('message',handler)
}

export function createDshBridge(env:DshBridgeEnv):DshBridge{
  let installed=false
  let stateTimer:ReturnType<typeof setTimeout>|undefined
  let removeListener:(()=>void)|undefined
  let unsubscribe:(()=>void)|undefined
  // A successful context read proves the story page is ready even if the route
  // store has not published yet; keep the panel's state in sync with it.
  let contextReady=false

  function send(payload:unknown){env.parent.postMessage(payload,env.origin)}

  function sendState(){
    if(env.expectContext&&!env.expectContext())contextReady=false
    send({type:'dsh-tavern:state',appPath:env.appPath(),hasContext:contextReady||env.hasContext()})
  }

  function scheduleState(){
    if(!installed||stateTimer!==undefined)return
    stateTimer=setTimeout(()=>{stateTimer=undefined;if(installed)sendState()},env.stateDelayMs??BRIDGE_STATE_DELAY_MS)
  }

  /** Reads the context now, retrying briefly while a freshly opened story page is still loading. */
  async function readContextWithRetry(kind:StoryContextKind,target?:string):Promise<StoryContext|null>{
    const deadline=Date.now()+(env.expectContext?.()?CONTEXT_READY_TIMEOUT_MS:0)
    for(;;){
      const context=await env.readContext(kind,target)
      if(context)return context
      if(!installed||Date.now()>=deadline||!env.expectContext?.())return null
      await new Promise<void>(resolve=>setTimeout(resolve,CONTEXT_READY_POLL_MS))
    }
  }

  function answerContext(id:string|number,data:Record<string,unknown>){
    const kind=data.kind
    if(kind!=='continue'&&kind!=='roleplay'){
      send({type:'dsh-tavern:context',id,ok:false,error:'不支持的故事上下文类型'})
      return
    }
    const target=typeof data.target==='string'&&data.target.trim()?data.target.trim():undefined
    void readContextWithRetry(kind,target).then(context=>{
      if(!installed)return
      if(!context){
        send({type:'dsh-tavern:context',id,ok:false,error:'当前页面没有可用的故事上下文'})
        return
      }
      send({type:'dsh-tavern:context',id,ok:true,context:{...context,appPath:env.appPath()}})
      contextReady=true
      scheduleState()
    }).catch((error:unknown)=>{
      if(!installed)return
      const message=error instanceof Error&&error.message?error.message:'读取故事上下文失败'
      send({type:'dsh-tavern:context',id,ok:false,error:message})
    })
  }

  function handle(event:MessageEvent){
    if(!installed)return
    if(event.origin!==env.origin)return
    if(event.source!==(env.parent as unknown as MessageEventSource))return
    const data=event.data as Record<string,unknown>|null|undefined
    if(!data||typeof data!=='object')return
    const type=data.type
    if(type==='dsh-tavern:ping'){
      send({type:'dsh-tavern:pong',appPath:env.appPath()})
      return
    }
    if(type==='dsh-tavern:context-request'){
      const id=data.id
      if(typeof id!=='string'&&typeof id!=='number')return
      answerContext(id,data)
    }
  }

  function uninstall(){
    if(!installed)return
    installed=false
    if(stateTimer!==undefined){clearTimeout(stateTimer);stateTimer=undefined}
    removeListener?.();removeListener=undefined
    unsubscribe?.();unsubscribe=undefined
  }

  function install(){
    if(installed)return ()=>{}
    installed=true
    removeListener=env.listen?env.listen(handle):listenToWindow(handle)
    unsubscribe=env.subscribe(scheduleState)
    scheduleState()
    return uninstall
  }

  return{install,handle}
}

export interface InstallDshBridgeOptions{router:Router}

/**
 * Wire the real browser environment. Returns undefined (and stays inert) when
 * the app is not embedded, so the same bundle keeps working as a normal page.
 */
export function installDshBridge(options:InstallDshBridgeOptions):(()=>void)|undefined{
  if(typeof window==='undefined'||window.parent===window)return undefined
  const router=options.router
  const origin=window.location.origin
  const cleanups:(()=>void)[]=[]
  let contextModule:typeof import('./dsh-context')|undefined
  let notifyState:(()=>void)|undefined
  let disposed=false

  const loadContextModule=()=>{
    if(contextModule)return Promise.resolve(contextModule)
    return import('./dsh-context').then(module=>{
      contextModule=module
      notifyState?.()
      return module
    })
  }

  const bridge=createDshBridge({
    parent:window.parent,
    origin,
    appPath:()=>router.currentRoute.value.path,
    hasContext:()=>contextModule?contextModule.hasStoryContext(router):false,
    expectContext:()=>/^\/worlds\/[^/]+\/(play|explore|read)$/.test(router.currentRoute.value.path)
      ||/^\/books\/[^/]+\/read$/.test(router.currentRoute.value.path),
    readContext:(kind,target)=>loadContextModule().then(module=>module.readStoryContext(router,kind,target)),
    subscribe:(listener)=>{
      notifyState=listener
      const offRouter=router.afterEach(()=>listener())
      // World state (line head, history, loaded characters) changes without a
      // route change; observe the store once it is available.
      void import('./world/store').then(({useWorld})=>{
        if(disposed)return
        const offStore=useWorld().$subscribe(()=>listener())
        cleanups.push(offStore)
        listener()
      }).catch(()=>{})
      return ()=>{notifyState=undefined;offRouter()}
    },
  })

  const uninstallBridge=bridge.install()
  void loadContextModule().catch(()=>{})
  return ()=>{
    disposed=true
    uninstallBridge()
    for(const cleanup of cleanups)cleanup()
    cleanups.length=0
  }
}
