import {test,expect,type Page} from '@playwright/test'
import {mkdirSync,writeFileSync} from 'node:fs'
import path from 'node:path'

const artifacts=process.env.TEST_ARTIFACTS_DIR
if(!artifacts)throw new Error('Explicit owned evidence directory required')
mkdirSync(artifacts,{recursive:true})
const base='/api/reader/v1'
async function queued(page:Page){
  return page.evaluate(async()=>{
    const db=await new Promise<IDBDatabase>((resolve,reject)=>{const request=indexedDB.open('story-reader-private');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})
    try{return await new Promise<any[]>((resolve,reject)=>{const request=db.transaction('cache').objectStore('cache').get(localStorage.getItem('reader-account')+':offline-note-queue:v1');request.onsuccess=()=>resolve(request.result||[]);request.onerror=()=>reject(request.error)})}finally{db.close()}
  })
}
async function newNote(page:Page,text:string){
  await page.locator('.reader-body .paragraph').first().click()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click()
  await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
  await page.getByLabel('我的阅读笔记').fill(text)
}
test('actual lost POST and local cleanup failures recover the same note across offline retry and reopen',async({page,context})=>{
  const url=process.env.E2E_BASE_URL
  if(url!=='http://127.0.0.1:18828')throw new Error('Only immutable owned M fixture 18828 is allowed')
  expect((await(await page.request.get('/health/ready')).json()).aiMode).toBe('mock')
  const reuse=process.env.NEXT_RECOVERY_REUSE,runId=Date.now().toString(36)
  if(reuse&&!/^note_recovery_[a-z0-9]+$/.test(reuse))throw new Error('Only a previously owned synthetic recovery account can be reused')
  const username=reuse||'note_recovery_'+runId,password='owned-note-recovery-20261005'
  await page.goto(reuse?'/login':'/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const login=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:reuse?'登录':'创建账号并进入',exact:true}).click();await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  writeFileSync(path.join(artifacts,'synthetic-recovery-account.json'),JSON.stringify({username,fixture:'reader-world-next-m1-ui-20261005-m',reused:!!reuse,runId},null,2))
  const headers={Authorization:'Bearer '+(await(await login).json()).accessToken}
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click()
  await(await chooser).setFiles({name:'owned-note-recovery-'+runId+'.txt',mimeType:'text/plain',buffer:Buffer.from('第一章 灯下\n桥边的木板响了一声。旅人停下脚步，记下这一次相遇：'+runId+'。\n\n他把旧信放在灯下，等待天色渐明。\n','utf8')})
  await page.getByRole('button',{name:'确认并开始阅读',exact:true}).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
  const bookId=new URL(page.url()).pathname.split('/')[2],bookInfo=await(await page.request.get(base+'/books/'+bookId,{headers})).json()
  const book={bookId,branchId:bookInfo.originalBranchId},readUrl=`/books/${bookId}/read?branch=${book.branchId}`,revisionId=bookInfo.originalRevisionId
  await page.goto(readUrl);await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
  const usageBefore=await(await page.request.get(base+'/usage',{headers})).json()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'下载这条故事线',exact:true}).click()
  const download=page.getByRole('dialog',{name:'下载故事线',exact:true});await download.getByRole('button',{name:'下载这条故事线',exact:true}).click();await expect(download.getByRole('status')).toHaveText('已下载，可离线阅读')
  await download.getByRole('link',{name:'已下载的故事线',exact:true}).click();await page.getByRole('link',{name:'阅读',exact:true}).click()
  const unknownText='服务端已提交但响应丢失的唯一笔记',keys:string[]=[]
  let committedId=''
  await page.route('**'+base+`/books/${book.bookId}/bookmarks`,async route=>{
    const request=route.request()
    if(request.method()!=='POST'||request.postDataJSON()?.note!==unknownText){await route.continue();return}
    keys.push(request.headers()['idempotency-key'])
    if(keys.length===1){const response=await route.fetch();expect(response.status()).toBe(201);committedId=(await response.json()).id;await route.abort('failed')}else await route.continue()
  })
  const list=async()=>{const response=await page.request.get(base+`/books/${book.bookId}/bookmarks?branchId=${book.branchId}&revisionId=${revisionId}`,{headers});expect(response.status(),await response.text()).toBe(200);return response.json()}
  try{
    await newNote(page,unknownText);await page.getByRole('button',{name:'保存笔记',exact:true}).click()
    await expect(page.getByRole('button',{name:'重试保存同一条笔记',exact:true})).toBeVisible();expect(committedId).toBeTruthy();expect((await list()).items.filter((n:any)=>n.note===unknownText)).toHaveLength(1)
    await page.setViewportSize({width:375,height:812});await page.screenshot({path:path.join(artifacts,'note-unknown-response-375.png')})
    await context.setOffline(true);await expect(page.getByText('离线阅读 · 仅显示此账号已缓存内容',{exact:true})).toBeVisible()
    await page.getByRole('button',{name:'重试保存同一条笔记',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
    const unknownQueue=(await queued(page)).filter(n=>n.note===unknownText);expect(unknownQueue).toHaveLength(1);expect(unknownQueue[0].key).toBe(keys[0]);expect(unknownQueue[0].attempted).toBe(true)
    await context.setOffline(false);await page.reload();await expect(page.locator('.reader-body')).toBeVisible()
    await expect.poll(async()=>(await list()).items.filter((n:any)=>n.note===unknownText).length).toBe(1);await expect.poll(async()=>(await queued(page)).length).toBe(0)
    expect(keys.length).toBeGreaterThanOrEqual(2);expect(new Set(keys).size).toBe(1);expect((await list()).items.find((n:any)=>n.note===unknownText).id).toBe(committedId)

    await page.evaluate(()=>{
      const original=IDBObjectStore.prototype.put
      IDBObjectStore.prototype.put=function(value:any,key?:IDBValidKey){
        if((window as any).__nextFailDraftClear&&value===null&&typeof key==='string'&&key.includes(':note-draft:'))throw new DOMException('Owned synthetic draft cleanup fault','QuotaExceededError')
        return original.call(this,value,key!)
      }
      ;(window as any).__nextFailDraftClear=true
    })
    const confirmedText='已保存成功只需要清理本机草稿的唯一笔记'
    await newNote(page,confirmedText);await page.getByRole('button',{name:'保存笔记',exact:true}).click();await expect(page.getByRole('button',{name:'完成草稿清理',exact:true})).toBeVisible()
    expect((await list()).items.filter((n:any)=>n.note===confirmedText)).toHaveLength(1)
    await context.setOffline(true);await expect(page.getByText('离线阅读 · 仅显示此账号已缓存内容',{exact:true})).toBeVisible()
    await page.evaluate(()=>{(window as any).__nextFailDraftClear=false});await page.getByRole('button',{name:'完成草稿清理',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(await queued(page)).toHaveLength(0)

    const freshText='尚未联网并在重开后恢复的唯一笔记'
    await page.evaluate(()=>{(window as any).__nextFailDraftClear=true});await newNote(page,freshText);await page.getByRole('button',{name:'保存笔记',exact:true}).click();await expect(page.getByRole('button',{name:'完成草稿清理',exact:true})).toBeVisible()
    const fresh=(await queued(page)).filter(n=>n.note===freshText);expect(fresh).toHaveLength(1);expect(fresh[0].attempted).toBe(false)
    await page.goto(readUrl+'&anchor='+encodeURIComponent(JSON.stringify(fresh[0].anchor)));await expect(page.locator('.reader-body')).toBeVisible()
    await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
    await expect(page.getByRole('button',{name:'完成草稿清理',exact:true})).toBeVisible();expect(await page.getByLabel('我的阅读笔记').inputValue()).toBe(freshText)
    await page.getByRole('button',{name:'完成草稿清理',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)
    expect((await queued(page)).filter(n=>n.note===freshText).map(n=>[n.id,n.key,n.attempted])).toEqual([[fresh[0].id,fresh[0].key,false]])
    await page.screenshot({path:path.join(artifacts,'note-recovered-offline-375.png')})
    await context.setOffline(false);await page.reload();await expect.poll(async()=>(await queued(page)).length).toBe(0)
    const all=(await list()).items;expect(all).toHaveLength(3);for(const wanted of [unknownText,confirmedText,freshText])expect(all.filter((n:any)=>n.note===wanted)).toHaveLength(1)
    expect(await(await page.request.get(base+'/usage',{headers})).json()).toEqual(usageBefore)
    writeFileSync(path.join(artifacts,'note-recovery-observations.json'),JSON.stringify({execution:'actual-chromium-idb-serviceworker-postgresql',realProviderCalls:0,unknownPostCommittedThenResponseAborted:true,unknownRetryUsesSameRequestKey:true,sameKeyPostRequests:keys.length,unknownRemoteIdUnchanged:true,confirmedCleanupOfflineDoesNotQueueAgain:true,freshOfflineQueueDurableBeforeCleanup:true,reopenUsesSameLocalRowAndKey:true,unattemptedOfflineNoteRemainsUnattempted:true,finalRemoteNotes:3,usageUnchanged:true,limitations:['Synthetic deterministic storage fault','No native device or physical disk-full certification']},null,2))
  }finally{await context.setOffline(false)}
})
