import {test, expect} from '@playwright/test'
import {mkdirSync, writeFileSync} from 'node:fs'
import path from 'node:path'
import {randomUUID} from 'node:crypto'

test('F21 switching from a failed task to a safe candidate clears the old task error', async({page, context, baseURL})=>{
  expect(baseURL).toBe('http://127.0.0.1:8811')
  expect((await (await context.request.get(baseURL+'/health/ready')).json()).aiMode).toBe('mock')
  const username='taskerror_'+Date.now().toString(36), password='isolated-task-error-fixture-123'
  await page.goto('/register')
  await page.getByLabel('用户名').fill(username);await page.getByLabel('密码').fill(password)
  const login=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.status()===200)
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click()
  const credentials=await(await login).json()
  await expect(page.getByRole('heading',{name:'你的书架'})).toBeVisible()
  const headers={Authorization:'Bearer '+credentials.accessToken}
  async function post(route:string, body:unknown, code:number){
    const response=await context.request.post(baseURL+'/api/reader/v1'+route,{headers:{...headers,'Idempotency-Key':randomUUID()},data:body})
    expect(response.status()).toBe(code);return response.json()
  }
  async function get(route:string){
    const response=await context.request.get(baseURL+'/api/reader/v1'+route,{headers})
    expect(response.status()).toBe(200);return response.json()
  }
  const book=await post('/books/demo',{},201)
  const directory=await get('/branches/'+book.branchId+'/chapters?revisionId='+book.revisionId)
  const chapter=await get('/branches/'+book.branchId+'/chapters/'+directory.items[0].id+'?revisionId='+book.revisionId)
  const anchor=chapter.blocks.at(-1).endAnchor
  const progress=await context.request.put(baseURL+'/api/reader/v1/books/'+book.bookId+'/progress',{headers,data:{
    branchId:book.branchId,revisionId:book.revisionId,anchor,expectedProgressVersion:0,deviceId:credentials.sessionId,clientSeq:1}})
  expect(progress.status()).toBe(200)
  async function createTask(window:number){
    const profile=await post('/model-profiles',{name:'fixture window '+window,baseUrl:'mock://deterministic',
      modelId:'fixture-v1',key:'TASK-ERROR-FIXTURE-NOT-REAL',contextWindow:window,
      callBudget:100,inputBudget:1000000,outputBudget:100000},201)
    const operation={operation:'counterfactual',profileId:profile.id,anchor,expectedHeadRevisionId:book.revisionId,
      instruction:'让林渡留下救阿岑',target:{mode:'new_child_branch',name:'任务错误回归'}}
    const plan=await post('/operation-plans',operation,201)
    const consent=await post('/consents',{planId:plan.planId,planHash:plan.planHash,profileId:profile.id,
      maxCalls:plan.maxCalls,inputTokens:plan.inputTokens,outputTokens:plan.outputTokens},201)
    const task=await post('/generation-tasks',{...operation,consentId:consent.consentId},202)
    let result:any
    await expect.poll(async()=>{result=await get('/tasks/'+task.taskId);return result.state},
      {timeout:30000}).toMatch(/^(succeeded|failed|cancelled)$/)
    return result
  }
  // The first task really fails its configured window; no task metadata or
  // network response is rewritten. The second uses a separate valid profile.
  const failed=await createTask(4096), safe=await createTask(32768)
  expect(failed.state).toBe('failed');expect(failed.errorCode).toBe('CONTEXT_BUDGET_EXCEEDED')
  expect(safe.resultOutcome).toBe('complete')
  await page.goto('/books/'+book.bookId+'/read?branch='+book.branchId+'&revision='+book.revisionId)
  await expect(page.locator('.reader-body')).toBeVisible()
  await page.getByRole('button',{name:'打开目录',exact:true}).click()
  await page.getByRole('dialog').locator('.chapter-link').first().click()
  await page.locator('.paragraph').last().click();await page.getByRole('button',{name:'改剧情',exact:true}).click()
  const selection=page.getByLabel('写作记录')
  await selection.selectOption('task:'+failed.id)
  await expect(page.getByRole('alert')).toContainText('这段故事较长，当前模型暂时无法完成')
  await selection.selectOption('task:'+safe.id)
  await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeEnabled()
  const lingering=await page.getByRole('alert').count()
  const out=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests');mkdirSync(out,{recursive:true})
  writeFileSync(path.join(out,'generation-task-error.json'),JSON.stringify({
    failedTaskId:failed.id,selectedTaskId:safe.id,failedErrorCode:failed.errorCode,
    selectedTaskOutcome:safe.resultOutcome,safeCandidateVisible:true,oldErrorCount:lingering,
    paidCalls:0,passed:lingering===0,provider:'isolated deterministic fixture'},null,2))
  expect(lingering).toBe(0)
})
