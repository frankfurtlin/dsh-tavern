import {describe,it,expect} from 'vitest'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
import ScriptProgress from './ScriptProgress.vue'
import type {ScriptProgress as Progress,EndingPhase} from './types'

const progress=(phase?:EndingPhase):Progress=>({completedCount:7,totalCount:7,objectives:[],
  ending:{id:'return',title:'亲自归还宝石',description:phase==='resolved'?'宝石已交还失主。':'你已选择归还，仍需实际交接。',phase},
  resolution:phase?{phase,completedCount:phase==='resolved'?1:0,totalCount:1,
    steps:[{id:'handoff',title:'交还失主',description:'取得后当面交付',origin:'adaptation',available:true,completed:phase==='resolved'}]}:null})

describe('ending obligations',()=>{
  it.each([undefined,'selected','resolving','legacy_unverified'] as const)('does not announce completion for %s',async phase=>{
    const html=await renderToString(createSSRApp(ScriptProgress,{progress:progress(phase)}))
    expect(html).not.toContain('故事已完成');expect(html).not.toContain('宝石已交还失主')
    expect(html).toContain('主线 7 / 7')
  })
  it('shows completion only with resolved aftermath',async()=>{
    const html=await renderToString(createSSRApp(ScriptProgress,{progress:progress('resolved')}))
    expect(html).toContain('故事已完成');expect(html).toContain('善后 1 / 1');expect(html).toContain('宝石已交还失主')
  })
  it('refreshes from a resolved branch to a pending branch without stale outcome',async()=>{
    const a=await renderToString(createSSRApp(ScriptProgress,{progress:progress('resolved')}))
    const b=await renderToString(createSSRApp(ScriptProgress,{progress:progress('selected')}))
    expect(a).toContain('故事已完成');expect(b).toContain('待办理');expect(b).not.toContain('故事已完成')
  })
})
