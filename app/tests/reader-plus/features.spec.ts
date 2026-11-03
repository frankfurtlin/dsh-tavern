import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
import {mkdirSync} from 'node:fs'
import {randomUUID} from 'node:crypto'
let username='',book:any,profile:any,other:any,access=''
const password='reader-plus-browser-fixture-123',out=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/reader-plus')
async function login(page:Page){
  const creating=!username;username ||= 'plus_'+Date.now().toString(36)
  await page.goto(creating?'/register':'/login')
  await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const response=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:creating?'创建账号并进入':'登录',exact:true}).click()
  access=(await(await response).json()).accessToken
  await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  return {Authorization:'Bearer '+access}
}
for(const width of [360,390,430,1440])test(`Reader Plus persists defaults, collections and private notes ${width}px`,async({page,context,baseURL})=>{
  expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await(await context.request.get(baseURL+'/health/ready')).json()).aiMode).toBe('mock')
  await page.setViewportSize({width,height:900})
  const headers=await login(page),base=baseURL+'/api/reader/v1'
  async function post(route:string,body:unknown){const response=await context.request.post(base+route,{headers:{...headers,'Idempotency-Key':randomUUID()},data:body});expect(response.ok()).toBeTruthy();return response.json()}
  if(!book){book=await post('/books/demo',{});profile=await post('/model-profiles',{name:'Reader Plus default fixture',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-NOT-REAL',contextWindow:32768});other=await post('/model-profiles',{name:'Other fixture',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-NOT-REAL',contextWindow:32768})}
  await page.goto('/settings/models')
  const otherCard=page.locator('article').filter({has:page.getByRole('heading',{name:/Other fixture/})})
  await expect(otherCard).toBeVisible()
  if(await otherCard.getByRole('button',{name:'设为默认',exact:true}).count())await otherCard.getByRole('button',{name:'设为默认',exact:true}).click()
  await expect(otherCard.locator('.default-model')).toHaveText('默认模型')
  await page.reload();await expect(otherCard.locator('.default-model')).toHaveText('默认模型')
  await page.goto('/library')
  const card=page.locator('.book-item').first()
  await card.getByRole('button',{name:/管理/}).click()
  await page.getByLabel('书籍标签').fill('悬疑，重读')
  await page.getByRole('button',{name:'保存书名',exact:true}).click()
  await expect(card.locator('.book-tags')).toHaveText('悬疑重读')
  if(await card.getByRole('button',{name:'收藏雾港来信',exact:true}).count())await card.getByRole('button',{name:'收藏雾港来信',exact:true}).click()
  await expect(card.getByRole('button',{name:'取消收藏雾港来信',exact:true})).toHaveAttribute('aria-pressed','true')
  await page.getByLabel('搜索书架').fill('不存在的书名')
  await expect(page.getByRole('heading',{name:'没有找到这本故事'})).toBeVisible()
  await page.getByLabel('搜索书架').fill('悬疑')
  await expect(card).toBeVisible()
  await page.getByLabel('书架排序').selectOption('title')
  await page.reload();await expect(card.getByRole('button',{name:'取消收藏雾港来信',exact:true})).toBeVisible()
  mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,`library-${width}.png`),fullPage:true})
  await card.getByRole('button',{name:/继续阅读|打开故事/}).click()
  await expect(page.locator('.reader-body')).toBeVisible()
  await page.getByRole('button',{name:'打开目录',exact:true}).click();await page.getByRole('dialog').locator('.chapter-link').first().click()
  const block=page.locator('.paragraph').nth(1),blockId=(await block.getAttribute('id'))!.replace('block-','')
  await block.click();await page.getByRole('button',{name:'续写',exact:true}).click()
  await expect(page.getByLabel('模型通道')).toHaveValue(other.id)
  await page.getByRole('button',{name:'关闭',exact:true}).last().click()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
  const text='第 '+width+' 像素的阅读想法：林渡的选择让我想重读这一段。'
  await page.getByLabel('我的阅读笔记').fill(text)
  await page.getByRole('button',{name:'关闭',exact:true}).last().click()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
  await expect(page.getByLabel('我的阅读笔记')).toHaveValue(text)
  await page.getByRole('button',{name:'保存笔记',exact:true}).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.goto('/notebook')
  const noteCard=page.locator('.note-card').filter({hasText:text})
  await expect(noteCard).toBeVisible();await noteCard.getByRole('button',{name:'编辑笔记',exact:true}).click()
  await page.getByLabel('我的阅读笔记').fill(text+' 已编辑。');await page.getByRole('button',{name:'保存笔记',exact:true}).click()
  await expect(noteCard).toContainText('已编辑。')
  await page.reload();await expect(noteCard).toContainText('已编辑。')
  await page.screenshot({path:path.join(out,`notebook-${width}.png`),fullPage:true})
  await noteCard.getByRole('button',{name:'回到这一段',exact:true}).click()
  await expect(page).toHaveURL(new RegExp('block='+blockId))
  await expect(page.locator('#block-'+blockId)).toBeInViewport()
  await page.goto('/insights');await page.getByLabel('每天读多少分钟？').fill('30')
  await page.getByRole('button',{name:'保存目标',exact:true}).click()
  await expect(page.getByLabel('每天读多少分钟？')).toHaveValue('30')
  await page.reload();await expect(page.getByLabel('每天读多少分钟？')).toHaveValue('30')
  await page.screenshot({path:path.join(out,`insights-${width}.png`),fullPage:true})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
})

