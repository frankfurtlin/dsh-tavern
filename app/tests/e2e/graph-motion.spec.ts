import {expectSavedInline,readSavedInline} from './helpers/landing'
import {chooseCustomChannel} from './helpers/landing'
import {expectLibraryLanding} from './helpers/landing'
import {test,expect,Page} from '@playwright/test'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
async function register(page:Page){await page.goto('/register');await page.getByLabel('用户名').fill('graph_'+Date.now().toString(36));await page.getByLabel('密码').fill('graph-motion-fixture-123');await page.getByRole('button',{name:'创建账号并进入',exact:true}).click();await expectLibraryLanding(page)}
async function setup(page:Page){
  await register(page);await page.goto('/settings/models');await page.getByRole('button',{name:'添加通道'}).click();await chooseCustomChannel(page);await page.getByLabel('通道名称').fill('故事图测试');await page.getByLabel('兼容 Chat Completions 的基础地址').fill('mock://deterministic');await page.getByLabel('模型标识').fill('fixture-v1');await page.getByLabel('API Key').fill('FIXTURE-ONLY');await page.getByRole('button',{name:'保存配置'}).click();await expect(page.getByRole('heading',{name:'故事图测试 默认模型 可用配置'})).toBeVisible()
  await page.goto('/library');await page.getByRole('button',{name:'加入示例书'}).click();await expect(page.locator('.reader-body h1')).toHaveText('第一章 渡口')
  for(const title of ['第二章 空船','第三章 旧信']){await page.getByRole('button',{name:'下一章',exact:true}).click();await expect(page.locator('.reader-body h1')).toHaveText(title)}
  await page.locator('.paragraph').last().click();await page.getByRole('button',{name:'从这里写',exact:true}).click();await page.getByRole('button',{name:/开始写作/}).click();await expectSavedInline(page);await readSavedInline(page);await expect(page.locator('.reader-body [data-content-source="ai"]').first()).toBeVisible()
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.locator('.timeline-row')).toHaveCount(2);await expect(page.locator('.timeline-edges path')).toHaveCount(1)
}
const number=(page:Page,name:string)=>page.locator('.timeline-viewport').getAttribute(name).then(Number)

test('Graph motion: real pan, cursor zoom, node spring and live fork curve keep the reading route intact',async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width:1440,height:1000});await setup(page)
  await expect(page.locator('.timeline-page')).not.toContainText(/许多可能|看清故事|每条线保留|共用章节只显示|故事的起点|前文共用/)
  const edge=page.locator('.timeline-edges path'),child=page.locator('.timeline-row:not(.is-original) .timeline-chapter'),third=page.locator('.timeline-row.is-original .timeline-chapter').nth(2)
  expect(await edge.getAttribute('data-source')).toContain(await third.getAttribute('data-chapter-id'))
  expect(await edge.getAttribute('data-target')).toContain(await child.getAttribute('data-chapter-id'))
  await page.waitForTimeout(500);const before=await edge.getAttribute('d'),node=await child.locator('.timeline-node-core').boundingBox(),url=page.url()
  await page.mouse.move(node!.x+node!.width/2,node!.y+node!.height/2);await page.mouse.down();await page.mouse.move(node!.x+node!.width/2+26,node!.y+node!.height/2+28,{steps:8})
  await expect.poll(()=>edge.getAttribute('d')).not.toBe(before);await page.mouse.up();expect(page.url()).toBe(url)
  await expect.poll(()=>edge.getAttribute('d'),{timeout:3000}).toBe(before)
  const graph=await page.locator('.timeline-viewport').boundingBox(),oldX=await number(page,'data-camera-x')
  await page.mouse.move(graph!.x+35,graph!.y+35);await page.mouse.down();await page.mouse.move(graph!.x+95,graph!.y+65,{steps:8});await page.mouse.up();expect(await number(page,'data-camera-x')).toBeGreaterThan(oldX+40)
  const oldScale=await number(page,'data-camera-scale');await page.mouse.wheel(0,-120);await expect.poll(()=>number(page,'data-camera-scale')).toBeGreaterThan(oldScale)
  await page.getByRole('button',{name:'定位当前线',exact:true}).click();await child.focus();await page.waitForTimeout(350)
  expect(await child.locator('.timeline-node-core svg path').first().evaluate(node=>getComputedStyle(node).strokeOpacity)).toBe('1')
  await page.getByRole('button',{name:'重置故事图',exact:true}).click();await page.mouse.move(15,15);await child.evaluate(node=>(node as HTMLElement).blur());await page.waitForTimeout(350)
  await page.screenshot({path:path.join(artifacts,'graph-desktop.png')});await child.focus();await child.press('Enter');await expect(page.locator('.reader-body [data-content-source="ai"]').first()).toBeVisible()
})

