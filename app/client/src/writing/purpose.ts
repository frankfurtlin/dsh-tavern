import {ContractError} from '../domain/local-contract'
import {parseWritingProse} from './prose'
import {directionPresentation} from './directions'
export type WritingPurpose={kind:'directions'}|{kind:'rewrite'|'expand';editId:string;editRevision:number}
export function validateWritingPurpose(value:unknown):asserts value is WritingPurpose{
 if(!value||typeof value!=='object'||Array.isArray(value))throw new ContractError('WRITING_PURPOSE','写作操作格式无效')
 const v=value as WritingPurpose,keys=v.kind==='directions'?['kind']:['kind','editId','editRevision']
 if(!['directions','rewrite','expand'].includes(v.kind)||Object.keys(v).some(k=>!keys.includes(k))||v.kind!=='directions'&&(typeof v.editId!=='string'||!v.editId.startsWith('writing-edit:')||v.editId.length>160||!Number.isSafeInteger(v.editRevision)||v.editRevision<1))throw new ContractError('WRITING_PURPOSE','写作操作来源无效，未发送请求')
}
export const writingPresentation=(raw:string,purpose?:WritingPurpose)=>purpose?.kind==='directions'?directionPresentation(raw):parseWritingProse(raw)
