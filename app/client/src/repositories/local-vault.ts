import {playableReferences} from '../playable/contract'
import {novelReferences} from '../novel-characters/relations'
import {promotionReferences} from '../companion/promotion-contract'
import {openDB,type DBSchema,type IDBPDatabase} from 'idb'
import {canonical,sha256,ContractError,LOCAL_PROTOCOL,type Document,type DomainKind,type MutationReceipt} from '../domain/local-contract'
import {validateUpdate} from '../domain/mutation-policy'
import {editReferences} from '../writing/edits-relations'
import {planningReferences} from '../writing/planning-contract'
import {activityReferences} from '../reader/activity-contract'
import {verifyVaultSnapshot} from './local-backup-validation'
import {localReferences,validateLocalRelations,validateLocalValue,validateSourceBytes} from '../domain/local-validation'
import {acceptedBasisReferences} from '../writing/accepted-basis'
import {planBranchDeletion,planStoryDeletion,deletionState,validateDeletedDocuments,type BranchDeletionPlan,type DeletionReceipt,type StoryDeletionPlan,type StoryDeletionReceipt,type ErasureReceipt,type DeletedDocument} from './local-deletion'

type Sealed={iv:Uint8Array;bytes:ArrayBuffer}
export type VaultInfo={id:string;createdAt:string;openedAt?:string;salt:Uint8Array;iterations:number;verifier:Sealed}
type Stored={key:string;vaultId:string;id:string;kind:DomainKind;revision:number;updatedAt:string;sealed:Sealed}
type Operation={key:string;vaultId:string;operationId:string;payloadHash:string;sealed:Sealed}
export type WriteDocument={kind:DomainKind;id:string;expectedRevision:number;value:unknown}
export interface VaultSnapshot {protocol:typeof LOCAL_PROTOCOL;vaultId:string;createdAt:string;heads:Document[];revisions:Document[];receipts:MutationReceipt[][];deleted?:DeletedDocument[]}
interface VaultDB extends DBSchema {
  vaults:{key:string;value:VaultInfo}
  heads:{key:string;value:Stored;indexes:{vault:string;'vault-kind':[string,DomainKind]}}
  revisions:{key:string;value:Stored;indexes:{vault:string}}
  operations:{key:string;value:Operation;indexes:{vault:string}}
  deleted:{key:string;value:Stored;indexes:{vault:string}}
  erasures:{key:string;value:Operation;indexes:{vault:string}}
}
const DATABASE='reader-device-private-v1'
const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true})
const stamp=()=>new Date().toISOString()
async function database():Promise<IDBPDatabase<VaultDB>> {
  return openDB<VaultDB>(DATABASE,3,{upgrade(db,oldVersion,_newVersion,tx){
    if(oldVersion===0){db.createObjectStore('vaults',{keyPath:'id'});for(const name of ['heads','revisions','operations'] as const){const store=db.createObjectStore(name,{keyPath:'key'});store.createIndex('vault','vaultId')}}
    if(oldVersion<2)tx.objectStore('heads').createIndex('vault-kind',['vaultId','kind'])
    if(oldVersion<3)for(const name of ['deleted','erasures'] as const){const store=db.createObjectStore(name,{keyPath:'key'});store.createIndex('vault','vaultId')}
  },blocked(){window.dispatchEvent(new Event('reader-vault-upgrade-blocked'))}})
}
async function derive(password:string,salt:Uint8Array,iterations:number):Promise<CryptoKey>{
  const secret=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveKey'])
  return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:Uint8Array.from(salt),iterations},secret,{name:'AES-GCM',length:256},false,['encrypt','decrypt'])
}
async function seal(key:CryptoKey,value:unknown,aad:string):Promise<Sealed>{
  const iv=crypto.getRandomValues(new Uint8Array(12))
  return {iv,bytes:await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(aad)},key,encoder.encode(canonical(value)))}
}
async function unseal<T>(key:CryptoKey,value:Sealed,aad:string):Promise<T>{
  const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:Uint8Array.from(value.iv),additionalData:encoder.encode(aad)},key,value.bytes)
  return JSON.parse(decoder.decode(plain)) as T
}
const keyOf=(vaultId:string,id:string)=>vaultId+':'+id
const aadOf=(row:Pick<Stored,'vaultId'|'kind'|'id'|'revision'>)=>canonical({protocol:LOCAL_PROTOCOL,vaultId:row.vaultId,kind:row.kind,id:row.id,revision:row.revision})
const operationAad=(row:Pick<Operation,'vaultId'|'operationId'|'payloadHash'>)=>canonical({protocol:LOCAL_PROTOCOL,vaultId:row.vaultId,operationId:row.operationId,payloadHash:row.payloadHash})
// A derived per-book reading preference can be explicitly chosen again. It
// contains no prose or candidate identity; its new references must still be
// live. Every content/candidate ID remains permanently fenced.
const reusablePreference=(row:WriteDocument)=>row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='main-reading-line'&&row.id==='settings:main-reading-line:'+(row.value as {storyId:string}).storyId
// Only opaque IDs and kinds leave the encrypted repository; never document values.
const changes=typeof window!=='undefined'&&typeof BroadcastChannel!=='undefined'?new BroadcastChannel('reader-private-commits-v1'):null
;(changes as (BroadcastChannel&{unref?:()=>void})|null)?.unref?.()
const notifyCommit=(detail:{vaultId:string;kinds:DomainKind[];ids:string[]})=>{if(typeof window!=='undefined')window.dispatchEvent(new CustomEvent('reader-private-commit',{detail}))}
changes?.addEventListener('message',event=>{const m=event.data;if(m&&typeof m.vaultId==='string'&&Array.isArray(m.kinds)&&m.kinds.length<=30&&m.kinds.every((v:unknown)=>typeof v==='string')&&Array.isArray(m.ids)&&m.ids.length<=200&&m.ids.every((v:unknown)=>typeof v==='string'&&v.length<=160))notifyCommit(m)})

export async function listVaults():Promise<Pick<VaultInfo,'id'|'createdAt'>[]>{
  const db=await database();try{return (await db.getAll('vaults')).sort((a,b)=>(b.openedAt||b.createdAt).localeCompare(a.openedAt||a.createdAt)).map(({id,createdAt})=>({id,createdAt}))}finally{db.close()}
}
export async function createVault(password:string):Promise<LocalVault>{
  if(password.length<8||password.length>1024)throw new ContractError('VAULT_PASSWORD_INVALID','请设置至少 8 个字符的解锁口令')
  const id=crypto.randomUUID(),salt=crypto.getRandomValues(new Uint8Array(16)),iterations=310000,key=await derive(password,salt,iterations)
  const info:VaultInfo={id,salt,iterations,createdAt:stamp(),verifier:await seal(key,{protocol:LOCAL_PROTOCOL,purpose:'unlock-verifier'},'vault:'+id)}
  const db=await database();try{await db.add('vaults',info)}finally{db.close()}
  return new LocalVault(info.id,key)
}
export async function unlockVault(id:string,password:string):Promise<LocalVault>{
  const db=await database();let info:VaultInfo|undefined;try{info=await db.get('vaults',id)}finally{db.close()}
  if(!info)throw new ContractError('VAULT_NOT_FOUND','此设备找不到这份私人库')
  if(info.iterations!==310000||info.salt.length!==16)throw new ContractError('VAULT_FORMAT_INVALID','私人库格式无效，原数据保留')
  try{
    const key=await derive(password,info.salt,info.iterations)
    const verified=await unseal<{protocol:string;purpose:string}>(key,info.verifier,'vault:'+id)
    if(verified.protocol!==LOCAL_PROTOCOL||verified.purpose!=='unlock-verifier')throw new Error('invalid verifier')
    const db=await database();try{await db.put('vaults',{...info,openedAt:stamp()})}finally{db.close()}
    return new LocalVault(id,key)
  }catch{throw new ContractError('VAULT_UNLOCK_FAILED','口令不正确，或私人库已损坏；原数据保留')}
}

