import {ContractError} from '../domain/local-contract'

/** Shared by provider parsing, durable proposals and typed backup validation. */
export const cardAuthoringFields={name:'name',description:'description',personality:'personality',scenario:'scenario',first_mes:'firstMessage',mes_example:'exampleMessages',creator_notes:'creatorNotes',system_prompt:'systemPrompt',post_history_instructions:'postHistoryInstructions',tags:'tags',alternate_greetings:'alternateGreetings'} as const
export function validateAuthoredFields(value:unknown,requireNew=false,allowEmpty=false):asserts value is Record<string,unknown>{
 const fail=(message:string):never=>{throw new ContractError('CARD_AUTHORING',message+'；原卡和模型返回保留')}
 if(!value||typeof value!=='object'||Array.isArray(value))fail('生成的角色字段无效')
 const fields=value as Record<string,unknown>
 if(!allowEmpty&&!Object.keys(fields).length||Object.keys(fields).some(key=>!Object.hasOwn(cardAuthoringFields,key)))fail('生成字段不属于可编辑角色设定')
 for(const [field,item] of Object.entries(fields)){
  if(field==='tags'||field==='alternate_greetings'){if(!Array.isArray(item)||item.length>128||item.some(text=>typeof text!=='string'||text.length>(field==='tags'?120:30000)))fail('生成的角色列表字段无效')}
  else if(typeof item!=='string'||item.length>(field==='name'?200:30000)||field==='name'&&!item.trim())fail('生成的角色文字字段无效')
 }
 if(requireNew&&['name','description','first_mes'].some(field=>!Object.hasOwn(fields,field)))fail('新角色需要姓名、简介与开场字段')
}
