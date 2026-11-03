import {afterEach,beforeEach,expect,it,vi,type Mock} from 'vitest'
import {PanoramaMediaController,type PanoramaRevision,type PanoramaSurface,type PanoramaMediaState} from './panorama-media'
const revision=(id:string,scope='checkpoint-A'):PanoramaRevision=>({scope,revision:id,url:'/image/'+id,interactionsVerified:false})
const deferred=<T>()=>{let resolve!:(v:T)=>void;const promise=new Promise<T>(r=>resolve=r);return {promise,resolve}}
let states:PanoramaMediaState[],surface:PanoramaSurface,create:Mock<(url:string)=>Promise<PanoramaSurface>>,download:Mock<(url:string,signal:AbortSignal)=>Promise<Blob>>,media:PanoramaMediaController
beforeEach(()=>{
  let id=0
  vi.stubGlobal('URL',{createObjectURL:()=> 'blob:'+(++id),revokeObjectURL:vi.fn()})
  states=[];surface={replace:vi.fn(async()=>true),destroy:vi.fn()}
  create=vi.fn(async()=>surface);download=vi.fn(async()=>new Blob(['actual-image']))
  media=new PanoramaMediaController(create,s=>states.push(s),download)
})
afterEach(()=>{media.dispose();vi.unstubAllGlobals()})
it('keeps one surface during a progressive update and enables interactions only for the committed verified revision',async()=>{
  await media.show(revision('base'))
  expect(states.at(-1)?.interactionsEnabled).toBe(false)
  await media.show({...revision('complete'),interactionsVerified:true})
  expect(create).toHaveBeenCalledTimes(1)
  expect(surface.replace).toHaveBeenCalledWith('blob:2')
  expect(states.at(-1)?.shown?.revision).toBe('complete')
  expect(states.at(-1)?.interactionsEnabled).toBe(true)
})
it('retains the actual previous texture when an upgrade fails during rendering',async()=>{
  await media.show(revision('base'))
  vi.mocked(surface.replace).mockRejectedValueOnce(new Error('GPU texture failed'))
  expect(await media.show(revision('upgrade'))).toBe(false)
  expect(surface.replace).toHaveBeenLastCalledWith('blob:1')
  expect(states.at(-1)?.phase).toBe('retained')
  expect(states.at(-1)?.shown?.revision).toBe('base')
})
it('discards a late response and never replaces the latest image with stale bytes',async()=>{
  const slow=deferred<Blob>()
  download.mockImplementationOnce(()=>slow.promise)
  const first=media.show(revision('old'))
  await media.show(revision('new'))
  slow.resolve(new Blob(['old']));await first
  expect(states.at(-1)?.shown?.revision).toBe('new')
  expect(create).toHaveBeenCalledTimes(1)
})
it('removes the old location immediately when the checkpoint scope changes',async()=>{
  await media.show(revision('base'))
  download.mockRejectedValueOnce(new Error('New place unavailable'))
  await media.show(revision('other','checkpoint-B'))
  expect(surface.destroy).toHaveBeenCalledTimes(1)
  expect(states.at(-1)?.shown).toBe(null)
  expect(states.at(-1)?.phase).toBe('failed')
})
it('reuses received bytes for the same revision while accepting newly verified interaction metadata',async()=>{
  await media.show(revision('base'))
  await media.show({...revision('base'),interactionsVerified:true})
  expect(download).toHaveBeenCalledTimes(1)
  expect(states.at(-1)?.interactionsEnabled).toBe(true)
})
it('does not reappear after the world is closed during image loading',async()=>{
  const slow=deferred<Blob>();download.mockReturnValueOnce(slow.promise)
  const pending=media.show(revision('base'));media.dispose()
  slow.resolve(new Blob(['late']));await pending
  expect(create).not.toHaveBeenCalled()
  expect(states.at(-1)?.phase).toBe('empty')
})
