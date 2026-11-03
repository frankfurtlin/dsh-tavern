import {canonical,ContractError,type DomainKind} from '../domain/local-contract'
import {parseStoryDirections,type StoryDirection} from './directions'
import {resolveWritingPreferences,validateWritingPreferences,validateWritingPreferenceLayer,type WritingPreferences,type WritingPreferenceField,type WritingPreferenceLayer} from './preferences'
import type {WritingRun} from './contract'
export interface PreferenceSnapshot {
 global:{id:string;revision:number;values:Partial<WritingPreferences>}|null
 book:{id:string;revision:number;values:Partial<WritingPreferences>}|null
 once:Partial<WritingPreferences>
 sources:Record<WritingPreferenceField,'default'|'global'|'book'|'once'>
}
export interface WritingComposeDraft {assetType:'writing-compose-draft';storyId:string;count:number;direction:string;once:Partial<WritingPreferences>;selectedPlanId:string|null;createdAt?:string}
export interface WritingComposeView {assetType:'writing-compose-view';storyId:string;draftId:string}
export interface WritingDirectionPlan {assetType:'writing-directions';runId:string;runRevision:number;storyId:string;original:StoryDirection[];choices:{id:string;title:string;direction:string}[];createdAt:string}
const fail=():never=>{throw new ContractError('WRITING_PLAN','写作方向或设置范围无效，旧内容保留')}
const id=(v:unknown)=>typeof v==='string'&&!!v&&v.length<=160
export function validatePreferenceSnapshot(value:unknown,storyId:string,final:WritingPreferences){
 if(!value||typeof value!=='object'||Array.isArray(value))fail()
 const v=value as PreferenceSnapshot
 for(const [scope,layer] of [['global',v.global],['book',v.book]] as const){if(layer!==null){if(!layer||layer.id!==(scope==='global'?'settings:writing:global':'settings:writing:book:'+storyId)||!Number.isSafeInteger(layer.revision)||layer.revision<1)fail();validateWritingPreferences(layer.values)}}
 validateWritingPreferences(v.once)
 const resolved=resolveWritingPreferences(v.global?.values,v.book?.values,v.once)
 if(canonical(resolved.values)!==canonical(final)||canonical(resolved.sources)!==canonical(v.sources))fail()
}
export function validateWritingComposeDraft(value:unknown,documentId?:string):asserts value is WritingComposeDraft{
 if(!value||typeof value!=='object'||Array.isArray(value))fail()
 const v=value as WritingComposeDraft
 if(v.assetType!=='writing-compose-draft'||!id(v.storyId)||documentId!==undefined&&documentId!=='settings:writing-draft:'+v.storyId&&!documentId.startsWith('writing-compose-copy:')||!Number.isSafeInteger(v.count)||v.count<1||v.count>10||typeof v.direction!=='string'||v.direction.length>20000||!(v.selectedPlanId===null||id(v.selectedPlanId))||v.createdAt!==undefined&&!Number.isFinite(Date.parse(v.createdAt))||documentId?.startsWith('writing-compose-copy:')&&!v.createdAt)fail()
 validateWritingPreferences(v.once)
}
export function validateWritingComposeView(value:unknown,documentId?:string):asserts value is WritingComposeView{const v=value as WritingComposeView;if(!v||v.assetType!=='writing-compose-view'||!id(v.storyId)||!id(v.draftId)||documentId!==undefined&&documentId!=='settings:writing-compose-view:'+v.storyId)fail()}
export function validateWritingDirectionPlan(value:unknown,documentId?:string):asserts value is WritingDirectionPlan{
 if(!value||typeof value!=='object'||Array.isArray(value))fail()
 const v=value as WritingDirectionPlan
 if(v.assetType!=='writing-directions'||!id(v.runId)||!v.runId.startsWith('writing-run:')||!id(v.storyId)||documentId!==undefined&&!documentId.startsWith('writing-directions:')||!Number.isSafeInteger(v.runRevision)||v.runRevision<1||typeof v.createdAt!=='string'||!Number.isFinite(Date.parse(v.createdAt))||!Array.isArray(v.choices)||v.choices.length!==3)fail()
 parseStoryDirections(JSON.stringify({choices:v.original}))
 if(v.choices.some(c=>!c||!id(c.id)||typeof c.title!=='string'||!c.title.trim()||c.title.length>100||typeof c.direction!=='string'||!c.direction.trim()||c.direction.length>20000)||new Set(v.choices.map(c=>c.id)).size!==3)fail()
}
export function planningReferences(kind:DomainKind,value:Record<string,unknown>){
 const refs:{id:string;kind:DomainKind;revision?:number}[]=[]
 if(kind!=='settings')return refs
 if(['writing-compose-draft','writing-compose-view','writing-directions'].includes(String(value.assetType))||value.assetType==='writing-preferences'&&value.scope==='book')refs.push({id:String(value.storyId),kind:'story'})
 if(value.assetType==='writing-compose-view')refs.push({id:String(value.draftId),kind:'settings'})
 if(value.assetType==='writing-compose-draft'&&value.selectedPlanId)refs.push({id:String(value.selectedPlanId),kind:'settings'})
 if(value.assetType==='writing-directions'){const v=value as unknown as WritingDirectionPlan;refs.push({id:v.runId,kind:'settings',revision:v.runRevision})}
 if(value.assetType==='writing-run'&&value.preferenceSnapshot){const snapshot=value.preferenceSnapshot as PreferenceSnapshot;for(const layer of [snapshot.global,snapshot.book])if(layer)refs.push({...layer,kind:'settings'})}
 return refs
}
export function validatePlanningRelations(kind:DomainKind,value:Record<string,unknown>,lookup:(id:string,revision?:number)=>{kind:DomainKind;revision?:number;value:unknown}|undefined){
 if(kind!=='settings')return
 if(value.assetType==='writing-compose-view'){const row=lookup(String(value.draftId));if(row?.kind!=='settings'||(row.value as WritingComposeDraft).assetType!=='writing-compose-draft'||(row.value as WritingComposeDraft).storyId!==value.storyId)fail()}
 if(value.assetType==='writing-compose-draft'&&value.selectedPlanId){const plan=lookup(String(value.selectedPlanId));if(plan?.kind!=='settings'||(plan.value as WritingDirectionPlan).assetType!=='writing-directions'||(plan.value as WritingDirectionPlan).storyId!==value.storyId)fail()}
 if(value.assetType==='writing-directions'){
  const plan=value as unknown as WritingDirectionPlan,row=lookup(plan.runId,plan.runRevision),run=row?.value as WritingRun|undefined
  if(row?.kind!=='settings'||row.revision!==plan.runRevision||run?.assetType!=='writing-run'||run.purpose?.kind!=='directions'||run.storyId!==plan.storyId||run.status!=='completed'||canonical(parseStoryDirections(run.chapters[0]!.raw??run.chapters[0]!.text))!==canonical(plan.original))fail()
 }
 if(value.assetType==='writing-run'&&value.preferenceSnapshot){const run=value as unknown as WritingRun;for(const [scope,layer] of [['global',run.preferenceSnapshot!.global],['book',run.preferenceSnapshot!.book]] as const)if(layer){const row=lookup(layer.id,layer.revision),v=row?.value as WritingPreferenceLayer|undefined;if(row?.kind!=='settings'||row.revision!==layer.revision||v?.assetType!=='writing-preferences'||v.scope!==scope||v.storyId!==(scope==='global'?null:run.storyId)||canonical(v.values)!==canonical(layer.values))fail();validateWritingPreferenceLayer(v,layer.id)}}
}
