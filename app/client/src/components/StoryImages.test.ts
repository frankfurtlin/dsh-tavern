import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h,createRenderer} from 'vue'
import StoryImages from './StoryImages.vue'
import {imagePlacements,imageSource} from '../api/assets'
import {compileRender,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
vi.mock('../api/image-changes',()=>({recordImageChange:vi.fn().mockResolvedValue(undefined),currentImagePlacements:async(rows:unknown)=>rows}))
const mock=vi.hoisted(()=>({owner:'owner',epoch:0,store:null as any}))
vi.mock('../api/assets',()=>({imageCapabilities:async()=>({ready:true}),imagePlacements:vi.fn(),imageSource:vi.fn(),preferredVariant:()=> 'reading',imageScopeKey:JSON.stringify,sameImageRoot:(a:any,b:any)=>a.worldId===b.worldId&&a.lineId===b.lineId,captureImageScope:(_scope:any,active:()=>boolean)=>{const owner=mock.owner,epoch=mock.epoch;return()=>owner===mock.owner&&epoch===mock.epoch&&active()}}))
vi.mock('../stores/app',()=>({useApp:()=>mock.store}))
vi.mock('./Sheet.vue',()=>({default:defineComponent({setup:(_,{slots})=>()=>h('aside',slots.default?.())})}))
vi.mock('./ImageEditor.vue',()=>({default:defineComponent({setup:()=>()=>h('section')})}))
compileRender(StoryImages,new URL('./StoryImages.vue',import.meta.url))
let root:Host,body:Host,app:ReturnType<typeof local.createApp>|undefined,props:any,layout=vi.fn()
// This host supports real Vue Teleport placement and focus calls, but has no layout, image decode or browser geometry.
const local=createRenderer<Host,Host>({
 createElement:tag=>Object.assign(node(tag),{focus:()=>{}}),createText:value=>node('#text',value),createComment:value=>node('#comment',value),
 setText:(n,value)=>{n.text=value},setElementText:(n,value)=>{n.text=value;n.children=[]},patchProp:(n,key,_old,value)=>{n.props[key]=value},
 insert:(n,parent,anchor)=>{if(n.parent){const at=n.parent.children.indexOf(n);if(at>=0)n.parent.children.splice(at,1)}n.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;if(at<0)parent.children.push(n);else parent.children.splice(at,0,n)},
 remove:n=>{if(n.parent){const at=n.parent.children.indexOf(n);if(at>=0)n.parent.children.splice(at,1)}n.parent=null},parentNode:n=>n.parent,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null,querySelector:()=>body
})
const scope={kind:'world' as const,worldId:'world',lineId:'line',checkpointId:'one'}
const row=(id:string,cp='one')=>({id,scope:{...scope,checkpointId:cp},purpose:'scene' as const,assetId:id,contentId:null,paragraphId:null,side:'after' as const,order:0,alt:'图片'+id,version:1,asset:{assetId:id,ownerId:'owner',scope,licenseNote:'本人绘制',variants:{},sourceKind:'user_upload',sha256:'fixture-hash',mime:'image/png',width:100,height:80,createdAt:'fixture',version:1,referenceCount:1}})
function mount(changes:Record<string,unknown>={}){root=node('root');body=node('body');props=reactive({scope,purpose:'scene',foldable:true,...changes});app=local.createApp(defineComponent({setup:()=>()=>h('article',[h('p','甲😀乙，原文不受图片展开影响。'),h(StoryImages,{...props,onLayout:layout})])}));provideSfc(app);app.mount(root)}
async function click(label:string,target=root){const button=descendants(target,n=>n.tag==='button'&&(text(n)===label||n.props['aria-label']===label))[0];expect(button).toBeDefined();await button.props.onClick();await flush()}
beforeEach(()=>{mock.owner='owner';mock.epoch=0;mock.store=reactive({online:true,user:{id:'owner'}});layout=vi.fn();vi.mocked(imagePlacements).mockReset().mockResolvedValue([row('a'),row('b')]);vi.mocked(imageSource).mockReset().mockImplementation(async(asset:any)=>'data:fixture-'+asset.assetId);vi.stubGlobal('window',new EventTarget());vi.stubGlobal('HTMLElement',class{});vi.stubGlobal('document',{activeElement:null})})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
it('folds only the image while preserving actual surrounding UTF16 prose, and reopening performs no mutation',async()=>{mount();await flush();expect(descendants(root,n=>n.tag==='img')).toHaveLength(1);await click('收起图片');expect(descendants(root,n=>n.tag==='img')).toHaveLength(0);expect(text(root)).toContain('甲😀乙，原文不受图片展开影响。');await click('显示图片');expect(descendants(root,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-a');expect(imagePlacements).toHaveBeenCalledTimes(1);expect(layout).toHaveBeenCalledTimes(3)})
it('uses the actual lightbox template for next and close without changing prose',async()=>{mount();await flush();await click('放大图片a');expect(descendants(body,n=>n.props.role==='dialog')).toHaveLength(1);await click('下一张图片',body);expect(descendants(body,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-b');await click('关闭图片预览',body);expect(descendants(body,n=>n.props.role==='dialog')).toHaveLength(0);expect(text(root)).toContain('甲😀乙')})
it('does not display late bytes from a previous checkpoint after a scope change',async()=>{const late=deferred<string>();vi.mocked(imageSource).mockReturnValueOnce(late.promise);mount();await flush();vi.mocked(imagePlacements).mockResolvedValue([row('new','two')] as any);props.scope={...scope,checkpointId:'two'};await flush();late.resolve('data:old-private');await flush();expect(descendants(root,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-new');expect(descendants(root,n=>n.tag==='img').some(n=>n.props.src==='data:old-private')).toBe(false)})

it('keeps the selected large picture when the previous large-image GET completes later',async()=>{mount();await flush();const late=deferred<string>();vi.mocked(imageSource).mockReturnValueOnce(late.promise);const first=descendants(root,n=>n.tag==='button'&&n.props['aria-label']==='放大图片a')[0]!.props.onClick();await flush();await click('下一张图片',body);late.resolve('data:late-a');await first;await flush();expect(descendants(body,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-b')})
it('keeps loaded images without another GET when layout rerenders pass an equivalent scope object',async()=>{mount();await flush();expect(imagePlacements).toHaveBeenCalledOnce();const count=layout.mock.calls.length;props.scope={...scope};await flush();expect(imagePlacements).toHaveBeenCalledOnce();expect(layout.mock.calls.length).toBe(count);expect(descendants(root,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-a')})
it('keeps the selected picture attribution when its rendered bytes fail instead of borrowing the first picture',async()=>{
  vi.mocked(imagePlacements).mockResolvedValue([row('a'),{...row('b'),asset:{...row('b').asset,licenseNote:'第二张图片许可'}}] as any)
  mount();await flush();await click('放大图片a');await click('下一张图片',body)
  const selected=descendants(body,n=>n.tag==='img')[0]!
  expect(selected.props.src).toBe('data:fixture-b');expect(selected.props.alt).toBe('图片b')
  await selected.props.onError();await flush()
  expect(descendants(body,n=>n.tag==='img')).toHaveLength(0)
  expect(descendants(root,n=>n.tag==='img')).toHaveLength(0)
  expect(text(body)).toContain('图片b');expect(text(body)).toContain('第二张图片许可')
  expect(text(root)).toContain('甲😀乙，原文不受图片展开影响。')
  await click('上一张图片',body);expect(descendants(body,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-a')
})
it('keeps an empty selected picture slot as text fallback without showing the first picture under its metadata',async()=>{
  vi.mocked(imagePlacements).mockResolvedValue([row('a'),{...row('b'),asset:{...row('b').asset,licenseNote:'第二张图片许可'}}] as any)
  vi.mocked(imageSource).mockImplementation(async(asset:any)=>asset.assetId==='b'?'':'data:fixture-'+asset.assetId)
  mount();await flush();expect(descendants(root,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-a')
  await click('放大图片a');await click('下一张图片',body)
  expect(descendants(body,n=>n.tag==='img')).toHaveLength(0)
  expect(descendants(root,n=>n.tag==='img')).toHaveLength(0)
  expect(text(body)).toContain('图片b');expect(text(body)).toContain('第二张图片许可')
  expect(text(root)).toContain('甲😀乙，原文不受图片展开影响。')
  await click('上一张图片',body);expect(descendants(body,n=>n.tag==='img')[0]?.props.src).toBe('data:fixture-a')
})
it('shows only the after add-image control when an editable paragraph has no before placement',async()=>{
  vi.mocked(imagePlacements).mockResolvedValue([]);mount({purpose:'illustration',side:'before',contentId:'one',paragraphId:'0',editable:true});await flush()
  expect(descendants(root,n=>n.tag==='button'&&n.props['aria-label']==='设置段落插图')).toHaveLength(0)
  props.side='after';await flush();expect(descendants(root,n=>n.tag==='button'&&n.props['aria-label']==='设置段落插图')).toHaveLength(1)
})
it('keeps an existing matching before placement editable when its image bytes fail, without treating an unrelated after image as before',async()=>{
  const before={...row('before'),purpose:'illustration',side:'before',contentId:'one',paragraphId:'0'},after={...row('after'),purpose:'illustration',side:'after',contentId:'one',paragraphId:'0'}
  vi.mocked(imagePlacements).mockResolvedValue([before,after] as any);vi.mocked(imageSource).mockRejectedValue(new Error('missing bytes'))
  mount({purpose:'illustration',side:'before',contentId:'one',paragraphId:'0',editable:true});await flush()
  expect(descendants(root,n=>n.tag==='button'&&n.props['aria-label']==='设置段落插图')).toHaveLength(1);expect(imageSource).toHaveBeenCalledOnce();expect(vi.mocked(imageSource).mock.calls[0][0].assetId).toBe('before')
  vi.mocked(imagePlacements).mockResolvedValue([after] as any);props.scope={...scope,checkpointId:'two'};await flush();expect(descendants(root,n=>n.tag==='button'&&n.props['aria-label']==='设置段落插图')).toHaveLength(0)
})
