import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import ReaderPage from './ReaderPage.vue'
import GenerationStart from '../components/GenerationStart.vue'
import GenerationDraft from '../components/GenerationDraft.vue'
import SpeechBar from '../components/SpeechBar.vue'
import BookmarkRemoval from '../components/BookmarkRemoval.vue'
import {speechEngine} from '../test/speech-engine'
import {snapshot,chapter as draftChapter} from '../api/generation-test-fixtures'
import {api,ApiError,allPages} from '../api'
import {localNotes} from '../offline/storage'
import {readingSelection,readingSelectionPoints} from '../api/reading-annotations'
import {queueNote,syncNotes} from '../offline/notes'
import {cacheGet,cacheSet} from '../platform/bridge'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const state=vi.hoisted(()=>({epoch:0,route:null as any,store:null as any,push:vi.fn(),replace:vi.fn(),stream:vi.fn(),speech:null as any,images:[] as any[]}))
vi.mock('vue-router',()=>({useRoute:()=>state.route,useRouter:()=>({push:state.push,replace:state.replace})}))
vi.mock('../api',()=>({api:vi.fn(),allPages:vi.fn(),authGeneration:()=>state.epoch,newKey:()=> 'synthetic-key',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../api/stories',()=>({storyId:(kind:string,id:string)=>kind+'-'+id,rememberContext:vi.fn(),sessionContext:vi.fn()}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>undefined),cacheSet:vi.fn(),accountId:()=> 'synthetic-owner',backendBase:()=> 'https://fixture.invalid',appForeground:()=>true,native:false}))
vi.mock('../platform/system-speech',()=>({systemSpeech:()=>state.speech.driver}))
vi.mock('../platform/world-cache',()=>({isWorldLineDeleted:()=>false}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>false}))
vi.mock('../platform/bookmark-cache',()=>({isBookmarkDeleted:()=>false,bookmarkScope:()=>()=>true,bookmarkDeletionRequest:async()=>undefined,saveBookmarkDeletionRequest:async()=>{},markBookmarkDeleted:vi.fn()}))
vi.mock('../api/generation-watch',()=>({watchGeneration:state.stream}))
vi.mock('../platform/reading-activity',()=>({useReadingActivity:()=>{}}))
vi.mock('../stores/app',()=>({useApp:()=>state.store}))
vi.mock('../offline/storage',()=>({localNotes:vi.fn(),downloadedOrigin:async()=>false,readPackage:async()=>undefined,packageId:(...ids:string[])=>ids.join(':')}))
vi.mock('../api/reading-annotations',()=>({readingSelection:vi.fn(),readingSelectionPoints:vi.fn(async()=>null)}))
vi.mock('../offline/notes',()=>({queueNote:vi.fn(),syncNotes:vi.fn()}))
vi.mock('../components/StoryBlock.vue',()=>({default:defineComponent({props:{block:Object},setup:p=>()=>h('p',p.block?.text)})}))
vi.mock('../components/ReaderViewport.vue',()=>({default:defineComponent({setup:(_,{slots,expose})=>{expose({measure:async()=>{for(const item of descendants(root,()=>true))(item as any).querySelectorAll=()=>[]},gotoAnchor:async()=>{},gotoBlock:async()=>{},observe:()=>{}});return()=>h('section',slots.default?.())}})}))
vi.mock('../components/GenerationPanel.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
vi.mock('../components/ChatPanel.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
vi.mock('../components/CharacterCards.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
vi.mock('../components/NoteEditor.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
vi.mock('../components/DownloadReading.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
vi.mock('../components/ReadingExport.vue',()=>({default:defineComponent({props:{scope:Object},setup:p=>()=>h('section',{'data-reading-export-scope':JSON.stringify(p.scope)},'阅读导出')})}))
vi.mock('../components/Sheet.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('section',slots.default?.())})}))
vi.mock('../components/StoryImages.vue',()=>({default:defineComponent({props:['scope','purpose','side','contentId','paragraphId'],setup:(p,{slots})=>()=>h('section',{'data-image-side':p.side},[slots.fallback?.(),...state.images.filter(row=>row.scope.bookId===p.scope?.bookId&&row.scope.branchId===p.scope?.branchId&&row.scope.revisionId===p.scope?.revisionId&&row.purpose===p.purpose&&row.contentId===p.contentId&&(!p.side||row.side===p.side)).map(row=>h('img',{'data-placement':row.id,alt:row.alt}))])})}))
compileRender(ReaderPage,new URL('./ReaderPage.vue',import.meta.url))
compileRender(GenerationStart,new URL('../components/GenerationStart.vue',import.meta.url))
compileRender(GenerationDraft,new URL('../components/GenerationDraft.vue',import.meta.url))
compileRender(SpeechBar,new URL('../components/SpeechBar.vue',import.meta.url))
compileRender(BookmarkRemoval,new URL('../components/BookmarkRemoval.vue',import.meta.url))
const source='a'.repeat(32),carrier='b'.repeat(32),world='world-'+'c'.repeat(32),branch='d'.repeat(32),revision='e'.repeat(32),chapter='f'.repeat(32),block='1'.repeat(32)
const at={bookId:source,branchId:branch,revisionId:revision,chapterId:chapter,blockId:block,start:3,end:3,quoteHash:'unchanged-hash',offsetUnit:'utf16' as const}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host
function mount(){root=node('root');app=renderer.createApp(ReaderPage);provideSfc(app);app.component('RouterLink',defineComponent({props:{to:[Object,String]},setup:(p,{slots})=>()=>h('a',{href:p.to},slots.default?.())}));app.mount(root)}
function missing(){return new ApiError('BOOK_DELETED','来源小说已移除',404)}
it('opens the exact reader export scope without writing progress or submitting generation',async()=>{
  state.route.query={branch,revision}
  vi.mocked(allPages).mockImplementation(async path=>path.includes('/chapters')?[{id:chapter,title:'第一章',blockCount:1}]:path.includes('/branches')?[{id:branch,name:'原文',headRevisionId:revision}]:[] as any)
  vi.mocked(api).mockImplementation(async path=>path==='/books/'+source?{id:source,title:'合成阅读书',originalBranchId:branch}:path.includes('/progress')?{version:1,anchor:null,maxObservedAnchor:null}:{id:chapter,title:'第一章',revisionId:revision,blocks:[{id:block,text:'甲😀乙',source:'original',startAnchor:at,endAnchor:at}]} as any)
  mount();await flush();const setup=(app as any)._instance.setupState;setup.sheet='tools';await flush();const open=descendants(root,n=>n.tag==='button'&&text(n)==='导出阅读文件 · TXT / EPUB')[0]!;expect(open.props.disabled).toBeFalsy();vi.mocked(api).mockClear();open.props.onClick();await flush();expect(descendants(root,n=>n.props['data-reading-export-scope'])[0]!.props['data-reading-export-scope']).toBe(JSON.stringify({kind:'book',bookId:source,branchId:branch,revisionId:revision}));expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method&&options.method!=='GET')).toHaveLength(0)
})
beforeEach(()=>{
  state.images=[];const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>values.set(key,value)})
  vi.mocked(cacheSet).mockReset().mockResolvedValue(undefined)
  vi.mocked(cacheGet).mockReset().mockResolvedValue(undefined)
  vi.mocked(localNotes).mockReset().mockImplementation(async(_book,_line,_revision,notes)=>notes)
  vi.mocked(readingSelection).mockReset();vi.mocked(readingSelectionPoints).mockReset().mockResolvedValue(null);vi.mocked(queueNote).mockReset();vi.mocked(syncNotes).mockReset()
  state.stream.mockReset().mockReturnValue(vi.fn())
  state.speech=speechEngine();vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}})
  state.epoch=0;state.route=reactive({params:{bookId:source},query:{branch,revision,chapter,anchor:JSON.stringify(at),story:world},fullPath:'/books/'+source+'/read?branch='+branch});state.store=reactive({online:true,settings:{fontSize:22,lineHeight:1.8,readingWidth:36,readingMode:'scroll'},message:vi.fn(),user:{sessionId:'synthetic-session'}});state.push.mockReset().mockResolvedValue(undefined);state.replace.mockReset().mockResolvedValue(undefined);vi.mocked(api).mockReset();vi.mocked(allPages).mockReset()
  const events=new EventTarget();vi.stubGlobal('window',Object.assign(events,{scrollTo:vi.fn()}));vi.stubGlobal('document',Object.assign(new EventTarget(),{hidden:false}));vi.stubGlobal('IntersectionObserver',class{observe(){}disconnect(){}});vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})
})
it('does not restore deleted prose when local note merging finishes after the deletion event',async()=>{
  state.route.query={branch,revision}
  vi.mocked(allPages).mockImplementation(async(path)=>path.includes('/branches')?[{id:branch,name:'原文',headRevisionId:revision}]:path.includes('/chapters')?[{id:chapter,title:'第一章',blockCount:1}]:[] as any)
  vi.mocked(api).mockImplementation(async(path)=>path==='/books/'+source?{id:source,title:'仍在等待笔记的原书',originalBranchId:branch}:path.includes('/progress')?{version:1,anchor:null,maxObservedAnchor:null}:{id:chapter,title:'第一章',revisionId:revision,blocks:[{id:block,text:'迟到笔记不能恢复的原文',source:'original',startAnchor:at,endAnchor:at}]} as any)
  const notes=deferred<any>();vi.mocked(localNotes).mockReturnValueOnce(notes.promise)
  mount();await flush();expect(localNotes).toHaveBeenCalledTimes(1)
  vi.mocked(api).mockRejectedValueOnce(missing()).mockRejectedValueOnce(new ApiError('NOT_FOUND','来源已经删除',404))
  window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:source}}));await flush()
  notes.resolve([]);await flush()
  expect(text(root)).not.toContain('迟到笔记不能恢复的原文');expect(text(root)).not.toContain('仍在等待笔记的原书')
})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
it('resolves an old publication link using its exact anchor and preserves the world origin',async()=>{
  const moved={...at,bookId:carrier};vi.mocked(api).mockRejectedValueOnce(missing()).mockResolvedValueOnce({bookId:carrier,branchId:branch,revisionId:revision,anchor:moved,storyId:'book-'+carrier,migrated:true})
  mount();await flush()
  expect(vi.mocked(api).mock.calls[1]).toEqual(['/reading-context/resolve',{method:'POST',body:{bookId:source,branchId:branch,revisionId:revision,anchor:at}}])
  expect(state.replace).toHaveBeenCalledExactlyOnceWith({path:'/books/'+carrier+'/read',query:{...state.route.query,branch,revision,story:world,chapter,anchor:JSON.stringify(moved)}})
  expect(allPages).not.toHaveBeenCalled()
})
it('uses the canonical retained story instead of retaining a removed book origin',async()=>{
  state.route.query.story='book-'+source;vi.mocked(api).mockRejectedValueOnce(missing()).mockResolvedValueOnce({bookId:carrier,branchId:branch,revisionId:revision,anchor:{...at,bookId:carrier},storyId:'book-'+carrier,migrated:true})
  mount();await flush();expect(state.replace.mock.calls[0][0].query.story).toBe('book-'+carrier)
})
it('does not infer a publication branch for a deleted source without an explicit branch',async()=>{
  state.route.query={};vi.mocked(api).mockRejectedValue(missing());mount();await flush()
  expect(api).toHaveBeenCalledTimes(1);expect(state.replace).not.toHaveBeenCalled();expect(text(root)).toContain('这本小说已经移除')
  expect(descendants(root,item=>item.tag==='a'&&item.props.href==='/library')).toHaveLength(1)
  descendants(root,item=>item.tag==='button'&&item.props['aria-label']==='返回故事入口并保存进度')[0].props.onClick();await flush();expect(state.push).toHaveBeenCalledWith('/library')
})
it('does not redirect a different route or account when the old resolver arrives late',async()=>{
  const request=deferred<any>();vi.mocked(api).mockRejectedValueOnce(missing()).mockReturnValueOnce(request.promise)
  mount();await flush();state.epoch++;state.route.fullPath='/library';request.resolve({bookId:carrier,branchId:branch,revisionId:revision,storyId:'book-'+carrier,migrated:true});await flush()
  expect(state.replace).not.toHaveBeenCalled()
})
it('clears visible deleted source prose before waiting for a new request and never restores it on failure',async()=>{
  state.route.query={branch,revision};vi.mocked(allPages).mockImplementation(async(path)=>path.includes('/branches')?[{id:branch,name:'原文',headRevisionId:revision}]:path.includes('/chapters')?[{id:chapter,title:'第一章',blockCount:1}]:[] as any)
  vi.mocked(api).mockImplementation(async(path)=>path==='/books/'+source?{id:source,title:'合成原书',originalBranchId:branch}:path.includes('/progress')?{version:1,anchor:null,maxObservedAnchor:null}:{id:chapter,title:'第一章',revisionId:revision,blocks:[{id:block,text:'删除后不能留下的合成正文',source:'original',startAnchor:{...at,start:0,end:0},endAnchor:at}]} as any)
  mount();await flush();expect(text(root)).toContain('删除后不能留下的合成正文')
  const request=deferred<any>();vi.mocked(api).mockReturnValueOnce(request.promise)
  window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:source}}));await flush()
  expect(text(root)).not.toContain('删除后不能留下的合成正文');expect(text(root)).not.toContain('合成原书')
  request.reject(missing());vi.mocked(api).mockRejectedValueOnce(new ApiError('NOT_FOUND','无法打开这条故事线',404));await flush()
  expect(text(root)).not.toContain('删除后不能留下的合成正文');expect(text(root)).toContain('无法打开这条故事线');expect(state.replace).not.toHaveBeenCalled()
})

