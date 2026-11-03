import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import WorldPlayPage from './WorldPlayPage.vue'
import WorldWritePanel from './WorldWritePanel.vue'
import SceneReply from './SceneReply.vue'
import PublicStreamText from '../components/PublicStreamText.vue'
import {defaultRules} from './types'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({store:null as any,api:vi.fn()}))
vi.mock('./store',()=>({useWorld:()=>mock.store}))
vi.mock('./api',()=>({worldApi:mock.api}))
vi.mock('vue-router',()=>({useRoute:()=>({params:{worldId:'world'},query:{line:'line'},fullPath:'/worlds/world/play?line=line'}),useRouter:()=>({push:vi.fn()})}))
vi.mock('../api',()=>({newKey:()=> 'unused',authGeneration:()=>0,ApiError:class extends Error{}}))
vi.mock('../stores/app',()=>({useApp:()=>({online:true})}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>()=>({valid:()=>true})}))
vi.mock('./WorldDetails.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldScene.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldPortraitScene.vue',()=>({default:{render:()=>null}}))
vi.mock('./WorldCorrection.vue',()=>({default:{render:()=>null}}))
vi.mock('../components/StoryImages.vue',()=>({default:{render:()=>null}}))
vi.mock('../components/WorldAppearanceProvider.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('section',slots.default?.())})}))
vi.mock('../components/CharacterAvatar.vue',()=>({default:{render:()=>null}}))
for(const [component,name] of [[WorldPlayPage,'./WorldPlayPage.vue'],[WorldWritePanel,'./WorldWritePanel.vue'],[SceneReply,'./SceneReply.vue'],[PublicStreamText,'../components/PublicStreamText.vue']] as const)compileRender(component,new URL(name,import.meta.url))
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
beforeEach(()=>{
  vi.useFakeTimers();vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{activeElement:null,querySelector:()=>null})
  mock.api.mockReset().mockResolvedValue({id:'turn',chapters:[],warnings:[],status:'writing'})
  mock.store=reactive({world:{id:'world',name:'合成世界',status:'active'},line:{id:'line',name:'合成线',status:'active',headCheckpointId:'formal',head:{id:'formal',text:'',options:[],changes:[],state:{playerId:''}}},state:{playerId:'',entities:[],rules:defaultRules(),timeLabel:'本日',location:''},history:[],drafts:[],input:'下一条未提交稿',running:true,busy:false,recovering:false,connected:true,error:'',task:{id:'task',phase:'writing',state:'running',persistenceState:'pending',result:{chapterIndex:1,chapterCount:3},stream:{text:'你😀',contentId:'content',streamVersion:1}},load:vi.fn(async()=>{}),saveInput:vi.fn(async()=>{}),stopWatching:vi.fn(),act:vi.fn(),cancel:vi.fn()})
})
afterEach(()=>{app?.unmount();app=undefined;vi.useRealTimers();vi.unstubAllGlobals()})
async function mount(component:typeof WorldPlayPage|typeof WorldWritePanel,props={}){root=node('root');app=renderer.createApp(component,props);provideSfc(app);app.component('RouterLink',defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}));app.mount(root);await flush()}
it('actual Play SFC uses batched public stream and preserves composer, focus and formal head',async()=>{
  await mount(WorldPlayPage);const textarea=descendants(root,n=>n.tag==='textarea')[0];Object.defineProperty(document,'activeElement',{value:textarea,configurable:true})
  for(let i=0;i<32;i++)mock.store.task.stream.text+='好';await flush();expect(text(root)).not.toContain('你😀'+'好'.repeat(32))
  await vi.advanceTimersByTimeAsync(16);await flush();expect(text(root)).toContain('你😀'+'好'.repeat(32))
  expect(descendants(root,n=>n.tag==='textarea')[0]).toBe(textarea);expect(textarea.value).toBe('下一条未提交稿');expect(document.activeElement).toBe(textarea);expect(mock.store.line.headCheckpointId).toBe('formal');expect(mock.store.act).not.toHaveBeenCalled();expect(mock.api).not.toHaveBeenCalled()
})
it('actual Write SFC batches a public chapter and resets changed content without accepting it',async()=>{
  await mount(WorldWritePanel,{turnId:'turn'});mock.store.task.stream.text+='新增正文';await flush();await vi.advanceTimersByTimeAsync(16);await flush();expect(text(root)).toContain('你😀新增正文')
  mock.store.task.stream.text+='旧排队';mock.store.task.stream.contentId='chapter-two';mock.store.task.stream.streamVersion=1;mock.store.task.stream.text='第二章新正文';await flush();await vi.advanceTimersByTimeAsync(30);await flush();expect(text(root)).toContain('第二章新正文');expect(text(root)).not.toContain('旧排队');expect(text(root)).not.toContain('你😀新增正文');expect(mock.store.line.headCheckpointId).toBe('formal');expect(mock.api.mock.calls.every(([,options])=>!options?.method)).toBe(true)
})

it('restored world writing shows completed chapters plus latest partial without any accept control',async()=>{
  mock.store.running=false;mock.store.task=null
  mock.api.mockResolvedValue({id:'turn',chapters:[{index:1,title:'灯下',text:'第一章已收到的完整正文',changes:[],options:[]}],warnings:[],status:'historical',draftText:'第二章收到的片段😀',acceptedCount:0})
  await mount(WorldWritePanel,{turnId:'turn'});expect(text(root)).toContain('第一章已收到的完整正文');expect(text(root)).toContain('第二章收到的片段😀');expect(text(root)).toContain('从备份恢复')
  expect(text(root)).not.toContain('保存并继续游玩');expect(descendants(root,n=>n.tag==='select')).toHaveLength(0);expect(mock.store.line.headCheckpointId).toBe('formal');expect(mock.api.mock.calls.every(([,options])=>!options?.method)).toBe(true)
})
