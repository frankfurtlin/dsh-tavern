import {describe,expect,it,vi,beforeEach} from 'vitest'
import {api} from '../api'
import {
  characterAvatarBlob,createCharacterChat,createGroupChat,createWorldInfo,editMessage,exportCharacterBlob,
  exportChatBlob,getCharacter,getChat,getPrompt,importCharacter,importChats,listCharacterChats,
  listCharacters,listChats,listPresets,listWorldInfo,messageText,previewWorldInfo,selectSwipe,setDefaultPreset,
  updateCharacter,updateChat,updateChatSampling,updatePreset,updatePrompt,updateWorldInfo,worldInfoKeys,
  createPreset,type TavernCharacter,
} from './api'

vi.mock('../api',()=>({
  api:vi.fn(),
  newKey:vi.fn(()=>'key-1'),
  authorizationHeaders:()=>({Authorization:'Bearer token'}),
}))
vi.mock('../platform/bridge',()=>({backendBase:()=>'https://app.test'}))

const card:TavernCharacter={id:'c1',name:'林渡',description:'雨夜侦探',tags:['悬疑'],hasAvatar:true}

describe('tavern character api',()=>{
  beforeEach(()=>{vi.mocked(api).mockReset()})

  it('lists characters with q/sort and fills the avatar url',async()=>{
    vi.mocked(api).mockResolvedValueOnce({items:[card,{...card,id:'c2',hasAvatar:false}]})
    const result=await listCharacters('雨','name')
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/characters?sort=name&q=%E9%9B%A8')
    expect(result[0].avatarUrl).toBe('https://app.test/api/reader/v1/tavern/characters/c1/avatar')
    expect(result[1].avatarUrl).toBeNull()
  })

  it('unwraps a single-character envelope',async()=>{
    vi.mocked(api).mockResolvedValueOnce({card})
    await expect(getCharacter('c1')).resolves.toMatchObject({id:'c1',name:'林渡'})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/characters/c1')
  })

  it('patches a character with an idempotency key',async()=>{
    vi.mocked(api).mockResolvedValueOnce(card)
    await updateCharacter('c1',{name:'林渡',tags:['悬疑']})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/characters/c1',{method:'PATCH',key:'key-1',body:{name:'林渡',tags:['悬疑']}})
  })

  it('imports a card as multipart form data',async()=>{
    vi.mocked(api).mockResolvedValueOnce({card})
    const file=new File(['{}'],'card.json',{type:'application/json'})
    await importCharacter(file)
    const [path,options]=vi.mocked(api).mock.calls[0]
    expect(path).toBe('/tavern/characters/imports')
    expect(options).toMatchObject({method:'POST',key:'key-1'})
    expect(options?.body).toBeInstanceOf(FormData)
    expect((options?.body as FormData).get('file')).toBe(file)
  })

  it('creates a chat with and without a greeting',async()=>{
    vi.mocked(api).mockResolvedValueOnce({chat:{id:'chat-1',cardId:'c1',title:'林渡'}}).mockResolvedValueOnce({id:'chat-2',cardId:'c1',title:'林渡'})
    await createCharacterChat('c1')
    await createCharacterChat('c1','g1')
    expect(vi.mocked(api).mock.calls[0][1]).toMatchObject({method:'POST',body:{}})
    expect(vi.mocked(api).mock.calls[1][1]).toMatchObject({method:'POST',body:{greetingId:'g1'}})
  })

  it('loads chat details, chat history and world info lists',async()=>{
    vi.mocked(api)
      .mockResolvedValueOnce({chat:{id:'chat-1',cardId:'c1',title:'林渡'},messages:[{id:'m1',role:'user',sequence:1,swipes:[],activeSwipe:0}]})
      .mockResolvedValueOnce({items:[{id:'chat-1',cardId:'c1',title:'林渡'}]})
      .mockResolvedValueOnce({items:[{id:'w1',keys:['雨'],content:'雨夜',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'}]})
    const detail=await getChat('chat-1')
    expect(detail.messages).toHaveLength(1)
    expect((await listCharacterChats('c1'))[0].id).toBe('chat-1')
    expect((await listWorldInfo('c1'))[0].id).toBe('w1')
  })

  it('uses idempotency keys for message and swipe writes',async()=>{
    vi.mocked(api).mockResolvedValueOnce({message:{id:'m1'}}).mockResolvedValueOnce(null)
    await editMessage('chat-1','m1','改写')
    await selectSwipe('chat-1','m1',1)
    expect(vi.mocked(api).mock.calls[0]).toEqual(['/tavern/chats/chat-1/messages/m1',{method:'PATCH',key:'key-1',body:{text:'改写'}}])
    expect(vi.mocked(api).mock.calls[1]).toEqual(['/tavern/chats/chat-1/messages/m1/swipes/1/select',{method:'POST',key:'key-1'}])
  })

  it('renames chats and creates world info entries',async()=>{
    vi.mocked(api).mockResolvedValueOnce({id:'chat-1',cardId:'c1',title:'新名字'}).mockResolvedValueOnce({entry:{id:'w2',keys:['灯'],content:'灯',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'}})
    await updateChat('chat-1','新名字')
    await createWorldInfo('c1',{keys:['灯'],content:'灯'})
    expect(vi.mocked(api).mock.calls[0][1]).toMatchObject({method:'PATCH',body:{title:'新名字'}})
    expect(vi.mocked(api).mock.calls[1][0]).toBe('/tavern/characters/c1/world-info')
  })
})

describe('tavern helpers',()=>{
  it('reads the active swipe text and normalizes world-info keys',()=>{
    expect(messageText({id:'m1',role:'assistant',sequence:1,activeSwipe:1,swipes:[{content:'旧'},{content:'新'}]})).toBe('新')
    expect(worldInfoKeys({keys:['雨',' 屋檐 ']})).toEqual(['雨','屋檐'])
    expect(worldInfoKeys({keys:'雨,屋檐；灯' as unknown as string[]})).toEqual(['雨','屋檐','灯'])
  })
})

describe('tavern binary downloads',()=>{
  it('exports a character card as a blob',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response(new Blob(['png']))))
    await expect(exportCharacterBlob('c1')).resolves.toBeInstanceOf(Blob)
    expect(vi.mocked(fetch)).toHaveBeenCalledWith('https://app.test/api/reader/v1/tavern/characters/c1/export',expect.objectContaining({credentials:'include',headers:{Authorization:'Bearer token'}}))
    vi.unstubAllGlobals()
  })

  it('explains a missing export endpoint',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response('',{status:404})))
    await expect(exportCharacterBlob('c1')).rejects.toThrow('服务端暂不支持导出角色卡')
    vi.unstubAllGlobals()
  })

  it('returns null when the avatar endpoint answers 404',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response('',{status:404})))
    await expect(characterAvatarBlob('c1')).resolves.toBeNull()
    vi.unstubAllGlobals()
  })
})

