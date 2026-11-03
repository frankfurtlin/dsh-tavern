import {validatePlayableUpdate} from '../playable/contract'
import {validateCompanionCreationUpdate,type CompanionCreationDraft} from '../companion/creation'
import {validateOriginalDraftUpdate,type OriginalStoryDraft} from '../catalog/original-story'
import {validateIndexUpdate,type CharacterIndex} from '../novel-characters/index-contract'
import {validateActivityUpdate} from '../reader/activity-contract'
import {validateImportUpdate,type ImportEntry} from '../catalog/import-contract'
import {validateSkillVersion} from '../skills/versions'
import {validateEditUpdate,type WritingEdit} from '../writing/edits-contract'
import type {SkillManifest} from '../skills/contract'
import {canonical,ContractError,type Document,type DomainKind} from './local-contract'
import {validateLocalValue} from './local-validation'
import {validateVectorUpdate,type VectorRecord} from '../media/vector-contract'
import {validateVideoUpdate,type VideoRecord} from '../media/video-contract'
/** Policies apply before encryption; the repository then CAS checks the read revision. */
export function validateUpdate(kind:DomainKind,previous:Document<unknown>|null,next:unknown){
  validateLocalValue(kind,next)
  if(!next||typeof next!=='object'||Array.isArray(next))throw new ContractError('DOCUMENT_INVALID','对象内容必须符合数据合同')
  if(kind==='world'||kind==='checkpoint'||kind==='settings'&&((next as {assetType?:string}).assetType==='playable-intent'||(previous?.value as {assetType?:string}|undefined)?.assetType==='playable-intent'))validatePlayableUpdate(kind,previous,next)
  if(!previous&&kind==='settings'&&(next as Record<string,unknown>).assetType==='companion-create-draft'&&(next as Record<string,unknown>).status!=='editing')throw new ContractError('COMPANION_DRAFT','伴侣创建必须先保存编辑草稿')
  if(!previous){if(kind==='settings'&&(next as Record<string,unknown>).assetType==='original-story-draft'&&(next as Record<string,unknown>).status!=='editing')throw new ContractError('ORIGINAL_DRAFT','新故事必须先保存编辑草稿');if(kind==='story'&&(next as Record<string,unknown>).origin==='import'&&typeof (next as Record<string,unknown>).sourceId!=='string')throw new ContractError('DOCUMENT_REFERENCE_INVALID','导入故事必须保留原文件来源；未写入任何数据');return}
  if(previous.kind!==kind)throw new ContractError('REVISION_CONFLICT','对象类型已变化')
  const before=previous.value as Record<string,unknown>,after=next as Record<string,unknown>
  if(kind==='settings'&&after.assetType==='companion-create-draft'&&before.assetType!=='companion-create-draft')throw new ContractError('COMPANION_DRAFT','伴侣草稿不能从其他设置变更而来')
  if(kind==='settings'&&before.assetType==='companion-create-draft')validateCompanionCreationUpdate(before as unknown as CompanionCreationDraft,after as unknown as CompanionCreationDraft)
  if(kind==='settings'&&after.assetType==='original-story-draft'&&before.assetType!=='original-story-draft')throw new ContractError('ORIGINAL_DRAFT','新故事草稿不能从其他设置类型变更而来')
  if(kind==='settings'&&before.assetType==='original-story-draft')validateOriginalDraftUpdate(before as unknown as OriginalStoryDraft,after as unknown as OriginalStoryDraft)
  if(kind==='settings'&&before.assetType==='import-entry')validateImportUpdate(previous,after as unknown as ImportEntry)
  if(kind==='settings'&&before.assetType==='reading-event')validateActivityUpdate(previous,after)
  if(!before||typeof before!=='object'||Array.isArray(before))throw new ContractError('DOCUMENT_INVALID','原对象格式需要修复；原数据保留')
  let immutable:string[]=[]
  if(kind==='settings'&&before.assetType)immutable=['assetType']
  if(kind==='settings'&&before.assetType==='novel-character-index')validateIndexUpdate(before as unknown as CharacterIndex,after as unknown as CharacterIndex)
  if(kind==='settings'&&before.assetType==='novel-character')immutable.push('identityId','cardId','name','primary','createdAt')
  if(kind==='paragraph'&&before.sliceOf!==undefined)immutable.push('sliceOf')
  if(kind==='settings'&&before.assetType==='novel-character-scan'){immutable.push('scope','runId','historical','createdAt','index');if(before.applied===true&&(after.applied!==true||canonical(after.characterIds)!==canonical(before.characterIds)))throw new ContractError('CHARACTER_HISTORY','已保存的人物关联不能覆盖')}
  if(kind==='settings'&&before.assetType==='agent-skill'){immutable.push('createdAt');validateSkillVersion(before.manifest as SkillManifest,after.manifest as SkillManifest)}
  if(kind==='settings'&&before.assetType==='chat-plugin'){immutable.push('createdAt','builtin');if(canonical(before.manifest)!==canonical(after.manifest)||before.source!==after.source){const old=before.manifest as {id:string;version:string},next=after.manifest as {id:string;version:string};if(old.id!==next.id||old.version===next.version)throw new ContractError('PLUGIN_VERSION','插件内容更新必须保存新版本');if(after.enabled!==false||canonical(after.grants)!=='[]')throw new ContractError('PLUGIN_REAUTHORIZE','新版本须重新启用和授权')}}
  if(kind==='settings'&&before.assetType==='plugin-overrides')immutable.push('targetId','targetKind')
  if(kind==='settings'&&before.assetType==='plugin-editor-draft')immutable.push('conflict')
  if(kind==='settings'&&before.assetType==='card-editor-draft'){immutable.push('cardId','identityId','createdAt','conflict');if(before.retired===true&&after.retired!==true)throw new ContractError('CARD_DRAFT','已结束的角色草稿不能自动重新打开')}
  if(kind==='settings'&&before.assetType==='plugin-report'){immutable.push('targetId','taskId','snapshot','historical','createdAt');if(canonical((after.trace as unknown[]).slice(0,(before.trace as unknown[]).length))!==canonical(before.trace))throw new ContractError('PLUGIN_HISTORY','已有插件诊断不能覆盖')}
  if(kind==='settings'&&before.assetType==='skill-draft')immutable.push('skillId','conflict')
  if(kind==='settings'&&before.assetType==='writing-edit')validateEditUpdate(before as unknown as WritingEdit,after as unknown as WritingEdit)
  if(kind==='settings'&&before.assetType==='writing-preferences')immutable.push('scope','storyId')
  if(kind==='settings'&&before.assetType==='main-reading-line')immutable.push('storyId')
  if(kind==='settings'&&before.assetType==='writing-compose-draft')immutable.push('storyId','createdAt')
  if(kind==='settings'&&before.assetType==='writing-compose-view')immutable.push('storyId')
  if(kind==='settings'&&before.assetType==='writing-directions'){immutable.push('runId','runRevision','storyId','original','createdAt');if(canonical((before.choices as {id:string}[]).map(c=>c.id))!==canonical((after.choices as {id:string}[]).map(c=>c.id)))throw new ContractError('WRITING_PLAN','旧方向身份必须保留')}
  if(kind==='settings'&&before.assetType==='media-provider')immutable.push('id')
  if(kind==='settings'&&before.assetType==='stage-state')immutable.push('targetId','targetKind')
  if(kind==='settings'&&before.assetType==='agent-team'){immutable.push('parentId','parentRevision','sessionId','configSnapshot','children','limits','rootSteps','rootCalls','historical','createdAt');const states:Record<string,string[]>={ready:['ready','running','cancelled'],running:['running','completed','unknown','cancelled'],completed:['completed','running','cancelled'],cancelled:['cancelled'],unknown:['unknown','cancelled']};if(before.historical!==true&&!states[String(before.status)]?.includes(String(after.status)))throw new ContractError('AGENT_TEAM_STATE','已调度的子任务不能重新派生或重发')}
  if(kind==='settings'&&before.assetType==='skill-run'){
    immutable.push('consumer','skillId','skillRevision','manifest','grants','input','references','historical','createdAt','configSnapshot','limits')
    if(before.attemptId)immutable.push('attemptId');if(before.output)immutable.push('output')
    const transitions:Record<string,string[]>={queued:['queued','running','cancelled'],running:['running','ready','failed','unknown','cancelled','persistence_failed'],ready:['ready','applied','rejected','cancelled'],applied:['applied','rolled_back'],rejected:['rejected'],rolled_back:['rolled_back'],failed:['failed'],unknown:['unknown'],cancelled:['cancelled'],persistence_failed:['persistence_failed','ready','unknown','cancelled']}
    if(before.historical!==true&&!transitions[String(before.status)]?.includes(String(after.status)))throw new ContractError('SKILL_STATE','技能原任务不能倒退或重复执行')
    if((after.calls as number)<(before.calls as number)||!(after.raw as string).startsWith(before.raw as string)||canonical((after.resourceIds as unknown[]).slice(0,(before.resourceIds as unknown[]).length))!==canonical(before.resourceIds))throw new ContractError('SKILL_HISTORY','已收到产物和调用记录不能覆盖')
    const known=before.provider as Record<string,unknown>|null;if(known?.requestId&&(after.provider as Record<string,unknown>|null)?.requestId!==known.requestId)throw new ContractError('SKILL_REQUEST','原供应商请求身份必须保留')
    const usage=after.provider as Record<string,unknown>|null;if(known){if(!usage||known.finished===true&&usage.finished!==true)throw new ContractError('SKILL_USAGE','已知回执不能删除');for(const field of ['input','output'])if(known[field]!==null&&(usage[field]===null||Number(usage[field])<Number(known[field])||before.status!=='running'&&known[field]!==usage[field]))throw new ContractError('SKILL_USAGE','已知用量不能减少或改写')}
  }
  if(kind==='settings'&&before.assetType==='embedding-record')validateVectorUpdate(before as unknown as VectorRecord,after as unknown as VectorRecord)
  if(kind==='settings'&&before.assetType==='video-record')validateVideoUpdate(before as unknown as VideoRecord,after as unknown as VideoRecord)
  if(kind==='asset'){immutable=['mediaKind','mime','sha256','bytes','sourceId','createdAt','width','height','duration','origin','parentAssetId','jobId','videoRecordId']}
  if(kind==='mediaJob'){
    immutable=['type','configSnapshot','prompt','referenceAssetIds','referenceAssetHashes','source','intentHash','createdAt','historical']
    if(before.claimId)immutable.push('claimId')
    if(before.outputAssetId)immutable.push('outputAssetId')
    if(before.text!==null)immutable.push('text')
    const transitions:Record<string,string[]>={queued:['queued','calling','cancelled'],calling:['calling','completed','failed','unknown','cancelled','persistence_failed'],persistence_failed:['persistence_failed','completed','cancelled'],unknown:['unknown','completed','cancelled'],cancelled:['cancelled'],completed:['completed'],failed:['failed']}
    if(before.historical!==true&&!transitions[String(before.status)]?.includes(String(after.status)))throw new ContractError('MEDIA_STATE','媒体原请求不能倒退或重复发送')
  }
  if(kind==='settings'&&previous?.id==='settings:media-storage'&&(after.used as number)<(before.used as number))throw new ContractError('MEDIA_STORAGE','原素材尚未清理，不能减少已用容量')
  if(kind==='settings'&&previous?.id.startsWith('budget:')&&(after.calls as number)<(before.calls as number))throw new ContractError('DAILY_BUDGET','设备今日调用记录不能减少')
  if(kind==='branch'){
    immutable=['storyId','parentBranchId','parentRevision','storyRevision','fork','prefixChapters','createdAt']
    if(before.edition){const old=before.edition as {editId:string;editRevision:number},next=after.edition as {editId?:string;editRevision?:number}|undefined;if(next?.editId!==old.editId||Number(next.editRevision)<old.editRevision||canonical(before.chapters)!==canonical(after.chapters)&&Number(next.editRevision)<=old.editRevision)throw new ContractError('BRANCH_HISTORY_IMMUTABLE','编辑故事线只能接受自己的新候选，旧版本保留')}
    else{immutable.push('edition');if(canonical((after.chapters as unknown[]).slice(0,(before.chapters as unknown[]).length))!==canonical(before.chapters)||canonical((after.paragraphIds as unknown[]).slice(0,(before.paragraphIds as unknown[]).length))!==canonical(before.paragraphIds))throw new ContractError('BRANCH_HISTORY_IMMUTABLE','这条故事线保留已有正文；改写请从相应位置分叉')}
  }
  if(kind==='settings'&&before.assetType==='writing-run'){
    immutable=['assetType','storyId','storyRevision','parentBranchId','parentRevision','anchor','prefixChapters','prefixParagraphId','targetBranchId','targetRevision','append','direction','purpose','chapterCount','length','style','forbidden','authorNote','template','preferenceSnapshot','eventSchema','eventPrompt','eventSubjects','acceptedBasis','intent','configSnapshot','timeouts','dailyCalls','historical','originalStatus','createdAt']
    const old=before.chapters as Record<string,unknown>[],next=after.chapters as Record<string,unknown>[]
    for(let i=0;i<old.length;i++){
      const a=old[i]!,b=next[i]!
      if(a.acceptedProof!==undefined?canonical(a.acceptedProof)!==canonical(b.acceptedProof??null):b.acceptedProof!==undefined&&(i<Number(before.acceptedCount)||i>=Number(after.acceptedCount)))throw new ContractError('WRITING_ACCEPTED_PROOF_IMMUTABLE','已接受正文的来源凭据必须保留，只能随首次接受保存')
      if(canonical(a.originalStatus??null)!==canonical(b.originalStatus??null))throw new ContractError('WRITING_HISTORY_IMMUTABLE','迁移前的章节状态必须保留')
      if(a.status!=='running'&&a.status!=='queued'||before.status!=='running'&&before.status!=='queued')for(const field of ['status','attemptId','request','text','raw','title','presentation','quality','eventLedger','warnings','providerRequestId','finishReason','usage'])if(canonical(a[field]??null)!==canonical(b[field]??null))throw new ContractError('WRITING_HISTORY_IMMUTABLE','已结束章节的正文与供应商回执必须保留，不能覆盖')
      if(a.providerRequestId!==null&&a.providerRequestId!==b.providerRequestId)throw new ContractError('WRITING_REQUEST','已知供应商请求身份必须保留')
      if(a.finishReason!==null&&a.finishReason!==b.finishReason)throw new ContractError('WRITING_FINISH','已知结束回执不能覆盖')
      const usage=a.usage as {input:number|null;output:number|null},current=b.usage as typeof usage
      for(const field of ['input','output'] as const)if(usage[field]!==null&&(current[field]===null||current[field]!<usage[field]!||a.status!=='running'&&current[field]!==usage[field]))throw new ContractError('WRITING_USAGE','已知用量不能减少或改写')
      if(a.status!=='running'&&a.status!=='queued'&&canonical(usage)!==canonical(current))throw new ContractError('WRITING_USAGE','已结束章节的用量必须保留')
    }
    for(let i=0;i<old.length;i++){const a=old[i]!,b=next[i]!;if(a.id!==b.id||a.attemptId&&(a.attemptId!==b.attemptId||canonical(a.request)!==canonical(b.request))||a.status==='complete'&&(b.status!=='complete'||a.text!==b.text||a.title!==b.title)||(a.raw!==undefined?typeof b.raw!=='string'||!(b.raw as string).startsWith(a.raw as string):b.raw!==undefined?!(b.raw as string).startsWith(a.text as string):!(b.text as string).startsWith(a.text as string))||a.status==='complete'&&canonical(a.raw??null)!==canonical(b.raw??null)||canonical((b.paragraphIds as unknown[]).slice(0,(a.paragraphIds as unknown[]).length))!==canonical(a.paragraphIds))throw new ContractError('WRITING_HISTORY_IMMUTABLE','已收到正文和原请求保留，不能覆盖或重发')}
    if((after.acceptedCount as number)<(before.acceptedCount as number)||before.historical!==true&&(before.status!=='queued'&&after.status==='queued'||before.status!=='running'&&before.status!=='queued'&&before.status!==after.status))throw new ContractError('WRITING_STATE','写作记录不能倒退或重复发送')
  }
  if(kind==='settings'&&before.assetType==='mcp-invocation')immutable=['assetType','connectorId','connectorRevision','tool','arguments','requestId','createdAt','sourceUrl']
  if(kind==='settings'&&before.assetType==='mcp-invocation'){
    if(before.claimId)immutable.push('claimId')
    if(before.result!==null&&canonical(before.result)!==canonical(after.result))throw new ContractError('MCP_RESULT_IMMUTABLE','已知工具回执保留原返回，不能覆盖')
    if(before.historical===true&&after.historical!==true)throw new ContractError('MCP_HISTORICAL','迁移的工具记录不能重新启用')
    const transitions:Record<string,string[]>={prepared:['prepared','calling','cancelled'],calling:['calling','completed','failed','unknown','cancelled','persistence_failed'],persistence_failed:['persistence_failed','completed','failed','cancelled'],completed:['completed'],failed:['failed'],unknown:['unknown'],cancelled:['cancelled']}
    if(!(before.historical===true&&after.historical===true)&&!transitions[String(before.status)]?.includes(String(after.status)))throw new ContractError('MCP_STATE','MCP 请求状态不能倒退或重发')
  }
  if(kind==='story'&&before.immutableSource===true)immutable=['immutableSource','origin','sourceHash','sourceId','paragraphIds']
  if(kind==='card'||kind==='companion')immutable=['identityId']
  if(kind==='card'&&before.container)immutable.push('container')
  if(kind==='companion'){immutable.push('createdAt','sourceCard','promotion');if(before.sessionId)immutable.push('sessionId')}
  if(kind==='memory')immutable=['companionId','source','createdAt']
  if(kind==='relationship')immutable=['companionId','algorithm']
  if(kind==='note')immutable=['anchor','createdAt','quote']
  if(kind==='task')immutable=['operationId','payloadHash','targetId','targetRevision','sessionId','configSnapshot','request','kind','speakerId','messageId','messageRevision','variantId','plan','historical','promptTrace']
  if(kind==='agentRun'){
    immutable=['sessionId','instruction','references','policy','createdAt','delegation','historical'];if(before.configSnapshot)immutable.push('configSnapshot')
    if(Array.isArray(before.plan)&&before.plan.length)immutable.push('plan')
    const agentStates:Record<string,string[]>={queued:['queued','planning','cancelled','failed'],planning:['planning','streaming','ready','failed','unknown','cancelled'],streaming:['streaming','ready','failed','unknown','cancelled'],ready:['ready','running','cancelled','failed'],running:['running','ready','needs_approval','completed','cancelled','failed','unknown'],needs_approval:['needs_approval','ready','completed','cancelled','failed'],completed:['completed'],cancelled:['cancelled'],failed:['failed'],unknown:['unknown']};if(before.historical!==true&&!agentStates[String(before.status)]?.includes(String(after.status)))throw new ContractError('AGENT_STATE','原计划不能返回排队或重发状态')
    if(before.historical!==true&&['cancelled','completed','failed','unknown'].includes(String(before.status))&&before.status!==after.status)throw new ContractError('AGENT_STATE','已结束的助手任务不能重新执行')
    if((after.cursor as number)<(before.cursor as number)||canonical((after.results as unknown[]).slice(0,(before.results as unknown[]).length))!==canonical(before.results))throw new ContractError('AGENT_STATE','已保存的工具结果不能被覆盖')
    if(before.provider&&(!after.provider||(before.provider as Record<string,unknown>).attemptId!==(after.provider as Record<string,unknown>).attemptId))throw new ContractError('AGENT_STATE','不能重复发送原模型请求')
  }
  if(kind==='changeSet'){
    immutable=['runId','sessionId','callId','targetId','targetKind','expectedRevision','before','after','createdAt','tool']
    const states:Record<string,string[]>={proposed:['proposed','applied','rejected'],applied:['applied','rolled_back'],rejected:['rejected'],rolled_back:['rolled_back']};if(!states[String(before.status)]?.includes(String(after.status)))throw new ContractError('AGENT_CHANGE_STATE','修改不能返回待确认状态')
  }
  if(kind==='task'){
    const transitions:Record<string,string[]>={queued:['queued','running','cancelled','failed'],running:['running','received','unknown','failed','cancelled'],unknown:['unknown','received','failed','saved'],received:['received','saved'],saved:['saved'],failed:['failed','received','saved'],paused:['paused','received','saved'],cancelled:['cancelled','received','saved']}
    if(typeof before.status!=='string'||typeof after.status!=='string'||!transitions[before.status]?.includes(after.status))throw new ContractError('TASK_STATE_INVALID','已发送或历史请求不能返回等待队列')
    if(before.attemptId)immutable.push('attemptId','ownerId')
    if(before.saveInputHash)immutable.push('saveInputHash')
  }
  if(kind==='message')immutable=['sessionId','role','speakerId','personaName']
  if(kind==='session')immutable=['cards','parent','createdAt','companionId','importFile','importMetadata']
  for(const field of immutable){if(canonical(before[field]??null)!==canonical(after[field]??null))throw new ContractError('IMMUTABLE_FIELD','来源与身份保留原版本；这次修改需要创建新副本')}
  if(kind==='story'&&before.immutableSource===true){
    if(!Array.isArray(after.chapters)||!Array.isArray(after.paragraphIds)||after.chapters.some(chapter=>!chapter||typeof chapter!=='object'||!Array.isArray((chapter as {paragraphIds?:unknown}).paragraphIds)))throw new ContractError('DOCUMENT_INVALID','目录必须保留原正文顺序')
    const flattened=after.chapters.flatMap(chapter=>(chapter as {paragraphIds:unknown[]}).paragraphIds)
    if(canonical(flattened)!==canonical(before.paragraphIds))throw new ContractError('SOURCE_IMMUTABLE','目录调整不能遗漏或重排原正文')
  }
}
