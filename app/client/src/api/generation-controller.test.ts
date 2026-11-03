import {beforeEach,it,expect,vi} from 'vitest'
import {createGenerationController} from './generation-controller'
import {proseProtocol,type GenerationIntent,type GenerationRequest,type GenerationSnapshot} from './generation-types'
import {snapshot,chapter,testAnchor} from './generation-test-fixtures'
const mock=vi.hoisted(()=>({api:vi.fn(),cacheGet:vi.fn(),cacheSet:vi.fn(),watch:vi.fn(),auth:0,owner:'owner',base:'https://fixture.invalid',deleted:false,keys:0}))
vi.mock('./index',()=>({api:mock.api,newKey:()=> 'unique-intent-'+(++mock.keys),authGeneration:()=>mock.auth,ApiError:class extends Error{constructor(public code:string,message:string,public status:number){super(message)}}}))
vi.mock('../platform/bridge',()=>({cacheGet:mock.cacheGet,cacheSet:mock.cacheSet,accountId:()=>mock.owner,backendBase:()=>mock.base}))
vi.mock('../platform/book-cache',()=>({isBookDeleted:()=>mock.deleted}))
vi.mock('./generation-watch',()=>({watchGeneration:mock.watch}))
const request:GenerationRequest={protocol:proseProtocol,originAnchor:testAnchor,operation:'continue_from_anchor',chapterCount:3,chapterLength:'standard',instruction:'',profileId:'profile',expectedHeadRevisionId:'base',target:{mode:'new_child_branch',name:'AI续写',branchId:'branch'}}
const receipt={generationId:'batch',taskId:'parent',protocol:proseProtocol}
const deferred=<T>()=>{let resolve!:(v:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve}}
beforeEach(()=>{mock.auth=0;mock.owner='owner';mock.base='https://fixture.invalid';mock.deleted=false;mock.api.mockReset();mock.cacheGet.mockReset().mockResolvedValue(undefined);mock.cacheSet.mockReset().mockResolvedValue(undefined);mock.watch.mockReset().mockReturnValue(vi.fn());mock.api.mockResolvedValue({protocol:proseProtocol,ready:true})})
async function controller(){const c=createGenerationController('book','branch');await c.initialize();return c}
function update(c:ReturnType<typeof createGenerationController>,value:GenerationSnapshot){mock.watch.mock.calls.at(-1)![1](value,()=>true);expect(c.state.value).not.toBeNull()}
it('persists received prose and owner raw receipt before the watcher advances',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request)
  const raw='{"body":"甲😀"}',value=snapshot({chapters:[chapter(1,{rawPresentation:{schema:'prose-presentation-v1',chapterIndex:1,selected:'original',attempts:[{kind:'original',raw,rawHash:'a'.repeat(64),finishReason:null}],presentation:{text:'甲😀',title:'第1章',format:'json',thoughtOmitted:false,pending:false}}})]})
  await mock.watch.mock.calls.at(-1)![1](value,()=>true)
  expect(mock.cacheSet.mock.calls.at(-1)?.[1]).toMatchObject({bookId:'book',protocol:proseProtocol,generationId:'batch',snapshot:{chapters:[{text:'甲😀',rawPresentation:{attempts:[{raw}]}}]}})
  c.dispose()
})
it('restores received bytes before an offline capability GET and issues no creating request',async()=>{
  const saved=snapshot({state:'cancelled',persistenceState:'saved'})
  mock.cacheGet.mockResolvedValue({bookId:'book',generationId:'batch',protocol:proseProtocol,snapshot:saved})
  mock.api.mockRejectedValue(new Error('offline'))
  const c=await controller();expect(c.ready.value).toBe(true);expect(c.supported.value).toBe(true);expect(c.cached.value).toBe(true);expect(c.state.value?.chapters[0].text).toBe('甲😀')
  const calls=mock.api.mock.calls.length;expect(await c.submit(request)).toBe(false);expect(await c.rewrite('新方向')).toBe(false);await c.save();await c.cancel();await c.accept(1)
  expect(mock.api).toHaveBeenCalledTimes(calls);expect(mock.watch).not.toHaveBeenCalled();c.dispose()
})
it('refreshes an offline cache through a pure GET before restoring mutation controls',async()=>{
  mock.cacheGet.mockResolvedValue({bookId:'book',generationId:'batch',snapshot:snapshot()});mock.api.mockRejectedValue(new Error('offline'))
  const c=await controller();mock.api.mockResolvedValueOnce(snapshot({eventCursor:6}));await c.refresh()
  expect(c.cached.value).toBe(false);expect(c.state.value?.eventCursor).toBe(6);expect(mock.api.mock.calls.at(-1)?.[0]).toBe('/generations/batch');expect(mock.api.mock.calls.every(call=>!call[1]?.method)).toBe(true);expect(mock.watch).toHaveBeenCalledTimes(1);c.dispose()
})
it('does not import a cached generation with the wrong owner-scoped book, branch or receipt',async()=>{
  for(const record of [
    {bookId:'other',generationId:'batch',snapshot:snapshot()},
    {bookId:'book',generationId:'different',snapshot:snapshot()},
    {bookId:'book',generationId:'batch',snapshot:snapshot({scopeAnchor:{...testAnchor,branchId:'another'}})},
  ]){
    mock.cacheGet.mockResolvedValue(record);mock.api.mockRejectedValue(new Error('offline'));const c=await controller();expect(c.state.value).toBeNull();expect(c.cached.value).toBe(false);expect(mock.watch).not.toHaveBeenCalled();c.dispose()
  }
})
it('preserves received in-memory bytes when the local snapshot write fails',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);mock.cacheSet.mockRejectedValueOnce(new Error('quota'))
  await mock.watch.mock.calls.at(-1)![1](snapshot(),()=>true)
  expect(c.state.value?.chapters[0].text).toBe('甲😀');expect(c.error.value).toContain('本机副本暂未保存');expect(mock.api.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1);c.dispose()
})
it('drops a late cached snapshot read after an account changes without fetching a capability',async()=>{
  const gate=deferred<unknown>();mock.cacheGet.mockReturnValueOnce(gate.promise);const c=createGenerationController('book','branch'),initializing=c.initialize();mock.owner='different';gate.resolve({bookId:'book',generationId:'batch',snapshot:snapshot()});await initializing
  expect(c.state.value).toBeNull();expect(mock.api).not.toHaveBeenCalled();expect(mock.watch).not.toHaveBeenCalled();c.dispose()
})
it('keeps an earlier received raw separate after text delta and restores both offline',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request)
  const raw='{"body":"甲😀"}',rawPresentation={schema:'prose-presentation-v1' as const,chapterIndex:1,selected:'original' as const,attempts:[{kind:'original' as const,raw,rawHash:'a'.repeat(64),finishReason:null}],presentation:{text:'甲😀',title:'第1章',format:'json' as const,thoughtOmitted:false,pending:false}}
  await mock.watch.mock.calls.at(-1)![1](snapshot({chapters:[chapter(1,{rawPresentation})]}),()=>true)
  await mock.watch.mock.calls.at(-1)![1](snapshot({eventCursor:6,chapters:[chapter(1,{text:'甲😀新到的文字'})]}),()=>true)
  expect(c.state.value?.chapters[0].rawPresentation).toBeUndefined();expect(c.receivedRaw.value[0].rawPresentation.attempts[0].raw).toBe(raw)
  const saved=mock.cacheSet.mock.calls.at(-1)![1];c.dispose();mock.cacheGet.mockResolvedValue(saved);mock.api.mockRejectedValue(new Error('offline'))
  const restored=await controller();expect(restored.state.value?.chapters[0].text).toBe('甲😀新到的文字');expect(restored.receivedRaw.value[0].rawPresentation.attempts[0].raw).toBe(raw);restored.dispose()
})
it('archives an uncertain rewrite before switching task and creates the new task own intent',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({state:'cancelled',persistenceState:'saved'}));mock.api.mockRejectedValueOnce(new Error('lost ACK'));await c.rewrite('OLD_TASK_ONLY')
  const old=c.rewriteIntent.value!;mock.api.mockResolvedValueOnce(snapshot({id:'new-batch',taskId:'new-task',state:'cancelled',persistenceState:'saved'}));await c.open('new-batch')
  expect(c.state.value?.id).toBe('new-batch');expect(c.rewriteIntent.value).toBeNull();expect(c.unconfirmed.value[0].rewriteIntent).toEqual(old);expect(c.unconfirmed.value[0].generationId).toBe('batch')
  mock.api.mockResolvedValueOnce({...receipt,generationId:'new-batch',taskId:'new-task'});await c.rewrite('NEW_TASK_ONLY')
  const posts=mock.api.mock.calls.filter(call=>call[1]?.method==='POST'&&call[0].endsWith('/rewrite'));expect(posts.at(-1)?.[0]).toBe('/generations/new-batch/rewrite');expect(posts.at(-1)?.[1].body.instruction).toBe('NEW_TASK_ONLY');expect(posts.at(-1)?.[1].key).not.toBe(old.key);c.dispose()
})
it('does not switch or post when archiving an old task rewrite fails',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({state:'cancelled',persistenceState:'saved'}));mock.api.mockRejectedValueOnce(new Error('lost ACK'));await c.rewrite('ORIGINAL')
  mock.api.mockResolvedValueOnce(snapshot({id:'new-batch',taskId:'new-task'}));mock.cacheSet.mockRejectedValueOnce(new Error('quota'));const calls=mock.api.mock.calls.filter(call=>call[1]?.method==='POST').length;await c.open('new-batch')
  expect(c.state.value?.id).toBe('batch');expect(c.rewriteIntent.value?.generationId).toBe('batch');expect(mock.api.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(calls);c.dispose()
})
it('keeps an uncertain rewrite bound when reopening the same task',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({state:'cancelled',persistenceState:'saved'}));mock.api.mockRejectedValueOnce(new Error('lost ACK'));await c.rewrite('ORIGINAL')
  const old=c.rewriteIntent.value;mock.api.mockResolvedValueOnce(snapshot({state:'cancelled',persistenceState:'saved'}));await c.open('batch');expect(c.rewriteIntent.value).toEqual(old);expect(c.unconfirmed.value).toHaveLength(0);c.dispose()
})
it('opens imported history by pure GET and rejects save, cancel and rewrite without losing prose',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce({...snapshot({state:'running',persistenceState:'recoverable',eventCursor:0}),importedHistory:true,readOnly:true});await c.open('batch')
  expect(c.active.value).toBe(false);expect(mock.watch).not.toHaveBeenCalled()
  const before=mock.api.mock.calls.length;await c.cancel();await c.save();expect(await c.rewrite('不要重新付费')).toBe(false)
  expect(mock.api).toHaveBeenCalledTimes(before);expect(c.state.value?.chapters[0].text).toBe('甲😀');c.dispose()
})
it('explicitly accepts an imported saved prefix against the current Batch head, without starting observation',async()=>{
  const c=await controller(),saved=(i:number)=>chapter(i,{completeness:'complete',durability:'saved',canAccept:i===2,contentHash:'h'+i})
  const value={...snapshot({state:'historical-unknown',phase:'historical-unknown',providerState:'unknown',persistenceState:'unknown',usageState:'unknown',eventCursor:0,targetBranchId:'fixed-target',lastAcceptedRevisionId:'batch-head-new',acceptedPrefix:1,completedChapters:2,chapters:[saved(1),saved(2),chapter(3)]}),importedHistory:true,readOnly:true}
  mock.api.mockResolvedValueOnce(value);await c.open('batch')
  mock.api.mockResolvedValueOnce({generationId:'batch',acceptedPrefix:2,branchId:'fixed-target',revisionId:'accepted-new'}).mockResolvedValueOnce({...value,acceptedPrefix:2,lastAcceptedRevisionId:'accepted-new'})
  expect(await c.accept(2)).toMatchObject({acceptedPrefix:2});expect(mock.api.mock.calls.find(call=>call[0]==='/generations/batch/accept')?.[1].body).toMatchObject({target:'append',expectedHeadRevisionId:'batch-head-new',prefixCount:2,expectedAcceptedPrefix:1})
  expect(mock.watch).not.toHaveBeenCalled();expect(c.state.value?.acceptedPrefix).toBe(2);c.dispose()
})
it('unknown mounted create queries only an owner scoped receipt and keeps the original input/key',async()=>{
  const intent:GenerationIntent={key:'old-request-key',request:{...request,instruction:'保存的愿望'}};mock.cacheGet.mockResolvedValue({intent});mock.api.mockResolvedValueOnce({protocol:proseProtocol,ready:true}).mockResolvedValueOnce({found:false})
  const c=await controller();expect(c.intent.value?.request.instruction).toBe('保存的愿望');expect(c.error.value).toContain('输入已保留');expect(mock.api.mock.calls.map(call=>call[0])).toEqual(['/generations/capabilities','/generations/request-status?requestId=old-request-key&bookId=book']);expect(mock.api.mock.calls.every(call=>call.length===1)).toBe(true);c.dispose()
})
it('recovers a found receipt without creating another generation',async()=>{
  mock.cacheGet.mockResolvedValue({intent:{key:'old',request}});mock.api.mockResolvedValueOnce({protocol:proseProtocol,ready:true}).mockResolvedValueOnce({found:true,receipt})
  const c=await controller();expect(mock.watch).toHaveBeenCalledTimes(1);expect(c.intent.value?.generationId).toBe('batch');expect(mock.api.mock.calls.every(call=>call.length===1)).toBe(true);c.dispose()
})
it('does not POST after a late cache intent save belongs to another account',async()=>{
  const c=await controller(),gate=deferred<void>();mock.cacheSet.mockReturnValueOnce(gate.promise);const sending=c.submit(request);mock.auth++;gate.resolve();await sending;expect(mock.api).toHaveBeenCalledTimes(1);expect(mock.watch).not.toHaveBeenCalled();c.dispose()
})
it('keeps a confirmed receipt in memory after cache failure and does not POST on explicit revisit',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);mock.cacheSet.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('local write failed'))
  expect(await c.submit(request)).toBe(false);expect(c.intent.value?.generationId).toBe('batch');expect(await c.submit(request)).toBe(true)
  expect(mock.api.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1);expect(mock.watch).toHaveBeenCalledTimes(1);c.dispose()
})
it('reuses the original key only for another explicit submit after an unknown response',async()=>{
  const c=await controller();mock.api.mockRejectedValueOnce(new Error('response lost'));await c.submit(request);mock.api.mockResolvedValueOnce(receipt);await c.submit({...request,instruction:'不能静默替换原请求'})
  const posts=mock.api.mock.calls.filter(call=>call[1]?.method==='POST');expect(posts).toHaveLength(2);expect(posts[0][1].key).toBe(posts[1][1].key);expect(posts[1][1].body.instruction).toBe('');c.dispose()
})
it('keeps recoverable processing occupied and uses only cancel/save endpoints for its recovery',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({state:'failed',persistenceState:'recoverable'}));const calls=mock.api.mock.calls.length
  expect(c.active.value).toBe(true);expect(await c.submit(request)).toBe(false);expect(mock.api.mock.calls).toHaveLength(calls)
  mock.api.mockResolvedValueOnce({}).mockResolvedValueOnce(snapshot({state:'cancelled',persistenceState:'saved',eventCursor:6}));await c.cancel();expect(c.active.value).toBe(false)
  expect(mock.api.mock.calls.at(-2)?.[0]).toBe('/generations/batch/cancel');expect(mock.api.mock.calls.at(-1)?.[0]).toBe('/generations/batch');c.dispose()
})
it('accepts two while the third streams and then appends three against its own last accepted head',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request)
  const saved=(index:number)=>chapter(index,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'hash-'+index}),chapters=[saved(1),saved(2),chapter(3)]
  update(c,snapshot({chapters,completedChapters:2}));mock.api.mockResolvedValueOnce({generationId:'batch',acceptedPrefix:2}).mockResolvedValueOnce(snapshot({chapters,completedChapters:2,acceptedPrefix:2,targetBranchId:'target',lastAcceptedRevisionId:'accepted-two',eventCursor:6}));await c.accept(2)
  update(c,snapshot({chapters:[saved(1),saved(2),saved(3)],completedChapters:3,acceptedPrefix:2,targetBranchId:'target',lastAcceptedRevisionId:'accepted-two',eventCursor:7}));mock.api.mockResolvedValueOnce({generationId:'batch',acceptedPrefix:3}).mockResolvedValueOnce(snapshot({chapters:[saved(1),saved(2),saved(3)],completedChapters:3,acceptedPrefix:3,targetBranchId:'target',lastAcceptedRevisionId:'accepted-three',eventCursor:8}));await c.accept(3)
  const accepts=mock.api.mock.calls.filter(call=>call[0]==='/generations/batch/accept');expect(accepts[0][1].body).toMatchObject({prefixCount:2,expectedAcceptedPrefix:0,expectedHeadRevisionId:'base',target:'child'});expect(accepts[1][1].body).toMatchObject({prefixCount:3,expectedAcceptedPrefix:2,expectedHeadRevisionId:'accepted-two',target:'append'});c.dispose()
})
it('does not apply a late saved result after source deletion or route disposal',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);const callback=mock.watch.mock.calls[0][1];mock.deleted=true;callback(snapshot(),()=>true);expect(c.state.value).toBeNull();c.dispose()
})
it('retains complete prose on CAS409 and requires an explicit refresh, not an automatic resubmit',async()=>{
  const {ApiError}=await import('./index'),c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({chapters:[chapter(1,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'h'})],completedChapters:1}))
  mock.api.mockRejectedValueOnce(new ApiError('REVISION_CONFLICT','conflict',409));await c.accept(1);expect(c.conflict.value).toBe(true);expect(c.state.value?.chapters[0].text).toBe('甲😀');expect(mock.api.mock.calls.filter(call=>call[0]==='/generations/batch/accept')).toHaveLength(1);c.dispose()
})
it('reuses an uncertain rewrite key and body even after the user types a different wish',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({state:'cancelled',persistenceState:'saved'}))
  mock.api.mockRejectedValueOnce(new Error('reply lost'));await c.rewrite('原先批准的方向');mock.api.mockResolvedValueOnce(receipt);await c.rewrite('不能偷偷换成新请求')
  const calls=mock.api.mock.calls.filter(call=>call[0]==='/generations/batch/rewrite');expect(calls).toHaveLength(2);expect(calls[0][1].key).toBe(calls[1][1].key);expect(calls[1][1].body.instruction).toBe('原先批准的方向');c.dispose()
})
it('loads a historical version without replacing the current stream or issuing any action',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({candidateVersion:2}));mock.api.mockResolvedValueOnce(snapshot({candidateVersion:1,chapters:[chapter(1,{text:'原候选正文'})]}));await c.selectVersion(1)
  expect(c.preview.value?.chapters[0].text).toBe('原候选正文');expect(c.state.value?.candidateVersion).toBe(2);expect(mock.api.mock.calls.at(-1)?.[1]).toBeUndefined();c.dispose()
})
it('drops an older historical GET after the user selects the current version',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);update(c,snapshot({candidateVersion:2}));const old=deferred<GenerationSnapshot>();mock.api.mockReturnValueOnce(old.promise);const selecting=c.selectVersion(1);await c.selectVersion(2);old.resolve(snapshot({candidateVersion:1}));await selecting;expect(c.preview.value).toBeNull();c.dispose()
})
it('persists an unknown request before clearing the active draft, and restores it by pure lookup',async()=>{
  const c=await controller();mock.api.mockRejectedValueOnce(new Error('create response lost'));await c.submit(request);const originalKey=c.intent.value!.key;await c.newDraft();expect(c.intent.value).toBeNull();expect(c.unconfirmed.value.map(r=>r.key)).toContain(originalKey)
  const persisted=mock.cacheSet.mock.calls.find(call=>call[0]==='generation-unconfirmed:branch');expect(persisted?.[1].records[0].intent.request.instruction).toBe('')
  mock.api.mockResolvedValueOnce({found:false});await c.restoreUnconfirmed(originalKey);expect(c.intent.value?.key).toBe(originalKey);expect(mock.api.mock.calls.at(-1)?.[1]).toBeUndefined();expect(mock.api.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1);c.dispose()
})
it('reopens a confirmed target branch against the authorized snapshot while preserving the original scope',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(snapshot({targetBranchId:'branch',scopeAnchor:{...testAnchor,branchId:'original-source-line'}}));await c.open('batch');expect(c.state.value?.scopeAnchor.branchId).toBe('original-source-line');expect(mock.watch).toHaveBeenCalledTimes(1);expect(mock.api.mock.calls.at(-1)?.[1]).toBeUndefined();c.dispose()
})
it('accepts a natural-tail first prefix into its initial source target, but an explicit pinned first prefix into a child',async()=>{
  const saved=chapter(1,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'h'}),c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit({...request,target:{mode:'append_existing',name:'原线',branchId:'branch'}})
  update(c,snapshot({targetBranchId:'branch',chapters:[saved],completedChapters:1}));mock.api.mockResolvedValueOnce({generationId:'batch',acceptedPrefix:1}).mockResolvedValueOnce(snapshot({targetBranchId:'branch',chapters:[saved],completedChapters:1,acceptedPrefix:1,eventCursor:6}));await c.accept(1)
  expect(mock.api.mock.calls.find(call=>call[0]==='/generations/batch/accept')?.[1].body).toMatchObject({target:'append',sourcePolicy:'requireUnchanged'});c.dispose()
  const pinned=await controller();mock.api.mockResolvedValueOnce(receipt);await pinned.submit({...request,target:{mode:'append_existing',name:'原线',branchId:'branch'}});update(pinned,snapshot({targetBranchId:'branch',chapters:[saved],completedChapters:1}));mock.api.mockResolvedValueOnce({generationId:'batch',acceptedPrefix:1}).mockResolvedValueOnce(snapshot({targetBranchId:'new-child',chapters:[saved],completedChapters:1,acceptedPrefix:1,eventCursor:6}));await pinned.accept(1,true)
  expect(mock.api.mock.calls.filter(call=>call[0]==='/generations/batch/accept').at(-1)?.[1].body).toMatchObject({target:'child',sourcePolicy:'forkPinned'});pinned.dispose()
})
it('keeps an accepted prefix on its fixed target even when pinned is requested again',async()=>{
  const c=await controller();mock.api.mockResolvedValueOnce(receipt);await c.submit(request);const saved=(i:number)=>chapter(i,{completeness:'complete',durability:'saved',canAccept:true,contentHash:'h'+i})
  update(c,snapshot({targetBranchId:'target',acceptedPrefix:1,lastAcceptedRevisionId:'accepted-one',chapters:[saved(1),saved(2)],completedChapters:2}));mock.api.mockResolvedValueOnce({generationId:'batch',acceptedPrefix:2}).mockResolvedValueOnce(snapshot({targetBranchId:'target',acceptedPrefix:2,lastAcceptedRevisionId:'accepted-two',chapters:[saved(1),saved(2)],completedChapters:2,eventCursor:6}));await c.accept(2,true)
  expect(mock.api.mock.calls.find(call=>call[0]==='/generations/batch/accept')?.[1].body).toMatchObject({target:'append',sourcePolicy:'requireUnchanged',expectedHeadRevisionId:'accepted-one'});c.dispose()
})