describe('tavern chat pagination and import/export',()=>{
  beforeEach(()=>{vi.mocked(api).mockReset()})

  it('passes limit and before to chat history requests',async()=>{
    vi.mocked(api).mockResolvedValue({chat:{id:'chat-1',cardId:'c1',title:'林渡'},messages:[{id:'m1',role:'assistant',sequence:1,swipes:[],activeSwipe:0}],hasMore:false})
    const page=await getChat('chat-1',{limit:200,before:42})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/chats/chat-1?limit=200&before=42')
    expect(page.hasMore).toBe(false)
  })

  it('previews world-info injection without an idempotency key',async()=>{
    const preview={matched:[{id:'w1',keys:['雨'],content:'雨夜',position:'before_char',depth:4,constant:false,probability:100,order:0,enabled:true,source:'manual'}],injection:'[世界书] 雨夜'}
    vi.mocked(api).mockResolvedValueOnce({preview})
    await expect(previewWorldInfo('c1','外面下雨了')).resolves.toEqual(preview)
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/characters/c1/world-info/preview',{method:'POST',body:{message:'外面下雨了'}})
    const [,options]=vi.mocked(api).mock.calls[0]
    expect(options).not.toHaveProperty('key')
  })

  it('imports chats as multipart form data',async()=>{
    vi.mocked(api).mockResolvedValueOnce({chat:{id:'imported-1',cardId:'c1',title:'导入的聊天'}})
    const file=new File(['{"role":"user","text":"你好"}\n'],'chat.jsonl',{type:'application/jsonl'})
    await expect(importChats('c1',file)).resolves.toMatchObject({id:'imported-1'})
    const [path,options]=vi.mocked(api).mock.calls[0]
    expect(path).toBe('/tavern/characters/c1/chats/imports')
    expect(options).toMatchObject({method:'POST',key:'key-1'})
    expect((options?.body as FormData).get('file')).toBe(file)
  })

  it('downloads chat exports per format',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response(new Blob(['{"a":1}']))))
    await expect(exportChatBlob('chat-1','json')).resolves.toBeInstanceOf(Blob)
    expect(vi.mocked(fetch)).toHaveBeenCalledWith('https://app.test/api/reader/v1/tavern/chats/chat-1/export?format=json',expect.objectContaining({credentials:'include'}))
    vi.unstubAllGlobals()
  })

  it('explains a missing chat export endpoint',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response('',{status:404})))
    await expect(exportChatBlob('chat-1')).rejects.toThrow('服务端暂不支持导出聊天')
    vi.unstubAllGlobals()
  })

  it('saves chat-level sampling through the chat patch',async()=>{
    vi.mocked(api).mockResolvedValueOnce({id:'chat-1',title:'林渡 · 聊天',sampling:{temperature:0.7,topP:0.9,maxTokens:512,stop:['END']}})
    await expect(updateChatSampling('chat-1',{temperature:0.7,topP:0.9,maxTokens:512,stop:['END']})).resolves.toMatchObject({sampling:{temperature:0.7}})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/chats/chat-1',{method:'PATCH',key:'key-1',body:{sampling:{temperature:0.7,topP:0.9,maxTokens:512,stop:['END']}}})
  })

  it('unwraps prompt settings and fills defaults',async()=>{
    vi.mocked(api).mockResolvedValueOnce({prompt:{systemPrompt:'系统',mainPrompt:'主线',temperature:0.5,topP:0.8,maxTokens:2048,stop:['STOP']}})
    const value=await getPrompt('c1')
    expect(value).toMatchObject({systemPrompt:'系统',mainPrompt:'主线',temperature:0.5,topP:0.8,maxTokens:2048,stop:['STOP']})
    vi.mocked(api).mockResolvedValueOnce({prompt:{}})
    await expect(getPrompt('c1')).resolves.toMatchObject({temperature:0.8,topP:0.95,maxTokens:1024,stop:[]})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/characters/c1/prompt')
  })

  it('patches prompt overrides with an idempotency key',async()=>{
    vi.mocked(api).mockResolvedValueOnce({prompt:{systemPrompt:'新'}})
    await updatePrompt('c1',{systemPrompt:'新',temperature:0.9})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/characters/c1/prompt',{method:'PATCH',key:'key-1',body:{systemPrompt:'新',temperature:0.9}})
  })

  it('maps preset envelope fields between the wire names and the prompt names',async()=>{
    vi.mocked(api).mockResolvedValueOnce({items:[{id:'p1',name:'细腻',isDefault:true,system:'系统',main:'主线',jailbreak:'越狱',postHistory:'后置',temperature:0.8,topP:0.95,maxTokens:1024,stop:[]}]})
    const [preset]=await listPresets()
    expect(preset).toMatchObject({id:'p1',name:'细腻',systemPrompt:'系统',mainPrompt:'主线',jailbreakPrompt:'越狱',postHistoryPrompt:'后置',isDefault:true})
  })

  it('creates a preset using the server field names',async()=>{
    vi.mocked(api).mockResolvedValueOnce({preset:{id:'p2',name:'冷硬',system:'S',main:'M',jailbreak:'J',postHistory:'P',temperature:0.4,topP:0.9,maxTokens:512,stop:[]}})
    await expect(createPreset({name:'冷硬',systemPrompt:'S',mainPrompt:'M',jailbreakPrompt:'J',postHistoryPrompt:'P',temperature:0.4,topP:0.9,maxTokens:512,stop:[]})).resolves.toMatchObject({id:'p2',systemPrompt:'S',mainPrompt:'M'})
    expect(vi.mocked(api)).toHaveBeenLastCalledWith('/tavern/presets',{method:'POST',key:'key-1',body:{name:'冷硬',system:'S',main:'M',jailbreak:'J',postHistory:'P',temperature:0.4,topP:0.9,maxTokens:512,stop:[]}})
  })

  it('never sends isDefault inside preset create or update bodies',async()=>{
    vi.mocked(api).mockResolvedValueOnce({preset:{id:'p3',name:'默认款',system:'',main:'',jailbreak:'',postHistory:'',temperature:null,topP:null,maxTokens:null,stop:[]}})
    await createPreset({name:'默认款',isDefault:true})
    const createBody=(vi.mocked(api).mock.calls[0][1] as {body:Record<string,unknown>}).body
    expect(createBody).not.toHaveProperty('isDefault')
    expect(createBody).toMatchObject({name:'默认款'})

    vi.mocked(api).mockResolvedValueOnce({preset:{id:'p3',name:'默认款',system:'',main:'',jailbreak:'',postHistory:'',temperature:null,topP:null,maxTokens:null,stop:[]}})
    await updatePreset('p3',{name:'默认款',isDefault:true})
    const updateBody=(vi.mocked(api).mock.calls[1][1] as {body:Record<string,unknown>}).body
    expect(updateBody).not.toHaveProperty('isDefault')
    expect(updateBody).toEqual({name:'默认款'})
  })

  it('patches preset fields with the wire names and maps the reply back',async()=>{
    vi.mocked(api).mockResolvedValueOnce({preset:{id:'p1',name:'细腻',system:'新系统',main:'',jailbreak:'',postHistory:'',temperature:0.8,topP:0.95,maxTokens:1024,stop:[]}})
    await expect(updatePreset('p1',{systemPrompt:'新系统'})).resolves.toMatchObject({systemPrompt:'新系统'})
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/presets/p1',{method:'PATCH',key:'key-1',body:{system:'新系统'}})
  })

  it('marks a preset as default',async()=>{
    vi.mocked(api).mockResolvedValueOnce({preset:{id:'p1',name:'细腻',isDefault:true}})
    await setDefaultPreset('p1')
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/presets/p1',{method:'PATCH',key:'key-1',body:{isDefault:true}})
  })

  it('creates a group chat with 2-5 member card ids',async()=>{
    vi.mocked(api).mockResolvedValueOnce({chat:{id:'g1',cardId:'',mode:'group',title:'群聊',members:[{cardId:'c1',name:'林渡'},{cardId:'c2',name:'阿青'}]}})
    const chat=await createGroupChat(['c1','c2'])
    expect(chat.mode).toBe('group')
    expect(chat.members?.map(member=>member.name)).toEqual(['林渡','阿青'])
    expect(vi.mocked(api)).toHaveBeenCalledWith('/tavern/chats',{method:'POST',key:'key-1',body:{cardIds:['c1','c2']}})
  })

  it('lists chats from an items envelope or a bare array',async()=>{
    vi.mocked(api).mockResolvedValueOnce({items:[{id:'chat-1',cardId:'c1',title:'单聊'}]}).mockResolvedValueOnce([{id:'chat-2',cardId:'',mode:'group',title:'群聊'}])
    await expect(listChats()).resolves.toHaveLength(1)
    await expect(listChats()).resolves.toHaveLength(1)
  })
})
