import {describe,it,expect,vi,afterEach} from 'vitest'
import {createDshBridge,BRIDGE_STATE_DELAY_MS} from './dsh-bridge'
import type {DshBridgeEnv,StoryContext} from './dsh-bridge'

const ORIGIN='http://127.0.0.1:3080'
const CONTEXT:StoryContext={appPath:'/worlds/w1/play',worldTitle:'长夜',lineTitle:'主线',mode:'world-play',characters:[{name:'阿岚',identity:'守夜人',speech:'寡言'}],activeCharacter:'阿岚',recentText:'雨停在门外。'}

function flush(){return new Promise(resolve=>setTimeout(resolve,0))}

function setup(overrides:Partial<DshBridgeEnv>={}){
  const posted:{data:unknown;targetOrigin:string}[]=[]
  const parent={postMessage:(data:unknown,targetOrigin:string)=>{posted.push({data,targetOrigin})}}
  let listener:(()=>void)|undefined
  const env:DshBridgeEnv={
    parent:parent as unknown as DshBridgeEnv['parent'],
    origin:ORIGIN,
    appPath:()=>'/worlds/w1/play',
    hasContext:()=>true,
    readContext:vi.fn(async()=>CONTEXT),
    subscribe(notify){listener=notify;return ()=>{listener=undefined}},
    listen(){return ()=>{}},
    ...overrides,
  }
  const bridge=createDshBridge(env)
  const uninstall=bridge.install()
  // window.parent is the same object the app posts to, so the fake source must
  // be that object; a different object models a sibling/foreign window.
  const source=parent as unknown as MessageEventSource
  const event=(data:unknown,origin=ORIGIN,eventSource:unknown=source)=>({data,origin,source:eventSource} as unknown as MessageEvent)
  return{posted,bridge,uninstall,env,source,event,notify:()=>listener?.()}
}

afterEach(()=>{vi.useRealTimers()})

describe('dsh bridge message validation',()=>{
  it('answers ping with pong and the current app path',()=>{
    const {posted,bridge,event}=setup()
    bridge.handle(event({type:'dsh-tavern:ping'}))
    expect(posted).toHaveLength(1)
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:pong',appPath:'/worlds/w1/play'})
    expect(posted[0]!.targetOrigin).toBe(ORIGIN)
  })

  it('ignores messages from another origin, another source or malformed data',()=>{
    const {posted,bridge,event}=setup()
    bridge.handle(event({type:'dsh-tavern:ping'},'http://evil.example'))
    bridge.handle(event({type:'dsh-tavern:ping'},ORIGIN,{}))
    expect(posted).toHaveLength(0)
    bridge.handle(event(null))
    bridge.handle(event('ping'))
    bridge.handle(event({type:'other'}))
    expect(posted).toHaveLength(0)
  })

  it('does not answer anything before install',()=>{
    const {posted,event}=setup()
    const inert=createDshBridge({...minimalEnv(posted),listen:()=>()=>{}})
    inert.handle(event({type:'dsh-tavern:ping'}))
    expect(posted).toHaveLength(0)
  })

  it('stops answering after uninstall',()=>{
    const {posted,bridge,event,uninstall}=setup()
    uninstall()
    bridge.handle(event({type:'dsh-tavern:ping'}))
    expect(posted).toHaveLength(0)
  })
})

function minimalEnv(posted:{data:unknown}[]):DshBridgeEnv{
  return{
    parent:{postMessage:(data:unknown)=>{posted.push({data})}} as unknown as DshBridgeEnv['parent'],
    origin:ORIGIN,
    appPath:()=>'/',
    hasContext:()=>false,
    readContext:async()=>null,
    subscribe:()=>()=>{},
  }
}

