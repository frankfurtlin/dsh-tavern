import {beforeEach,afterEach,it,expect,vi} from 'vitest'

const bridge=vi.hoisted(()=>({clear:vi.fn(),cacheSet:vi.fn(),cacheGet:vi.fn(),owner:'old-account-id'}))
vi.mock('../platform/bridge',()=>({backendBase:()=>'',native:false,secureRefresh:async()=>null,cacheGet:bridge.cacheGet,cacheSet:bridge.cacheSet,clearPrivate:bridge.clear,accountId:()=>bridge.owner}))
beforeEach(()=>{vi.resetModules();bridge.clear.mockReset();bridge.cacheSet.mockReset();bridge.cacheGet.mockReset();bridge.owner='old-account-id';vi.stubGlobal('window',new EventTarget());vi.stubGlobal('localStorage',{getItem:()=>null});vi.stubGlobal('fetch',vi.fn())})
afterEach(()=>vi.unstubAllGlobals())
it('does not replay a private POST under another account after refresh has resolved',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch)
  const data={user:{id:'old-account-id'},get accessToken(){queueMicrotask(()=>{client.beginAuthentication();client.setAccess('new-account')});return'old-account-refreshed'}}
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce({ok:true,status:200,json:async()=>data} as Response)
  client.setAccess('old-account')
  await expect(client.api('/model-profiles',{method:'POST',body:{name:'private configuration',key:'TEST-ONLY-SECRET'}})).rejects.toMatchObject({code:'SESSION_CHANGED'})
  expect(fetchMock.mock.calls.map(call=>call[0])).toEqual(['/api/reader/v1/model-profiles','/api/reader/v1/auth/refresh'])
  expect(bridge.clear).not.toHaveBeenCalled();expect(bridge.cacheSet).not.toHaveBeenCalled()
})
it('rejects a refresh cookie belonging to another browser tab account before replaying a private POST',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch)
  bridge.clear.mockImplementationOnce(async()=>{bridge.owner='';window.dispatchEvent(new Event('reader-private-cleared'))})
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce({ok:true,status:200,json:async()=>({accessToken:'another-tab-token',user:{id:'another-account-id'}})} as Response)
  client.setAccess('old-account')
  await expect(client.api('/model-profiles',{method:'POST',body:{name:'private configuration',key:'TEST-ONLY-SECRET'}})).rejects.toMatchObject({code:'SESSION_CHANGED'})
  expect(fetchMock).toHaveBeenCalledTimes(2);expect(bridge.clear).toHaveBeenCalledTimes(1);expect(bridge.cacheSet).not.toHaveBeenCalled();expect(client.authorizationHeaders()).toEqual({})
})
it('does not clear a newer session after the previous session refresh failed',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch)
  bridge.clear.mockImplementationOnce(async()=>{window.dispatchEvent(new Event('reader-private-cleared'));client.beginAuthentication();client.setAccess('new-account')})
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce(new Response('{}',{status:401}))
  client.setAccess('old-account')
  await expect(client.api('/settings',{method:'PUT',body:{expectedVersion:1,fontSize:24}})).rejects.toMatchObject({code:'SESSION_CHANGED'})
  expect(bridge.clear).toHaveBeenCalledTimes(1);expect(client.authorizationHeaders()).toEqual({Authorization:'Bearer new-account'});expect(fetchMock).toHaveBeenCalledTimes(2)
})
it('keeps an offline account and pending mutation when refresh is interrupted by navigation',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch)
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockRejectedValueOnce(new DOMException('Navigation interrupted request','AbortError'))
  await expect(client.api('/books/book/progress',{method:'PUT',body:{expectedProgressVersion:1}})).rejects.toMatchObject({code:'NETWORK_UNAVAILABLE',status:0})
  expect(bridge.clear).not.toHaveBeenCalled();expect(bridge.cacheSet).not.toHaveBeenCalled();expect(bridge.owner).toBe('old-account-id');expect(fetchMock).toHaveBeenCalledTimes(2)
})
it('reads account cache if an expired access token cannot be refreshed over the network',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch),cached={anchor:{chapterId:'cached-second-chapter'},version:3}
  bridge.cacheGet.mockResolvedValueOnce(cached)
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockRejectedValueOnce(new TypeError('Network unavailable'))
  expect(await client.api('/books/book/progress',{cache:true})).toEqual(cached)
  expect(bridge.cacheGet).toHaveBeenCalledWith('api:/books/book/progress');expect(bridge.clear).not.toHaveBeenCalled();expect(fetchMock).toHaveBeenCalledTimes(2)
})
it('still clears an account when the server rejects its refresh credential',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch)
  bridge.clear.mockImplementationOnce(async()=>{bridge.owner='';window.dispatchEvent(new Event('reader-private-cleared'))})
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce(new Response('{}',{status:401}))
  await expect(client.api('/books/book/progress',{cache:true})).rejects.toMatchObject({code:'SESSION_CHANGED'})
  expect(bridge.clear).toHaveBeenCalledTimes(1);expect(bridge.cacheGet).not.toHaveBeenCalled();expect(fetchMock).toHaveBeenCalledTimes(2)
})
it('clears a newly refreshed credential if the server rejects it on the single permitted retry',async()=>{
  const client=await import('./index'),fetchMock=vi.mocked(fetch)
  bridge.clear.mockImplementationOnce(async()=>{bridge.owner='';window.dispatchEvent(new Event('reader-private-cleared'))})
  fetchMock.mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce(new Response(JSON.stringify({accessToken:'refreshed-token',user:{id:'old-account-id'}}))).mockResolvedValueOnce(new Response('{}',{status:401}))
  await expect(client.api('/settings',{method:'PUT',body:{fontSize:24}})).rejects.toMatchObject({code:'SESSION_CHANGED'})
  expect(bridge.clear).toHaveBeenCalledTimes(1);expect(fetchMock).toHaveBeenCalledTimes(3);expect(client.authorizationHeaders()).toEqual({})
})
