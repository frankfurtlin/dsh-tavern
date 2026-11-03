import {describe,it,expect} from 'vitest'
import {defaultProfileId,mimoPreset} from './profiles'
import type {Profile} from './types'
const profile=(id:string,values:Partial<Profile>={}):Profile=>({id,name:id,baseUrl:'mock://deterministic',modelId:'fixture-v1',keyTail:'test',contextWindow:32768,outputParameter:'max_tokens',supportsJson:false,version:1,status:'active',callBudget:100,inputBudget:1000000,outputBudget:100000,callsReserved:0,inputReserved:0,outputReserved:0,...values})
describe('Account default model selection',()=>{
  it('uses the explicit account preference rather than list order',()=>expect(defaultProfileId([profile('old'),profile('preferred',{isDefault:true})])).toBe('preferred'))
  it('prefers the exact Go/MiMo preset for a legacy account',()=>expect(defaultProfileId([profile('old'),profile('mimo',{baseUrl:mimoPreset.baseUrl+'/',modelId:mimoPreset.modelId})])).toBe('mimo'))
  it('does not select a revoked default or a different host with the same model name',()=>expect(defaultProfileId([profile('usable'),profile('revoked',{isDefault:true,status:'revoked'}),profile('unrelated',{modelId:mimoPreset.modelId})])).toBe('usable'))
  it('leaves unavailable accounts unconfigured',()=>expect(defaultProfileId([profile('revoked',{status:'revoked'})])).toBe(''))
  it('never automatically selects a reactivated configuration without a key',()=>expect(defaultProfileId([profile('empty',{hasKey:false,isDefault:true}),profile('available',{hasKey:true})])).toBe('available'))
})
