import {canonical,ContractError} from '../domain/local-contract'
import type {CharacterCard} from '../tavern/card-format'
import {authoredCard,type CardAuthoringOperation} from './card-authoring'
import type {SkillReference,SkillOutput} from './contract'
export function cardAuthoringResult(operation:CardAuthoringOperation,raw:string,refs:SkillReference[]):SkillOutput{
 const fail=(message:string):never=>{throw new ContractError('CARD_AUTHORING',message+'；原返回保留，可查看或复制')}
 let value:unknown;try{value=JSON.parse(raw.trim().replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,''))}catch{fail('生成结果尚不是完整JSON')}
 if(!value||typeof value!=='object'||Array.isArray(value))fail('生成结果结构无效')
 const result=value as Record<string,unknown>;if(Object.keys(result).some(field=>!['summary','card'].includes(field))||typeof result.summary!=='string'||!result.summary.trim()||result.summary.length>4000)fail('生成结果需要改动说明和角色字段')
 const target=operation==='card-revise'?refs.find(ref=>ref.kind==='card'):undefined;if(operation==='card-revise'&&refs.filter(ref=>ref.kind==='card').length!==1)fail('修订结果需要一个冻结的角色来源')
 const before=target?JSON.parse(canonical(target.value)):null,after=authoredCard(result.card,before?before as CharacterCard:undefined),id=target?.id||crypto.randomUUID()
 return {format:operation+'-result-v1',title:operation==='card-design'?'新的角色设定':'角色修订候选',body:result.summary as string,citations:[],proposals:[{kind:'card',id,expectedRevision:target?.revision||0,before,after:after as unknown as Record<string,unknown>,label:after.name}],links:[{path:'/local/characters?card='+encodeURIComponent(id),label:'到角色库查看'}]}
}