function prepareReadable(){
  state.route.query={branch,revision}
  vi.mocked(allPages).mockImplementation(async(path)=>path.includes('/branches')?[{id:branch,name:'原文',headRevisionId:revision}]:path.includes('/chapters')?[{id:chapter,title:'第一章',blockCount:1}]:[] as any)
  vi.mocked(api).mockImplementation(async(path)=>path==='/books/'+source?{id:source,title:'合成划线书',originalBranchId:branch}:path.includes('/progress')?{version:1,anchor:null,maxObservedAnchor:null}:{id:chapter,title:'第一章',revisionId:revision,blocks:[{id:block,text:'甲😀乙',source:'original',startAnchor:{...at,start:0,end:0},endAnchor:{...at,start:4,end:4}}]} as any)
}
it('recognizes the saved reading position after the server reorders anchor fields',async()=>{
  prepareReadable()
  const saved=Object.fromEntries(Object.entries(at).reverse())
  vi.mocked(cacheGet).mockResolvedValue({anchor:at,version:3,sequence:7})
  const fixture=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation(async(path,options)=>path.includes('/progress')?{version:4,anchor:saved,maxObservedAnchor:saved}:fixture(path,options) as any)
  mount();await flush()
  expect(text(root)).toContain('合成划线书')
  expect(text(root)).not.toContain('位置冲突')
  expect((app as any)._instance.setupState.version).toBe(4)
})
it('keeps a genuine conflict when an older local reading position differs from the server',async()=>{
  prepareReadable()
  const saved={...at,start:4,end:4}
  vi.mocked(cacheGet).mockResolvedValue({anchor:at,version:3,sequence:7})
  const fixture=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation(async(path,options)=>path.includes('/progress')?{version:4,anchor:saved,maxObservedAnchor:saved}:fixture(path,options) as any)
  mount();await flush()
  expect(text(root)).toContain('位置冲突')
  expect((app as any)._instance.setupState.version).toBe(3)
})
it('opens an explicit CAS deletion confirmation instead of immediately deleting a bookmark',async()=>{
  prepareReadable()
  const note={id:'bookmark-cas-a',anchor:at,note:'保留到确认删除',status:'valid',version:7}
  vi.mocked(localNotes).mockResolvedValue([note])
  mount();await flush()
  descendants(root,item=>item.tag==='button'&&item.props['aria-label']==='阅读工具')[0].props.onClick();await flush()
  descendants(root,item=>item.tag==='button'&&text(item)==='查看书签')[0].props.onClick();await flush()
  descendants(root,item=>item.tag==='button'&&item.props['aria-label']==='删除书签')[0].props.onClick();await flush()
  expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='DELETE')).toHaveLength(0)
  expect(text(root)).toContain('确认删除笔记')
})
it('persists the exact selected range before attempting online synchronization',async()=>{
  prepareReadable();const range={...at,start:1,end:3,quoteHash:'selected-emoji-hash'},saved={id:'local-highlight',anchor:range,note:'',status:'pending_sync',version:1}
  vi.mocked(readingSelection).mockResolvedValue(range);const pending=deferred<any>();vi.mocked(queueNote).mockReturnValue(pending.promise);vi.mocked(syncNotes).mockResolvedValue({synced:1,remaining:0})
  mount();await flush();document.dispatchEvent(new Event('selectionchange'));await flush()
  const button=descendants(root,item=>item.tag==='button'&&text(item)==='划线')[0];expect(button).toBeDefined();button.props.onClick();await flush()
  expect(queueNote).toHaveBeenCalledExactlyOnceWith(range,'');expect(syncNotes).not.toHaveBeenCalled()
  pending.resolve(saved);await flush();expect(syncNotes).toHaveBeenCalledTimes(1)
})
it('retains offline highlights locally without issuing a sync or pretending a server save',async()=>{
  prepareReadable();state.store.online=false;const range={...at,start:1,end:3,quoteHash:'selected-emoji-hash'}
  vi.mocked(readingSelection).mockResolvedValue(range);vi.mocked(queueNote).mockResolvedValue({id:'local-highlight',anchor:range,note:'',status:'pending_sync',version:1})
  mount();await flush();document.dispatchEvent(new Event('selectionchange'));await flush()
  descendants(root,item=>item.tag==='button'&&text(item)==='划线')[0].props.onClick();await flush()
  expect(queueNote).toHaveBeenCalledExactlyOnceWith(range,'');expect(syncNotes).not.toHaveBeenCalled();expect(state.store.message).toHaveBeenCalledWith('划线已保存在本机')
})
it('does not synchronize another account after a late local highlight save',async()=>{
  prepareReadable();const range={...at,start:1,end:3,quoteHash:'selected-emoji-hash'},pending=deferred<any>()
  vi.mocked(readingSelection).mockResolvedValue(range);vi.mocked(queueNote).mockReturnValue(pending.promise)
  mount();await flush();document.dispatchEvent(new Event('selectionchange'));await flush();descendants(root,item=>item.tag==='button'&&text(item)==='划线')[0].props.onClick();await flush()
  state.epoch++;pending.resolve({id:'local-highlight',anchor:range,note:'',status:'pending_sync',version:1});await flush()
  expect(syncNotes).not.toHaveBeenCalled();expect(state.store.message).not.toHaveBeenCalled()
})

