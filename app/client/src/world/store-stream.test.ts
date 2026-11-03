import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useWorld} from './store'
const mock=vi.hoisted(()=>({api:vi.fn(),events:vi.fn(),epoch:0}))
vi.mock('../api',()=>({authGeneration:()=>mock.epoch,newKey:()=> 'not-used'}))
vi.mock('./api',()=>({worldApi:mock.api,worldEvents:mock.events}))
vi.mock('../platform/bridge',()=>({cacheGet:vi.fn(async()=>null),cacheSet:vi.fn(async()=>{})}))
const initial={id:'task',type:'world_turn',state:'running',persistenceState:'pending',eventSeq:1,result:{},stream:{text:'你😀',stage:'generation:scene:1',version:1,contentId:'content',chapterIndex:1,streamVersion:1,snapshotVersion:1,durability:'journaled'}}
const event=(seq=2,more={})=>({taskId:'task',seq,type:'text_delta',payload:{contentId:'content',chapterIndex:1,streamVersion:1,startUtf16:3,delta:'好',durability:'journaled',...more}})
beforeEach(()=>{setActivePinia(createPinia());vi.useFakeTimers();vi.stubGlobal('window',new EventTarget());mock.epoch=0;mock.api.mockReset().mockResolvedValue(initial);mock.events.mockReset().mockResolvedValue(undefined)})
afterEach(()=>{useWorld().stopWatching();vi.useRealTimers();vi.unstubAllGlobals()})
async function start(){const store=useWorld();store.world={id:'world'} as any;store.line={id:'line',headCheckpointId:'formal'} as any;store.input='下一行动';await store.watchTask('task');return store}
it('world direct delta appends once, preserves action/head and avoids per-delta GET',async()=>{
  const store=await start(),callback=mock.events.mock.calls[0][3];callback(event());callback(event())
  expect(store.task?.stream.text).toBe('你😀好');expect(store.input).toBe('下一行动');expect(store.line?.headCheckpointId).toBe('formal');expect(mock.api).toHaveBeenCalledOnce()
})
it('world epoch/gap obtains a snapshot and ignores obsolete owner event',async()=>{
  const store=await start(),callback=mock.events.mock.calls[0][3];callback(event(4));await Promise.resolve();await Promise.resolve();expect(mock.api).toHaveBeenCalledTimes(2);expect(store.task?.stream.text).toBe('你😀')
  mock.epoch++;callback(event());expect(store.task?.stream.text).toBe('你😀');expect(store.input).toBe('下一行动')
})

it('world loads every restored draft page without generation or advancing the formal head',async()=>{
  const records=Array.from({length:21},(_,i)=>({id:'history-'+i,status:'historical',draftText:'已收到正文'+i,chapters:[]}))
  mock.api.mockImplementation(async(path:string)=>{
    if(path==='/worlds/world')return {id:'world',activeLineId:'line'}
    if(path==='/worlds/world/lines/line')return {id:'line',headCheckpointId:'formal',pendingTaskId:null,head:{state:{}}}
    if(path.endsWith('/history'))return {items:[]}
    if(path.endsWith('/drafts'))return {items:records.slice(0,20),nextCursor:'older'}
    if(path.endsWith('/drafts?cursor=older'))return {items:records.slice(20),nextCursor:null}
    throw new Error('Unexpected read '+path)
  })
  const store=useWorld();await store.load('world','line');expect(store.drafts).toHaveLength(21);expect(store.drafts.at(-1)?.draftText).toBe('已收到正文20');expect(store.line?.headCheckpointId).toBe('formal');expect(store.task).toBeNull();expect(mock.events).not.toHaveBeenCalled();expect(mock.api.mock.calls.every(([,options])=>!options?.method)).toBe(true)
})
