import {describe,it,expect} from 'vitest'
import {reduceGeneration,mergeGenerationSnapshot,parseGenerationSnapshot,generationFinished,acceptablePrefix} from './generation-stream'
import type {GenerationEvent,GenerationRawPresentation} from './generation-types'
import {snapshot,chapter} from './generation-test-fixtures'
function delta(extra:Partial<GenerationEvent['payload']>={},seq=6):GenerationEvent{return {taskId:'parent',seq,type:'text_delta',payload:{candidateVersion:1,contentId:'content-1',chapterIndex:1,streamVersion:1,startUtf16:3,delta:'乙',durability:'journaled',...extra}}}
const rawReceipt=():GenerationRawPresentation=>({schema:'prose-presentation-v1',chapterIndex:1,selected:'original',attempts:[{kind:'original',raw:'<think>合成分析</think>{"body":"甲😀"}',rawHash:'a'.repeat(64),finishReason:null}],presentation:{text:'甲😀',title:'第1章',format:'json',thoughtOmitted:true,pending:false}})
describe('durable prose event reduction',()=>{
  it('retains only the owner raw receipt DTO and leaves legacy chapters without invented receipts',()=>{
    const raw=rawReceipt(),parsed=parseGenerationSnapshot(snapshot({chapters:[chapter(1,{rawPresentation:raw})]}))
    expect(parsed.chapters[0].rawPresentation).toEqual(raw)
    expect(parsed.chapters[0].rawPresentation).not.toBe(raw)
    expect(parseGenerationSnapshot(snapshot()).chapters[0].rawPresentation).toBeUndefined()
  })
  it.each(['body','title','index','duplicate','unknown','finish','pending'])('rejects an inconsistent received record %s before display or cache',fault=>{
    const raw=rawReceipt(),c=chapter(1,{rawPresentation:raw})
    if(fault==='body')raw.presentation.text+='错'
    else if(fault==='title')raw.presentation.title+='错'
    else if(fault==='index')raw.chapterIndex=2
    else if(fault==='duplicate')raw.attempts.push({...raw.attempts[0]})
    else if(fault==='unknown')(raw.attempts[0] as any).authorization='不能进入DTO'
    else {c.completeness='complete';c.durability='saved';c.contentHash='hash';c.canAccept=true;if(fault==='pending'){raw.attempts[0].finishReason='stop';raw.presentation.pending=true}}
    expect(()=>parseGenerationSnapshot(snapshot({chapters:[c]}))).toThrow('写作记录不完整')
  })
  it('drops a stale raw snapshot on a reading-only delta and restores the authoritative receipt on GET',()=>{
    const old=parseGenerationSnapshot(snapshot({chapters:[chapter(1,{rawPresentation:rawReceipt()})]})),next=reduceGeneration(old,delta()).value
    expect(next.chapters[0].text).toBe('甲😀乙');expect(next.chapters[0].rawPresentation).toBeUndefined()
    expect(old.chapters[0].rawPresentation?.presentation.text).toBe('甲😀')
    const raw=rawReceipt();raw.presentation.text+='乙';raw.attempts[0].raw='{"body":"甲😀乙"}'
    const saved=mergeGenerationSnapshot(next,snapshot({eventCursor:6,chapters:[chapter(1,{text:'甲😀乙',snapshotVersion:2,rawPresentation:raw})]}))
    expect(saved.chapters[0].rawPresentation).toEqual(raw)
  })
  it('keeps the known original receipt of an accepted chapter immutable in later versions',()=>{
    const raw=rawReceipt();raw.attempts[0].finishReason='stop'
    const first=chapter(1,{rawPresentation:raw,completeness:'complete',durability:'saved',contentHash:'body-hash'}),old=parseGenerationSnapshot(snapshot({acceptedPrefix:1,chapters:[first]}))
    const next=snapshot({candidateVersion:2,eventCursor:7,acceptedPrefix:1,chapters:[first]})
    expect(mergeGenerationSnapshot(old,next).candidateVersion).toBe(2)
    const changed=structuredClone(first);changed.rawPresentation!.attempts[0].raw='{"body":"甲😀"}'
    expect(()=>mergeGenerationSnapshot(old,{...next,chapters:[changed]})).toThrow('已保存的原返回')
  })
  it('keeps imported unknown metadata as historical read-only instead of a live task',()=>{
    const input={...snapshot({state:'historical-unknown',phase:'historical-unknown',providerState:'unknown',persistenceState:'unknown',usageState:'unknown',eventCursor:0}),importedHistory:true,readOnly:true}
    const parsed=parseGenerationSnapshot(input)
    expect(parsed).toMatchObject({importedHistory:true,readOnly:true,state:'historical-unknown',persistenceState:'unknown',eventCursor:0})
    expect(generationFinished(parsed)).toBe(true)
    expect(reduceGeneration(parsed,{...delta({},1)}).kind).toBe('ignored')
  })
  it('keeps legacy snapshots usable when the new history flags are absent',()=>{
    const {importedHistory,readOnly,...legacy}=snapshot()
    expect(parseGenerationSnapshot(legacy)).toMatchObject({importedHistory:false,readOnly:false})
    expect(generationFinished(parseGenerationSnapshot(legacy))).toBe(false)
  })
  it('appends only a contiguous UTF16 delta and leaves the input snapshot untouched',()=>{
    const old=snapshot(),result=reduceGeneration(old,delta());expect(result.kind).toBe('applied');expect(result.value.chapters[0].text).toBe('甲😀乙');expect(result.value.eventCursor).toBe(6);expect(old.chapters[0].text).toBe('甲😀')
  })
  it('does not append a duplicate event twice',()=>{const once=reduceGeneration(snapshot(),delta()).value,result=reduceGeneration(once,delta());expect(result.kind).toBe('duplicate');expect(result.value.chapters[0].text).toBe('甲😀乙')})
  it.each([{seq:7},{offset:2},{version:2},{content:'different'},{delta:'\ud83d'}])('requires an authoritative snapshot for an incompatible event %j',bad=>{
    const event=delta({...('offset'in bad?{startUtf16:bad.offset}:{}),...('version'in bad?{streamVersion:bad.version}:{}),...('content'in bad?{contentId:bad.content}:{}),...('delta'in bad?{delta:bad.delta}:{})},'seq'in bad?bad.seq:6)
    const result=reduceGeneration(snapshot(),event);expect(result.kind).toBe('resync');expect(result.value.eventCursor).toBe(5);expect(result.value.chapters[0].text).toBe('甲😀')
  })
  it('ignores another task without moving this cursor',()=>{const result=reduceGeneration(snapshot(),{...delta(),taskId:'other'});expect(result.kind).toBe('ignored');expect(result.value.eventCursor).toBe(5)})
  it('does not accept a chapter from text_delta even when an event claims saved durability',()=>{const result=reduceGeneration(snapshot(),delta({durability:'saved'}));expect(result.kind).toBe('resync');expect(acceptablePrefix(result.value)).toBe(0)})
  it('keeps accepted prose immutable across late delta and replacement snapshots',()=>{
    const old=snapshot({acceptedPrefix:1,completedChapters:1,chapters:[chapter(1,{text:'已正式保存的章',contentHash:'accepted-hash',completeness:'complete',durability:'saved',canAccept:true})]})
    expect(reduceGeneration(old,delta({startUtf16:old.chapters[0].text.length})).kind).toBe('resync')
    expect(()=>mergeGenerationSnapshot(old,{...old,eventCursor:7,chapters:[chapter(1,{text:'被错换的正文'})]})).toThrow()
  })
  it('permits only saved complete continuous prefixes, never skipping a partial chapter',()=>{
    const saved=(index:number)=>chapter(index,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'hash-'+index})
    expect(acceptablePrefix(snapshot({chapters:[saved(1),chapter(2),saved(3)]}))).toBe(1)
    expect(acceptablePrefix(snapshot({chapters:[saved(1),saved(2),saved(3)]}))).toBe(3)
  })
  it('keeps recoverable failure alive while a saved terminal failure ends observation',()=>{expect(generationFinished(snapshot({state:'failed',persistenceState:'recoverable'}))).toBe(false);expect(generationFinished(snapshot({state:'failed',persistenceState:'saved'}))).toBe(true)})
  it('rejects stale snapshots and a reduction of an already accepted prefix',()=>{
    const old=snapshot({eventCursor:10,acceptedPrefix:1});expect(mergeGenerationSnapshot(old,snapshot({eventCursor:9}))).toBe(old)
    expect(()=>mergeGenerationSnapshot(old,snapshot({eventCursor:11,acceptedPrefix:0}))).toThrow()
  })
  it('allows a new candidate version only when the accepted prefix text remains identical',()=>{
    const first=chapter(1,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'h1'}),old=snapshot({acceptedPrefix:1,chapters:[first,chapter(2)],eventCursor:10})
    const newer=snapshot({candidateVersion:2,acceptedPrefix:1,chapters:[first,chapter(2,{streamVersion:2,text:''})],eventCursor:11})
    expect(mergeGenerationSnapshot(old,newer).candidateVersion).toBe(2);expect(()=>mergeGenerationSnapshot(old,{...newer,chapters:[{...first,text:'改掉已接受章'},newer.chapters[1]]})).toThrow()
  })
  it('does not require an already accepted chapter to remain an actionable save',()=>{
    const old=snapshot({acceptedPrefix:1,completedChapters:1,chapters:[chapter(1,{completeness:'complete',durability:'saved',canAccept:false,contentHash:'accepted'})]});expect(parseGenerationSnapshot(old).acceptedPrefix).toBe(1);expect(acceptablePrefix(old)).toBe(1)
  })
  it('projects only public snapshot fields, never a provider checkpoint or prompt',()=>{
    const input={...snapshot(),checkpoint:{systemPrompt:'must not render'},keyCipher:'not a user credential',privateLedger:{text:'must not render'}};const parsed=parseGenerationSnapshot(input)
    expect(JSON.stringify(parsed)).not.toContain('must not render');expect('checkpoint'in parsed).toBe(false);expect('keyCipher'in parsed).toBe(false)
  })
})
