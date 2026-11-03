import {it, expect, vi} from 'vitest'
import {readFileSync} from 'node:fs'
import {createContext, runInContext} from 'node:vm'

const source = readFileSync(new URL('../../scripts/service-worker-shell.js', import.meta.url), 'utf8')
function deferred<T>() {let resolve!:(v:T)=>void;const promise=new Promise<T>(r=>resolve=r);return {promise,resolve}}
function harness(transport:ReturnType<typeof vi.fn>,assets:string[],coreAssets=assets) {
  const listeners = new Map<string,(event:any)=>void>(), saved = new Map<string,Response>()
  const registration={active:{scriptURL:'https://reader.test/sw.js?release=new'}}
  const skipWaiting = vi.fn().mockResolvedValue(undefined), remove = vi.fn()
  const cache = {put:vi.fn(async(key:string,value:Response)=>{saved.set(key,value)}),match:vi.fn(async(key:string)=>saved.get(key))}
  class LocalRequest extends Request {constructor(input:string,init?:RequestInit){super(new URL(input,'https://reader.test'),init)}}
  runInContext(source,createContext({self:{skipWaiting,location:{href:registration.active.scriptURL},registration,clients:{claim:vi.fn()},addEventListener:(name:string,fn:any)=>listeners.set(name,fn)},
    location:{origin:'https://reader.test'},VERSION:'reader-shell-new',ASSETS:assets,CORE_ASSETS:coreAssets,OPTIONAL_ASSETS:['/optional.png'],
    caches:{open:vi.fn().mockResolvedValue(cache),keys:vi.fn().mockResolvedValue(['reader-shell-old']),delete:remove},
    fetch:transport,Request:LocalRequest,AbortController,URL,setTimeout,clearTimeout}))
  return {listeners,saved,cache,skipWaiting,remove,registration,install:()=>{let pending!:Promise<void>;listeners.get('install')!({waitUntil:(p:Promise<void>)=>pending=p});return pending},
    warm:()=>{let pending!:Promise<void>;const reply=vi.fn();listeners.get('message')!({data:{kind:'reader-shell-warm'},ports:[{postMessage:reply}],waitUntil:(p:Promise<void>)=>pending=p});return {pending,reply}}}
}
it('limits cold installation to sixteen downloads and activates only after the complete offline shell is stored',async()=>{
  const gates=new Map<string,ReturnType<typeof deferred<Response>>>(),assets=Array.from({length:40},(_,i)=>'/asset-'+i+'.js')
  let active=0,peak=0
  const transport=vi.fn(async(request:Request)=>{active++;peak=Math.max(peak,active);const gate=deferred<Response>();gates.set(new URL(request.url).pathname,gate);const response=await gate.promise;active--;return response})
  const h=harness(transport,assets),pending=h.install()
  await vi.waitFor(()=>expect(gates.size).toBe(16));expect(h.skipWaiting).not.toHaveBeenCalled()
  gates.get(assets[0])!.resolve(new Response('first'))
  await vi.waitFor(()=>expect(gates.size).toBe(17));expect(h.saved.size).toBe(1)
  for(let i=1;i<assets.length;i++) {await vi.waitFor(()=>expect(gates.has(assets[i])).toBe(true));gates.get(assets[i])!.resolve(new Response('complete'))}
  await pending
  expect(peak).toBe(16);expect([...h.saved.keys()].sort()).toEqual([...assets].sort());expect(h.skipWaiting).toHaveBeenCalledTimes(1)
})
it('does not activate or discard the previous offline shell when an asset is unavailable',async()=>{
  const h=harness(vi.fn().mockResolvedValue(new Response('denied',{status:403})),['/entry.js'])
  await expect(h.install()).rejects.toThrow('Offline shell asset unavailable')
  expect(h.skipWaiting).not.toHaveBeenCalled();expect(h.cache.put).not.toHaveBeenCalled();expect(h.remove).not.toHaveBeenCalled()
})
it('leaves private APIs, health requests and external assets outside the shell cache',()=>{
  const transport=vi.fn(),h=harness(transport,['/entry.js'])
  for(const url of ['https://reader.test/api/reader/v1/auth/me','https://reader.test/health/ready','https://other.test/entry.js']) {
    const respondWith=vi.fn();h.listeners.get('fetch')!({request:new Request(url),respondWith});expect(respondWith).not.toHaveBeenCalled()
  }
  expect(transport).not.toHaveBeenCalled();expect(h.cache.put).not.toHaveBeenCalled()
})
it('activates the authentication entry before a slow optional view finishes and keeps that view available offline afterwards',async()=>{
  const view=deferred<Response>(),transport=vi.fn((request:Request)=>request.url.endsWith('/entry.js')?Promise.resolve(new Response('entry')):view.promise)
  const h=harness(transport,['/entry.js','/reader.js'],['/entry.js'])
  await h.install();expect(h.skipWaiting).toHaveBeenCalledTimes(1);expect(h.saved.size).toBe(1)
  const warm=h.warm();await vi.waitFor(()=>expect(transport).toHaveBeenCalledTimes(2));expect(warm.reply).not.toHaveBeenCalled();expect(h.remove).not.toHaveBeenCalled()
  view.resolve(new Response('reader'));await warm.pending
  expect(h.saved.size).toBe(2);expect(warm.reply).toHaveBeenCalledWith({kind:'reader-shell-warm-complete',complete:true})
})
it('preserves the previous offline cache after a failed warm-up and supports a later retry',async()=>{
  const transport=vi.fn().mockResolvedValueOnce(new Response('entry')).mockResolvedValueOnce(new Response('retry later',{status:503})).mockResolvedValueOnce(new Response('reader'))
  const h=harness(transport,['/entry.js','/reader.js'],['/entry.js']);await h.install()
  const failed=h.warm();await failed.pending;expect(failed.reply).toHaveBeenCalledWith({kind:'reader-shell-warm-complete',complete:false});expect(h.remove).not.toHaveBeenCalled()
  const retry=h.warm();await retry.pending;expect(retry.reply).toHaveBeenCalledWith({kind:'reader-shell-warm-complete',complete:true});expect(h.saved.size).toBe(2)
})
it('does not remove the new worker cache when a replaced worker finishes an older warm-up',async()=>{
  const view=deferred<Response>(),transport=vi.fn((request:Request)=>request.url.endsWith('/entry.js')?Promise.resolve(new Response('entry')):view.promise)
  const h=harness(transport,['/entry.js','/reader.js'],['/entry.js']);await h.install();const old=h.warm()
  await vi.waitFor(()=>expect(transport).toHaveBeenCalledTimes(2));h.registration.active.scriptURL='https://reader.test/sw.js?release=next'
  view.resolve(new Response('old reader'));await old.pending;expect(h.remove).not.toHaveBeenCalled()
})
