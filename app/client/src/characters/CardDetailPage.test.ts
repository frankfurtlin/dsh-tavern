import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {defineComponent,h,onUnmounted,reactive} from 'vue'
import CardDetailPage from './CardDetailPage.vue'
import * as cardApi from './api'
import {compileRender,descendants,flush,node,provideSfc,renderer,text,type Host} from '../test/host-renderer'

const harness=vi.hoisted(()=>({push:vi.fn(),route:null as any}))
vi.mock('vue-router',()=>({
  useRoute:()=>harness.route,
  useRouter:()=>({push:harness.push}),
  RouterLink:defineComponent({setup:(_,{slots})=>()=>h('a',slots.default?.())}),
}))
vi.mock('./api',()=>({
  getCard:vi.fn(),updateCard:vi.fn(),listLore:vi.fn(),createLore:vi.fn(),updateLore:vi.fn(),deleteLore:vi.fn(),
  listPresets:vi.fn(),createPreset:vi.fn(),startCardChat:vi.fn(),cardAvatarBlob:vi.fn(),contextPreview:vi.fn(),
  listMemories:vi.fn(),createMemory:vi.fn(),updateMemory:vi.fn(),deleteMemory:vi.fn(),extractMemories:vi.fn(),setCompanion:vi.fn(),
}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>({valid:()=>!disposed})}}))
vi.mock('../workspace/PageHeader.vue',()=>({default:defineComponent({props:{title:String,description:String,eyebrow:String},setup:(p,{slots})=>()=>h('header',[p.title,slots.default?.()])})}))
vi.mock('lucide-vue-next',()=>{
  const stub=defineComponent({setup:()=>()=>null})
  return{ArrowLeft:stub,Brain:stub,BookOpen:stub,Eye:stub,Heart:stub,MessageSquareText:stub,Pencil:stub,Plus:stub,Replace:stub,Save:stub,Sparkles:stub,Trash2:stub}
})
compileRender(CardDetailPage,new URL('./CardDetailPage.vue',import.meta.url))

const card={id:'c1',name:'林渡',description:'旧书摊主，说话总带着包袱。',personality:'机智',scenario:'北平的书摊',firstMessage:'来了？',systemPrompt:'',postHistoryInstructions:'',tags:['相声'],creator:'佚名',source:'png' as const,hasAvatar:false,presetId:null}
const lore={id:'l1',name:'雨夜',keys:['雨','屋檐'],content:'屋檐下躲雨时，他把外套披在你肩上。',enabled:true,order:100,source:'import' as const}
const preset={id:'p1',name:'细腻描写',systemSuffix:'多写环境与心理。',enabled:true}
const memory={id:'m1',kind:'fact' as const,title:'读者养猫',content:'读者养了一只叫团子的猫。',salience:60,enabled:true,source:'manual' as const,updatedAt:'2026-11-04T02:00:00Z'}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host

function fire(target:Host,key:string,payload?:unknown){
  const handler=target.props[key]
  if(Array.isArray(handler)){for(const fn of handler)fn(payload)}
  else handler?.(payload)
}
function button(label:string){return descendants(root,item=>item.tag==='button'&&text(item).includes(label))[0]}
function forms(){return descendants(root,item=>item.tag==='form')}
function mount(){root=node('root');app=renderer.createApp(CardDetailPage);provideSfc(app);app.mount(root)}

