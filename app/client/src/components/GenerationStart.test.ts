import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {ref,markRaw} from 'vue'
import GenerationStart from './GenerationStart.vue'
import type {GenerationController} from '../api/generation-controller'
import {testAnchor} from '../api/generation-test-fixtures'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,deferred,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({allPages:vi.fn(),api:vi.fn(),store:{online:true,settings:{chapterLength:'long'},message:vi.fn()},valid:true}))
vi.mock('../api',()=>({api:mock.api,allPages:mock.allPages,ApiError:class extends Error{}}));vi.mock('../stores/app',()=>({useApp:()=>mock.store}));vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>mock.valid})}))
compileRender(GenerationStart,new URL('./GenerationStart.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>,root:Host,c:GenerationController,started:ReturnType<typeof vi.fn>
function mount(){root=node('root');app=renderer.createApp(GenerationStart,{anchor:testAnchor,branch:{id:'branch',name:'原文',parentBranchId:null},operation:'continue_from_anchor',controller:c,onStarted:started});provideSfc(app);app.mount(root)}
beforeEach(()=>{mock.valid=true;mock.allPages.mockReset().mockResolvedValue([{id:'default-mimo',status:'active',hasKey:true,isDefault:true,modelId:'mimo-v2.6-flash',baseUrl:'https://opencode.ai/zen/go/v1'}]);mock.api.mockReset();started=vi.fn();c=markRaw({intent:ref(null),state:ref(null),unconfirmed:ref([]),busy:ref(false),active:ref(false),error:ref(''),submit:vi.fn(),refresh:vi.fn(),newDraft:vi.fn(),open:vi.fn()} as unknown as GenerationController)})
afterEach(()=>app?.unmount())
it('does not create on mount and closes only after a confirmed explicit submit',async()=>{
  const submitted=deferred<boolean>();vi.mocked(c.submit).mockReturnValue(submitted.promise);mount();await flush();expect(c.submit).not.toHaveBeenCalled();expect(mock.api).not.toHaveBeenCalled()
  descendants(root,n=>n.tag==='button'&&text(n).includes('开始写作'))[0].props.onClick();await flush();expect(started).not.toHaveBeenCalled();expect(c.submit).toHaveBeenCalledWith(expect.objectContaining({chapterLength:'long',chapterCount:1,profileId:'default-mimo',originAnchor:testAnchor}))
  submitted.resolve(true);await flush();expect(started).toHaveBeenCalledTimes(1)
})
it('does not close another account or a different route when an old create completes late',async()=>{
  const submitted=deferred<boolean>();vi.mocked(c.submit).mockReturnValue(submitted.promise);mount();await flush();expect(text(root)).toContain('开始写作');descendants(root,n=>n.tag==='button'&&text(n).includes('开始写作'))[0].props.onClick();mock.valid=false;submitted.resolve(true);await flush();expect(started).not.toHaveBeenCalled()
})
