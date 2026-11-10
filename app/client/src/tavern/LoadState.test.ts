import {afterEach,describe,expect,it,vi} from 'vitest'
import {h} from 'vue'
import LoadState from './LoadState.vue'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

compileRender(LoadState,new URL('./LoadState.vue',import.meta.url))

const retry=vi.fn()
let app:ReturnType<typeof renderer.createApp>|undefined
let host:Host

function mount(props:Record<string,unknown>={},slots:Record<string,()=>unknown>={}){
  host=node('root')
  app=renderer.createApp({render:()=>h(LoadState,props,slots)})
  provideSfc(app)
  app.mount(host)
}

afterEach(()=>{app?.unmount();app=undefined;retry.mockReset()})

describe('LoadState',()=>{
  it('shows the loading text while loading',async()=>{
    mount({loading:true,loadingText:'正在读取…'})
    await flush()
    expect(text(host)).toContain('正在读取…')
    expect(descendants(host,item=>item.props.role==='status')).toHaveLength(1)
  })

  it('shows a retryable error and emits retry when clicked',async()=>{
    mount({error:'服务端暂不支持',onRetry:retry})
    await flush()
    expect(text(host)).toContain('加载失败')
    expect(text(host)).toContain('服务端暂不支持')
    const button=descendants(host,item=>item.tag==='button'&&text(item).trim()==='重试')[0]
    button!.props.onClick?.()
    await flush()
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('shows the empty state with a custom action slot',async()=>{
    mount({empty:true,emptyTitle:'还没有角色卡',emptyText:'导入一张开始聊天。'},{action:()=>h('a',{href:'#import'},'导入角色卡')})
    await flush()
    const body=text(host)
    expect(body).toContain('还没有角色卡')
    expect(body).toContain('导入一张开始聊天。')
    expect(body).toContain('导入角色卡')
  })

  it('renders nothing when no state is requested',async()=>{
    mount({})
    await flush()
    expect(text(host)).toBe('')
  })
})
