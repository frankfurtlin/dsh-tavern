import type {Anchor,AcceptResult,Target} from './types'
import type {EventLedger} from '../writing/events'
import type {AcceptedEventContext} from '../writing/accepted-context'

export const proseProtocol='reader-prose-v1' as const
export type ChapterLength='short'|'standard'|'long'
export type GenerationDurability='journaled'|'saving'|'saved'|'unconfirmed'
export interface GenerationHint {code:string;message?:string}
export interface GenerationRawPresentation {
  schema:'prose-presentation-v1';chapterIndex:number;selected:'original'|'repair'
  attempts:{kind:'original'|'repair';raw:string;rawHash:string;finishReason:string|null}[]
  presentation:{text:string;title:string;format:'prose'|'json'|'unrecognized';thoughtOmitted:boolean;pending:boolean}
}
export interface GenerationReceivedRaw {
  generationId:string;candidateVersion:number;contentId:string;streamVersion:number;snapshotVersion:number
  rawPresentation:GenerationRawPresentation
}
export interface GenerationChapter {
  index:number;contentId:string;title:string;text:string;contentHash:string|null
  streamVersion:number;snapshotVersion:number;completeness:'partial'|'complete'
  durability:GenerationDurability;canAccept:boolean;hints:GenerationHint[]
  rawPresentation?:GenerationRawPresentation
  eventLedger?:EventLedger
}
export interface GenerationSnapshot {
  id:string;taskId:string;protocol:typeof proseProtocol;bookId:string;scopeAnchor:Anchor;createdAt:string
  // Normalized to false for the pre-import-history DTO. Historical unknown
  // metadata is not a running task or evidence of successful model execution.
  importedHistory:boolean;readOnly:boolean
  candidateVersion:number;requestedChapters:number;completedChapters:number;acceptedPrefix:number
  targetBranchId:string|null;lastAcceptedRevisionId:string|null;eventCursor:number
  state:string;phase:string;providerState:string;persistenceState:string;usageState:string
  cancelRequested:boolean;errorCode:string|null;chapters:GenerationChapter[]
  eventStateStatus?:'complete'|'unavailable'
  acceptedEventBasis?:AcceptedEventContext
  acceptedBasisUnavailable?:boolean
  fee?:{amount:number|null;currency:string|null;certainty:'known'|'unknown'}
  selection?:{purpose:'expand'|'rewrite';startAnchor:Anchor;endAnchor:Anchor;sourcePolicy:'requireUnchanged'|'forkPinned'}
}
export interface GenerationRequest {
  protocol:typeof proseProtocol;originAnchor:Anchor;operation:string;chapterCount:number
  chapterLength:ChapterLength;instruction:string;profileId:string;expectedHeadRevisionId:string;target:Target
  selection?:NonNullable<GenerationSnapshot['selection']>
}
export interface GenerationReceipt {generationId:string;taskId:string;protocol:typeof proseProtocol}
export type GenerationRequestStatus={found:false}|{found:true;receipt:GenerationReceipt}
export interface GenerationCapabilities {protocol:typeof proseProtocol;ready:boolean;selectionWriting?:boolean}
export interface GenerationAcceptRequest {
  candidateVersion:number;prefixCount:number;expectedAcceptedPrefix:number
  expectedHeadRevisionId:string;target:'child'|'append';sourcePolicy:'requireUnchanged'|'forkPinned'
}
export interface GenerationAcceptResult extends AcceptResult {generationId:string;candidateVersion:number;acceptedPrefix:number}
export interface GenerationEvent {
  taskId:string;seq:number;type:'phase'|'text_delta'|'chapter_saved'|'content_hint'|'usage'|'terminal'
  payload:{candidateVersion?:number;contentId?:string;chapterIndex?:number;streamVersion?:number;startUtf16?:number;delta?:string;durability?:GenerationDurability;[key:string]:unknown}
}
export interface GenerationIntent {key:string;request:GenerationRequest;generationId?:string;taskId?:string}
export interface GenerationRewriteIntent {key:string;generationId:string;body:{candidateVersion:number;expectedAcceptedPrefix:number;instruction:string}}
export interface GenerationVersion {candidateVersion:number;createdAt:string}
