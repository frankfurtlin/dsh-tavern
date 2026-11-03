import {it,expect,vi} from 'vitest'
import {comparisonPath,createComparisonController,type ComparisonQuery,type ComparisonSnapshot,type ComparisonRevealIntent} from './comparison'
const deferred=<T>()=>{let resolve!:(v:T)=>void;const promise=new Promise<T>(done=>resolve=done);return{promise,resolve}}
const query=(right='b'):ComparisonQuery=>({storyId:'world-root',kind:'world',resourceId:'w',left:{id:'a',name:'同名',nodeId:'cpA'},right:{id:right,name:'同名',nodeId:'cp'+right},alignment:'latest'})
const result=(q:ComparisonQuery)=>({protocol:'reader-compare-v1',storyId:q.storyId,kind:q.kind,alignment:q.alignment,atMinute:null,common:{nodeId:'cp0',visible:true,title:'共同起点'},left:{lineId:q.left.id,requestedNodeId:q.left.nodeId},right:{lineId:q.right.id,requestedNodeId:q.right.nodeId}} as ComparisonSnapshot)
const intent:ComparisonRevealIntent={requestId:'original-key',storyId:'world-root',kind:'world',resourceId:'w',lineId:'b',nodeId:'cpb',expectedVersion:0}
it('does not let a late A response overwrite the selected B comparison',async()=>{
 const old=deferred<ComparisonSnapshot>(),get=vi.fn().mockImplementationOnce(()=>old.promise).mockImplementationOnce(async(q)=>result(q)),c=createComparisonController({get,reveal:vi.fn(),lookup:vi.fn(),persist:vi.fn(),active:()=>true,unknown:()=>false})
 const a=c.load(query()),b=await c.load(query('c'));old.resolve(result(query()));expect(await a).toBeNull();expect(c.snapshot).toBe(b);expect(c.snapshot?.right.lineId).toBe('c')
})
it('reopens an unknown write through the same pure lookup without a second POST',async()=>{
 const saved:(ComparisonRevealIntent|null)[]=[],reveal=vi.fn().mockRejectedValue(new Error('response lost')),lookup=vi.fn().mockResolvedValueOnce({found:false,result:null}).mockResolvedValueOnce({found:true,result:{protocol:'reader-compare-v1',kind:'world',resourceId:'w',lineId:'b',nodeId:'cpb',version:1,anchor:{checkpointId:'cpb'}}}),c=createComparisonController({get:vi.fn(),reveal,lookup,persist:async(v)=>{saved.push(v)},active:()=>true,unknown:()=>true})
 await expect(c.reveal(intent)).rejects.toThrow('response lost');expect(c.pending).toBe(intent);expect(await c.recover()).toBeNull();expect(c.pending).toBe(intent);expect(await c.recover()).toMatchObject({version:1});expect(c.pending).toBeNull();expect(reveal).toHaveBeenCalledTimes(1);expect(lookup).toHaveBeenCalledTimes(2);expect(lookup).toHaveBeenLastCalledWith(intent);expect(saved).toEqual([intent,null])
})
it('rejects mismatching route/line data and ignores account-late completion',async()=>{
 let active=true;const old=deferred<ComparisonSnapshot>(),c=createComparisonController({get:()=>old.promise,reveal:vi.fn(),lookup:vi.fn(),persist:vi.fn(),active:()=>active,unknown:()=>false});const pending=c.load(query());active=false;old.resolve(result(query()));expect(await pending).toBeNull();expect(c.snapshot).toBeNull()
 const other=createComparisonController({get:async()=>result(query('different')),reveal:vi.fn(),lookup:vi.fn(),persist:vi.fn(),active:()=>true,unknown:()=>false});await expect(other.load(query())).rejects.toThrow('比较路径已经变化')
})
it('pins exact IDs and explicitly requests actual-world-time alignment',()=>{
 const q={...query(),alignment:'same-time' as const,atMinute:42};const path=comparisonPath(q);expect(path).toContain('leftLineId=a');expect(path).toContain('rightLineId=b');expect(path).toContain('leftNodeId=cpA');expect(path).toContain('atMinute=42');expect(path).not.toContain('同名')
})
it('retains the original request when a success body belongs to another path',async()=>{
 const persist=vi.fn(),c=createComparisonController({get:vi.fn(),reveal:async()=>({protocol:'reader-compare-v1',kind:'world',resourceId:'w',lineId:'c',nodeId:'cp-c',version:1,anchor:{checkpointId:'cp-c'}}),lookup:vi.fn(),persist,active:()=>true,unknown:()=>false})
 await expect(c.reveal(intent)).rejects.toThrow('回执');expect(c.pending).toBe(intent);expect(persist).toHaveBeenCalledExactlyOnceWith(intent)
})
