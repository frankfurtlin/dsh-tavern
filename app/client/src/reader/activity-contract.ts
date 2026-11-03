import {canonical,ContractError,unicodeOffset,type DomainKind,type Document} from '../domain/local-contract'
export interface ReadingEvent {assetType:'reading-event';storyId:string;source:{id:string;kind:'story'|'branch';revision:number};anchor:{paragraphId:string;offset:number};seconds:number;recordedAt:string;localDate:string;readerId:string}
export interface ReadingGoal {assetType:'reading-goal';minutes:number}
const id=(v:unknown)=>typeof v==='string'&&v.length>0&&v.length<=160
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const keys=(v:Record<string,unknown>,names:string)=>Object.keys(v).sort().join(',')===names
const fail=():never=>{throw new ContractError('READING_ACTIVITY','阅读记录无效，已有记录保留')}
const calendarDay=(v:unknown)=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&Number.isFinite(Date.parse(v+'T12:00:00Z'))&&new Date(v+'T12:00:00Z').toISOString().slice(0,10)===v
export function localDay(date:Date){return date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0')}
export function validateReadingEvent(value:unknown,documentId?:string):asserts value is ReadingEvent {
 if(!object(value)||!keys(value,'anchor,assetType,localDate,readerId,recordedAt,seconds,source,storyId')||value.assetType!=='reading-event'||!id(value.storyId)||!id(value.readerId)||documentId!==undefined&&!/^reading-event:[a-zA-Z0-9-]+$/.test(documentId)||!object(value.source)||!keys(value.source,'id,kind,revision')||!id(value.source.id)||!['story','branch'].includes(String(value.source.kind))||!Number.isSafeInteger(value.source.revision)||Number(value.source.revision)<1||!object(value.anchor)||!keys(value.anchor,'offset,paragraphId')||!id(value.anchor.paragraphId)||!Number.isSafeInteger(value.anchor.offset)||Number(value.anchor.offset)<0||!Number.isSafeInteger(value.seconds)||Number(value.seconds)<1||Number(value.seconds)>60||typeof value.recordedAt!=='string'||value.recordedAt.length>40||!/^\d{4}-\d{2}-\d{2}T/.test(value.recordedAt)||!Number.isFinite(Date.parse(value.recordedAt))||!calendarDay(value.localDate))fail()
}
export function validateReadingGoal(value:unknown,documentId?:string):asserts value is ReadingGoal {if(!object(value)||!keys(value,'assetType,minutes')||value.assetType!=='reading-goal'||!Number.isSafeInteger(value.minutes)||Number(value.minutes)<5||Number(value.minutes)>240||documentId!==undefined&&documentId!=='settings:reading-goal')fail()}
export function activityReferences(kind:DomainKind,value:Record<string,unknown>){if(kind!=='settings'||value.assetType!=='reading-event')return [];const event=value as unknown as ReadingEvent;return [{id:event.storyId,kind:'story' as const},{...event.source},{id:event.anchor.paragraphId,kind:'paragraph' as const}]}
export function validateActivityRelations(kind:DomainKind,value:Record<string,unknown>,lookup:(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined){
 if(kind!=='settings'||value.assetType!=='reading-event')return
 const event=value as unknown as ReadingEvent,line=lookup(event.source.id,event.source.revision),para=lookup(event.anchor.paragraphId),v=line?.value as {storyId?:string;paragraphIds?:string[]}|undefined,text=(para?.value as {text?:string})?.text
 if(line?.kind!==event.source.kind||line.revision!==event.source.revision||event.source.kind==='story'&&event.source.id!==event.storyId||event.source.kind==='branch'&&v?.storyId!==event.storyId||!v?.paragraphIds?.includes(event.anchor.paragraphId)||para?.kind!=='paragraph'||typeof text!=='string'||event.anchor.offset>text.length||unicodeOffset(text,event.anchor.offset)!==event.anchor.offset)fail()
}
export function validateActivityUpdate(previous:Document,value:unknown){if(canonical(previous.value)!==canonical(value))throw new ContractError('READING_ACTIVITY_IMMUTABLE','已保存的阅读时间不能改写')}
/** Monotonic elapsed time, with suspension gaps and the idle tail excluded. */
export class ReadingMeter {
 private last:number;private interaction:number;private milliseconds=0
 constructor(now:number){this.last=now;this.interaction=now}
 touch(now:number){this.interaction=now}
 reset(now:number){this.last=now;this.interaction=now}
 sample(now:number,eligible:boolean){const previous=this.last,delta=now-previous;this.last=now;if(!Number.isFinite(now)||delta<=0||delta>2500||!eligible)return 0;const active=Math.max(0,Math.min(now,this.interaction+120000)-previous);this.milliseconds+=active;return active}
 takeSeconds(){const seconds=Math.min(60,Math.floor(this.milliseconds/1000));this.milliseconds-=seconds*1000;return seconds}
 get seconds(){return Math.floor(this.milliseconds/1000)}
}
export function readingSummary(events:Document<ReadingEvent>[],today=new Date()){
 const totals=new Map<string,number>(),books=new Set<string>();let seconds=0
 for(const row of events){validateReadingEvent(row.value,row.id);seconds+=row.value.seconds;totals.set(row.value.localDate,(totals.get(row.value.localDate)||0)+row.value.seconds);books.add(row.value.storyId)}
 const week=Array.from({length:7},(_,i)=>{const day=new Date(today);day.setDate(day.getDate()+i-6);const date=localDay(day);return {date,seconds:totals.get(date)||0}})
 const todayDate=localDay(today);let streak=0,day=new Date(today);if(!(totals.get(todayDate)||0))day.setDate(day.getDate()-1)
 while((totals.get(localDay(day))||0)>0){streak++;day.setDate(day.getDate()-1)}
 let longestStreak=0,run=0,last=NaN;for(const date of [...totals.keys()].sort()){const ordinal=Date.parse(date+'T12:00:00Z')/86400000;run=ordinal===last+1?run+1:1;last=ordinal;longestStreak=Math.max(longestStreak,run)}
 return {seconds,todaySeconds:totals.get(todayDate)||0,week,streak,longestStreak,books:books.size,eventCount:events.length,badges:{first:seconds>=60,hour:seconds>=3600,threeDays:longestStreak>=3}}
}
