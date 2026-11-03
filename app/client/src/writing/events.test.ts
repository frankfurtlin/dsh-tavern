import {describe,it,expect} from 'vitest'
import vectors from './__fixtures__/event-vectors.json'
import {sha256} from '../domain/local-contract'
import {eventCanonical,foldEvents,foldScopedEvents,validateEvents,eventsFromProposals,type EventLedger} from './events'
import {receivedProseObject} from './prose'
import {validateSourceBytes} from '../domain/local-validation'
const clone=<T>(v:T):T=>JSON.parse(JSON.stringify(v))
const baseline=(vectors[0] as {ledgers:EventLedger[]}).ledgers
describe('shared Python/TypeScript event contracts',()=>{
 for(const vector of vectors)it(vector.name+' matches exact canonical state and hash',async()=>{
  const state='groups' in vector?foldScopedEvents(vector.groups as {scope:string;ledgers:EventLedger[]}[]):foldEvents(vector.ledgers as EventLedger[])
  expect(eventCanonical(state)).toBe(vector.canonical)
  expect(await sha256(eventCanonical(state))).toBe(vector.sha256)
 })
 it('rejects duplicate or malformed batch namespaces instead of colliding local event IDs',()=>{
  for(const groups of [[{scope:'a',ledgers:[]},{scope:'a',ledgers:[]}],[{scope:' ',ledgers:[]}],[{scope:true,ledgers:[]}],[{scope:'a',ledgers:{}}]])expect(()=>foldScopedEvents(groups as unknown as {scope:string;ledgers:EventLedger[]}[])).toThrow()
 })
 it('does not confuse Unicode offsets or accept nonexistent/forward identities',async()=>{
  const ledger=clone(baseline[0]!) as EventLedger
  validateEvents(ledger,undefined,{subjects:new Set(['lin','mei']),priorIds:[]})
  expect(ledger.events[0]!.end).toBe(12)
  ledger.events[0]!.subjectId='unknown';expect(()=>validateEvents(ledger,undefined,{subjects:new Set(['lin','mei'])})).toThrow()
  const past=clone(baseline[3]!) as EventLedger
  expect(()=>validateEvents(past,undefined,{priorIds:[]})).toThrow()
  expect(()=>foldEvents([past])).toThrow()
 })
 it('rejects coerced enum arrays, changed quotes and invented metadata',()=>{
  for(const [key,value] of [['certainty',['narrated']],['confidence',true],['quote','没有的引文'],['offsetUnit','codepoint']]){
   const ledger=clone(baseline[0]!) as unknown as Record<string,unknown>,e=(ledger.events as Record<string,unknown>[])[0]!;e[String(key)]=value
   expect(()=>validateEvents(ledger,'🙂林渡把蓝信交给梅舟。梅舟从渡口走到南岸。')).toThrow()
  }
 })
 it('extracts only exact unique quotes and marks unsupported metadata unavailable',async()=>{
  const e=baseline[0]!.events[0]!,{id,start,end,offsetUnit,...proposal}=e
  const value=await eventsFromProposals([proposal],'🙂林渡把蓝信交给梅舟。梅舟从渡口走到南岸。',1,new Set(['lin','mei']))
  expect(value.events[0]).toEqual(e)
  expect((await eventsFromProposals([proposal],'🙂林渡把蓝信交给梅舟。重复。🙂林渡把蓝信交给梅舟。',1)).status).toBe('unavailable')
  expect((await eventsFromProposals([proposal],e.quote,1,new Set())).status).toBe('unavailable')
 })
 it('keeps raw metadata separate, detects duplicates and bounds nested decoding',()=>{
  expect(receivedProseObject('<think>分析</think>{"body":"正文。","events":[]}')?.events).toEqual([])
  for(const raw of ['{"body":"正文","events":[],"events":[]}','{"body":"正文","extra":'+ '['.repeat(10000)+'0'+']'.repeat(10000)+'}','{"body":"正文","extra":{"same":1,"same":2}}'])expect(receivedProseObject(raw)).toBeNull()
 })
 it('durable local writes verify event source hash rather than trusting metadata',async()=>{
  const ledger=clone(baseline[0]!) as EventLedger,text='🙂林渡把蓝信交给梅舟。梅舟从渡口走到南岸。'
  await validateSourceBytes('settings',{assetType:'writing-run',chapters:[{text,eventLedger:ledger}]})
  await expect(validateSourceBytes('settings',{assetType:'writing-run',chapters:[{text:text+'改',eventLedger:ledger}]})).rejects.toThrow()
 })
})
