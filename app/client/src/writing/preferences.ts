import {ContractError} from '../domain/local-contract'
export const writingPreferenceFields=['length','style','forbidden','authorNote','template'] as const
export type WritingPreferenceField=typeof writingPreferenceFields[number]
export interface WritingPreferences {length:number;style:string;forbidden:string;authorNote:string;template:string}
export interface WritingPreferenceLayer {assetType:'writing-preferences';scope:'global'|'book';storyId:string|null;values:Partial<WritingPreferences>}
export const writingDefaults:WritingPreferences={length:2000,style:'',forbidden:'',authorNote:'',template:''}
export const preferenceScopeLabels={default:'默认',global:'全局',book:'本书',once:'本次'} as const
export function validateWritingPreferences(value:unknown):asserts value is Partial<WritingPreferences>{
 const fail=():never=>{throw new ContractError('WRITING_PREFERENCES','写作设置格式无效，原设置保留')}
 if(!value||typeof value!=='object'||Array.isArray(value))fail()
 for(const [field,item] of Object.entries(value as Record<string,unknown>)){
  if(!writingPreferenceFields.includes(field as WritingPreferenceField))fail()
  if(field==='length'){if(!Number.isSafeInteger(item)||Number(item)<100||Number(item)>10000)fail()}
  else if(typeof item!=='string'||item.length>(field==='authorNote'||field==='template'?20000:10000))fail()
  if(field==='template'&&typeof item==='string'&&[...item.matchAll(/\{\{([^{}]+)\}\}/g)].some(m=>!['chapterTitle','direction',...writingPreferenceFields.filter(f=>f!=='template')].includes(m[1]!.trim())))fail()
 }
}
export function validateWritingPreferenceLayer(value:unknown,documentId?:string):asserts value is WritingPreferenceLayer{
 if(!value||typeof value!=='object'||Array.isArray(value))throw new ContractError('WRITING_PREFERENCES','写作设置格式无效')
 const v=value as WritingPreferenceLayer
 if(v.assetType!=='writing-preferences'||!['global','book'].includes(v.scope)||v.scope==='global'&&v.storyId!==null||v.scope==='book'&&(typeof v.storyId!=='string'||!v.storyId||v.storyId.length>160)||documentId!==undefined&&documentId!==(v.scope==='global'?'settings:writing:global':'settings:writing:book:'+v.storyId))throw new ContractError('WRITING_PREFERENCES','写作设置范围无效，未保存到其他书')
 validateWritingPreferences(v.values)
}
export function resolveWritingPreferences(global:Partial<WritingPreferences>={},book:Partial<WritingPreferences>={},once:Partial<WritingPreferences>={}){
 for(const layer of [global,book,once])validateWritingPreferences(layer)
 const values={...writingDefaults},sources:Record<WritingPreferenceField,keyof typeof preferenceScopeLabels>={length:'default',style:'default',forbidden:'default',authorNote:'default',template:'default'}
 for(const [scope,layer] of [['global',global],['book',book],['once',once]] as const)for(const field of writingPreferenceFields)if(Object.hasOwn(layer,field)){(values as unknown as Record<string,unknown>)[field]=layer[field];sources[field]=scope}
 return {values,sources}
}
export function writingTemplate(template:string,values:WritingPreferences,direction:string,chapterTitle:string){
 validateWritingPreferences(values)
 return template.replace(/\{\{([^{}]+)\}\}/g,(_,field:string)=>String(({...values,direction,chapterTitle} as Record<string,string|number>)[field.trim()]??''))
}
export function writingCapabilityWarnings(values:WritingPreferences,outputLimit:number){
 return outputLimit<values.length?['当前模型输出上限小于目标篇幅，章节可能提前停止。可降低目标字数或提高模型输出上限。']:[]
}
