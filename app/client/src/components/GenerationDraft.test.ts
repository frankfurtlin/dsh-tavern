import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {ref,computed,markRaw} from 'vue'
import GenerationDraft from './GenerationDraft.vue'
import type {GenerationController} from '../api/generation-controller'
import {snapshot,chapter} from '../api/generation-test-fixtures'
import {acceptablePrefix,generationFinished} from '../api/generation-stream'
import {compileRender,renderer,node,provideSfc,descendants,text,flush,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({store:{online:true,message:vi.fn()},copy:vi.fn()}));vi.mock('../stores/app',()=>({useApp:()=>mock.store}))
compileRender(GenerationDraft,new URL('./GenerationDraft.vue',import.meta.url))
let app:ReturnType<typeof renderer.createApp>,root:Host,c:GenerationController
function mount(){root=node('root');app=renderer.createApp(GenerationDraft,{controller:c});provideSfc(app);app.mount(root)}
function button(label:string){return descendants(root,n=>n.tag==='button'&&text(n).includes(label))[0]}
function saved(index:number){return chapter(index,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'h-'+index})}
beforeEach(()=>{
  mock.store.online=true;mock.copy.mockReset().mockResolvedValue(undefined);vi.stubGlobal('navigator',{clipboard:{writeText:mock.copy}});vi.stubGlobal('window',{scrollTo:vi.fn()})
  const state=ref(snapshot()),preview=ref(null)
  c=markRaw({state,preview,cached:ref(false),receivedRaw:ref([]),rewriteIntent:ref(null),lastAccepted:ref(null),versions:ref([]),intent:ref(null),busy:ref(false),error:ref(''),conflict:ref(false),active:computed(()=>!generationFinished(state.value)),prefix:computed(()=>acceptablePrefix(state.value)),accept:vi.fn(),cancel:vi.fn(),save:vi.fn(),refresh:vi.fn(),rewrite:vi.fn(),loadVersions:vi.fn(),selectVersion:vi.fn()} as unknown as GenerationController)
})
afterEach(()=>{app?.unmount();vi.unstubAllGlobals()})
it('labels earlier raw fragments without calling them the current prose source',async()=>{
  const raw='{"body":"甲😀"}',rawPresentation={schema:'prose-presentation-v1' as const,chapterIndex:1,selected:'original' as const,attempts:[{kind:'original' as const,raw,rawHash:'a'.repeat(64),finishReason:null}],presentation:{text:'甲😀',title:'第1章',format:'json' as const,thoughtOmitted:false,pending:false}}
  c.receivedRaw.value=[{generationId:'batch',candidateVersion:1,contentId:'content-1',streamVersion:1,snapshotVersion:1,rawPresentation}];c.state.value=snapshot({chapters:[chapter(1,{text:'甲😀新增正文'})]});mount();await flush()
  expect(text(root)).toContain('较早的原返回片段');expect(text(root)).not.toContain('当前正文来源');button('复制原返回').props.onClick();await flush();expect(mock.copy).toHaveBeenLastCalledWith(raw)
  c.preview.value=snapshot({candidateVersion:2});await flush();expect(descendants(root,n=>n.props.class==='generation-raw')).toHaveLength(0)
})
it('labels a cached draft and keeps copying usable while mutations await a fresh GET',async()=>{
  c.state.value=snapshot({state:'cancelled',persistenceState:'recoverable',chapters:[saved(1)],completedChapters:1});c.cached.value=true;mount();await flush()
  expect(text(root)).toContain('本机草稿 · 等待联网确认')
  expect(button('保存前 1 章').props.disabled).toBe(true);expect(button('重试保存正文').props.disabled).toBe(true);expect(button('载入最新信息').props.disabled).toBe(false)
  expect(descendants(root,n=>n.props['aria-label']==='停止写作并保留正文')[0].props.disabled).toBe(true)
  c.state.value={...c.state.value!,persistenceState:'saved'};await flush();expect(button('重写未保存').props.disabled).toBe(true)
  button('复制这一章').props.onClick();await flush();expect(mock.copy).toHaveBeenLastCalledWith('甲😀');expect(c.accept).not.toHaveBeenCalled();expect(c.rewrite).not.toHaveBeenCalled()
})
it('keeps original response details apart from reading bytes and copies the chosen representation',async()=>{
  const raw='<think>合成私密分析</think>{"body":"甲😀"}'
  c.state.value=snapshot({chapters:[chapter(1,{rawPresentation:{schema:'prose-presentation-v1',chapterIndex:1,selected:'original',attempts:[{kind:'original',raw,rawHash:'a'.repeat(64),finishReason:null}],presentation:{text:'甲😀',title:'第1章',format:'json',thoughtOmitted:true,pending:false}}})]})
  mount();await flush()
  const prose=descendants(root,n=>n.props.class==='generation-prose')[0]
  expect(text(prose)).toBe('甲😀');expect(text(prose)).not.toContain('合成私密分析')
  const details=descendants(root,n=>n.tag==='details'&&n.props.class==='generation-raw')[0]
  expect(text(details)).toContain(raw);expect(details.props.open).not.toBe(true)
  button('复制原返回').props.onClick();await flush();expect(mock.copy).toHaveBeenLastCalledWith(raw)
  button('复制这一章').props.onClick();await flush();expect(mock.copy).toHaveBeenLastCalledWith('甲😀')
  expect(c.accept).not.toHaveBeenCalled();expect(c.rewrite).not.toHaveBeenCalled()
})
it('renders imported partial history without replay/stop/save/rewrite controls while keeping copying usable',async()=>{
  c.state.value={...snapshot({state:'running',persistenceState:'recoverable',eventCursor:0}),importedHistory:true,readOnly:true};mount();await flush()
  expect(text(root)).toContain('导入历史');expect(text(root)).not.toContain('正在恢复保存');expect(text(root)).toContain('甲😀')
  expect(descendants(root,n=>n.props['aria-label']==='停止写作并保留正文')).toHaveLength(0);expect(button('重试保存正文')).toBeUndefined();expect(button('重写未保存')).toBeUndefined()
  button('复制这一章').props.onClick();await flush();expect(mock.copy).toHaveBeenCalledExactlyOnceWith('甲😀')
})
it('keeps an explicitly acceptable imported saved prefix available without pretending its unknown task finished',async()=>{
  c.state.value={...snapshot({state:'historical-unknown',phase:'historical-unknown',providerState:'unknown',persistenceState:'unknown',usageState:'unknown',eventCursor:0,chapters:[saved(1)],completedChapters:1}),importedHistory:true,readOnly:true};mount();await flush()
  expect(text(root)).toContain('状态未记录');expect(text(root)).not.toContain('已保存 1 /');expect(button('加入前 1 章')).toBeDefined()
  button('加入前 1 章').props.onClick();await flush();expect(c.accept).toHaveBeenCalledExactlyOnceWith(1);expect(c.save).not.toHaveBeenCalled();expect(c.rewrite).not.toHaveBeenCalled()
})
it('offers only a saved complete continuous prefix while the next chapter streams',async()=>{
  c.state.value=snapshot({completedChapters:2,chapters:[saved(1),saved(2),chapter(3)]});mount();await flush()
  expect(button('保存前 2 章')).toBeDefined();button('保存前 2 章').props.onClick();await flush();expect(c.accept).toHaveBeenCalledExactlyOnceWith(2);expect(c.cancel).not.toHaveBeenCalled()
  expect(descendants(root,n=>n.tag==='option').map(n=>n.props.value)).toEqual([1,2]);expect(text(root)).toContain('正在写第 3 / 3 章')
})
it('offers all completed chapters by default as a streamed batch grows',async()=>{
  c.state.value=snapshot({completedChapters:1,chapters:[saved(1),chapter(2),chapter(3)]});mount();await flush()
  c.state.value=snapshot({state:'succeeded',completedChapters:3,chapters:[saved(1),saved(2),saved(3)]});await flush()
  expect(button('保存全部完整章节')).toBeDefined()
  button('保存全部完整章节').props.onClick();await flush();expect(c.accept).toHaveBeenCalledExactlyOnceWith(3)
})
it('keeps a deliberately selected prefix while later chapters finish and moves beyond an accepted prefix',async()=>{
  c.state.value=snapshot({completedChapters:2,chapters:[saved(1),saved(2),chapter(3)]});mount();await flush()
  const select=descendants(root,n=>n.props['aria-label']==='连续保存至章节')[0]
  select.props['onUpdate:modelValue'](1);select.props.onChange();await flush()
  c.state.value=snapshot({state:'succeeded',completedChapters:3,chapters:[saved(1),saved(2),saved(3)]});await flush()
  button('保存前 1 章').props.onClick();await flush();expect(c.accept).toHaveBeenCalledExactlyOnceWith(1)
  c.state.value={...c.state.value!,acceptedPrefix:1};await flush()
  expect(button('保存全部完整章节')).toBeDefined()
  expect(descendants(root,n=>n.tag==='option').map(n=>n.props.value)).toEqual([2,3])
})
it('keeps partial prose readable/copyable and stop/save controls during recoverable failure',async()=>{
  c.state.value=snapshot({state:'failed',persistenceState:'recoverable'});mount();await flush()
  expect(text(root)).toContain('正在恢复保存');expect(text(root)).toContain('甲😀');expect(button('保存前')).toBeUndefined()
  button('复制这一章').props.onClick();await flush();expect(mock.copy).toHaveBeenCalledExactlyOnceWith('甲😀')
  descendants(root,n=>n.props['aria-label']==='停止写作并保留正文')[0].props.onClick();button('重试保存正文').props.onClick();expect(c.cancel).toHaveBeenCalledTimes(1);expect(c.save).toHaveBeenCalledTimes(1)
})
it('appends new text spans without replacing the previously selectable node',async()=>{
  mount();await flush();const first=descendants(root,n=>n.tag==='span'&&n.props['data-delta-index']===0)[0]
  c.state.value={...c.state.value!,eventCursor:6,chapters:[chapter(1,{text:'甲😀乙'})]};await new Promise(resolve=>setTimeout(resolve,30));await flush()
  const spans=descendants(root,n=>n.tag==='span'&&n.props['data-delta-index']!==undefined);expect(spans).toHaveLength(2);expect(spans[0]).toBe(first);expect(text(first)).toBe('甲😀');expect(text(spans[1])).toBe('乙')
})
it('upward input immediately freezes follow, then latest scrolls only the draft body',async()=>{
  mount();await flush();const body=descendants(root,n=>n.props['aria-label']==='生成正文')[0] as Host&{scrollTop:number;scrollHeight:number;clientHeight:number};body.scrollTop=100;body.scrollHeight=900;body.clientHeight=300
  body.props.onWheelPassive({deltaY:-1});c.state.value={...c.state.value!,eventCursor:6,chapters:[chapter(1,{text:'甲😀乙'})]};await new Promise(resolve=>setTimeout(resolve,30));await flush()
  expect(text(root)).toContain('甲😀乙');expect(body.scrollTop).toBe(100);expect(text(root)).toContain('返回最新');button('返回最新').props.onClick();await flush();expect(body.scrollTop).toBe(900);expect(window.scrollTo).not.toHaveBeenCalled()
})
it('historical candidates remain copyable but cannot save/rewrite over the current version',async()=>{
  c.state.value=snapshot({state:'succeeded',persistenceState:'saved',candidateVersion:2});c.preview.value=snapshot({candidateVersion:1,chapters:[saved(1)]});mount();await flush()
  expect(text(root)).toContain('较早的候选 · 只读');expect(button('保存前')).toBeUndefined();expect(button('重写未保存')).toBeUndefined();expect(button('复制这一章')).toBeDefined()
})
it('returns the actual version selection to current after viewing an older candidate, so selecting that older candidate again works',async()=>{
  c.state.value=snapshot({state:'succeeded',persistenceState:'saved',candidateVersion:2});c.versions.value=[{candidateVersion:1},{candidateVersion:2}] as any
  vi.mocked(c.selectVersion).mockImplementation(async(version:number)=>{c.preview.value=version===2?null:snapshot({candidateVersion:version});return undefined})
  mount();await flush();const select=descendants(root,n=>n.props['aria-label']==='选择候选版本')[0] as Host&{selectedIndex:number}
  select.props['onUpdate:modelValue'](1);await select.props.onChange();await flush();expect(text(root)).toContain('候选 1')
  await button('返回当前候选').props.onClick();await flush();expect(text(root)).toContain('候选 2');expect(select.selectedIndex).toBe(1)
  select.props['onUpdate:modelValue'](1);await select.props.onChange();await flush();expect(c.selectVersion).toHaveBeenLastCalledWith(1);expect(text(root)).toContain('较早的候选 · 只读')
})

