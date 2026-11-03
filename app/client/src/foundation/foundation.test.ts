import {describe,it,expect} from 'vitest'
import {createTracker,sanitizeMetadata} from './vendor/telemetry.js'
import {createLocaleController} from './vendor/i18n.js'
import {seoMetadata,sitemapXml} from './vendor/seo.js'
class MemoryStorage {map=new Map<string,string>();getItem(k:string){return this.map.get(k)||null}setItem(k:string,v:string){this.map.set(k,v)}}
describe('foundation adapters',()=>{
  it('redacts private bodies, credentials and nested content',()=>{
    expect(sanitizeMetadata({prompt:'a novel',key:'sk-1234',email:'a@b',nested:{content:'secret',count:3}})).toEqual({key:'[redacted]',nested:{count:3}})
  })
  it('retains stable IDs until persisted acknowledgement',async()=>{
    const storage=new MemoryStorage(),sent:Record<string,any>[]=[];let persisted=false
    const tracker=createTracker({productId:'fixture',storage,persistentStorage:new MemoryStorage(),session:{get:()=>({user:{id:'owner-one'}})},fetchImpl:async(_url:string,options:RequestInit)=>{const body=JSON.parse(options.body as string);sent.push(body);return {ok:true,json:async()=>({code:200,data:{persisted,accepted:body.events.length}})}}})
    tracker.track('page_view','/product/wallet');await tracker.flush();expect(tracker.pending()).toBe(1)
    persisted=true;await tracker.flush();expect(tracker.pending()).toBe(0)
    expect(sent[0].events[0].eventId).toBe(sent[1].events[0].eventId)
  })
  it('does not send the previous account queue under a new identity',async()=>{
    let owner='owner-one';const sent:Record<string,any>[]=[]
    const tracker=createTracker({productId:'fixture',storage:new MemoryStorage(),persistentStorage:new MemoryStorage(),session:{get:()=>({user:{id:owner}})},fetchImpl:async(_url:string,options:RequestInit)=>{const body=JSON.parse(options.body as string);sent.push(body);return {ok:true,json:async()=>({code:200,data:{persisted:true,accepted:body.events.length}})}}})
    tracker.track('click','product.first');owner='owner-two';tracker.track('click','product.second');await tracker.flush()
    expect(sent[0].expectedUserId).toBe('owner-two');expect(tracker.pending()).toBe(1)
    tracker.clear();expect(tracker.pending()).toBe(0)
  })
  it('latest language choice wins when an older loader completes later',async()=>{
    let finish!:(v:Record<string,string>)=>void
    const controller=createLocaleController({loaders:{'zh-CN':()=>new Promise(resolve=>finish=resolve),en:()=>({title:'English'})},initial:'zh-CN'})
    await Promise.resolve();await controller.switchLocale('en');finish({title:'中文'});await controller.ready
    expect(controller.current()).toBe('en')
  })
  it('SEO rejects foreign canonical URLs and keeps private pages unindexed',()=>{
    expect(()=>seoMetadata({origin:'https://reader.example.com',path:'https://other.example.com',title:'x'})).toThrow()
    expect(seoMetadata({origin:'https://reader.example.com',path:'/product/wallet?owner=secret',title:'x'}).robots).toBe('noindex,nofollow')
    expect(sitemapXml(['https://reader.example.com/help?a=1&b=2'])).toContain('&amp;')
  })
})
