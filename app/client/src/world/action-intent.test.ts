import {expect,it} from 'vitest'
import {interpretInput} from './action-intent'
it.each([
  ['我走到炉边取出药材。',undefined,'action'],
  ['你觉得这枚铜牌意味着什么？','character','dialogue'],
  ['我拔刀攻击陆遥。','character','action'],
  ['我仔细查看铜路牌。','observation','observe'],
  ['我等待雪停。',undefined,'wait'],
  ['我不再等待，拔刀迎战。',undefined,'action'],
  ['向旅人询问来路',undefined,'dialogue'],
  ['我把两块牌子放在一起。',undefined,'action'],
])('interprets %s without category controls',(text,target,expected)=>expect(interpretInput(text,target)).toBe(expected))
