import {describe,it,expect} from 'vitest'
import {expandMacros,type MacroContext} from './macros'
const context:MacroContext={char:'林渡',user:'梅舟',persona:'读者',description:'摆渡人',personality:'寡言',scenario:'码头',mesExamples:'灯亮了',input:'你好',lastMessage:'上一条',lastUserMessage:'上一问',lastCharMessage:'上一答',date:'2026-10-06',time:'01:00:00',seed:'fixed-vector',variables:{count:2},globalVariables:{harbor:'北岸'},pipe:'上一步'}
describe('bounded literal macro expansion',()=>{
 it('keeps character/user identities, case-insensitive V3 macros, UTF16 and unknown syntax',()=>{
  const output=expandMacros('{{CHAR}}/{{user}}/{{persona}}/{{description}}/{{personality}}/{{scenario}}/{{mesExamples}}/{{input}}/{{lastMessage}}/{{lastUserMessage}}/{{lastCharMessage}}/{{date}}/{{time}}/{{getvar::count}}/{{getglobalvar::harbor}}/{{pipe}}/{{unknown}}',context)
  expect(output.text).toBe('林渡/梅舟/读者/摆渡人/寡言/码头/灯亮了/你好/上一条/上一问/上一答/2026-10-06/01:00:00/2/北岸/上一步/{{unknown}}');expect(output.unknown).toEqual(['{{unknown}}'])
 })
 it('random, pick and dice replay with the frozen seed and never execute a command',()=>{
  const source='{{random::岸边::码头::灯塔}} {{pick:A,B,C}} {{random:A\\,B,C}} {{roll::2d6+3}} {{roll:6}} {{roll:d6}} /send {{constructor}}'
  const first=expandMacros(source,context),second=expandMacros(source,context)
  expect(first).toEqual(second);expect(first.text).not.toMatch(/\{\{(?:roll|random|pick)/);expect(first.text).toContain('/send {{constructor}}');expect(Object.keys(first.randomResults).length).toBeGreaterThan(3)
 })
 it('V3 hidden/comment macros are absent from wire text and Unicode reverse preserves emoji',()=>{
  expect(expandMacros('A{{// hidden}}{{comment:注释}}{{hidden_key:蓝灯}}{{reverse:🙂林渡}}B',context).text).toBe('A渡林🙂B')
 })
 it('recursive identity definitions stop at finite depth; oversized expansion fails before POST',()=>{
  expect(expandMacros('{{char}}',{...context,char:'{{char}}'}).unknown).toContain('展开达到深度上限，保留未展开原文')
  expect(()=>expandMacros('{{description}}'.repeat(1000),{...context,description:'正文'.repeat(1000)})).toThrow(/长度上限/)
 })
})
