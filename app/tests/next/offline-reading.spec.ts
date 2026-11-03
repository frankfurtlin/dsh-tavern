import {test,expect} from '@playwright/test'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR
if(!artifacts)throw new Error('Explicit owned artifacts required')
mkdirSync(artifacts,{recursive:true})
test('explicit download survives offline restart with an unvisited image and conflict-safe note synchronization',async({page,context,browser})=>{
  test.setTimeout(180000)
  const url=process.env.E2E_BASE_URL
  if(url!=='http://127.0.0.1:18825')throw new Error('Only owned Next offline fixture 18825 is allowed')
  const health=await page.request.get(url+'/health/ready');expect((await health.json()).aiMode).toBe('mock')
  const username='offline_'+Date.now().toString(36),password='owned-offline-fixture-password-20261005',base='/api/reader/v1'
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const loggedIn=page.waitForResponse(response=>response.url().endsWith('/auth/login')&&response.status()===200)
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const headers={Authorization:'Bearer '+(await(await loggedIn).json()).accessToken}
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click()
  await(await chooser).setFiles(path.resolve('../tests/fixtures/next-offline-images.epub'))
  await expect(page.getByRole('button',{name:'确认并开始阅读',exact:true})).toBeVisible({timeout:15000})
  await page.getByRole('button',{name:'确认并开始阅读',exact:true}).click();await expect(page.locator('.reader-body')).toContainText('甲😀乙')
  const bookId=new URL(page.url()).pathname.split('/')[2],book=await(await page.request.get(base+'/books/'+bookId,{headers})).json(),branchId=book.originalBranchId,revisionId=book.originalRevisionId
  const directory=await(await page.request.get(base+`/branches/${branchId}/chapters?revisionId=${revisionId}`,{headers})).json()
  expect(directory.items).toHaveLength(2)
  const chapter=await(await page.request.get(base+`/branches/${branchId}/chapters/${directory.items[0].id}?revisionId=${revisionId}`,{headers})).json(),anchor=chapter.blocks[0].startAnchor
  const noteResponse=await page.request.post(base+`/books/${bookId}/bookmarks`,{headers:{...headers,'Idempotency-Key':'offline-base-note'},data:{anchor,note:'同步前的笔记'}})
  expect(noteResponse.status(),await noteResponse.text()).toBe(201);const remoteNote=await noteResponse.json()
  const usageBefore=await(await page.request.get(base+'/usage',{headers})).json()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'下载这条故事线',exact:true}).click()
  const download=page.getByRole('dialog',{name:'下载故事线',exact:true});await download.getByRole('button',{name:'下载这条故事线',exact:true}).click()
  await expect(download.getByRole('status')).toHaveText('已下载，可离线阅读',{timeout:20000})
  await download.getByRole('link',{name:'已下载的故事线',exact:true}).click();await expect(page.getByRole('heading',{name:'已下载',exact:true})).toBeVisible()
  const other=await browser.newContext({baseURL:url})
  try{
    const login=await other.request.post(base+'/auth/login',{data:{username,password,clientType:'web',deviceName:'offline-note-second-browser'}})
    expect(login.status(),await login.text()).toBe(200);const otherHeaders={Authorization:'Bearer '+(await login.json()).accessToken}
    await context.setOffline(true);await page.reload();await expect(page.getByRole('heading',{name:'已下载',exact:true})).toBeVisible()
    await expect(page.locator('.note-card')).toContainText('2 章')
    await page.getByRole('link',{name:'阅读',exact:true}).click();await expect(page.locator('.reader-body')).toContainText('甲😀乙')
    await page.getByRole('button',{name:'下一章',exact:true}).click();await expect(page.locator('.reader-body')).toContainText('灯影照着潮水')
    const image=page.locator('.reader-body img');await expect(image).toHaveCount(1)
    await expect.poll(()=>image.evaluate(image=>({complete:(image as HTMLImageElement).complete,width:(image as HTMLImageElement).naturalWidth}))).toEqual({complete:true,width:16})
    await page.getByRole('button',{name:'上一章',exact:true}).click();await expect(page.locator('.reader-body')).toContainText('甲😀乙')
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'查看书签',exact:true}).click()
    await page.getByRole('button',{name:'编辑笔记',exact:true}).click();await page.getByLabel('我的阅读笔记').fill('本机离线改动')
    await page.getByRole('button',{name:'保存笔记',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
    await page.getByLabel('我的阅读笔记').fill('另一份离线新笔记');await page.getByRole('button',{name:'保存笔记',exact:true}).click()
    await page.goto('/downloads');await expect(page.getByText('2 条笔记待同步',{exact:true})).toBeVisible()
    for(const size of [{width:390,height:844},{width:1440,height:900}]){await page.setViewportSize(size);const width=await page.evaluate(()=>({inner:innerWidth,scroll:document.documentElement.scrollWidth}));expect(width.scroll).toBeLessThanOrEqual(width.inner+1);await page.screenshot({path:path.join(artifacts,`offline-downloads-${size.width}.png`)})}
    const remoteEdit=await other.request.patch(base+`/books/${bookId}/bookmarks/${remoteNote.id}`,{headers:otherHeaders,data:{expectedVersion:remoteNote.version,note:'另一设备的新想法'}})
    expect(remoteEdit.status(),await remoteEdit.text()).toBe(200)
    await context.setOffline(false);await page.reload()
    await expect.poll(async()=>{const data=await(await other.request.get(base+`/books/${bookId}/bookmarks?branchId=${branchId}&revisionId=${revisionId}`,{headers:otherHeaders})).json();return data.items.map((note:{note:string})=>note.note).sort()},{timeout:15000}).toEqual(['另一份离线新笔记','另一设备的新想法','本机离线改动'].sort())
    await page.reload();await expect(page.getByText('2 条笔记待同步',{exact:true})).toHaveCount(0)
    await context.setOffline(true);await page.reload();await page.getByRole('link',{name:'阅读',exact:true}).click()
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'查看书签',exact:true}).click()
    const marks=page.getByRole('dialog',{name:'段落书签',exact:true})
    await expect(marks).toContainText('另一设备的新想法');await expect(marks).toContainText('本机离线改动');await expect(marks).toContainText('另一份离线新笔记');await expect(marks).toContainText('冲突副本')
    await context.setOffline(false)
    const usageAfter=await(await other.request.get(base+'/usage',{headers:otherHeaders})).json();expect(usageAfter).toEqual(usageBefore)
    writeFileSync(path.join(artifacts,'offline-observations.json'),JSON.stringify({testType:'actual-chromium-idb-serviceworker-postgresql',providerCallsReal:0,explicitCompleteDownload:true,actualOfflineReload:true,unvisitedImageDecoded:true,imageNaturalWidth:16,offlineNotesCreated:1,offlineNotesEdited:1,remoteEditPreserved:true,conflictCopyPreserved:true,secondOfflineReloadNotesPersist:true,usageUnchanged:true,limitations:['No native filesystem or physical-device certification','Offline package source deletion is a separate unfinished batch']},null,2))
  }finally{await context.setOffline(false);await other.close()}
})
