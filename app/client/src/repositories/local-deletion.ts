import {canonical,sha256,ContractError,type Document,type DomainKind} from '../domain/local-contract'
import {localReferences} from '../domain/local-validation'
import type {VaultSnapshot} from './local-vault'

export interface DeletionItem {id:string;kind:DomainKind;revision:number;label:string;versions:number}
export interface BranchDeletionPlan {
  vaultId:string;branchId:string;storyId:string;name:string;fingerprint:string;state:string
  items:DeletionItem[];blockers:DeletionItem[];activeTasks:DeletionItem[]
  counts:Partial<Record<DomainKind,number>>;historyVersions:number
}
export interface DeletionReceipt {
  operationId:string;payloadHash:string;branchId:string;committedAt:string
  documentIds:string[];counts:Partial<Record<DomainKind,number>>;historyVersions:number
}
export interface DeletedDocument {id:string;kind:DomainKind;revision:number;deletedAt:string}
export interface StoryDeletionPlan {
  vaultId:string;storyIds:string[];names:string[];fingerprint:string;state:string
  items:DeletionItem[];blockers:DeletionItem[];activeTasks:DeletionItem[]
  counts:Partial<Record<DomainKind,number>>;historyVersions:number;sourceBytes:number
}
export interface StoryDeletionReceipt {
  type:'story-batch';operationId:string;payloadHash:string;storyIds:string[];committedAt:string
  documentIds:string[];counts:Partial<Record<DomainKind,number>>;historyVersions:number
}
export type ErasureReceipt=DeletionReceipt|StoryDeletionReceipt

/** Revision identities are immutable; compare every head/history identity in the
 * final transaction so a newly added dependent cannot race a deletion preview. */
export function deletionState(heads:Pick<Document,'id'|'kind'|'revision'|'updatedAt'>[],revisions:Pick<Document,'id'|'kind'|'revision'|'updatedAt'>[]):string {
  const rows=(values:typeof heads)=>values.map(({id,kind,revision,updatedAt})=>({id,kind,revision,updatedAt})).sort((a,b)=>a.id.localeCompare(b.id)||a.revision-b.revision)
  return canonical({heads:rows(heads),revisions:rows(revisions)})
}
function label(row:Document):string {
  const v=row.value as Record<string,unknown>
  const names:Record<string,string>={'reading-event':'阅读记录','writing-run':'写作记录','writing-edit':'修订候选','stage-state':'剧场进度','main-reading-line':'主要阅读线','novel-character':'人物证据','novel-character-scan':'人物整理','agent-session':'助手会话','skill-run':'技能记录'}
  return String(v.name||v.title||names[String(v.assetType)]||({note:'笔记',progress:'阅读位置',paragraph:'正文段落',asset:'素材',session:'角色对话',card:'角色卡',agentRun:'助手记录',changeSet:'变更记录'} as Record<string,string>)[row.kind]||'关联记录').slice(0,200)
}
const taskActive=(row:Document)=>{
  const v=row.value as Record<string,unknown>
  return v.historical!==true&&(row.kind==='task'||row.kind==='mediaJob'||row.kind==='agentRun'||row.kind==='settings'&&['writing-run','skill-run','novel-character-index','embedding-record','video-record'].includes(String(v.assetType)))&&['queued','running','calling','planning','streaming','submitting','querying','prepared','unknown','persistence_failed'].includes(String(v.status))
}
const removable=(row:Document)=>row.kind==='note'||row.kind==='progress'||row.kind==='paragraph'||row.kind==='settings'&&['reading-event','original-story-draft','writing-edit','writing-run','stage-state','main-reading-line','writing-directions','writing-compose-draft','writing-compose-view'].includes(String((row.value as {assetType?:string}).assetType))
function references(row:Document,heads:Map<string,Document>){
  const refs=localReferences(row.kind,row.value),v=row.value as Record<string,unknown>
  // These input selections are polymorphic, so their kinds come from the vault
  // rather than a fixed DomainKind. They must still fence deletion.
  if(row.kind==='settings')for(const id of [...(v.assetType==='agent-session'&&Array.isArray(v.referenceIds)?v.referenceIds:[]),...(v.assetType==='skill-draft'&&Array.isArray(v.selected)?v.selected:[])]){const target=heads.get(String(id));if(target)refs.push({id:target.id,kind:target.kind})}
  return refs
}

/** All historical references count, including a card or child that has since
 * changed its current head. Independent cards, conversations and assets block
 * deletion rather than being silently cascaded. */
