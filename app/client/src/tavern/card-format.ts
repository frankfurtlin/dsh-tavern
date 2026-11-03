import type {NovelCardOrigin} from '../novel-characters/contract'
import {ContractError,canonical} from '../domain/local-contract'
import type {CardContainer} from './card-container'
export interface CharacterCard {identityId:string;format:'v1'|'v2'|'v3';source:Record<string,unknown>;data:Record<string,unknown>;name:string;description:string;personality:string;scenario:string;firstMessage:string;exampleMessages:string;creatorNotes:string;systemPrompt:string;postHistoryInstructions:string;tags:string[];alternateGreetings:string[];container?:CardContainer;novelOrigin?:NovelCardOrigin}
const strings=(value:unknown):string[]=>Array.isArray(value)?value.filter((x):x is string=>typeof x==='string'):[]
const text=(value:unknown)=>typeof value==='string'?value:''
export function readCard(json:string):CharacterCard{
  if(new TextEncoder().encode(json).length>8*1024*1024)throw new ContractError('CARD_LIMIT','角色卡超过 8 MiB')
  let value:unknown;try{value=JSON.parse(json)}catch{throw new ContractError('CARD_INVALID','角色卡 JSON 无效')}
  if(!value||typeof value!=='object'||Array.isArray(value))throw new ContractError('CARD_INVALID','角色卡结构无效')
  const source=value as Record<string,unknown>,format=source.spec==='chara_card_v3'?'v3':source.spec==='chara_card_v2'?'v2':'v1'
  if(source.spec&&format==='v1')throw new ContractError('CARD_VERSION_UNSUPPORTED','这份角色卡版本暂不支持；原文件保留')
  const data=format==='v1'?source:source.data
  if(!data||typeof data!=='object'||Array.isArray(data))throw new ContractError('CARD_INVALID','角色卡缺少 data')
  const fields=data as Record<string,unknown>,name=text(fields.name).trim()
  if(!name||name.length>200)throw new ContractError('CARD_NAME_INVALID','角色卡需要有效姓名')
  return {identityId:crypto.randomUUID(),format,source:JSON.parse(canonical(source)),data:JSON.parse(canonical(fields)),name,description:text(fields.description),personality:text(fields.personality),scenario:text(fields.scenario),firstMessage:text(fields.first_mes),exampleMessages:text(fields.mes_example),creatorNotes:text(fields.creator_notes),systemPrompt:text(fields.system_prompt),postHistoryInstructions:text(fields.post_history_instructions),tags:strings(fields.tags),alternateGreetings:strings(fields.alternate_greetings)}
}
export function writeCard(card:CharacterCard):string {
  const data={...card.data,name:card.name,description:card.description,personality:card.personality,scenario:card.scenario,first_mes:card.firstMessage,mes_example:card.exampleMessages,creator_notes:card.creatorNotes,system_prompt:card.systemPrompt,post_history_instructions:card.postHistoryInstructions,tags:card.tags,alternate_greetings:card.alternateGreetings}
  const value=card.format==='v1'?{...card.source,...data}:{...card.source,spec:card.format==='v3'?'chara_card_v3':'chara_card_v2',spec_version:card.source.spec_version||(card.format==='v3'?'3.0':'2.0'),data}
  const pretty=JSON.stringify(value,null,2)
  if(new TextEncoder().encode(pretty).length<=8*1024*1024)return pretty
  const compact=JSON.stringify(value)
  if(new TextEncoder().encode(compact).length>8*1024*1024)throw new ContractError('CARD_EXPORT_LIMIT','角色卡导出超过 8 MiB，请缩小字段；原版本保留')
  return compact
}
export function emptyCard():CharacterCard{return readCard('{"spec":"chara_card_v3","spec_version":"3.0","data":{"name":"新角色","extensions":{}}}')}
