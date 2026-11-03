import {test,expect} from '@playwright/test'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR
if(!artifacts)throw new Error('Explicit new owned artifacts required')
mkdirSync(artifacts,{recursive:true})
const base='/api/reader/v1',worldBase='/api/world/v1'
test('source removal preserves its frozen world and independent publication while clearing downloaded source data',async({page,context})=>{
  test.setTimeout(180000)
  const url=process.env.E2E_BASE_URL
  if(url!=='http://127.0.0.1:18828')throw new Error('Only owned Next source deletion fixture 18828 is allowed')
  const health=await page.request.get(url+'/health/ready');expect((await health.json()).aiMode).toBe('mock')
  await page.goto('/register');await page.getByLabel('用户名').fill('delete_'+Date.now().toString(36));await page.getByLabel('密码').fill('owned-source-delete-password-20261005')
  const loginPromise=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const login=await(await loginPromise).json(),headers={Authorization:'Bearer '+login.accessToken}
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click();await(await chooser).setFiles(path.resolve('../tests/fixtures/next-offline-images.epub'))
  await expect(page.getByRole('button',{name:'确认并开始阅读',exact:true})).toBeVisible({timeout:15000});await page.getByRole('button',{name:'确认并开始阅读',exact:true}).click();await expect(page.locator('.reader-body')).toContainText('甲😀乙')
  const sourceId=new URL(page.url()).pathname.split('/')[2]
  const source=await(await page.request.get(base+'/books/'+sourceId,{headers})).json()
  async function download(){
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'下载这条故事线',exact:true}).click()
    const dialog=page.getByRole('dialog',{name:'下载故事线',exact:true});await dialog.getByRole('button',{name:'下载这条故事线',exact:true}).click()
    await expect(dialog.getByRole('status')).toHaveText('已下载，可离线阅读',{timeout:20000});await dialog.getByRole('link',{name:'已下载的故事线',exact:true}).click()
    await expect(page.getByRole('heading',{name:'已下载',exact:true})).toBeVisible()
  }
  await download()
  const otherResponse=await page.request.post(base+'/books/demo',{headers:{...headers,'Idempotency-Key':'source-delete-unrelated-demo'},data:{}});expect(otherResponse.status()).toBe(201)
  const other=await otherResponse.json()
  await page.goto(`/books/${other.bookId}/read?branch=${other.branchId}`);await expect(page.locator('.reader-body .paragraph').first()).toBeVisible();await download()
  const profile=await page.request.post(base+'/model-profiles',{headers,data:{name:'Owned source deletion fixture',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'TEST-KEY-NOT-REAL',contextWindow:32768,callBudget:30,inputBudget:1000000,outputBudget:200000}})
  expect(profile.status()).toBe(201);expect((await page.request.put(base+'/model-profiles/'+(await profile.json()).id+'/default',{headers})).ok()).toBeTruthy()
  const directory=await(await page.request.get(base+`/branches/${source.originalBranchId}/chapters?revisionId=${source.originalRevisionId}`,{headers})).json()
  const chapter=await(await page.request.get(base+`/branches/${source.originalBranchId}/chapters/${directory.items[0].id}?revisionId=${source.originalRevisionId}`,{headers})).json(),anchor=chapter.blocks.at(-1).endAnchor
  const progress=await(await page.request.get(base+`/books/${sourceId}/progress?branchId=${source.originalBranchId}&revisionId=${source.originalRevisionId}`,{headers})).json()
  const saved=await page.request.put(base+`/books/${sourceId}/progress`,{headers,data:{branchId:source.originalBranchId,revisionId:source.originalRevisionId,anchor,expectedProgressVersion:progress.version,deviceId:login.sessionId,clientSeq:5000}})
  expect(saved.status(),await saved.text()).toBe(200)
  await page.goto(`/worlds/new?book=${sourceId}&branch=${source.originalBranchId}&anchor=${encodeURIComponent(JSON.stringify(anchor))}`)
  await page.getByLabel('世界名称',{exact:true}).fill('删除来源后仍在的世界')
  const createdPromise=page.waitForResponse(r=>r.url().endsWith(worldBase+'/worlds')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'建立世界',exact:true}).click();const created=await(await createdPromise).json()
  await expect(page.getByRole('heading',{name:'你想成为谁？'})).toBeVisible({timeout:30000})
  await page.getByRole('button',{name:/一个新人物/}).click();await page.getByLabel('你的名字',{exact:true}).fill('雨客');await page.getByLabel('一句话身份',{exact:true}).fill('路过灯塔的旅人，想了解眼前的船与旧信。')
  await page.getByRole('button',{name:'进入故事',exact:true}).click();await expect(page.locator('.world-options button')).toHaveCount(3,{timeout:30000})
  const worldId=created.worldId,lineId=new URL(page.url()).searchParams.get('line')!
  await expect.poll(async()=>{const line=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json();return !line.pendingTaskId&&!!line.headCheckpointId}).toBe(true)
  const opening=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()
  await page.getByLabel('你的下一步行动').fill('我观察灯下的旧信，向身边的人询问眼前的情况。');await page.getByRole('button',{name:'提交行动',exact:true}).click()
  await expect.poll(async()=>{const line=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json();return !line.pendingTaskId&&line.headCheckpointId!==opening.headCheckpointId},{timeout:30000}).toBe(true)
  const before=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()
  const publicHistory=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}/history`,{headers})).json()
  const prose=publicHistory.items.filter((cp:{text:string;kind:string})=>cp.text&&cp.kind!=='start').map((cp:{text:string})=>cp.text)
  expect(prose.length).toBeGreaterThanOrEqual(2)
  await page.goto(`/worlds/${worldId}/read?line=${lineId}`);await expect(page.locator('.world-read-chapter')).toHaveCount(prose.length)
  await page.getByRole('button',{name:'世界正文目录',exact:true}).click();await page.getByRole('button',{name:'下载这条世界线',exact:true}).click()
  const worldDownload=page.getByRole('dialog',{name:'下载世界线',exact:true});await worldDownload.getByRole('button',{name:'下载这条世界线',exact:true}).click();await expect(worldDownload.getByRole('status')).toHaveText('已下载，可离线阅读')
  await worldDownload.getByRole('link',{name:'已下载的故事线',exact:true}).click();await expect(page.locator('[data-download-kind="world"]')).toHaveCount(1)
  await page.goto(`/worlds/${worldId}/play?line=${lineId}`);await expect(page.locator('.world-options button')).toHaveCount(3)
  const publishPromise=page.waitForResponse(r=>r.url().includes('/publish')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'世界操作',exact:true}).click();await page.getByRole('button',{name:'存为阅读故事线',exact:true}).click()
  const publication=await(await publishPromise).json();expect(publication.bookId).not.toBe(sourceId)
  await expect(page.locator('.reader-body .paragraph').first()).toBeVisible({timeout:15000});await download()
  await expect(page.locator('.note-card')).toHaveCount(4)
  const usageBefore=await(await page.request.get(base+'/usage',{headers})).json()
  await page.goto('/stories/book-'+sourceId);await expect(page.getByRole('heading',{name:source.title,exact:true})).toBeVisible()
  await page.getByRole('button',{name:'管理故事',exact:true}).click();await page.getByText('移除小说',{exact:true}).click()
  const deletionPromise=page.waitForResponse(r=>r.url().endsWith(base+'/books/'+sourceId)&&r.request().method()==='DELETE')
  await page.getByRole('button',{name:'删除这本小说',exact:true}).click();const deletionResponse=await deletionPromise
  expect(deletionResponse.status()).toBe(202);const deletion=await deletionResponse.json();expect(deletion.deleted).toBe(true)
  await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  await expect.poll(async()=>{const receipt=await(await page.request.get(base+`/books/${sourceId}/deletion`,{headers})).json();return receipt.cleanupState},{timeout:15000}).toBe('completed')
  const original=await page.request.get(base+'/books/'+sourceId,{headers});expect(original.status()).toBe(404);expect((await original.json()).code).toBe('BOOK_DELETED')
  const retainedWorld=await(await page.request.get(worldBase+'/worlds/'+worldId,{headers})).json();expect(retainedWorld.sourceRemoved).toBe(true);expect(retainedWorld.status).toBe('active')
  const retainedBook=await(await page.request.get(base+'/books/'+publication.bookId,{headers})).json();expect(retainedBook.id).toBe(publication.bookId)
  await page.goto('/downloads');await expect(page.locator('.note-card')).toHaveCount(3);await expect(page.locator(`a[href*="/books/${sourceId}/read"]`)).toHaveCount(0)
  await expect.poll(()=>page.evaluate(async({sourceId,branchId})=>{
    const database=await new Promise<IDBDatabase>((resolve,reject)=>{const request=indexedDB.open('story-reader-private',1);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})
    try{return await new Promise<number>((resolve,reject)=>{const request=database.transaction('cache','readonly').objectStore('cache').getAllKeys();request.onsuccess=()=>resolve(request.result.filter(key=>{const value=String(key);return value.includes('offline-reading-package:v1:'+sourceId+':')||value.includes('api:/books/'+sourceId)||value.includes('api:/branches/'+branchId)||value.includes('asset:'+sourceId+':')}).length);request.onerror=()=>reject(request.error)})}finally{database.close()}
  },{sourceId,branchId:source.originalBranchId}),{timeout:15000}).toBe(0)
  // Remove only this synthetic world's ordinary API cache. The explicit
  // public package must suffice for the complete downloaded history.
  const worldPackAudit=await page.evaluate(async({worldId,headId})=>{
    const owner=localStorage.getItem('reader-account')!,database=await new Promise<IDBDatabase>((resolve,reject)=>{const request=indexedDB.open('story-reader-private',1);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})
    try{return await new Promise<{publicOnly:boolean;headId:string;historyCount:number}>((resolve,reject)=>{const tx=database.transaction('cache','readwrite'),store=tx.objectStore('cache'),keys=store.getAllKeys();let pack:any
      keys.onsuccess=()=>{for(const key of keys.result){const name=String(key);if(name.startsWith(owner+':world-api:')&&name.includes('/worlds/'+worldId))store.delete(key);if(name.startsWith(owner+':world-offline-package:v1:'+worldId+':')&&name.endsWith(':'+headId)){const request=store.get(key);request.onsuccess=()=>{pack=request.result}}}}
      tx.oncomplete=()=>{if(!pack){reject(new Error('Synthetic explicit world package missing'));return}const forbidden=new Set(['state','definition','seed','sourceBookId','sourceAnchor','gm','rules','hiddenGoals','fullBookReference']);const privateKey=(value:any):boolean=>!!value&&typeof value==='object'&&Object.entries(value).some(([key,item])=>forbidden.has(key)||privateKey(item));resolve({publicOnly:!privateKey(pack.payload),headId:pack.payload.line.headCheckpointId,historyCount:pack.payload.history.length})};tx.onerror=()=>reject(tx.error)
    })}finally{database.close()}
  },{worldId,headId:before.headCheckpointId})
  expect(worldPackAudit.publicOnly).toBe(true);expect(worldPackAudit.headId).toBe(before.headCheckpointId);expect(worldPackAudit.historyCount).toBe(publicHistory.items.length)
  await page.screenshot({path:path.join(artifacts,'source-removed-downloads.png'),fullPage:true})
  await context.setOffline(true);await page.reload();await expect(page.locator('.note-card')).toHaveCount(3)
  await page.locator(`a[href*="/books/${other.bookId}/read"]`).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
  await page.goto('/downloads');await expect(page.locator('.note-card')).toHaveCount(3)
  await page.locator(`[data-download-kind="book"] a[href*="/books/${retainedBook.id}/read"]`).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
  await page.goto('/downloads');await page.locator('[data-download-kind="world"]').getByRole('link',{name:'阅读',exact:true}).click()
  await expect(page.locator('.world-read-chapter')).toHaveCount(prose.length);expect(await page.locator('.world-prose').allTextContents()).toEqual(prose)
  await expect(page.getByRole('link',{name:'回到故事，继续行动'})).toHaveCount(0)
  await page.reload();await expect(page.locator('.world-read-chapter')).toHaveCount(prose.length)
  await page.goto(`/worlds/${worldId}/play?line=${lineId}`);await expect(page.locator('.world-options button')).toHaveCount(0);await expect(page.locator('.world-play .empty .error')).toHaveText('网络不可用，已保存的内容仍可阅读')
  await page.goto(`/books/${sourceId}/read?branch=${source.originalBranchId}`);await expect(page.getByRole('alert')).toBeVisible();await expect(page.locator('.reader-body')).toHaveCount(0)
  await context.setOffline(false);await page.goto(`/worlds/${worldId}/play?line=${lineId}`);await expect(page.locator('.world-play-title')).toContainText('来源已移除')
  await page.getByRole('button',{name:'世界操作',exact:true}).click();await expect(page.getByRole('link',{name:'回到来源小说'})).toHaveCount(0);await page.getByRole('button',{name:'关闭',exact:true}).click()
  const usageAfter=await(await page.request.get(base+'/usage',{headers})).json();expect(usageAfter).toEqual(usageBefore)
  expect((await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json()).headCheckpointId).toBe(before.headCheckpointId)
  await page.screenshot({path:path.join(artifacts,'source-removed-world.png')})
  await page.getByLabel('你的下一步行动').fill('我观察身边的人，询问这封旧信来自哪里。');await page.getByRole('button',{name:'提交行动',exact:true}).click()
  await expect.poll(async()=>{const line=await(await page.request.get(worldBase+`/worlds/${worldId}/lines/${lineId}`,{headers})).json();return !line.pendingTaskId&&line.headCheckpointId!==before.headCheckpointId},{timeout:30000}).toBe(true)
  writeFileSync(path.join(artifacts,'source-delete-observations.json'),JSON.stringify({testType:'actual-chromium-idb-serviceworker-owned-postgresql-mock',providerCallsReal:0,sourceRemoved:true,sourceOfflinePackageRemoved:true,sourcePrivateIndexedDbKeysPhysicallyRemoved:true,otherBookPackagePreserved:true,otherBookActuallyReadOffline:true,independentPublicationReadableOffline:true,worldExplicitPublicPackagePreserved:true,worldCompleteHistoryReadableOfflineAfterApiCacheRemoved:true,worldPackageContainsNoPrivateState:true,offlinePlayNeverPretendsSuccess:true,frozenWorldContinuesAfterSourceRemoval:true,usageUnchangedDuringDeleteReadAndNavigation:true,limitations:['Legacy embedded publication link migration has separate backend/SFC evidence, not this future-publication browser flow','No physical native filesystem or paid provider quality certification']},null,2))
})
