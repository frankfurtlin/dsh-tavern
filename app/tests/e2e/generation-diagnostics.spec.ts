import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
import {execFileSync} from 'node:child_process'

async function setupWriting(page:Page){
  const name='review_'+Date.now().toString(36)
  await page.goto('/register');await page.getByLabel('用户名').fill(name);await page.getByLabel('密码').fill('isolated-review-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('正文保留测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'正文保留测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口');await page.waitForTimeout(3500)
}

for(const width of [390,1440])test(`Reader choice keeps warning prose readable and accepts it once ${width}px`,async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width,height:900});await setupWriting(page)
  const originalUrl=page.url(),originalText=await page.locator('.paragraph').first().innerText()
  let posts=0;page.on('request',r=>{if(r.method()==='POST'&&/\/(story-runs|generation-tasks)$/.test(r.url()))posts++})
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  const received=page.waitForResponse(r=>r.request().method()==='GET'&&/\/candidates\/[a-f0-9]{32}$/.test(r.url()))
  await page.getByRole('button',{name:/开始写作/}).click();await expect(page.getByRole('button',{name:'接着读',exact:true})).toBeVisible({timeout:30000})
  const candidate=await(await received).json();expect(candidate.taskId).toMatch(/^[a-f0-9]{32}$/)
  // Change only review metadata in the isolated fixture, exercising real HTTP
  // serialization, browser caching and acceptance without another model request.
  const program=[
    'from app.config import settings',
    'from app.db import SessionLocal',
    'from app.models import Task,Candidate,UsageRecord',
    'from app.generation_review import REVIEW_POLICY',
    'from sqlalchemy import select,func',
    'assert settings.ai_mode=="mock"',
    'with SessionLocal() as db:',
    ' before=db.scalar(select(func.count()).select_from(UsageRecord))',
    ` task=db.get(Task,${JSON.stringify(candidate.taskId)})`,
    ' candidate=db.scalar(select(Candidate).where(Candidate.task_id==task.id))',
    ' assert task.state=="succeeded" and candidate.outcome=="complete"',
    ' warnings=[{"code":"SPOILER","chapterIndex":1},{"code":"HISTORY_CONFLICT","chapterIndex":1}]',
    ' task.checkpoint={**task.checkpoint,"generationReviewPolicy":REVIEW_POLICY,"reviewWarnings":warnings}',
    ' candidate.validation_status="blocked"',
    ' parent=db.get(Task,task.payload["flowId"])',
    ' parent.checkpoint={**parent.checkpoint,"reviewWarnings":warnings}',
    ' db.commit()',
    ' assert db.scalar(select(func.count()).select_from(UsageRecord))==before',
  ].join('\n')
  execFileSync('docker',['compose','-f','compose.e2e.yaml','exec','-T','worker','python','-c',program],{cwd:path.resolve('..'),stdio:'pipe'})
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  const writing=page.getByRole('region',{name:'故事写作'})
  await expect(writing.getByRole('alert')).toHaveCount(0)
  await expect(writing.getByRole('complementary',{name:'内容提示',exact:true})).toContainText('可能涉及原作后续情节')
  await expect(writing.getByRole('complementary',{name:'内容提示',exact:true})).toContainText('起点之前')
  await expect(writing.locator('.candidate-text')).toContainText(candidate.text)
  await expect(writing.locator('.candidate-text')).toBeInViewport()
  await expect(writing.getByRole('button',{name:'接着读',exact:true})).toBeEnabled();expect(posts).toBe(1)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:path.join(process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests'),`reader-choice-preview-${width}.png`),fullPage:true})
  await writing.getByRole('button',{name:'接着读',exact:true}).click();await expect(page.locator('.reader-body .paragraph[data-content-source="ai"]').first()).toBeVisible()
  expect(posts).toBe(1);expect(page.url()).not.toBe(originalUrl)
  await page.goto(originalUrl);await expect(page.locator('.paragraph').first()).toHaveText(originalText)
})

test('Writing diagnostics show one accurate failure and retain history without resubmitting',async({page})=>{
  await page.setViewportSize({width:390,height:900})
  const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
  const name='gate_'+Date.now().toString(36)
  await page.goto('/register');await page.getByLabel('用户名').fill(name);await page.getByLabel('密码').fill('isolated-gate-fixture-123')
  await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)
  await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page)
  await page.getByLabel('通道名称').fill('诊断测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic')
  await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-NOT-REAL');await page.getByRole('button',{name:'保存配置'}).click()
  await expect(page.getByRole('heading',{name:'诊断测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
  let posts=0
  page.on('request',r=>{if(r.method()==='POST'&&/\/(story-runs|generation-tasks)$/.test(r.url()))posts++})
  const runs=[
    {id:'rejected-spoiler',state:'failed',phase:'rejected',currentTaskId:'spoiler-child',candidateId:'spoiler-candidate',requestedChapters:1,completedChapters:0,errorCode:'GENERATION_SPOILER',scopeAnchor:null,createdAt:'2026-10-03T14:58:29Z'},
    {id:'rejected-history',state:'failed',phase:'rejected',currentTaskId:'history-child',candidateId:'history-candidate',requestedChapters:1,completedChapters:0,errorCode:'GENERATION_HISTORY_CONFLICT',scopeAnchor:null,createdAt:'2026-10-03T14:57:53Z'},
  ]
  await page.route('**/api/reader/v1/story-runs?*',route=>route.fulfill({json:{items:runs,nextCursor:null}}))
  await page.route('**/api/reader/v1/story-runs/rejected-*',route=>route.fulfill({json:runs.find(r=>route.request().url().endsWith(r.id))}))
  await page.route('**/api/reader/v1/candidates/*-candidate',route=>route.fulfill({json:{id:route.request().url().split('/').pop(),text:null,chapters:null,outcome:'blocked',validationStatus:'blocked',status:'pending',persistenceState:'saved',partial:false}}))
  await page.locator('.paragraph').nth(1).click();await page.getByRole('button',{name:'从这里写',exact:true}).click()
  const writing=page.getByRole('region',{name:'故事写作'})
  await expect(writing.getByRole('alert')).toHaveCount(1)
  await expect(writing.getByRole('alert')).toContainText('原作后续情节')
  await expect(writing.locator('.writing-progress,.writing-result')).toHaveCount(0)
  await expect(writing.getByRole('button',{name:/开始写作/})).toBeEnabled()
  await writing.getByLabel('写作记录').selectOption('run:rejected-history')
  await expect(writing.getByRole('alert')).toContainText('起点之前')
  await expect(writing.getByRole('alert')).toHaveCount(1)
  await expect(writing.locator('.writing-progress,.writing-result')).toHaveCount(0)
  expect(posts).toBe(0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:path.join(artifacts,'writing-failure-diagnostics-390.png'),fullPage:true})
})
