import {beforeEach,expect,it,vi} from 'vitest'
const fixture=vi.hoisted(()=>({cache:new Map<string,unknown>(),writeError:false,next:0}))
vi.mock('../api',()=>({newKey:()=> 'intent-'+(++fixture.next)}))
vi.mock('./api',()=>({worldApi:vi.fn()}))
vi.mock('../platform/bridge',()=>({
  cacheGet:async(key:string)=>structuredClone(fixture.cache.get(key)),
  cacheUpdate:async(key:string,update:(value:unknown)=>unknown,valid:()=>boolean)=>{
    if(fixture.writeError)throw new Error('disk full')
    if(!valid())return undefined
    const result=update(structuredClone(fixture.cache.get(key)))
    fixture.cache.set(key,structuredClone(result));return result
  }
}))
import {worldApi} from './api'
import {worldMutation,pendingWorldMutation,clearWorldMutation,clearConfirmedWorldMutation} from './mutation-intent'
beforeEach(()=>{fixture.cache.clear();fixture.writeError=false;fixture.next=0;vi.mocked(worldApi).mockReset()})
it('retires an old confirmed receipt for a new command without discarding an uncertain intent',async()=>{
  vi.mocked(worldApi).mockResolvedValueOnce({id:'line-a'}).mockResolvedValueOnce({id:'line-b'})
  await worldMutation('fork:world','/worlds/world/fork',{checkpointId:'a'},()=>true)
  await clearConfirmedWorldMutation('fork:world',()=>true)
  await worldMutation('fork:world','/worlds/world/fork',{checkpointId:'b'},()=>true)
  expect(worldApi).toHaveBeenCalledTimes(2);expect(fixture.next).toBe(2)
  vi.mocked(worldApi).mockRejectedValueOnce(new Error('unknown'))
  await expect(worldMutation('rewrite:world','/worlds/world/rewrite',{checkpointId:'c'},()=>true)).rejects.toThrow()
  const pending=await pendingWorldMutation('rewrite:world')
  await clearConfirmedWorldMutation('rewrite:world',()=>true)
  expect(await pendingWorldMutation('rewrite:world')).toEqual(pending)
})
it('keeps even a confirmed receipt when the account scope changed',async()=>{
  vi.mocked(worldApi).mockResolvedValueOnce({id:'line-a'})
  await worldMutation('fork:world','/worlds/world/fork',{checkpointId:'a'},()=>true)
  const pending=await pendingWorldMutation('fork:world')
  await clearConfirmedWorldMutation('fork:world',()=>false)
  expect(await pendingWorldMutation('fork:world')).toEqual(pending)
})
it('replays the same key and payload after a committed POST loses its response',async()=>{
  vi.mocked(worldApi).mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({worldId:'one'})
  const body={name:'原操作',rules:{genre:'mystery'}}
  await expect(worldMutation('create','/worlds',body,()=>true)).rejects.toThrow('response lost')
  expect((await pendingWorldMutation('create'))?.key).toBe('intent-1')
  // Re-import represents a new page instance; storage outlives the component.
  vi.resetModules();const reloaded=await import('./mutation-intent')
  await expect(reloaded.worldMutation('create','/worlds',body,()=>true)).resolves.toEqual({worldId:'one'})
  const calls=vi.mocked(worldApi).mock.calls
  expect(calls).toHaveLength(2);expect(calls[0][1]).toEqual(calls[1][1]);expect(fixture.next).toBe(1)
})
it('does not send changed input while an earlier operation is unconfirmed',async()=>{
  vi.mocked(worldApi).mockRejectedValue(new Error('response lost'))
  await expect(worldMutation('create','/worlds',{name:'A'},()=>true)).rejects.toThrow()
  await expect(worldMutation('create','/worlds',{name:'B'},()=>true)).rejects.toThrow('先恢复')
  expect(worldApi).toHaveBeenCalledTimes(1)
})
it('does not send anything if intent persistence fails',async()=>{
  fixture.writeError=true
  await expect(worldMutation('begin:w','/worlds/w/begin',{},()=>true)).rejects.toThrow('disk full')
  expect(worldApi).not.toHaveBeenCalled()
})
it('does not send when the captured account or route is no longer valid',async()=>{
  await expect(worldMutation('fork:w','/worlds/w/fork',{},()=>false)).rejects.toThrow('页面已变化')
  expect(worldApi).not.toHaveBeenCalled();expect(fixture.cache.size).toBe(0)
})
it('keeps fork and rewrite intents independent and clears only a confirmed slot',async()=>{
  vi.mocked(worldApi).mockResolvedValue({})
  await worldMutation('fork:w','/worlds/w/fork',{checkpointId:'c'},()=>true)
  await worldMutation('rewrite:w','/worlds/w/rewrite',{checkpointId:'c'},()=>true)
  expect((await pendingWorldMutation('fork:w'))?.key).not.toBe((await pendingWorldMutation('rewrite:w'))?.key)
  await clearWorldMutation('fork:w',()=>true)
  expect(await pendingWorldMutation('fork:w')).toBeUndefined();expect(await pendingWorldMutation('rewrite:w')).toBeDefined()
})
it('entering another world cannot clear an unrelated creation receipt',async()=>{
  vi.mocked(worldApi).mockResolvedValue({worldId:'world-a'})
  await worldMutation('create','/worlds',{name:'A'},()=>true)
  await clearWorldMutation('create',()=>true,'world-b')
  expect((await pendingWorldMutation('create'))?.receipt?.worldId).toBe('world-a')
  await clearWorldMutation('create',()=>true,'world-a')
  expect(await pendingWorldMutation('create')).toBeUndefined()
})
it('a late scene refresh cannot clear a newer movement intent',async()=>{
  vi.mocked(worldApi).mockResolvedValue({worldId:'world-a',checkpointId:'checkpoint-a'})
  await worldMutation('scene-move:a','/worlds/a/scene/move',{targetId:'left'},()=>true)
  const old=(await pendingWorldMutation('scene-move:a'))!
  await clearWorldMutation('scene-move:a',()=>true,'world-a',old.key)
  await worldMutation('scene-move:a','/worlds/a/scene/move',{targetId:'right'},()=>true)
  await clearWorldMutation('scene-move:a',()=>true,'world-a',old.key)
  expect((await pendingWorldMutation('scene-move:a'))?.body).toEqual({targetId:'right'})
})
it('exposes the exact persisted key on a new operation and its cached replay',async()=>{
  vi.mocked(worldApi).mockResolvedValue({worldId:'world-a',checkpointId:'checkpoint-a'})
  const keys:string[]=[]
  await worldMutation('scene-move:a','/worlds/a/scene/move',{targetId:'left'},()=>true,intent=>keys.push(intent.key))
  await worldMutation('scene-move:a','/worlds/a/scene/move',{targetId:'left'},()=>true,intent=>keys.push(intent.key))
  expect(keys).toEqual(['intent-1','intent-1']);expect(worldApi).toHaveBeenCalledTimes(1)
})