export async function planBranchDeletion(snapshot:VaultSnapshot,branchId:string):Promise<BranchDeletionPlan> {
  const heads=new Map(snapshot.heads.map(row=>[row.id,row])),branch=heads.get(branchId)
  if(!branch||branch.kind!=='branch')throw new ContractError('BRANCH_NOT_FOUND','这条故事线不存在，原文保留')
  const history=new Map<string,Document[]>(),reverse=new Map<string,Set<string>>()
  for(const row of snapshot.revisions){const group=history.get(row.id)||[];group.push(row);history.set(row.id,group);for(const ref of references(row,heads)){const dependants=reverse.get(ref.id)||new Set<string>();dependants.add(row.id);reverse.set(ref.id,dependants)}}
  const selected=new Set([branchId]),blocked=new Set<string>(),active=new Set<string>(),candidates=new Set<string>()
  const queue=[branchId]
  // Only reclaim paragraphs in the deleted branch's historical body. Shared
  // original text or a paragraph referenced elsewhere remains physically intact.
  for(const row of history.get(branchId)||[])for(const id of (row.value as {paragraphIds:string[]}).paragraphIds)candidates.add(id)
  while(queue.length){const id=queue.shift()!;for(const dependantId of reverse.get(id)||[]){if(selected.has(dependantId))continue;const row=heads.get(dependantId);if(!row)throw new ContractError('VAULT_INTEGRITY_INVALID','关联记录缺少当前版本，未继续删除');if(taskActive(row)){active.add(row.id);continue}if(!removable(row)){blocked.add(row.id);continue}selected.add(row.id);queue.push(row.id)}}
  // A queued/cancelled run can own a copied character prefix before accepting
  // any chapter. It is not yet present in the branch manifest. Reclaim only
  // those explicit family-owned candidates and keep every surviving owner.
  for(const id of selected)for(const row of history.get(id)||[]){const v=row.value as {assetType?:string;prefixParagraphId?:string|null;prefixChapters?:{paragraphIds:string[]}[];chapters?:{paragraphIds:string[]}[]};if(row.kind==='settings'&&v.assetType==='writing-run'){if(v.prefixParagraphId)candidates.add(v.prefixParagraphId);for(const chapter of [...(v.prefixChapters||[]),...(v.chapters||[])])for(const paragraphId of chapter.paragraphIds)candidates.add(paragraphId)}}
  // Paragraphs may form prefix chains. A fixed point removes only a chain with
  // no surviving owner; surviving history is never truncated to permit deletion.
  let changed=true
  while(changed){changed=false;for(const id of candidates){if(selected.has(id))continue;const row=heads.get(id);if(row?.kind==='paragraph'&&[...(reverse.get(id)||[])].every(owner=>selected.has(owner))){selected.add(id);changed=true}}}
  // Re-evaluate all surviving references after orphan collection. This also
  // catches unknown future setting types conservatively.
  for(const id of selected)for(const owner of reverse.get(id)||[])if(!selected.has(owner)){if(taskActive(heads.get(owner)!))active.add(owner);else blocked.add(owner)}
  const item=(id:string):DeletionItem=>{const row=heads.get(id)!;return {id,kind:row.kind,revision:row.revision,label:label(row),versions:history.get(id)?.length||0}}
  const items=[...selected].sort().map(item),blockers=[...blocked].sort().map(item),activeTasks=[...active].sort().map(item),counts:BranchDeletionPlan['counts']={}
  for(const row of items)counts[row.kind]=(counts[row.kind]||0)+1
  const state=deletionState(snapshot.heads,snapshot.revisions),fingerprint=await sha256(canonical({vaultId:snapshot.vaultId,branchId,state,ids:items.map(row=>row.id)}))
  return {vaultId:snapshot.vaultId,branchId,storyId:(branch.value as {storyId:string}).storyId,name:label(branch),fingerprint,state,items,blockers,activeTasks,counts,historyVersions:items.reduce((sum,row)=>sum+row.versions,0)}
}

/** A selected book owns all of its branches, but never an independent world,
 * card, conversation or asset. Shared source/paragraph bytes remain intact. */
