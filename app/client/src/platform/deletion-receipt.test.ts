import {describe,it,expect} from 'vitest'
import {createDeletionReceiptStore} from './deletion-receipt'

const receipt=(id:string,recordedAt?:number)=>JSON.stringify({id,base:'https://reader.example',...(recordedAt?{recordedAt}:{})})
describe('account deletion receipt recovery',()=>{
  it('repairs an older restored WebView mirror from the newer durable native receipt',async()=>{
    let web=receipt('older-restored-snapshot',100)
    const file=receipt('current-response',200)
    const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:value=>{web=value},readNative:async()=>file})
    expect((await store.load())?.id).toBe('current-response')
    expect(JSON.parse(web)).toEqual(JSON.parse(file))
  })
  it('reconciles a stale native backup from local recovery through the same ordered queue',async()=>{
    let web=receipt('current-response',200),file=receipt('old',100),release!:()=>void
    const pending=new Promise<void>(resolve=>{release=resolve}),writes:string[]=[]
    const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:value=>{web=value},readNative:async()=>file,
      writeNative:async value=>{writes.push(JSON.parse(value).id);if(writes.length===1)await pending;file=value},now:()=>300})
    expect((await store.load())?.id).toBe('current-response')
    const next=store.save({id:'next-response',base:'https://reader.example'})
    await Promise.resolve();await Promise.resolve()
    expect(writes).toEqual(['current-response']);expect(JSON.parse(web).id).toBe('next-response')
    release();await next
    expect(writes).toEqual(['current-response','next-response']);expect(JSON.parse(file).id).toBe('next-response')
  })
  it('returns the durable latest receipt when mirror repair fails',async()=>{
    const store=createDeletionReceiptStore({readLocal:()=>receipt('old',100),writeLocal:()=>{throw new Error('quota')},readNative:async()=>receipt('new',200)})
    expect((await store.load())?.id).toBe('new')
  })
  it('preserves same-origin web receipts with an empty backend base, including legacy records',async()=>{
    let web=JSON.stringify({id:'legacy',base:''})
    const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:value=>{web=value},now:()=>100})
    expect(await store.load()).toEqual({id:'legacy',base:''})
    expect(await store.save({id:'new',base:''})).toEqual({localSaved:true,nativeSaved:false})
    expect(await store.load()).toEqual({id:'new',base:''})
  })
  it('observes a newer local response saved while the native read is pending',async()=>{
    let web=receipt('old',100),release!:(value:string)=>void
    const pending=new Promise<string>(resolve=>{release=resolve})
    const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:value=>{web=value},readNative:()=>pending,now:()=>200})
    const loading=store.load()
    await store.save({id:'new',base:'https://reader.example'})
    release(receipt('old',100))
    expect((await loading)?.id).toBe('new')
  })
  it('recovers an existing native receipt when the old WebView mirror disagrees',async()=>{
    const store=createDeletionReceiptStore({readLocal:()=>receipt('old'),writeLocal:()=>{},readNative:async()=>receipt('current')})
    expect(await store.load()).toEqual({id:'current',base:'https://reader.example'})
  })
  it('saves the current response locally before native IO and survives native failure after writing',async()=>{
    let web=receipt('old'),file=receipt('old'),release!:()=>void
    const pending=new Promise<void>(resolve=>{release=resolve})
    const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:value=>{web=value},readNative:async()=>file,writeNative:async value=>{file=value;await pending;throw new Error('callback failed')},now:()=>100})
    const saving=store.save({id:'new',base:'https://reader.example'})
    expect(JSON.parse(web).id).toBe('new')
    release();expect(await saving).toEqual({localSaved:true,nativeSaved:false})
    expect(await store.load()).toEqual({id:'new',base:'https://reader.example'})
  })
  it('reopens the newer local receipt when the native backup still contains an older account',async()=>{
    const store=createDeletionReceiptStore({readLocal:()=>receipt('new',200),writeLocal:()=>{},readNative:async()=>receipt('old',100)})
    expect(await store.load()).toEqual({id:'new',base:'https://reader.example'})
  })
  it('a stamped local receipt takes precedence over an unversioned native backup',async()=>{
    const store=createDeletionReceiptStore({readLocal:()=>receipt('new',200),writeLocal:()=>{},readNative:async()=>receipt('old')})
    expect((await store.load())?.id).toBe('new')
  })
  it('recovers the newer native copy after WebView storage is missing or stale',async()=>{
    for(const web of [null,receipt('old',100)]){
      const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:()=>{},readNative:async()=>receipt('new',200)})
      expect((await store.load())?.id).toBe('new')
    }
  })
  it('independent corrupt copies do not hide the valid receipt',async()=>{
    for(const [web,file] of [['{broken',receipt('valid',100)],[receipt('valid',100),'null']]){
      const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:()=>{},readNative:async()=>file})
      expect((await store.load())?.id).toBe('valid')
    }
  })
  it('serializes native writes without replacing the newer local response',async()=>{
    let web:string|null=null,file='',release!:()=>void
    const pending=new Promise<void>(resolve=>{release=resolve}),writes:string[]=[]
    const store=createDeletionReceiptStore({readLocal:()=>web,writeLocal:value=>{web=value},readNative:async()=>file,writeNative:async value=>{writes.push(JSON.parse(value).id);if(writes.length===1)await pending;file=value},now:()=>100})
    const first=store.save({id:'first',base:'https://reader.example'}),second=store.save({id:'second',base:'https://reader.example'})
    await Promise.resolve();await Promise.resolve()
    expect(writes).toEqual(['first']);expect(JSON.parse(web!).id).toBe('second')
    expect((await store.load())?.id).toBe('second')
    release();await Promise.all([first,second])
    expect(writes).toEqual(['first','second']);expect(JSON.parse(file).id).toBe('second')
    expect(JSON.parse(web!).recordedAt).toBeGreaterThan(100)
  })
  it('keeps a durable native receipt when local storage is unavailable',async()=>{
    let file=''
    const store=createDeletionReceiptStore({readLocal:()=>null,writeLocal:()=>{throw new Error('quota')},readNative:async()=>file,writeNative:async value=>{file=value},now:()=>100})
    expect(await store.save({id:'native-only',base:'https://reader.example'})).toEqual({localSaved:false,nativeSaved:true})
    expect((await store.load())?.id).toBe('native-only')
  })
  it('reports a total storage failure without fabricating a saved receipt',async()=>{
    const store=createDeletionReceiptStore({readLocal:()=>null,writeLocal:()=>{throw new Error('quota')},writeNative:async()=>{throw new Error('disk')},now:()=>100})
    await expect(store.save({id:'new',base:'https://reader.example'})).rejects.toThrow('本机无法保存清理回执')
  })
})
