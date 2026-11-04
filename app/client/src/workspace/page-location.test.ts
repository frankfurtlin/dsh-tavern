import {describe,it,expect} from 'vitest'
import {pageLocation} from './page-location'
const locate=(path:string,params:Record<string,string>={})=>pageLocation({path,params,meta:{}})
describe('secondary page navigation',()=>{
 it.each(['/product/account','/product/wallet','/product/admin','/data-use','/features','/settings/import-history'])('%s returns to discovery on direct entry',path=>{
  expect(locate(path).parent?.path).toBe('/explore')
  expect(locate(path).title).not.toBe('酒馆')
 })
 it('routes a world chat back to the character library',()=>{
  expect(locate('/conversations/world/session-1')).toMatchObject({area:{path:'/characters'},parent:{path:'/characters'}})
 })
 it('returns from an author editor to its exact work',()=>expect(locate('/worlds/abc/author',{worldId:'abc'})).toMatchObject({area:{path:'/worlds/scripts'},parent:{path:'/stories/world-abc'}}))
 it.each(['/published/abc','/authors/abc'])('%s has a predictable discovery fallback',path=>expect(locate(path).parent?.path).toBe('/explore'))
 it.each(['/stories/world-abc','/worlds/abc/lines','/books/abc/branches'])('%s retains the story-specific return control',path=>expect(locate(path).ownBack).toBe(true))
 it('does not add a redundant return control to a main navigation page',()=>expect(locate('/my-prompts').parent).toBeUndefined())
})
