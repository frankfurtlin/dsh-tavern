export type ComparisonKind='book'|'world'
export type ComparisonAlignment='latest'|'same-time'
export interface ComparisonTarget {id:string;name:string;nodeId:string}
export interface ComparisonQuery {storyId:string;kind:ComparisonKind;resourceId:string;left:ComparisonTarget;right:ComparisonTarget;alignment:ComparisonAlignment;atMinute?:number;leftOffset?:number;rightOffset?:number}
export interface ComparisonItem {id:string;chapterId?:string;title:string;text:string;partial?:boolean;action?:string;minute?:number}
export interface ComparisonSide {lineId:string;name:string;nodeId:string;requestedNodeId:string;observedNodeId:string|null;viewVersion:number;hasUnseen:boolean;items:ComparisonItem[];nextOffset:number|null;state:Record<string,unknown>|null}
export interface ComparisonSnapshot {protocol:'reader-compare-v1';storyId:string;kind:ComparisonKind;alignment:ComparisonAlignment;atMinute:number|null;common:{nodeId:string|null;visible:boolean;title:string|null;anchor?:unknown};left:ComparisonSide;right:ComparisonSide;differences:{path:string;before:unknown;after:unknown;beforeKnown:boolean;afterKnown:boolean}[];differencesTruncated:boolean}
export interface ComparisonCapabilities {protocol:'reader-compare-v1';ready:boolean;revealReady:boolean;alignments:{book:ComparisonAlignment[];world:ComparisonAlignment[]};maxPageSize:number}
export interface ComparisonRevealIntent {requestId:string;storyId:string;kind:ComparisonKind;resourceId:string;lineId:string;nodeId:string;expectedVersion:number}
export interface ComparisonRevealReceipt {protocol:'reader-compare-v1';kind:ComparisonKind;resourceId:string;lineId:string;nodeId:string;version:number;anchor:unknown}
export interface ComparisonRevealStatus {found:boolean;result:ComparisonRevealReceipt|null}
class ComparisonReceiptMismatch extends Error{}
export function comparisonPath(query:ComparisonQuery){
  const values=new URLSearchParams({kind:query.kind,[query.kind==='book'?'bookId':'worldId']:query.resourceId,leftLineId:query.left.id,rightLineId:query.right.id,leftNodeId:query.left.nodeId,rightNodeId:query.right.nodeId,alignment:query.alignment,limit:'20',leftOffset:String(query.leftOffset||0),rightOffset:String(query.rightOffset||0)})
  if(query.alignment==='same-time')values.set('atMinute',String(query.atMinute))
  return `/stories/${encodeURIComponent(query.storyId)}/compare?${values}`
}
export function validateComparison(value:ComparisonSnapshot,query:ComparisonQuery){
  if(value.protocol!=='reader-compare-v1'||value.storyId!==query.storyId||value.kind!==query.kind||value.alignment!==query.alignment||value.left.lineId!==query.left.id||value.right.lineId!==query.right.id||value.left.requestedNodeId!==query.left.nodeId||value.right.requestedNodeId!==query.right.nodeId)throw new Error('比较路径已经变化，请重新打开')
  return value
}
export function sameReveal(receipt:ComparisonRevealReceipt,intent:ComparisonRevealIntent){return receipt.protocol==='reader-compare-v1'&&receipt.kind===intent.kind&&receipt.resourceId===intent.resourceId&&receipt.lineId===intent.lineId&&receipt.nodeId===intent.nodeId&&receipt.version===intent.expectedVersion+1}

export interface ComparisonControllerDependencies {
  get(query:ComparisonQuery):Promise<ComparisonSnapshot>
  reveal(intent:ComparisonRevealIntent):Promise<ComparisonRevealReceipt>
  lookup(intent:ComparisonRevealIntent):Promise<ComparisonRevealStatus>
  persist(intent:ComparisonRevealIntent|null):Promise<void>
  active():boolean
  unknown(error:unknown):boolean
}
// No model endpoint is available to this controller. A lost write response is
// recovered only by the original durable request lookup, never by another POST.
export function createComparisonController(deps:ComparisonControllerDependencies){
  let readEpoch=0,writeEpoch=0,disposed=false,snapshot:ComparisonSnapshot|null=null,intent:ComparisonRevealIntent|null=null
  const active=()=>!disposed&&deps.active()
  return {
    get snapshot(){return snapshot},get pending(){return intent},
    invalidate(){readEpoch++;snapshot=null},
    dispose(){disposed=true;readEpoch++;writeEpoch++;snapshot=null},
    restore(value:ComparisonRevealIntent){if(active())intent=value},
    async load(query:ComparisonQuery){const epoch=++readEpoch;try{const value=await deps.get(query);if(!active()||epoch!==readEpoch)return null;snapshot=validateComparison(value,query);return snapshot}catch(error){if(!active()||epoch!==readEpoch)return null;throw error}},
    async reveal(value:ComparisonRevealIntent){
      if(!active()||intent)return null
      const epoch=++writeEpoch;intent=value;await deps.persist(value);if(!active()||epoch!==writeEpoch)return null
      try{const receipt=await deps.reveal(value);if(!active()||epoch!==writeEpoch)return null;if(!sameReveal(receipt,value))throw new ComparisonReceiptMismatch('查看回执与当前路径不符');await deps.persist(null);if(!active()||epoch!==writeEpoch)return null;intent=null;return receipt}
      catch(error){if(!active()||epoch!==writeEpoch)return null;if(!(error instanceof ComparisonReceiptMismatch)&&!deps.unknown(error)){await deps.persist(null);if(active()&&epoch===writeEpoch)intent=null}throw error}
    },
    async recover(){
      const value=intent;if(!active()||!value)return null
      const epoch=++writeEpoch,status=await deps.lookup(value);if(!active()||epoch!==writeEpoch)return null
      if(!status.found)return null
      if(!status.result||!sameReveal(status.result,value))throw new Error('查看回执与原请求不符')
      await deps.persist(null);if(!active()||epoch!==writeEpoch)return null;intent=null;return status.result
    }
  }
}
