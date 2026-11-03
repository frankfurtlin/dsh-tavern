import {test,expect,type Page} from '@playwright/test'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR
if(!artifacts)throw new Error('An explicit owned evidence directory is required')
mkdirSync(artifacts,{recursive:true})
async function select(page:Page,text:string){
  await page.locator('[data-reader-content]').filter({hasText:'甲😀乙'}).first().evaluate((element,wanted)=>{
    const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT),nodes:Text[]=[];while(walker.nextNode())nodes.push(walker.currentNode as Text)
    const whole=nodes.map(node=>node.data).join(''),start=whole.indexOf(wanted);if(start<0)throw new Error('Synthetic selection text missing')
    const point=(offset:number)=>{let read=0;for(const node of nodes){if(offset<=read+node.length)return {node,offset:offset-read};read+=node.length}throw new Error('Invalid selection endpoint')}
    const first=point(start),last=point(start+wanted.length),range=document.createRange();range.setStart(first.node,first.offset);range.setEnd(last.node,last.offset)
    const selection=getSelection();selection?.removeAllRanges();selection?.addRange(range);document.dispatchEvent(new Event('selectionchange'))
  },text)
  await expect(page.getByRole('toolbar',{name:'文字批注'})).toBeVisible()
}
test('actual EPUB selections persist exact emoji ranges, survive offline reload and preserve a remote note conflict',async({page,context,browser})=>{
  const url=process.env.E2E_BASE_URL
  if(url!=='http://127.0.0.1:18828')throw new Error('Only owned Next annotation fixture 18828 is allowed')
  expect((await(await page.request.get('/health/ready')).json()).aiMode).toBe('mock')
  const base='/api/reader/v1',username='annotation_'+Date.now().toString(36),password='owned-annotation-fixture-20261005' // gitleaks:allow -- synthetic test fixture
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const login=page.waitForResponse(response=>response.url().endsWith('/auth/login')&&response.status()===200)
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const headers={Authorization:'Bearer '+(await(await login).json()).accessToken}
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click();await(await chooser).setFiles(path.resolve('../tests/fixtures/next-annotations.epub'))
  await page.getByRole('button',{name:'确认并开始阅读',exact:true}).click();await expect(page.locator('.reader-body')).toContainText('甲😀乙')
  const bookId=new URL(page.url()).pathname.split('/')[2],book=await(await page.request.get(base+'/books/'+bookId,{headers})).json(),branchId=book.originalBranchId,revisionId=book.originalRevisionId
  const usageBefore=await(await page.request.get(base+'/usage',{headers})).json(),originalText=await page.locator('[data-reader-content]').filter({hasText:'甲😀乙'}).first().textContent()
  await select(page,'甲😀乙');await page.getByRole('toolbar',{name:'文字批注'}).getByRole('button',{name:'划线',exact:true}).click()
  const list=async()=>await(await page.request.get(base+`/books/${bookId}/bookmarks?branchId=${branchId}&revisionId=${revisionId}`,{headers})).json()
  await expect.poll(async()=>(await list()).items.length).toBe(1)
  const mark=(await list()).items[0];expect(mark.anchor.end-mark.anchor.start).toBe(4);expect(mark.anchor.offsetUnit).toBe('utf16')
  expect(mark.anchor.bookId).toBe(bookId);expect(mark.anchor.branchId).toBe(branchId);expect(mark.anchor.revisionId).toBe(revisionId)
  await expect.poll(async()=>(await page.locator('.reader-body mark.reading-highlight').allTextContents()).join('')).toBe('甲😀乙')
  expect(await page.locator('[data-reader-content]').filter({hasText:'甲😀乙'}).first().textContent()).toBe(originalText)
  await page.reload();await expect.poll(async()=>(await page.locator('.reader-body mark.reading-highlight').allTextContents()).join('')).toBe('甲😀乙')
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'查看书签',exact:true}).click();await page.getByRole('button',{name:'添加笔记',exact:true}).click()
  await page.getByLabel('我的阅读笔记').fill('划线的初始想法');await page.getByRole('button',{name:'保存笔记',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
  const saved=(await list()).items[0]
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'下载这条故事线',exact:true}).click()
  const download=page.getByRole('dialog',{name:'下载故事线',exact:true});await download.getByRole('button',{name:'下载这条故事线',exact:true}).click();await expect(download.getByRole('status')).toHaveText('已下载，可离线阅读')
  await download.getByRole('link',{name:'已下载的故事线',exact:true}).click()
  const other=await browser.newContext({baseURL:url})
  try{
    const otherLogin=await other.request.post(base+'/auth/login',{data:{username,password,clientType:'web',deviceName:'annotation-conflict-second-browser'}}),otherHeaders={Authorization:'Bearer '+(await otherLogin.json()).accessToken}
    await context.setOffline(true);await page.reload();await page.getByRole('link',{name:'阅读',exact:true}).click()
    await expect.poll(async()=>(await page.locator('.reader-body mark.reading-highlight').allTextContents()).join('')).toBe('甲😀乙')
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'查看书签',exact:true}).click();await page.getByRole('button',{name:'编辑笔记',exact:true}).click()
    await page.getByLabel('我的阅读笔记').fill('保留我的离线想法');await page.getByRole('button',{name:'保存笔记',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
    await select(page,'信');await page.getByRole('toolbar',{name:'文字批注'}).getByRole('button',{name:'划线',exact:true}).click()
    await page.reload();await expect.poll(async()=>(await page.locator('.reader-body mark.reading-highlight').allTextContents()).join('')).toBe('甲😀乙信')
    const remote=await other.request.patch(base+`/books/${bookId}/bookmarks/${saved.id}`,{headers:otherHeaders,data:{expectedVersion:saved.version,note:'另一设备的划线想法'}});expect(remote.status(),await remote.text()).toBe(200)
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(artifacts,'annotations-offline-390.png')})
    await context.setOffline(false);await page.reload()
    await expect.poll(async()=>(await list()).items.map((note:{note:string})=>note.note).sort()).toEqual(['','保留我的离线想法','另一设备的划线想法'].sort())
    await page.goto('/notebook');await expect(page.locator('.note-card')).toHaveCount(2)
    await page.locator('.note-card').filter({hasText:'保留我的离线想法'}).getByRole('button',{name:'回到这一段'}).click()
    await expect.poll(()=>new URL(page.url()).searchParams.has('anchor')).toBe(true)
    const returned=JSON.parse(new URL(page.url()).searchParams.get('anchor')||'null');expect(returned).toEqual(mark.anchor)
    await expect.poll(async()=>(await page.locator('.reader-body mark.reading-highlight').allTextContents()).join('')).toBe('甲😀乙信')
    await page.setViewportSize({width:1440,height:900});await page.screenshot({path:path.join(artifacts,'annotations-online-1440.png')})
    const usageAfter=await(await page.request.get(base+'/usage',{headers})).json();expect(usageAfter).toEqual(usageBefore)
    writeFileSync(path.join(artifacts,'annotations-observations.json'),JSON.stringify({testType:'actual-chromium-dom-idb-serviceworker-postgresql',providerCallsReal:0,epubInlineSelection:true,selectedRangeUtf16:4,emojiAndInlineFormattingPreserved:true,originalProseUnchanged:true,persistedAfterOnlineReload:true,offlineHighlightCreated:true,offlineHighlightPersistsAfterReload:true,remoteEditAndLocalConflictCopyPreserved:true,notebookReturnsExactAnchor:true,usageUnchanged:true,limitations:['No native or physical device certification','No child-line annotation or CAS DELETE certification in this test']},null,2))
  }finally{await context.setOffline(false);await other.close()}
})
