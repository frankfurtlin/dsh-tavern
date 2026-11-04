import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import DiscoveryPage from './DiscoveryPage.vue'
import {api} from '../api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn(),query:{} as Record<string,string>}))
vi.mock('vue-router',()=>({useRoute:()=>({query:harness.query}),useRouter:()=>({push:harness.push})}))
vi.mock('../api',()=>({api:vi.fn(),newKey:()=> 'fixture-key'}))
vi.mock('../stores/app',()=>({useApp:()=>({user:null,message:vi.fn()})}))
vi.mock('../analytics/product-events',()=>({trackProductEvent:vi.fn()}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../catalog/CuratedCover.vue',()=>({default:defineComponent({props:{coverKey:String,title:String},setup:(props)=>()=>h('span',props.title)})}))
vi.mock('./PageHeader.vue',()=>({default:defineComponent({props:{title:String,description:String},setup:(props,{slots})=>()=>h('header',[props.title,slots.default?.()])})}))
compileRender(DiscoveryPage,new URL('./DiscoveryPage.vue',import.meta.url))

const item={key:'rain',title:'雨夜的留声机',genre:'悬疑',description:'一卷来历不明的录音。',available:true,worldId:'world-1',lineId:null as string|null,publicationId:null}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){
  root=node('root');app=renderer.createApp(DiscoveryPage);provideSfc(app)
  app.component('RouterLink',defineComponent({props:{to:[String,Object]},setup:(props,{slots})=>()=>h('a',{to:props.to},slots.default?.())}))
  app.mount(root)
}
function button(label:string){return descendants(root,node=>node.tag==='button'&&text(node).includes(label))[0]}
const curated={items:[item]}
const chat={id:'chat-1',characterName:'林渡',lastMessage:'外面下雨了，你带伞了吗？',updatedAt:'2026-01-01T00:00:00'}
beforeEach(()=>{
  harness.push.mockReset().mockResolvedValue(undefined);harness.query={}
  vi.mocked(api).mockReset().mockImplementation(async (path)=>{
    if(path==='/experience/curated')return curated as any
    if(path.startsWith('/play-controls/world-chats'))return {items:[chat]} as any
    throw new Error('unexpected request '+path)
  })
})
afterEach(()=>{app?.unmount();app=undefined})

it('shows a continue-last entry with character name and summary when an active chat exists',async()=>{
  mount();await flush()
  expect(text(root)).toContain('继续上次');expect(text(root)).toContain('林渡');expect(text(root)).toContain('外面下雨了')
  const link=descendants(root,node=>node.tag==='a'&&node.props.to==='/conversations/world/chat-1')[0]
  expect(link).toBeDefined()
})
it('hides continue-last when there is no active chat',async()=>{
  vi.mocked(api).mockImplementation(async (path)=>{
    if(path==='/experience/curated')return curated as any
    if(path.startsWith('/play-controls/world-chats'))return {items:[]} as any
    throw new Error('unexpected request '+path)
  })
  mount();await flush()
  expect(text(root)).toContain('雨夜的留声机');expect(text(root)).not.toContain('继续上次')
})
it('keeps the explore page usable when the continue-last request fails',async()=>{
  vi.mocked(api).mockImplementation(async (path)=>{
    if(path==='/experience/curated')return curated as any
    throw new Error('合成读取失败')
  })
  mount();await flush()
  expect(text(root)).toContain('雨夜的留声机');expect(text(root)).not.toContain('继续上次')
})
it('opens a new story with auto=1 from the explore start button',async()=>{
  vi.mocked(api).mockImplementation((async (path:string,options?:{method?:string})=>{
    if(path==='/experience/curated')return curated as any
    if(path.startsWith('/play-controls/world-chats'))return {items:[]} as any
    if(path==='/experience/curated/rain/open'&&options?.method==='POST')return {worldId:'world-1'} as any
    throw new Error('unexpected request '+path)
  }) as any)
  mount();await flush()
  await button('开始游玩')!.props.onClick();await flush()
  expect(harness.push).toHaveBeenCalledWith('/worlds/new?world=world-1&auto=1')
})
it('resumes an existing line directly with auto=1 from the continue button',async()=>{
  vi.mocked(api).mockImplementation((async (path:string,options?:{method?:string})=>{
    if(path==='/experience/curated')return {items:[{...item,lineId:'line-1'}]} as any
    if(path.startsWith('/play-controls/world-chats'))return {items:[]} as any
    if(path==='/experience/curated/rain/open'&&options?.method==='POST')return {worldId:'world-1',lineId:'line-1'} as any
    throw new Error('unexpected request '+path)
  }) as any)
  mount();await flush()
  await button('继续游玩')!.props.onClick();await flush()
  expect(harness.push).toHaveBeenCalledWith('/worlds/world-1/play?line=line-1&auto=1')
})
