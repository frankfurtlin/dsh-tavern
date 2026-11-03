// Public, committed event envelopes only. Gaps/epochs/unknown shapes require GET.
export interface DurableContent {contentId:string;chapterIndex:number;streamVersion:number;snapshotVersion:number;text:string;durability:'journaled'|'saved';contentHash?:string}
export interface DurableEvent {taskId:string;seq:number;type:string;payload:unknown}
export interface DurableState {taskId:string;seq:number;content:DurableContent|null}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const positive=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>0
export function validUtf16(s:string){for(let i=0;i<s.length;i++){const c=s.charCodeAt(i);if(c>=0xd800&&c<=0xdbff){const next=s.charCodeAt(++i);if(!(next>=0xdc00&&next<=0xdfff))return false}else if(c>=0xdc00&&c<=0xdfff)return false}return true}
export function streamSnapshot(taskId:string,seq:number,stream:unknown):DurableState{
  let content:DurableContent|null=null
  if(object(stream)&&typeof stream.contentId==='string'&&stream.contentId&&positive(stream.chapterIndex)&&positive(stream.streamVersion)&&positive(stream.snapshotVersion)&&typeof stream.text==='string'&&validUtf16(stream.text)&&['saved','journaled'].includes(String(stream.durability)))content={contentId:stream.contentId,chapterIndex:stream.chapterIndex,streamVersion:stream.streamVersion,snapshotVersion:stream.snapshotVersion,text:stream.text,durability:stream.durability as DurableContent['durability'],...(typeof stream.contentHash==='string'?{contentHash:stream.contentHash}:{})}
  return {taskId,seq:Number.isSafeInteger(seq)&&seq>=0?seq:0,content}
}
export function reduceDurable(state:DurableState,input:unknown):{state:DurableState;resync:boolean}{
  const reset=()=>({state,resync:true})
  if(!object(input)||input.taskId!==state.taskId||!positive(input.seq)||typeof input.type!=='string')return reset()
  if(input.seq<=state.seq)return{state,resync:false}
  if(input.seq!==state.seq+1||!object(input.payload))return reset()
  const payload=input.payload
  if(input.type==='text_delta'){
    const keys=['contentId','chapterIndex','streamVersion','startUtf16','delta','durability']
    if(Object.keys(payload).length!==keys.length||!keys.every(k=>k in payload)||typeof payload.contentId!=='string'||!payload.contentId||!positive(payload.chapterIndex)||!positive(payload.streamVersion)||!Number.isSafeInteger(payload.startUtf16)||Number(payload.startUtf16)<0||typeof payload.delta!=='string'||!validUtf16(payload.delta)||payload.durability!=='journaled')return reset()
    const old=state.content
    if(old&&(old.contentId!==payload.contentId||old.chapterIndex!==payload.chapterIndex||old.streamVersion!==payload.streamVersion||old.durability==='saved'))return reset()
    if(payload.startUtf16!==(old?.text.length||0))return reset()
    const content:DurableContent={contentId:payload.contentId,chapterIndex:payload.chapterIndex,streamVersion:payload.streamVersion,snapshotVersion:(old?.snapshotVersion||0)+1,text:(old?.text||'')+payload.delta,durability:'journaled'}
    return{state:{...state,seq:input.seq,content},resync:false}
  }
  if(['terminal','chapter_saved','content_hint'].includes(input.type)||input.type==='phase'&&payload.phase==='snapshot_replaced')return reset()
  if(!['phase','state','usage','usage_updated','stream_updated','phase_changed','turn_started','turn_committed','chapter_ready'].includes(input.type))return reset()
  return{state:{...state,seq:input.seq},resync:false}
}
