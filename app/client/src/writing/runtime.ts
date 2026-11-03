import {acceptedWritingParagraphs} from './accepted-prose'
import {createAcceptedProof} from './accepted-proof'
import {collectAcceptedBasis} from './accepted-basis-source'
import {acceptedCandidateState} from './accepted-candidate-state'
import {eventCanonical} from './events'
import {eventSchema,eventsFromProposals,foldEvents} from './events'
import {eventWritingPrompt} from './event-prompt'
import {receivedProseObject} from './prose'
import {visibleCharacter,type NovelCharacter} from '../novel-characters/contract'
import {writingPresentation,type WritingPurpose} from './purpose'
import {directionSystemPrompt} from './directions'
import {selectionText,type WritingEdit} from './edits-contract'
import {assessWritingQuality} from './quality'
import {WritingSecretFilter} from './secret-filter'
import {writingPreferenceLayers} from './planning-runtime'
import {resolveWritingPreferences,writingTemplate,type WritingPreferences} from './preferences'
import {canonical,sha256,ContractError,unicodeOffset,type Document,type LocalStory,type Paragraph,type ProgressAnchor,type ProviderConfiguration,type PromptMessage} from '../domain/local-contract'
import {LocalVault,type WriteDocument} from '../repositories/local-vault'
import {prepareTextTask,estimateInput} from '../task-runtime/local-text'
import {streamText,ProviderFailure,type ProviderEvent} from '../provider-adapters/text'
import {completePrefix,prefixAt,type LocalBranch,type WritingRun,type WritingChapter} from './contract'
const clone=<T>(v:T):T=>JSON.parse(canonical(v)),stamp=()=>new Date().toISOString()
const active=new Map<string,LocalWritingExecution>(),pending=new Map<string,{revision:number;value:WritingRun;recoveryValue?:WritingRun}>()
const control=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('reader-local-writing-v1')
;(control as (BroadcastChannel&{unref?:()=>void})|null)?.unref?.()
control?.addEventListener('message',event=>{if(event.data?.type==='stop'&&typeof event.data.vaultId==='string'&&typeof event.data.id==='string')active.get(event.data.vaultId+':'+event.data.id)?.stop()})
if(typeof window!=='undefined')window.addEventListener('reader-local-locked',()=>{for(const execution of active.values())execution.stop();pending.clear()})
async function commit(vault:LocalVault,operation:string,documents:WriteDocument[],conditions:Parameters<LocalVault['commitBundle']>[3]=[]){
  const hash=await sha256(canonical(conditions.length?{documents,preconditions:conditions}:documents))
  try{return await vault.commitBundle(operation,documents,undefined,conditions)}catch(error){if(!vault.unlocked)throw error;const receipt=await vault.receipt(operation);if(receipt?.payloadHash!==hash)throw error;return [receipt]}
}
export function progressKey(storyId:string,branchId:string|null){return 'progress:'+storyId+(branchId?':'+branchId:'')}
export function writingPendingView(vault:LocalVault,row:Document<WritingRun>):Document<WritingRun> {
  if(!vault.unlocked)return row
  const copy=pending.get(vault.id+':'+row.id)
  return copy?{...row,revision:copy.revision,value:clone(copy.value)}:row
}
export function hasPendingWritingSave(vault:LocalVault,id:string){return vault.unlocked&&pending.has(vault.id+':'+id)}
export async function readingLine(vault:LocalVault,storyId:string,branchId:string|null,revision?:number){
  if(revision!==undefined&&(!Number.isSafeInteger(revision)||revision<1))throw new ContractError('LINE_REVISION','历史版本编号无效')
  const storyHead=await vault.read<LocalStory>(storyId);if(!storyHead||storyHead.kind!=='story')throw new ContractError('STORY_MISSING','找不到这本故事')
  const story=revision!==undefined&&!branchId?await vault.read<LocalStory>(storyId,revision):storyHead
  if(!story||story.kind!=='story')throw new ContractError('LINE_REVISION','找不到这个历史版本，当前版本仍保留')
  if(!branchId)return {story,storyHead,line:story,branch:null,branchHead:null}
  const branchHead=await vault.read<LocalBranch>(branchId),branch=revision===undefined?branchHead:await vault.read<LocalBranch>(branchId,revision)
  if(!branchHead||branchHead.kind!=='branch'||!branch||branch.kind!=='branch'||branch.value.storyId!==storyId||branchHead.value.storyId!==storyId)throw new ContractError('BRANCH_MISSING','找不到这个故事版本，原文仍保留')
  return {story,storyHead,line:{...story,value:{...story.value,chapters:branch.value.chapters,paragraphIds:branch.value.paragraphIds}},branch,branchHead}
}
export interface WritingOptions extends Partial<WritingPreferences> {chapterCount:number;direction:string;intentId?:string;purpose?:WritingPurpose;sourceRevision?:number;sourceStoryRevision?:number}
async function writingIntent(config:Document<ProviderConfiguration>,anchor:ProgressAnchor,options:WritingOptions){
 if(options.intentId===undefined)return null
 if(typeof options.intentId!=='string'||!options.intentId||options.intentId.length>500)throw new ContractError('WRITING_INTENT','写作意图无效，未调用模型')
 const {intentId,...request}=options,intent={schema:'writing-intent-v2' as const,id:intentId,requestHash:await sha256(canonical({anchor,options:request,profileId:config.id}))}
 return {id:'writing-run:'+await sha256(canonical({schema:intent.schema,intentId})),intent}
}
function priorWritingIntent(prior:Document<WritingRun>,intent:NonNullable<Awaited<ReturnType<typeof writingIntent>>>){
 if(prior.kind!=='settings'||prior.value.assetType!=='writing-run'||prior.value.historical)throw new ContractError('WRITING_INTENT','原选择记录不能重发')
 if(canonical(prior.value.intent)!==canonical(intent.intent))throw new ContractError('WRITING_INTENT','同一次选择的内容已变化，未创建另一笔请求')
 return prior
}
async function buildWriting(vault:LocalVault,config:Document<ProviderConfiguration>,anchor:ProgressAnchor,options:WritingOptions){
  const loaded=await readingLine(vault,anchor.storyId,anchor.branchId,options.sourceRevision),{branch,line,storyHead,branchHead}=loaded,paragraph=await vault.read<Paragraph>(anchor.paragraphId)
  if(options.sourceStoryRevision!==undefined&&(!Number.isSafeInteger(options.sourceStoryRevision)||options.sourceStoryRevision<1||options.sourceRevision===undefined))throw new ContractError('LINE_REVISION','选区的来源故事版本无效，未调用模型')
  const pinnedStoryRevision=options.sourceRevision===undefined?undefined:branch?.value.storyRevision||loaded.story.revision
  if(options.sourceStoryRevision!==undefined&&options.sourceStoryRevision!==pinnedStoryRevision)throw new ContractError('LINE_REVISION','选区绑定的故事历史版本不一致，未调用模型')
  const story=pinnedStoryRevision===undefined?loaded.story:await vault.read<LocalStory>(anchor.storyId,pinnedStoryRevision)
  if(story?.kind!=='story')throw new ContractError('LINE_REVISION','选区的来源故事版本缺失，未调用模型')
  if(branchHead?.value.archived)throw new ContractError('BRANCH_ARCHIVED','这条故事线已归档，可以阅读；恢复后再写')
  if(!paragraph||paragraph.kind!=='paragraph'||!line.value.paragraphIds.includes(anchor.paragraphId)||unicodeOffset(paragraph.value.text,anchor.offset)!==anchor.offset||anchor.offset<0||anchor.offset>paragraph.value.text.length)throw new ContractError('WRITING_ANCHOR','这次起点不属于当前故事线')
  const template=await prepareTextTask(config,[{role:'user',content:'配置校验'}],story.id,story.revision,'draft',crypto.randomUUID(),crypto.randomUUID(),crypto.randomUUID())
  const layers=await writingPreferenceLayers(vault,story.id),once:Partial<WritingPreferences>={}
  for(const field of ['length','style','forbidden','authorNote','template'] as const)if(options[field]!==undefined)(once as Record<string,unknown>)[field]=options[field]
  const resolved=resolveWritingPreferences(layers.global?.value.values,layers.book?.value.values,once),layerSnapshot=(row:typeof layers.global)=>row?{id:row.id,revision:row.revision,values:clone(row.value.values)}:null
  const append=options.sourceRevision===undefined&&!options.purpose&&!!branch&&!branch.value.edition&&!options.direction.trim()&&line.value.paragraphIds.at(-1)===anchor.paragraphId&&anchor.offset===paragraph.value.text.length
  if(options.intentId!==undefined&&(typeof options.intentId!=='string'||!options.intentId||options.intentId.length>500))throw new ContractError('WRITING_INTENT','写作意图无效，未调用模型')
  const intent=await writingIntent(config,anchor,options),partial=anchor.offset>0&&anchor.offset<paragraph.value.text.length,prefixParagraphId=partial?crypto.randomUUID():null,prefixChapters=prefixAt(line.value.chapters,anchor.paragraphId,anchor.offset,paragraph.value.text,prefixParagraphId),id=intent?.id||'writing-run:'+crypto.randomUUID(),at=stamp()
  const run:WritingRun={assetType:'writing-run',...(options.purpose?{purpose:clone(options.purpose)}:{}),storyId:story.id,storyRevision:story.revision,parentBranchId:branch?.id||null,parentRevision:branch?.revision||story.revision,anchor:clone(anchor),prefixChapters,prefixParagraphId,targetBranchId:append?branch!.id:crypto.randomUUID(),targetRevision:append?branch!.revision:0,append,direction:options.direction.trim(),chapterCount:options.chapterCount,length:options.length??2000,style:options.style||'',forbidden:options.forbidden||'',authorNote:options.authorNote||'',configSnapshot:template.configSnapshot,timeouts:template.plan.timeouts,dailyCalls:template.plan.dailyCalls,chapters:Array.from({length:options.chapterCount},(_,i)=>({id:crypto.randomUUID(),title:'第 '+(prefixChapters.length+i+1)+' 章',paragraphIds:[],text:'',status:'queued',attemptId:null,request:[],providerRequestId:null,finishReason:null,usage:{input:null,output:null},warnings:[]})),status:'queued',ownerId:null,leaseUntil:null,acceptedCount:0,acceptedRevision:0,historical:false,createdAt:at,updatedAt:at,error:null}
  const documents:WriteDocument[]=[]
  if(intent)run.intent=intent.intent
  Object.assign(run,resolved.values,{preferenceSnapshot:{global:layerSnapshot(layers.global),book:layerSnapshot(layers.book),once:clone(once),sources:resolved.sources}})
  if(!run.purpose){
    run.eventSchema=eventSchema
    run.eventPrompt=eventWritingPrompt
    run.eventSubjects=(await vault.list<NovelCharacter>('settings')).filter(row=>row.value.assetType==='novel-character'&&visibleCharacter(row.value,anchor,line.value)).map(row=>({id:row.id,revision:row.revision,name:row.value.name}))
    if(run.eventSubjects.length>200)throw new ContractError('WRITING_CONTEXT','已读人物超过这次上下文上限，未发送请求')
  }
  if(options.purpose?.kind==='directions'&&!prefixChapters.flatMap(c=>c.paragraphIds).length)throw new ContractError('WRITING_ANCHOR','请在已读文字之后规划走向，未发送空上下文请求')
  if(prefixParagraphId){const text=paragraph.value.text.slice(0,anchor.offset);documents.push({kind:'paragraph',id:prefixParagraphId,expectedRevision:0,value:{...paragraph.value,id:prefixParagraphId,text,sourceEnd:paragraph.value.sourceStart+text.length,textHash:await sha256(text),forkOf:{paragraphId:paragraph.id,offset:anchor.offset}}})}
  const conditions=[{kind:'story' as const,id:story.id,revision:storyHead.revision},...(branchHead?[{kind:'branch' as const,id:branchHead.id,revision:branchHead.revision}]:[]),...(['global','book'] as const).map(scope=>({kind:'settings' as const,id:scope==='global'?'settings:writing:global':'settings:writing:book:'+story.id,revision:layers[scope]?.revision||0}))]
  if(run.eventSchema){const inline=new Map(documents.filter(d=>d.kind==='paragraph').map(d=>[d.id,d.value as Paragraph])),collected=await collectAcceptedBasis(vault,run,inline);run.acceptedBasis=collected.basis;conditions.push(...collected.conditions);const people=new Map(run.eventSubjects!.map(s=>[s.id,s]));for(const b of run.acceptedBasis.subjects)if(!people.has(b.subjectId))people.set(b.subjectId,{id:b.subjectId,revision:b.subjectRevision,name:b.name});run.eventSubjects=[...people.values()];if(run.eventSubjects.length>200)throw new ContractError('WRITING_CONTEXT','已读人物超过这次上下文上限，未发送请求')}
  documents.push({kind:'settings',id,expectedRevision:0,value:run})
  return {id,run,documents,conditions}
}
export async function prepareWriting(vault:LocalVault,config:Document<ProviderConfiguration>,anchor:ProgressAnchor,options:WritingOptions){
  const intent=await writingIntent(config,anchor,options)
  if(intent){const prior=await vault.read<WritingRun>(intent.id);if(prior)return priorWritingIntent(prior,intent)}
  const {id,run,documents,conditions}=await buildWriting(vault,config,anchor,options)
  try{await commit(vault,'writing-prepare:'+id,documents,conditions)}catch(error){if(intent){const prior=await vault.read<WritingRun>(id);if(prior)return priorWritingIntent(prior,intent)}throw error}
  return (await vault.read<WritingRun>(id))!
}
export async function previewWritingRequest(vault:LocalVault,config:Document<ProviderConfiguration>,anchor:ProgressAnchor,options:WritingOptions){
 const {run,documents}=await buildWriting(vault,config,anchor,options),inline=new Map(documents.filter(d=>d.kind==='paragraph').map(d=>[d.id,d.value as Paragraph]))
 const request=await writingPrompt(vault,run,0,inline)
 return {request,modelId:run.configSnapshot.modelId,values:{length:run.length,style:run.style,forbidden:run.forbidden,authorNote:run.authorNote,template:run.template??''},sources:run.preferenceSnapshot!.sources,inputEstimate:estimateInput(request),outputLimit:run.configSnapshot.outputLimit,contextLimit:run.configSnapshot.contextLimit}
}
async function writingPrompt(vault:LocalVault,run:WritingRun,index:number,inline=new Map<string,Paragraph>()):Promise<PromptMessage[]>{
  const prefixIds=run.prefixChapters.flatMap(c=>c.paragraphIds),texts:string[]=[]
  const previous=run.chapters.slice(0,index).map(c=>c.title+'\n'+c.text).join('\n\n'),instructions=['写一个完整的中文小说章节，只输出章节标题和正文。不要 JSON、分析、思考标签、说明或结语。','延续下列已读故事，不读取或暗示分叉点之后的原文。新章节必须保持人物身份、地点、时间、持有物品、动机和未解线索的一致性；需要改变时写出明确过渡。',`目标约 ${run.length} 字，章尾完整结束。`,run.direction?`新的故事走向：${run.direction}`:'自然接着故事写。',run.style&&'风格：'+run.style,run.forbidden&&'避免使用：'+run.forbidden,run.authorNote&&'作者注释：'+run.authorNote].filter(Boolean).join('\n')
  let actionInstructions='',selectedText=''
  if(run.eventSchema)actionInstructions=instructions.replace('只输出章节标题和正文。不要 JSON、分析、思考标签、说明或结语。','输出 JSON，格式为 {"title":"章节标题","body":"完整正文","events":[]}。不要分析、思考标签、说明或结语。')+'\n'+run.eventPrompt
  if(run.purpose?.kind==='directions')actionInstructions=directionSystemPrompt
  else if(run.purpose){const edit=await vault.read<WritingEdit>(run.purpose.editId,run.purpose.editRevision);if(edit?.kind!=='settings'||edit.value.assetType!=='writing-edit')throw new ContractError('WRITING_EDIT_MISSING','选区来源版本缺失，未调用模型');selectedText=selectionText(edit.value.selection,edit.value.paragraphs);actionInstructions=run.purpose.kind==='expand'?'只扩写用户明确选中的原文，在不改变原有事实和结尾的前提下增加动作、环境与对话。只输出替换选区的完整正文，不输出章节标题、JSON、思考或说明。':'按用户要求替换所选文字的写法。只输出替换选区的完整正文，不输出章节标题、JSON、思考或说明。'}
  const extra=[run.purpose&&`目标约 ${run.length} 字。`,run.purpose&&run.style&&'风格：'+run.style,run.purpose&&run.forbidden&&'避免使用：'+run.forbidden,run.purpose&&run.authorNote&&'作者注释：'+run.authorNote,run.template&&'用户写作模板（遵守输出格式与已读边界）：\n'+writingTemplate(run.template,{length:run.length,style:run.style,forbidden:run.forbidden,authorNote:run.authorNote,template:run.template},run.direction,run.chapters[index]!.title)].filter(Boolean).join('\n')
  const ledgers=run.chapters.slice(0,index).filter(c=>c.eventLedger).map(c=>c.eventLedger!),events=run.eventSchema?'\n\n已读人物稳定ID：'+canonical(run.eventSubjects)+'\n\n前序新章事件记录：'+canonical(ledgers)+'\n当前候选变化状态：'+canonical(foldEvents(ledgers))+'\n本次候选章序号：'+(index+1):''
  const accepted=run.acceptedBasis?'\n\n冻结的已读事件与身份依据：'+eventCanonical(run.acceptedBasis)+'\n已读来源与当前候选合并状态：'+eventCanonical(acceptedCandidateState(run,index)):''
  const request=(prefix:string[]):PromptMessage[]=>[{role:'system',content:(actionInstructions||instructions)+(extra?'\n'+extra:'')},{role:'user',content:'已读原文（窗口内最近的前文）：\n'+prefix.join('\n\n')+'\n\n本次已完成章节（全部保留）：\n'+previous+events+accepted+(run.purpose?'\n\n本次明确选区：\n'+selectedText+'\n\n用户要求：'+run.direction:'\n\n请写 '+run.chapters[index]!.title+'。')} ]
  const fits=(prefix:string[])=>estimateInput(request(prefix))+run.configSnapshot.outputLimit<=run.configSnapshot.contextLimit
  if(!fits([]))throw new ContractError('CONTEXT_LIMIT','写作设置与前序新章超过模型窗口，本次在下一章发送前停止。完整章节仍可接受。')
  let full=false
  for(let end=prefixIds.length;end>0&&!full;end-=64){const ids=prefixIds.slice(Math.max(0,end-64),end),rows=await vault.readMany<Paragraph>(ids.filter(id=>!inline.has(id))),byId=new Map(rows.map(row=>[row.id,row.value]));for(let i=ids.length-1;i>=0;i--){const paragraph=inline.get(ids[i]!)||byId.get(ids[i]!);if(!paragraph)throw new ContractError('WRITING_SOURCE','前文段落缺失，未发送不完整来源');const candidate=[paragraph.text,...texts];if(!fits(candidate)){full=true;break}texts.unshift(paragraph.text)}}
  if(prefixIds.length&&!texts.length)throw new ContractError('CONTEXT_LIMIT','所选位置之前的最近段落放不进模型窗口，未发送空上下文写作。请增大窗口或减少输出与设置。')
  return request(texts)
}
async function writingSourceConditions(vault:LocalVault,run:WritingRun){
 const story=await vault.read<LocalStory>(run.storyId),parent=run.parentBranchId?await vault.read<LocalBranch>(run.parentBranchId):null,target=run.targetBranchId===run.parentBranchId?parent:await vault.read<LocalBranch>(run.targetBranchId)
 if(story?.kind!=='story'||run.parentBranchId&&(parent?.kind!=='branch'||parent.value.storyId!==run.storyId)||target&&(target.kind!=='branch'||target.value.storyId!==run.storyId))throw new ContractError('WRITING_SOURCE','写作来源或目标已失效，已有正文保留')
 if(parent?.value.archived||target?.value.archived)throw new ContractError('BRANCH_ARCHIVED','写作来源或目标已归档，已有正文保留；恢复后再接受')
 return [...new Map([{id:story.id,kind:'story' as const,revision:story.revision},...(parent?[{id:parent.id,kind:'branch' as const,revision:parent.revision}]:[]),{id:run.targetBranchId,kind:'branch' as const,revision:target?.revision||0}].map(c=>[c.id,c])).values()]
}
export class LocalWritingExecution {
  private controller=new AbortController();private document!:Document<WritingRun>;private persistence=Promise.resolve();private heartbeat:ReturnType<typeof setInterval>|null=null
  private storageFailure=false
  private async modelAuthority(){const model=await this.vault.read<ProviderConfiguration>(this.document.value.configSnapshot.id);if(model?.kind!=='settings'||model.value.enabled!==true||!model.value.slots.includes('text'))throw new ContractError('WRITING_MODEL_REVOKED','文本通道已停用，未继续发送；已收到正文保留');return {model,conditions:await writingSourceConditions(this.vault,this.document.value)}}
  constructor(private vault:LocalVault,private id:string,private changed:(row:Document<WritingRun>)=>void=()=>{},private transport:typeof fetch=fetch,private beforeSend?:()=>Promise<void>){}
  stop(){this.controller.abort()}
  private notify(row:Document<WritingRun>){this.changed(row);if(typeof window!=='undefined')window.dispatchEvent(new CustomEvent('reader-local-writing-changed',{detail:{vaultId:this.vault.id,runId:this.id}}))}
  private async update(mutate:(run:WritingRun)=>void){const work=this.persistence.then(async()=>{
    if(this.storageFailure)throw new ContractError('WRITING_SAVE_PENDING','正文正在等待恢复保存，不再写入旧状态')
    const key=this.vault.id+':'+this.id
    for(let attempt=0;attempt<3;attempt++){const head=await this.vault.read<WritingRun>(this.id);if(!head||head.value.ownerId!==this.document.value.ownerId)throw new ContractError('WRITING_OWNER','写作执行权已变化');const value=clone(head.value);mutate(value);value.updatedAt=stamp();value.leaseUntil=value.status==='running'?new Date(Date.now()+30000).toISOString():null;pending.set(key,{revision:head.revision,value});this.changed({...head,value});try{await commit(this.vault,crypto.randomUUID(),[{kind:'settings',id:this.id,expectedRevision:head.revision,value}]);pending.delete(key);this.document=(await this.vault.read<WritingRun>(this.id))!;this.notify(this.document);return}catch(error){if((error as {code?:string}).code!=='REVISION_CONFLICT'||attempt===2){this.storageFailure=true;this.stop();if(this.heartbeat){clearInterval(this.heartbeat);this.heartbeat=null}throw error}pending.delete(key)}}
  });this.persistence=work.catch(()=>{});return work}
  private async claimChapter(index:number,request:PromptMessage[]){const work=this.persistence.then(async()=>{
    if(this.storageFailure)throw new ContractError('WRITING_SAVE_PENDING','正文正在等待恢复保存，不再开始下一章')
    for(let retry=0;retry<3;retry++){const {model,conditions}=await this.modelAuthority(),head=(await this.vault.read<WritingRun>(this.id))!,value=clone(head.value),owner=this.document.value.ownerId;if(value.status!=='running'||value.ownerId!==owner||value.chapters[index]!.attemptId)throw new ContractError('WRITING_ALREADY_SENT','写作状态已变化，未继续发送');const day=stamp().slice(0,10),budgetId='budget:'+value.configSnapshot.id+':'+day,budget=await this.vault.read<{calls:number}>(budgetId);if((budget?.value.calls||0)>=value.dailyCalls)throw new ContractError('DAILY_LIMIT','模型已达到设备日调用上限，已完成章节仍可接受');const chapter=value.chapters[index]!,attempt=crypto.randomUUID();chapter.attemptId=attempt;chapter.request=request;chapter.status='running';value.updatedAt=stamp();value.leaseUntil=new Date(Date.now()+30000).toISOString();try{await commit(this.vault,'writing-attempt:'+this.id+':'+attempt,[{kind:'settings',id:this.id,expectedRevision:head.revision,value},{kind:'settings',id:budgetId,expectedRevision:budget?.revision||0,value:{calls:(budget?.value.calls||0)+1,day,scope:'this-device-vault',profileId:value.configSnapshot.id}}],[{kind:'settings',id:model.id,revision:model.revision},...conditions]);this.document=(await this.vault.read<WritingRun>(this.id))!;this.notify(this.document);return attempt}catch(error){if((error as {code?:string}).code!=='REVISION_CONFLICT'||retry===2)throw error}}
    throw new ContractError('REVISION_CONFLICT','写作保存冲突，未继续发送')
  });this.persistence=work.then(()=>{},()=>{});return work}
  async execute(){
    const key=this.vault.id+':'+this.id;this.document=(await this.vault.read<WritingRun>(this.id))!
    if(!this.document||this.document.kind!=='settings'||this.document.value.assetType!=='writing-run')throw new ContractError('WRITING_MISSING','找不到写作记录')
    if(this.document.value.historical||this.document.value.status!=='queued'||this.document.value.chapters.some(c=>c.attemptId))throw new ContractError('WRITING_ALREADY_SENT','原请求不能重复发送，已收到的正文仍可阅读')
    if(active.has(key))throw new ContractError('WRITING_BUSY','这次写作正在运行')
    active.set(key,this);let owns=false
    try{
      const owner=crypto.randomUUID(),initial=clone(this.document.value);initial.status='running';initial.ownerId=owner;initial.leaseUntil=new Date(Date.now()+30000).toISOString();initial.updatedAt=stamp()
      await commit(this.vault,'writing-claim:'+this.id+':'+owner,[{kind:'settings',id:this.id,expectedRevision:this.document.revision,value:initial}]);owns=true;this.document=(await this.vault.read<WritingRun>(this.id))!;this.notify(this.document)
      this.heartbeat=setInterval(()=>void this.update(()=>{}).catch(()=>this.stop()),5000)
      for(let index=0;index<this.document.value.chapterCount;index++){
        this.controller.signal.throwIfAborted();if(!this.vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定')
        await this.beforeSend?.();this.controller.signal.throwIfAborted();const request=await writingPrompt(this.vault,this.document.value,index),attempt=await this.claimChapter(index,request),value=this.document.value
        this.controller.signal.throwIfAborted();const current=await this.vault.read<WritingRun>(this.id);if(current?.value.status!=='running'||current.value.ownerId!==owner||current.value.chapters[index]?.attemptId!==attempt)throw new ContractError('WRITING_STOPPED','写作已停止，未继续发送')
        await this.beforeSend?.();this.controller.signal.throwIfAborted();await this.modelAuthority();this.controller.signal.throwIfAborted()
        const secret=new WritingSecretFilter(value.configSnapshot.key)
        const publishText=async(chunk:string)=>{if(!chunk)return;await this.update(run=>{const c=run.chapters[index]!,raw=(c.raw??c.text)+chunk;const {text,...presentation}=writingPresentation(raw,run.purpose);secret.check(raw);secret.check(text);if(presentation.title)secret.check(presentation.title);if(raw.length>1_000_000)throw new ContractError('OUTPUT_LIMIT','输出达到本地上限，已收到的文字保留');c.raw=raw;c.text=text;c.presentation=presentation})}
        await streamText(value.configSnapshot,request,this.id,async(event:ProviderEvent)=>{
          if(event.type==='text'){await publishText(secret.feed(event.text));return}
          if(event.type==='request')secret.check(event.id)
          if(event.type==='finish'&&event.reason)secret.check(event.reason)
          await this.update(run=>{const c=run.chapters[index]!;if(event.type==='request')c.providerRequestId??=event.id;if(event.type==='finish')c.finishReason??=event.reason;if(event.type==='usage'){if(event.input!==null)c.usage.input=Math.max(c.usage.input??0,event.input);if(event.output!==null)c.usage.output=Math.max(c.usage.output??0,event.output)}})
        },this.controller.signal,value.timeouts,this.transport)
        await publishText(secret.finish())
        const chapter=this.document.value.chapters[index]!,eventLedgers=this.document.value.chapters.slice(0,index).filter(c=>c.eventLedger).map(c=>c.eventLedger!),metadata=receivedProseObject(chapter.raw||'')
        const eventLedger=value.eventSchema?await eventsFromProposals(['stop','end_turn'].includes(chapter.finishReason||'')&&!chapter.presentation?.pending?metadata?.events:undefined,chapter.text,index+1,new Set(value.eventSubjects?.map(s=>s.id)),eventLedgers.flatMap(l=>l.events.map(e=>e.id))):undefined
        await this.update(run=>{const c=run.chapters[index]!;c.status=c.text.trim()&&!c.presentation?.pending&&['stop','end_turn'].includes(c.finishReason||'')?'complete':'partial';if(c.raw!==undefined)c.quality=assessWritingQuality(c.text,c.raw,c.finishReason,run.length);c.warnings=writingWarnings(c,run.length);if(eventLedger){c.eventLedger=eventLedger;const hints=foldEvents([...eventLedgers,eventLedger]).hints.filter(hint=>eventLedger.events.some(entry=>entry.id===hint.eventId)),labels:Record<string,string>={ITEM_TRANSFER_GAP:'物品交接与前文不一致，可能缺少过渡。',LOCATION_TRANSITION_GAP:'人物位置变化与前文不一致，可能缺少过渡。',THREAD_REOPENING_UNCLEAR:'已结束的线索再次推进，可能需要说明。'};c.warnings=[...new Set([...c.warnings,...hints.map(hint=>labels[hint.code]).filter((label):label is string=>!!label)])]}if(c.presentation?.title)c.title=c.presentation.title;const heading=c.text.match(/^\s*(?:#{1,3}\s*)?((?:第[\d零〇一二三四五六七八九十百]+章|第\s*\d+\s*章)[^\n]{0,100})\n/);if(heading)c.title=heading[1]!.trim()})
        if(this.document.value.chapters[index]!.status!=='complete')throw new ContractError('OUTPUT_PARTIAL','模型没有完整结束；已有正文可查看，前序完整章仍可接受')
      }
      await this.update(run=>{run.status='completed';run.error=null})
    }catch(error){
      if(owns&&this.vault.unlocked&&!pending.has(key)){await this.update(run=>{const c=run.chapters.find(c=>c.status==='running');if(c)c.status=c.text?'partial':error instanceof ContractError||error instanceof ProviderFailure&&!error.uncertain?'failed':'unknown';run.status=this.controller.signal.aborted?'cancelled':error instanceof ProviderFailure&&error.uncertain?'unknown':'failed';run.error=error instanceof ContractError?error.message:this.controller.signal.aborted?'已停止，已收到的正文保留。原请求不会重发。':'写作中断，已收到的正文保留。'}).catch(()=>{})}
      else if(owns&&pending.has(key)){const cached=pending.get(key)!;cached.value.status='unknown';cached.value.leaseUntil=null;cached.value.error='正文已收到，但保存失败。请只重试保存，不会再次调用模型。';for(const c of cached.value.chapters)if(c.status==='running')c.status=c.text?'partial':'unknown';this.notify({...this.document,value:clone(cached.value)})}
      if(!owns)throw error
    }finally{if(this.heartbeat)clearInterval(this.heartbeat);if(active.get(key)===this)active.delete(key);this.stop()}
    return pending.has(key)?{...this.document,value:clone(pending.get(key)!.value)}:this.document
  }
}
export function writingWarnings(chapter:WritingChapter,length:number){return assessWritingQuality(chapter.text,chapter.raw??chapter.text,chapter.finishReason,length).warnings}
export async function retryWritingSave(vault:LocalVault,id:string){const key=vault.id+':'+id,copy=pending.get(key);if(!copy)throw new ContractError('WRITING_SAVE_MISSING','这页已没有待保存文字，请查看写作记录；原请求不会重发');copy.recoveryValue??={...clone(copy.value),error:'已收到的正文已保存，原请求不会自动重发。'};await commit(vault,'writing-save-recovery:'+id+':'+copy.revision,[{kind:'settings',id,expectedRevision:copy.revision,value:copy.recoveryValue}]);pending.delete(key);return (await vault.read<WritingRun>(id))!}
export async function stopWriting(vault:LocalVault,id:string){active.get(vault.id+':'+id)?.stop();control?.postMessage({type:'stop',vaultId:vault.id,id});const row=await vault.read<WritingRun>(id);if(row&&row.value.status==='queued')await commit(vault,'writing-stop:'+id,[{kind:'settings',id,expectedRevision:row.revision,value:{...row.value,status:'cancelled',updatedAt:stamp()}}])}
export async function recoverWriting(vault:LocalVault,row:Document<WritingRun>){if(hasPendingWritingSave(vault,row.id))return writingPendingView(vault,row);if(row.value.status!=='running'||row.value.leaseUntil&&Date.parse(row.value.leaseUntil)>Date.now()||active.has(vault.id+':'+row.id))return row;const value=clone(row.value);value.status='unknown';value.leaseUntil=null;value.error='原执行页面已断开，已保存正文保留；不会自动重发。';for(const c of value.chapters)if(c.status==='running')c.status=c.text?'partial':'unknown';try{await commit(vault,crypto.randomUUID(),[{kind:'settings',id:row.id,expectedRevision:row.revision,value}])}catch(error){if((error as {code?:string}).code!=='REVISION_CONFLICT')throw error}return (await vault.read<WritingRun>(row.id))!}
export async function acceptWriting(vault:LocalVault,id:string,count:number){
  const row=await vault.read<WritingRun>(id);if(!row||row.value.assetType!=='writing-run')throw new ContractError('WRITING_MISSING','找不到写作记录');const run=clone(row.value)
  if(run.purpose)throw new ContractError('WRITING_ACTION','此结果请在选区编辑或剧情方向面板使用，不能插入整章')
  if(!Number.isInteger(count)||count<1||count>completePrefix(run))throw new ContractError('WRITING_PREFIX','只能接受连续的完整章节，片段仍可查看')
  if(run.acceptedCount>=count)return {branchId:run.targetBranchId,count:run.acceptedCount}
  const conditions=await writingSourceConditions(vault,run),previous=await vault.read<LocalBranch>(run.targetBranchId)
  let expected=run.acceptedCount?run.acceptedRevision:run.targetRevision
  if((previous?.revision||0)!==expected){const prior=expected?await vault.read<LocalBranch>(run.targetBranchId,expected):null,content=(v:LocalBranch)=>{const {name,archived,...rest}=v;return rest};if(!previous||prior?.kind!=='branch'||canonical(content(previous.value))!==canonical(content(prior.value)))throw new ContractError('REVISION_CONFLICT','目标故事线已有其他修改。原候选保留，请先比较。');expected=previous.revision}
  if(previous?.value.edition)throw new ContractError('WRITING_FORK_REQUIRED','此候选不能改动选区修订的来源证明，请从这里另开故事线；正文仍保留')
  const documents:WriteDocument[]=[],chapters=[]
  for(let index=run.acceptedCount;index<count;index++){const chapter=run.chapters[index]!;chapter.paragraphIds=[];for(const part of acceptedWritingParagraphs(chapter)){const paraId=crypto.randomUUID();chapter.paragraphIds.push(paraId);documents.push({kind:'paragraph',id:paraId,expectedRevision:0,value:{id:paraId,chapterId:chapter.id,...part,textHash:await sha256(part.text),writingRunId:id}})}if(run.eventSchema)chapter.acceptedProof=await createAcceptedProof(chapter.text,chapter.paragraphIds);chapters.push({id:chapter.id,title:chapter.title,paragraphIds:chapter.paragraphIds})}
  const all=[...(previous?previous.value.chapters:run.prefixChapters),...chapters],branch:LocalBranch=previous?{...previous.value,chapters:all,paragraphIds:all.flatMap(c=>c.paragraphIds)}:{storyId:run.storyId,name:run.direction.slice(0,40)||'接着故事写',archived:false,parentBranchId:run.parentBranchId,parentRevision:run.parentRevision,storyRevision:run.storyRevision,fork:run.anchor,prefixChapters:run.prefixChapters,chapters:all,paragraphIds:all.flatMap(c=>c.paragraphIds),createdAt:stamp()}
  run.acceptedCount=count;run.acceptedRevision=expected+1;run.updatedAt=stamp();documents.push({kind:'branch',id:run.targetBranchId,expectedRevision:expected,value:branch},{kind:'settings',id,expectedRevision:row.revision,value:run})
  const operation='writing-accept:'+id+':'+count
  try{await commit(vault,operation,documents,conditions)}catch(error){const head=await vault.read<WritingRun>(id);if(head&&head.value.acceptedCount>=count&&await vault.receipt(operation))return {branchId:head.value.targetBranchId,count:head.value.acceptedCount};throw error}
  return {branchId:run.targetBranchId,count}
}
