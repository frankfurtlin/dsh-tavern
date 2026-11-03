import {describe,it,expect} from 'vitest'
import {loginDestination} from './auth-navigation'
describe('login destination',()=>{
  it('keeps the requested story line and anchor',()=>expect(loginDestination('/worlds/abc/play?line=def#action')).toBe('/worlds/abc/play?line=def#action'))
  it('returns to private prompt management after authentication',()=>expect(loginDestination('/my-prompts?draft=abc')).toBe('/my-prompts?draft=abc'))
  it.each(['/published/abc?opening=night','/authors/abc'])('keeps work and author deep links %s',value=>expect(loginDestination(value)).toBe(value))
  it.each(['//evil.test/a','https://evil.test','/\\evil.test','/login','/register','/local/library','/worlds/../../login','/worlds\n/abc',null,['/worlds']])('rejects an external, public or invalid destination %s',value=>expect(loginDestination(value,'/library')).toBe('/library'))
})
