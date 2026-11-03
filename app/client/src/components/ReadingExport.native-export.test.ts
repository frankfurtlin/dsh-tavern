import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h,onUnmounted,ref,type Ref} from 'vue'
import ReadingExport from './ReadingExport.vue'
import type {ReadingExportScope} from '../api/reading-export'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({owner:'a'.repeat(32),epoch:1,store:null as any,intent:null as any,cap:vi.fn(),create:vi.fn(),save:vi.fn(),download:vi.fn()}))
vi.mock('../api',()=>({newKey:()=> 'owned-native-export-key',ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../api/reading-export',async original=>{const actual=await original<any>();return {...actual,readingExportAvailable:mock.cap,loadReadingIntent:async()=>mock.intent,saveReadingIntent:mock.save,queryReadingExport:vi.fn(),captureReadingExportScope:(_scope:unknown,active=()=>true)=>{const owner=mock.owner,epoch=mock.epoch;return()=>owner===mock.owner&&epoch===mock.epoch&&active()},createReadingExport:mock.create,downloadReadingExport:mock.download}})
vi.mock('../platform/bridge',()=>({accountId:()=>mock.owner,native:true}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>false}))
vi.mock('../platform/world-cache',()=>({isWorldLineDeleted:()=>false}))
vi.mock('../platform/request-scope',()=>({useRequestScope:(context=()=>null)=>{let disposed=false;onUnmounted(()=>disposed=true);return()=>{const owner=mock.owner,epoch=mock.epoch,key=JSON.stringify(context());return{valid:()=>!disposed&&owner===mock.owner&&epoch===mock.epoch&&key===JSON.stringify(context())}}}}))
vi.mock('../stores/app',()=>({useApp:()=>mock.store}))
compileRender(ReadingExport,new URL('./ReadingExport.vue',import.meta.url))
const scope={kind:'world' as const,worldId:'b'.repeat(32),lineId:'c'.repeat(32),checkpointId:'d'.repeat(32)}
const job={id:'e'.repeat(32),status:'ready',version:1,expiresAt:'2099-01-01T00:00:00Z',protocol:'reader-export-v1',format:'epub',fileName:'公开故事.epub',sha256:'f'.repeat(64),size:42,chapterCount:2}
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,scopeRef:Ref<ReadingExportScope>
function mount(){root=node('root');scopeRef=ref({...scope});app=renderer.createApp(defineComponent({setup:()=>()=>h(ReadingExport,{scope:scopeRef.value})}));provideSfc(app);app.mount(root)}
async function submit(){const form=descendants(root,n=>n.tag==='form')[0]!;await form.props.onSubmit({preventDefault(){}});await flush()}
function download(){const button=descendants(root,n=>n.tag==='button'&&text(n)==='保存 EPUB')[0]!;expect(button.props.disabled).toBeFalsy();return button.props.onClick() as Promise<void>}
beforeEach(()=>{mock.owner='a'.repeat(32);mock.epoch=1;mock.intent=null;mock.store=reactive({online:true,user:{id:mock.owner}});for(const fn of [mock.cap,mock.create,mock.save,mock.download])fn.mockReset();mock.cap.mockResolvedValue(true);mock.create.mockResolvedValue(job);mock.save.mockResolvedValue(undefined);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{activeElement:null})})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
it('reports successful native system save only after the native completion, with no browser-save claim',async()=>{
  const gate=deferred<void>();mock.download.mockReturnValueOnce(gate.promise);mount();await flush();await submit();const pending=download();await flush()
  expect(text(root)).not.toContain('文件已校验并保存到所选位置');gate.resolve();await pending;await flush()
  expect(text(root)).toContain('文件已校验并保存到所选位置');expect(text(root)).not.toContain('交给浏览器保存');expect(mock.download).toHaveBeenCalledOnce()
})
it('shows an unsupported native installation as unavailable without submitting or pretending a save',async()=>{
  mock.cap.mockResolvedValueOnce(false);mount();await flush();expect(text(root)).toContain('此安装包尚未提供阅读文件保存');expect(mock.create).not.toHaveBeenCalled();expect(mock.download).not.toHaveBeenCalled();expect(descendants(root,n=>n.tag==='form')).toHaveLength(0)
})
it('cancelled native save retains the prepared file for explicit retry and never displays success',async()=>{
  mock.download.mockRejectedValueOnce(Object.assign(new Error('已取消文件保存'),{code:'BACKUP_CANCELLED'}));mount();await flush();await submit();await download();await flush()
  expect(text(root)).toContain('已取消文件保存');expect(text(root)).toContain(job.fileName);expect(text(root)).not.toContain('文件已校验并保存');expect(mock.download).toHaveBeenCalledOnce();expect(mock.create).toHaveBeenCalledOnce()
})
it('an exact world checkpoint change discards late native save success and progress',async()=>{
  const gate=deferred<void>();mock.download.mockReturnValueOnce(gate.promise);mount();await flush();await submit();const pending=download();await flush()
  const oldProgress=mock.download.mock.calls[0]![2];scopeRef.value={...scope,checkpointId:'9'.repeat(32)};await flush();oldProgress(42);gate.resolve();await pending;await flush()
  expect(text(root)).not.toContain('文件已校验并保存');expect(text(root)).not.toContain('已接收');expect(mock.download).toHaveBeenCalledOnce()
})
