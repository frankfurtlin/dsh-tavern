import {canonical,ContractError} from '../domain/local-contract'
import type {SkillManifest} from './contract'
export function nextSkillVersion(version:string){const parts=version.split('-')[0]!.split('.').map(BigInt);return parts[0]+'.'+parts[1]+'.'+(parts[2]!+1n)}
export function validateSkillVersion(before:SkillManifest,after:SkillManifest){
 if(before.id!==after.id||before.operation!==after.operation)throw new ContractError('SKILL_IDENTITY','执行器与技能标识不可改写；请新建副本')
 if(canonical(before)===canonical(after))return
 const parts=(value:string)=>value.split('-')[0]!.split('.').map(BigInt),a=parts(before.version),b=parts(after.version);let comparison=0;for(let i=0;i<3;i++){if(a[i]!==b[i]){comparison=b[i]!>a[i]!?1:-1;break}}
 if(comparison<0||comparison===0&&(before.version===after.version||!before.version.includes('-')||after.version.includes('-')))throw new ContractError('SKILL_VERSION','修改内容请提升版本；回退也保存为新的版本')
}