it('coalesces rapid one-character snapshots into bounded selectable text blocks within 150 ms',async()=>{
  c.state.value=snapshot({chapters:[chapter(1,{text:'开头。'})]});mount();await flush()
  let body='开头。'
  for(const character of '甲😀乙é'.repeat(200)){
    body+=character;c.state.value={...c.state.value!,chapters:[chapter(1,{text:body})]}
  }
  const started=performance.now()
  await new Promise(resolve=>setTimeout(resolve,40));await flush()
  const spans=descendants(root,n=>n.tag==='span'&&n.props['data-delta-index']!==undefined)
  expect(performance.now()-started).toBeLessThan(150)
  expect(spans.map(text).join('')).toBe(body)
  expect(spans.length).toBeLessThanOrEqual(1+Math.ceil((body.length-3)/128)+2)
  expect(spans.every(span=>!/[\uD800-\uDBFF]$/.test(text(span))&&!/^[\uDC00-\uDFFF]/.test(text(span)))).toBe(true)
})
it('discards an old pending text block on stream replacement and on unmount',async()=>{
  mount();await flush()
  c.state.value={...c.state.value!,chapters:[chapter(1,{text:'甲😀旧尾'})]}
  c.state.value={...c.state.value!,chapters:[chapter(1,{streamVersion:2,text:'新版本'})]}
  await new Promise(resolve=>setTimeout(resolve,40));await flush()
  const spans=descendants(root,n=>n.tag==='span'&&n.props['data-delta-index']!==undefined)
  expect(spans.map(text).join('')).toBe('新版本')
  c.state.value={...c.state.value!,chapters:[chapter(1,{streamVersion:2,text:'新版本不得晚写'})]}
  app.unmount();await new Promise(resolve=>setTimeout(resolve,40));await flush()
  expect(text(root)).toBe('')
})
