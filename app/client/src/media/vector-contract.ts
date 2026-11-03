import {canonical,ContractError} from '../domain/local-contract'
import {validateAssetLink,validateMediaProvider,type AssetLink,type MediaProvider} from './contract'
export interface VectorRecord {
  assetType:'embedding-record';purpose:'index'|'query';retired:boolean;input:string;inputHash:string;intentHash:string;source:AssetLink|null;configSnapshot:MediaProvider&{revision:number};
  status:'queued'|'calling'|'saved'|'failed'|'unknown'|'cancelled'|'persistence_failed';claimId:string|null;leaseUntil:string|null;requestId:string|null;
  vector:number[]|null;inputTokens:number|null;error:string|null;createdAt:string;updatedAt:string;historical:boolean;
}
export interface VectorDraft {assetType:'vector-draft';input:string;source:AssetLink|null;createdAt:string}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v),hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)
const nullableText=(v:unknown,n:number)=>v===null||typeof v==='string'&&v.length<=n
export function validVector(v:unknown):v is number[]{return Array.isArray(v)&&v.length>0&&v.length<=16384&&v.every(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=1e9)&&v.some(n=>n!==0)}
function invalid():never{throw new ContractError('VECTOR_CONTRACT','向量对象格式无效，原索引保留')}
export function validateVectorDraft(v:unknown,documentId?:string):asserts v is VectorDraft {if(!object(v)||v.assetType!=='vector-draft'||documentId!==undefined&&!documentId.startsWith('vector-draft:')||typeof v.input!=='string'||v.input.length>8000||typeof v.createdAt!=='string'||!Number.isFinite(Date.parse(v.createdAt)))invalid();if(v.source!==null)validateAssetLink(v.source)}
export function validateVectorRecord(v:unknown,documentId?:string):asserts v is VectorRecord {
  if(!object(v)||v.assetType!=='embedding-record'||documentId!==undefined&&!documentId.startsWith('vector:')||!['index','query'].includes(String(v.purpose))||typeof v.retired!=='boolean'||typeof v.input!=='string'||!v.input.trim()||v.input.length>8000||!hash(v.inputHash)||!hash(v.intentHash)||!object(v.configSnapshot)||!Number.isSafeInteger(v.configSnapshot.revision)||Number(v.configSnapshot.revision)<1||!['queued','calling','saved','failed','unknown','cancelled','persistence_failed'].includes(String(v.status))||!nullableText(v.claimId,160)||v.claimId===''||!nullableText(v.requestId,1000)||!nullableText(v.leaseUntil,50)||v.leaseUntil!==null&&!Number.isFinite(Date.parse(String(v.leaseUntil)))||!nullableText(v.error,2000)||v.vector!==null&&!validVector(v.vector)||v.inputTokens!==null&&(!Number.isSafeInteger(v.inputTokens)||Number(v.inputTokens)<0)||typeof v.historical!=='boolean'||[v.createdAt,v.updatedAt].some(date=>typeof date!=='string'||!Number.isFinite(Date.parse(date))))invalid()
  validateMediaProvider(v.configSnapshot);if(v.configSnapshot.protocol!=='openai-embeddings')invalid();if(v.source!==null)validateAssetLink(v.source)
  if(v.status==='queued'&&(v.claimId||v.requestId||v.vector||v.leaseUntil)||v.status==='calling'&&(!v.claimId||!v.leaseUntil)||v.status==='saved'&&(!v.claimId||!v.vector)||v.status!=='calling'&&v.leaseUntil!==null||v.vector&&!v.claimId)invalid()
  const key=String(v.configSnapshot.key);if(key&&[v.requestId,v.error].some(value=>typeof value==='string'&&value.includes(key)))invalid()
}
export function validateVectorUpdate(before:VectorRecord,after:VectorRecord){
  for(const key of ['assetType','purpose','input','inputHash','intentHash','source','configSnapshot','createdAt','historical'] as const)if(canonical(before[key])!==canonical(after[key]))throw new ContractError('IMMUTABLE_FIELD','向量保留原输入、来源与通道，改动请创建新索引')
  if(before.claimId&&before.claimId!==after.claimId||before.vector&&canonical(before.vector)!==canonical(after.vector))invalid()
  if(before.requestId&&before.requestId!==after.requestId)throw new ContractError('IMMUTABLE_FIELD','已知供应商请求编号保留原返回')
  if(before.vector&&(before.requestId!==after.requestId||before.inputTokens!==after.inputTokens))throw new ContractError('IMMUTABLE_FIELD','已知向量回执和用量保留原返回')
  const transitions:Record<VectorRecord['status'],VectorRecord['status'][]>= {queued:['queued','calling','cancelled'],calling:['calling','saved','failed','unknown','cancelled','persistence_failed'],persistence_failed:['persistence_failed','saved','cancelled'],unknown:['unknown','saved','cancelled'],saved:['saved'],failed:['failed'],cancelled:['cancelled']}
  if(!before.historical&&!transitions[before.status].includes(after.status))invalid()
}
export function cosine(left:number[],right:number[]){if(!validVector(left)||!validVector(right)||left.length!==right.length)throw new ContractError('VECTOR_DIMENSIONS','向量维度或数值不匹配，请使用同一个向量模型');let dot=0,a=0,b=0;for(let i=0;i<left.length;i++){dot+=left[i]!*right[i]!;a+=left[i]!*left[i]!;b+=right[i]!*right[i]!}return Math.max(-1,Math.min(1,dot/Math.sqrt(a*b)))}