describe('dsh bridge context requests',()=>{
  it('returns the story context for a valid continue request',async()=>{
    const {posted,bridge,event}=setup()
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r1',kind:'continue'}))
    await flush()
    expect(posted).toHaveLength(1)
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:context',id:'r1',ok:true,context:{...CONTEXT,appPath:'/worlds/w1/play'}})
  })

  it('passes the roleplay target through to the context reader',async()=>{
    const {bridge,event,env,posted}=setup()
    bridge.handle(event({type:'dsh-tavern:context-request',id:7,kind:'roleplay',target:' 阿岚 '}))
    await flush()
    expect(env.readContext).toHaveBeenCalledWith('roleplay','阿岚')
    expect(posted).toHaveLength(1)
    expect(posted[0]!.data).toMatchObject({type:'dsh-tavern:context',id:7,ok:true})
  })

  it('reports a failure when the page has no context',async()=>{
    const {posted,bridge,event}=setup({readContext:vi.fn(async()=>null)})
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r2',kind:'continue'}))
    await flush()
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:context',id:'r2',ok:false,error:'当前页面没有可用的故事上下文'})
  })

  it('reports an error when reading the context throws',async()=>{
    const {posted,bridge,event}=setup({readContext:vi.fn(async()=>{throw new Error('读取失败：网络错误')})})
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r3',kind:'roleplay'}))
    await flush()
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:context',id:'r3',ok:false,error:'读取失败：网络错误'})
  })

  it('rejects unknown kinds and missing ids',async()=>{
    const {posted,bridge,event}=setup()
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r4',kind:'summarise'}))
    bridge.handle(event({type:'dsh-tavern:context-request',kind:'continue'}))
    bridge.handle(event({type:'dsh-tavern:context-request',id:null,kind:'continue'}))
    await flush()
    expect(posted).toHaveLength(1)
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:context',id:'r4',ok:false,error:'不支持的故事上下文类型'})
  })
})

describe('dsh bridge state notifications',()=>{
  it('sends one throttled state message for bursts of changes',()=>{
    vi.useFakeTimers()
    const {posted,notify}=setup({stateDelayMs:BRIDGE_STATE_DELAY_MS})
    expect(posted).toHaveLength(0)
    vi.advanceTimersByTime(BRIDGE_STATE_DELAY_MS-1)
    expect(posted).toHaveLength(0)
    vi.advanceTimersByTime(1)
    expect(posted).toHaveLength(1)
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:state',appPath:'/worlds/w1/play',hasContext:true})
    notify();notify();notify()
    vi.advanceTimersByTime(BRIDGE_STATE_DELAY_MS)
    expect(posted).toHaveLength(2)
  })

  it('cancels a pending state message on uninstall',()=>{
    vi.useFakeTimers()
    const {posted,uninstall}=setup({stateDelayMs:BRIDGE_STATE_DELAY_MS})
    uninstall()
    vi.advanceTimersByTime(BRIDGE_STATE_DELAY_MS*2)
    expect(posted).toHaveLength(0)
  })
})

describe('dsh bridge context readiness',()=>{
  it('waits for a freshly opened story page instead of answering "no context"',async()=>{
    let calls=0
    const {posted,bridge,event}=setup({
      expectContext:()=>true,
      readContext:vi.fn(async()=>{
        calls+=1
        return calls<3?null:CONTEXT
      }),
    })
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r-ready',kind:'continue'}))
    await new Promise(resolve=>setTimeout(resolve,900))
    expect(calls).toBeGreaterThanOrEqual(3)
    expect(posted.at(-1)!.data).toMatchObject({type:'dsh-tavern:context',id:'r-ready',ok:true})
  })

  it('answers "no context" immediately when the page cannot provide one',async()=>{
    const {posted,bridge,event}=setup({expectContext:()=>false,readContext:vi.fn(async()=>null)})
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r-none',kind:'continue'}))
    await flush()
    expect(posted).toHaveLength(1)
    expect(posted[0]!.data).toEqual({type:'dsh-tavern:context',id:'r-none',ok:false,error:'当前页面没有可用的故事上下文'})
  })
})

describe('dsh bridge state after a successful context read',()=>{
  it('reports hasContext once the context has been read, even if the store is not ready yet',async()=>{
    vi.useFakeTimers()
    const {posted,bridge,event}=setup({hasContext:()=>false})
    bridge.handle(event({type:'dsh-tavern:context-request',id:'r-state',kind:'continue'}))
    await vi.advanceTimersByTimeAsync(0)
    const answer=posted.find(item=>(item.data as {type?:string}).type==='dsh-tavern:context')
    expect(answer!.data).toMatchObject({id:'r-state',ok:true})
    await vi.advanceTimersByTimeAsync(600)
    const state=posted.map(item=>item.data as {type?:string;hasContext?:boolean}).filter(item=>item.type==='dsh-tavern:state')
    expect(state.at(-1)).toMatchObject({hasContext:true})
  })
})
