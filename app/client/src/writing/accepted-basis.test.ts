import {describe,it,expect} from 'vitest'
import {sha256,canonical,type Document,type Paragraph} from '../domain/local-contract'
import {collectAcceptedBasis} from './accepted-basis-source'
import {acceptedBasisSourceBytes,replayAcceptedBasis,rebaseAcceptedBasis} from './accepted-basis'
import {createAcceptedProof} from './accepted-proof'
import {eventsFromProposals,eventSchema,eventCanonical} from './events'
import {eventWritingPrompt} from './event-prompt'
import {validateUpdate} from '../domain/mutation-policy'
import {writingEventRelations} from './event-relations'
import {acceptedCandidateState} from './accepted-candidate-state'
import type {LocalVault} from '../repositories/local-vault'
import type {WritingRun} from './contract'
const at='2026-10-06T00:00:00.000Z'
function base():WritingRun {return {assetType:'writing-run',storyId:'story',storyRevision:1,parentBranchId:'branch',parentRevision:1,anchor:{storyId:'story',branchId:'branch',paragraphId:'p1',offset:17},prefixChapters:[{id:'c1',title:'灯下',paragraphIds:['p1']}],prefixParagraphId:null,targetBranchId:'new-branch',targetRevision:0,append:false,direction:'',chapterCount:1,length:500,style:'',forbidden:'',authorNote:'',configSnapshot:{id:'model:test',revision:1,protocol:'chat-completions',baseUrl:'http://127.0.0.1:18999/v1',modelId:'synthetic',key:'',contextLimit:64000,outputLimit:3000},timeouts:{firstPacketMs:5000,idleMs:5000,totalMs:20000},dailyCalls:30,chapters:[{id:'candidate-c',title:'下一章',paragraphIds:[],text:'',status:'queued',attemptId:null,request:[],providerRequestId:null,finishReason:null,usage:{input:null,output:null},warnings:[]}],status:'queued',ownerId:null,leaseUntil:null,acceptedCount:0,acceptedRevision:0,historical:false,createdAt:at,updatedAt:at,error:null,eventSchema,eventPrompt:eventWritingPrompt,eventSubjects:[]}}
async function fixture(){
 const rows=new Map<string,Document>(),readIds:string[]=[],put=(id:string,kind:Document['kind'],value:unknown,revision=1)=>{const row={id,kind,value,revision,updatedAt:at};rows.set(id+':'+revision,row);rows.set(id,row);return row}
 const source='林渡是摆渡人。',text='第一章 灯下\n林渡把蓝信收在木箱里🙂。\n\n林渡擦干了手。',proof=await createAcceptedProof(text,['p1','p2'])
 put('source','paragraph',{id:'source',chapterId:'source-chapter',text:source,textHash:await sha256(source),sourceStart:0,sourceEnd:source.length})
 const character={assetType:'novel-character',name:'林渡',primary:{id:'source',revision:1,offset:0,quote:'林渡'},scope:{storyId:'story',branchId:null,anchor:{storyId:'story',branchId:null,paragraphId:'source',offset:source.length},sources:[{id:'source',revision:1,text:source}]},facts:[],aliases:[]}
 put('person','settings',character)
 for(const [i,p] of proof.segments.entries())put(p.paragraphId,'paragraph',{id:p.paragraphId,chapterId:'c1',text:p.text,textHash:p.paragraphHash,sourceStart:i*100,sourceEnd:i*100+p.text.length,writingRunId:'writing-run:origin'})
 const ledger=await eventsFromProposals([{kind:'item',subjectId:'person',certainty:'narrated',confidence:900,statement:'蓝信收在木箱里',quote:proof.segments[0]!.text,change:{item:'蓝信',fromHolderId:null,toHolderId:'person',location:'木箱'}}],text,1,new Set(['person']))
 const origin:WritingRun={...base(),acceptedCount:1,acceptedRevision:1,status:'completed' as const,eventSubjects:[{id:'person',revision:1,name:'林渡'}],chapters:[{id:'c1',title:'灯下',paragraphIds:['p1','p2'],text,status:'complete' as const,attemptId:'a',request:[],providerRequestId:null,finishReason:'stop',usage:{input:null,output:null},warnings:[],eventLedger:ledger,acceptedProof:proof}]}
 put('writing-run:origin','settings',origin,3)
 const run=base();run.prefixChapters=[{id:'source-chapter',title:'起点',paragraphIds:['source']},{id:'c1',title:'灯下',paragraphIds:['p1','p2']}];run.anchor.paragraphId='p2';run.anchor.offset=proof.segments[1]!.text.length
 put('branch','branch',{storyId:'story',paragraphIds:['source','p1','p2']})
 const lookup=(id:string,revision?:number)=>rows.get(revision===undefined?id:id+':'+revision)
 const vault={read:async(id:string,revision?:number)=>{readIds.push(id+(revision?':'+revision:''));return lookup(id,revision)},list:()=>{throw new Error('No broad vault scan permitted')}} as unknown as LocalVault
 return {rows,put,lookup,vault,readIds,run,origin,proof,text}
}
describe('frozen accepted-story basis',()=>{
 it('collects exact accepted origins only and proves inherited people from a visible occurrence',async()=>{
  const f=await fixture(),before=canonical([...f.rows]);const {basis,conditions}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis
  expect(basis.origins).toEqual([{runId:'writing-run:origin',runRevision:3,through:1,eventsAvailable:true}]);expect(basis.events).toHaveLength(1);expect(basis.subjects).toHaveLength(1);expect(basis.state.items['蓝信']!.holderId).toBe(basis.subjects[0]!.canonicalId);expect(conditions).toEqual([{kind:'settings',id:'writing-run:origin',revision:3}]);expect(canonical([...f.rows])).toBe(before)
  await acceptedBasisSourceBytes(f.run,f.lookup);f.run.eventSubjects=[{id:'person',revision:1,name:'林渡'}];writingEventRelations(f.run,f.lookup)
  expect(f.readIds.some(id=>id.includes('future'))).toBe(false)
 })
 it('excludes a partial Unicode quote and preserves exact nested paragraph lineage',async()=>{
  const f=await fixture(),p=f.proof.segments[0]!,cut=p.text.indexOf('🙂')+2,fragment={id:'fragment',chapterId:'c1',text:p.text.slice(0,cut),textHash:await sha256(p.text.slice(0,cut)),sourceStart:0,sourceEnd:cut,forkOf:{paragraphId:'p1',offset:cut}}
  f.run.prefixChapters=[f.run.prefixChapters[0]!,{id:'c1',title:'灯下',paragraphIds:['fragment']}];f.run.prefixParagraphId='fragment';f.run.anchor.paragraphId='p1';f.run.anchor.offset=cut
  const {basis}=await collectAcceptedBasis(f.vault,f.run,new Map([['fragment',fragment as Paragraph]]));expect(basis.events).toEqual([]);expect(basis.prefix[1]!.lineage).toEqual(['p1']);f.put('fragment','paragraph',fragment);f.run.acceptedBasis=basis;await acceptedBasisSourceBytes(f.run,f.lookup)
 })
 it('does not rescan later origin revisions or expose chapters accepted after the anchor',async()=>{
  const f=await fixture(),{basis}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis
  f.put('writing-run:origin','settings',{...f.origin,direction:'FUTURE_SECRET'},4)
  await acceptedBasisSourceBytes(f.run,f.lookup);expect(basis.sources.map(s=>s.contentId)).toEqual(['c1']);expect(eventCanonical(basis)).not.toContain('FUTURE_SECRET');expect(basis.origins[0]!.runRevision).toBe(3)
 })
 it('preserves usable old prose without inventing events for a legacy origin',async()=>{
  const f=await fixture();delete f.origin.eventSchema;delete f.origin.eventSubjects;delete f.origin.eventPrompt;delete f.origin.chapters[0]!.eventLedger;delete f.origin.chapters[0]!.acceptedProof
  const {basis}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis;expect(basis.status).toBe('partial');expect(basis.warnings).toEqual(['SOURCE_EVENTS_UNAVAILABLE']);expect(basis.sources).toEqual([]);expect(basis.prefix).toHaveLength(3);await acceptedBasisSourceBytes(f.run,f.lookup)
 })
 it('rejects forged scopes, source receipts, identity occurrences, state and bytes before writes',async()=>{
  const f=await fixture(),{basis}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis
  for(const mutate of [(b:typeof basis)=>{b.scope.parentRevision++},(b:typeof basis)=>{b.origins[0]!.through=2},(b:typeof basis)=>{b.sources[0]!.contentId='other'},(b:typeof basis)=>{b.events[0]!.entry.statement='forged'},(b:typeof basis)=>{b.subjects[0]!.primary.offset++},(b:typeof basis)=>{b.state.items['蓝信']!.location='future'}]){const run=structuredClone(f.run);mutate(run.acceptedBasis!);expect(()=>replayAcceptedBasis(run,f.lookup)).toThrow()}
  for(const mutate of [(b:typeof basis)=>{b.hash='0'.repeat(64)},(b:typeof basis)=>{b.sources[0]!.ledgerHash='0'.repeat(64)},(b:typeof basis)=>{b.sources[0]!.projectionHash='0'.repeat(64)},(b:typeof basis)=>{b.subjects[0]!.canonicalId='reader-proof:'+'0'.repeat(64);b.state.items['蓝信']!.holderId=b.subjects[0]!.canonicalId}]){const run=structuredClone(f.run);mutate(run.acceptedBasis!);await expect(acceptedBasisSourceBytes(run,f.lookup)).rejects.toThrow()}
 })
 it('keeps the whole basis immutable through status changes, including deletion and retrofitting',async()=>{
  const f=await fixture(),{basis}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis;const previous={id:'writing-run:new',kind:'settings' as const,revision:1,updatedAt:at,value:f.run}
  validateUpdate('settings',previous,structuredClone(f.run));const deleted=structuredClone(f.run);delete deleted.acceptedBasis;expect(()=>validateUpdate('settings',previous,deleted)).toThrow();expect(()=>validateUpdate('settings',{...previous,value:deleted},f.run)).toThrow()
 })
 it('combines an actual new handoff with the accepted holder using explicit identity proof',async()=>{
  const f=await fixture(),{basis}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis;f.run.eventSubjects=[{id:'person',revision:1,name:'林渡'}];const text='林渡把蓝信放到岸上。',ledger=await eventsFromProposals([{kind:'item',subjectId:'person',certainty:'narrated',confidence:900,statement:text,quote:text,change:{item:'蓝信',fromHolderId:'person',toHolderId:null,location:'岸上'}}],text,1,new Set(['person']))
  f.run.chapters=[{...f.origin.chapters[0]!,id:'new-c',text,eventLedger:ledger,paragraphIds:[]}];const state=acceptedCandidateState(f.run,1);expect(state.items['蓝信']!.location).toBe('岸上');expect(state.hints).toEqual([]);expect(state.eventOrder).toHaveLength(2)
 })
 it('rebuilds migrated typed references and hashes while every source sentence stays literal',async()=>{
  const f=await fixture(),{basis}=await collectAcceptedBasis(f.vault,f.run);f.run.acceptedBasis=basis;const before=basis.sources[0]!.ledgerHash;await rebaseAcceptedBasis(f.run,f.lookup);expect(basis.sources[0]!.ledgerHash).toBe(before);await acceptedBasisSourceBytes(f.run,f.lookup);expect(f.origin.chapters[0]!.text).toBe(f.text)
 })
})