it.each(['before','during'])('does not keep a selection from the previous chapter when extraction starts %s a pending chapter request',async when=>{
  prepareReadable();const otherChapter='2'.repeat(32),otherBlock='3'.repeat(32),next=deferred<any>(),selection=deferred<any>(),range={...at,start:1,end:3,quoteHash:'old-emoji-range'}
  vi.mocked(allPages).mockImplementation(async(path)=>path.includes('/chapters')?[{id:chapter,title:'第一章',blockCount:1},{id:otherChapter,title:'第二章',blockCount:1}]:path.includes('/branches')?[{id:branch,name:'原文',headRevisionId:revision}]:[] as any)
  vi.mocked(readingSelection).mockReturnValue(selection.promise);mount();await flush()
  if(when==='before'){document.dispatchEvent(new Event('selectionchange'));await flush()}
  vi.mocked(api).mockReturnValueOnce(next.promise);descendants(root,item=>item.tag==='button'&&text(item).includes('下一章'))[0].props.onClick();await flush()
  if(when==='during'){document.dispatchEvent(new Event('selectionchange'));await flush()}
  next.resolve({id:otherChapter,title:'第二章',revisionId:revision,blocks:[{id:otherBlock,text:'下一章的独立正文',source:'original',startAnchor:{...at,chapterId:otherChapter,blockId:otherBlock},endAnchor:{...at,chapterId:otherChapter,blockId:otherBlock}}]});await flush();selection.resolve(range);await flush()
  expect(text(root)).toContain('下一章的独立正文');expect(descendants(root,item=>item.props['aria-label']==='文字批注')).toHaveLength(0);expect(queueNote).not.toHaveBeenCalled()
})