beforeEach(()=>{
  // Vue's native v-model directive reads document.activeElement on updates.
  vi.stubGlobal('document',{activeElement:null})
  vi.mocked(cardApi.getCard).mockReset().mockResolvedValue({...card})
  vi.mocked(cardApi.listLore).mockReset().mockResolvedValue([{...lore}])
  vi.mocked(cardApi.listPresets).mockReset().mockResolvedValue([{...preset}])
  vi.mocked(cardApi.updateCard).mockReset().mockImplementation(async(_id,patch)=>({...card,...patch}))
  vi.mocked(cardApi.createLore).mockReset()
  vi.mocked(cardApi.updateLore).mockReset()
  vi.mocked(cardApi.deleteLore).mockReset().mockResolvedValue(undefined)
  vi.mocked(cardApi.createPreset).mockReset()
  vi.mocked(cardApi.startCardChat).mockReset().mockResolvedValue({sessionId:'s1',cardId:'c1'})
  vi.mocked(cardApi.cardAvatarBlob).mockReset().mockResolvedValue(null)
  vi.mocked(cardApi.contextPreview).mockReset().mockResolvedValue({loreMatched:[{id:'l1',name:'雨夜',keys:['雨']}],preset:{id:'p1',name:'细腻描写'},systemSuffix:'多写环境与心理。'})
  vi.mocked(cardApi.listMemories).mockReset().mockResolvedValue([])
  vi.mocked(cardApi.createMemory).mockReset()
  vi.mocked(cardApi.updateMemory).mockReset()
  vi.mocked(cardApi.deleteMemory).mockReset().mockResolvedValue(undefined)
  vi.mocked(cardApi.extractMemories).mockReset()
  vi.mocked(cardApi.setCompanion).mockReset().mockImplementation(async(_id,companion)=>({...card,companion}))
  harness.push.mockReset().mockResolvedValue(undefined)
  harness.route=reactive({params:{id:'c1'}})
})
afterEach(()=>{app?.unmount();app=undefined;vi.restoreAllMocks();vi.unstubAllGlobals()})

it('loads the card settings, world-book entries and presets',async()=>{
  mount();await flush()
  expect(cardApi.getCard).toHaveBeenCalledWith('c1')
  expect(cardApi.listLore).toHaveBeenCalledWith('c1')
  expect(text(root)).toContain('林渡')
  expect(text(root)).toContain('旧书摊主')
  expect(text(root)).toContain('相声')
  expect(text(root)).toContain('雨夜')
  expect(text(root)).toContain('关键词：雨、屋檐')
  expect(text(root)).toContain('细腻描写')
  const options=descendants(root,item=>item.tag==='option').map(item=>text(item))
  expect(options).toContain('细腻描写')
})

it('saves the edited settings with a PATCH and keeps the new values',async()=>{
  mount();await flush()
  const form=forms()[0]
  const nameInput=descendants(form,item=>item.tag==='input')[0]
  fire(nameInput,'onUpdate:modelValue','林渡·改')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.updateCard).toHaveBeenCalledWith('c1',expect.objectContaining({name:'林渡·改',description:'旧书摊主，说话总带着包袱。',tags:['相声']}))
  expect(text(root)).toContain('角色设定已保存')
  expect(text(root)).toContain('林渡·改')
})

it('creates a manual world-book entry and shows the refreshed list',async()=>{
  const created={id:'l2',name:'雨夜的约定',keys:['雨'],content:'雨声盖住了街上的脚步。',enabled:true,order:100,source:'manual' as const}
  vi.mocked(cardApi.listLore).mockResolvedValueOnce([{...lore}]).mockResolvedValueOnce([{...lore},created])
  vi.mocked(cardApi.createLore).mockResolvedValueOnce(created)
  mount();await flush()
  button('新增条目').props.onClick()
  await flush()
  const loreForm=forms()[1]
  const inputs=descendants(loreForm,item=>item.tag==='input')
  const content=descendants(loreForm,item=>item.tag==='textarea')[0]
  fire(inputs[0],'onUpdate:modelValue','雨夜的约定')
  fire(inputs[1],'onUpdate:modelValue','雨，屋檐')
  fire(content,'onUpdate:modelValue','雨声盖住了街上的脚步。')
  await flush()
  fire(loreForm,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.createLore).toHaveBeenCalledWith('c1',expect.objectContaining({name:'雨夜的约定',keys:['雨','屋檐'],content:'雨声盖住了街上的脚步。',enabled:true,order:100}))
  expect(text(root)).toContain('雨声盖住了街上的脚步。')
  expect(text(root)).toContain('世界书条目已保存')
})

it('toggles and deletes a world-book entry through the documented routes',async()=>{
  vi.mocked(cardApi.updateLore).mockResolvedValueOnce({...lore,enabled:false})
  mount();await flush()
  button('停用').props.onClick()
  await flush()
  expect(cardApi.updateLore).toHaveBeenCalledWith('c1','l1',{enabled:false})
  fire(descendants(root,item=>item.props['aria-label']==='删除世界书条目')[0],'onClick')
  await flush()
  button('确认删除').props.onClick()
  await flush()
  expect(cardApi.deleteLore).toHaveBeenCalledWith('c1','l1')
  expect(text(root)).toContain('世界书条目已删除')
})