test('visible reading records actual duration and an unfocused tab does not add time',async({page,context,baseURL})=>{
  const headers=await login(page),base=baseURL+'/api/reader/v1'
  if(!book){const response=await context.request.post(base+'/books/demo',{headers:{...headers,'Idempotency-Key':randomUUID()},data:{}});expect(response.status()).toBe(201);book=await response.json()}
  const before=await(await context.request.get(base+'/reading-stats',{headers})).json()
  await page.goto('/books/'+book.bookId+'/read?branch='+book.branchId)
  await expect(page.locator('.reader-body')).toBeVisible();await page.bringToFront();await page.locator('.paragraph').first().click()
  await expect.poll(async()=>{const stats=await(await context.request.get(base+'/reading-stats',{headers})).json();return stats.totalSeconds-before.totalSeconds},{timeout:40000,intervals:[1000]}).toBeGreaterThanOrEqual(29)
  const requests:any[]=[];page.on('request',request=>{if(request.url().endsWith('/reading-activity'))requests.push(request.postDataJSON())})
  const another=await context.newPage();await another.goto('/library');await another.bringToFront()
  await page.waitForTimeout(3500)
  const settled=requests.length;await page.waitForTimeout(3500);expect(requests.length).toBe(settled)
  await another.close()
})

test('an older local note draft cannot silently overwrite a newer saved revision',async({page,context,baseURL})=>{
  const headers=await login(page),base=baseURL+'/api/reader/v1'
  const notes=await(await context.request.get(base+'/notebook',{headers})).json()
  const note=notes.items[0]
  expect(note).toBeTruthy()
  await page.goto('/notebook')
  const card=page.locator('.note-card').filter({hasText:note.note})
  await card.getByRole('button',{name:'编辑笔记',exact:true}).click()
  const draft='还没保存的旧版本想法'
  await page.getByLabel('我的阅读笔记').fill(draft)
  await page.getByRole('button',{name:'关闭',exact:true}).last().click()
  const newer='另一设备先保存的新想法'
  const remote=await context.request.patch(base+'/books/'+note.anchor.bookId+'/bookmarks/'+note.id,{headers,data:{expectedVersion:note.version,note:newer}})
  expect(remote.status()).toBe(200)
  await page.reload()
  await page.locator('.note-card').filter({hasText:newer}).getByRole('button',{name:'编辑笔记',exact:true}).click()
  await expect(page.getByLabel('我的阅读笔记')).toHaveValue(draft)
  await expect(page.getByRole('dialog')).toContainText('这份草稿基于旧版本')
  await expect(page.getByRole('button',{name:'保存笔记',exact:true})).toBeDisabled()
  const saved=await(await context.request.get(base+'/books/'+note.anchor.bookId+'/bookmarks/'+note.id,{headers})).json()
  expect(saved.note).toBe(newer)
  await page.getByRole('button',{name:'使用账号里的笔记',exact:true}).click()
  await expect(page.getByLabel('我的阅读笔记')).toHaveValue(newer)
  await page.getByRole('button',{name:'保存笔记',exact:true}).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('successful note POST followed by local cleanup failure is not submitted twice',async({page})=>{
  await login(page)
  await page.goto('/books/'+book.bookId+'/read?branch='+book.branchId)
  await expect(page.locator('.reader-body')).toBeVisible()
  await page.locator('.paragraph').first().click()
  await page.getByRole('button',{name:'阅读工具',exact:true}).click();await page.getByRole('button',{name:'写阅读笔记',exact:true}).click()
  const text='保存已成功时，本机故障也不能生成第二条笔记。'
  await page.getByLabel('我的阅读笔记').fill(text)
  await page.evaluate(()=>{
    const original=IDBObjectStore.prototype.put
    let fail=true
    IDBObjectStore.prototype.put=function(value:any,key?:IDBValidKey){
      if(fail&&value===null&&String(key).includes(':note-draft:')){fail=false;throw new Error('Intentional local cleanup failure')}
      return original.call(this,value,key)
    }
  })
  const posts:string[]=[];page.on('request',request=>{if(request.method()==='POST'&&/\/bookmarks$/.test(request.url()))posts.push(request.postData()||'')})
  await page.getByRole('button',{name:'保存笔记',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('笔记已保存到账号')
  await expect(page.getByLabel('我的阅读笔记')).toBeDisabled()
  await page.getByRole('button',{name:'完成草稿清理',exact:true}).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(posts).toHaveLength(1)
  await page.goto('/notebook');await expect(page.locator('.note-card').filter({hasText:text})).toHaveCount(1)
})

test('native hide signal pauses activity even when DOM remains visible and focused',async({page})=>{
  await login(page)
  await page.goto('/books/'+book.bookId+'/read?branch='+book.branchId)
  await expect(page.locator('.reader-body')).toBeVisible();await page.bringToFront()
  const requests:any[]=[];page.on('request',request=>{if(request.url().endsWith('/reading-activity'))requests.push(request.postDataJSON())})
  await page.evaluate(()=>window.dispatchEvent(new Event('reader-save')))
  await page.waitForTimeout(1000)
  const before=requests.length
  expect(await page.evaluate(()=>document.visibilityState==='visible'&&document.hasFocus())).toBeTruthy()
  await page.waitForTimeout(31000)
  expect(requests.length).toBe(before)
  await page.evaluate(()=>window.dispatchEvent(new Event('reader-resume')))
  await expect.poll(()=>requests.length,{timeout:35000,intervals:[1000]}).toBeGreaterThan(before)
})

test('an active profile with its revoked key still missing is excluded from AI selectors',async({page,context,baseURL})=>{
  const headers=await login(page),base=baseURL+'/api/reader/v1'
  const created=await context.request.post(base+'/model-profiles',{headers,data:{name:'Reactivated without key',baseUrl:'mock://deterministic',modelId:'fixture-v1',key:'FIXTURE-NOT-REAL',contextWindow:32768}})
  expect(created.status()).toBe(201);const profile=await created.json()
  const revoked=await context.request.patch(base+'/model-profiles/'+profile.id,{headers,data:{expectedVersion:profile.version,status:'revoked'}})
  expect(revoked.status()).toBe(200)
  const active=await context.request.patch(base+'/model-profiles/'+profile.id,{headers,data:{expectedVersion:(await revoked.json()).version,status:'active'}})
  expect(active.status()).toBe(200);expect((await active.json()).hasKey).toBe(false)
  await page.goto('/settings/models')
  const card=page.locator('article').filter({has:page.getByRole('heading',{name:/Reactivated without key/})})
  await expect(card).toContainText('待补充 Key')
  await expect(card.getByRole('button',{name:'设为默认',exact:true})).toBeDisabled()
  await page.goto('/books/'+book.bookId+'/read?branch='+book.branchId)
  await expect(page.locator('.reader-body')).toBeVisible();await page.locator('.paragraph').first().click()
  await page.getByRole('button',{name:'续写',exact:true}).click()
  await expect(page.getByLabel('模型通道')).toHaveValue(other.id)
  await expect(page.getByLabel('模型通道').getByRole('option',{name:/Reactivated without key/})).toHaveCount(0)
})
