import {afterEach,beforeEach,it,expect,vi} from 'vitest'
import {authorizedBlob,safeFileName,saveBlob} from './download'

vi.mock('../api',()=>({authorizationHeaders:()=>({Authorization:'Bearer token'})}))
vi.mock('./bridge',()=>({backendBase:()=>'http://localhost:8810'}))

let fetchMock:ReturnType<typeof vi.fn>

beforeEach(()=>{
  fetchMock=vi.fn()
  vi.stubGlobal('fetch',fetchMock)
  vi.useFakeTimers()
})
afterEach(()=>{
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('sanitizes download names and keeps the fallback for empty names',()=>{
  expect(safeFileName('林渡/雨夜:*?')).toBe('林渡_雨夜___')
  expect(safeFileName('   ','角色卡')).toBe('角色卡')
})

it('fetches authorized blobs with the reader base url and bearer header',async()=>{
  const blob=new Blob(['png'])
  fetchMock.mockResolvedValue({ok:true,status:200,blob:async()=>blob})
  await expect(authorizedBlob('/character-cards/c1/export')).resolves.toBe(blob)
  expect(fetchMock).toHaveBeenCalledWith('http://localhost:8810/api/reader/v1/character-cards/c1/export',{credentials:'include',headers:{Authorization:'Bearer token'}})
})

it('turns a missing endpoint into a friendly message instead of crashing',async()=>{
  fetchMock.mockResolvedValue({ok:false,status:404})
  await expect(authorizedBlob('/world-chats/chat-1/injection')).rejects.toThrow('服务端暂不支持这个导出，请稍后重试。')
})

it('clicks a temporary download link and releases the object url',()=>{
  const createObjectURL=vi.fn(()=>'blob:preview')
  const revokeObjectURL=vi.fn()
  vi.stubGlobal('URL',{createObjectURL,revokeObjectURL})
  const link={href:'',download:'',rel:'',click:vi.fn(),remove:vi.fn()}
  const appendChild=vi.fn()
  vi.stubGlobal('document',{createElement:()=>link,body:{appendChild}})
  saveBlob(new Blob(['jsonl']),'林渡.jsonl')
  expect(link.download).toBe('林渡.jsonl')
  expect(link.click).toHaveBeenCalledTimes(1)
  expect(appendChild).toHaveBeenCalledWith(link)
  expect(link.remove).toHaveBeenCalledTimes(1)
  expect(createObjectURL).toHaveBeenCalledTimes(1)
  vi.advanceTimersByTime(1000)
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview')
})