test('Graph motion: mobile graph, minimap and compact menus remain within the viewport',async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width:390,height:900});await setup(page)
  const graph=await page.locator('.timeline-viewport').boundingBox(),third=await page.locator('.timeline-row.is-original .timeline-node-core').nth(2).boundingBox(),child=await page.locator('.timeline-row:not(.is-original) .timeline-node-core').boundingBox()
  expect(third!.x).toBeGreaterThanOrEqual(graph!.x);expect(child!.x+child!.width).toBeLessThanOrEqual(graph!.x+graph!.width)
  const summary=page.locator('.story-line-chip:not(.is-original) summary');await summary.click();await expect(page.locator('.timeline-line-menu[open]')).toHaveCount(1)
  const menu=await page.locator('.timeline-line-menu[open] .timeline-menu-content').boundingBox();expect(menu!.x).toBeGreaterThanOrEqual(0);expect(menu!.x+menu!.width).toBeLessThanOrEqual(390)
  await page.keyboard.press('Escape');await expect(page.locator('.timeline-line-menu[open]')).toHaveCount(0)
  const touch=await page.context().newCDPSession(page);await touch.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:2})
  const point=(id:number,x:number,y:number)=>({id,x,y,radiusX:4,radiusY:4,force:1})
  const oldX=await number(page,'data-camera-x'),x0=graph!.x+35,y0=graph!.y+45
  await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point(1,x0,y0)]})
  await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[point(1,x0+60,y0+20)]})
  await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await expect.poll(()=>number(page,'data-camera-x')).toBeGreaterThan(oldX+40)
  const scale=await number(page,'data-camera-scale'),mid=graph!.x+graph!.width/2
  await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point(1,mid-50,y0),point(2,mid+50,y0)]})
  await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[point(1,mid-70,y0),point(2,mid+70,y0)]})
  await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await expect.poll(()=>number(page,'data-camera-scale')).toBeGreaterThan(scale)
  await touch.send('Emulation.setTouchEmulationEnabled',{enabled:false});await touch.detach()
  const x=await number(page,'data-camera-x');await page.getByRole('button',{name:'故事图总览，点击定位',exact:true}).click({position:{x:20,y:20}});await expect.poll(()=>number(page,'data-camera-x')).not.toBe(x)
  await page.getByRole('button',{name:'定位当前线',exact:true}).click();await page.waitForTimeout(350);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.reload();await expect(page.locator('.timeline-row')).toHaveCount(2);await expect(page.locator('.timeline-edges path')).toHaveCount(1);await page.waitForTimeout(350)
  const tools=await page.locator('.graph-tools').boundingBox(),navigation=page.locator('.bottom-nav')
  const nav=await navigation.isVisible()?await navigation.boundingBox():null
  expect(tools!.y+tools!.height).toBeLessThanOrEqual(nav?.y||900)
  await page.screenshot({path:path.join(artifacts,'graph-mobile.png')})
})

