import {ContractError,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {ChatSession} from '../tavern/chat-contract'
import {importRegexRule,type RegexAsset} from './regex'
export async function resolveRegex(vault:LocalVault,session:ChatSession,speakerId:string){
 const defaults=await vault.read<{regexIds:string[]}>('settings:regex-bindings'),ids=[...new Set([...(defaults?.value.regexIds||[]),...(session.regexIds||[])])],rows=await vault.readMany<RegexAsset>(ids)
 if(rows.length!==ids.length||rows.some(row=>row.kind!=='settings'||row.value.assetType!=='regex'))throw new ContractError('REGEX_REFERENCE','绑定的正则缺失；未发送请求')
 const member=session.cards.find(row=>row.value.identityId===speakerId),extension=member?.value.data.extensions as Record<string,unknown>|undefined,embedded=extension?.regex_scripts
 if(session.allowCardRegex&&member&&Array.isArray(embedded)){if(embedded.length>32)throw new ContractError('REGEX_LIMIT','卡内正则超过 32 条');for(const [index,value] of embedded.entries()){const rule=importRegexRule(JSON.stringify(value));rows.push({id:'embedded-regex:'+member.id+':'+index,revision:member.revision,kind:'settings',updatedAt:'',value:{assetType:'regex',name:rule.name,rule,scopeIdentityId:member.value.identityId}})}}
 return {rows,preconditions:[...rows.filter(row=>!row.id.startsWith('embedded-regex:')).map(row=>({id:row.id,revision:row.revision,kind:'settings' as const})),{id:'settings:regex-bindings',revision:defaults?.revision||0,kind:'settings' as const}]}
}
