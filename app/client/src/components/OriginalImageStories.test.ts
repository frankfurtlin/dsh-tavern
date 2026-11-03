import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {reactive,defineComponent,h,onUnmounted} from 'vue'
import OriginalImageStories from './OriginalImageStories.vue'
import {api} from '../api'
import {compileRender,renderer,node,text,flush,deferred,provideSfc,descendants,type Host} from '../test/host-renderer'
const mock=vi.hoisted(()=>({owner:'owner',base:'https://fixture.invalid',epoch:0,values:new Map<string,unknown>(),keys:0,push:vi.fn(),store:null as any}))
vi.mock('vue-router',()=>({useRouter:()=>({push:mock.push})}))
vi.mock('../api',()=>({api:vi.fn(),newKey:()=> 'request-'+(++mock.keys)}))
vi.mock('../api/assets',()=>({imageCapabilities:async()=>({ready:true})}))
vi.mock('../platform/bridge',()=>({accountId:()=>mock.owner,backendBase:()=>mock.base,cacheGet:async(key:string)=>structuredClone(mock.values.get(key)),cacheSet:async(key:string,value:unknown)=>{mock.values.set(key,JSON.parse(JSON.stringify(value)))}}))
vi.mock('../platform/request-scope',()=>({useRequestScope:()=>{let disposed=false;onUnmounted(()=>disposed=true);return()=>{const epoch=mock.epoch,owner=mock.owner;return{valid:()=>!disposed&&epoch===mock.epoch&&owner===mock.owner}}}}))
vi.mock('../stores/app',()=>({useApp:()=>mock.store}))
compileRender(OriginalImageStories,new URL('./OriginalImageStories.vue',import.meta.url))
const entries={items:[{id:'original-light-letter',title:'灯下的回信',description:'自编故事',imageCount:6,license:'CC0-1.0'}]},receipt={bookId:'a'.repeat(32),branchId:'b'.repeat(32)},cacheKey='original-image-story-intent:v1'
let app:ReturnType<typeof renderer.createApp>|undefined,root:Host,installed=vi.fn()
function mount(){root=node('root');app=renderer.createApp(defineComponent({setup:()=>()=>h(OriginalImageStories,{onInstalled:installed})}));provideSfc(app);app.mount(root)}
async function click(label:string){const row=descendants(root,n=>n.tag==='button'&&text(n)===label)[0];expect(row).toBeDefined();expect(row.props.disabled).toBeFalsy();await row.props.onClick();await flush()}
beforeEach(()=>{mock.owner='owner';mock.epoch=0;mock.values.clear();mock.keys=0;mock.push.mockReset();mock.store=reactive({online:true});installed=vi.fn();vi.mocked(api).mockReset().mockResolvedValue(entries);vi.stubGlobal('window',new EventTarget())})
afterEach(()=>{app?.unmount();app=undefined;vi.unstubAllGlobals()})
it('only lists authored licensed bundles on mount; it never installs a book or requests generation implicitly',async()=>{mount();await flush();expect(text(root)).toContain('CC0');expect(api).toHaveBeenCalledExactlyOnceWith('/assets/examples');expect(installed).not.toHaveBeenCalled();expect(mock.push).not.toHaveBeenCalled()})
it('restores an unknown install by pure lookup and submits only after another explicit click with its saved key',async()=>{
  mock.values.set(cacheKey,{id:'original-light-letter',key:'previous-key'});vi.mocked(api).mockResolvedValueOnce(entries).mockResolvedValueOnce({found:false});mount();await flush();expect(vi.mocked(api).mock.calls.every(([,options])=>options===undefined)).toBe(true);expect(text(root)).toContain('待确认');vi.mocked(api).mockResolvedValueOnce({found:false}).mockResolvedValueOnce(receipt);await click('加入这套图文故事');expect(vi.mocked(api).mock.calls.at(-1)).toEqual(['/assets/examples/original-light-letter/install',{method:'POST',body:{},key:'previous-key'}]);expect(installed).toHaveBeenCalledTimes(1);expect(mock.push).toHaveBeenCalledExactlyOnceWith('/stories/book-'+receipt.bookId)
})
it('reopens a response-lost install by pure receipt lookup without repeating the installation',async()=>{
  mount();await flush();vi.mocked(api).mockRejectedValueOnce(new Error('response lost'));await click('加入这套图文故事');expect(mock.values.get(cacheKey)).toEqual({id:'original-light-letter',key:'request-1'});app!.unmount();app=undefined;vi.mocked(api).mockResolvedValueOnce(entries).mockResolvedValueOnce({found:true,receipt});mount();await flush();expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1);expect(mock.values.get(cacheKey)).toBeNull();expect(installed).toHaveBeenCalledTimes(1)
})
it('does not navigate or emit a late install receipt to another signed-in owner',async()=>{
  mount();await flush();const reply=deferred<any>();vi.mocked(api).mockReturnValueOnce(reply.promise);const installing=click('加入这套图文故事');await flush();mock.owner='other';mock.epoch++;reply.resolve(receipt);await installing;expect(installed).not.toHaveBeenCalled();expect(mock.push).not.toHaveBeenCalled();expect(mock.values.get(cacheKey)).toEqual({id:'original-light-letter',key:'request-1'})
})
