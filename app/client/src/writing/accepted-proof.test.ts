import {describe,it,expect} from 'vitest'
import {createAcceptedProof,validateAcceptedProof} from './accepted-proof'
import {validateWritingRun,type WritingRun} from './contract'
import {validateSourceBytes} from '../domain/local-validation'
import {validateUpdate} from '../domain/mutation-policy'
import {eventSchema} from './events'
import {eventWritingPrompt} from './event-prompt'
const text='第一章 灯下\n  甲🙂  \n\n\t乙。\t\n'
function run():WritingRun{
 const now='2026-10-06T00:00:00.000Z'
 return {assetType:'writing-run',storyId:'story',storyRevision:1,parentBranchId:null,parentRevision:1,anchor:{storyId:'story',branchId:null,paragraphId:'source',offset:3},prefixChapters:[{id:'source-chapter',title:'原文',paragraphIds:['source']}],prefixParagraphId:null,targetBranchId:'branch',targetRevision:0,append:false,direction:'',chapterCount:1,length:500,style:'',forbidden:'',authorNote:'',configSnapshot:{id:'model:test',revision:1,protocol:'chat-completions',baseUrl:'http://127.0.0.1:18999/v1',modelId:'synthetic',key:'',contextLimit:64000,outputLimit:3000},timeouts:{firstPacketMs:5000,idleMs:5000,totalMs:20000},dailyCalls:30,chapters:[{id:'chapter',title:'灯下',paragraphIds:[],text,status:'complete',attemptId:'attempt',request:[],providerRequestId:null,finishReason:'stop',usage:{input:null,output:null},warnings:[]}],status:'completed',ownerId:null,leaseUntil:null,acceptedCount:0,acceptedRevision:0,historical:false,createdAt:now,updatedAt:now,error:null,eventSchema,eventPrompt:eventWritingPrompt,eventSubjects:[]}
}
describe('accepted paragraph provenance',()=>{
 it('binds original body UTF16 positions to the actual immutable paragraph IDs and bytes',async()=>{
  const proof=await createAcceptedProof(text,['p1','p2'])
  validateAcceptedProof(proof,text,['p1','p2'])
  expect(proof.segments.map(p=>[p.sourceStart,p.sourceEnd,p.targetEnd])).toEqual([[9,12,3],[17,19,2]])
  expect(proof.segments.map(p=>p.paragraphId)).toEqual(['p1','p2'])
  await validateSourceBytes('settings',{assetType:'writing-run',chapters:[{text,acceptedProof:proof}]})
 })
 it('rejects ordinary changed paragraph ID/order/revision/quote/offset/map fields',async()=>{
  const proof=await createAcceptedProof(text,['p1','p2'])
  for(const patch of [{paragraphId:'different'},{paragraphRevision:2},{text:'甲'},{sourceStart:10},{sourceEnd:11},{extra:'invented'}]){const value=structuredClone(proof);Object.assign(value.segments[0]!,patch);expect(()=>validateAcceptedProof(value,text,['p1','p2'])).toThrow()}
  expect(()=>validateAcceptedProof(proof,text,['p2','p1'])).toThrow()
  expect(()=>validateAcceptedProof({...proof,extra:true},text,['p1','p2'])).toThrow()
 })
 it('recomputes source and paragraph hashes before storage',async()=>{
  const proof=await createAcceptedProof(text,['p1','p2'])
  for(const change of [(p:typeof proof)=>{p.sourceHash='0'.repeat(64)},(p:typeof proof)=>{p.segments[0]!.paragraphHash='0'.repeat(64)}]){const value=structuredClone(proof);change(value);await expect(validateSourceBytes('settings',{assetType:'writing-run',chapters:[{text,acceptedProof:value}]})).rejects.toThrow()}
 })
 it('rejects empty, duplicate and mismatched accepted paragraph lists',async()=>{
  for(const ids of [[],['p1'],['same','same'],['','p2']])await expect(createAcceptedProof(text,ids)).rejects.toThrow()
  await expect(createAcceptedProof('  \n',['p1'])).rejects.toThrow()
 })
 it('allows provenance only as part of the first accepted prefix transaction',async()=>{
  const before=run(),after=structuredClone(before);after.acceptedCount=1;after.acceptedRevision=1;after.chapters[0]!.paragraphIds=['p1','p2'];after.chapters[0]!.acceptedProof=await createAcceptedProof(text,['p1','p2'])
  validateWritingRun(after)
  validateUpdate('settings',{id:'writing-run:test',kind:'settings',revision:1,updatedAt:before.updatedAt,value:before},after)
  const notAccepted=structuredClone(after);notAccepted.acceptedCount=0;notAccepted.acceptedRevision=0
  expect(()=>validateWritingRun(notAccepted)).toThrow()
  const legacy=structuredClone(after);delete legacy.chapters[0]!.acceptedProof
  validateWritingRun(legacy)
  expect(()=>validateUpdate('settings',{id:'writing-run:test',kind:'settings',revision:1,updatedAt:before.updatedAt,value:legacy},after)).toThrow()
 })
 it('preserves accepted proofs across ordinary status saves and rejects removing or changing them',async()=>{
  const value=run();value.acceptedCount=1;value.acceptedRevision=1;value.chapters[0]!.paragraphIds=['p1','p2'];value.chapters[0]!.acceptedProof=await createAcceptedProof(text,['p1','p2'])
  const previous={id:'writing-run:test',kind:'settings' as const,revision:2,updatedAt:value.updatedAt,value}
  validateUpdate('settings',previous,structuredClone(value))
  for(const change of [(r:WritingRun)=>{delete r.chapters[0]!.acceptedProof},(r:WritingRun)=>{r.chapters[0]!.acceptedProof!.sourceHash='0'.repeat(64)}]){const next=structuredClone(value);change(next);expect(()=>validateUpdate('settings',previous,next)).toThrow()}
 })
})