it('binds an existing preset to the card when the select changes',async()=>{
  mount();await flush()
  const select=descendants(root,item=>item.tag==='select')[0]
  fire(select,'onUpdate:modelValue','p1')
  fire(select,'onChange')
  await flush()
  expect(cardApi.updateCard).toHaveBeenCalledWith('c1',{presetId:'p1'})
  expect(text(root)).toContain('已绑定该预设')
})

it('creates a preset and binds it in one flow',async()=>{
  const created={id:'p2',name:'冷静叙述',systemSuffix:'保持克制的语气。',enabled:true}
  vi.mocked(cardApi.createPreset).mockResolvedValueOnce(created)
  mount();await flush()
  button('新建预设').props.onClick()
  await flush()
  const presetForm=forms()[1]
  const inputs=descendants(presetForm,item=>item.tag==='input')
  const suffix=descendants(presetForm,item=>item.tag==='textarea')[0]
  fire(inputs[0],'onUpdate:modelValue','冷静叙述')
  fire(suffix,'onUpdate:modelValue','保持克制的语气。')
  await flush()
  fire(presetForm,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.createPreset).toHaveBeenCalledWith({name:'冷静叙述',systemSuffix:'保持克制的语气。'})
  expect(cardApi.updateCard).toHaveBeenLastCalledWith('c1',{presetId:'p2'})
  expect(text(root)).toContain('预设已创建并绑定')
})

it('previews which lore and system suffix the next message would inject',async()=>{
  mount();await flush()
  const messageInput=descendants(root,item=>item.tag==='input'&&item.props.placeholder==='例如：外面下雨了，我们在屋里待一会儿吧')[0]
  fire(messageInput,'onUpdate:modelValue','外面下雨了')
  await flush()
  button('预览').props.onClick()
  await flush()
  expect(cardApi.contextPreview).toHaveBeenCalledWith('c1','外面下雨了')
  expect(text(root)).toContain('命中的世界书：雨夜')
  expect(text(root)).toContain('预设：细腻描写')
  expect(text(root)).toContain('多写环境与心理。')
})

it('starts a card chat from the detail page',async()=>{
  mount();await flush()
  button('开聊').props.onClick()
  await flush()
  expect(cardApi.startCardChat).toHaveBeenCalledWith('c1')
  expect(harness.push).toHaveBeenCalledWith('/conversations/world/s1')
})

it('shows the load error with a retry action',async()=>{
  vi.mocked(cardApi.getCard).mockRejectedValueOnce(new Error('角色卡不存在'))
  mount();await flush()
  expect(text(root)).toContain('角色卡不存在')
  expect(text(root)).toContain('重新加载')
})

it('renders the long-term memory list with kind, source and salience',async()=>{
  vi.mocked(cardApi.listMemories).mockResolvedValue([{...memory}])
  mount();await flush()
  expect(cardApi.listMemories).toHaveBeenCalledWith('c1')
  expect(text(root)).toContain('读者养猫')
  expect(text(root)).toContain('读者养了一只叫团子的猫。')
  expect(text(root)).toContain('事实')
  expect(text(root)).toContain('手记')
  expect(text(root)).toContain('权重 60')
})

it('creates a manual memory through the editor form',async()=>{
  const created={...memory,id:'m2',title:'常去茶馆',content:'读者常在周末去城西的茶馆。'}
  vi.mocked(cardApi.listMemories).mockResolvedValueOnce([]).mockResolvedValueOnce([created])
  vi.mocked(cardApi.createMemory).mockResolvedValueOnce(created)
  mount();await flush()
  button('新增记忆').props.onClick()
  await flush()
  const form=forms()[1]
  const title=descendants(form,item=>item.tag==='input'&&item.props.placeholder==='例如：读者养猫')[0]
  const content=descendants(form,item=>item.tag==='textarea')[0]
  fire(title,'onUpdate:modelValue','常去茶馆')
  fire(content,'onUpdate:modelValue','读者常在周末去城西的茶馆。')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.createMemory).toHaveBeenCalledWith('c1',expect.objectContaining({kind:'fact',title:'常去茶馆',content:'读者常在周末去城西的茶馆。',salience:50,enabled:true}))
  expect(text(root)).toContain('常去茶馆')
  expect(text(root)).toContain('记忆已保存')
})

