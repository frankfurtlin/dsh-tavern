import {describe,it,expect} from 'vitest'
import {memoryDiff} from './memory-diff'
describe('memory restoration preview',()=>{
 it('shows the actual changed range without splitting emoji',()=>{const d=memoryDiff('我们约好周五🌙见面','我们约好周六🌟见面');expect(d).toEqual({same:false,prefix:'我们约好周',removed:'五🌙',added:'六🌟',suffix:'见面'})})
 it('distinguishes clearing, unchanged and new memory',()=>{expect(memoryDiff('保留的约定','').removed).toBe('保留的约定');expect(memoryDiff('','新的约定').added).toBe('新的约定');expect(memoryDiff('相同','相同').same).toBe(true)})
})
