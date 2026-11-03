import {ContractError,type DomainKind} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {ChatSession} from './chat-contract'
import type {QuickReplySet} from './quick-replies'
export interface ScriptAuthority {setId:string;revision:number;buttonId:string;compat?:boolean}
/** Recheck both asset authorization and its current session/global binding. */
export async function scriptAuthority(vault:LocalVault,sessionId:string,sources:ScriptAuthority[],signal?:AbortSignal){
 signal?.throwIfAborted();if(!sources.length||sources.length>32)throw new ContractError('SCRIPT_REVOKED','脚本授权无效')
 const session=await vault.read<ChatSession>(sessionId),bindings=await vault.read<{quickReplyIds:string[]}>('settings:quick-replies')
 if(!session||session.kind!=='session'||session.value.archived)throw new ContractError('SCRIPT_SCOPE','会话已不可用，脚本停止')
 const ids=new Set([...(session.value.quickReplyIds||[]),...(bindings?.value.quickReplyIds||[])]),conditions:{id:string;revision:number;kind:DomainKind}[]=[{id:sessionId,revision:session.revision,kind:'session'},{id:'settings:quick-replies',revision:bindings?.revision||0,kind:'settings'}]
 for(const source of sources){const row=await vault.read<QuickReplySet>(source.setId);if(!row||row.kind!=='settings'||row.value.assetType!=='quick-replies'||row.revision!==source.revision||!ids.has(row.id)||!row.value.enabled||row.value.policy==='disabled'||source.compat&&row.value.policy!=='compat'||!row.value.buttons.some(button=>button.id===source.buttonId&&button.enabled))throw new ContractError('SCRIPT_REVOKED','按钮已停用、取消绑定或改变，脚本停止');if(!conditions.some(item=>item.id===row.id))conditions.push({id:row.id,revision:row.revision,kind:'settings'})}
 signal?.throwIfAborted();return conditions
}
