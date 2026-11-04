import {describe,expect,it,vi,beforeEach} from 'vitest'
import {createSSEParser,dispatchSSEEvent,parseEventBlock,readSSEStream,streamChat,type SSEEvent} from './sse'

vi.mock('../api',()=>({authorizationHeaders:()=>({Authorization:'Bearer token'})}))
vi.mock('../platform/bridge',()=>({backendBase:()=>'https://app.test'}))

function byteStream(chunks:string[]){
  const encoder=new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller){for(const chunk of chunks)controller.enqueue(encoder.encode(chunk));controller.close()},
  })
}

async function collect(chunks:string[]):Promise<SSEEvent[]>{
  const events:SSEEvent[]=[]
  await readSSEStream(byteStream(chunks),event=>events.push(event))
  return events
}

describe('createSSEParser',()=>{
  it('frames events that arrive split across chunks',()=>{
    const events:SSEEvent[]=[]
    const parser=createSSEParser(event=>events.push(event))
    parser('event: del')
    parser('ta\ndata: {"text":"你')
    expect(events).toHaveLength(0)
    parser('好"}\n\n')
    expect(events).toEqual([{event:'delta',data:'{"text":"你好"}'}])
  })

  it('joins multi-line data, normalizes CRLF and ignores comments/BOM',()=>{
    const events:SSEEvent[]=[]
    const parser=createSSEParser(event=>events.push(event))
    parser('\uFEFF: keep-alive\r\nid: 7\r\nevent: message\r\ndata: {"a":1}\r\ndata: {"b":2}\r\n\r\n')
    expect(events).toEqual([{event:'message',data:'{"a":1}\n{"b":2}',id:'7'}])
  })

  it('returns null for blocks without a data field',()=>{
    expect(parseEventBlock('event: ping')).toBeNull()
    expect(parseEventBlock(': comment only')).toBeNull()
  })
})

describe('readSSEStream',()=>{
  it('decodes a split multi-byte character correctly',async()=>{
    const encoder=new TextEncoder()
    const bytes=encoder.encode('event: delta\ndata: {"text":"雨"}\n\n')
    const stream=new ReadableStream<Uint8Array>({
      start(controller){
        // Split in the middle of the UTF-8 sequence for 雨.
        controller.enqueue(bytes.slice(0,20))
        controller.enqueue(bytes.slice(20))
        controller.close()
      },
    })
    const events:SSEEvent[]=[]
    await readSSEStream(stream,event=>events.push(event))
    expect(events).toHaveLength(1)
    expect(JSON.parse(events[0].data)).toEqual({text:'雨'})
  })
})

describe('dispatchSSEEvent',()=>{
  it('routes delta, message, done and error events',()=>{
    const onDelta=vi.fn(),onMessage=vi.fn(),onDone=vi.fn(),onError=vi.fn()
    const handlers={onDelta,onMessage,onDone,onError}
    dispatchSSEEvent({event:'delta',data:'{"text":"嗨"}'},handlers)
    dispatchSSEEvent({event:'message',data:'{"message":{"id":"m1"}}'},handlers)
    dispatchSSEEvent({event:'done',data:'{"chat":{"id":"c1"}}'},handlers)
    dispatchSSEEvent({event:'error',data:'{"code":"MODEL_FAILED","message":"模型调用失败"}'},handlers)
    dispatchSSEEvent({event:'done',data:'[DONE]'},handlers)
    expect(onDelta).toHaveBeenCalledWith('嗨')
    expect(onMessage).toHaveBeenCalledWith({message:{id:'m1'}})
    expect(onDone).toHaveBeenCalledTimes(2)
    expect(onError).toHaveBeenCalledWith({code:'MODEL_FAILED',message:'模型调用失败'})
  })
})

describe('streamChat',()=>{
  beforeEach(()=>{vi.restoreAllMocks()})

  it('POSTs JSON to the tavern prefix and forwards parsed SSE events',async()=>{
    const fetchMock=vi.fn(async()=>new Response(byteStream([
      'event: message\ndata: {"message":{"id":"m1","role":"user"}}\n\n',
      'event: delta\ndata: {"text":"你"}\n\n',
      'event: delta\ndata: {"text":"好"}\n\n',
      'event: done\ndata: {}\n\n',
    ]),{status:200}))
    vi.stubGlobal('fetch',fetchMock)
    const onMessage=vi.fn(),onDelta=vi.fn(),onDone=vi.fn(),onError=vi.fn()
    await streamChat('/chats/c1/messages',{text:'你好'},{onMessage,onDelta,onDone,onError})
    expect(fetchMock).toHaveBeenCalledWith('https://app.test/api/reader/v1/tavern/chats/c1/messages',expect.objectContaining({
      method:'POST',
      credentials:'include',
      headers:expect.objectContaining({Authorization:'Bearer token','Content-Type':'application/json',Accept:'text/event-stream'}),
      body:'{"text":"你好"}',
    }))
    expect(onMessage).toHaveBeenCalledWith({message:{id:'m1',role:'user'}})
    expect(onDelta.mock.calls.map(call=>call[0])).toEqual(['你','好'])
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('surfaces the server problem message for a missing endpoint',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({code:'NOT_FOUND',message:'流式接口尚未上线'}),{status:404})))
    await expect(streamChat('/chats/c1/messages',{text:'x'},{})).rejects.toThrow('流式接口尚未上线')
    vi.unstubAllGlobals()
  })

  it('turns 401 into a re-login hint',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response('',{status:401})))
    await expect(streamChat('/chats/c1/messages',{text:'x'},{})).rejects.toThrow('登录已过期，请重新登录。')
    vi.unstubAllGlobals()
  })
})
