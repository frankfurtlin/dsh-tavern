import {ContractError} from '../domain/local-contract'
import type {ChatSession} from './chat-contract'
export interface GroupSettings {mode:'specified'|'sequential'|'random'|'natural';disabledIds:string[];probabilities:Record<string,number>;seed:string;turn:number;lastSpeakerId:string|null;director:string;maxAutomaticTurns:number}
export interface GroupRun {assetType:'group-run';intentId:string;sessionId:string;limit:number;started:number;state:'running'|'paused'|'stopped'|'completed';snapshot:GroupSettings;createdAt:string}
export function defaultGroup():GroupSettings{return {mode:'specified',disabledIds:[],probabilities:{},seed:crypto.randomUUID(),turn:0,lastSpeakerId:null,director:'',maxAutomaticTurns:6}}
export function validateGroup(v:unknown,identities?:string[]):asserts v is GroupSettings{
 const value=v as GroupSettings,bad=()=>{throw new ContractError('GROUP_INVALID','群聊编排设置无效')}
 if(!value||typeof value!=='object'||Array.isArray(value)||!['specified','sequential','random','natural'].includes(value.mode)||!Array.isArray(value.disabledIds)||value.disabledIds.length>8||value.disabledIds.some(id=>typeof id!=='string'||!id||id.length>160)||new Set(value.disabledIds).size!==value.disabledIds.length||!value.probabilities||typeof value.probabilities!=='object'||Array.isArray(value.probabilities)||Object.keys(value.probabilities).length>8||Object.entries(value.probabilities).some(([id,n])=>!id||id.length>160||typeof n!=='number'||!Number.isInteger(n)||n<0||n>100)||typeof value.seed!=='string'||!value.seed||value.seed.length>160||!Number.isSafeInteger(value.turn)||value.turn<0||value.lastSpeakerId!==null&&(typeof value.lastSpeakerId!=='string'||!value.lastSpeakerId)||typeof value.director!=='string'||value.director.length>2000||!Number.isSafeInteger(value.maxAutomaticTurns)||value.maxAutomaticTurns<1||value.maxAutomaticTurns>12)bad()
 if(identities&&[...value.disabledIds,...Object.keys(value.probabilities),...(value.lastSpeakerId?[value.lastSpeakerId]:[])].some(id=>!identities.includes(id)))bad()
}
function random(seed:string){let n=2166136261;for(const c of seed)n=Math.imul(n^c.charCodeAt(0),16777619)>>>0;return n/4294967296}
/** Scheduling only sees member identities/names and public user text. No private facts. */
export function chooseGroupSpeaker(session:ChatSession,requested:string,publicInput:string,settings=session.group){
 if(session.cards.length===1)return {speakerId:session.cards[0]!.value.identityId,next:settings||null,trace:null}
 const group=settings||defaultGroup(),ids=session.cards.map(row=>row.value.identityId);validateGroup(group,ids)
 const active=session.cards.filter(row=>!group.disabledIds.includes(row.value.identityId)&&(group.probabilities[row.value.identityId]??100)>0);if(!active.length)throw new ContractError('GROUP_NO_SPEAKER','没有可发言的成员，请启用一位角色')
 let speakerId=requested
 if(group.mode==='specified'){if(!active.some(row=>row.value.identityId===requested))throw new ContractError('GROUP_SPEAKER_DISABLED','所选成员已停用或发言概率为零')}
 else if(group.mode==='sequential'){const prior=ids.indexOf(group.lastSpeakerId||'');speakerId=Array.from({length:ids.length},(_,index)=>ids[(prior+1+index)%ids.length]!).find(id=>active.some(row=>row.value.identityId===id))!}
 else {const weights=active.map(row=>({id:row.value.identityId,weight:(group.probabilities[row.value.identityId]??100)*(group.mode==='natural'?(publicInput.includes(row.value.name)?4:row.value.identityId===group.lastSpeakerId?0.25:1):1)})),total=weights.reduce((n,row)=>n+row.weight,0);let pick=random(group.seed+':'+group.turn+':'+group.mode)*total;speakerId=weights.at(-1)!.id;for(const row of weights){pick-=row.weight;if(pick<0){speakerId=row.id;break}}}
 return {speakerId,next:{...group,turn:group.turn+1,lastSpeakerId:speakerId},trace:{mode:group.mode,seed:group.seed,turn:group.turn,speakerId,disabledIds:group.disabledIds,probabilities:group.probabilities,naturalRule:group.mode==='natural'?'公开提及加权与避免连续发言':null}}
}
