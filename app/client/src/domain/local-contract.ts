/** Device-private data contract. Account repositories never share this namespace. */
export const LOCAL_PROTOCOL = 'reader-local-v1' as const
export type DomainKind = 'story'|'source'|'sourceChunk'|'branch'|'paragraph'|'progress'|'note'|'card'|'persona'|'session'|'message'|'lorebook'|'memory'|'world'|'checkpoint'|'companion'|'relationship'|'asset'|'mediaJob'|'agentRun'|'changeSet'|'plugin'|'settings'|'task'
export interface Document<T = unknown> {id:string;kind:DomainKind;revision:number;value:T;updatedAt:string}
export interface MutationReceipt {protocol:typeof LOCAL_PROTOCOL;operationId:string;payloadHash:string;documentId:string;revision:number;committedAt:string}
export interface Paragraph {id:string;chapterId:string;text:string;sourceStart:number;sourceEnd:number;textHash:string}
export interface Chapter {id:string;title:string;paragraphIds:string[]}
export interface LocalStory {title:string;origin:'import'|'original';immutableSource:boolean;sourceHash:string;sourceId?:string;format:'txt'|'md'|'epub';encoding:string;chapters:Chapter[];paragraphIds:string[];coverAssetId?:string;tags:string[];favorite:boolean;pinned?:boolean;groups?:string[]}
export interface ProgressAnchor {storyId:string;branchId:string|null;paragraphId:string;offset:number;beatId?:string;lineRevision?:number}
export type TextProtocol='chat-completions'|'responses'|'anthropic'|'gemini'
export type ModelSlot='text'|'vision'|'image'|'speech'|'video'|'embedding'
export interface ProviderSnapshot {id:string;revision:number;protocol:TextProtocol;baseUrl:string;modelId:string;key:string;contextLimit:number;outputLimit:number;outputParameter?:'max_tokens'|'max_completion_tokens';includeUsage?:boolean;queryStoredResponse?:boolean}
export interface ProviderConfiguration extends Omit<ProviderSnapshot,'revision'> {name:string;enabled:boolean;slots:ModelSlot[];source:string;verifiedAt:string;dailyCalls:number;firstPacketMs:number;idleMs:number;totalMs:number}
export interface PromptMessage {role:'system'|'user'|'assistant';content:string}
export type LocalTaskStatus='queued'|'running'|'paused'|'unknown'|'received'|'saved'|'failed'|'cancelled'
export interface LocalTask {operationId:string;payloadHash:string;targetId:string;targetRevision:number;configSnapshot:ProviderSnapshot;status:LocalTaskStatus;providerRequestId:string|null;attemptId:string|null;text:string;usage:{input:number|null;output:number|null};events:{seq:number;type:string;at:string}[]}
export class ContractError extends Error {constructor(public code:string,message:string){super(message);this.name='ContractError'}}

/** Canonical JSON for transport hashes: sorted object keys, unchanged array order. */
export function canonical(value:unknown):string {
  if(value===null||typeof value==='string'||typeof value==='boolean')return JSON.stringify(value)
  if(typeof value==='number'){if(!Number.isFinite(value)||Math.abs(value)>Number.MAX_SAFE_INTEGER)throw new ContractError('JSON_NUMBER_INVALID','数值超出合同范围');return JSON.stringify(value)}
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']'
  if(typeof value==='object'&&Object.getPrototypeOf(value)===Object.prototype){
    return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}'
  }
  throw new ContractError('JSON_VALUE_INVALID','数据含不能保存的类型')
}
export async function sha256(value:string|Uint8Array):Promise<string>{
  const input=typeof value==='string'?new TextEncoder().encode(value):value
  const hash=await crypto.subtle.digest('SHA-256',Uint8Array.from(input))
  return Array.from(new Uint8Array(hash),n=>n.toString(16).padStart(2,'0')).join('')
}
export function unicodeOffset(text:string,offset:number):number {
  const bounded=Math.max(0,Math.min(text.length,Math.trunc(offset)))
  return bounded>0&&bounded<text.length&&/[\uD800-\uDBFF]/.test(text[bounded-1]!)&&/[\uDC00-\uDFFF]/.test(text[bounded]!)?bounded-1:bounded
}
