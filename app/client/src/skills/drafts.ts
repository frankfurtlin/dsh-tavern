import {canonical,ContractError,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {skillId,type SkillRun} from './contract'

export interface SkillDraft {assetType:'skill-draft';skillId:string;input:SkillRun['input'];selected:string[];conflict:boolean;updatedAt:string}
export const skillDraftId=(id:string)=>'skill-draft:'+id
export function validateSkillDraft(value:unknown,id?:string):asserts value is SkillDraft{
 const v=value as SkillDraft
 if(!v||v.assetType!=='skill-draft'||!skillId(v.skillId)||!v.skillId.startsWith('skill:')||typeof v.conflict!=='boolean'||typeof v.updatedAt!=='string'||!Number.isFinite(Date.parse(v.updatedAt))||!Array.isArray(v.selected)||v.selected.length>32||!v.selected.every(skillId)||new Set(v.selected).size!==v.selected.length||!v.input||typeof v.input!=='object'||Array.isArray(v.input)||Object.keys(v.input).length>16||Object.entries(v.input).some(([key,item])=>!/^[a-zA-Z][a-zA-Z0-9_]{0,40}$/.test(key)||!(typeof item==='string'&&item.length<=20000||typeof item==='number'&&Number.isFinite(item)&&Math.abs(item)<=Number.MAX_SAFE_INTEGER||typeof item==='boolean'))||id!==undefined&&!(v.conflict?id.startsWith(skillDraftId(v.skillId)+':'):id===skillDraftId(v.skillId)))throw new ContractError('SKILL_DRAFT','技能草稿格式无效；原草稿保留')
}
/** A stale writer gets a separate encrypted copy, never replaces another window's draft. */
export async function saveSkillDraft(vault:LocalVault,skillId:string,expectedRevision:number,input:SkillRun['input'],selected:string[],conflictId?:string):Promise<Document<SkillDraft>>{
 const id=conflictId||skillDraftId(skillId),value:SkillDraft={assetType:'skill-draft',skillId,input:JSON.parse(canonical(input)),selected:[...selected],conflict:!!conflictId,updatedAt:new Date().toISOString()},operation=crypto.randomUUID()
 validateSkillDraft(value,id)
 try{await vault.commit(operation,'settings',id,expectedRevision,value)}catch(error){if(vault.unlocked&&(await vault.receipt(operation)))return (await vault.read<SkillDraft>(id))!;if((error as {code?:string}).code!=='REVISION_CONFLICT')throw error;return saveSkillDraft(vault,skillId,0,input,selected,skillDraftId(skillId)+':'+crypto.randomUUID())}
 return (await vault.read<SkillDraft>(id))!
}
