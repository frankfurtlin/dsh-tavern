import {describe,it,expect,vi,beforeEach} from 'vitest'
const state=vi.hoisted(()=>({route:{path:'/worlds/a/author',fullPath:'/worlds/a/author?tab=fields'},owner:'one',auth:1,dispose:()=>{}}))
vi.mock('vue',()=>({onUnmounted:(fn:()=>void)=>{state.dispose=fn}}))
vi.mock('vue-router',()=>({useRoute:()=>state.route}))
vi.mock('../api',()=>({authGeneration:()=>state.auth}))
vi.mock('./bridge',()=>({accountId:()=>state.owner}))
import {useRequestScope} from './request-scope'
beforeEach(()=>{state.route={path:'/worlds/a/author',fullPath:'/worlds/a/author?tab=fields'};state.owner='one';state.auth=1})
describe('resource request lifetime',()=>{
 it('keeps default query-sensitive behavior',()=>{const check=useRequestScope()();state.route.fullPath='/worlds/a/author?tab=test';expect(check.valid()).toBe(false)})
 it('allows author tab changes while still rejecting resource and account changes',()=>{let world='a';const capture=useRequestScope(()=>world,()=>state.route.path),check=capture();state.route.fullPath='/worlds/a/author?tab=test';expect(check.valid()).toBe(true);world='b';expect(check.valid()).toBe(false);world='a';state.owner='two';expect(check.valid()).toBe(false);state.owner='one';state.auth++;expect(check.valid()).toBe(false);state.auth--;state.route.path='/worlds/b/author';expect(check.valid()).toBe(false)})
 it('rejects responses after unmount',()=>{const check=useRequestScope(()=>null,()=>state.route.path)();state.dispose();expect(check.valid()).toBe(false)})
})
