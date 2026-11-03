import {expectLibraryLanding} from './helpers/landing'
import {test,expect} from '@playwright/test'
import {writeFileSync,mkdirSync} from 'node:fs'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
let username=''
for(const characters of [100_000,1_000_000])test(`F performance real browser ${characters} Unicode characters / 100 chapters`,async({page})=>{
  test.setTimeout(120000)
  await page.goto(username?'/login':'/register')
  const creating=!username;username||='perf_'+Date.now().toString(36)
  await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill('browser-performance-123')
  await page.getByRole('button',{name:creating?'创建账号并进入':'登录',exact:true}).click();await expectLibraryLanding(page)
  let remaining=characters,content=''
  for(let i=0;i<100;i++){const heading=`第${i+1}章\n`,size=Math.floor(remaining/(100-i));let budget=size-heading.length;content+=heading;while(budget>0){const length=Math.min(199,budget-1);content+='河岸的潮声伴着木板轻响，人在雾中等待船只。'.repeat(12).slice(0,length)+'\n';budget-=length+1}remaining-=size}
  expect(Array.from(content).length).toBe(characters)
  const started=Date.now(),chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click();await(await chooser).setFiles({name:`browser-${characters}.txt`,mimeType:'text/plain',buffer:Buffer.from(content)})
  await expect(page.getByRole('button',{name:'确认并开始阅读'})).toBeVisible({timeout:60000})
  const imported=Date.now()-started,opened=Date.now();await page.getByRole('button',{name:'确认并开始阅读'}).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible();const firstRead=Date.now()-opened
  const directory:number[]=[],navigation:number[]=[]
  for(let i=0;i<31;i++){
    directory.push(await page.evaluate(async()=>{const start=performance.now();(document.querySelector('[aria-label="打开目录"]') as HTMLButtonElement).click();await new Promise<void>(resolve=>{function ready(){if(document.querySelector('[role="dialog"]'))requestAnimationFrame(()=>resolve());else requestAnimationFrame(ready)}ready()});return performance.now()-start}))
    await expect(page.getByRole('dialog')).toBeVisible()
    const chapter=i+2;navigation.push(await page.evaluate(async chapter=>{const start=performance.now();(document.querySelectorAll('[role="dialog"] .chapter-link')[chapter-1] as HTMLButtonElement).click();await new Promise<void>((resolve,reject)=>{function ready(){if(performance.now()-start>3000){reject(new Error('Chapter interaction deadline exceeded'));return}if(document.querySelector('.reader-body h1')?.textContent===`第${chapter}章`&&!document.querySelector('[role="dialog"]'))requestAnimationFrame(()=>resolve());else requestAnimationFrame(ready)}ready()});return performance.now()-start},chapter))
  }
  const p95=(values:number[])=>values.slice().sort((a,b)=>a-b)[Math.ceil(values.length*.95)-1]
  const coldDirectory:number[]=[],coldNavigation:number[]=[],coldRead:number[]=[],url=page.url()
  for(let i=0;i<30;i++){
    // New page application state and no private IndexedDB data; keep the login.
    // PostgreSQL/OS are deliberately not restarted and that scope is reported.
    await page.evaluate(async()=>{const db=await new Promise<IDBDatabase>((resolve,reject)=>{const open=indexedDB.open('story-reader-private',1);open.onsuccess=()=>resolve(open.result);open.onerror=reject});const tx=db.transaction('cache','readwrite');tx.objectStore('cache').clear();await new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onerror=reject});db.close()})
    const opened=Date.now();await page.goto(url);await expect(page.locator('.reader-body .paragraph').first()).toBeVisible();coldRead.push(Date.now()-opened)
    coldDirectory.push(await page.evaluate(async()=>{const start=performance.now();(document.querySelector('[aria-label="打开目录"]') as HTMLButtonElement).click();await new Promise<void>(resolve=>{function ready(){if(document.querySelector('[role="dialog"]'))requestAnimationFrame(()=>resolve());else requestAnimationFrame(ready)}ready()});return performance.now()-start}))
    coldNavigation.push(await page.evaluate(async()=>{const start=performance.now();(document.querySelectorAll('[role="dialog"] .chapter-link')[98] as HTMLButtonElement).click();await new Promise<void>((resolve,reject)=>{function ready(){if(performance.now()-start>3000){reject(new Error('Cold chapter deadline exceeded'));return}if(document.querySelector('.reader-body h1')?.textContent==='第99章'&&!document.querySelector('[role="dialog"]'))requestAnimationFrame(()=>resolve());else requestAnimationFrame(ready)}ready()});return performance.now()-start}))
  }
  const report={characters,chapters:100,importToPreviewMs:imported,confirmationToFirstParagraphMs:firstRead,directoryMs:{first:directory[0],coldP95:p95(coldDirectory),coldSamples:30,coldValues:coldDirectory,hotP95:p95(directory.slice(1)),hotSamples:30,hotValues:directory.slice(1)},navigationMs:{first:navigation[0],coldP95:p95(coldNavigation),coldSamples:30,coldValues:coldNavigation,hotP95:p95(navigation.slice(1)),hotSamples:30,hotValues:navigation.slice(1)},coldFirstParagraphMs:{p95:p95(coldRead),samples:30,values:coldRead},environment:{browser:await page.evaluate(()=>navigator.userAgent),viewport:page.viewportSize(),backend:process.env.E2E_RUNTIME==='native'?'Native PostgreSQL 16/API/worker, loopback HTTP, no enforced container CPU or memory limits':'actual Docker PostgreSQL/API; db 1vCPU/3GB, api 1vCPU/2GB, worker 2vCPU/3GB (total 4vCPU/8GB), loopback HTTP',modelCalls:0},measurement:'Directory/chapter latency uses browser performance clock, DOM click through actual Vue handlers, and next paint. Thirty cold client samples clear private IndexedDB and reload the application; thirty warm application samples retain caches. OS/PostgreSQL remain warm, so these are not thirty cold OS/DB startups.'}
  mkdirSync(artifacts,{recursive:true});writeFileSync(path.join(artifacts,`browser-performance-${characters}.json`),JSON.stringify(report,null,2))
  expect(imported).toBeLessThan(60000);expect(firstRead).toBeLessThan(3000);expect(p95(coldRead)).toBeLessThan(3000);expect(p95(directory.slice(1))).toBeLessThan(200);expect(p95(navigation.slice(1))).toBeLessThan(200)
})
