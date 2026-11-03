import {it,expect} from 'vitest'
import {sceneFeedback} from './scene-feedback'
import type {SceneView} from './scene-types'
const scene=(value:Partial<SceneView>)=>value as SceneView
it('distinguishes a saved image rejected by vision from a genuinely unknown image response',()=>{
  const rejected=sceneFeedback(scene({status:'unknown',recovery:{stage:'inspection',imageRetained:true,canInspect:true,httpStatus:400}}),false)
  expect(rejected.title).toContain('图片已保存');expect(rejected.description).toContain('HTTP 400')
  const unknown=sceneFeedback(scene({status:'unknown'}),false)
  expect(unknown.title).toBe('图片生成结果尚未确认');expect(unknown.description).toContain('不会自动重复出图')
})
it('shows model availability and a saved-image progress message without inventing success',()=>{
  const s=scene({recovery:{stage:'inspection',imageRetained:true,canInspect:true,rejectionCategory:'model_unavailable'}})
  expect(sceneFeedback(s,false).description).toContain('视觉模型暂不可用')
  expect(sceneFeedback(s,true).title).toBe('正在校验并补全场景')
})
it('explains a stopped visual pipeline without treating it as a story cancellation',()=>{
  const stopped=sceneFeedback(scene({status:'failed',errorCode:'SCENE_STOPPED'}),false)
  expect(stopped.title).toBe('场景生成已停止')
  expect(stopped.description).toContain('主线进度不变')
  expect(stopped.description).toContain('实际用量')
})
it.each([429,402])('explains a retained image when supplier HTTP %s interrupts verification',httpStatus=>{
  const result=sceneFeedback(scene({status:'failed',recovery:{stage:'inspection',imageRetained:true,canInspect:true,httpStatus}}),false)
  expect(result.title).toContain(httpStatus===429?'服务繁忙':'额度不足')
  expect(result.description).toContain('复用图片')
  expect(result.description).toContain('文字游玩')
  expect(result.description).not.toContain('配置模型')
})

it('counts down a long supplier cooldown without promising an automatic retry',()=>{
  const now=Date.now(),until=new Date(now+6512_000).toISOString()
  const s=scene({status:'failed',recovery:{stage:'inspection',imageRetained:true,canInspect:true,httpStatus:429,retryAt:until,retryAfterSeconds:6512}})
  const early=sceneFeedback(s,false,now)
  expect(early.title).toContain('等待校验服务恢复')
  expect(early.description).toContain('108 分 32 秒')
  expect(early.description).toContain('手动继续')
  expect(early.description).toContain('不会自动发起新请求')
  expect(sceneFeedback(s,false,now+6513_000).description).not.toContain('还需')
  expect(sceneFeedback({...s,recovery:{...s.recovery!,retryAt:'invalid'}},false,now).description).not.toContain('NaN')
})
