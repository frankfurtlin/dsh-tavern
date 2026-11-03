import {describe,it,expect} from 'vitest'
import {boundedReplace} from './regex-replacement'
import {importRegexRule,exportRegexRule} from './regex'
describe('bounded regex output and inert asset import',()=>{
 it('keeps native capture, match, Unicode and literal-dollar replacement semantics',()=>{
  expect(boundedReplace('(灯)(塔)','g','灯塔🌿灯塔','$2$1/{{match}}/$$',[])).toEqual({text:'塔灯/灯塔/$🌿塔灯/灯塔/$',count:2})
  expect(boundedReplace('(?<a>灯塔)','','灯塔','$<a>',[]).text).toBe('灯塔')
 })
 it('literal trim applies to matched text without modifying capture groups',()=>{expect(boundedReplace('(apple)','','apple','{{match}}/$1',['le']).text).toBe('app/apple')})
 it('zero-width Unicode replacement advances safely without splitting surrogate pairs',()=>{expect(boundedReplace('(?:)','gu','🌿','X',[])).toEqual({text:'X🌿X',count:2})})
 it('bounds output incrementally before amplified replacement allocates a giant string',()=>{
  expect(()=>boundedReplace('$','','a'.repeat(100000),'$`'.repeat(10000),[])).toThrow('output-limit')
  expect(()=>boundedReplace('','g','a'.repeat(30000),'b',[])).toThrow('match-limit')
 })
 it('preserves ST rule metadata and independent display/prompt channels across edits',()=>{
  const rule=importRegexRule('{"id":"owned","scriptName":"晚灯","findRegex":"灯塔","replaceString":"🌿","placement":[1,2,5],"markdownOnly":true,"promptOnly":false,"disabled":true,"unknown":{"keep":true}}');rule.enabled=true;rule.display=false;rule.prompt=true
  const exported=exportRegexRule(rule),restored=importRegexRule(exported);expect(restored.enabled).toBe(true);expect(restored.display).toBe(false);expect(restored.prompt).toBe(true);expect(JSON.parse(exported).unknown).toEqual({keep:true})
 })
 it('rejects malformed placement, permissions and unbounded expressions before storage',()=>{for(const source of ['{"placement":[9]}','{"findRegex":"'+('a'.repeat(513))+'"}','{"trimStrings":[3]}'])expect(()=>importRegexRule(source)).toThrow()})
})
