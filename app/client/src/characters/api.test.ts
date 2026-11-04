import {beforeEach,it,expect,vi} from 'vitest'
vi.mock('../api',()=>({api:vi.fn(),authorizationHeaders:vi.fn(()=>({Authorization:'Bearer token'})),newKey:()=> 'fixture-key'}))
vi.mock('../platform/bridge',()=>({backendBase:()=>''}))
import {api} from '../api'
import {cardAvatarBlob,cardGreetings,contextPreview,draftCard,createLore,createMemory,createPreset,createRegexScript,deleteCard,deleteLore,deleteMemory,deletePreset,deleteRegexScript,extractMemories,getCard,importCard,listCards,listLore,listMemories,listPresets,listRegexScripts,setCompanion,startCardChat,updateCard,updateLore,updateMemory,updatePreset,updateRegexScript} from './api'
const mocked=vi.mocked(api)
const card={id:'c1',name:'林渡',description:'',personality:'',scenario:'',tags:[],creator:null,source:'png' as const,hasAvatar:true}
beforeEach(()=>{mocked.mockReset();vi.unstubAllGlobals()})

it('reads the {items} envelope and still tolerates a bare array',async()=>{
 mocked.mockResolvedValueOnce({items:[card]})
 await expect(listCards()).resolves.toEqual([card])
 expect(mocked).toHaveBeenCalledWith('/character-cards')
 mocked.mockResolvedValueOnce([card])
 await expect(listCards('林渡')).resolves.toEqual([card])
 expect(mocked).toHaveBeenLastCalledWith('/character-cards?q=%E6%9E%97%E6%B8%A1')
 mocked.mockResolvedValueOnce({} as any)
 await expect(listCards()).resolves.toEqual([])
})

it('uploads the chosen file as multipart FormData on the documented route',async()=>{
 const file=new File([new Uint8Array([1,2])],'card.png',{type:'image/png'})
 mocked.mockResolvedValueOnce({card})
 const result=await importCard(file)
 expect(mocked).toHaveBeenCalledTimes(1)
 const [path,options]=mocked.mock.calls[0]
 expect(path).toBe('/character-cards/imports')
 expect(options?.method).toBe('POST')
 const body=options?.body as FormData
 expect(body).toBeInstanceOf(FormData)
 const uploaded=body.get('file') as File
 expect(uploaded).toBeInstanceOf(File)
 expect(uploaded.name).toBe('card.png')
 expect(result.card).toEqual(card)
})

it('starts a card chat and deletes the card through the documented routes',async()=>{
 mocked.mockResolvedValueOnce({sessionId:'s1',cardId:'c1'})
 await expect(startCardChat('c1')).resolves.toEqual({sessionId:'s1',cardId:'c1'})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/chats',{method:'POST',key:'fixture-key'})
 mocked.mockResolvedValueOnce({})
 await deleteCard('c1')
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1',{method:'DELETE',key:'fixture-key'})
})

it('fetches avatar bytes with the bearer token and treats 404 as no avatar',async()=>{
 const blob=new Blob(['png'],{type:'image/png'})
 const fetchMock=vi.fn().mockResolvedValueOnce({ok:true,blob:()=>Promise.resolve(blob)})
 vi.stubGlobal('fetch',fetchMock)
 await expect(cardAvatarBlob('c1')).resolves.toBe(blob)
 expect(fetchMock).toHaveBeenCalledWith('/api/reader/v1/character-cards/c1/avatar',{credentials:'include',headers:{Authorization:'Bearer token'}})
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce({ok:false,status:404,blob:()=>Promise.resolve(new Blob())}))
 await expect(cardAvatarBlob('missing')).resolves.toBeNull()
})

it('reads card detail and saves the editable settings with a PATCH',async()=>{
 mocked.mockResolvedValueOnce({card:{...card,presetId:'p1',systemPrompt:'你是一个说书人'}})
 await expect(getCard('c1')).resolves.toMatchObject({id:'c1',presetId:'p1',systemPrompt:'你是一个说书人'})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1')
 // Older builds returned the bare card object; both shapes stay readable.
 mocked.mockResolvedValueOnce({...card,name:'林渡·改'})
 await expect(updateCard('c1',{name:'林渡·改',tags:['相声'],presetId:null})).resolves.toMatchObject({name:'林渡·改'})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1',{method:'PATCH',key:'fixture-key',body:{name:'林渡·改',tags:['相声'],presetId:null}})
})

it('reads, creates, edits and deletes world-book lore',async()=>{
 const lore={id:'l1',name:'雨夜',keys:['雨','屋檐'],content:'屋檐下躲雨时……',enabled:true,order:100,source:'manual' as const}
 mocked.mockResolvedValueOnce({items:[lore]})
 await expect(listLore('c1')).resolves.toEqual([lore])
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/lore')
 mocked.mockResolvedValueOnce({lore})
 await expect(createLore('c1',{name:'雨夜',keys:['雨'],content:'屋檐下躲雨时……'})).resolves.toEqual(lore)
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/lore',{method:'POST',key:'fixture-key',body:{name:'雨夜',keys:['雨'],content:'屋檐下躲雨时……'}})
 mocked.mockResolvedValueOnce({...lore,enabled:false})
 await expect(updateLore('c1','l1',{enabled:false})).resolves.toMatchObject({enabled:false})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/lore/l1',{method:'PATCH',key:'fixture-key',body:{enabled:false}})
 mocked.mockResolvedValueOnce({})
 await deleteLore('c1','l1')
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/lore/l1',{method:'DELETE',key:'fixture-key'})
})

