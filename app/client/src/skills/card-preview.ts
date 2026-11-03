import {canonical} from '../domain/local-contract'
import {cardAuthoringFields} from './card-authoring-fields'

export const cardPreviewLabels:Record<string,string>={name:'姓名',description:'人物简介',personality:'性格与说话习惯',scenario:'故事背景',firstMessage:'开场白',exampleMessages:'对话示例',creatorNotes:'作者备注',systemPrompt:'系统提示',postHistoryInstructions:'后置提示',tags:'标签',alternateGreetings:'其他开场白'}
export function proposalPreviewFields(kind:string,before:Record<string,unknown>|null,after:Record<string,unknown>){
 const fields=kind==='card'?Object.values(cardAuthoringFields):Object.keys(after)
 return fields.filter(key=>before?canonical(before[key]??null)!==canonical(after[key]??null):after[key]!==undefined&&after[key]!==''&&(!Array.isArray(after[key])||(after[key] as unknown[]).length>0))
}