export async function planStoryDeletion(snapshot:VaultSnapshot,storyIds:string[]):Promise<StoryDeletionPlan>{
  if(!storyIds.length||storyIds.length>1000||new Set(storyIds).size!==storyIds.length||storyIds.some(id=>typeof id!=='string'||!id||id.length>160))throw new ContractError('DELETION_SELECTION','请选择 1—1000 本书')
  const ids=[...storyIds].sort(),heads=new Map(snapshot.heads.map(row=>[row.id,row])),books=ids.map(id=>heads.get(id))
  if(books.some(row=>!row||row.kind!=='story'))throw new ContractError('STORY_NOT_FOUND','所选书籍已变化，请重新检查')
  const history=new Map<string,Document[]>(),reverse=new Map<string,Set<string>>()
  for(const row of snapshot.revisions){const rows=history.get(row.id)||[];rows.push(row);history.set(row.id,rows);for(const ref of references(row,heads)){const owners=reverse.get(ref.id)||new Set<string>();owners.add(row.id);reverse.set(ref.id,owners)}}
  const selected=new Set(ids),blocked=new Set<string>(),active=new Set<string>(),candidates=new Set<string>(),queue=[...ids]
  const owned=(row:Document)=>removable(row)||row.kind==='branch'&&ids.includes((row.value as {storyId:string}).storyId)||row.kind==='settings'&&((row.value as {assetType?:string;storyId?:string}).assetType==='import-entry'&&ids.includes((row.value as {storyId:string}).storyId))
  while(queue.length){const id=queue.shift()!;for(const owner of reverse.get(id)||[]){if(selected.has(owner))continue;const row=heads.get(owner);if(!row)throw new ContractError('VAULT_INTEGRITY_INVALID','关联记录缺少当前版本，未继续删除');if(taskActive(row)){active.add(owner);continue}if(!owned(row)){blocked.add(owner);continue}selected.add(owner);queue.push(owner)}}
  for(const id of selected)for(const row of history.get(id)||[]){const v=row.value as {sourceId?:string;paragraphIds?:string[];assetType?:string;prefixParagraphId?:string;prefixChapters?:{paragraphIds:string[]}[];chapters?:{paragraphIds:string[]}[]};if(row.kind==='story'||row.kind==='branch'){for(const p of v.paragraphIds||[])candidates.add(p);if(row.kind==='story'&&v.sourceId)candidates.add(v.sourceId)}if(row.kind==='settings'&&v.assetType==='writing-run'){if(v.prefixParagraphId)candidates.add(v.prefixParagraphId);for(const c of [...v.prefixChapters||[],...v.chapters||[]])for(const p of c.paragraphIds)candidates.add(p)}}
  // Raw sources and their chunks are reclaimable only when every surviving
  // historical owner is already selected. Future pending imports keep them.
  for(const id of [...candidates])for(const row of history.get(id)||[])if(row.kind==='source')for(const chunk of (row.value as {chunkIds:string[]}).chunkIds)candidates.add(chunk)
  let changed=true
  while(changed){changed=false;for(const id of candidates){if(selected.has(id))continue;const row=heads.get(id);if(row&&['paragraph','source','sourceChunk'].includes(row.kind)&&[...reverse.get(id)||[]].every(owner=>selected.has(owner))){selected.add(id);changed=true}}}
  for(const id of selected)for(const owner of reverse.get(id)||[])if(!selected.has(owner)){const row=heads.get(owner);if(!row)throw new ContractError('VAULT_INTEGRITY_INVALID','关联记录缺少当前版本');if(taskActive(row))active.add(owner);else blocked.add(owner)}
  const item=(id:string):DeletionItem=>{const row=heads.get(id)!;return {id,kind:row.kind,revision:row.revision,label:label(row),versions:history.get(id)?.length||0}},items=[...selected].sort().map(item),counts:StoryDeletionPlan['counts']={}
  for(const row of items)counts[row.kind]=(counts[row.kind]||0)+1
  const state=deletionState(snapshot.heads,snapshot.revisions),fingerprint=await sha256(canonical({type:'story-batch',vaultId:snapshot.vaultId,storyIds:ids,state,ids:items.map(row=>row.id)}))
  return {vaultId:snapshot.vaultId,storyIds:ids,names:books.map(row=>String((row!.value as {title:string}).title)),fingerprint,state,items,blockers:[...blocked].sort().map(item),activeTasks:[...active].sort().map(item),counts,historyVersions:items.reduce((sum,row)=>sum+row.versions,0),sourceBytes:items.filter(row=>row.kind==='source').reduce((sum,row)=>sum+Number((heads.get(row.id)!.value as {bytes:number}).bytes),0)}
}

export function validateDeletedDocuments(values:unknown,existingIds:Set<string>):asserts values is DeletedDocument[] {
  if(!Array.isArray(values)||values.length>3_100_000)throw new ContractError('BACKUP_DELETION_INVALID','删除索引格式无效')
  const seen=new Set<string>(),kinds=new Set<DomainKind>(['story','source','sourceChunk','branch','paragraph','progress','note','card','persona','session','message','lorebook','memory','world','checkpoint','companion','relationship','asset','mediaJob','agentRun','changeSet','plugin','settings','task'])
  for(const value of values){const row=value as DeletedDocument;if(!row||typeof row!=='object'||Object.keys(row).sort().join(',')!=='deletedAt,id,kind,revision'||typeof row.id!=='string'||!row.id||row.id.length>160||!kinds.has(row.kind)||!Number.isSafeInteger(row.revision)||row.revision<1||typeof row.deletedAt!=='string'||!Number.isFinite(Date.parse(row.deletedAt))||seen.has(row.id)||existingIds.has(row.id))throw new ContractError('BACKUP_DELETION_INVALID','删除索引与当前内容冲突，未创建私人库');seen.add(row.id)}
}