function prepareStreamingReader(){
  prepareReadable()
  vi.mocked(allPages).mockImplementation(async(path)=>path==='/model-profiles'?[{id:'default-mimo',status:'active',hasKey:true,isDefault:true}]:path.includes('/branches')?[{id:branch,name:'原文',headRevisionId:revision,parentBranchId:null}]:path.includes('/chapters')?[{id:chapter,title:'第一章',blockCount:1}]:[] as any)
  vi.mocked(api).mockImplementation(async(path,options)=>path==='/generations/capabilities'?{protocol:'reader-prose-v1',ready:true}:path==='/books/'+source+'/generations'?{generationId:'inline-batch',taskId:'inline-parent',protocol:'reader-prose-v1'}:path==='/books/'+source?{id:source,title:'合成原书',originalBranchId:branch}:path.includes('/progress')?{version:1,anchor:null,maxObservedAnchor:null}:{id:chapter,title:'第一章',revisionId:revision,blocks:[{id:block,text:'甲😀乙',source:'original',startAnchor:{...at,start:0,end:0},endAnchor:{...at,start:4,end:4}}]} as any)
}
async function startInline(){
  mount();await flush();descendants(root,n=>n.tag==='button'&&text(n)==='从这里写')[0].props.onClick();await flush()
  expect(descendants(root,n=>n.props['aria-label']==='开始小说写作')).toHaveLength(1)
  descendants(root,n=>n.tag==='button'&&text(n).includes('开始写作'))[0].props.onClick();await flush()
  return snapshot({id:'inline-batch',taskId:'inline-parent',bookId:source,scopeAnchor:{...at,start:4,end:4},chapters:[draftChapter(1,{text:'真实字块一'})]})
}
it('closes the light start panel but keeps its parent observation and draft directly after the pinned paragraph',async()=>{
  prepareStreamingReader();const draft=await startInline();expect(state.stream).toHaveBeenCalledTimes(1);const stop=state.stream.mock.results[0].value
  state.stream.mock.calls[0][1](draft,()=>true);await flush()
  expect(descendants(root,n=>n.props['aria-label']==='开始小说写作')).toHaveLength(0)
  const paragraph=descendants(root,n=>n.props['data-reading-block']===block)[0],inline=descendants(root,n=>n.props['aria-label']==='行内写作草稿')[0]
  expect(inline.parent).toBe(paragraph.parent);expect(inline.parent!.children.indexOf(inline)).toBeGreaterThan(inline.parent!.children.indexOf(paragraph));expect(text(paragraph)).toBe('甲😀乙');expect(stop).not.toHaveBeenCalled()
  state.stream.mock.calls[0][1]({...draft,eventCursor:6,chapters:[{...draft.chapters[0],text:'真实字块一字块二'}]},()=>true);await vi.waitFor(()=>expect(text(root)).toContain('真实字块一字块二'),{timeout:150,interval:10});expect(stop).not.toHaveBeenCalled()
})
it('clears inline prose and invalidates its late observer immediately when the source is removed',async()=>{
  prepareStreamingReader();const draft=await startInline(),callback=state.stream.mock.calls[0][1],stop=state.stream.mock.results[0].value;callback(draft,()=>true);await flush();expect(text(root)).toContain('真实字块一')
  vi.mocked(api).mockRejectedValue(missing());window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:source}}));await flush();expect(stop).toHaveBeenCalledTimes(1);callback({...draft,eventCursor:6},()=>true);await flush();expect(text(root)).not.toContain('真实字块一')
})
it('opens system speech from readable selected prose, starts only explicitly and highlights only actual speech callbacks',async()=>{
  prepareReadable();mount();await flush();expect(state.speech.driver.play).not.toHaveBeenCalled();descendants(root,n=>n.props['data-reading-block']===block)[0].props.onClick();descendants(root,n=>n.props['aria-label']==='阅读工具')[0].props.onClick();await flush();await descendants(root,n=>n.tag==='button'&&text(n).includes('播放当前正文'))[0].props.onClick();await flush()
  expect(state.speech.driver.loadQueue).toHaveBeenCalledOnce();const input=state.speech.driver.loadQueue.mock.calls[0][0];expect(input.fromId).toBe(block);expect(input.segments[0].text).toBe('甲😀乙');expect(input.segments[0].anchor).toMatchObject({bookId:source,branchId:branch,revisionId:revision});expect(text(root)).toContain('正在启动系统声音');expect(descendants(root,n=>n.props['data-reading-block']===block)[0].props.class).not.toContain('speech-playing')
  state.speech.emit({status:'playing',currentId:block});await flush();expect(descendants(root,n=>n.props['data-reading-block']===block)[0].props.class).toContain('speech-playing');state.speech.emit({status:'ended',lastCompletedId:block,eventVersion:4});await flush();expect(descendants(root,n=>n.props['data-reading-block']===block)[0].props.class).toContain('speech-completed');expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(0)
})
it('unmounting or removing the source stops and clears the system queue instead of allowing a late event to restore its prose',async()=>{
  prepareReadable();mount();await flush();descendants(root,n=>n.props['aria-label']==='阅读工具')[0].props.onClick();await flush();await descendants(root,n=>n.tag==='button'&&text(n).includes('播放当前正文'))[0].props.onClick();await flush();expect(state.speech.driver.play).toHaveBeenCalledOnce();vi.mocked(api).mockRejectedValue(missing());window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:source}}));await flush();expect(state.speech.driver.stop).toHaveBeenCalledWith({clearPosition:true});state.speech.emit({status:'playing',currentId:block,eventVersion:10});await flush();expect(descendants(root,n=>n.props['aria-label']==='系统听书')).toHaveLength(0)
})
it('offers explicit narration only after the inline chapter snapshot confirms saved complete content',async()=>{
  prepareStreamingReader();const draft=await startInline();state.stream.mock.calls[0][1](draft,()=>true);await flush();expect(descendants(root,n=>n.tag==='button'&&text(n).includes('听这一章'))).toHaveLength(0)
  const saved={...draft,eventCursor:6,completedChapters:1,chapters:[{...draft.chapters[0],completeness:'complete',durability:'saved',canAccept:true,contentHash:'saved-hash'}]};state.stream.mock.calls[0][1](saved,()=>true);await flush();descendants(root,n=>n.tag==='button'&&text(n).includes('听这一章'))[0].props.onClick();await vi.waitFor(()=>expect(state.speech.driver.loadQueue).toHaveBeenCalledOnce());await flush();expect(state.speech.driver.loadQueue.mock.calls[0][0].segments[0]).toMatchObject({text:'真实字块一',anchor:{draft:true,generationId:'inline-batch'}});expect(state.speech.driver.play).toHaveBeenCalledOnce();expect(queueNote).not.toHaveBeenCalled()
})

