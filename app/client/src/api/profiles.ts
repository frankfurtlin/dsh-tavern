import type {Profile} from './types'

export const mimoPreset = {name:'OpenCode Go · MiMo 2.6 Flash',baseUrl:'https://opencode.ai/zen/go/v1',modelId:'mimo-v2.6-flash',contextWindow:1048576,outputParameter:'max_tokens',supportsJson:true}

export function defaultProfileId(profiles:Profile[]):string {
  const available=profiles.filter(profile=>profile.status==='active'&&profile.hasKey!==false)
  return available.find(profile=>profile.isDefault)?.id
    ||available.find(profile=>profile.modelId===mimoPreset.modelId&&profile.baseUrl.replace(/\/$/,'')===mimoPreset.baseUrl)?.id
    ||available[0]?.id||''
}
