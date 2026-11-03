import {describe,it,expect} from 'vitest'
import {SSEParser,normalizeEvent,endpoint,requestBody,streamText} from './text'
import type {ProviderSnapshot} from '../domain/local-contract'
const profile:ProviderSnapshot={id:'synthetic-profile',revision:1,protocol:'chat-completions',baseUrl:'https://provider.invalid/v1',modelId:'fixture-model',key:'isolated-not-a-real-key',contextLimit:32768,outputLimit:2048,includeUsage:true}
describe('four official text protocol contracts',()=>{
  it('rejects unsupported browser IPv6 HTTP before transport without changing saved URLs',async()=>{
    const config={...profile,baseUrl:'http://[::1]:11434/v1'},original=config.baseUrl
    let calls=0
    const transport=(async()=>{calls++;throw new Error('Must not send')}) as typeof fetch
    await expect(streamText(config,[],'s',async()=>{},new AbortController().signal,{firstPacketMs:1000,idleMs:1000,totalMs:2000},transport)).rejects.toThrow(/localhost.*127\.0\.0\.1/)
    expect(config.baseUrl).toBe(original);expect(calls).toBe(0)
    for(const host of ['localhost','127.0.0.1'])expect(endpoint({...profile,baseUrl:`http://${host}:11434/v1`})).toBe(`http://${host}:11434/v1/chat/completions`)
  })
  it('keeps SSE multiline/CRLF and arbitrary UTF8 packet boundaries',()=>{
    const bytes=new TextEncoder().encode('event: message\r\ndata: {"text":\r\ndata: "🌿你好"}\r\n\r\n: heartbeat\n\ndata: [DONE]\n\n'),decoder=new TextDecoder('utf-8',{fatal:true}),parser=new SSEParser(),frames:string[]=[]
    for(const byte of bytes)frames.push(...parser.feed(decoder.decode(Uint8Array.of(byte),{stream:true})))
    frames.push(...parser.feed(decoder.decode(),true))
    expect(frames).toEqual(['{"text":\n"🌿你好"}','[DONE]'])
  })
  it('emits content and actual usage without leaking thoughts/tools',()=>{
    expect(normalizeEvent(profile,{id:'req-one',choices:[{index:0,delta:{content:'你好',reasoning_content:'private',tool_calls:[{}]},finish_reason:null}]})).toEqual([{type:'request',id:'req-one'},{type:'text',text:'你好'}])
    expect(normalizeEvent({...profile,protocol:'responses'},{type:'response.reasoning_text.delta',delta:'hidden'})).toEqual([])
    expect(normalizeEvent({...profile,protocol:'responses'},{type:'response.output_text.delta',delta:'readable'})).toEqual([{type:'text',text:'readable'}])
    expect(normalizeEvent({...profile,protocol:'anthropic'},{type:'content_block_delta',delta:{type:'thinking_delta',thinking:'private'}})).toEqual([])
    expect(normalizeEvent({...profile,protocol:'anthropic'},{type:'message_delta',delta:{stop_reason:'end_turn'},usage:{output_tokens:21}})).toEqual([{type:'usage',input:null,output:21},{type:'finish',reason:'end_turn'}])
    expect(normalizeEvent({...profile,protocol:'gemini'},{responseId:'req-g',candidates:[{content:{parts:[{text:'private',thought:true},{text:'正文'}]},finishReason:'STOP'}],usageMetadata:{promptTokenCount:5,candidatesTokenCount:9,thoughtsTokenCount:3}})).toEqual([{type:'request',id:'req-g'},{type:'text',text:'正文'},{type:'finish',reason:'stop'},{type:'usage',input:5,output:12}])
  })
  it('uses distinct request schemas, no private key in body, forbids redirect credential URLs',()=>{
    const messages=[{role:'system' as const,content:'identity'},{role:'user' as const,content:'hello'}]
    expect(requestBody({...profile,protocol:'responses'},messages)).toMatchObject({instructions:'identity',input:[{role:'user',content:'hello'}],store:false})
    expect(requestBody({...profile,protocol:'anthropic'},messages)).toMatchObject({system:'identity',max_tokens:2048})
    expect(requestBody({...profile,protocol:'gemini'},messages)).toMatchObject({systemInstruction:{parts:[{text:'identity'}]},contents:[{role:'user',parts:[{text:'hello'}]}]})
    expect(()=>endpoint({...profile,baseUrl:'https://name:secret@provider.invalid/v1'})).toThrow(/密钥/)
    expect(()=>endpoint({...profile,baseUrl:'http://remote.invalid/v1'})).toThrow(/HTTPS/)
    expect(JSON.stringify(requestBody(profile,messages))).not.toContain(profile.key)
  })
  it('returns streamed text before end; truncated stream stays unknown without retry',async()=>{
    const emitted:string[]=[],bytes=new TextEncoder().encode('data: {"choices":[{"index":0,"delta":{"content":"第一段"}}]}\n\n')
    let calls=0
    const transport=(async()=>{calls++;return new Response(new ReadableStream({start(controller){controller.enqueue(bytes);controller.close()}}),{headers:{'Content-Type':'text/event-stream'}})}) as typeof fetch
    await expect(streamText(profile,[{role:'user',content:'hi'}],'s',async event=>{if(event.type==='text')emitted.push(event.text)},new AbortController().signal,{firstPacketMs:1000,idleMs:1000,totalMs:2000},transport)).rejects.toMatchObject({code:'STREAM_INTERRUPTED',uncertain:true})
    expect(emitted).toEqual(['第一段']);expect(calls).toBe(1)
  })
  it('separates first packet/idle/total timeouts and never repeats transport',async()=>{
    for(const mode of ['first','idle','total']){
      let calls=0
      const transport=(async(_url,options)=>{calls++;return new Response(new ReadableStream({start(controller){options?.signal?.addEventListener('abort',()=>controller.error(new DOMException('stopped','AbortError')));if(mode!=='first')controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"已收到"}}]}\n\n'))}}))}) as typeof fetch
      await expect(streamText(profile,[],'s',async()=>{},new AbortController().signal,{firstPacketMs:mode==='first'?100:1000,idleMs:mode==='idle'?100:1000,totalMs:mode==='total'?100:2000},transport)).rejects.toMatchObject({code:{first:'FIRST_PACKET_TIMEOUT',idle:'IDLE_TIMEOUT',total:'TOTAL_TIMEOUT'}[mode]})
      expect(calls).toBe(1)
    }
  })
})
