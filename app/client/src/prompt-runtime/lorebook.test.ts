import {describe,it,expect} from 'vitest'
import {importLorebook,exportLorebook,validateLorebook} from './lorebook'
describe('worldbook canonical asset contract',()=>{
 it('round-trips V3 envelope, fields and unknown extensions without dropping original source',()=>{
  const source={spec:'lorebook_v3',unknown_root:{preserve:true},data:{name:'港口手记',scan_depth:7,token_budget:1500,recursive_scanning:true,extensions:{owned:{keep:'unknown'}},entries:[{id:'harbor-fact',keys:['北塔'],secondary_keys:['来信'],selective:true,constant:false,use_regex:true,enabled:true,insertion_order:10,content:'{{char}} 记得北塔。',extensions:{nested:{stay:true},speakerIds:['identity-A'],probability:50,cooldown:2,delay:3}}]}}
  const book=importLorebook(JSON.stringify(source));expect(book.entries[0]?.speakerIds).toEqual(['identity-A']);book.entries[0]!.content='仅林渡知道北塔。';const output=JSON.parse(exportLorebook(book));expect(output.unknown_root).toEqual(source.unknown_root);expect(output.data.extensions).toEqual(source.data.extensions);expect(output.data.entries[0].extensions.nested).toEqual({stay:true});expect(importLorebook(JSON.stringify(output)).entries[0]?.content).toBe('仅林渡知道北塔。')
 })
 it('accepts supported numeric-map entry import without importing execution permissions',()=>{
  const book=importLorebook('{"name":"港口","entries":{"0":{"key":["灯塔"],"content":"看得见的蓝灯","disable":true,"order":2,"extensions":{"untrusted_command":"/send stolen"}}}}')
  expect(book.entries[0]?.enabled).toBe(false);expect(book.entries[0]?.keys).toEqual(['灯塔']);expect(book.entries[0]?.source.extensions).toEqual({untrusted_command:'/send stolen'})
 })
 it('rejects malformed probability, duplicate stable IDs and unbounded scans before write',()=>{
  for(const raw of ['{"name":"手记","scan_depth":1001,"entries":[]}','{"name":"手记","entries":[{"id":"x","probability":101}]}','{"name":"手记","entries":[{"id":"same"},{"id":"same"}]}'])expect(()=>importLorebook(raw)).toThrow()
  const book=importLorebook('{"name":"手记","entries":[]}');book.entries.push({id:'bad'} as never);expect(()=>validateLorebook(book)).toThrow()
 })
})
