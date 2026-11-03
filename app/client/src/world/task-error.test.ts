import {expect,it} from 'vitest'
import {taskError} from './task-error'
it('explains a blocked journey while keeping the received draft',()=>{
 expect(taskError('WORLD_ROUTE_RULE','failed')).toContain('路线暂时无法通行')
 expect(taskError('WORLD_ROUTE_RULE','failed')).toContain('正文已保留')
 expect(taskError('UNKNOWN','failed')).toBe('这次未完成，正文已作为草稿保留。')
 expect(taskError('WORLD_ROUTE_RULE','cancelled')).toBe('已停止，草稿已保留。')
})
it('distinguishes connection establishment failure from an interrupted model response',()=>{
 expect(taskError('PROVIDER_CONNECTION_FAILED','failed')).toContain('当前步骤尚未发送')
 expect(taskError('PROVIDER_CONNECTION_FAILED','failed')).not.toContain('正文')
 expect(taskError('PROVIDER_STATUS_UNKNOWN','failed')).toContain('已收到的正文会保留')
})
it('never claims an empty failed request has a retained draft',()=>{
 for(const code of ['PROVIDER_STATUS_UNKNOWN','WORLD_ROUTE_RULE','WORLD_RESOURCE_RULE','WORLD_OUTPUT_INVALID','CONTEXT_BUDGET_EXCEEDED']){
  const message=taskError(code,'failed',false)
  expect(message).not.toMatch(/正文已保留|草稿已保留|正文会保留/)
  expect(message).toContain('世界状态未推进')
 }
})
