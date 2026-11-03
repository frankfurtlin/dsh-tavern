import {ContractError,sha256} from '../domain/local-contract'
import {projectAcceptedText,type AcceptedTextSegment} from './accepted-projection'

export interface AcceptedParagraphProof extends AcceptedTextSegment {
 paragraphId:string;paragraphRevision:1;paragraphHash:string
}
export interface AcceptedChapterProof {
 schema:'accepted-chapter-proof-v1';style:'local-v1';sourceHash:string;segments:AcceptedParagraphProof[]
}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v)
function fail():never{throw new ContractError('ACCEPTED_PROOF','接受正文的来源凭据不一致，原文仍保留')}
export async function createAcceptedProof(text:string,paragraphIds:string[]):Promise<AcceptedChapterProof>{
 const parts=projectAcceptedText(text,'local-v1').segments
 if(!parts.length||parts.length!==paragraphIds.length||new Set(paragraphIds).size!==paragraphIds.length||paragraphIds.some(id=>typeof id!=='string'||!id||id.length>160))fail()
 return {schema:'accepted-chapter-proof-v1',style:'local-v1',sourceHash:await sha256(text),segments:await Promise.all(parts.map(async(part,index)=>({...part,paragraphId:paragraphIds[index]!,paragraphRevision:1 as const,paragraphHash:await sha256(part.text)})))}
}
/** Recompute the original UTF16 map; hashes are verified separately before storage. */
export function validateAcceptedProof(value:unknown,text:string,paragraphIds:string[]):asserts value is AcceptedChapterProof{
 const expected=projectAcceptedText(text,'local-v1').segments
 if(!record(value)||Object.keys(value).length!==4||value.schema!=='accepted-chapter-proof-v1'||value.style!=='local-v1'||!hash(value.sourceHash)||!Array.isArray(value.segments)||!expected.length||value.segments.length!==expected.length||paragraphIds.length!==expected.length||new Set(paragraphIds).size!==paragraphIds.length)fail()
 for(const [index,part] of value.segments.entries()){
  const wanted=expected[index]!
  if(!record(part)||Object.keys(part).length!==8||Object.entries(wanted).some(([key,item])=>!Object.hasOwn(part,key)||part[key]!==item)||part.paragraphId!==paragraphIds[index]||typeof part.paragraphId!=='string'||!part.paragraphId||part.paragraphId.length>160||part.paragraphRevision!==1||!hash(part.paragraphHash))fail()
 }
}
export async function validateAcceptedProofBytes(proof:AcceptedChapterProof,text:string){
 if(proof.sourceHash!==await sha256(text))fail()
 for(const part of proof.segments)if(part.paragraphHash!==await sha256(part.text))fail()
}
