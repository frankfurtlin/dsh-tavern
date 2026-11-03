import {it,expect} from 'vitest'
import {parseAcceptedEventContext,type AcceptedEventContext} from './accepted-context'
import {parseGenerationSnapshot,mergeGenerationSnapshot} from '../api/generation-stream'
import {snapshot,chapter,testAnchor} from '../api/generation-test-fixtures'
const anchor={...testAnchor,blockId:'received-block',chapterId:'received-chapter',quoteHash:'a'.repeat(64)}
function context():AcceptedEventContext{
 const quote='林渡把信放在岸上。'
 return {schema:'accepted-event-context-v1',basisHash:'b'.repeat(64),scope:{...anchor},sourceCount:1,originCount:1,prefixCount:1,status:'complete',warnings:[],paragraphChapters:[{paragraphId:'received-block',chapterId:'received-chapter'}],subjects:[],events:[{
  ref:{runId:'old-run',contentId:'received-content',localEventId:'c1:e2'},dependencies:[],frameRef:null,temporalFrame:'present',
  entry:{id:'c1:e2',kind:'item',subjectId:null,certainty:'narrated',confidence:900,statement:'信在岸上。',quote,start:0,end:quote.length,offsetUnit:'utf16',change:{item:'信',fromHolderId:null,toHolderId:null,location:'岸上'}},
  evidence:[{paragraphId:'received-block',paragraphRevision:1,start:0,end:quote.length,quote}]}]}
}
it('retains clipped source event numbering and independently copies only the bounded public context',()=>{
 const input=context(),parsed=parseAcceptedEventContext(input);expect(parsed.events[0].ref.localEventId).toBe('c1:e2');expect(parsed).toEqual(input)
 parsed.events[0].entry.statement='only parsed copy';expect(input.events[0].entry.statement).toBe('信在岸上。')
})
it.each(['unknown-field','scope','dependency','frame','event-id','evidence','duplicates','status','warning','chapter-map','missing-evidence-map','missing-anchor-map','wrong-anchor-chapter'])('rejects malformed public metadata %s without treating it as a source proof',fault=>{
 const input=context() as any
 if(fault==='unknown-field')input.privateJournal='must not copy'
 if(fault==='scope')input.scope={...anchor,start:10,end:2}
 if(fault==='dependency')input.events[0].dependencies=[{runId:'other-run',contentId:'other',localEventId:'c1:e1'}]
 if(fault==='frame')input.events[0].frameRef=input.events[0].ref
 if(fault==='event-id')input.events[0].ref.localEventId='c1:e1'
 if(fault==='evidence')input.events[0].evidence[0].end++
 if(fault==='duplicates')input.events.push(structuredClone(input.events[0]))
 if(fault==='status')input.status='partial'
 if(fault==='warning')input.warnings=[{bad:'unhashable'}]
 if(fault==='chapter-map')input.paragraphChapters.push(structuredClone(input.paragraphChapters[0]))
 if(fault==='missing-evidence-map'){input.events[0].evidence[0].paragraphId='unmapped-source'}
 if(fault==='missing-anchor-map')input.scope.blockId='unmapped-anchor'
 if(fault==='wrong-anchor-chapter')input.paragraphChapters[0].chapterId='other-chapter'
 expect(()=>parseAcceptedEventContext(input)).toThrow()
})
it('keeps raw and acceptable prose when a source context is malformed or belongs to another anchor',()=>{
 const good=snapshot({scopeAnchor:anchor,completedChapters:1,chapters:[chapter(1,{text:'收到的完整正文。',completeness:'complete',durability:'saved',contentHash:'c'.repeat(64),canAccept:true})]})
 for(const basis of [{...context(),privateJournal:'SECRET'},{...context(),scope:{...anchor,bookId:'other-book'}},{...context(),paragraphChapters:[]}]){
  const parsed=parseGenerationSnapshot({...good,acceptedEventBasis:basis});expect(parsed.chapters).toEqual(good.chapters);expect(parsed.acceptedEventBasis).toBeUndefined();expect(parsed.acceptedBasisUnavailable).toBe(true);expect(JSON.stringify(parsed)).not.toContain('SECRET')
 }
})
it('missing prior event metadata is soft unavailable and preserves all received chapters',()=>{
 const text='林渡回到灯下。',quote='前一夜。',ledger={schema:'prose-events-v1',sourceHash:'d'.repeat(64),chapterIndex:2,status:'model-proposed',events:[{
  id:'c2:e1',kind:'time',subjectId:null,certainty:'narrated',confidence:900,statement:quote,quote,start:0,end:quote.length,offsetUnit:'utf16',change:{label:'前一夜',relation:'before',relativeTo:'c1:e1'}}]}
 const input=snapshot({chapters:[chapter(1,{text}),chapter(2,{text:quote,eventLedger:ledger as any})]}),parsed=parseGenerationSnapshot(input)
 expect(parsed.chapters.map(c=>c.text)).toEqual([text,quote]);expect(parsed.chapters[1].eventLedger).toEqual(ledger);expect(parsed.eventStateStatus).toBe('unavailable');expect(input.chapters[0].hints).toEqual([])
 expect(parsed.chapters.every(c=>c.hints.some(h=>h.code==='CHECK_UNAVAILABLE'))).toBe(true)
})
it('invalid individual event quotes retain prose and replace only the unavailable metadata with a hint',()=>{
 const ledger={schema:'prose-events-v1',sourceHash:'d'.repeat(64),chapterIndex:1,status:'model-proposed',events:[context().events[0].entry]}
 const input=snapshot({chapters:[chapter(1,{eventLedger:ledger as any})]}),parsed=parseGenerationSnapshot(input)
 expect(parsed.chapters[0].text).toBe('甲😀');expect(parsed.chapters[0].eventLedger).toBeUndefined();expect(parsed.eventStateStatus).toBe('unavailable');expect(parsed.chapters[0].hints[0].code).toBe('CHECK_UNAVAILABLE')
})
it('later snapshots cannot replace a frozen basis and old clients may omit it without erasing the known view',()=>{
 const input={...snapshot({scopeAnchor:anchor}),acceptedEventBasis:context()},old=parseGenerationSnapshot(input)
 expect(mergeGenerationSnapshot(old,snapshot({scopeAnchor:anchor,eventCursor:6})).acceptedEventBasis).toEqual(old.acceptedEventBasis)
 expect(()=>mergeGenerationSnapshot(old,{...input,eventCursor:6,acceptedEventBasis:{...context(),basisHash:'e'.repeat(64)}})).toThrow('续写依据')
})