it('edits an existing memory and keeps the new content',async()=>{
  vi.mocked(cardApi.listMemories).mockResolvedValueOnce([{...memory}]).mockResolvedValueOnce([{...memory,content:'团子是只橘猫。'}])
  vi.mocked(cardApi.updateMemory).mockResolvedValueOnce({...memory,content:'团子是只橘猫。'})
  mount();await flush()
  fire(descendants(root,item=>item.props['aria-label']==='编辑记忆')[0],'onClick')
  await flush()
  const form=forms()[1]
  const content=descendants(form,item=>item.tag==='textarea')[0]
  fire(content,'onUpdate:modelValue','团子是只橘猫。')
  await flush()
  fire(form,'onSubmit',{preventDefault(){}})
  await flush()
  expect(cardApi.updateMemory).toHaveBeenCalledWith('c1','m1',expect.objectContaining({title:'读者养猫',content:'团子是只橘猫。',salience:60}))
  expect(text(root)).toContain('团子是只橘猫。')
})

it('toggles and deletes a memory with two-step confirmation',async()=>{
  vi.mocked(cardApi.listLore).mockResolvedValue([])
  vi.mocked(cardApi.listMemories).mockResolvedValue([{...memory}])
  vi.mocked(cardApi.updateMemory).mockResolvedValueOnce({...memory,enabled:false})
  mount();await flush()
  button('停用').props.onClick()
  await flush()
  expect(cardApi.updateMemory).toHaveBeenCalledWith('c1','m1',{enabled:false})
  fire(descendants(root,item=>item.props['aria-label']==='删除记忆')[0],'onClick')
  await flush()
  button('确认删除').props.onClick()
  await flush()
  expect(cardApi.deleteMemory).toHaveBeenCalledWith('c1','m1')
  expect(text(root)).toContain('记忆已删除')
})

it('extracts new memories and reports how many were added',async()=>{
  const added={...memory,id:'m3',source:'auto' as const}
  vi.mocked(cardApi.listMemories).mockResolvedValueOnce([]).mockResolvedValueOnce([added])
  vi.mocked(cardApi.extractMemories).mockResolvedValueOnce({added:[added],skipped:1})
  mount();await flush()
  button('立即整理').props.onClick()
  await flush()
  expect(cardApi.extractMemories).toHaveBeenCalledWith('c1')
  expect(text(root)).toContain('新增 1 条记忆')
  expect(text(root)).toContain('读者养猫')
})

it('reports when extraction finds nothing new',async()=>{
  vi.mocked(cardApi.extractMemories).mockResolvedValueOnce({added:[],skipped:2})
  mount();await flush()
  button('立即整理').props.onClick()
  await flush()
  expect(text(root)).toContain('暂无新记忆')
})

it('surfaces the backend message when extraction fails',async()=>{
  vi.mocked(cardApi.extractMemories).mockRejectedValueOnce(new Error('记忆整理失败，请稍后重试'))
  mount();await flush()
  button('立即整理').props.onClick()
  await flush()
  expect(text(root)).toContain('记忆整理失败，请稍后重试')
})

it('marks the card as a long-term companion through the toggle',async()=>{
  vi.mocked(cardApi.getCard).mockResolvedValue({...card,companion:false})
  vi.mocked(cardApi.setCompanion).mockResolvedValueOnce({...card,companion:true})
  mount();await flush()
  const toggle=descendants(root,item=>item.tag==='input'&&item.props.type==='checkbox')[0]
  fire(toggle,'onChange')
  await flush()
  expect(cardApi.setCompanion).toHaveBeenCalledWith('c1',true)
  expect(text(root)).toContain('已设为长期伙伴')
})

it('removes the companion flag again',async()=>{
  vi.mocked(cardApi.getCard).mockResolvedValue({...card,companion:true})
  vi.mocked(cardApi.setCompanion).mockResolvedValueOnce({...card,companion:false})
  mount();await flush()
  fire(descendants(root,item=>item.tag==='input'&&item.props.type==='checkbox')[0],'onChange')
  await flush()
  expect(cardApi.setCompanion).toHaveBeenCalledWith('c1',false)
  expect(text(root)).toContain('已取消长期伙伴')
})