test('Graph motion: reduced motion stops animation while keyboard navigation and node dragging still work',async({page})=>{
  test.setTimeout(90000);await page.emulateMedia({reducedMotion:'reduce'});await page.setViewportSize({width:1440,height:1000});await setup(page)
  await expect(page.locator('.timeline-viewport')).toHaveClass(/reduced-motion/)
  expect(await page.locator('.timeline-node-orbit').first().evaluate(node=>getComputedStyle(node).animationName)).toBe('none')
  expect(await page.locator('.timeline-edges path').evaluate(node=>getComputedStyle(node).animationName)).toBe('none')
  const view=page.locator('.timeline-viewport');await view.focus();const oldX=await number(page,'data-camera-x');await page.keyboard.press('ArrowRight');expect(await number(page,'data-camera-x')).toBeLessThan(oldX)
  const scale=await number(page,'data-camera-scale');await page.keyboard.press('+');expect(await number(page,'data-camera-scale')).toBeGreaterThan(scale)
  await page.keyboard.press('Home');const child=page.locator('.timeline-row:not(.is-original) .timeline-chapter'),edge=page.locator('.timeline-edges path'),before=await edge.getAttribute('d'),node=await child.locator('.timeline-node-core').boundingBox()
  await page.mouse.move(node!.x+node!.width/2,node!.y+node!.height/2);await page.mouse.down();await page.mouse.move(node!.x+node!.width/2+24,node!.y+node!.height/2+24,{steps:5});await expect.poll(()=>edge.getAttribute('d')).not.toBe(before);await page.mouse.up();await expect.poll(()=>edge.getAttribute('d')).toBe(before)
  await page.mouse.move(node!.x+node!.width/2,node!.y+node!.height/2);await page.mouse.down();await page.mouse.move(node!.x+node!.width/2+20,node!.y+node!.height/2+20,{steps:5});await expect.poll(()=>edge.getAttribute('d')).not.toBe(before)
  const url=page.url();await page.evaluate(()=>window.dispatchEvent(new Event('reader-save')));await expect.poll(()=>edge.getAttribute('d')).toBe(before);await expect(view).not.toHaveClass(/is-dragging/);await page.mouse.up();expect(page.url()).toBe(url)
  await child.focus();await child.press('Enter');await expect(page.locator('.reader-body [data-content-source="ai"]').first()).toBeVisible()
})

test('Graph motion: a long imported book loads real chapter pages and masks unread titles',async({page})=>{
  test.setTimeout(90000);await page.setViewportSize({width:390,height:900});await register(page)
  const content=Array.from({length:45},(_,index)=>`第${index+1}章 秘密标题${index+1}\n河岸传来潮声，林渡在码头等待。\n\n灯影落进水里，他把绳结重新系好。\n`).join('\n')
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'导入小说',exact:true}).click();await(await chooser).setFiles({name:'graph-long.txt',mimeType:'text/plain',buffer:Buffer.from(content)})
  await expect(page.getByRole('button',{name:'确认并开始阅读'})).toBeVisible({timeout:30000});await page.getByRole('button',{name:'确认并开始阅读'}).click();await expect(page.locator('.reader-body .paragraph').first()).toBeVisible()
  await page.getByRole('button',{name:'故事时间线',exact:true}).click();await expect(page.locator('.timeline-chapter')).toHaveCount(20)
  await expect(page.locator('.timeline-chapter').nth(19)).not.toContainText('秘密标题20')
  const next=page.getByRole('button',{name:'后 20 章',exact:true}),menu=page.locator('.timeline-line-menu summary');await menu.click();await next.click();await expect(page.locator('.timeline-node-footer').first()).toContainText('21');await expect(page.locator('.timeline-chapter')).toHaveCount(20);await expect(page.locator('.timeline-chapter').first()).toHaveAccessibleName(/第21章/)
  await menu.click();await next.click();await expect(page.locator('.timeline-chapter')).toHaveCount(5);await expect(page.locator('.timeline-node-footer').first()).toContainText('41');await menu.click();await expect(next).toBeDisabled();await page.keyboard.press('Escape');await expect(page.locator('.timeline-edges path')).toHaveCount(0)
  const last=page.locator('.timeline-chapter').last();await last.focus();await last.press('Enter');await expect(page.locator('.reader-body h1')).toHaveText('第45章 秘密标题45')
})