/** A restored namespace becomes visible in the same transaction as every head. */
export async function installVaultSnapshot(password:string,snapshot:VaultSnapshot,signal:AbortSignal):Promise<LocalVault>{
  await verifyVaultSnapshot(snapshot,signal)
  if(password.length<8||password.length>1024)throw new ContractError('VAULT_PASSWORD_INVALID','请设置至少 8 个字符的解锁口令')
  const id=crypto.randomUUID(),salt=crypto.getRandomValues(new Uint8Array(16)),iterations=310000,key=await derive(password,salt,iterations),info:VaultInfo={id,salt,iterations,createdAt:stamp(),verifier:await seal(key,{protocol:LOCAL_PROTOCOL,purpose:'unlock-verifier'},'vault:'+id)}
  signal.throwIfAborted();const rows:Stored[]=[],headRows:Stored[]=[],byRevision=new Map<string,Stored>()
  for(let start=0;start<snapshot.revisions.length;start+=64){const sealedRows=await Promise.all(snapshot.revisions.slice(start,start+64).map(async document=>{validateLocalValue(document.kind,document.value,document.id);const row:Stored={key:keyOf(id,document.id)+':'+document.revision,vaultId:id,id:document.id,kind:document.kind,revision:document.revision,updatedAt:document.updatedAt,sealed:await seal(key,document.value,aadOf({vaultId:id,kind:document.kind,id:document.id,revision:document.revision}))};return row}));for(const row of sealedRows){if(byRevision.has(row.key))throw new ContractError('BACKUP_REVISION_INVALID','备份含重复版本');rows.push(row);byRevision.set(row.key,row)}signal.throwIfAborted()}
  for(const document of snapshot.heads){const row=byRevision.get(keyOf(id,document.id)+':'+document.revision);if(!row||row.kind!==document.kind)throw new ContractError('BACKUP_REVISION_INVALID','备份缺少当前版本');headRows.push({...row,key:keyOf(id,row.id)})}
  const deletedRows=await Promise.all((snapshot.deleted||[]).map(async document=>{const row:Stored={key:keyOf(id,document.id),vaultId:id,id:document.id,kind:document.kind,revision:document.revision,updatedAt:document.deletedAt,sealed:await seal(key,document,aadOf({vaultId:id,kind:document.kind,id:document.id,revision:document.revision}))};return row}))
  const db=await database(),tx=db.transaction(['vaults','heads','revisions','deleted'],'readwrite'),abort=()=>{try{tx.abort()}catch{}}
  signal.addEventListener('abort',abort,{once:true})
  try{signal.throwIfAborted();await tx.objectStore('vaults').add(info);for(const row of rows){signal.throwIfAborted();await tx.objectStore('revisions').add(row)}for(const row of headRows){signal.throwIfAborted();await tx.objectStore('heads').add(row)}for(const row of deletedRows){signal.throwIfAborted();await tx.objectStore('deleted').add(row)}await tx.done;return new LocalVault(id,key)}
  catch(error){abort();await tx.done.catch(()=>{});throw error}finally{signal.removeEventListener('abort',abort);db.close()}
}

