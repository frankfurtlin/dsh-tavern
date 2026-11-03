import {ContractError,type DomainKind} from '../domain/local-contract'
export const expressions=['neutral','happy','sad','angry','surprised','shy'] as const
export interface StageSettings {assetType:'stage-settings';textMs:number;fontSize:number;autoDelay:number;reducedMotion:boolean;systemVoice:boolean;voice:string}
export interface StageState {assetType:'stage-state';targetId:string;targetKind:'story'|'branch'|'session';cardIds:string[];cursor:{targetId:string;targetKind:'paragraph'|'message';offset:number;variantId:string|null}|null;overrides:{targetId:string;targetKind:'paragraph'|'message';textHash:string;start:number;end:number;cardId:string|null}[];portraits:{cardId:string;expression:typeof expressions[number];assetId:string}[]}
const obj=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v),id=(v:unknown)=>typeof v==='string'&&!!v&&v.length<=160,integer=(v:unknown,lo:number,hi:number)=>Number.isSafeInteger(v)&&Number(v)>=lo&&Number(v)<=hi
export const defaultStageSettings=():StageSettings=>({assetType:'stage-settings',textMs:28,fontSize:23,autoDelay:1400,reducedMotion:false,systemVoice:false,voice:''})
export const stageStateId=(targetId:string)=>'stage-state:'+targetId
export function validateStageSettings(v:unknown){if(!obj(v)||v.assetType!=='stage-settings'||!integer(v.textMs,0,150)||!integer(v.fontSize,16,40)||!integer(v.autoDelay,500,10000)||typeof v.reducedMotion!=='boolean'||typeof v.systemVoice!=='boolean'||typeof v.voice!=='string'||v.voice.length>1000)throw new ContractError('STAGE_SETTINGS','舞台设置无效，原设置保留')}
export function validateStageState(v:unknown,documentId?:string):asserts v is StageState {
 const bad=()=>{throw new ContractError('STAGE_STATE','演出存档无效，原正文保留')}
 if(!obj(v)||v.assetType!=='stage-state'||!id(v.targetId)||!['story','branch','session'].includes(String(v.targetKind))||documentId!==undefined&&documentId!==stageStateId(String(v.targetId))||!Array.isArray(v.cardIds)||v.cardIds.length>100||!v.cardIds.every(id)||new Set(v.cardIds).size!==v.cardIds.length||!Array.isArray(v.overrides)||v.overrides.length>2000||!Array.isArray(v.portraits)||v.portraits.length>600)bad()
 const r=v as unknown as StageState
 if(r.cursor!==null&&(!obj(r.cursor)||!id(r.cursor.targetId)||!['paragraph','message'].includes(r.cursor.targetKind)||!integer(r.cursor.offset,0,6_000_000)||r.cursor.variantId!==null&&!id(r.cursor.variantId)||(r.cursor.targetKind==='paragraph')!==(r.cursor.variantId===null)))bad()
 for(const row of r.overrides)if(!obj(row)||!id(row.targetId)||!['paragraph','message'].includes(row.targetKind)||!(/^[a-f0-9]{64}$/).test(String(row.textHash))||!integer(row.start,0,6_000_000)||!integer(row.end,row.start+1,6_000_000)||row.cardId!==null&&!r.cardIds.includes(row.cardId))bad()
 if(new Set(r.overrides.map(row=>[row.targetId,row.textHash,row.start,row.end].join(':'))).size!==r.overrides.length)bad()
 for(const row of r.portraits)if(!obj(row)||!r.cardIds.includes(row.cardId)||!expressions.includes(row.expression)||!id(row.assetId))bad()
 if(new Set(r.portraits.map(row=>row.cardId+':'+row.expression)).size!==r.portraits.length)bad()
}
export function stageReferences(value:StageState):{id:string;kind:DomainKind}[]{return [{id:value.targetId,kind:value.targetKind},...(value.targetKind==='session'?[]:value.cardIds.map(id=>({id,kind:'card' as const}))),...value.overrides.map(row=>({id:row.targetId,kind:row.targetKind})),...value.portraits.map(row=>({id:row.assetId,kind:'asset' as const})),...(value.cursor?[{id:value.cursor.targetId,kind:value.cursor.targetKind}]:[])]}
