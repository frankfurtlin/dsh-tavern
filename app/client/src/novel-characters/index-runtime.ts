import {canonical,sha256,ContractError,type Document,type Paragraph,type ProviderConfiguration} from '../domain/local-contract'
import type {LocalVault,WriteDocument} from '../repositories/local-vault'
import {slotModel} from '../repositories/local-models'
import type {SkillRun} from '../skills/contract'
import {stopSkillRun} from '../skills/runtime'
import {characterChunk,characterSlice,type CharacterChunk} from './chunking'
import {prepareCharacterScan,runCharacterScan,applyCharacterScan} from './runtime'
import type {CharacterScan,CharacterScope,NovelCharacter} from './contract'
import {validateCharacterIndex,type CharacterIndex,type CharacterIndexSource,type CharacterIndexEntry} from './index-contract'
const stamp=()=>new Date().toISOString(),copy=<T>(v:T):T=>JSON.parse(canonical(v))
const active=new Map<string,Promise<Document<CharacterIndex>|null>>()
const key=(v:LocalVault,id:string)=>v.id+':'+id
async function commit(v:LocalVault,id:string,writes:WriteDocument[],conditions:Parameters<LocalVault['commitBundle']>[3]=[],signal?:AbortSignal){const hash=await sha256(canonical(conditions.length?{documents:writes,preconditions:conditions}:writes));try{await v.commitBundle(id,writes,signal,conditions)}catch(e){if(!v.unlocked||(await v.receipt(id))?.payloadHash!==hash)throw e}}
export async function prepareCharacterIndex(v:LocalVault,source:CharacterIndexSource,maxCalls:number,intentId:string,signal:AbortSignal){
 signal.throwIfAborted();if(!Number.isSafeInteger(maxCalls)||maxCalls<1||maxCalls>50||!intentId||intentId.length>160)throw new ContractError('CHARACTER_INDEX_LIMIT','请选择 1—50 次的整理上限')
 const id='character-index:'+await sha256(canonical({source,maxCalls,intentId})),old=await v.read<CharacterIndex>(id);if(old)return old
 const chunk=await characterChunk(v,source.storyId,source.branchId,source.anchor);if(!chunk||canonical(source)!==canonical({storyId:chunk.scope.storyId,storyRevision:chunk.scope.storyRevision,branchId:chunk.scope.branchId,branchRevision:chunk.scope.branchRevision,anchor:chunk.scope.anchor}))throw new ContractError('CHARACTER_INDEX_SOURCE','整理读点或故事版本已变化，请重新选择')
 const profile=await slotModel(v,'text'),at=stamp(),value:CharacterIndex={assetType:'novel-character-index',schema:'novel-character-index-v1',source:copy(source),profile:copy(profile),maxCalls,grants:[{id:'grant:'+await sha256(canonical({id,intentId,maxCalls})),calls:maxCalls,at}],entries:[],completedCount:0,status:'queued',authorized:true,historical:false,activeScanId:null,ownerId:null,leaseUntil:null,error:null,createdAt:at,updatedAt:at}
 validateCharacterIndex(value,id);try{await commit(v,'character-index-prepare:'+id,[{kind:'settings',id,expectedRevision:0,value}],[{id:profile.id,kind:'settings',revision:profile.revision},{id:source.storyId,kind:'story',revision:source.storyRevision},...(source.branchId?[{id:source.branchId,kind:'branch' as const,revision:source.branchRevision!}]:[])],signal)}catch(e){if(!v.unlocked||!['REVISION_CONFLICT','IDEMPOTENCY_CONFLICT'].includes((e as ContractError).code))throw e;const winner=await v.read<CharacterIndex>(id);if(!winner||canonical(winner.value.source)!==canonical(source)||winner.value.maxCalls!==maxCalls)throw e;return winner}
 return (await v.read<CharacterIndex>(id))!
}
/** A new explicit click appends a grant; past limits are never rewritten. */
export async function extendCharacterIndex(v:LocalVault,id:string,calls:number,intentId:string,signal:AbortSignal){
 signal.throwIfAborted();if(!Number.isSafeInteger(calls)||calls<1||calls>50||!intentId||intentId.length>160)throw new ContractError('CHARACTER_INDEX_LIMIT','追加上限无效')
 const grantId='grant:'+await sha256(canonical({id,calls,intentId})),row=await v.read<CharacterIndex>(id);if(!row)throw new ContractError('CHARACTER_INDEX_SOURCE','找不到整理记录');if(row.value.grants.some(g=>g.id===grantId))return row
 if(row.value.status!=='paused'||row.value.historical||!row.value.authorized||row.value.activeScanId!==null||row.value.completedCount!==row.value.entries.length||row.value.maxCalls+calls>50)throw new ContractError('CHARACTER_INDEX_GRANT','这条记录暂不可追加，单条整理最多50次；原请求不会重发')
 const at=stamp();await commit(v,'character-index-grant:'+grantId,[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,maxCalls:row.value.maxCalls+calls,grants:[...row.value.grants,{id:grantId,calls,at}],error:null,updatedAt:at}}],[],signal);return (await v.read<CharacterIndex>(id))!
}
/** Only exact primary occurrences are reintroduced. A shared name is never an identity key. */
async function withBindings(v:LocalVault,chunk:CharacterChunk){
 const scope=copy(chunk.scope),line=await v.read<{paragraphIds:string[]}>(scope.branchId||scope.storyId,scope.branchRevision||scope.storyRevision);if(!line)throw new ContractError('CHARACTER_INDEX_SOURCE','原故事版本缺失')
 const ids=line.value.paragraphIds,anchorIndex=ids.indexOf(scope.anchor.paragraphId),rows=(await v.list<NovelCharacter>('settings')).filter(r=>r.value.assetType==='novel-character'&&r.value.scope.storyId===scope.storyId&&r.value.scope.branchId===scope.branchId&&chunk.scope.sources.some(s=>s.text.includes(r.value.name)))
 const bindings:CharacterIndexEntry['bindings']=[],intervals=scope.sources.map(s=>({id:s.id,revision:s.revision,start:s.start!,limit:s.limit}))
 for(const row of rows){const p=row.value.primary,pos=ids.indexOf(p.id);if(pos<0||pos>anchorIndex||pos===anchorIndex&&p.offset+p.quote.length>scope.anchor.offset)continue;if(intervals.some(s=>s.id===p.id&&s.revision===p.revision&&s.start<=p.offset&&s.limit>=p.offset+p.quote.length))continue;bindings.push({id:row.id,revision:row.revision});intervals.push({id:p.id,revision:p.revision,start:p.offset,limit:p.offset+p.quote.length})}
 if(bindings.length>16)throw new ContractError('CHARACTER_INDEX_IDENTITIES','这段正文涉及超过16个既有人物，请缩小范围后整理；没有按名字合并')
 intervals.sort((a,b)=>ids.indexOf(a.id)-ids.indexOf(b.id)||a.start-b.start);const merged:typeof intervals=[];for(const r of intervals){const tail=merged.at(-1);if(tail?.id===r.id&&r.start<=tail.limit){if(tail.revision!==r.revision)throw new ContractError('CHARACTER_INDEX_SOURCE','原文人物版本不一致');tail.limit=Math.max(tail.limit,r.limit)}else merged.push({...r})}
 scope.sources=[];for(const r of merged){const row=await v.read<Paragraph>(r.id,r.revision);if(!row||row.kind!=='paragraph')throw new ContractError('CHARACTER_INDEX_SOURCE','人物原文缺失');scope.sources.push(await characterSlice(row,r.start,r.limit))}
 return {scope,bindings}
}
export async function assertCharacterIndexLive(v:LocalVault,scanId:string){
 const scan=await v.read<CharacterScan>(scanId);if(!scan?.value.index)return null
 const binding=scan.value.index,row=await v.read<CharacterIndex>(binding.id),plan=row?.value,entry=plan?.entries[binding.step]
 if(!row||plan?.assetType!=='novel-character-index'||plan.historical||!plan.authorized||plan.status!=='running'||plan.activeScanId!==scanId||entry?.scanId!==scanId||!plan.leaseUntil||Date.parse(plan.leaseUntil)<=Date.now())throw new ContractError('CHARACTER_INDEX_STOPPED','人物整理已停止或执行权已变化')
 const profile=await v.read<ProviderConfiguration>(plan.profile.id),story=await v.read(plan.source.storyId),branch=plan.source.branchId?await v.read(plan.source.branchId):null
 if(profile?.revision!==plan.profile.revision||!profile.value.enabled||story?.revision!==plan.source.storyRevision||plan.source.branchId&&branch?.revision!==plan.source.branchRevision)throw new ContractError('CHARACTER_INDEX_CHANGED','模型或故事版本已变化，整理已暂停')
 return row
}
export async function stopCharacterIndex(v:LocalVault,id:string){
 for(let retry=0;retry<3;retry++){const row=await v.read<CharacterIndex>(id);if(!row||['completed','cancelled'].includes(row.value.status))return;const scan=row.value.activeScanId?await v.read<CharacterScan>(row.value.activeScanId):null;try{await commit(v,'character-index-stop:'+id+':'+row.revision,[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,status:'cancelled',authorized:false,activeScanId:null,ownerId:null,leaseUntil:null,error:'已停止，已有角色和原返回保留',updatedAt:stamp()}}]);if(scan)await stopSkillRun(v,scan.value.runId);return}catch(e){if((e as ContractError).code!=='REVISION_CONFLICT'||retry===2)throw e}}
}
/** Refresh never calls this. Explicit continuation only executes an unsent child or saves a known result. */
export function runCharacterIndex(v:LocalVault,id:string,signal:AbortSignal,transport:typeof fetch=fetch){
 const existing=active.get(key(v,id));if(existing)return existing
 const owner=crypto.randomUUID(),work=(async()=>{let row=await v.read<CharacterIndex>(id);if(!row||row.value.historical||!row.value.authorized||['completed','cancelled'].includes(row.value.status))throw new ContractError('CHARACTER_INDEX_INERT','这条整理记录不能执行')
  if(row.value.status==='running'){if(Date.parse(row.value.leaseUntil!)>Date.now())return row;await commit(v,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,status:'paused',ownerId:null,leaseUntil:null,error:'原响应待核对，未重发请求',updatedAt:stamp()}}]);if(row.value.activeScanId){const child=await v.read<CharacterScan>(row.value.activeScanId);if(child)await stopSkillRun(v,child.value.runId)}return (await v.read<CharacterIndex>(id))!}
  if(row.value.activeScanId){const child=await v.read<CharacterScan>(row.value.activeScanId),run=child?await v.read<SkillRun>(child.value.runId):null;if(!child||!run||!['queued','ready'].includes(run.value.status)&&!child.value.applied)throw new ContractError('CHARACTER_INDEX_UNCONFIRMED','原响应尚未确认；可以查看记录，这次不会重发')}
  signal.throwIfAborted();await commit(v,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,status:'running',ownerId:owner,leaseUntil:new Date(Date.now()+900000).toISOString(),error:null,updatedAt:stamp()}}],[],signal)
  const stop=()=>{if(v.unlocked)void stopCharacterIndex(v,id).catch(()=>{})};signal.addEventListener('abort',stop,{once:true})
  try{while(true){signal.throwIfAborted();row=(await v.read<CharacterIndex>(id))!;if(!row||row.value.status!=='running'||row.value.ownerId!==owner||!row.value.authorized)throw new ContractError('CHARACTER_INDEX_STOPPED','这次整理已停止')
    let scan=row.value.activeScanId?await v.read<CharacterScan>(row.value.activeScanId):null
    if(!scan){if(row.value.entries.length>=row.value.maxCalls){await commit(v,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,status:'paused',ownerId:null,leaseUntil:null,error:'已达到本次整理上限；已有角色保留',updatedAt:stamp()}}]);break}
     const cursor=row.value.entries.at(-1)?.next||undefined,chunk=await characterChunk(v,row.value.source.storyId,row.value.source.branchId,row.value.source.anchor,cursor)
     if(!chunk)throw new ContractError('CHARACTER_INDEX_SOURCE','本次覆盖记录与终点不一致')
     const {scope,bindings}=await withBindings(v,chunk);scan=await prepareCharacterScan(v,scope,signal,id+':'+row.value.entries.length,{plan:row,coverage:chunk.scope.sources.map(s=>({id:s.id,revision:s.revision,start:s.start!,limit:s.limit})),next:chunk.next,bindings})
    }
    await assertCharacterIndexLive(v,scan.id);const run=await v.read<SkillRun>(scan.value.runId);if(!run)throw new ContractError('CHARACTER_INDEX_RUN','人物整理任务缺失')
    if(!scan.value.applied){if(run.value.status==='queued')await runCharacterScan(v,scan,signal,transport);else if(run.value.status==='ready')await applyCharacterScan(v,scan.id,signal);else throw new ContractError('CHARACTER_INDEX_UNCONFIRMED','原响应尚未确认；请求没有重发')}
    signal.throwIfAborted();row=(await assertCharacterIndexLive(v,scan.id))!;if(row.value.ownerId!==owner)throw new ContractError('CHARACTER_INDEX_OWNER','人物整理执行权已变化');const complete=row.value.entries.at(-1)!.next===null
    await commit(v,'character-index-finish:'+id+':'+row.value.completedCount,[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,completedCount:row.value.completedCount+1,activeScanId:null,status:complete?'completed':'running',ownerId:complete?null:owner,leaseUntil:complete?null:new Date(Date.now()+900000).toISOString(),error:null,updatedAt:stamp()}}],[],signal);if(complete)break
   }
  }catch(e){if(v.unlocked){const current=await v.read<CharacterIndex>(id);if(current?.value.status==='running'&&current.value.ownerId===owner)await commit(v,crypto.randomUUID(),[{kind:'settings',id,expectedRevision:current.revision,value:{...current.value,status:'paused',ownerId:null,leaseUntil:null,error:(e as Error).message.slice(0,2000)||'整理暂停，原返回保留',updatedAt:stamp()}}]).catch(()=>{})}throw e}finally{signal.removeEventListener('abort',stop)}
  return await v.read<CharacterIndex>(id)
 })();active.set(key(v,id),work);void work.finally(()=>active.delete(key(v,id))).catch(()=>{});return work
}
