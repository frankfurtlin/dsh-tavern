import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive} from 'vue'
import DownloadWorldReading from './DownloadWorldReading.vue'
import {downloadWorldReading} from '../offline/download-world'
import {compileActualTemplate,mountHost,hostText,flushHost,hostDeferred} from '../offline/sfc-test-host'
const fixture=vi.hoisted(()=>({owner:'owner-a',epoch:0,path:'/worlds/world-a/read',app:{online:true}}))
vi.mock('../offline/download-world',()=>({downloadWorldReading:vi.fn()}))
vi.mock('../stores/app',()=>({useApp:()=>fixture.app}))
vi.mock('../platform/request-scope',async()=>{const {onUnmounted}=await import('vue');return {useRequestScope:(context:()=>unknown)=>{let disposed=false;onUnmounted(()=>{disposed=true});return()=>{const owner=fixture.owner,epoch=fixture.epoch,path=fixture.path,key=JSON.stringify(context());return {valid:()=>!disposed&&owner===fixture.owner&&epoch===fixture.epoch&&path===fixture.path&&key===JSON.stringify(context())}}}}})
compileActualTemplate(DownloadWorldReading,new URL('./DownloadWorldReading.vue',import.meta.url))
let mounted:ReturnType<typeof mountHost>|undefined,props:{worldId:string;lineId:string;headId:string;origin:string;lineName:string}
beforeEach(()=>{fixture.owner='owner-a';fixture.epoch=0;fixture.path='/worlds/world-a/read';fixture.app.online=true;vi.mocked(downloadWorldReading).mockReset().mockResolvedValue({} as any);props=reactive({worldId:'world-a',lineId:'line-a',headId:'head-a',origin:'',lineName:'合成公开线'})})
afterEach(()=>{mounted?.app.unmount();mounted=undefined})
function mount(){mounted=mountHost(DownloadWorldReading,props);return mounted}
it('downloads only after the actual button is clicked and sends the exact selected line/head',async()=>{
  const {root,find}=mount();await flushHost();expect(downloadWorldReading).not.toHaveBeenCalled()
  find(n=>n.tag==='button'&&hostText(n).includes('下载这条世界线'))!.props.onClick();await flushHost()
  expect(downloadWorldReading).toHaveBeenCalledWith('world-a','line-a','head-a','',expect.any(Function));expect(hostText(root)).toContain('已下载，可离线阅读')
})
it('stops a pending explicit download and its late success cannot register success in the reopened controls',async()=>{
  const old=hostDeferred<any>();vi.mocked(downloadWorldReading).mockReturnValueOnce(old.promise)
  const {root,find}=mount();find(n=>n.tag==='button')!.props.onClick();await flushHost();expect(hostText(root)).toContain('正在保存公开正文')
  const active=vi.mocked(downloadWorldReading).mock.calls[0]![4]!
  find(n=>n.tag==='button'&&hostText(n).includes('停止下载'))!.props.onClick();await flushHost();expect(active()).toBe(false)
  old.resolve({});await flushHost();expect(hostText(root)).not.toContain('已下载，可离线阅读');expect(hostText(root)).toContain('下载这条世界线')
})
it.each(['head','owner','route','unmount'])('invalidates the pending download after %s changes, without exposing a late error',async(kind)=>{
  const old=hostDeferred<any>();vi.mocked(downloadWorldReading).mockReturnValueOnce(old.promise)
  const {root,find}=mount();find(n=>n.tag==='button')!.props.onClick();await flushHost();const active=vi.mocked(downloadWorldReading).mock.calls[0]![4]!
  if(kind==='head')props.headId='head-b';else if(kind==='owner'){fixture.owner='owner-b';fixture.epoch++}else if(kind==='route')fixture.path='/downloads';else mounted!.app.unmount()
  await flushHost();expect(active()).toBe(false);old.reject(new Error('旧下载错误'));await flushHost();expect(hostText(root)).not.toContain('旧下载错误')
  if(kind==='head'){expect(hostText(root)).not.toContain('正在保存');find(n=>n.tag==='button')!.props.onClick();await flushHost();expect(downloadWorldReading).toHaveBeenLastCalledWith('world-a','line-a','head-b','',expect.any(Function))}
})
it('keeps the explicit control disabled offline and does not invoke the downloader',async()=>{
  fixture.app.online=false;const {find}=mount();const button=find(n=>n.tag==='button')!;expect(button.props.disabled).toBe(true);button.props.onClick();await flushHost();expect(downloadWorldReading).not.toHaveBeenCalled()
})
