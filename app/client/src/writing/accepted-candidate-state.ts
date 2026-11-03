import {canonical} from '../domain/local-contract'
import {foldVisibleAcceptedEvents,type SubjectBinding} from './accepted-event-state'
import type {VisibleAcceptedEvent,EventRef} from './accepted-events'
import type {WritingRun} from './contract'

/** Candidate IDs remain local to this run; only explicit occurrence proofs join actors. */
export function acceptedCandidateState(run:WritingRun,through:number){
 const basis=run.acceptedBasis!,scope='candidate:'+run.targetBranchId,refs=new Map<string,EventRef>(),frames=new Map<string,boolean>(),views:VisibleAcceptedEvent[]=[]
 const chapters=run.chapters.slice(0,through)
 for(const c of chapters)for(const e of c.eventLedger?.events||[])refs.set(e.id,{runId:scope,contentId:c.id,localEventId:e.id})
 for(const c of chapters){let past=false,frameRef:EventRef|null=null
  for(const e of c.eventLedger?.events||[]){const ref=refs.get(e.id)!,deps:EventRef[]=[]
   if(e.kind==='time'&&e.change.relativeTo!==null)deps.push(refs.get(e.change.relativeTo)!)
   if(e.kind==='event')deps.push(...e.change.causedBy.map(id=>refs.get(id)!))
   let current=frameRef
   if(e.certainty==='narrated'){if(e.kind==='time'&&e.change.relation!=='unknown'){past=e.change.relation==='before'||e.change.relativeTo!==null&&frames.get(canonical(refs.get(e.change.relativeTo)!))===true;frameRef=ref;current=null}frames.set(canonical(ref),past)}
   if(current)deps.push(current)
   views.push({ref,sourceRunRevision:0,sourceHash:c.eventLedger!.sourceHash,ledgerHash:'',entry:e,dependencies:[...new Map(deps.map(d=>[canonical(d),d])).values()],frameRef:current,temporalFrame:past?'past':'present',evidence:[]})
  }
 }
 const bindings:SubjectBinding[]=[...basis.subjects]
 for(const s of run.eventSubjects||[]){const proof=basis.subjects.find(b=>b.subjectId===s.id&&b.name===s.name);if(proof)bindings.push({runId:scope,subjectId:s.id,canonicalId:proof.canonicalId})}
 return foldVisibleAcceptedEvents([...basis.events,...views],bindings)
}
