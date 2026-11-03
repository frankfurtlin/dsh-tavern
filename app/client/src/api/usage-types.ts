export interface UsageStory {id:string;kind:'book'|'world';title:string;removed:boolean}
export interface UsageRecord {
  id:string;taskId:string;providerCallId:string;profileId:string;phase:string;operation:string
  task:{id:string;type:string;state:string};story:UsageStory|null
  profile:{id:string;name:string;modelId:string|null;version:number|null}
  inputTokens:number|null;outputTokens:number|null;reasoningTokens:number|null
  reasoningIncludedInOutput:boolean|null;cost:number|null;currency:string|null;costState:'known'|'unknown'
  reservedInput:number;reservedOutput:number;usageState:string;status:string;createdAt:string;latencyMs:number|null
}
export interface UsageSummary {
  calls:number;records:number;notSentCalls:number;unconfirmedCalls:number
  inputTokens:number|null;outputTokens:number|null;reasoningTokens:number|null
  knownCostByCurrency:Record<string,number>;unknownCostCalls:number
}
export interface UsagePage {items:UsageRecord[];nextCursor:string|null;summary:UsageSummary}
export interface UsageFilters {
  tasks:{id:string;type:string;createdAt:string}[];profiles:{id:string;name:string;modelId:string}[]
  stories:UsageStory[];taskLimit:number
}
