import {expect,type Page} from '@playwright/test'

export async function expectLibraryLanding(page:Page){
  await expect(page).toHaveURL(/\/(worlds|library)(?:[?#].*)?$/)
  if(new URL(page.url()).pathname==='/worlds')await page.getByRole('link',{name:'书架',exact:true}).click()
  await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
}

export async function chooseCustomChannel(page:Page){
  await page.getByRole('dialog',{name:'添加模型通道'}).getByRole('combobox').first().selectOption('custom')
  await expect(page.getByLabel('通道名称')).toBeVisible()
}

const acceptance=new WeakMap<Page,Promise<any>>()
const observed=new WeakSet<Page>()
function observeAcceptance(page:Page){
  if(observed.has(page))return
  observed.add(page)
  page.on('response',response=>{if(response.ok()&&response.request().method()==='POST'&&/\/generations\/[^/]+\/accept$/.test(response.url()))acceptance.set(page,response.json())})
}
export async function expectSavedInline(page:Page,count=1){
  observeAcceptance(page)
  await expect(page.locator('.generation-chapter')).toHaveCount(count,{timeout:45000})
  await expect(page.getByRole('button',{name:'保存全部完整章节',exact:true})).toBeEnabled({timeout:45000})
}

export async function readSavedInline(page:Page){
  observeAcceptance(page)
  const read=page.getByRole('button',{name:'接着读已加入的章节',exact:true})
  if(!await read.isVisible())await page.getByRole('button',{name:'保存全部完整章节',exact:true}).click()
  await expect(read).toBeVisible()
  const receipt=await acceptance.get(page)
  if(!receipt)throw new Error('Acceptance receipt was not observed')
  await read.click()
  await expect.poll(()=>{const url=new URL(page.url());return {branch:url.searchParams.get('branch'),revision:url.searchParams.get('revision'),chapter:url.searchParams.get('chapter')}}).toEqual({branch:receipt.branchId,revision:receipt.revisionId,chapter:receipt.readingAnchor.chapterId})
  await expect(page.locator('.reader-body h1')).toBeVisible()
}
