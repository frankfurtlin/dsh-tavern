import {canonical,ContractError,unicodeOffset,type Document,type LocalStory,type Paragraph,type ProgressAnchor,type ProviderConfiguration,type DomainKind} from '../domain/local-contract'
import {validateLocalValue} from '../domain/local-validation'
import type {CharacterCursor} from './chunking'
import type {CharacterLookup,CharacterScan,NovelCharacter} from './contract'
import type {SkillRun} from '../skills/contract'

export interface CharacterIndexSource {storyId:string;storyRevision:number;branchId:string|null;branchRevision:number|null;anchor:ProgressAnchor}
export interface CharacterInterval {id:string;revision:number;start:number;limit:number}
export interface CharacterIndexEntry {scanId:string;coverage:CharacterInterval[];next:CharacterCursor|null;bindings:{id:string;revision:number}[]}
/** A finite, explicitly authorized parent. Each entry reserves one call before dispatch. */
export interface CharacterIndex {
 assetType:'novel-character-index';schema:'novel-character-index-v1';source:CharacterIndexSource
 profile:Document<ProviderConfiguration>;maxCalls:number;grants:{id:string;calls:number;at:string}[];entries:CharacterIndexEntry[];completedCount:number
 status:'queued'|'running'|'paused'|'completed'|'cancelled';authorized:boolean;historical:boolean
 activeScanId:string|null;ownerId:string|null;leaseUntil:string|null;error:string|null;createdAt:string;updatedAt:string
}
export interface CharacterIndexBinding {schema:'novel-character-index-v1';id:string;step:number}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const str=(v:unknown,max=160):v is string=>typeof v==='string'&&v.length>0&&v.length<=max
const integer=(v:unknown,min=0):v is number=>Number.isSafeInteger(v)&&Number(v)>=min
function fail(message:string):never{throw new ContractError('CHARACTER_INDEX',message+'；原文与已收到的结果保留')}
export function validateIndexBinding(v:unknown):asserts v is CharacterIndexBinding{if(!record(v)||v.schema!=='novel-character-index-v1'||!str(v.id)||!v.id.startsWith('character-index:')||!integer(v.step)||v.step>=50)fail('人物整理归属无效')}
export function validateCharacterIndex(v:unknown,id?:string):asserts v is CharacterIndex{
 if(!record(v)||v.assetType!=='novel-character-index'||v.schema!=='novel-character-index-v1'||id!==undefined&&!id.startsWith('character-index:')||!record(v.source)||!record(v.profile)||!integer(v.maxCalls,1)||Number(v.maxCalls)>50||!Array.isArray(v.entries)||v.entries.length>Number(v.maxCalls)||!integer(v.completedCount)||Number(v.completedCount)>v.entries.length||v.entries.length-Number(v.completedCount)>1||!['queued','running','paused','completed','cancelled'].includes(String(v.status))||typeof v.authorized!=='boolean'||typeof v.historical!=='boolean'||!str(v.createdAt,60)||!str(v.updatedAt,60)||v.error!==null&&!str(v.error,2000))fail('人物整理计划无效')
 if(!Array.isArray(v.grants)||!v.grants.length||v.grants.length>50||v.grants.some(g=>!record(g)||!str(g.id)||!integer(g.calls,1)||Number(g.calls)>50||!str(g.at,60)||!Number.isFinite(Date.parse(g.at)))||new Set(v.grants.map(g=>(g as {id:string}).id)).size!==v.grants.length||v.grants.reduce((n,g)=>n+Number((g as {calls:number}).calls),0)!==v.maxCalls)fail('人物整理调用授权不完整')
 const s=v.source,a=s.anchor;if(!str(s.storyId)||!integer(s.storyRevision,1)||s.branchId!==null&&!str(s.branchId)||(s.branchId===null?s.branchRevision!==null:!integer(s.branchRevision,1))||!record(a)||a.storyId!==s.storyId||a.branchId!==s.branchId||!str(a.paragraphId)||!integer(a.offset))fail('人物整理终点无效')
 const p=v.profile;if(p.kind!=='settings'||!str(p.id)||!p.id.startsWith('model:')||!integer(p.revision,1)||!record(p.value)||p.value.assetType!==undefined||p.value.id!==p.id||!str(p.updatedAt,60))fail('人物整理模型版本无效');validateLocalValue('settings',p.value,p.id)
 const entries=v.entries as unknown as CharacterIndexEntry[];for(const e of entries){if(!record(e)||!str(e.scanId)||!e.scanId.startsWith('character-scan:')||!Array.isArray(e.coverage)||!e.coverage.length||e.coverage.length>16||!Array.isArray(e.bindings)||e.bindings.length>16||e.bindings.some(b=>!record(b)||!str(b.id)||!integer(b.revision,1))||new Set(e.bindings.map(b=>b.id)).size!==e.bindings.length)fail('人物整理分段记录无效');for(const c of e.coverage)if(!record(c)||!str(c.id)||!integer(c.revision,1)||!integer(c.start)||!integer(c.limit,1)||Number(c.start)>=c.limit||Object.keys(c).some(k=>!['id','revision','start','limit'].includes(k)))fail('人物整理覆盖记录无效');if(e.coverage.reduce((n,c)=>n+c.limit-c.start,0)>48000)fail('人物整理分段超过上限');if(e.next!==null&&(!record(e.next)||!str(e.next.paragraphId)||!integer(e.next.offset)))fail('人物整理游标无效')}
 if(new Set(entries.map(e=>e.scanId)).size!==entries.length)fail('人物整理分段重复')
 for(const field of ['activeScanId','ownerId'])if(v[field]!==null&&!str(v[field]))fail('人物整理执行身份无效')
 if(v.leaseUntil!==null&&(!str(v.leaseUntil,60)||!Number.isFinite(Date.parse(v.leaseUntil))))fail('人物整理执行期限无效')
 if(v.activeScanId!==null&&v.activeScanId!==entries[Number(v.completedCount)]?.scanId||v.status==='running'&&(!v.authorized||v.historical||!v.ownerId||!v.leaseUntil)||v.status!=='running'&&(v.ownerId!==null||v.leaseUntil!==null)||v.status==='queued'&&(entries.length||v.activeScanId!==null)||v.status==='completed'&&(v.completedCount!==entries.length||!entries.length||entries.at(-1)!.next!==null||v.activeScanId!==null)||v.status==='cancelled'&&(v.authorized!==false||v.activeScanId!==null)||v.historical&&v.authorized)fail('人物整理状态与执行权不一致')
 if(new TextEncoder().encode(canonical(v)).byteLength>4_000_000)fail('人物整理计划超过上限')
}
export function indexReferences(v:CharacterIndex):{id:string;kind:DomainKind;revision?:number}[]{return [{id:v.source.storyId,kind:'story',revision:v.source.storyRevision},...(v.source.branchId?[{id:v.source.branchId,kind:'branch' as const,revision:v.source.branchRevision!}]:[]),{id:v.source.anchor.paragraphId,kind:'paragraph'},{id:v.profile.id,kind:'settings',revision:v.profile.revision},...v.entries.flatMap(e=>[{id:e.scanId,kind:'settings' as const},...e.coverage.map(c=>({id:c.id,kind:'paragraph' as const,revision:c.revision})),...e.bindings.map(b=>({id:b.id,kind:'settings' as const,revision:b.revision}))])]}
export function validateIndexRelations(id:string,v:CharacterIndex,lookup:CharacterLookup){
 const s=v.source,story=lookup(s.storyId,s.storyRevision),branch=s.branchId?lookup(s.branchId,s.branchRevision!):null,line=(branch||story)?.value as LocalStory|undefined,profile=lookup(v.profile.id,v.profile.revision)
 if(story?.kind!=='story'||story.revision!==s.storyRevision||s.branchId&&(branch?.kind!=='branch'||branch.revision!==s.branchRevision||(branch.value as {storyId:string}).storyId!==s.storyId)||!line||profile?.kind!=='settings'||profile.revision!==v.profile.revision||canonical(profile.value)!==canonical(v.profile.value))fail('人物整理来源或模型版本缺失')
 const ids=line.paragraphIds,lastIndex=ids.indexOf(s.anchor.paragraphId),last=lookup(s.anchor.paragraphId)?.value as Paragraph|undefined
 if(lastIndex<0||!last||s.anchor.offset>last.text.length||unicodeOffset(last.text,s.anchor.offset)!==s.anchor.offset)fail('人物整理终点越过原文')
 let position=0,offset=0;const normalize=()=>{while(position<=lastIndex){const p=lookup(ids[position]!)?.value as Paragraph|undefined;if(!p)fail('人物整理原段落缺失');if(offset===(position===lastIndex?s.anchor.offset:p!.text.length)){position++;offset=0}else break}},cursor=()=>{normalize();return position>lastIndex?null:{paragraphId:ids[position]!,offset}}
 for(const [step,e] of v.entries.entries()){
  const scan=lookup(e.scanId),value=scan?.value as CharacterScan|undefined,run=value?lookup(value.runId)?.value as SkillRun|undefined:undefined
  if(scan?.kind!=='settings'||value?.assetType!=='novel-character-scan'||value.index?.id!==id||value.index.step!==step||canonical({storyId:value.scope.storyId,storyRevision:value.scope.storyRevision,branchId:value.scope.branchId,branchRevision:value.scope.branchRevision,anchor:value.scope.anchor})!==canonical(s)||!run||canonical(run.configSnapshot?.text)!==canonical(v.profile)||step<v.completedCount&&!value.applied)fail('人物整理分段归属或完成凭据无效')
  const expected=[...e.coverage];for(const binding of e.bindings){const c=lookup(binding.id,binding.revision);if(c?.kind!=='settings'||c.revision!==binding.revision||(c.value as NovelCharacter).assetType!=='novel-character')fail('人物身份凭据缺失');const person=c!.value as NovelCharacter,p=lookup(person.primary.id,person.primary.revision)?.value as Paragraph|undefined;if(person.scope.storyId!==s.storyId||person.scope.branchId!==s.branchId||!p)fail('人物身份来自另一故事线');expected.push({id:person.primary.id,revision:person.primary.revision,start:person.primary.offset,limit:person.primary.offset+person.primary.quote.length})}
  const ranges=expected.sort((a,b)=>ids.indexOf(a.id)-ids.indexOf(b.id)||(a.start!-b.start!)),merged:{id:string;revision:number;start:number;limit:number}[]=[];for(const r of ranges){const tail=merged.at(-1);if(tail?.id===r.id&&r.start!<=tail.limit){if(tail.revision!==r.revision)fail('人物身份版本不一致');tail.limit=Math.max(tail.limit,r.limit)}else merged.push({id:r.id,revision:r.revision,start:r.start!,limit:r.limit})}
  if(canonical(merged)!==canonical(value!.scope.sources.map(c=>({id:c.id,revision:c.revision,start:c.start,limit:c.limit}))))fail('人物整理读了覆盖与身份凭据之外的正文')
  for(const c of e.coverage){normalize();const row=lookup(c.id,c.revision),p=row?.value as Paragraph|undefined,limit=position===lastIndex?s.anchor.offset:p?.text.length;if(position>lastIndex||c.id!==ids[position]||c.start!==offset||row?.kind!=='paragraph'||row.revision!==c.revision||!p||c.limit>limit!||unicodeOffset(p.text,c.start!)!==c.start||unicodeOffset(p.text,c.limit)!==c.limit)fail('人物整理覆盖有缺口、重复或越界');offset=c.limit}
  if(canonical(e.next)!==canonical(cursor()))fail('人物整理下一游标与实际覆盖不符')
 }
}
function validateIndexGrowth(before:CharacterIndex,after:CharacterIndex,historical=false){
 for(const k of ['schema','source','profile','historical','createdAt'] as const)if(canonical(before[k])!==canonical(after[k]))fail('人物整理冻结合同不能覆盖')
 if(after.grants.length<before.grants.length||after.grants.length>before.grants.length+1||canonical(after.grants.slice(0,before.grants.length))!==canonical(before.grants)||after.grants.length>before.grants.length&&(before.status!=='paused'||before.activeScanId!==null||before.completedCount!==before.entries.length||!historical&&(before.historical||!before.authorized)))fail('新调用上限需要明确的追加授权，原上限记录保留')
 if(after.completedCount<before.completedCount||after.entries.length<before.entries.length||canonical(after.entries.slice(0,before.entries.length))!==canonical(before.entries)||before.authorized===false&&after.authorized!==false)fail('人物整理记录与授权不能倒退')
 if(after.entries.length>before.entries.length+1||after.completedCount>before.completedCount+1||after.entries.length>before.entries.length&&before.entries.length!==before.completedCount||['completed','cancelled'].includes(before.status)&&(['entries','grants','completedCount','maxCalls','activeScanId'] as const).some(k=>canonical(before[k])!==canonical(after[k])))fail('人物整理历史不能跳过分段或改写已结束记录')
}
export function validateIndexUpdate(before:CharacterIndex,after:CharacterIndex){
 validateIndexGrowth(before,after)
 const allowed:Record<CharacterIndex['status'],CharacterIndex['status'][]>= {queued:['queued','running','cancelled'],running:['running','paused','completed','cancelled'],paused:['paused','running','cancelled'],completed:['completed'],cancelled:['cancelled']};if(!allowed[before.status].includes(after.status))fail('人物整理状态不能倒退或重发')
}
/** Export maps queued/running to inert paused. That mapping cannot erase append-only history. */
export function validateIndexHistoryUpdate(before:CharacterIndex,after:CharacterIndex){
 if(!before.historical&&!after.historical){validateIndexUpdate(before,after);return}
 if(!before.historical||!after.historical||before.authorized||after.authorized||before.ownerId!==null||after.ownerId!==null||before.leaseUntil!==null||after.leaseUntil!==null)fail('迁移人物计划不能带执行权')
 validateIndexGrowth(before,after,true)
 const allowed:Record<CharacterIndex['status'],CharacterIndex['status'][]>= {queued:[],running:[],paused:['paused','completed','cancelled'],completed:['completed'],cancelled:['cancelled']};if(!allowed[before.status].includes(after.status))fail('迁移人物历史不能倒退')
}
