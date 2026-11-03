import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h} from 'vue'
import ImageEditor from './ImageEditor.vue'
import {api,ApiError} from '../api'
import {imagePlacements,imagesChanged} from '../api/assets'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
vi.mock('../api/image-changes',()=>({recordImageChange:vi.fn().mockResolvedValue(undefined),currentImagePlacements:async(rows:unknown)=>rows}))
const mock=vi.hoisted(()=>({owner:'owner',epoch:0,store:null as any,values:new Map<string,unknown>(),keys:0,failClear:false}))
vi.mock('../api',()=>({api:vi.fn(),newKey:()=> 'request-'+(++mock.keys),ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../api/assets',()=>({imageCapabilities:async()=>({ready:true}),imageScopeKey:(scope:unknown)=>JSON.stringify(scope),captureImageScope:(_scope:unknown,active:()=>boolean)=>{const owner=mock.owner,epoch=mock.epoch;return()=>owner===mock.owner&&epoch===mock.epoch&&active()},imagePlacements:vi.fn(),imagesChanged:vi.fn()}))
vi.mock('../platform/bridge',()=>({cacheGet:async(key:string)=>structuredClone(mock.values.get(key)),cacheSet:async(key:string,value:unknown)=>{if(value===null&&mock.failClear)throw new Error('local disk failed');mock.values.set(key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('../stores/app',()=>({useApp:()=>mock.store}))
compileRender(ImageEditor,new URL('./ImageEditor.vue',import.meta.url))
const scope={kind:'book' as const,bookId:'book',branchId:'branch',revisionId:'revision'},asset={assetId:'asset',scope,variants:{}},placement={id:'placement',scope,purpose:'cover' as const,assetId:'old-asset',asset:{...asset,assetId:'old-asset'},contentId:null,paragraphId:null,side:'after' as const,order:0,alt:'原说明',version:4}
const pending={phase:'place',key:'original-place',scope,purpose:'cover',asset,body:{assetId:'asset',scope,purpose:'cover',placementId:'placement',expectedVersion:4,alt:'本机的画面说明',order:0,side:'after',contentId:null,paragraphId:null}}
const cacheKey='image-intent:'+encodeURIComponent(JSON.stringify(scope))+':cover::'
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,props:any,saved=vi.fn()
function mount(pendingValue:unknown=pending,changes:Record<string,unknown>={}){if(pendingValue)mock.values.set(cacheKey,structuredClone(pendingValue));props=reactive({scope,purpose:'cover',placement,...changes});root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h(ImageEditor,{...props,onSaved:saved})}));provideSfc(app);app.mount(root)}
function button(label:string){return descendants(root,n=>n.tag==='button'&&text(n)===label)[0]}
async function click(label:string){const item=button(label);expect(item).toBeDefined();expect(item.props.disabled).toBeFalsy();await item.props.onClick();await flush()}
function input(label:string){return descendants(root,n=>n.tag==='label'&&text(n).startsWith(label)).flatMap(n=>descendants(n,x=>x.tag==='input'))[0]}
beforeEach(()=>{mock.owner='owner';mock.epoch=0;mock.keys=0;mock.values.clear();mock.failClear=false;mock.store=reactive({user:{id:'owner'},online:true});saved=vi.fn();vi.mocked(api).mockReset().mockResolvedValue({found:false});vi.mocked(imagePlacements).mockReset();vi.mocked(imagesChanged).mockReset();vi.stubGlobal('window',new EventTarget());vi.stubGlobal('document',{activeElement:null});vi.stubGlobal('Image',class{naturalWidth=300;naturalHeight=450;onload?:()=>void;onerror?:()=>void;set src(_value:string){queueMicrotask(()=>this.onload?.())}});vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:preview');vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})})
afterEach(()=>{app?.unmount();app=undefined;vi.restoreAllMocks();vi.unstubAllGlobals()})
it('opens an unknown placement by pure receipt lookup and keeps the original uploaded asset',async()=>{mount();await flush();expect(api).toHaveBeenCalledExactlyOnceWith('/asset-placements/requests?requestId=original-place');expect(saved).not.toHaveBeenCalled();expect(mock.values.get(cacheKey)).toEqual(pending)})
it('shows a placement CAS conflict in the editor, refreshes explicitly without replacing the local description, and saves with the new version',async()=>{
  mount();await flush();vi.mocked(api).mockResolvedValueOnce({found:false}).mockRejectedValueOnce(new ApiError('ASSET_CONFLICT','另一设备已更新图片',409));await click('继续保存图片');expect(text(root)).toContain('另一设备');expect(saved).not.toHaveBeenCalled()
  vi.mocked(imagePlacements).mockResolvedValueOnce([{...placement,version:9,alt:'远端说明'}] as any);await click('载入最新信息');expect(input('图片说明').value).toBe('本机的画面说明');expect(vi.mocked(api).mock.calls.filter(([,opts])=>opts?.method==='POST')).toHaveLength(1)
  vi.mocked(api).mockResolvedValueOnce({...placement,assetId:'asset',version:10});await click('保存图片');expect(vi.mocked(api).mock.calls.at(-1)).toMatchObject(['/asset-placements',{method:'POST',body:{assetId:'asset',placementId:'placement',expectedVersion:9,alt:'本机的画面说明'}}]);expect(vi.mocked(api).mock.calls.filter(([path])=>path==='/assets')).toHaveLength(0);expect(saved).toHaveBeenCalledTimes(1)
})
it('persists a known rejected request and never retries it automatically after reopening',async()=>{
  mount();await flush();vi.mocked(api).mockResolvedValueOnce({found:false}).mockRejectedValueOnce(new ApiError('ASSET_CONFLICT','changed',409));await click('继续保存图片');app!.unmount();app=undefined;const calls=vi.mocked(api).mock.calls.length;mount(null);await flush();expect(vi.mocked(api).mock.calls).toHaveLength(calls);expect(button('载入最新信息')).toBeDefined();expect(mock.values.get(cacheKey)).toMatchObject({rejected:true,asset})
})
it('only retries removal after explicitly refreshing a conflicted placement version',async()=>{
  mount(null);await flush();vi.mocked(api).mockRejectedValueOnce(new ApiError('ASSET_CONFLICT','delete changed',409));await click('移除这处图片');expect(button('载入最新信息')).toBeDefined();vi.mocked(imagePlacements).mockResolvedValueOnce([{...placement,version:12}] as any);await click('载入最新信息');vi.mocked(api).mockResolvedValueOnce({deleted:true,placementId:'placement'});await click('移除这处图片');expect(vi.mocked(api).mock.calls.filter(([,opts])=>opts?.method==='DELETE').map(([,opts])=>opts?.body)).toEqual([{expectedVersion:4},{expectedVersion:12}]);expect(saved).toHaveBeenCalledTimes(1)
})
it('does not apply a late refresh after the user switches story or account',async()=>{
  mount();await flush();vi.mocked(api).mockResolvedValueOnce({found:false}).mockRejectedValueOnce(new ApiError('ASSET_CONFLICT','changed',409));await click('继续保存图片');const gate=deferred<any>();vi.mocked(imagePlacements).mockReturnValueOnce(gate.promise);const refreshing=click('载入最新信息');await flush();mock.owner='other';mock.epoch++;mock.store.user={id:'other'};gate.resolve([{...placement,version:9}]);await refreshing;expect(saved).not.toHaveBeenCalled();expect(button('保存图片')).toBeUndefined();expect(mock.values.get(cacheKey)).toMatchObject({rejected:true})
})
it('selects, explicitly crops and uploads actual File bytes only after rights confirmation',async()=>{
  mount(null);await flush();const file=new File([new Uint8Array([1,2,3])],'owned.png',{type:'image/png'});await input('选择封面').props.onChange({target:{files:[file]}});await flush();expect(descendants(root,n=>n.tag==='img'&&n.props.alt==='裁切预览')).toHaveLength(1);expect(api).not.toHaveBeenCalled();input('来源与许可').props['onUpdate:modelValue']('本人绘制，CC0');await flush();input('我有权使用').props['onUpdate:modelValue'](true);await flush();vi.mocked(api).mockResolvedValueOnce(asset).mockResolvedValueOnce({...placement,assetId:'asset'});await click('确认裁切并保存');const upload=vi.mocked(api).mock.calls[0];expect(upload[0]).toBe('/assets');const body=upload[1]?.body as FormData;expect(await (body.get('file') as File).arrayBuffer()).toEqual(await file.arrayBuffer());expect(JSON.parse(String(body.get('crop')))).toEqual({x:0,y:0,width:300,height:450});expect(body.get('rightsConfirmed')).toBe('true');expect(saved).toHaveBeenCalledTimes(1)
})
it('keeps a known committed placement on local cleanup failure and confirms by GET without another POST',async()=>{
  mount();await flush();mock.failClear=true;vi.mocked(api).mockResolvedValueOnce({found:false}).mockResolvedValueOnce({...placement,assetId:'asset'});await click('继续保存图片');expect(saved).not.toHaveBeenCalled();mock.failClear=false;vi.mocked(api).mockResolvedValueOnce({found:true,receipt:{...placement,assetId:'asset'}});await click('确认图片结果');expect(saved).toHaveBeenCalledTimes(1);expect(vi.mocked(api).mock.calls.filter(([,opts])=>opts?.method==='POST')).toHaveLength(1)
})

it('keeps an explicitly rejected file and crop editable, and only submits a changed crop after another user click',async()=>{
 mount(null);await flush();const file=new File([new Uint8Array([1,2,3])],'owned.png',{type:'image/png'});await input('选择封面').props.onChange({target:{files:[file]}});await flush();input('来源与许可').props['onUpdate:modelValue']('本人绘制');input('我有权使用').props['onUpdate:modelValue'](true);await flush();vi.mocked(api).mockRejectedValueOnce(new ApiError('IMAGE_TOO_LARGE','请缩小裁切',413));await click('确认裁切并保存');expect(text(root)).toContain('请缩小裁切');expect(mock.values.get(cacheKey)).toBeNull();expect(input('宽度')).toBeDefined();expect(vi.mocked(api).mock.calls).toHaveLength(1);input('宽度').props.onInput({target:{value:'150'}});await flush();vi.mocked(api).mockResolvedValueOnce(asset).mockResolvedValueOnce({...placement,assetId:'asset'});await click('确认裁切并保存');expect(JSON.parse(String((vi.mocked(api).mock.calls[1]![1]!.body as FormData).get('crop')))).toEqual({x:0,y:0,width:150,height:225});expect(saved).toHaveBeenCalledTimes(1)
})

it.each(['before','after'] as const)('preserves an existing %s paragraph placement when replacing its actual file',async(side)=>{
  mount(null,{purpose:'illustration',contentId:'content',paragraphId:'content',placement:{...placement,purpose:'illustration',contentId:'content',paragraphId:'content',side,order:6}});await flush()
  const file=new File([new Uint8Array([4,5,6])],'owned-replacement.png',{type:'image/png'});await input('选择段落插图').props.onChange({target:{files:[file]}});await flush();input('来源与许可').props['onUpdate:modelValue']('本人绘制');input('我有权使用').props['onUpdate:modelValue'](true);await flush()
  vi.mocked(api).mockResolvedValueOnce(asset).mockResolvedValueOnce({...placement,side,order:6});await click('确认裁切并保存')
  const body=vi.mocked(api).mock.calls.find(([path])=>path==='/asset-placements')![1]!.body
  expect(body).toMatchObject({side,order:6,contentId:'content',paragraphId:'content',placementId:'placement',expectedVersion:4});expect(saved).toHaveBeenCalledOnce()
})
it('drops an old replacement upload after the image scope switches before its response',async()=>{
  mount(null,{purpose:'illustration',contentId:'content',paragraphId:'content',placement:{...placement,purpose:'illustration',side:'before'}});await flush()
  await input('选择段落插图').props.onChange({target:{files:[new File([new Uint8Array([4,5,6])],'owned.png',{type:'image/png'})]}});await flush();input('来源与许可').props['onUpdate:modelValue']('本人绘制');input('我有权使用').props['onUpdate:modelValue'](true);await flush()
  const gate=deferred<any>();vi.mocked(api).mockReturnValueOnce(gate.promise);const saving=click('确认裁切并保存');await vi.waitFor(()=>expect(api).toHaveBeenCalledTimes(1))
  props.scope={...scope,bookId:'another-book'};props.placement=undefined;await flush();gate.resolve(asset);await saving
  expect(vi.mocked(api).mock.calls.filter(([path])=>path==='/asset-placements')).toHaveLength(0);expect(saved).not.toHaveBeenCalled();expect(imagesChanged).not.toHaveBeenCalled()
})
it.each(['before','after'] as const)('isolates an unknown %s placement from the opposite-side editor while retaining its original GET receipt',async(side)=>{
  const settings={purpose:'illustration',contentId:'content',paragraphId:'content',side,placement:{...placement,id:'placement-'+side,purpose:'illustration',side}}
  mount(null,settings);await flush();await input('选择段落插图').props.onChange({target:{files:[new File([new Uint8Array([4,5,6])],'owned.png',{type:'image/png'})]}});await flush();input('来源与许可').props['onUpdate:modelValue']('本人绘制');input('我有权使用').props['onUpdate:modelValue'](true);await flush()
  vi.mocked(api).mockResolvedValueOnce(asset).mockRejectedValueOnce(new Error('unknown response'));await click('确认裁切并保存')
  const request=vi.mocked(api).mock.calls.find(([path])=>path==='/asset-placements')![1]!.key;expect(request).toBeTruthy();app!.unmount();app=undefined
  vi.mocked(api).mockClear().mockResolvedValue({found:false});mount(null,{...settings,side:side==='before'?'after':'before',placement:undefined});await flush()
  expect(api).not.toHaveBeenCalled();expect(text(root)).not.toContain('有一份图片提交待确认');app!.unmount();app=undefined
  mount(null,settings);await flush();expect(api).toHaveBeenCalledExactlyOnceWith('/asset-placements/requests?requestId='+request);expect(text(root)).toContain('有一份图片提交待确认');expect(vi.mocked(api).mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0)
})
it('does not apply an old placement receipt when only the editor side changes during lookup',async()=>{
  const settings={purpose:'illustration',contentId:'content',paragraphId:'content',side:'after',placement:{...placement,purpose:'illustration'}}
  const shared='image-intent:'+encodeURIComponent(JSON.stringify(scope))+':illustration:content:content'
  mock.values.set(shared,{...pending,purpose:'illustration',body:{...pending.body,purpose:'illustration',contentId:'content',paragraphId:'content'}})
  const gate=deferred<any>();vi.mocked(api).mockReturnValueOnce(gate.promise);mount(null,settings);await flush();expect(api).toHaveBeenCalledExactlyOnceWith('/asset-placements/requests?requestId=original-place')
  props.side='before';props.placement=undefined;await flush();gate.resolve({found:true,receipt:{...placement,purpose:'illustration',contentId:'content',paragraphId:'content',side:'after'}});await flush()
  expect(saved).not.toHaveBeenCalled();expect(imagesChanged).not.toHaveBeenCalled();expect(mock.values.get(shared)).toBeDefined();expect(text(root)).not.toContain('有一份图片提交待确认')
})
it('keeps a decoded selected image and local description through equivalent scope and owner renders',async()=>{
  mount(null);await flush();const file=new File([new Uint8Array([4,5,6])],'owned.png',{type:'image/png'})
  await input('选择封面').props.onChange({target:{files:[file]}});await flush();input('图片说明').props['onUpdate:modelValue']('本机裁切草稿');input('来源与许可').props['onUpdate:modelValue']('本人绘制，CC0');input('我有权使用').props['onUpdate:modelValue'](true);await flush()
  props.scope={...scope};mock.store.user={id:'owner'};await flush()
  expect(descendants(root,n=>n.tag==='img'&&n.props.alt==='裁切预览')).toHaveLength(1);expect(input('图片说明').value).toBe('本机裁切草稿');expect(button('确认裁切并保存').props.disabled).toBeFalsy()
  vi.mocked(api).mockResolvedValueOnce(asset).mockResolvedValueOnce({...placement,assetId:'asset'});await click('确认裁切并保存')
  const form=vi.mocked(api).mock.calls.find(([path])=>path==='/assets')![1]!.body as FormData;expect(await(form.get('file') as File).arrayBuffer()).toEqual(await file.arrayBuffer());expect(vi.mocked(api).mock.calls.find(([path])=>path==='/asset-placements')![1]!.body).toMatchObject({alt:'本机裁切草稿'});expect(saved).toHaveBeenCalledOnce()
})
it.each(['revision','account'] as const)('clears decoded private image bytes when the actual %s changes',async(change)=>{
  mount(null);await flush();await input('选择封面').props.onChange({target:{files:[new File([new Uint8Array([4,5,6])],'owned.png',{type:'image/png'})]}});await flush()
  expect(descendants(root,n=>n.tag==='img'&&n.props.alt==='裁切预览')).toHaveLength(1)
  if(change==='revision')props.scope={...scope,revisionId:'different-revision'};else{mock.owner='other';mock.epoch++;mock.store.user={id:'other'}}
  await flush();expect(descendants(root,n=>n.tag==='img'&&n.props.alt==='裁切预览')).toHaveLength(0);expect(button('确认裁切并保存').props.disabled).toBeTruthy();expect(api).not.toHaveBeenCalled();expect(saved).not.toHaveBeenCalled()
})