it('manages presets and tolerates both envelope and bare returns',async()=>{
 const preset={id:'p1',name:'细腻描写',systemSuffix:'多写环境与心理。',enabled:true}
 mocked.mockResolvedValueOnce({items:[preset]})
 await expect(listPresets()).resolves.toEqual([preset])
 expect(mocked).toHaveBeenLastCalledWith('/presets')
 mocked.mockResolvedValueOnce({preset})
 await expect(createPreset({name:'细腻描写',systemSuffix:'多写环境与心理。'})).resolves.toEqual(preset)
 expect(mocked).toHaveBeenLastCalledWith('/presets',{method:'POST',key:'fixture-key',body:{name:'细腻描写',systemSuffix:'多写环境与心理。'}})
 mocked.mockResolvedValueOnce(preset)
 await expect(updatePreset('p1',{enabled:false})).resolves.toMatchObject({id:'p1'})
 expect(mocked).toHaveBeenLastCalledWith('/presets/p1',{method:'PATCH',key:'fixture-key',body:{enabled:false}})
 mocked.mockResolvedValueOnce({})
 await deletePreset('p1')
 expect(mocked).toHaveBeenLastCalledWith('/presets/p1',{method:'DELETE',key:'fixture-key'})
})

it('manages output replacement rules through /regex-scripts',async()=>{
 const rule={id:'r1',name:'句号改感叹号',pattern:'。$',replacement:'！',enabled:true,order:10}
 mocked.mockResolvedValueOnce({items:[rule]})
 await expect(listRegexScripts()).resolves.toEqual([rule])
 expect(mocked).toHaveBeenLastCalledWith('/regex-scripts')
 mocked.mockResolvedValueOnce({regex:rule})
 await expect(createRegexScript({name:'句号改感叹号',pattern:'。$',replacement:'！'})).resolves.toEqual(rule)
 expect(mocked).toHaveBeenLastCalledWith('/regex-scripts',{method:'POST',key:'fixture-key',body:{name:'句号改感叹号',pattern:'。$',replacement:'！'}})
 mocked.mockResolvedValueOnce(rule)
 await expect(updateRegexScript('r1',{order:20})).resolves.toMatchObject({id:'r1'})
 expect(mocked).toHaveBeenLastCalledWith('/regex-scripts/r1',{method:'PATCH',key:'fixture-key',body:{order:20}})
 mocked.mockResolvedValueOnce({})
 await deleteRegexScript('r1')
 expect(mocked).toHaveBeenLastCalledWith('/regex-scripts/r1',{method:'DELETE',key:'fixture-key'})
})

it('previews the injected context for a draft message',async()=>{
 const preview={loreMatched:[{id:'l1',name:'雨夜',keys:['雨']}],preset:{id:'p1',name:'细腻描写'},systemSuffix:'多写环境与心理。'}
 mocked.mockResolvedValueOnce(preview)
 await expect(contextPreview('c1','外面下雨了')).resolves.toEqual(preview)
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/context-preview',{method:'POST',body:{message:'外面下雨了'}})
})

it('manages card memories and toggles the long-term companion flag',async()=>{
 const memory={id:'m1',kind:'fact' as const,title:'读者养猫',content:'读者养了一只叫团子的猫。',salience:60,enabled:true,source:'manual' as const,updatedAt:'2026-11-04T02:00:00Z'}
 mocked.mockResolvedValueOnce({items:[memory]})
 await expect(listMemories('c1')).resolves.toEqual([memory])
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/memories')
 mocked.mockResolvedValueOnce({memory})
 await expect(createMemory('c1',{kind:'fact',title:'读者养猫',content:'读者养了一只叫团子的猫。',salience:60})).resolves.toEqual(memory)
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/memories',{method:'POST',key:'fixture-key',body:{kind:'fact',title:'读者养猫',content:'读者养了一只叫团子的猫。',salience:60}})
 mocked.mockResolvedValueOnce({memory:{...memory,enabled:false}})
 await expect(updateMemory('c1','m1',{enabled:false})).resolves.toMatchObject({enabled:false})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/memories/m1',{method:'PATCH',key:'fixture-key',body:{enabled:false}})
 mocked.mockResolvedValueOnce({})
 await deleteMemory('c1','m1')
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/memories/m1',{method:'DELETE',key:'fixture-key'})
 mocked.mockResolvedValueOnce({added:[memory],skipped:2})
 await expect(extractMemories('c1')).resolves.toEqual({added:[memory],skipped:2})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/memories/extract',{method:'POST',key:'fixture-key'})
 mocked.mockResolvedValueOnce({card:{...card,companion:true}})
 await expect(setCompanion('c1',true)).resolves.toMatchObject({companion:true})
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/companion',{method:'POST',key:'fixture-key',body:{companion:true}})
})

it('drafts a character from a brief without importing it',async()=>{
 mocked.mockResolvedValueOnce({draft:{name:'林渡',description:'旧书摊主',personality:'温和',scenario:'北平',firstMessage:'来了？',tags:['旧书']}})
 const draft=await draftCard('雨夜旧书店的青年')
 expect(mocked).toHaveBeenCalledWith('/character-cards/draft',{method:'POST',key:'fixture-key',body:{brief:'雨夜旧书店的青年'}})
 expect(draft.name).toBe('林渡');expect(draft.tags).toEqual(['旧书'])
})

it('requests greeting candidates for a card',async()=>{
 mocked.mockResolvedValueOnce({greetings:['雨停了吗？','进来看看书吧。',42]})
 await expect(cardGreetings('c1',3)).resolves.toEqual(['雨停了吗？','进来看看书吧。'])
 expect(mocked).toHaveBeenCalledWith('/character-cards/c1/greetings',{method:'POST',key:'fixture-key',body:{count:3}})
})
