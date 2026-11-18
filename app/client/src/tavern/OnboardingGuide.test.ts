import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {h} from 'vue'
import OnboardingGuide from './OnboardingGuide.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

compileRender(OnboardingGuide,new URL('./OnboardingGuide.vue',import.meta.url))

let app:ReturnType<typeof renderer.createApp>|undefined,host:Host
function mount(open=true){
  host=node('root')
  const closed=vi.fn()
  app=renderer.createApp({render:()=>h(OnboardingGuide,{open,onClose:closed})})
  provideSfc(app)
  app.mount(host)
  return closed
}
function aria(label:string){return descendants(host,item=>item.props['aria-label']===label)[0]}
function stepButton(label:string){return descendants(host,item=>item.tag==='button'&&text(item).trim()===label)[0]}

beforeEach(()=>{
  vi.stubGlobal('window',{localStorage:{getItem:vi.fn(()=>null),setItem:vi.fn(),removeItem:vi.fn()},addEventListener:vi.fn(),removeEventListener:vi.fn()})
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})

describe('OnboardingGuide',()=>{
  it('starts on step one and exposes three step dots',async()=>{
    mount();await flush()
    expect(text(host)).toContain('欢迎来到酒馆')
    expect(text(host)).toContain('选一张角色卡')
    const dots=descendants(host,item=>String(item.props.class||'').split(/\s+/).includes('guide-dot'))
    expect(dots).toHaveLength(3)
    expect(String(dots[0]!.props['aria-current'])).toBe('step')
  })

  it('moves forward and back between steps',async()=>{
    mount();await flush()
    stepButton('下一步')!.props.onClick?.()
    await flush()
    expect(text(host)).toContain('回一句话')
    stepButton('上一步')!.props.onClick?.()
    await flush()
    expect(text(host)).toContain('选一张角色卡')
  })

  it('closes and marks the guide seen on the last step',async()=>{
    const closed=mount();await flush()
    stepButton('下一步')!.props.onClick?.()
    await flush()
    stepButton('下一步')!.props.onClick?.()
    await flush()
    expect(text(host)).toContain('需要时让 DSH 帮忙')
    stepButton('开始体验')!.props.onClick?.()
    await flush()
    expect(window.localStorage.setItem).toHaveBeenCalledWith('tavern:guide-seen','1')
    expect(closed).toHaveBeenCalledTimes(1)
  })

  it('skips, marks the guide seen and closes',async()=>{
    const closed=mount();await flush()
    stepButton('跳过')!.props.onClick?.()
    await flush()
    expect(window.localStorage.setItem).toHaveBeenCalledWith('tavern:guide-seen','1')
    expect(closed).toHaveBeenCalledTimes(1)
  })

  it('closes on Escape',async()=>{
    const closed=mount();await flush()
    const handler=vi.mocked(window.addEventListener).mock.calls.find(call=>call[0]==='keydown')?.[1] as unknown as (event:{key:string;preventDefault:()=>void})=>void
    expect(handler).toBeTypeOf('function')
    handler({key:'Escape',preventDefault:vi.fn()})
    await flush()
    expect(closed).toHaveBeenCalledTimes(1)
    expect(window.localStorage.setItem).toHaveBeenCalledWith('tavern:guide-seen','1')
  })

  it('renders nothing when closed',async()=>{
    mount(false);await flush()
    expect(text(host)).not.toContain('欢迎来到酒馆')
  })
})
