import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {defineComponent,h,onUnmounted} from 'vue'
import WorkEntry from './WorkEntry.vue'
import {api} from '../api'
import {rememberContext} from '../api/stories'
import type {StoryDetail} from '../api/stories'
import {worldApi} from '../world/api'
import {worldMutation} from '../world/mutation-intent'
import {compileRender,renderer,node,descendants,text,flush,deferred,provideSfc,type Host} from '../test/host-renderer'

const f=vi.hoisted(()=>({epoch:0,push:vi.fn(),read:vi.fn(),cache:new Map<string,any>(),app:{online:true,worldEnabled:true,user:{username:'测试者'}}}))
vi.mock('vue-router',()=>({useRouter:()=>({push:f.push}),useRoute:()=>({fullPath:'/stories/fixture'})}))
vi.mock('../api',()=>({api:vi.fn(),authGeneration:()=>f.epoch}))
vi.mock('../api/stories',async original=>({...await original<typeof import('../api/stories')>(),rememberContext:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>f.app}))
vi.mock('../platform/bridge',()=>({cacheGet:async(key:string)=>f.cache.get(key),cacheSet:async(key:string,value:unknown)=>{f.cache.set(key,value)}}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const epoch=f.epoch;return{valid:()=>!disposed&&f.epoch===epoch}}}}))
vi.mock('../world/api',()=>({worldApi:vi.fn()}))
vi.mock('../world/mutation-intent',()=>({pendingWorldMutation:async(slot:string)=>f.cache.get('intent:'+slot),clearWorldMutation:async(slot:string)=>{f.cache.delete('intent:'+slot)},worldMutation:vi.fn()}))
vi.mock('./StoryImages.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('div',slots.fallback?.())})}))
vi.mock('./WorldLookLibrary.vue',()=>({default:defineComponent({setup:()=>()=>h('div',{'aria-label':'角色形象库'})})}))
compileRender(WorkEntry,new URL('./WorkEntry.vue',import.meta.url))
const bid='a'.repeat(32),wid='b'.repeat(32),lid='c'.repeat(32),cid='d'.repeat(32),branch='e'.repeat(32)
const anchor={bookId:bid,branchId:branch,revisionId:'f'.repeat(32),chapterId:'1'.repeat(32),blockId:'2'.repeat(32),start:0,end:4,offsetUnit:'utf16' as const,quoteHash:'hash'}
const playContext={storyId:'world-'+wid,mode:'play' as const,perspective:'actor' as const,entryOrigin:'worlds' as const,worldId:wid,worldLineId:lid,checkpointId:cid,characterId:'hero'}
const blank=()=>({version:0,available:false,context:null,updatedAt:null})
function story(kind:'book'|'world'='world'):StoryDetail{return {id:kind+'-'+(kind==='book'?bid:wid),kind,title:'雪关同行',status:'active',version:1,canCreateWorld:true,resume:{read:kind==='book'?{...blank(),available:true,context:{storyId:'book-'+bid,mode:'read',perspective:'reader',entryOrigin:'library',bookId:bid,readerBranchId:branch,anchor}}:blank(),play:blank(),chat:blank()},worlds:kind==='world'?[{id:wid,name:'第一条故事线',status:'active',version:1,activeLineId:lid,prepareTaskId:null,sourceRemoved:false,updatedAt:'',resumeContext:playContext}]:[]}}
const info=(status='active')=>({id:wid,name:'雪关同行',status,description:'雪封山道，炉火明灭。',timeLabel:'清晨',locationName:'雪关客舍',prepareTaskId:null,characters:[{id:'npc',name:'陆遥',description:'一位旅人',traits:'沉稳',available:true}]})
const savedLine=()=>({id:lid,worldId:wid,head:{title:'两枚铜牌',text:'他望向门外的雪。',state:{playerId:'hero',location:'inn',entities:[{id:'hero',name:'闻青',description:'途经雪关的药师'},{id:'inn',name:'雪关客舍'}]}}})
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
const state=()=>((app as any)._instance.setupState)
const mount=(value=story())=>{root=node('root');app=renderer.createApp(WorkEntry,{story:value,imageScope:null,timeline:null,onRead:f.read});provideSfc(app);app.component('RouterLink',defineComponent({props:{to:[String,Object]},setup:(p,{slots})=>()=>h('a',{'data-to':p.to},slots.default?.())}));app.mount(root)}
const button=(label:string)=>descendants(root,n=>n.tag==='button'&&text(n).includes(label))[0]!
beforeEach(()=>{
  vi.stubGlobal('document',{activeElement:null})
  vi.clearAllMocks();f.epoch=0;f.cache.clear();f.app.online=true;f.app.worldEnabled=true
  vi.mocked(api).mockResolvedValue({title:'第一章 渡口',blocks:[{id:anchor.blockId,text:'已读文字后续未读文字'},{id:'unread',text:'不应出现的未来剧情'}]} as any)
  vi.mocked(worldApi).mockImplementation(async(path)=>path.startsWith('/sources')?{items:[{id:bid,lines:[{id:branch,name:'原文',chapters:[{title:'第一章 渡口',anchor}]}]}]} as any:path.includes('/lines/')?savedLine() as any:info() as any)
  vi.mocked(worldMutation).mockImplementation(async(slot,path,body,valid,onIntent)=>{const intent={key:'test-key',path,body};f.cache.set('intent:'+slot,intent);onIntent?.(intent);if(!valid())throw new Error('scope changed');return path==='/worlds'?{worldId:wid,taskId:null}:{lineId:lid}})
})
afterEach(()=>{app?.unmount();app=undefined;vi.useRealTimers();vi.unstubAllGlobals()})

it('opens a novel without creating tasks, limits the preview to the selected anchor, and keeps original reading available',async()=>{
  mount(story('book'));await flush()
  expect(state().selected).toBe('original');expect(state().preview).toBe('已读文字');expect(text(root)).not.toContain('不应出现的未来剧情')
  expect(worldMutation).not.toHaveBeenCalled();button('开始阅读').props.onClick();await flush();expect(f.read).toHaveBeenCalledOnce();expect(f.push).not.toHaveBeenCalled()
})
it('keeps original reading usable when world sources cannot load',async()=>{
  vi.mocked(worldApi).mockRejectedValueOnce(new Error('世界暂时离线'));mount(story('book'));await flush()
  expect(button('开始阅读').props.disabled).toBe(false);button('开始阅读').props.onClick();await flush();expect(f.read).toHaveBeenCalledOnce()
})
it('defaults a script opening to its actual investigator while preserving a manually chosen identity',async()=>{
  vi.mocked(worldApi).mockResolvedValue({...info(),recommendedCharacterId:'doctor',characters:[{id:'npc',name:'侦探',available:true},{id:'doctor',name:'医生',available:true}]} as any)
  const value=story();value.worlds=[];mount(value);await flush()
  expect(state().character).toBe('doctor')
  await state().chooseCharacter('npc');await state().select('new:'+wid)
  expect(state().character).toBe('npc')
})
it('allows the exploration selector when world capabilities are unavailable and still uses real source openings',async()=>{
  f.app.worldEnabled=false;mount(story('book'));await flush()
  expect(button('探索模式').props.disabled).toBe(false);button('探索模式').props.onClick();await flush()
  expect(state().mode).toBe('explore');expect(state().current.kind).toBe('new')
  expect(text(root)).toContain('旅程设置');expect(text(root)).toContain('探索记录');expect(worldMutation).not.toHaveBeenCalled()
})
it('keeps exploration browsable offline without enabling a generation request',async()=>{
  f.app.online=false;mount();await flush();button('探索模式').props.onClick();await flush()
  expect(state().mode).toBe('explore');expect(state().canStart).toBe(false);expect(worldMutation).not.toHaveBeenCalled()
})
it('does not start original reading when exploration has no available source',async()=>{
  vi.mocked(worldApi).mockRejectedValueOnce(new Error('世界暂时离线'));mount(story('book'));await flush()
  button('探索模式').props.onClick();await flush();await state().start()
  expect(state().error).toContain('暂时没有可探索的开场');expect(f.read).not.toHaveBeenCalled();expect(worldMutation).not.toHaveBeenCalled()
})
it('selects an existing work character as a new opening rather than resuming an unrelated save',async()=>{
  mount();await flush();button('探索模式').props.onClick();await flush();await state().chooseCharacter('npc')
  expect(state().current.kind).toBe('new');expect(state().character).toBe('npc');expect(state().shownIdentityTab).toBe('cast')
  await state().start();expect(vi.mocked(worldMutation).mock.calls[0]?.[2]).toMatchObject({characterId:'npc'})
  expect(rememberContext).not.toHaveBeenCalled();expect(f.push).toHaveBeenCalledWith(expect.objectContaining({path:'/worlds/'+wid+'/explore'}))
})
it('preserves reading access to an archived world at its saved checkpoint',async()=>{
  const value=story();value.status='archived';value.worlds=[];value.resume.read={...blank(),available:true,context:{...playContext,mode:'read',perspective:'reader'}}
  vi.mocked(worldApi).mockImplementation(async path=>path.includes('/checkpoints/')?{title:'保留的故事',text:'这一段已经保存'} as any:info('archived') as any)
  mount(value);await flush();expect(state().preview).toBe('这一段已经保存');expect(button('开始阅读').props.disabled).toBe(false);button('开始阅读').props.onClick();await flush();expect(f.read).toHaveBeenCalledOnce();expect(worldMutation).not.toHaveBeenCalled()
})
it('switches from a book to a real opening for exploration and preserves the character draft across mode changes',async()=>{
  mount(story('book'));await flush();state().customName='闻青';state().opening='先向守关人问路';state().setMode('explore');await flush()
  expect(state().current.kind).toBe('new');expect(state().mode).toBe('explore');expect(state().canStart).toBe(true)
  state().setMode('read');await flush();expect(state().opening).toBe('先向守关人问路');expect(state().customName).toBe('闻青');expect(worldMutation).not.toHaveBeenCalled()
})
it('does not retain an unavailable identity when switching from a saved world to a novel opening',async()=>{
  const value=story('book');value.worlds=story().worlds;mount(value);await flush();await state().select('saved:'+wid);state().character='npc';await state().select('source:'+branch+':'+anchor.blockId)
  expect(state().character).toBe('custom');expect(state().canStart).toBe(true)
})
it.each([['read','play'],['explore','explore']])('continues a saved world in %s mode without creating another line',async(mode,path)=>{
  mount();await flush();state().setMode(mode);await state().start();await flush()
  expect(rememberContext).toHaveBeenCalledWith(playContext,expect.any(Function));expect(f.push).toHaveBeenCalledWith({path:'/worlds/'+wid+'/'+path,query:{line:lid,story:'world-'+wid}});expect(worldMutation).not.toHaveBeenCalled()
})
it('sends character settings and opening to the actual begin mutation, then enters the selected 360 mode',async()=>{
  mount();await flush();await state().select('new:'+wid);state().customName='闻青';state().gender='女';state().age='26';state().identity='途经雪关的药师';state().skills=['医术','观察'];state().opening='和陆遥一起去山脚';state().setMode('explore');await state().start();await flush()
  expect(worldMutation).toHaveBeenCalledWith('work-entry:begin:'+wid,'/worlds/'+wid+'/begin',expect.objectContaining({customName:'闻青',customIdentity:'途经雪关的药师；性别：女；年龄：26；擅长：医术、观察',opening:'和陆遥一起去山脚',characterId:null}),expect.any(Function),expect.any(Function))
  expect(f.push).toHaveBeenCalledWith(expect.objectContaining({path:'/worlds/'+wid+'/explore'}))
})
it('waits for book world preparation before beginning and retains the selected source anchor',async()=>{
  vi.useFakeTimers();let prepared=false
  vi.mocked(worldApi).mockImplementation(async path=>path.startsWith('/sources')?{items:[{id:bid,lines:[{id:branch,name:'原文',chapters:[{title:'第一章',anchor}]}]}]} as any:path.startsWith('/tasks/')?{state:prepared?'succeeded':'running',persistenceState:'saved',result:{}} as any:info(prepared?'active':'preparing') as any)
  vi.mocked(worldMutation).mockImplementation(async(_slot,path)=>path==='/worlds'?{worldId:wid,taskId:'prepare-task'}:{lineId:lid})
  mount(story('book'));await flush();state().setMode('explore');await flush();const run=state().start();await flush()
  expect(worldMutation).toHaveBeenCalledTimes(1);expect(vi.mocked(worldMutation).mock.calls[0]?.[2]).toMatchObject({anchor,fullBookReference:false,maxCalls:8})
  prepared=true;await vi.advanceTimersByTimeAsync(1200);await run;expect(worldMutation).toHaveBeenCalledTimes(2);expect(f.push).toHaveBeenCalledWith(expect.objectContaining({path:'/worlds/'+wid+'/explore'}))
})
it('ignores a delayed preview after the user picks another opening',async()=>{
  const late=deferred<any>();vi.mocked(worldApi).mockImplementation(async path=>path.includes('/lines/')?late.promise:info() as any)
  mount();await flush();const selectNew=state().select('new:'+wid);await selectNew;late.resolve(savedLine());await flush()
  expect(state().current.kind).toBe('new');expect(state().preview).toBe(info().description);expect(state().line).toBeNull()
})
it('does not begin or navigate after the account changes during creation',async()=>{
  const late=deferred<any>();vi.mocked(worldMutation).mockReturnValueOnce(late.promise)
  mount(story('book'));await flush();state().setMode('explore');await flush();const run=state().start();await flush();f.epoch++;late.resolve({worldId:wid,taskId:null});await run
  expect(worldMutation).toHaveBeenCalledTimes(1);expect(f.push).not.toHaveBeenCalled()
})
it('retries an uncertain begin using the original settings even if a stale handler changes the draft',async()=>{
  mount();await flush();await state().select('new:'+wid);state().customName='闻青';state().opening='在客舍等待'
  vi.mocked(worldMutation).mockImplementationOnce(async(_slot,_path,body,_valid,onIntent)=>{onIntent?.({key:'unknown-key',path:'/begin',body});throw new Error('响应尚未确认')})
  await state().start();expect(state().locked).toBe(true);state().customName='不能替换';state().opening='另一个操作';await state().start()
  expect(vi.mocked(worldMutation).mock.calls[1]?.[2]).toEqual(vi.mocked(worldMutation).mock.calls[0]?.[2]);expect(f.push).toHaveBeenCalledOnce()
})
it('restores a submitted opening after reload without resubmitting or replacing its original create payload',async()=>{
  const value=story('book'),selected='source:'+branch+':'+anchor.blockId,body={anchor:{...anchor,end:2},name:'原来的开场',rules:{},fullBookReference:false,maxCalls:8}
  f.cache.set('intent:work-entry:create:'+value.id,{path:'/worlds',key:'original',body})
  f.cache.set('work-entry:draft:'+value.id,{mode:'explore',selected,character:'custom',customName:'闻青',gender:'',age:'',identity:'药师',skills:[],opening:'到门口看看'})
  mount(value);await flush();expect(worldMutation).not.toHaveBeenCalled();expect(state().locked).toBe(true);await state().start()
  expect(vi.mocked(worldMutation).mock.calls[0]?.[2]).toEqual(body);expect(f.push).toHaveBeenCalledOnce()
})
