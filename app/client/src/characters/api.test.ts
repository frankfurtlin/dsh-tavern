import {beforeEach,it,expect,vi} from 'vitest'
vi.mock('../api',()=>({api:vi.fn(),authorizationHeaders:vi.fn(()=>({Authorization:'Bearer token'}))}))
vi.mock('../platform/bridge',()=>({backendBase:()=>''}))
import {api} from '../api'
import {cardAvatarBlob,deleteCard,importCard,listCards,startCardChat} from './api'
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
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1/chats',{method:'POST'})
 mocked.mockResolvedValueOnce({})
 await deleteCard('c1')
 expect(mocked).toHaveBeenLastCalledWith('/character-cards/c1',{method:'DELETE'})
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
