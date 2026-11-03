import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import DownloadsPage from './DownloadsPage.vue'
import {entries,noteQueue,removePackage} from '../offline/storage'
import {worldEntries,removeWorldPackage} from '../offline/world-storage'
import {compileActualTemplate,mountHost,hostAll,hostText,flushHost,hostDeferred} from '../offline/sfc-test-host'
const fixture=vi.hoisted(()=>({owner:'owner-a',epoch:0,path:'/downloads',app:{online:true,message:vi.fn()},books:[] as any[],worlds:[] as any[]}))
vi.mock('../offline/storage',()=>({entries:vi.fn(),noteQueue:vi.fn(),removePackage:vi.fn()}))
vi.mock('../offline/world-storage',()=>({worldEntries:vi.fn(),removeWorldPackage:vi.fn()}))
vi.mock('../offline/notes',()=>({syncNotes:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>fixture.app}))
vi.mock('../platform/request-scope',async()=>{const {onUnmounted}=await import('vue');return {useRequestScope:()=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const owner=fixture.owner,epoch=fixture.epoch,path=fixture.path;return {valid:()=>!disposed&&owner===fixture.owner&&epoch===fixture.epoch&&path===fixture.path}}}}})
compileActualTemplate(DownloadsPage,new URL('./DownloadsPage.vue',import.meta.url))
let mounted:ReturnType<typeof mountHost>|undefined
const book={id:'book-package',bookId:'book-a',branchId:'branch-a',revisionId:'revision-a',title:'合成小说',lineName:'原文',chapterCount:2,bytes:1000,downloadedAt:'2026-10-05T00:00:00Z'}
const world=(lineId='line-a')=>({id:'world-a:'+lineId+':head-a',worldId:'world-a',lineId,headCheckpointId:'head-a',title:'合成世界'+lineId,lineName:lineId,chapterCount:3,bytes:1000,downloadedAt:'2026-10-05T00:00:00Z'})
beforeEach(()=>{
  fixture.owner='owner-a';fixture.epoch=0;fixture.path='/downloads';fixture.books=[{...book}];fixture.worlds=[world()];fixture.app.online=true
  vi.mocked(entries).mockReset().mockImplementation(async()=>[...fixture.books]);vi.mocked(worldEntries).mockReset().mockImplementation(async()=>[...fixture.worlds]);vi.mocked(noteQueue).mockReset().mockResolvedValue([])
  vi.mocked(removePackage).mockReset().mockImplementation(async id=>{fixture.books=fixture.books.filter(item=>item.id!==id)})
  vi.mocked(removeWorldPackage).mockReset().mockImplementation(async id=>{fixture.worlds=fixture.worlds.filter(item=>item.id!==id)})
  vi.stubGlobal('window',new EventTarget());vi.stubGlobal('CustomEvent',class extends Event{detail:unknown;constructor(name:string,options?:{detail:unknown}){super(name);this.detail=options?.detail}})
})
afterEach(()=>{mounted?.app.unmount();mounted=undefined;vi.unstubAllGlobals()})
function mount(){mounted=mountHost(DownloadsPage);return mounted}
it('separates novel/world downloads and links to the exact downloaded world line/head/package',async()=>{
  const {root,find}=mount();await flushHost();expect(find(n=>n.props['aria-label']==='小说阅读下载')).toBeDefined();expect(find(n=>n.props['aria-label']==='世界故事下载')).toBeDefined()
  expect(hostAll(root,n=>n.tag==='a').map(n=>n.props.href)).toContainEqual({path:'/worlds/world-a/read',query:{line:'line-a',checkpoint:'head-a',download:'world-a:line-a:head-a'}})
  find(n=>n.props['aria-label']==='移除本机世界下载')!.props.onClick();await flushHost();expect(removeWorldPackage).toHaveBeenCalledWith('world-a:line-a:head-a',expect.any(Function));expect(hostText(root)).not.toContain('合成世界line-a');expect(hostText(root)).toContain('合成小说')
})
it('clears a removed novel immediately while retaining the independent world download and ignoring an old list reply',async()=>{
  const {root}=mount();await flushHost();const old=hostDeferred<any[]>();vi.mocked(entries).mockReturnValueOnce(old.promise)
  window.dispatchEvent(new Event('reader-notes-changed'));await flushHost();fixture.books=[];window.dispatchEvent(new CustomEvent('reader-book-deleted',{detail:{bookId:'book-a'}}));await flushHost()
  expect(hostText(root)).not.toContain('合成小说');expect(hostText(root)).toContain('合成世界line-a');old.resolve([{...book}]);await flushHost();expect(hostText(root)).not.toContain('合成小说');expect(hostText(root)).toContain('合成世界line-a')
})
it('clears only the deleted world line and ignores an old world list, keeping other lines and novel downloads',async()=>{
  fixture.worlds=[world(),world('line-b')];const {root}=mount();await flushHost();const old=hostDeferred<any[]>();vi.mocked(worldEntries).mockReturnValueOnce(old.promise)
  window.dispatchEvent(new Event('reader-notes-changed'));await flushHost();fixture.worlds=[world('line-b')];window.dispatchEvent(new CustomEvent('reader-world-deleted',{detail:{worldId:'world-a',lineId:'line-a'}}));await flushHost()
  expect(hostText(root)).not.toContain('合成世界line-a');expect(hostText(root)).toContain('合成世界line-b');expect(hostText(root)).toContain('合成小说');old.resolve([world(),world('line-b')]);await flushHost();expect(hostText(root)).not.toContain('合成世界line-a')
})
it.each(['account','route','unmount'])('does not show a late private downloads list after %s changes',async(kind)=>{
  const held=hostDeferred<any[]>();vi.mocked(worldEntries).mockReturnValueOnce(held.promise);const {root}=mount();await flushHost()
  if(kind==='account'){fixture.owner='owner-b';fixture.epoch++}else if(kind==='route')fixture.path='/library';else mounted!.app.unmount()
  held.resolve([world()]);await flushHost();expect(hostText(root)).not.toContain('合成世界line-a')
})
