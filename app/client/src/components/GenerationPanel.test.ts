import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive} from 'vue'
import GenerationPanel from './GenerationPanel.vue'
import {testAnchor} from '../api/generation-test-fixtures'
import {compileRender,renderer,node,provideSfc,text,flush,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({api:vi.fn(),allPages:vi.fn(),cacheGet:vi.fn(),cacheSet:vi.fn(),watch:vi.fn(),valid:true}))
vi.mock('../api',()=>({api:mock.api,allPages:mock.allPages,newKey:()=> 'explicit-request-key',ApiError:class extends Error{}}));vi.mock('../platform/bridge',()=>({cacheGet:mock.cacheGet,cacheSet:mock.cacheSet}));vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>mock.valid})}));vi.mock('../stores/app',()=>({useApp:()=>reactive({online:true,message:vi.fn()})}));vi.mock('../api/story-run-watch',()=>({watchStoryRun:mock.watch}));vi.mock('../api/task-watch',()=>({taskFinished:(t:any)=>['succeeded','cancelled','failed'].includes(t.state),watchTask:mock.watch}))
compileRender(GenerationPanel,new URL('./GenerationPanel.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>,root:Host
function mount(){root=node('root');app=renderer.createApp(GenerationPanel,{anchor:testAnchor,branch:{id:'branch',name:'原文',parentBranchId:null},operation:'continue_from_anchor'});provideSfc(app);app.mount(root)}
beforeEach(()=>{mock.valid=true;mock.api.mockReset();mock.cacheSet.mockReset().mockResolvedValue(undefined);mock.cacheGet.mockReset().mockResolvedValue(undefined);mock.watch.mockReset().mockReturnValue(()=>{});mock.allPages.mockReset().mockImplementation(async(path)=>path==='/model-profiles'?[{id:'mimo',status:'active',hasKey:true,isDefault:true}]:[])})
afterEach(()=>app?.unmount())
it('preserves a legacy unknown parent request without automatically reposting on mount',async()=>{
  mock.cacheGet.mockResolvedValue({pending:{key:'old-key',request:{chapterCount:3,instruction:'已批准的方向'}}});mount();await flush();expect(mock.api).not.toHaveBeenCalled();expect(text(root)).toContain('输入已保留');expect(mock.cacheSet).not.toHaveBeenCalled()
})
it('observes a known legacy parent by GET only and preserves old task records',async()=>{
  mock.cacheGet.mockResolvedValue({pending:{key:'old-key',runId:'old-run',request:{chapterCount:3,instruction:'原方向'}}});mock.api.mockResolvedValue({id:'old-run',state:'running',createdAt:'2026-10-05',requestedChapters:3,completedChapters:1,scopeAnchor:testAnchor});mount();await flush()
  expect(mock.api).toHaveBeenCalledExactlyOnceWith('/story-runs/old-run');expect(mock.watch).toHaveBeenCalledTimes(1);expect(mock.watch.mock.calls[0][0]).toBe('old-run');expect(mock.cacheSet).not.toHaveBeenCalled()
})
it('does not replay the pre-story-flow POST as a receipt lookup on mount',async()=>{
  mock.cacheGet.mockResolvedValue({request:{operation:'continue_from_anchor'},path:'/tasks',key:'legacy-key'});mount();await flush();expect(mock.api).not.toHaveBeenCalled();expect(text(root)).toContain('不会自动重新提交')
})
