import {canonical,ContractError,type ProviderConfiguration,type ProviderSnapshot} from '../domain/local-contract'
import type {Timeouts} from '../provider-adapters/text'
/** Frozen fields are derived from the exact historical profile, never from its current head. */
export function validateWritingModel(snapshot:ProviderSnapshot,timeouts:Timeouts,dailyCalls:number,profile:ProviderConfiguration,revision:number,historical:boolean){
 const expected:ProviderSnapshot={id:profile.id,revision,protocol:profile.protocol,baseUrl:profile.baseUrl,modelId:profile.modelId,key:profile.key,contextLimit:profile.contextLimit,outputLimit:profile.outputLimit,outputParameter:profile.outputParameter||'max_tokens',includeUsage:profile.includeUsage===true,queryStoredResponse:profile.queryStoredResponse===true}
 const actual={...snapshot,outputParameter:snapshot.outputParameter||'max_tokens',includeUsage:snapshot.includeUsage===true,queryStoredResponse:snapshot.queryStoredResponse===true}
 if(!snapshot.id.startsWith('model:')||!historical&&!profile.enabled||!profile.slots.includes('text')||canonical(expected)!==canonical(actual)||dailyCalls!==profile.dailyCalls||canonical(timeouts)!==canonical({firstPacketMs:profile.firstPacketMs,idleMs:profile.idleMs,totalMs:profile.totalMs}))throw new ContractError('WRITING_MODEL_SOURCE','写作模型快照与授权的历史配置不一致，未发送或写入')
}
