import {it,expect} from 'vitest'
import {reduceDurable,streamSnapshot} from './durable-stream'
const initial=()=>streamSnapshot('task',1,{contentId:'c',chapterIndex:1,streamVersion:1,snapshotVersion:1,text:'你😀',durability:'journaled'})
const event=(payload:Record<string,unknown>={},seq=2)=>({taskId:'task',seq,type:'text_delta',payload:{contentId:'c',chapterIndex:1,streamVersion:1,startUtf16:3,delta:'好',durability:'journaled',...payload}})
it('UTF16 delta appends once and duplicate tuple does not duplicate text',()=>{const first=reduceDurable(initial(),event());expect(first.resync).toBe(false);expect(first.state.content?.text).toBe('你😀好');expect(reduceDurable(first.state,event()).state).toBe(first.state)})
it('gap requires GET and retains visible text',()=>{const state=initial(),r=reduceDurable(state,event({},4));expect(r.resync).toBe(true);expect(r.state).toBe(state)})
it('epoch change requires an explicit snapshot before replacement',()=>{const r=reduceDurable(initial(),event({streamVersion:2,startUtf16:0,delta:'改写'}));expect(r.resync).toBe(true);expect(r.state.content?.text).toBe('你😀')})
it('unknown content cannot overwrite current chapter',()=>{expect(reduceDurable(initial(),event({contentId:'other'})).resync).toBe(true)})
it('a Python-character offset is rejected for emoji',()=>{expect(reduceDurable(initial(),event({startUtf16:2})).resync).toBe(true)})
it('lone surrogates and private extra fields cannot enter public text',()=>{expect(reduceDurable(initial(),event({delta:'\ud800'})).resync).toBe(true);expect(reduceDurable(initial(),event({privateLedger:'not-public'})).resync).toBe(true)})
it('saved messages are immutable and final notification requires metadata snapshot',()=>{const state=streamSnapshot('task',1,{...initial().content!,durability:'saved'});expect(reduceDurable(state,event()).resync).toBe(true);expect(reduceDurable(initial(),{taskId:'task',seq:2,type:'chapter_saved',payload:{}}).resync).toBe(true)})
it('wrong task and unsafe integer seq reject without pollution',()=>{expect(reduceDurable(initial(),{...event(),taskId:'other'}).resync).toBe(true);expect(reduceDurable(initial(),event({},Number.MAX_SAFE_INTEGER+1)).resync).toBe(true)})
it('legacy notifications advance the cursor without appending arbitrary data',()=>{const state=initial();const r=reduceDurable(state,{taskId:'task',seq:2,type:'stream_updated',payload:{text:'wrong'}});expect(r.resync).toBe(false);expect(r.state.content).toBe(state.content);expect(r.state.seq).toBe(2)})