const laterBlock='4'.repeat(32),laterAt={...at,blockId:laterBlock,start:8,end:8,quoteHash:'later-utf16-hash'}
function twoParagraphs(){
  prepareReadable();const previous=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation(async(path,options)=>{
    if(options?.method==='PUT'&&path==='/books/'+source+'/progress'){const body=options.body as any;return {version:body.expectedProgressVersion+1,anchor:body.anchor,maxObservedAnchor:body.anchor,revisionId:revision}}
    if(path.includes('/chapters/'))return {id:chapter,title:'第一章',revisionId:revision,blocks:[{id:block,text:'甲😀乙',source:'original',startAnchor:{...at,start:0,end:0},endAnchor:at},{id:laterBlock,text:'后段甲😀乙',source:'original',startAnchor:{...laterAt,start:0,end:0},endAnchor:laterAt}]} as any
    return previous(path,options) as any
  })
}
async function openSpeech(){descendants(root,n=>n.props['aria-label']==='阅读工具')[0].props.onClick();await flush();await descendants(root,n=>n.tag==='button'&&text(n).includes('播放当前正文'))[0].props.onClick();await flush()}
const readingPuts=()=>vi.mocked(api).mock.calls.filter(([path,options])=>path==='/books/'+source+'/progress'&&options?.method==='PUT')
it('saves a manually skipped reader paragraph by Progress CAS while leaving actual heard completion and Playback PUT untouched',async()=>{
  twoParagraphs();mount();await flush();await openSpeech();const setup=(app as any)._instance.setupState,engine=state.speech.driver,input=engine.loadQueue.mock.calls[0][0]
  engine.skip.mockResolvedValue({scopeKey:input.scopeKey,status:'paused',queueLoaded:true,currentId:laterBlock,eventVersion:4})
  vi.mocked(api).mockClear();await descendants(root,n=>n.props['aria-label']==='听书下一段')[0].props.onClick();await flush()
  expect(readingPuts()).toHaveLength(1);expect(readingPuts()[0][1]?.body).toMatchObject({anchor:laterAt,expectedProgressVersion:setup.version-1,deviceId:'synthetic-session'})
  expect(setup.observed).toEqual(laterAt);expect(setup.speech.lastCompleted.value).toBeNull();expect(vi.mocked(api).mock.calls.some(([path])=>path==='/playback/position')).toBe(false)
})
it('retains a manual reader skip after a Progress conflict and saves it only after explicit conflict resolution',async()=>{
  twoParagraphs();mount();await flush();await openSpeech();const setup=(app as any)._instance.setupState,input=state.speech.driver.loadQueue.mock.calls[0][0]
  state.speech.driver.skip.mockResolvedValue({scopeKey:input.scopeKey,status:'paused',queueLoaded:true,currentId:laterBlock,eventVersion:4})
  const error=new ApiError('PROGRESS_CONFLICT','另一设备已更新位置',409);(error as any).details={saved:{version:7,anchor:at,maxObservedAnchor:at,revisionId:revision},localAnchor:laterAt}
  const previous=vi.mocked(api).getMockImplementation()!;vi.mocked(api).mockImplementation(async(path,options)=>{if(options?.method==='PUT')throw error;return previous(path,options) as any})
  await descendants(root,n=>n.props['aria-label']==='听书下一段')[0].props.onClick();await flush();expect(text(root)).toContain('另一设备保存了新的位置');expect(setup.current).toEqual(laterAt);expect(setup.speech.lastCompleted.value).toBeNull()
  vi.mocked(api).mockImplementation(previous);const before=readingPuts().length;await descendants(root,n=>n.tag==='button'&&text(n)==='继续本机位置并保存')[0].props.onClick();await flush()
  expect(readingPuts()).toHaveLength(before+1);expect(readingPuts().at(-1)![1]?.body).toMatchObject({anchor:laterAt,expectedProgressVersion:7});expect(setup.conflict).toBeNull();expect(setup.observed).toEqual(laterAt)
})
it('does not persist or show a delayed manual reader skip after authentication changes',async()=>{
  twoParagraphs();mount();await flush();await openSpeech();vi.mocked(api).mockClear();const input=state.speech.driver.loadQueue.mock.calls[0][0],gate=deferred<any>()
  state.speech.driver.skip.mockReturnValue(gate.promise);const skipping=descendants(root,n=>n.props['aria-label']==='听书下一段')[0].props.onClick();await flush();state.epoch++
  gate.resolve({scopeKey:input.scopeKey,status:'paused',queueLoaded:true,currentId:laterBlock,eventVersion:4});await skipping;await flush();expect(readingPuts()).toHaveLength(0);expect((app as any)._instance.setupState.observed).not.toEqual(laterAt)
})
it('renders legal before and after illustration placements once on their precise sides without changing the paragraph anchor',async()=>{
  twoParagraphs();state.images=[{id:'before-owned',scope:{bookId:source,branchId:branch,revisionId:revision},purpose:'illustration',contentId:block,side:'before',alt:'段前图'},{id:'after-owned',scope:{bookId:source,branchId:branch,revisionId:revision},purpose:'illustration',contentId:block,side:'after',alt:'段后图'},{id:'foreign',scope:{bookId:carrier,branchId:branch,revisionId:revision},purpose:'illustration',contentId:block,side:'before',alt:'别书图'}]
  mount();await flush();const paragraph=descendants(root,n=>n.props['data-reading-block']===block)[0],before=descendants(root,n=>n.props['data-placement']==='before-owned'),after=descendants(root,n=>n.props['data-placement']==='after-owned')
  expect(before).toHaveLength(1);expect(after).toHaveLength(1);expect(descendants(root,n=>n.props['data-placement']==='foreign')).toHaveLength(0)
  expect(paragraph.parent!.children.indexOf(before[0].parent!)).toBeLessThan(paragraph.parent!.children.indexOf(paragraph));expect(paragraph.parent!.children.indexOf(after[0].parent!)).toBeGreaterThan(paragraph.parent!.children.indexOf(paragraph));expect(text(paragraph)).toBe('甲😀乙');expect(readingPuts()).toHaveLength(0)
})
it.each(['auth','route'] as const)('does not send a manual reader Progress write after %s changes during its durable local save',async(change)=>{
  twoParagraphs();mount();await flush();await openSpeech();const input=state.speech.driver.loadQueue.mock.calls[0][0],gate=deferred<void>()
  vi.mocked(api).mockClear();vi.mocked(cacheSet).mockImplementation(async(key,value)=>{if(key.startsWith('pending-progress:')&&(value as any)?.anchor?.blockId===laterBlock)await gate.promise})
  state.speech.driver.skip.mockResolvedValue({scopeKey:input.scopeKey,status:'paused',queueLoaded:true,currentId:laterBlock,eventVersion:4})
  const skipping=descendants(root,n=>n.props['aria-label']==='听书下一段')[0].props.onClick();await flush();expect(cacheSet).toHaveBeenCalled()
  if(change==='auth')state.epoch++;else state.route.fullPath='/library'
  await flush();gate.resolve();await skipping;await flush();expect(readingPuts()).toHaveLength(0)
})
it('does not apply a late manual reader Progress success to a newly authenticated reader',async()=>{
  twoParagraphs();mount();await flush();await openSpeech();const input=state.speech.driver.loadQueue.mock.calls[0][0],gate=deferred<any>(),setup=(app as any)._instance.setupState
  state.speech.driver.skip.mockResolvedValue({scopeKey:input.scopeKey,status:'paused',queueLoaded:true,currentId:laterBlock,eventVersion:4});vi.mocked(api).mockClear();const previous=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation((path,options)=>options?.method==='PUT'?gate.promise:previous(path,options))
  const skipping=descendants(root,n=>n.props['aria-label']==='听书下一段')[0].props.onClick();await vi.waitFor(()=>expect(readingPuts()).toHaveLength(1));const expected=setup.version;state.epoch++
  gate.resolve({version:99,anchor:laterAt,maxObservedAnchor:laterAt,revisionId:revision});await skipping;await flush();expect(setup.version).toBe(expected);expect(setup.observed).not.toEqual(laterAt)
})
