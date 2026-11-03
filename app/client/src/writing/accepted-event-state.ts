/** Replay proven visible events; identity bindings are explicit source occurrences. */
import {canonical,ContractError} from '../domain/local-contract'
import {foldScopedEvents} from './events'
import type {VisibleAcceptedEvent,EventRef} from './accepted-events'
export interface SubjectBinding {runId:string;subjectId:string;canonicalId:string}
const key=(ref:EventRef)=>canonical(ref)
const actorKey=(runId:string,id:string)=>canonical([runId,id])
export function foldVisibleAcceptedEvents(events:VisibleAcceptedEvent[],bindings:SubjectBinding[]){
 const identities=new Map<string,string>()
 for(const binding of bindings){const id=actorKey(binding.runId,binding.subjectId);if(identities.has(id))throw new ContractError('ACCEPTED_EVENTS','人物来源关联重复，原文仍保留');identities.set(id,binding.canonicalId)}
 const state=foldScopedEvents([]),dict=<T>()=>Object.create(null) as Record<string,T>
 // User narrative labels remain literal keys; use null-prototype accumulators.
 state.items=dict();state.locations=dict();state.motivations=dict();state.threads=dict();state.knowledge=dict();state.characters=dict();state.relationships=dict()
 const known=new Set<string>()
 for(const view of events){
  const id=key(view.ref),e=view.entry
  if(known.has(id)||view.dependencies.some(ref=>!known.has(key(ref))))throw new ContractError('ACCEPTED_EVENTS','事件来源引用不闭合，原文仍保留')
  known.add(id);state.eventOrder.push(id)
  if(e.certainty!=='narrated')continue
  const actor=(value:string|null)=>value===null?null:identities.get(actorKey(view.ref.runId,value))||actorKey(view.ref.runId,value)
  const source=(local:string)=>{const ref=view.dependencies.find(ref=>ref.runId===view.ref.runId&&ref.localEventId===local);if(!ref)throw new ContractError('ACCEPTED_EVENTS','事件来源引用缺失，原文仍保留');return key(ref)}
  const hint=(code:string,previousEventId:string)=>state.hints.push({code,eventId:id,previousEventId,severity:'soft'})
  if(e.kind==='time'){state.timeRelations.push({eventId:id,...e.change,relativeTo:e.change.relativeTo===null?null:source(e.change.relativeTo)});continue}
  if(e.kind==='event')state.causalEvents.push({eventId:id,effect:e.change.effect,causedBy:e.change.causedBy.map(source),temporalFrame:view.temporalFrame})
  if(view.temporalFrame==='past'){state.historicalEvents.push(id);continue}
  const subject=actor(e.subjectId)
  if(e.kind==='item'){
   const c=e.change,old=state.items[c.item],from=actor(c.fromHolderId)
   if(old&&old.holderId!==null&&from!==null&&old.holderId!==from)hint('ITEM_TRANSFER_GAP',old.eventId)
   state.items[c.item]={holderId:actor(c.toHolderId),location:c.location,eventId:id}
  }else if(e.kind==='location'&&subject!==null){
   const old=state.locations[subject];if(old&&e.change.from!==null&&old.place!==e.change.from)hint('LOCATION_TRANSITION_GAP',old.eventId)
   state.locations[subject]={place:e.change.to,eventId:id}
  }else if(e.kind==='motivation'&&subject!==null)state.motivations[subject]={...e.change,eventId:id}
  else if(e.kind==='knowledge')for(const original of e.change.learnedByIds){const person=actor(original)!;(state.knowledge[person]??=[]).push({fact:e.change.fact,eventId:id})}
  else if(e.kind==='unresolved'){const old=state.threads[e.change.threadKey];if(old&&old.state==='resolved'&&e.change.state==='advanced')hint('THREAD_REOPENING_UNCLEAR',old.eventId);state.threads[e.change.threadKey]={state:e.change.state,detail:e.change.detail,eventId:id}}
  else if(e.kind==='relationship'&&subject!==null)(state.relationships[subject]??=dict())[actor(e.change.otherId)!]={relation:e.change.relation,eventId:id}
  else if(e.kind==='character')state.characters[subject??id]={...e.change,subjectId:subject,eventId:id}
 }
 const plain=<T>(value:Record<string,T>)=>Object.fromEntries(Object.entries(value))
 return {...state,schema:'accepted-event-state-v1' as const,items:plain(state.items),locations:plain(state.locations),motivations:plain(state.motivations),threads:plain(state.threads),knowledge:plain(state.knowledge),characters:plain(state.characters),relationships:plain(Object.fromEntries(Object.entries(state.relationships).map(([person,others])=>[person,plain(others)])))}
}
