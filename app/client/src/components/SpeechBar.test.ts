import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {ref,computed,markRaw,defineComponent,h} from 'vue'
import SpeechBar from './SpeechBar.vue'
import {defaultSpeechPreferences,type SpeechSegment,type SpeechState} from '../platform/speech-types'
import type {SpeechController} from '../platform/speech-controller'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,type Host} from '../test/host-renderer'
vi.mock('../platform/speech-controller',()=>({speechReason:(reason:string)=>reason==='process-restored'?'已恢复本机位置，点击播放继续。':''}))
vi.mock('./Sheet.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('section',slots.default?.())})}))
compileRender(SpeechBar,new URL('./SpeechBar.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>,root:Host,c:SpeechController,locate:ReturnType<typeof vi.fn>
const paragraph:SpeechSegment={id:'paragraph',chapterId:'chapter',chapterTitle:'第一章',text:'合成正文',anchor:{bookId:'book',branchId:'line',revisionId:'rev',chapterId:'chapter',blockId:'paragraph',start:4,end:4,offsetUnit:'utf16',quoteHash:'hash'}}
function mount(){root=node('root');app=renderer.createApp(SpeechBar,{controller:c,onLocate:locate});provideSfc(app);app.mount(root)}
const button=(name:string)=>descendants(root,n=>n.tag==='button'&&(n.props['aria-label']===name||text(n).includes(name)))[0]
beforeEach(()=>{vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});vi.stubGlobal('document',{activeElement:null});locate=vi.fn();const state=ref<SpeechState>({scopeKey:'scope',status:'paused',queueLoaded:false,count:3,currentId:'paragraph',chapterTitle:'第一章',reason:'process-restored'});c=markRaw({state,preferences:ref({...defaultSpeechPreferences}),voices:ref([{id:'zh',name:'中文系统',language:'zh-CN',offline:true}]),available:ref(true),loading:ref(false),busy:ref(false),error:ref(''),instructions:ref(''),lastCompleted:ref(null),playing:computed(()=>['starting','playing'].includes(state.value.status)),play:vi.fn(),pause:vi.fn(),skip:vi.fn(),configure:vi.fn(),retryVoices:vi.fn(),openSettings:vi.fn(),stop:vi.fn()} as unknown as SpeechController)})
afterEach(()=>{app?.unmount();vi.unstubAllGlobals()})
it('restored paused shows position without autoplay; actual play/start changes only the explicit button',async()=>{
  mount();await flush();expect(text(root)).toContain('已恢复本机位置');expect(c.play).not.toHaveBeenCalled();expect(button('听书上一段').props.disabled).toBe(true);button('播放系统听书').props.onClick();expect(c.play).toHaveBeenCalledOnce();c.state.value={...c.state.value,status:'playing',queueLoaded:true,index:1};await flush();button('暂停听书').props.onClick();expect(c.pause).toHaveBeenCalledOnce();button('听书上一段').props.onClick();expect(c.skip).toHaveBeenCalledExactlyOnceWith(-1)
})
it('the actual menu exposes every timer and speed and uses explicit configure without a cloud success claim',async()=>{
  mount();await flush();button('听书更多设置').props.onClick();await flush();expect(text(root)).toContain('保存在这台设备');const selects=descendants(root,n=>n.tag==='select');expect(selects).toHaveLength(3);expect(descendants(selects[1],n=>n.tag==='option').map(n=>n.props.value)).toEqual([.75,1,1.25,1.5,1.75,2]);expect(descendants(selects[2],n=>n.tag==='option').map(n=>n.props.value)).toEqual([0,5,15,30,60]);selects[1].props.onChange({target:{value:'1.5'}});selects[2].props.onChange({target:{value:'30'}});expect(c.configure).toHaveBeenNthCalledWith(1,{...defaultSpeechPreferences,rate:1.5});expect(c.configure).toHaveBeenNthCalledWith(2,{...defaultSpeechPreferences,sleepMinutes:30});expect(text(root)).not.toContain('云同步')
})
it('manual current position cannot enable completed location; a real lastCompleted locator uses the canonical anchor',async()=>{
  mount();await flush();button('听书更多设置').props.onClick();await flush();expect(button('回到已听段落').props.disabled).toBe(true);c.lastCompleted.value=paragraph;await flush();button('回到已听段落').props.onClick();expect(locate).toHaveBeenCalledExactlyOnceWith(paragraph)
})
it('missing preferred voice displays the actual fallback and offers system settings and retry, without a paid option',async()=>{
  c.state.value={...c.state.value,voiceFallback:true,resolvedVoiceId:'zh'};c.preferences.value.voiceId='absent';mount();await flush();button('听书更多设置').props.onClick();await flush();expect(text(root)).toContain('当前使用 中文系统');expect(text(root)).toContain('此设备缺少偏好声音');button('重新读取系统声音').props.onClick();button('打开系统语音设置').props.onClick();expect(c.retryVoices).toHaveBeenCalledOnce();expect(c.openSettings).toHaveBeenCalledOnce();expect(text(root)).not.toContain('在线配音')
})

it('unknown cloud completion offers only original-request confirmation; conflict preferences require explicit refresh and save',async()=>{
 const cloud=markRaw({supported:ref(true),status:ref('unknown'),preferenceStatus:ref('conflict'),busy:ref(false),preferenceBusy:ref(false),message:ref('保存结果尚未确认'),preferenceMessage:ref('本机偏好保留'),pending:ref({requestId:'original'}),completion:ref({paragraphId:'paragraph'}),recover:vi.fn(),refreshPosition:vi.fn(),saveLatest:vi.fn(),refreshPreferences:vi.fn(),savePreferences:vi.fn()})
 ;(c as any).cloud=cloud;mount();await flush();button('听书更多设置').props.onClick();await flush();button('确认原听书请求').props.onClick();expect(cloud.recover).toHaveBeenCalledOnce();expect(cloud.saveLatest).not.toHaveBeenCalled();expect(button('保存本机已听位置')).toBeUndefined()
 button('载入最新信息').props.onClick();expect(cloud.refreshPreferences).toHaveBeenCalledOnce();expect(cloud.savePreferences).not.toHaveBeenCalled();cloud.preferenceStatus.value='local';await flush();button('保存本机偏好').props.onClick();expect(cloud.savePreferences).toHaveBeenCalledOnce()
})
