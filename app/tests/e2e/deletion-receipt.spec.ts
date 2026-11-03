import {expectLibraryLanding} from './helpers/landing'
import {test,expect} from '@playwright/test'
import {randomUUID} from 'node:crypto'

for(const leaveWhilePending of [false,true])test(`same-origin deletion receipt ${leaveWhilePending?'survives a route change':'survives reload'}`,async({page,baseURL,request})=>{
  expect(baseURL).toBe('http://127.0.0.1:8811')
  const health=await request.get('/health/ready');expect((await health.json()).aiMode).toBe('mock')
  const username='dr_'+Date.now().toString(36)+'_'+randomUUID().slice(0,6),password='deletion-receipt-fixture-123' // gitleaks:allow -- synthetic test fixture
  await page.goto('/register');await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const logging=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/auth/login'))
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  const account=await(await logging).json();expect(account.user.id).toBeTruthy()
  await expectLibraryLanding(page)
  await expect(page.locator('.mock-banner')).toContainText('确定性 mock')
  const headers={Authorization:'Bearer '+account.accessToken}
  let accepted=false,responseReceipt='',posts=0
  page.on('request',event=>{if(event.method()==='POST'&&event.url().endsWith('/auth/delete-account'))posts++})
  try{
    const sessions=page.waitForResponse(response=>response.request().method()==='GET'&&response.url().endsWith('/auth/sessions'))
    await page.goto('/settings');expect((await sessions).ok()).toBeTruthy();await expect(page.getByRole('button',{name:'注销我的账号',exact:true})).toBeVisible()
    const own=await request.get('/api/reader/v1/auth/me',{headers});expect(await own.json()).toMatchObject({id:account.user.id,username})
    await page.evaluate(delay=>{
      const state=window as unknown as {deletionReceipt?:string;deletionPosts:number;releaseDeletion?:()=>void}
      state.deletionPosts=0
      const realFetch=window.fetch.bind(window)
      window.fetch=async(input,options)=>{
        const response=await realFetch(input,options)
        if(options?.method==='POST'&&new URL(String(input),location.href).pathname==='/api/reader/v1/auth/delete-account'){
          state.deletionPosts++
          if(response.ok){state.deletionReceipt=(await response.clone().json()).receiptId;if(delay)await new Promise<void>(resolve=>{state.releaseDeletion=resolve})}
        }
        return response
      }
    },leaveWhilePending)
    await page.getByRole('button',{name:'注销我的账号',exact:true}).click()
    const dialog=page.getByRole('dialog');await dialog.getByLabel('当前密码').fill(password);await dialog.getByLabel('输入“删除我的账号”').fill('删除我的账号')
    await dialog.getByRole('button',{name:'确认删除我的账号',exact:true}).click()
    await expect.poll(()=>page.evaluate(()=>(window as unknown as {deletionReceipt?:string}).deletionReceipt)).toBeTruthy()
    responseReceipt=await page.evaluate(()=>(window as unknown as {deletionReceipt:string}).deletionReceipt);accepted=true
    if(leaveWhilePending){
      // Browser hash navigation changes Vue Router fullPath without triggering
      // an unrelated authenticated request before this successful response.
      await page.evaluate(()=>{location.hash='left-after-confirmation'})
      await expect(page).toHaveURL(/#left-after-confirmation$/)
      expect(await page.evaluate(()=>localStorage.getItem('reader-account'))).toBe(account.user.id)
      await page.evaluate(()=>(window as unknown as {releaseDeletion:()=>void}).releaseDeletion())
    }
    await expect(page.getByRole('heading',{name:'回到你的故事'})).toBeVisible()
    expect(await page.evaluate(()=>localStorage.getItem('reader-account'))).toBeNull()
    const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('reader-deletion-receipt')||'null'))
    expect(saved).toMatchObject({id:responseReceipt,base:''});expect(saved.recordedAt).toBeGreaterThan(0)
    expect(await page.evaluate(()=>(window as unknown as {deletionPosts:number}).deletionPosts)).toBe(1)
    await page.reload();await expect(page.getByRole('heading',{name:'回到你的故事'})).toBeVisible()
    expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('reader-deletion-receipt')||'null').id)).toBe(responseReceipt)
    await expect(page.locator('.hint[role="status"]')).toContainText(/此回执/)
    const status=await request.get('/api/reader/v1/auth/deletion-receipts/'+responseReceipt);expect(status.ok()).toBeTruthy()
    expect(posts).toBe(1)
  }finally{
    await page.evaluate(()=>(window as unknown as {releaseDeletion?:()=>void}).releaseDeletion?.()).catch(()=>{})
    if(!accepted){
      const own=await request.get('/api/reader/v1/auth/me',{headers})
      if(own.ok()){
        expect(await own.json()).toMatchObject({id:account.user.id,username})
        const deletion=await request.post('/api/reader/v1/auth/delete-account',{headers,data:{currentPassword:password,confirmation:'删除我的账号'}})
        expect(deletion.ok()).toBeTruthy()
      }
    }
  }
})
