import type {ActionKind} from './types'

// Keep the engine's typed actions without asking the reader to classify prose.
export function interpretInput(text:string,targetType?:string):ActionKind{
  const value=text.trim()
  if(/走到|前往|离开|攻击|拔刀|拿起|取出|推开|打开|交给/.test(value))return 'action'
  if(/等待|等到|休息|睡一|停留/.test(value))return 'wait'
  if(/观察|查看|检查|环顾|看看|打量/.test(value))return 'observe'
  if(targetType==='character'||/询问|问问|告诉|对.+说|你好|你觉得|[“”「」]/.test(value))return 'dialogue'
  return 'action'
}