/** Every write has one atomic revision/receipt commit. No network access. */
export class LocalVault {
  private key:CryptoKey|null
  private epoch=0
  private writes=new Set<{abort():void}>()
  constructor(readonly id:string,key:CryptoKey){this.key=key}
  lock(){this.key=null;this.epoch++;for(const tx of this.writes){try{tx.abort()}catch{/* A committed receipt stays committed. */}}this.writes.clear()}
  get unlocked(){return this.key!==null}
  async snapshot():Promise<VaultSnapshot>{
    const scope=this.capture(),db=await database()
    try{
      const tx=db.transaction(['heads','revisions','operations','deleted'],'readonly'),[heads,revisions,operations,tombstones]=await Promise.all([tx.objectStore('heads').index('vault').getAll(this.id),tx.objectStore('revisions').index('vault').getAll(this.id),tx.objectStore('operations').index('vault').getAll(this.id),tx.objectStore('deleted').index('vault').getAll(this.id)]);await tx.done
      const decodeRows=async(rows:Stored[],history:boolean)=>{const documents:Document[]=[];for(let start=0;start<rows.length;start+=64){documents.push(...await Promise.all(rows.slice(start,start+64).map(async row=>{this.identity(row,row.id,history?row.revision:undefined);return {id:row.id,kind:row.kind,revision:row.revision,updatedAt:row.updatedAt,value:await this.decode(scope,row)}})));this.active(scope.epoch)}return documents}
      const receipts:MutationReceipt[][]=[];for(const operation of operations){if(operation.vaultId!==this.id||operation.key!==keyOf(this.id,operation.operationId))throw new ContractError('VAULT_INTEGRITY_INVALID','回执索引校验失败');const value=await unseal<MutationReceipt[]>(scope.key,operation.sealed,operationAad(operation));if(!Array.isArray(value)||value.some(receipt=>receipt.operationId!==operation.operationId||receipt.payloadHash!==operation.payloadHash))throw new ContractError('VAULT_INTEGRITY_INVALID','回执内容校验失败');receipts.push(value);this.active(scope.epoch)}
      const decodedHeads=await decodeRows(heads,false),decodedRevisions=await decodeRows(revisions,true)
      this.active(scope.epoch)
      const deleted:DeletedDocument[]=await Promise.all(tombstones.map(async row=>{this.identity(row,row.id);const value=await unseal<DeletedDocument>(scope.key,row.sealed,aadOf(row));this.active(scope.epoch);if(value.id!==row.id||value.kind!==row.kind||value.revision!==row.revision||value.deletedAt!==row.updatedAt)throw new ContractError('VAULT_INTEGRITY_INVALID','删除索引校验失败');return value}))
      this.active(scope.epoch)
      return {protocol:LOCAL_PROTOCOL,vaultId:this.id,createdAt:stamp(),heads:decodedHeads,revisions:decodedRevisions,receipts,...(deleted.length?{deleted}:{})}
    }finally{db.close()}
  }
  private capture(){if(!this.key)throw new ContractError('VAULT_LOCKED','请先解锁私人库');return {key:this.key,epoch:this.epoch}}
  private active(epoch:number){if(!this.key||epoch!==this.epoch)throw new ContractError('VAULT_LOCKED','私人库已锁定，本次操作未继续')}
  private identity(row:Stored,id:string,revision?:number){
    const expectedKey=keyOf(this.id,id)+(revision===undefined?'':':'+revision)
    if(row.vaultId!==this.id||row.id!==id||row.key!==expectedKey||!Number.isSafeInteger(row.revision)||row.revision<1||revision!==undefined&&row.revision!==revision)throw new ContractError('VAULT_INTEGRITY_INVALID','私人库索引校验失败，原数据保留')
  }
  private async decode<T>(scope:{key:CryptoKey;epoch:number},row:Stored):Promise<T>{
    const value=await unseal<T>(scope.key,row.sealed,aadOf(row));this.active(scope.epoch)
    if(row.kind==='paragraph'){
      const paragraph=value as {id?:string;text?:string;textHash?:string}
      if(paragraph.id!==row.id||typeof paragraph.text!=='string'||await sha256(paragraph.text)!==paragraph.textHash)throw new ContractError('VAULT_INTEGRITY_INVALID','段落校验失败，原数据保留')
    }
    return value
  }
  async read<T>(id:string,revision?:number):Promise<Document<T>|null>{
    const scope=this.capture(),db=await database()
    try{
      const row=revision===undefined?await db.get('heads',keyOf(this.id,id)):await db.get('revisions',keyOf(this.id,id)+':'+revision)
      if(!row)return null
      this.identity(row,id,revision)
      const value=await this.decode<T>(scope,row);this.active(scope.epoch)
      return {id:row.id,kind:row.kind,revision:row.revision,updatedAt:row.updatedAt,value}
    }finally{db.close()}
  }
  async list<T>(kind:DomainKind):Promise<Document<T>[]>{
    const scope=this.capture(),db=await database()
    try{
      const rows=await db.getAllFromIndex('heads','vault-kind',[this.id,kind])
      const result=await Promise.all(rows.map(async row=>{this.identity(row,row.id);return {id:row.id,kind:row.kind,revision:row.revision,updatedAt:row.updatedAt,value:await this.decode<T>(scope,row)}}))
      this.active(scope.epoch);return result
    }finally{db.close()}
  }
  async readMany<T>(ids:string[]):Promise<Document<T>[]> {
    const scope=this.capture(),db=await database()
    try{
      const tx=db.transaction('heads','readonly'),rows=await Promise.all(ids.map(id=>tx.store.get(keyOf(this.id,id))))
      await tx.done
      rows.forEach((row,index)=>{if(row)this.identity(row,ids[index]!)})
      const result:Document<T>[]=[]
      for(let start=0;start<rows.length;start+=64){
        const page=await Promise.all(rows.slice(start,start+64).map(async row=>row?{id:row.id,kind:row.kind,revision:row.revision,updatedAt:row.updatedAt,value:await this.decode<T>(scope,row)}:null))
        this.active(scope.epoch);for(const row of page)if(row)result.push(row)
      }
      return result
    }finally{db.close()}
  }
  /** Limit decoding to this vault's exact opaque namespace, not every setting. */
  async listPrefix<T>(prefix:string):Promise<Document<T>[]>{
    if(!prefix||prefix.length>160)throw new ContractError('DOCUMENT_PREFIX','记录范围无效')
    const scope=this.capture(),db=await database(),start=keyOf(this.id,prefix)
    try{const rows=await db.getAll('heads',IDBKeyRange.bound(start,start+'\uffff'));const values=await Promise.all(rows.map(async row=>{this.identity(row,row.id);return {id:row.id,kind:row.kind,revision:row.revision,updatedAt:row.updatedAt,value:await this.decode<T>(scope,row)}}));this.active(scope.epoch);return values}finally{db.close()}
  }
  async receipt(operationId:string):Promise<MutationReceipt|null>{
    return (await this.receipts(operationId))?.[0]||null
  }
  /** A read-only, authenticated head snapshot also fences saves that change nothing. */
  async checkPreconditions(conditions:{id:string;revision:number;kind:DomainKind}[],unusedOperationId?:string):Promise<void>{
    if(conditions.length>3200||conditions.some(c=>!c.id||!Number.isSafeInteger(c.revision)||c.revision<0))throw new ContractError('MUTATION_INVALID','保存前置版本无效')
    if(unusedOperationId!==undefined&&(!unusedOperationId||unusedOperationId.length>160))throw new ContractError('MUTATION_INVALID','保存请求无效')
    const scope=this.capture(),db=await database()
    try{
      const tx=db.transaction(['heads','operations','erasures'],'readonly')
      const [rows,prior,erasure]=await Promise.all([Promise.all(conditions.map(c=>tx.objectStore('heads').get(keyOf(this.id,c.id)))),unusedOperationId?tx.objectStore('operations').get(keyOf(this.id,unusedOperationId)):undefined,unusedOperationId?tx.objectStore('erasures').get(keyOf(this.id,unusedOperationId)):undefined]);await tx.done
      if(prior){await this.operationReceipts(scope,prior,unusedOperationId!);throw new ContractError('IDEMPOTENCY_CONFLICT','这次保存的内容与原操作不同')}
      if(erasure){await this.erasure(scope,erasure,unusedOperationId!);throw new ContractError('IDEMPOTENCY_CONFLICT','此操作编号已经用于删除')}
      for(let index=0;index<conditions.length;index++){const condition=conditions[index]!,row=rows[index];if(row){this.identity(row,condition.id);await this.decode(scope,row)}if(condition.revision===0?!!row:!row||row.kind!==condition.kind||row.revision!==condition.revision)throw new ContractError('REVISION_CONFLICT','另一窗口已修改内容，当前输入保留')}
      this.active(scope.epoch)
    }finally{db.close()}
  }
  async receipts(operationId:string):Promise<MutationReceipt[]|null>{
    const scope=this.capture(),db=await database()
    try{const row=await db.get('operations',keyOf(this.id,operationId));if(!row)return null;const values=await this.operationReceipts(scope,row,operationId);for(const receipt of values)if(await db.get('deleted',keyOf(this.id,receipt.documentId)))throw new ContractError('DOCUMENT_DELETED','这份内容已删除，旧回执不会恢复正文');return values}finally{db.close()}
  }
  /** Authenticated local receipts, without decrypting unrelated prose or model configurations. */
  async documentReceipts(documentId:string,limit=20):Promise<MutationReceipt[][]>{
    if(!documentId||documentId.length>160||!Number.isSafeInteger(limit)||limit<1||limit>100)throw new ContractError('MUTATION_INVALID','回执查询无效')
    const scope=this.capture(),db=await database()
    try{
      const head=await db.get('heads',keyOf(this.id,documentId));this.active(scope.epoch)
      if(!head||await db.get('deleted',keyOf(this.id,documentId)))throw new ContractError('DOCUMENT_DELETED','这份内容已删除或不存在')
      this.identity(head,documentId);await this.decode(scope,head)
      const operations=await db.getAllFromIndex('operations','vault',this.id),matches:MutationReceipt[][]=[]
      for(const operation of operations){const receipts=await this.operationReceipts(scope,operation,operation.operationId);if(receipts.some(r=>r.documentId===documentId))matches.push(receipts)}
      this.active(scope.epoch);return matches.sort((a,b)=>b[0]!.committedAt.localeCompare(a[0]!.committedAt)).slice(0,limit)
    }finally{db.close()}
  }
  private async operationReceipts(scope:{key:CryptoKey;epoch:number},row:Operation,operationId:string){
    if(row.vaultId!==this.id||row.operationId!==operationId||row.key!==keyOf(this.id,operationId))throw new ContractError('VAULT_INTEGRITY_INVALID','操作回执索引校验失败')
    const receipts=await unseal<MutationReceipt[]>(scope.key,row.sealed,operationAad(row));this.active(scope.epoch)
    if(!Array.isArray(receipts)||!receipts.length||receipts.some(receipt=>receipt.protocol!==LOCAL_PROTOCOL||receipt.operationId!==operationId||receipt.payloadHash!==row.payloadHash))throw new ContractError('VAULT_INTEGRITY_INVALID','操作回执校验失败')
    return receipts
  }
  async previewBranchDeletion(branchId:string,signal?:AbortSignal):Promise<BranchDeletionPlan>{
    signal?.throwIfAborted();const scope=this.capture(),plan=await planBranchDeletion(await this.snapshot(),branchId);signal?.throwIfAborted();this.active(scope.epoch);return plan
  }
  async lastBranchDeletion(branchId:string):Promise<DeletionReceipt|null>{
    const scope=this.capture(),db=await database()
    try{const rows=await db.getAllFromIndex('erasures','vault',this.id);for(const row of rows){const receipt=await this.erasure(scope,row,row.operationId);if('branchId' in receipt&&receipt.branchId===branchId)return receipt}return null}finally{db.close()}
  }
  private async erasure(scope:{key:CryptoKey;epoch:number},row:Operation,operationId:string):Promise<ErasureReceipt>{
    if(row.vaultId!==this.id||row.operationId!==operationId||row.key!==keyOf(this.id,operationId))throw new ContractError('VAULT_INTEGRITY_INVALID','删除回执索引校验失败')
    const value=await unseal<ErasureReceipt>(scope.key,row.sealed,operationAad(row));this.active(scope.epoch)
    if(!value||value.operationId!==operationId||value.payloadHash!==row.payloadHash||!Array.isArray(value.documentIds)||!value.documentIds.length||new Set(value.documentIds).size!==value.documentIds.length||value.documentIds.some(id=>typeof id!=='string'||!id||id.length>160)||!Number.isSafeInteger(value.historyVersions)||value.historyVersions<value.documentIds.length||!Number.isFinite(Date.parse(value.committedAt))||('type' in value?value.type!=='story-batch'||!Array.isArray(value.storyIds)||!value.storyIds.length||value.storyIds.some(id=>!value.documentIds.includes(id)):typeof value.branchId!=='string'||!value.documentIds.includes(value.branchId)))throw new ContractError('VAULT_INTEGRITY_INVALID','删除回执校验失败')
    return value
  }
  private async branchErasure(scope:{key:CryptoKey;epoch:number},row:Operation,operationId:string):Promise<DeletionReceipt>{
    const receipt=await this.erasure(scope,row,operationId);if(!('branchId' in receipt))throw new ContractError('VAULT_INTEGRITY_INVALID','删除回执对象不一致');return receipt
  }
  async previewStoryDeletion(storyIds:string[],signal?:AbortSignal):Promise<StoryDeletionPlan>{
    signal?.throwIfAborted();const scope=this.capture(),plan=await planStoryDeletion(await this.snapshot(),storyIds);signal?.throwIfAborted();this.active(scope.epoch);return plan
  }
  async wasDeleted(id:string):Promise<boolean>{
    if(!id||id.length>160)throw new ContractError('MUTATION_INVALID','内容编号无效')
    const scope=this.capture(),db=await database()
    try{const row=await db.get('deleted',keyOf(this.id,id));this.active(scope.epoch);if(!row)return false;this.identity(row,id);const value=await unseal<DeletedDocument>(scope.key,row.sealed,aadOf(row));this.active(scope.epoch);validateDeletedDocuments([value],new Set());if(value.id!==row.id||value.kind!==row.kind||value.revision!==row.revision)throw new ContractError('VAULT_INTEGRITY_INVALID','删除身份校验失败');return true}finally{db.close()}
  }
  async deleteStories(operationId:string,storyIds:string[],fingerprint:string,signal?:AbortSignal):Promise<StoryDeletionReceipt>{
    signal?.throwIfAborted();if(!operationId||operationId.length>160||!/^[a-f0-9]{64}$/.test(fingerprint))throw new ContractError('MUTATION_INVALID','删除请求无效')
    const scope=this.capture(),ids=[...storyIds].sort(),payloadHash=await sha256(canonical({type:'delete-local-stories-v1',storyIds:ids,fingerprint})),lookup=await database()
    try{const prior=await lookup.get('erasures',keyOf(this.id,operationId));if(prior){if(prior.payloadHash!==payloadHash)throw new ContractError('IDEMPOTENCY_CONFLICT','这次删除与原操作不同');const receipt=await this.erasure(scope,prior,operationId);if(!('type' in receipt))throw new ContractError('VAULT_INTEGRITY_INVALID','删除回执对象不一致');return receipt}}finally{lookup.close()}
    const plan=await planStoryDeletion(await this.snapshot(),ids)
    if(plan.fingerprint!==fingerprint)throw new ContractError('DELETION_CHANGED','删除影响已变化，请重新检查')
    if(plan.blockers.length)throw new ContractError('DELETION_DEPENDENCIES','独立对象仍在使用所选书籍，请先处理关联')
    if(plan.activeTasks.length)throw new ContractError('DELETION_TASK_ACTIVE','仍有未结束或响应未知的任务，请先停止或确认结果')
    const receipt:StoryDeletionReceipt={type:'story-batch',operationId,payloadHash,storyIds:ids,committedAt:stamp(),documentIds:plan.items.map(row=>row.id),counts:plan.counts,historyVersions:plan.historyVersions}
    return this.erasePlan(scope,plan,receipt,ids,signal)
  }
  /** No paragraph bytes or old candidate bodies remain in heads/revisions after
   * this transaction. Opaque authenticated tombstones prevent late recreation;
   * original mutation receipts stay as audit identities, never as live content. */
  async deleteBranch(operationId:string,branchId:string,fingerprint:string,signal?:AbortSignal):Promise<DeletionReceipt>{
    signal?.throwIfAborted()
    if(!operationId||operationId.length>160||!branchId||!/^[a-f0-9]{64}$/.test(fingerprint))throw new ContractError('MUTATION_INVALID','删除请求无效')
    const scope=this.capture(),payloadHash=await sha256(canonical({type:'delete-local-branch-v1',branchId,fingerprint})),lookup=await database()
    try{const prior=await lookup.get('erasures',keyOf(this.id,operationId));if(prior){if(prior.payloadHash!==payloadHash)throw new ContractError('IDEMPOTENCY_CONFLICT','这次删除与原操作不同');return this.branchErasure(scope,prior,operationId)}}finally{lookup.close()}
    const snapshot=await this.snapshot(),plan=await planBranchDeletion(snapshot,branchId)
    if(plan.fingerprint!==fingerprint)throw new ContractError('DELETION_CHANGED','删除影响已变化，请重新检查')
    if(plan.blockers.length)throw new ContractError('DELETION_DEPENDENCIES','仍有后代或独立对象引用这条故事线，请先处理关联')
    if(plan.activeTasks.length)throw new ContractError('DELETION_TASK_ACTIVE','仍有未结束或响应未知的任务，请先停止或确认结果')
    const receipt:DeletionReceipt={operationId,payloadHash,branchId,committedAt:stamp(),documentIds:plan.items.map(row=>row.id),counts:plan.counts,historyVersions:plan.historyVersions}
    return this.erasePlan(scope,plan,receipt,[branchId],signal)
  }
  private async erasePlan<T extends ErasureReceipt>(scope:{key:CryptoKey;epoch:number},plan:BranchDeletionPlan|StoryDeletionPlan,receipt:T,priority:string[],signal?:AbortSignal):Promise<T>{
    const {operationId,payloadHash,committedAt}=receipt
    const deleted:Stored[]=await Promise.all(plan.items.map(async item=>{const value:DeletedDocument={id:item.id,kind:item.kind,revision:item.revision,deletedAt:committedAt};return {key:keyOf(this.id,item.id),vaultId:this.id,id:item.id,kind:item.kind,revision:item.revision,updatedAt:committedAt,sealed:await seal(scope.key,value,aadOf({vaultId:this.id,kind:item.kind,id:item.id,revision:item.revision}))}}))
    const operation:Operation={key:keyOf(this.id,operationId),vaultId:this.id,operationId,payloadHash,sealed:await seal(scope.key,receipt,operationAad({vaultId:this.id,operationId,payloadHash}))}
    this.active(scope.epoch);signal?.throwIfAborted();const db=await database()
    try{
      const tx=db.transaction(['heads','revisions','deleted','erasures','operations'],'readwrite',{durability:'strict'}),abort=()=>{try{tx.abort()}catch{}}
      this.writes.add(tx);signal?.addEventListener('abort',abort,{once:true});void tx.done.finally(()=>{this.writes.delete(tx);signal?.removeEventListener('abort',abort)}).catch(()=>{})
      try{
        const previous=await tx.objectStore('erasures').get(operation.key)
        if(previous){if(previous.payloadHash!==payloadHash)throw new ContractError('IDEMPOTENCY_CONFLICT','这次删除与原操作不同');await tx.done;return await this.erasure(scope,previous,operationId) as T}
        if(await tx.objectStore('operations').get(operation.key))throw new ContractError('IDEMPOTENCY_CONFLICT','此操作编号已经用于保存')
        const [heads,revisions]=await Promise.all([tx.objectStore('heads').index('vault').getAll(this.id),tx.objectStore('revisions').index('vault').getAll(this.id)])
        if(deletionState(heads,revisions)!==plan.state)throw new ContractError('DELETION_CHANGED','另一窗口已改变删除影响，请重新检查')
        this.active(scope.epoch);signal?.throwIfAborted();const ids=new Set(receipt.documentIds)
        for(const row of deleted){await tx.objectStore('heads').delete(row.key);await tx.objectStore('deleted').add(row)}
        for(const row of revisions)if(ids.has(row.id))await tx.objectStore('revisions').delete(row.key)
        this.active(scope.epoch);signal?.throwIfAborted();await tx.objectStore('erasures').add(operation);await tx.done
        const detail={vaultId:this.id,kinds:[...new Set(plan.items.map(row=>row.kind))],ids:[...priority,...receipt.documentIds.filter(id=>!priority.includes(id))].slice(0,200)};notifyCommit(detail);changes?.postMessage(detail);return receipt
      }catch(error){abort();await tx.done.catch(()=>{});throw error}
    }finally{db.close()}
  }
  async commit<T>(operationId:string,kind:DomainKind,id:string,expectedRevision:number,value:T):Promise<MutationReceipt>{
    return (await this.commitBundle(operationId,[{kind,id,expectedRevision,value}]))[0]!
  }
  async commitBundle(operationId:string,documents:WriteDocument[],signal?:AbortSignal,preconditions:{id:string;revision:number;kind:DomainKind}[]=[]):Promise<MutationReceipt[]>{
    if(!operationId||operationId.length>160||!documents.length||documents.length>3_100_000||new Set(documents.map(row=>row.id)).size!==documents.length||documents.some(row=>!row.id||row.id.length>160||!Number.isSafeInteger(row.expectedRevision)||row.expectedRevision<0))throw new ContractError('MUTATION_INVALID','保存请求无效')
    if(preconditions.length>3200||preconditions.some(condition=>!condition.id||!Number.isSafeInteger(condition.revision)||condition.revision<0))throw new ContractError('MUTATION_INVALID','保存前置版本无效')
    const scope=this.capture(),snapshots=JSON.parse(canonical(documents)) as WriteDocument[],conditions=JSON.parse(canonical(preconditions)) as typeof preconditions,payloadHash=await sha256(canonical(conditions.length?{documents:snapshots,preconditions:conditions}:snapshots)),updatedAt=stamp()
    signal?.throwIfAborted()
    const lookup=await database()
    try{for(const row of snapshots){if(await lookup.get('deleted',keyOf(this.id,row.id))&&!reusablePreference(row))throw new ContractError('DOCUMENT_DELETED','这份内容已删除，晚到结果不会重新写入');for(const ref of localReferences(row.kind,row.value))if(await lookup.get('deleted',keyOf(this.id,ref.id)))throw new ContractError('DOCUMENT_DELETED','引用内容已删除，晚到结果不会重新写入')}if(await lookup.get('erasures',keyOf(this.id,operationId)))throw new ContractError('IDEMPOTENCY_CONFLICT','此操作编号已经用于删除');const previous=await lookup.get('operations',keyOf(this.id,operationId));if(previous){const receipts=await this.operationReceipts(scope,previous,operationId);if(previous.payloadHash!==payloadHash)throw new ContractError('IDEMPOTENCY_CONFLICT','这次保存的内容与原操作不同');return receipts}}finally{lookup.close()}
    for(const document of snapshots){const previous=document.expectedRevision?await this.read(document.id,document.expectedRevision):null;if(document.expectedRevision&&!previous)throw new ContractError('REVISION_CONFLICT','对象历史版本不存在');validateLocalValue(document.kind,document.value,document.id);validateUpdate(document.kind,previous,document.value);signal?.throwIfAborted()}
    // A checkpoint is a publication, never a detached or future scene draft.
    for(const write of snapshots)if(write.kind==='checkpoint'){
      const cp=write.value as import('../playable/contract').PlayCheckpoint,worldWrite=snapshots.find(row=>row.kind==='world'&&row.id===cp.worldId)
      if(!worldWrite)throw new ContractError('PLAYABLE_HISTORY','检查点必须与存档正文一起保存')
      const world=worldWrite.value as import('../playable/contract').PlayWorld,save=world.saves.find(s=>s.id===cp.saveId)
      if(!save||save.headId!==write.id)throw new ContractError('PLAYABLE_HISTORY','检查点必须成为本次保存的实际进度')
      if(worldWrite.expectedRevision===0){if(cp.kind!=='start'||cp.parentId!==null)throw new ContractError('PLAYABLE_HISTORY','初始存档必须从故事起点开始')}
      else {
        const before=await this.read<import('../playable/contract').PlayWorld>(worldWrite.id,worldWrite.expectedRevision),parent=cp.parentId?await this.read<import('../playable/contract').PlayCheckpoint>(cp.parentId):null
        if(!before||before.kind!=='world'||!parent||parent.kind!=='checkpoint'||parent.value.worldId!==worldWrite.id||!before.value.saves.some(s=>s.id===parent.value.saveId))throw new ContractError('PLAYABLE_HISTORY','检查点依赖尚未发布的存档，未写入任何数据')
        if(cp.kind==='action'&&!before.value.saves.some(s=>s.id===cp.saveId&&s.headId===cp.parentId)||cp.kind==='fork'&&before.value.saves.some(s=>s.id===cp.saveId))throw new ContractError('PLAYABLE_HISTORY','行动的起点与存档版本不一致')
        if(cp.kind==='fork'){
          const source=before.value.saves.find(s=>s.id===parent.value.saveId)!,seen=new Set<string>();let cursor:string|null=source.headId,found=false
          while(cursor){if(cursor===parent.id){found=true;break}if(seen.has(cursor)||seen.size>1000000)throw new ContractError('PLAYABLE_HISTORY','存档的前文依赖不完整');seen.add(cursor);const node:Document<import('../playable/contract').PlayCheckpoint>|null=await this.read<import('../playable/contract').PlayCheckpoint>(cursor);if(!node||node.kind!=='checkpoint'||node.value.worldId!==worldWrite.id||node.value.saveId!==source.id)break;if(cursor===source.rootId)break;cursor=node.value.parentId}
          if(!found)throw new ContractError('PLAYABLE_HISTORY','这个起点尚未成为实际存档，未写入任何数据')
        }
      }
    }
    // Original sources and paragraphs are immutable. Edits use new branch IDs.
    if(snapshots.some(row=>['paragraph','source','sourceChunk'].includes(row.kind)&&row.expectedRevision!==0))throw new ContractError('SOURCE_IMMUTABLE','正文保留原版本，请把修改保存到新故事线')
    const relationValues=new Map<string,{kind:DomainKind;value:unknown;revision:number}>(),relationRevisions=new Map<string,number>()
    for(const row of snapshots)relationValues.set(row.id,{kind:row.kind,value:row.value,revision:row.expectedRevision+1})
    const fetchReference=async(reference:{id:string;kind:DomainKind})=>{if(!relationValues.has(reference.id)){const prior=await this.read(reference.id);if(!prior||prior.kind!==reference.kind)throw new ContractError('DOCUMENT_REFERENCE_INVALID','引用对象不存在或类型不一致，未写入任何数据');relationValues.set(prior.id,prior);relationRevisions.set(prior.id,prior.revision)}else if(relationValues.get(reference.id)!.kind!==reference.kind)throw new ContractError('DOCUMENT_REFERENCE_INVALID','引用对象类型不一致，未写入任何数据')}
    for(const row of snapshots)for(const reference of localReferences(row.kind,row.value))await fetchReference(reference)
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as Record<string,unknown>).assetType==='skill-draft')for(const referenceId of (row.value as {selected:string[]}).selected){const prior=await this.read(referenceId);if(!prior)throw new ContractError('DOCUMENT_REFERENCE_INVALID','技能草稿引用不存在');await fetchReference({id:prior.id,kind:prior.kind})}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as Record<string,unknown>).assetType==='agent-session')for(const referenceId of (row.value as {referenceIds:string[]}).referenceIds){const prior=await this.read(referenceId);if(!prior)throw new ContractError('DOCUMENT_REFERENCE_INVALID','助手引用不存在');await fetchReference({id:prior.id,kind:prior.kind})}
    // Relationship events point to messages, whose owning session is the second
    // hop. Do not recursively load every message in a large conversation.
    for(const row of snapshots)if(row.kind==='relationship')for(const event of (row.value as {events:{type:string;sourceId:string}[]}).events)if(event.type==='conversation'){const message=relationValues.get(event.sourceId)!.value as {sessionId:string};await fetchReference({id:message.sessionId,kind:'session'})}
    // Parent coverage checks require the child's frozen run and the exact original
    // paragraph sequence, including empty paragraphs skipped by a finite chunk.
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='novel-character-index'){
      const plan=row.value as import('../novel-characters/index-contract').CharacterIndex
      const line=await this.read<{paragraphIds:string[]}>(plan.source.branchId||plan.source.storyId,plan.source.branchRevision||plan.source.storyRevision)
      if(!line)throw new ContractError('CHARACTER_INDEX_SOURCE','人物整理故事版本缺失')
      if(plan.entries.length){const ids=line.value.paragraphIds.slice(0,line.value.paragraphIds.indexOf(plan.source.anchor.paragraphId)+1);for(let start=0;start<ids.length;start+=64)await Promise.all(ids.slice(start,start+64).map(id=>fetchReference({id,kind:'paragraph'})))}
      for(const entry of plan.entries){const child=relationValues.get(entry.scanId)?.value as import('../novel-characters/contract').CharacterScan|undefined;if(!child)throw new ContractError('CHARACTER_INDEX_SOURCE','人物整理子任务缺失');await fetchReference({id:child.runId,kind:'settings'})}
    }
    const historicalSources=new Map<string,Document>()
    for(const row of snapshots)if(row.kind==='progress'||row.kind==='note'){
      const anchor=(row.kind==='progress'?row.value:(row.value as {anchor:unknown}).anchor) as {storyId:string;branchId:string|null;lineRevision?:number}
      if(anchor.lineRevision!==undefined){const id=anchor.branchId||anchor.storyId;if(relationValues.get(id)?.revision!==anchor.lineRevision){const historical=await this.read(id,anchor.lineRevision);if(historical)historicalSources.set(id+':'+anchor.lineRevision,historical)}}
    }
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='writing-run'){
      const run=row.value as import('../writing/contract').WritingRun
      for(const ref of acceptedBasisReferences(run))if(ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}
      for(const ref of run.acceptedBasis?.origins||[]){const original=relationValues.get(ref.runId)?.revision===ref.runRevision?relationValues.get(ref.runId):historicalSources.get(ref.runId+':'+ref.runRevision),source=original?.value as import('../writing/contract').WritingRun|undefined;if(source?.assetType==='writing-run')for(const c of source.chapters.slice(0,ref.through))for(const id of c.paragraphIds)await fetchReference({id,kind:'paragraph'})}
    }
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='writing-run'){const config=(row.value as import('../writing/contract').WritingRun).configSnapshot;if(relationValues.get(config.id)?.revision!==config.revision){const historical=await this.read(config.id,config.revision);if(historical)historicalSources.set(config.id+':'+config.revision,historical)}}
    for(const row of snapshots)if(row.kind==='asset'||row.kind==='mediaJob'||row.kind==='settings'&&['media-compose','embedding-record','vector-draft','video-record','video-draft'].includes(String((row.value as {assetType?:string}).assetType))){
      const value=row.value as {links?:{targetId:string;targetRevision:number;branchId?:string|null;branchRevision?:number}[];source?:{targetId:string;targetRevision:number;branchId?:string|null;branchRevision?:number}|null;configSnapshot?:{id:string;revision:number}}
      const links=[...(value.links||[]),...(value.source?[value.source]:[])]
      const references=[...links,...links.filter(link=>link.branchId).map(link=>({targetId:link.branchId!,targetRevision:link.branchRevision!})),...(value.configSnapshot?[{targetId:value.configSnapshot.id,targetRevision:value.configSnapshot.revision}]:[])]
      for(const ref of references)if(relationValues.get(ref.targetId)?.revision!==ref.targetRevision){const historical=await this.read(ref.targetId,ref.targetRevision);if(historical)historicalSources.set(ref.targetId+':'+ref.targetRevision,historical)}
    }
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='writing-run'){const source=row.value as {targetBranchId:string;acceptedRevision:number;acceptedCount:number};if(source.acceptedCount&&relationValues.get(source.targetBranchId)?.revision!==source.acceptedRevision){const historical=await this.read(source.targetBranchId,source.acceptedRevision);if(historical)historicalSources.set(source.targetBranchId+':'+source.acceptedRevision,historical)}}
    for(const row of snapshots)if(row.kind==='branch'||row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='writing-run'){const source=row.value as {parentBranchId:string|null;storyId:string;parentRevision:number},id=source.parentBranchId||source.storyId;if(relationValues.get(id)?.revision!==source.parentRevision){const historical=await this.read(id,source.parentRevision);if(historical)historicalSources.set(id+':'+source.parentRevision,historical)}}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as Record<string,unknown>).assetType==='mcp-invocation'){const source=row.value as {connectorId:string;connectorRevision:number};if(relationValues.get(source.connectorId)?.revision!==source.connectorRevision){const historical=await this.read(source.connectorId,source.connectorRevision);if(historical)historicalSources.set(source.connectorId+':'+source.connectorRevision,historical)}}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='agent-team'){const team=row.value as {parentId:string;parentRevision:number};if(relationValues.get(team.parentId)?.revision!==team.parentRevision){const original=await this.read(team.parentId,team.parentRevision);if(original)historicalSources.set(team.parentId+':'+team.parentRevision,original)}}
    for(const row of snapshots)if((row.kind==='agentRun'||row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='agent-team')&&(row.value as {configSnapshot?:unknown}).configSnapshot){const profile=(row.value as {configSnapshot:{id:string;revision:number}}).configSnapshot;if(relationValues.get(profile.id)?.revision!==profile.revision){const original=await this.read(profile.id,profile.revision);if(original)historicalSources.set(profile.id+':'+profile.revision,original)}}
    for(const row of snapshots){const value=row.value as Record<string,unknown>,data=value.assetType==='plugin-report'?value.snapshot:(value.promptTrace as {plugins?:{snapshot?:unknown}}|undefined)?.plugins?.snapshot;if(data){const snapshot=data as import('../plugins/contract').PluginSnapshot;for(const ref of [{id:snapshot.targetId,revision:snapshot.targetRevision},...snapshot.plugins,...snapshot.references.filter(ref=>ref.kind!=='card')])if(relationValues.get(ref.id)?.revision!==ref.revision){const original=await this.read(ref.id,ref.revision);if(original)historicalSources.set(ref.id+':'+ref.revision,original)}}}
    for(const row of snapshots)if(row.kind==='agentRun')for(const ref of (row.value as {references:{id:string;revision:number}[]}).references){if(relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='skill-run'){const value=row.value as {skillId:string;skillRevision:number;references:{id:string;revision:number}[];configSnapshot:Record<string,{id:string;revision:number}>|null},refs=[{id:value.skillId,revision:value.skillRevision},...value.references,...Object.values(value.configSnapshot||{})];for(const ref of refs)if(relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as Record<string,unknown>).assetType==='script-run'){const source=(row.value as {source:{setId:string;revision:number}}).source,current=relationValues.get(source.setId);if(current?.revision!==source.revision){const historical=await this.read(source.setId,source.revision);if(historical)historicalSources.set(source.setId+':'+source.revision,historical)}}
    for(const row of snapshots)for(const ref of novelReferences(row.kind,row.value as Record<string,unknown>))if(ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}
    for(const row of snapshots)for(const ref of activityReferences(row.kind,row.value as Record<string,unknown>))if('revision' in ref&&ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}
    for(const row of snapshots)for(const ref of promotionReferences(row.kind,row.value as Record<string,unknown>))if(ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as Record<string,unknown>).assetType==='card-editor-draft'){const draft=row.value as import('../tavern/card-drafts').CardDraft;if(draft.cardRevision>0&&relationValues.get(draft.cardId)?.revision!==draft.cardRevision){const historical=await this.read(draft.cardId,draft.cardRevision);if(historical)historicalSources.set(draft.cardId+':'+draft.cardRevision,historical)}}
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='companion-create-draft'){
      const draft=row.value as import('../companion/creation').CompanionCreationDraft,refs=[...(draft.sourceCard?[draft.sourceCard]:[]),...(draft.status==='completed'?[{id:draft.companionId,revision:1},{id:draft.sessionId,revision:1},{id:'relationship:'+draft.companionId,revision:1}]:[])]
      for(const ref of refs)if(relationValues.get(ref.id)?.revision!==ref.revision){const original=await this.read(ref.id,ref.revision);if(original)historicalSources.set(ref.id+':'+ref.revision,original)}
    }
    for(const row of snapshots)if(row.kind==='settings'&&(row.value as {assetType?:string}).assetType==='original-story-draft'&&(row.value as {status:string}).status==='completed'){const source=row.value as {storyId:string};if(relationValues.get(source.storyId)?.revision!==1){const original=await this.read(source.storyId,1);if(original)historicalSources.set(source.storyId+':1',original)}}
    for(const row of snapshots)for(const ref of editReferences(row.kind,row.value as Record<string,unknown>))if(ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}
    for(const row of snapshots)for(const ref of planningReferences(row.kind,row.value as Record<string,unknown>))if(ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const historical=await this.read(ref.id,ref.revision);if(historical)historicalSources.set(ref.id+':'+ref.revision,historical)}
    for(const row of snapshots)for(const ref of playableReferences(row.kind,row.value))if(ref.revision&&relationValues.get(ref.id)?.revision!==ref.revision){const original=await this.read(ref.id,ref.revision);if(original)historicalSources.set(ref.id+':'+ref.revision,original)}
    for(const row of snapshots)validateLocalRelations(row.kind,row.id,row.value,(id,revision)=>revision===undefined||relationValues.get(id)?.revision===revision?relationValues.get(id):historicalSources.get(id+':'+revision))
    for(const row of snapshots)if(row.kind==='source'||row.kind==='sourceChunk'||row.kind==='settings'&&['writing-run','embedding-record','stage-state','original-story-draft'].includes(String((row.value as {assetType?:string}).assetType)))await validateSourceBytes(row.kind,row.value,(id,revision)=>revision===undefined||relationValues.get(id)?.revision===revision?relationValues.get(id):historicalSources.get(id+':'+revision))
    const rows:Stored[]=[]
    for(let start=0;start<snapshots.length;start+=64){
      rows.push(...await Promise.all(snapshots.slice(start,start+64).map(async doc=>{if(doc.kind==='paragraph'){const value=doc.value as {id:string;text:string;textHash:string};if(value.id!==doc.id||await sha256(value.text)!==value.textHash)throw new ContractError('VAULT_INTEGRITY_INVALID','段落身份或 hash 不一致，未写入任何数据')}if(doc.kind==='sourceChunk'){const value=doc.value as {data:string;bytes:number;sha256:string};let bytes:Uint8Array;try{bytes=Uint8Array.from(atob(value.data),c=>c.charCodeAt(0))}catch{throw new ContractError('VAULT_INTEGRITY_INVALID','文件片段编码无效，未写入任何数据')}if(bytes.length!==value.bytes||await sha256(bytes)!==value.sha256)throw new ContractError('VAULT_INTEGRITY_INVALID','文件片段 hash 不一致，未写入任何数据')}return {key:keyOf(this.id,doc.id),vaultId:this.id,id:doc.id,kind:doc.kind,revision:doc.expectedRevision+1,updatedAt,sealed:await seal(scope.key,doc.value,aadOf({vaultId:this.id,kind:doc.kind,id:doc.id,revision:doc.expectedRevision+1}))}})))
      this.active(scope.epoch)
      signal?.throwIfAborted()
    }
    this.active(scope.epoch)
    signal?.throwIfAborted()
    const receipts=rows.map(row=>({protocol:LOCAL_PROTOCOL,operationId,payloadHash,documentId:row.id,revision:row.revision,committedAt:row.updatedAt}))
    const operation:Operation={key:keyOf(this.id,operationId),vaultId:this.id,operationId,payloadHash,sealed:await seal(scope.key,receipts,operationAad({vaultId:this.id,operationId,payloadHash}))}
    this.active(scope.epoch);signal?.throwIfAborted()
    const db=await database()
    try{
      const tx=db.transaction(['operations','heads','revisions','deleted','erasures'],'readwrite',{durability:'strict'})
      this.writes.add(tx)
      const abort=()=>{try{tx.abort()}catch{/* The atomic commit may already be published. */}}
      signal?.addEventListener('abort',abort,{once:true})
      void tx.done.finally(()=>{signal?.removeEventListener('abort',abort);this.writes.delete(tx)}).catch(()=>{})
      const operations=tx.objectStore('operations'),heads=tx.objectStore('heads'),revisions=tx.objectStore('revisions')
      for(const row of snapshots){if(await tx.objectStore('deleted').get(keyOf(this.id,row.id))&&!reusablePreference(row)){tx.abort();await tx.done.catch(()=>{});throw new ContractError('DOCUMENT_DELETED','这份内容已删除，晚到结果不会重新写入')}for(const ref of localReferences(row.kind,row.value))if(await tx.objectStore('deleted').get(keyOf(this.id,ref.id))){tx.abort();await tx.done.catch(()=>{});throw new ContractError('DOCUMENT_DELETED','引用内容已删除，晚到结果不会重新写入')}}
      if(await tx.objectStore('erasures').get(keyOf(this.id,operationId))){tx.abort();await tx.done.catch(()=>{});throw new ContractError('IDEMPOTENCY_CONFLICT','此操作编号已经用于删除')}
      const previous=await operations.get(keyOf(this.id,operationId))
      if(previous){
        if(previous.payloadHash!==payloadHash){tx.abort();await tx.done.catch(()=>{});throw new ContractError('IDEMPOTENCY_CONFLICT','这次保存的内容与原操作不同')}
        await tx.done;return this.operationReceipts(scope,previous,operationId)
      }
      for(const condition of conditions){
        const current=await heads.get(keyOf(this.id,condition.id))
        if(current){try{this.identity(current,condition.id)}catch(error){tx.abort();await tx.done.catch(()=>{});throw error}}
        if(condition.revision===0?!!current:!current||current.revision!==condition.revision||current.kind!==condition.kind){tx.abort();await tx.done.catch(()=>{});throw new ContractError('REVISION_CONFLICT','请求目标已变化，未继续执行；请比较后再发起')}
      }
      const incoming=new Map(snapshots.map(row=>[row.id,row])),references=new Map<string,DomainKind>()
      for(const document of snapshots)for(const reference of localReferences(document.kind,document.value)){
        const peer=incoming.get(reference.id)
        if(peer&&peer.kind!==reference.kind||references.has(reference.id)&&references.get(reference.id)!==reference.kind){tx.abort();await tx.done.catch(()=>{});throw new ContractError('DOCUMENT_REFERENCE_INVALID','引用对象类型不一致，未写入任何数据')}
        if(!peer)references.set(reference.id,reference.kind)
      }
      for(const [id,revision] of relationRevisions){const row=await heads.get(keyOf(this.id,id));if(!row||row.revision!==revision||row.kind!==relationValues.get(id)!.kind){tx.abort();await tx.done.catch(()=>{});throw new ContractError('REVISION_CONFLICT','引用对象已在其他窗口修改；请重新检查后保存')}try{this.identity(row,id)}catch(error){tx.abort();await tx.done.catch(()=>{});throw error}}
      for(let index=0;index<rows.length;index++){
        if(signal?.aborted||scope.epoch!==this.epoch){tx.abort();await tx.done.catch(()=>{});signal?.throwIfAborted();this.active(scope.epoch)}
        const row=rows[index]!,current=await heads.get(row.key),expectedRevision=snapshots[index]!.expectedRevision
        if(current){try{this.identity(current,row.id)}catch(error){tx.abort();await tx.done.catch(()=>{});throw error}}
        if((current?.revision||0)!==expectedRevision||current&&current.kind!==row.kind){tx.abort();await tx.done.catch(()=>{});throw new ContractError('REVISION_CONFLICT','其他窗口已修改，请比较两个版本后保存')}
      }
      this.active(scope.epoch)
      for(let index=0;index<rows.length;index++){const row=rows[index]!;await revisions.add({...row,key:row.key+':'+row.revision});await heads.put(row);if(reusablePreference(snapshots[index]!))await tx.objectStore('deleted').delete(row.key)}
      await operations.add(operation)
      await tx.done;const detail={vaultId:this.id,kinds:[...new Set(snapshots.map(row=>row.kind))],ids:snapshots.slice(0,200).map(row=>row.id)};notifyCommit(detail);changes?.postMessage(detail);return receipts
    }finally{db.close()}
  }
}
