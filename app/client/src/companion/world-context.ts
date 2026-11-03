import {ContractError} from '../domain/local-contract'

export interface CompanionWorld {
  schema:'companion-world-v1';name:string;description:string;currentPlace:string;dayPlan:string
  people:{name:string;description:string}[];shareWithChat:boolean;updatedAt:string
}
export type CompanionWorldDraft=Omit<CompanionWorld,'schema'|'updatedAt'>
export function worldDraft(world?:CompanionWorld):CompanionWorldDraft {
  return world?{name:world.name,description:world.description,currentPlace:world.currentPlace,dayPlan:world.dayPlan,people:world.people.map(person=>({...person})),shareWithChat:world.shareWithChat}:{name:'',description:'',currentPlace:'',dayPlan:'',people:[],shareWithChat:false}
}
export function validateCompanionWorld(value:unknown):asserts value is CompanionWorld {
  const fail=()=>{throw new ContractError('COMPANION_WORLD','伴侣世界设置无效；原档案保留')},obj=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v),text=(v:unknown,max:number)=>typeof v==='string'&&v.length<=max
  if(!obj(value))return fail()
  if(value.schema!=='companion-world-v1'||!text(value.name,120)||!text(value.description,6000)||!text(value.currentPlace,120)||!text(value.dayPlan,4000)||typeof value.shareWithChat!=='boolean'||typeof value.updatedAt!=='string'||!Number.isFinite(Date.parse(value.updatedAt))||!Array.isArray(value.people)||value.people.length>10)return fail()
  for(const person of value.people)if(!obj(person)||!text(person.name,120)||!(person.name as string).trim()||!text(person.description,2000))return fail()
}
/** User-authored context is opt-in, bound to the current companion revision. */
export function companionWorldContext(world?:CompanionWorld):string|null {
  if(!world||!world.shareWithChat)return null
  validateCompanionWorld(world)
  const {name,description,currentPlace,dayPlan,people}=world
  return '用户明确设定的虚构伴侣世界（不是实时位置、真实联系人或已发生的事件；不得据此编造共同经历）：\n'+JSON.stringify({name,description,currentPlace,dayPlan,people})
}
