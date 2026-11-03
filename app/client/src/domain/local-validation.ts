import {validatePlayDefinition,validatePlayWorld,validatePlayCheckpoint,validatePlayIntent,playableReferences,validatePlayableRelations} from '../playable/contract'
import {validateCompanionCreation,validateCompanionCreationRelations,companionCreationReferences,type CompanionCreationDraft} from '../companion/creation'
import {validateCompanionWorld} from '../companion/world-context'
import {validateOriginalStoryDraft,originalDraftReferences,validateOriginalDraftRelations,type OriginalStoryDraft} from '../catalog/original-story'
import {historicalReadingAnchor} from '../writing/line-history'
import {validateReadingEvent,validateReadingGoal,activityReferences,validateActivityRelations} from '../reader/activity-contract'
import {validateLibraryView,validateBookMetadata} from '../catalog/library-management'
import {validateLibraryMetadata,validateCollectionType} from '../catalog/collection-index'
import {validateImportEntry,type ImportEntry} from '../catalog/import-contract'
import {validateCharacterIndex} from '../novel-characters/index-contract'
import {validateNovelCharacter,validateCharacterScan,validateNovelCardMetadata,type NovelCharacter} from '../novel-characters/contract'
import {validateWritingEdit} from '../writing/edits-contract'
import {writingEventRelations} from '../writing/event-relations'
import {acceptedBasisReferences,acceptedBasisSourceBytes} from '../writing/accepted-basis'
import {acceptedWritingParagraphs} from '../writing/accepted-prose'
import {validateAcceptedProofBytes} from '../writing/accepted-proof'
import type {Paragraph} from './local-contract'
import {validateWritingModel} from '../writing/model-contract'
import type {ProviderConfiguration} from './local-contract'
import {validateWritingPreferenceLayer} from '../writing/preferences'
import {validateWritingComposeDraft,validateWritingComposeView,validateWritingDirectionPlan,planningReferences,validatePlanningRelations} from '../writing/planning-contract'
import {editReferences,validateEditRelations} from '../writing/edits-relations'
import {novelReferences,validateNovelRelations} from '../novel-characters/relations'
import {validatePromotion,validatePromotedMemorySource,promotionReferences,validatePromotionRelations} from '../companion/promotion-contract'
import {validateCardDraft,type CardDraft} from '../tavern/card-drafts'
import {validateInstalledPlugin,validatePluginOverrides,validatePluginReport,validatePluginDraft,validatePluginSnapshot,type PluginReport,type PluginOverrides,type PluginSnapshot} from '../plugins/contract'
import {snapshotReferences,validateSnapshotRelations,validateOverrideRelations,validateReportRelations} from '../plugins/relations'
import {validateAgentTeam,childReferences,parentTools,type AgentTeam} from '../agent/team-contract'
import type {AgentRun} from '../agent/contract'
import {validateSkillDraft} from '../skills/drafts'
import {validateReaderTheme} from '../presentation/themes'
import {validateAppearanceFields} from '../presentation/appearance'
import {validateReplies} from '../tavern/quick-replies'
import {validateAgentSession,validateAgentRun,validateAgentChange,agentReadableKinds,type AgentReference} from '../agent/contract'
import {canonical} from './local-contract'
import {validateMcpConnector,validateMcpInvocation} from '../agent/mcp-contract'
import {validateGroup} from '../tavern/group'
import {validatePreset} from '../prompt-runtime/preset'
import {ContractError,sha256,unicodeOffset,type DomainKind} from './local-contract'
import {validateLorebook} from '../prompt-runtime/lorebook'
import {validateRegexRule} from '../prompt-runtime/regex'
import {validatePersona} from '../tavern/persona'
import {validateBranch,validateWritingRun,prefixAt,type LocalBranch,type WritingRun} from '../writing/contract'
import {validateAsset,validateMediaJob,validateMediaProvider,validateAssetLink,type LocalAsset,type MediaJob,type AssetLink} from '../media/contract'
import {validateVectorDraft,validateVectorRecord,type VectorRecord} from '../media/vector-contract'
import {validateVideoDraft,validateVideoRecord,type VideoRecord} from '../media/video-contract'
import {validateStageSettings,validateStageState,stageReferences,type StageState} from '../theater/contract'
import {validateInstalledSkill,validateSkillRun,type SkillRun,type InstalledSkill,skillReadableKinds} from '../skills/contract'
const kinds=new Set('story source sourceChunk branch paragraph progress note card persona session message lorebook memory world checkpoint companion relationship asset mediaJob agentRun changeSet plugin settings task'.split(' '))
const record=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value)
const text=(value:unknown,max=1_000_000)=>typeof value==='string'&&value.length<=max
const id=(value:unknown)=>text(value,160)&&!!value
const integer=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0
const hash=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
type QuickReplyRelation={assetType:string;buttons:{id:string}[]}
const ids=(value:unknown)=>Array.isArray(value)&&value.every(id)&&new Set(value).size===value.length
const validAnchor=(value:unknown)=>record(value)&&id(value.storyId)&&id(value.paragraphId)&&integer(value.offset)&&(value.branchId===null||id(value.branchId))&&(value.lineRevision===undefined||integer(value.lineRevision)&&(value.lineRevision as number)>0)
/** Runtime boundaries for the implemented kinds, including creates. */
export function validateLocalValue(kind:DomainKind,value:unknown,documentId?:string){
  const bad=(path:string):never=>{throw new ContractError('DOCUMENT_INVALID','对象合同无效：'+kind+' '+path+'；原数据保留')}
  if(!kinds.has(kind)||!record(value))bad('/')
  const v=value as Record<string,unknown>
  if(documentId?.startsWith('play-world:')&&kind!=='world'||documentId?.startsWith('play-checkpoint:')&&kind!=='checkpoint'||documentId?.startsWith('play-intent:')&&(kind!=='settings'||v.assetType!=='playable-intent'))bad('/playableNamespace')
  if(kind==='world')validatePlayWorld(v,documentId)
  if(kind==='checkpoint')validatePlayCheckpoint(v,documentId)
  if(kind==='settings'&&v.assetType==='playable-intent')validatePlayIntent(v,documentId)
  if(documentId?.startsWith('companion-draft:')&&(kind!=='settings'||v.assetType!=='companion-create-draft'))bad('/companionDraftNamespace')
  if(documentId?.startsWith('story-draft:')&&(kind!=='settings'||v.assetType!=='original-story-draft'))bad('/originalDraftNamespace')
  if(v.libraryMetadata!==undefined){if(kind!=='card'&&kind!=='companion'&&kind!=='world')bad('/libraryMetadataKind');validateLibraryMetadata(v.libraryMetadata)}
  if(v.promptPresetId!==undefined&&v.promptPresetId!==null&&!id(v.promptPresetId))bad('/promptPresetId')
  if(v.defaultPresetId!==undefined&&(v.defaultPresetId!==null&&!id(v.defaultPresetId)||!record(v.cardBindings)))bad('/defaultPresetId')
  if(kind==='story'){
    validateBookMetadata({title:v.title,favorite:v.favorite,tags:v.tags,...(v.pinned!==undefined?{pinned:v.pinned}:{}),...(v.groups!==undefined?{groups:v.groups}:{})})
    if(v.sourceId!==undefined&&!id(v.sourceId))bad('/sourceId')
    if((!text(v.title,512)||!(v.title as string).trim())||!['import','original'].includes(String(v.origin))||typeof v.immutableSource!=='boolean'||!hash(v.sourceHash)||!['txt','md','epub'].includes(String(v.format))||!text(v.encoding,100)||!Array.isArray(v.chapters)||!v.chapters.length||v.chapters.length>10000||!ids(v.paragraphIds)||!Array.isArray(v.tags)||typeof v.favorite!=='boolean')bad('/')
    const chapters=v.chapters as Record<string,unknown>[],flat=chapters.flatMap(chapter=>{if(!record(chapter)||!id(chapter.id)||!text(chapter.title,1000)||!ids(chapter.paragraphIds))bad('/chapters');return chapter.paragraphIds as string[]})
    if(new Set(chapters.map(chapter=>chapter.id)).size!==chapters.length||flat.length!==(v.paragraphIds as string[]).length||flat.some((item,index)=>item!==(v.paragraphIds as string[])[index]))bad('/paragraphIds')
  }else if(kind==='branch'){
    validateBranch(v)
  }else if(kind==='paragraph'){
    if(!id(v.id)||!id(v.chapterId)||!text(v.text,6_000_000)||!integer(v.sourceStart)||!integer(v.sourceEnd)||(v.sourceEnd as number)-(v.sourceStart as number)!==(v.text as string).length||!hash(v.textHash))bad('/')
    if(v.forkOf!==undefined&&(!record(v.forkOf)||!id(v.forkOf.paragraphId)||!integer(v.forkOf.offset)))bad('/forkOf')
    if(v.sliceOf!==undefined){const s=v.sliceOf;if(!record(s)||s.schema!=='novel-character-slice-v1'||!id(s.paragraphId)||!integer(s.revision)||s.revision===0||!integer(s.start)||!integer(s.end)||Number(s.end)<=Number(s.start)||Number(s.end)-Number(s.start)!==(v.text as string).length||s.offsetUnit!=='utf16'||Object.keys(s).some(k=>!['schema','paragraphId','revision','start','end','offsetUnit'].includes(k))||v.forkOf!==undefined||v.writingRunId!==undefined)bad('/sliceOf')}
    if(v.writingRunId!==undefined&&!id(v.writingRunId))bad('/writingRunId')
  }else if(kind==='source'){
    if((!text(v.name,1000)||!(v.name as string).trim())||!hash(v.sha256)||!integer(v.bytes)||(v.bytes as number)>100*1024*1024||!ids(v.chunkIds))bad('/')
  }else if(kind==='sourceChunk'){
    if(!text(v.data,2_000_000)||!hash(v.sha256)||!integer(v.bytes)||(v.bytes as number)>1024*1024)bad('/')
  }else if(kind==='progress'){
    if(!validAnchor(v))bad('/')
  }else if(kind==='note'){
    if(!validAnchor(v.anchor)||!text(v.text)||!text(v.createdAt,50))bad('/')
    if(v.quote!==undefined&&!text(v.quote)||v.group!==undefined&&!text(v.group,120)||v.retired!==undefined&&typeof v.retired!=='boolean'||v.conflictOf!==undefined&&!id(v.conflictOf))bad('/metadata')
  }else if(kind==='card'){
    validateNovelCardMetadata(v)
    if(v.container!==undefined&&(!record(v.container)||!['png','charx'].includes(String(v.container.format))||!id(v.container.sourceId)||!hash(v.container.sha256)))bad('/container')
    if(!id(v.identityId)||(!text(v.name,200)||!(v.name as string).trim())||!['v1','v2','v3'].includes(String(v.format))||!record(v.source)||!record(v.data)||['description','personality','scenario','firstMessage','exampleMessages','creatorNotes','systemPrompt','postHistoryInstructions'].some(field=>!text(v[field]))||!Array.isArray(v.tags)||!Array.isArray(v.alternateGreetings))bad('/')
  }else if(kind==='session'){
    if(v.importFile!==undefined&&(!record(v.importFile)||!id(v.importFile.sourceId)||!hash(v.importFile.sha256)||!['reader-jsonl','st-jsonl'].includes(String(v.importFile.format))))bad('/importFile')
    if(v.importMetadata!==undefined&&!record(v.importMetadata))bad('/importMetadata')
    if(v.regexIds!==undefined&&!ids(v.regexIds)||v.allowCardRegex!==undefined&&typeof v.allowCardRegex!=='boolean')bad('/regex')
    if(v.lorebookIds!==undefined&&!ids(v.lorebookIds))bad('/lorebookIds')
    if(v.loreState!==undefined&&(!record(v.loreState)||!integer(v.loreState.turn)||!Array.isArray(v.loreState.activations)||v.loreState.activations.length>5000||v.loreState.activations.some(row=>!record(row)||!id(row.bookId)||!id(row.entryId)||!integer(row.turn))))bad('/loreState')
    if(v.parent!==undefined&&v.parent!==null&&(!record(v.parent)||!id(v.parent.sessionId)||!id(v.parent.messageId)||Object.keys(v.parent).some(key=>!['sessionId','messageId'].includes(key))))bad('/parent')
    if(v.modelOverride!==undefined&&v.modelOverride!==null&&!id(v.modelOverride))bad('/modelOverride')
    if((!text(v.title,2000)||!(v.title as string).trim())||!Array.isArray(v.cards)||!v.cards.length||v.cards.length>8||!ids(v.messageIds)||!record(v.persona)||!text(v.persona.name,200)||!text(v.persona.description)||!['reader','roleplay'].includes(String(v.mode))||!text(v.authorNote)||typeof v.archived!=='boolean'||!text(v.createdAt,50)||v.currentTaskId!==null&&!id(v.currentTaskId))bad('/')
    const identities=new Set();for(const item of v.cards as unknown[]){if(!record(item)||!id(item.id)||!integer(item.revision)||item.revision===0)bad('/cards');const member=item as Record<string,unknown>;validateLocalValue('card',member.value);const identity=(member.value as Record<string,unknown>).identityId;if(identities.has(identity))bad('/cards/identityId');identities.add(identity)};if(v.group!==undefined)validateGroup(v.group,[...identities] as string[])
  }else if(kind==='message'){
    if(v.personaName!==undefined&&!text(v.personaName,200))bad('/personaName')
    if(!id(v.sessionId)||!['user','assistant','system'].includes(String(v.role))||v.speakerId!==null&&!id(v.speakerId)||!Array.isArray(v.variants)||!v.variants.length||typeof v.hidden!=='boolean'||!text(v.createdAt,50))bad('/')
    const variants=v.variants as Record<string,unknown>[];if(variants.some(variant=>!record(variant)||!id(variant.id)||!text(variant.text)||!['complete','partial'].includes(String(variant.state))||!text(variant.createdAt,50))||!variants.some(variant=>variant.id===v.selectedVariantId)||new Set(variants.map(variant=>variant.id)).size!==variants.length)bad('/variants')
  }else if(kind==='task'){
    if(v.promptTrace!==undefined&&(!record(v.promptTrace)||JSON.stringify(v.promptTrace).length>2_000_000))bad('/promptTrace')
    if(!id(v.operationId)||!hash(v.payloadHash)||!id(v.targetId)||!integer(v.targetRevision)||!record(v.configSnapshot)||!['queued','running','paused','unknown','received','saved','failed','cancelled'].includes(String(v.status))||!text(v.text)||!record(v.usage)||!Array.isArray(v.events))bad('/')
    if(v.promptTrace&&record(v.promptTrace)&&record(v.promptTrace.plugins)){validatePluginSnapshot(v.promptTrace.plugins.snapshot)}
    const config=v.configSnapshot as Record<string,unknown>;if(!id(config.id)||!integer(config.revision)||config.revision===0||!['chat-completions','responses','anthropic','gemini'].includes(String(config.protocol))||!text(config.baseUrl,2000)||!id(config.modelId)||!text(config.key,16000)||!integer(config.contextLimit)||!integer(config.outputLimit))bad('/configSnapshot')
    if(!['chat','rewrite','continue','draft'].includes(String(v.kind))||!id(v.sessionId)||v.sessionId!==v.targetId||!id(v.messageId)||!id(v.variantId)||!id(v.speakerId)||!integer(v.messageRevision)||typeof v.historical!=='boolean'||!Array.isArray(v.request)||!v.request.length||v.request.some(message=>!record(message)||!['system','user','assistant'].includes(String(message.role))||!text(message.content))||!text(v.createdAt,50)||!text(v.updatedAt,50)||!record(v.plan))bad('/request')
    const plan=v.plan as Record<string,unknown>;if(plan.maxCalls!==1||!integer(plan.estimatedInput)||!integer(plan.outputLimit)||!integer(plan.dailyCalls)||(plan.dailyCalls as number)<1||(plan.dailyCalls as number)>10000||!record(plan.timeouts)||['firstPacketMs','idleMs','totalMs'].some(field=>!integer((plan.timeouts as Record<string,unknown>)[field])||((plan.timeouts as Record<string,number>)[field]||0)<100||((plan.timeouts as Record<string,number>)[field]||0)>3600000))bad('/plan')
  }else if(kind==='companion'){
    if(v.world!==undefined)validateCompanionWorld(v.world)
    if(v.promotion!==undefined)validatePromotion(v.promotion)
    if(!id(v.identityId)||(!text(v.name,200)||!(v.name as string).trim())||!text(v.personality,20000)||(typeof v.style!=='string'||!['gentle','cheerful','reserved','thoughtful','playful','steady','custom'].includes(v.style))||!text(v.createdAt,50)||v.proactiveEnabled!==false)bad('/')
    if(v.user!==undefined&&(!record(v.user)||!text(v.user.name,200)||!text(v.user.description,12000)))bad('/user')
    if(v.sessionId!==undefined&&!id(v.sessionId))bad('/sessionId')
    if(v.modelOverride!==undefined&&v.modelOverride!==null&&!id(v.modelOverride))bad('/modelOverride')
    if(v.sourceCard!==undefined&&(!record(v.sourceCard)||!id(v.sourceCard.id)||!integer(v.sourceCard.revision)||v.sourceCard.revision===0))bad('/sourceCard')
  }else if(kind==='memory'){
    if(!id(v.companionId)||!text(v.text,8000)||!['active','retired'].includes(String(v.status))||!record(v.source)||!['user','message','import','promotion'].includes(String(v.source.kind))||!text(v.source.text,1_000_000)||!text(v.createdAt,50))bad('/')
    if((v.source as Record<string,unknown>).kind==='promotion')validatePromotedMemorySource(v.source)
  }else if(kind==='relationship'){
    if(!id(v.companionId)||v.algorithm!=='shared-events-v1'||!integer(v.wallet)||!Array.isArray(v.events)||!Array.isArray(v.anniversaries))bad('/')
    const events=v.events as Record<string,unknown>[];if(events.some(event=>!record(event)||!id(event.id)||!id(event.sourceId)||!['conversation','gift','anniversary'].includes(String(event.type))||!text(event.at,50)||!text(event.label,120))||new Set(events.map(event=>event.id)).size!==events.length)bad('/events')
    if((v.anniversaries as unknown[]).some(day=>!record(day)||!id(day.id)||!text(day.title,120)||typeof day.date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(day.date)||!Number.isFinite(Date.parse(day.date))))bad('/anniversaries')
  }else if(kind==='settings'){
    if(v.assetType==='import-entry')validateImportEntry(v,documentId)
    if(v.assetType==='library-view'){validateLibraryView(v);if(documentId!==undefined&&documentId!=='settings:library-view')bad('/libraryViewNamespace')}
    if(v.assetType==='companion-create-draft')validateCompanionCreation(v,documentId)
    if(v.assetType==='original-story-draft')validateOriginalStoryDraft(v,documentId)
    if(documentId==='settings:library-welcome'&&v.assetType!=='library-welcome')bad('/libraryWelcomeNamespace')
    if(v.assetType==='library-welcome'&&(Object.keys(v).sort().join(',')!=='assetType,dismissed'||typeof v.dismissed!=='boolean'||documentId!==undefined&&documentId!=='settings:library-welcome'))bad('/libraryWelcome')
    if(v.assetType==='card-editor-draft')validateCardDraft(v)
    if(v.assetType==='novel-character'){validateNovelCharacter(v)}
    if(v.assetType==='novel-character-scan')validateCharacterScan(v)
    if(v.assetType==='novel-character-index')validateCharacterIndex(v,documentId)
    if(v.assetType==='stage-state')validateStageState(v,documentId)
    if(v.assetType==='agent-team')validateAgentTeam(v,documentId)
    if(v.assetType==='chat-plugin')validateInstalledPlugin(v,documentId)
    if(v.assetType==='plugin-overrides')validatePluginOverrides(v,documentId)
    if(v.assetType==='plugin-report')validatePluginReport(v)
    if(v.assetType==='plugin-editor-draft')validatePluginDraft(v)
    if(v.assetType==='agent-skill')validateInstalledSkill(v,documentId)
    if(v.assetType==='skill-run')validateSkillRun(v,documentId)
    if(v.assetType==='reader-theme')validateReaderTheme(v,documentId)
    if(v.assetType==='skill-draft')validateSkillDraft(v,documentId)
    if(documentId==='settings:reading'&&v.customThemeId!==undefined&&v.customThemeId!==null&&(!id(v.customThemeId)||!String(v.customThemeId).startsWith('theme:')))bad('/customThemeId')
    if(documentId==='settings:reading')validateAppearanceFields(v)
    if(v.assetType==='reading-event')validateReadingEvent(v,documentId)
    if(v.assetType==='reading-goal')validateReadingGoal(v,documentId)
    if(v.assetType==='library-type')validateCollectionType(v,documentId)
    if(v.assetType==='stage-settings'){validateStageSettings(v);if(documentId!==undefined&&documentId!=='settings:stage')bad('/stageSettingsNamespace')}
    if(v.assetType==='media-provider')validateMediaProvider(v,documentId)
    if(v.assetType==='embedding-record')validateVectorRecord(v,documentId)
    if(v.assetType==='vector-draft')validateVectorDraft(v,documentId)
    if(v.assetType==='video-record')validateVideoRecord(v,documentId)
    if(v.assetType==='video-draft')validateVideoDraft(v,documentId)
    if(v.assetType==='media-compose'){
      if(documentId!==undefined&&!documentId.startsWith('media-compose:')||!['image','edit','tts','asr'].includes(String(v.type))||!text(v.prompt,v.type==='tts'?4096:20000)||v.referenceId!==null&&!id(v.referenceId)||!text(v.createdAt,50)||v.source!==null&&!record(v.source))bad('/mediaCompose')
      if(v.source)validateAssetLink(v.source)
    }
    if(documentId?.startsWith('budget:')){
      const day=documentId.slice(-10),profile=documentId.slice(7,-11)
      if(!id(profile)||documentId!=='budget:'+profile+':'+day||!/^\d{4}-\d{2}-\d{2}$/.test(day)||!Number.isFinite(Date.parse(day+'T00:00:00Z'))||new Date(day+'T00:00:00Z').toISOString().slice(0,10)!==day||!integer(v.calls)||v.day!==undefined&&v.day!==day||v.profileId!==undefined&&v.profileId!==profile||v.scope!==undefined&&v.scope!=='this-device-vault')bad('/dailyBudget')
    }
    if(documentId==='settings:media-defaults'&&(!record(v.profiles)||Object.entries(v.profiles).some(([key,value])=>!['image','tts','asr','embedding','video'].includes(key)||!id(value))))bad('/mediaDefaults')
    if(documentId==='settings:media-storage'&&(!integer(v.used)||!integer(v.limit)||(v.limit as number)<1||(v.limit as number)>2*1024*1024*1024))bad('/mediaStorage')
    if(documentId?.startsWith('settings:media-transcript:')&&(!id(v.jobId)||documentId!=='settings:media-transcript:'+v.jobId||!text(v.text)))bad('/mediaTranscript')
    if(v.assetType==='writing-run')validateWritingRun(v,documentId)
    if(v.assetType==='writing-edit')validateWritingEdit(v,documentId)
    if(v.assetType==='writing-preferences')validateWritingPreferenceLayer(v,documentId)
    if(v.assetType==='main-reading-line')validateMainReadingLine(v,documentId)
    if(documentId?.startsWith('settings:main-reading-line:')&&v.assetType!=='main-reading-line')bad('/mainReadingLine')
    if(v.assetType==='writing-compose-draft')validateWritingComposeDraft(v,documentId)
    if(v.assetType==='writing-compose-view')validateWritingComposeView(v,documentId)
    if(v.assetType==='writing-directions')validateWritingDirectionPlan(v,documentId)
    if(documentId?.startsWith('settings:writing:')&&v.assetType!=='writing-preferences'&&(!id(v.storyId)||documentId!=='settings:writing:'+v.storyId||!integer(v.count)||(v.count as number)<1||(v.count as number)>10||!integer(v.length)||(v.length as number)<100||(v.length as number)>10000||!text(v.direction,20000)||!text(v.style,10000)||!text(v.forbidden,10000)||!text(v.authorNote,20000)))bad('/writingSettings')
    if(v.assetType==='mcp-connector')validateMcpConnector(v,documentId)
    if(v.assetType==='mcp-invocation')validateMcpInvocation(v,documentId)
    if(v.assetType==='mcp-auth'&&(!id(v.connectorId)||!String(v.connectorId).startsWith('mcp:')||!text(v.token,16000)||/[\r\n]/.test(String(v.token))||documentId!==undefined&&documentId!=='mcp-auth:'+v.connectorId))bad('/mcpAuth')
    if(documentId?.startsWith('mcp-budget:')&&(!id(v.connectorId)||documentId!=='mcp-budget:'+v.connectorId+':'+documentId.slice(-10)||!/^\d{4}-\d{2}-\d{2}$/.test(documentId.slice(-10))||!Number.isFinite(Date.parse(documentId.slice(-10)+'T00:00:00Z'))||new Date(documentId.slice(-10)+'T00:00:00Z').toISOString().slice(0,10)!==documentId.slice(-10)||!integer(v.used)))bad('/mcpBudget')
    if(v.assetType==='agent-session'){validateAgentSession(v);if(documentId!==undefined&&!documentId.startsWith('agent-session:'))bad('/agentSessionNamespace')}
    if(documentId?.startsWith('agent-draft:')&&(!id(v.agentSessionId)||documentId!=='agent-draft:'+v.agentSessionId||!text(v.text,20000)))bad('/agentDraft')
    if(documentId?.startsWith('agent-draft-conflict:')&&(!id(v.agentSessionId)||!documentId.startsWith('agent-draft-conflict:'+v.agentSessionId+':')||!text(v.text,20000)||v.conflict!==true||!text(v.createdAt,50)))bad('/agentDraftConflict')
    if(documentId==='settings:prompt-presets'&&(v.defaultPresetId===undefined||!record(v.cardBindings)))bad('/presetBindings')
    if(documentId==='settings:personas'&&(v.defaultPersonaId===undefined||!record(v.cardBindings)))bad('/personaBindings')
    if(documentId==='settings:quick-replies'&&!ids(v.quickReplyIds))bad('/quickReplyBindings')
    if(documentId==='settings:regex-bindings'&&!ids(v.regexIds))bad('/regexBindings')
    if(documentId==='settings:lorebooks'&&!ids(v.lorebookIds))bad('/lorebookBindings')
    if(documentId==='settings:global-variables'||documentId?.startsWith('variables:session:')){
      if(!record(v.values)||Object.keys(v.values).length>1000||Object.entries(v.values).some(([key,value])=>!id(key)||['__proto__','constructor','prototype'].includes(key)||!(text(value,20000)||typeof value==='number'&&Number.isFinite(value)&&Math.abs(value)<=Number.MAX_SAFE_INTEGER)))bad('/variables')
      if(documentId==='settings:global-variables'?v.sessionId!==undefined:!id(v.sessionId)||documentId!=='variables:session:'+v.sessionId)bad('/variableScope')
    }
    if(v.cardBindings!==undefined&&((v.defaultPersonaId===undefined)===(v.defaultPresetId===undefined)))bad('/bindingType')
    if(v.defaultPersonaId!==undefined&&(!record(v.cardBindings)||v.defaultPersonaId!==null&&!id(v.defaultPersonaId)))bad('/personaBindings')
    if(v.defaultPersonaId!==undefined&&v.defaultPersonaId!==null&&!id(v.defaultPersonaId)||v.cardBindings!==undefined&&(!record(v.cardBindings)||Object.entries(v.cardBindings).some(([key,value])=>!id(key)||!id(value))))bad('/personaBindings')
    if(v.quickReplyIds!==undefined&&!ids(v.quickReplyIds))bad('/quickReplyIds')
    if(v.regexIds!==undefined&&!ids(v.regexIds))bad('/regexIds')
    if(documentId?.startsWith('injections:')&&(documentId!=='injections:'+v.sessionId||!id(v.sessionId)||!record(v.entries)||Object.keys(v.entries).length>32||Object.entries(v.entries).some(([key,value])=>!id(key)||['__proto__','constructor','prototype'].includes(key)||!text(value,12000))))bad('/injections')
    if(v.assetType==='script-run'&&(!record(v.source)||!id(v.source.setId)||!id(v.source.buttonId)||!integer(v.source.revision)||(v.source.revision as number)<1||!['running','completed','cancelled','failed'].includes(String(v.status))||!id(v.sessionId)))bad('/scriptRun')
    if(v.assetType==='quick-replies')validateReplies(v)
    if(v.assetType==='group-run'){if(!id(v.intentId)||!id(v.sessionId)||!integer(v.limit)||(v.limit as number)<1||(v.limit as number)>12||!integer(v.started)||(v.started as number)>(v.limit as number)||!['running','paused','stopped','completed'].includes(String(v.state))||!text(v.createdAt,50))bad('/groupRun');validateGroup(v.snapshot);if((v.limit as number)>(v.snapshot as {maxAutomaticTurns:number}).maxAutomaticTurns||documentId!==undefined&&documentId!=='group-run:'+v.sessionId)bad('/groupRunScope')}
    if(v.assetType==='prompt-preset'){if(!text(v.name,200))bad('/name');validatePreset(v.preset)}
    if(v.assetType==='regex'){if(!text(v.name,200))bad('/name');validateRegexRule(v.rule)}
    if(v.lorebookIds!==undefined&&!ids(v.lorebookIds))bad('/lorebookIds')
    if(v.slots!==undefined){const slots=['text','vision','image','speech','video','embedding'];if(Array.isArray(v.slots)){if(!v.slots.length||v.slots.some(slot=>!slots.includes(String(slot))))bad('/slots')}else if(!record(v.slots)||Object.entries(v.slots).some(([slot,profile])=>!slots.includes(slot)||!id(profile)))bad('/slots')}
  }else if(kind==='asset')validateAsset(v)
  else if(kind==='mediaJob')validateMediaJob(v)
  else if(kind==='agentRun')validateAgentRun(v)
  else if(kind==='changeSet'){validateAgentChange(v);validateLocalValue(v.targetKind,v.before);validateLocalValue(v.targetKind,v.after)}
  else if(kind==='lorebook')validateLorebook(v)
  else if(kind==='persona')validatePersona(v)
  else if(Object.keys(v).length===0)bad('/')
}

export async function validateSourceBytes(kind:DomainKind,value:unknown,lookup?:(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined){
  const invalid=():never=>{throw new ContractError('DOCUMENT_SOURCE_INVALID','原文件片段、长度或 hash 不一致；未写入任何数据')}
  const chunk=async(value:unknown)=>{const v=value as {data:string;bytes:number;sha256:string};let bytes:Uint8Array;try{bytes=Uint8Array.from(atob(v.data),c=>c.charCodeAt(0))}catch{return invalid()}if(bytes.length!==v.bytes||await sha256(bytes)!==v.sha256)invalid();return bytes}
  if(kind==='sourceChunk')await chunk(value)
  if(kind==='settings'&&(value as {assetType?:string}).assetType==='original-story-draft'){const v=value as OriginalStoryDraft;if(v.status==='completed'){const source=lookup?.(v.sourceId);if(source?.kind!=='source'||(source.value as {sha256:string;bytes:number}).sha256!==await sha256(v.text)||(source.value as {bytes:number}).bytes!==new TextEncoder().encode(v.text).length)invalid()}}
  if(kind==='settings'&&(value as {assetType?:string}).assetType==='writing-run'){
    for(const chapter of (value as WritingRun).chapters){if(chapter.eventLedger&&chapter.eventLedger.sourceHash!==await sha256(chapter.text))invalid();if(chapter.acceptedProof)await validateAcceptedProofBytes(chapter.acceptedProof,chapter.text)}
    if((value as WritingRun).acceptedBasis){if(!lookup)invalid();await acceptedBasisSourceBytes(value as WritingRun,lookup!)}
  }
  if(kind==='settings'&&(value as {assetType?:string}).assetType==='embedding-record'){const v=value as VectorRecord;if(await sha256(v.input)!==v.inputHash)invalid()}
  if(kind==='settings'&&(value as {assetType?:string}).assetType==='stage-state'){
    const v=value as StageState
    for(const overlay of v.overrides){const source=lookup?.(overlay.targetId)?.value as {text?:string;variants?:{text:string}[]}|undefined,possible=overlay.targetKind==='paragraph'?[source?.text||'']:(source?.variants||[]).map(row=>row.text);let matched=false;for(const text of possible)if(await sha256(text)===overlay.textHash&&unicodeOffset(text,overlay.start)===overlay.start&&unicodeOffset(text,overlay.end)===overlay.end&&overlay.end<=text.length){matched=true;break}if(!matched)invalid()}
    if(v.cursor){const source=lookup?.(v.cursor.targetId)?.value as {text?:string;variants?:{id:string;text:string}[]}|undefined,text=v.cursor.targetKind==='paragraph'?source?.text:source?.variants?.find(row=>row.id===v.cursor!.variantId)?.text;if(text===undefined||v.cursor.offset>text.length||unicodeOffset(text,v.cursor.offset)!==v.cursor.offset)invalid()}
  }
  if(kind==='source'){
    const source=value as {bytes:number;sha256:string;chunkIds:string[]},bytes=new Uint8Array(source.bytes);let offset=0
    for(const id of source.chunkIds){const row=lookup?.(id);if(!row||row.kind!=='sourceChunk')invalid();const data=await chunk(row!.value);if(offset+data.length>bytes.length)invalid();bytes.set(data,offset);offset+=data.length}
    if(offset!==bytes.length||await sha256(bytes)!==source.sha256)invalid()
  }
}

export function localReferences(kind:DomainKind,value:unknown):{id:string;kind:DomainKind}[]{
  const v=value as Record<string,unknown>,refs:{id:string;kind:DomainKind}[]=[],add=(id:unknown,kind:DomainKind)=>{if(typeof id==='string'&&id)refs.push({id,kind})}
  if(kind==='world'||kind==='checkpoint'||kind==='settings'&&v.assetType==='playable-intent')for(const ref of playableReferences(kind,value))add(ref.id,ref.kind)
  if(kind==='settings'&&v.assetType==='companion-create-draft')for(const ref of companionCreationReferences(v as unknown as CompanionCreationDraft))add(ref.id,ref.kind)
  if(kind==='settings'&&v.assetType==='original-story-draft')for(const ref of originalDraftReferences(v as unknown as OriginalStoryDraft))add(ref.id,ref.kind)
  if(kind==='settings'&&v.assetType==='import-entry'){add(v.sourceId,'source');add(v.storyId,'story')}
  for(const ref of novelReferences(kind,v))add(ref.id,ref.kind)
  for(const ref of editReferences(kind,v))add(ref.id,ref.kind)
  for(const ref of planningReferences(kind,v))add(ref.id,ref.kind)
  for(const ref of activityReferences(kind,v))add(ref.id,ref.kind)
  if(kind==='settings'&&v.assetType==='main-reading-line')refs.push(...mainLineReferences(v as unknown as MainReadingLine))
  for(const ref of promotionReferences(kind,v))add(ref.id,ref.kind)
  if(kind==='settings'&&v.assetType==='card-editor-draft'&&(v.cardRevision as number)>0)add(v.cardId,'card')
  if(kind==='settings'&&v.assetType==='stage-state')refs.push(...stageReferences(v as unknown as StageState))
  if(kind==='settings'&&v.assetType==='skill-draft'){add(v.skillId,'settings');add((v.input as Record<string,unknown>).targetId,'companion')}
  if(kind==='settings')add(v.customThemeId,'settings')
  if(kind==='story'){add(v.sourceId,'source');for(const id of v.paragraphIds as string[])add(id,'paragraph')}
  if(kind==='branch'){const b=v as unknown as LocalBranch;add(b.storyId,'story');add(b.parentBranchId,'branch');add(b.fork.paragraphId,'paragraph');for(const id of b.paragraphIds)add(id,'paragraph')}
  if(kind==='paragraph'&&v.forkOf)add((v.forkOf as Record<string,unknown>).paragraphId,'paragraph')
  if(kind==='paragraph')add(v.writingRunId,'settings')
  if(kind==='source')for(const id of v.chunkIds as string[])add(id,'sourceChunk')
  const assetLink=(link:AssetLink)=>{add(link.targetId,link.targetKind);add(link.paragraphId,'paragraph');add(link.branchId,'branch')}
  if(kind==='asset'){const asset=v as unknown as LocalAsset;add(asset.sourceId,'source');add(asset.parentAssetId,'asset');add(asset.jobId,'mediaJob');add(asset.videoRecordId,'settings');for(const link of asset.links)assetLink(link)}
  if(kind==='mediaJob'){const job=v as unknown as MediaJob;add(job.configSnapshot.id,'settings');for(const id of job.referenceAssetIds)add(id,'asset');add(job.outputAssetId,'asset');if(job.source)assetLink(job.source)}
  if(kind==='settings'&&v.assetType==='media-compose'){if(v.source)assetLink(v.source as AssetLink);add(v.referenceId,'asset')}
  if(kind==='settings'&&['embedding-record','vector-draft'].includes(String(v.assetType))){if(v.source)assetLink(v.source as AssetLink);if(v.assetType==='embedding-record')add((v.configSnapshot as {id:string}).id,'settings')}
  if(kind==='settings'&&['video-record','video-draft'].includes(String(v.assetType))){if(v.source)assetLink(v.source as AssetLink);add(v.referenceId,'asset');if(v.assetType==='video-record'){add((v.configSnapshot as {id:string}).id,'settings');add(v.outputAssetId,'asset')}}
  if(kind==='settings')add(v.profileId,'settings')
  if(kind==='settings'&&v.profiles)for(const id of Object.values(v.profiles as Record<string,string>))add(id,'settings')
  if(kind==='settings'&&v.jobId)add(v.jobId,'mediaJob')
  if(kind==='card'&&v.container)add((v.container as Record<string,unknown>).sourceId,'source')
  if(kind==='persona'&&v.sourceCard)add((v.sourceCard as Record<string,unknown>).id,'card')
  if(kind==='progress'||kind==='note'){const anchor=(kind==='note'?v.anchor:v) as Record<string,unknown>;add(anchor.storyId,'story');add(anchor.branchId,'branch');add(anchor.paragraphId,'paragraph')}
  if(kind==='session'){if(v.importFile)add((v.importFile as Record<string,unknown>).sourceId,'source');for(const id of v.messageIds as string[])add(id,'message');for(const card of v.cards as {value:{container?:{sourceId:string}}}[])if(card.value.container)add(card.value.container.sourceId,'source');add(v.companionId,'companion');if(v.parent){add((v.parent as Record<string,unknown>).sessionId,'session');add((v.parent as Record<string,unknown>).messageId,'message')}}
  if(kind==='message')add(v.sessionId,'session')
  if(kind==='companion'){add(v.sessionId,'session');if(v.sourceCard)add((v.sourceCard as Record<string,unknown>).id,'card')}
  if(kind==='memory'||kind==='relationship'){add(v.companionId,'companion');if(kind==='memory'&&(v.source as Record<string,unknown>).kind==='message'){add((v.source as Record<string,unknown>).messageId,'message');add((v.source as Record<string,unknown>).sessionId,'session')}if(kind==='relationship')for(const event of v.events as Record<string,unknown>[])if(event.type==='conversation')add(event.sourceId,'message')}
  if(kind==='task')add(v.targetId,'session')
  if(kind==='settings'&&v.assetType==='writing-run'){const run=v as unknown as WritingRun;add(run.configSnapshot.id,'settings');for(const subject of run.eventSubjects||[])add(subject.id,'settings');for(const ref of acceptedBasisReferences(run))add(ref.id,ref.kind)}
  if(kind==='settings'){if(v.assetType!=='agent-session'&&v.assetType!=='agent-team'&&v.assetType!=='companion-create-draft')add(v.sessionId,'session');if(v.assetType==='script-run')add((v.source as Record<string,unknown>).setId,'settings')}
  if(kind==='settings'&&v.assetType==='writing-run'){const run=v as unknown as WritingRun;add(run.storyId,'story');add(run.parentBranchId,'branch');add(run.anchor.paragraphId,'paragraph');for(const chapter of run.prefixChapters)for(const id of chapter.paragraphIds)add(id,'paragraph');for(const chapter of run.chapters)for(const id of chapter.paragraphIds)add(id,'paragraph');if(run.acceptedCount)add(run.targetBranchId,'branch')}
  if(kind==='settings'&&v.storyId&&v.assetType===undefined&&v.count!==undefined)add(v.storyId,'story')
  if(kind==='settings'&&v.assetType==='agent-session'){for(const id of v.runIds as string[])add(id,'agentRun');add(v.parentId,'settings')}
  if(kind==='settings'&&v.connectorId)add(v.connectorId,'settings')
  if(kind==='settings')add(v.agentSessionId,'settings')
  if(kind==='settings'&&v.assetType==='plugin-overrides'){const config=v as unknown as PluginOverrides;add(config.targetId,config.targetKind);for(const id of Object.keys(config.entries))add(id,'settings')}
  if(kind==='settings'&&v.assetType==='plugin-report'){const report=v as unknown as PluginReport;for(const ref of snapshotReferences(report.snapshot))add(ref.id,ref.kind);add(report.taskId,'task')}
  if(kind==='task'&&record(v.promptTrace)&&record(v.promptTrace.plugins))for(const ref of snapshotReferences(v.promptTrace.plugins.snapshot as unknown as PluginSnapshot))add(ref.id,ref.kind)
  if(kind==='settings'&&v.assetType==='agent-team'){add(v.parentId,'agentRun');add(v.sessionId,'settings');if((v as unknown as AgentTeam).configSnapshot)add((v as unknown as AgentTeam).configSnapshot!.id,'settings');for(const child of (v as unknown as AgentTeam).children)add(child.runId,'agentRun')}
  if(kind==='agentRun'){const run=v as unknown as AgentRun;if(run.delegation){add(run.delegation.teamId,'settings');add(run.delegation.parentId,'agentRun')}if(run.configSnapshot)add(run.configSnapshot.id,'settings');add(v.sessionId,'settings');for(const ref of v.references as AgentReference[])add(ref.id,ref.kind)}
  if(kind==='settings'&&v.assetType==='skill-run'){const run=v as unknown as SkillRun;add(run.skillId,'settings');for(const ref of run.references)add(ref.id,ref.kind);for(const profile of Object.values(run.configSnapshot||{}) as {id:string}[])add(profile.id,'settings');for(const resource of run.resourceIds)add(resource,resource.startsWith('media-job:')?'mediaJob':'settings')}
  if(kind==='changeSet'){add(v.sessionId,'settings');add(v.runId,'agentRun');add(v.targetId,v.targetKind as DomainKind)}
  if((kind==='session'||kind==='settings')&&Array.isArray(v.quickReplyIds))for(const id of v.quickReplyIds)add(id,'settings')
  if(kind==='settings'){add(v.defaultPersonaId,'persona');add(v.defaultPresetId,'settings');if(v.cardBindings&&record(v.cardBindings))for(const [cardId,personaId] of Object.entries(v.cardBindings)){add(cardId,'card');add(personaId,v.defaultPresetId!==undefined?'settings':'persona')}}
  if((kind==='session'||kind==='settings')&&Array.isArray(v.lorebookIds))for(const id of v.lorebookIds)add(id,'lorebook')
  if((kind==='session'||kind==='settings')&&Array.isArray(v.regexIds))for(const id of v.regexIds)add(id,'settings')
  if(kind==='settings'&&v.slots&&!Array.isArray(v.slots))for(const profile of Object.values(v.slots as Record<string,unknown>))add(profile,'settings')
  if(kind==='session'||kind==='companion')add(v.modelOverride,'settings');if(kind==='session')add(v.promptPresetId,'settings')
  return refs
}

export function validateLocalRelations(kind:DomainKind,id:string,value:unknown,lookup:(id:string,revision?:number)=>{kind:DomainKind;value:unknown;revision?:number}|undefined,options:{historicalModelCapabilities?:boolean}={}){
  const v=value as Record<string,unknown>,need=(id:unknown,kind:DomainKind)=>{const row=typeof id==='string'?lookup(id):null;if(!row||row.kind!==kind)throw new ContractError('DOCUMENT_REFERENCE_INVALID','引用对象不属于该作用域，未写入任何数据');return row.value as Record<string,unknown>},deny=()=>{throw new ContractError('DOCUMENT_REFERENCE_INVALID','引用归属不一致，未写入任何数据')}
  if(kind==='world'||kind==='checkpoint'||kind==='settings'&&v.assetType==='playable-intent')validatePlayableRelations(kind,id,value,lookup)
  if(kind==='settings'&&v.assetType==='companion-create-draft')validateCompanionCreationRelations(v as unknown as CompanionCreationDraft,lookup)
  if(kind==='settings'&&v.assetType==='original-story-draft')validateOriginalDraftRelations(v as unknown as OriginalStoryDraft,lookup)
  if(kind==='settings'&&v.assetType==='import-entry'){const queue=v as unknown as ImportEntry;if(queue.sourceId){const source=need(queue.sourceId,'source');if(source.sha256!==queue.sha256||source.bytes!==queue.bytes)deny()}if(queue.storyId&&need(queue.storyId,'story').sourceHash!==queue.sha256)deny()}
  validateNovelRelations(kind,id,v,lookup)
  validateEditRelations(kind,id,v,lookup)
  validatePlanningRelations(kind,v,lookup)
  validateActivityRelations(kind,v,lookup)
  if(kind==='settings'&&v.assetType==='main-reading-line')validateMainLineRelations(v as unknown as MainReadingLine,lookup)
  if(kind==='settings'&&v.assetType==='writing-run')writingEventRelations(v as unknown as WritingRun,lookup)
  if(kind==='settings'&&v.assetType==='writing-run'){const run=v as unknown as WritingRun,profile=lookup(run.configSnapshot.id,run.configSnapshot.revision);if(profile?.kind!=='settings'||profile.revision!==run.configSnapshot.revision)deny();validateWritingModel(run.configSnapshot,run.timeouts,run.dailyCalls,profile!.value as ProviderConfiguration,profile!.revision!,run.historical)}
  validatePromotionRelations(kind,v,lookup)
  if(kind==='settings'&&v.assetType==='card-editor-draft'){const draft=v as unknown as CardDraft;if(draft.cardRevision>0){const source=lookup(draft.cardId,draft.cardRevision);if(source?.kind!=='card'||source.revision!==draft.cardRevision||(source.value as {identityId:string}).identityId!==draft.identityId)throw new ContractError('CARD_DRAFT','角色草稿不属于这个身份版本；原角色保留')}}
  const mediaLink=(link:AssetLink)=>{const row=lookup(link.targetId,link.targetRevision);if(!row||row.kind!==link.targetKind||row.revision!==link.targetRevision)deny();const branch=link.branchId?lookup(link.branchId,link.branchRevision):null;if(link.branchId&&(branch?.kind!=='branch'||branch.revision!==link.branchRevision||(branch.value as {storyId?:string}).storyId!==link.targetId||link.targetKind!=='story'))deny();if(link.paragraphId){const line=(branch||row)!.value as Record<string,unknown>;if(!['story','branch'].includes(link.targetKind)||!Array.isArray(line.paragraphIds)||!line.paragraphIds.includes(link.paragraphId))deny()}}
  if(kind==='settings'&&v.assetType==='stage-state'){
    const state=v as unknown as StageState,source=need(state.targetId,state.targetKind),memberIds=(state.targetKind==='session'?source.messageIds:source.paragraphIds) as string[],memberKind=state.targetKind==='session'?'message':'paragraph'
    for(const card of state.cardIds)if(state.targetKind==='session'){if(!(source.cards as {id:string}[]).some(row=>row.id===card))deny()}else need(card,'card')
    for(const override of state.overrides){if(override.targetKind!==memberKind||!memberIds.includes(override.targetId))deny();const ref=need(override.targetId,memberKind);if(memberKind==='paragraph'&&(ref.textHash!==override.textHash||override.end>String(ref.text).length))deny();if(memberKind==='message'&&(ref.sessionId!==state.targetId||!(ref.variants as {text:string}[]).some(row=>row.text.length>=override.end)))deny()}
    if(state.cursor){if(state.cursor.targetKind!==memberKind||!memberIds.includes(state.cursor.targetId))deny();const ref=need(state.cursor.targetId,memberKind);if(memberKind==='paragraph'&&state.cursor.offset>String(ref.text).length||memberKind==='message'&&(ref.sessionId!==state.targetId||!(ref.variants as {id:string;text:string}[]).some(row=>row.id===state.cursor!.variantId&&row.text.length>=state.cursor!.offset)))deny()}
    for(const portrait of state.portraits)if(need(portrait.assetId,'asset').mediaKind!=='image')deny()
  }
  if(kind==='asset'){const asset=v as unknown as LocalAsset,source=need(asset.sourceId,'source');if(source.sha256!==asset.sha256||source.bytes!==asset.bytes)deny();for(const link of asset.links)mediaLink(link);if(asset.jobId){const job=need(asset.jobId,'mediaJob') as unknown as MediaJob;if(job.outputAssetId!==id||!['completed','cancelled'].includes(job.status)||asset.mediaKind!==(job.type==='tts'?'audio':job.type==='asr'?'none':'image'))deny()}if(asset.parentAssetId===id)deny()}
  if(kind==='mediaJob'){const job=v as unknown as MediaJob,profile=lookup(job.configSnapshot.id,job.configSnapshot.revision);if(profile?.kind!=='settings'||profile.revision!==job.configSnapshot.revision||(profile.value as Record<string,unknown>).assetType!=='media-provider'||canonical(profile.value)!==canonical(Object.fromEntries(Object.entries(job.configSnapshot).filter(([key])=>key!=='revision'))))deny();job.referenceAssetIds.forEach((reference,index)=>{if(need(reference,'asset').sha256!==job.referenceAssetHashes[index])deny()});if(job.source)mediaLink(job.source);if(job.outputAssetId){const asset=need(job.outputAssetId,'asset');if(asset.jobId!==id||asset.mediaKind!==(job.type==='tts'?'audio':job.type==='asr'?'none':'image'))deny()}}
  if(kind==='settings'&&v.assetType==='media-compose'){if(v.source)mediaLink(v.source as AssetLink);if(v.referenceId){const reference=need(v.referenceId,'asset');if(v.type==='edit'&&reference.mediaKind!=='image'||v.type==='asr'&&reference.mediaKind!=='audio')deny()}}
  if(kind==='settings'&&['embedding-record','vector-draft'].includes(String(v.assetType))){if(v.source)mediaLink(v.source as AssetLink);if(v.assetType==='embedding-record'){const config=(v as unknown as VectorRecord).configSnapshot,profile=lookup(config.id,config.revision);if(profile?.kind!=='settings'||profile.revision!==config.revision||canonical(profile.value)!==canonical(Object.fromEntries(Object.entries(config).filter(([key])=>key!=='revision'))))deny()}}
  if(kind==='asset'&&v.videoRecordId){const job=need(v.videoRecordId,'settings') as unknown as VideoRecord;if(job.assetType!=='video-record'||job.outputAssetId!==id||!['saved','cancelled'].includes(job.status)||v.mediaKind!=='video'||job.providerStatus!=='SUCCEEDED')deny()}
  if(kind==='settings'&&['video-record','video-draft'].includes(String(v.assetType))){if(v.source)mediaLink(v.source as AssetLink);if(v.referenceId){const asset=need(v.referenceId,'asset');if(!['image/png','image/jpeg','image/webp'].includes(String(asset.mime))||v.assetType==='video-record'&&asset.sha256!==v.referenceHash)deny()}if(v.assetType==='video-record'){const job=v as unknown as VideoRecord,profile=lookup(job.configSnapshot.id,job.configSnapshot.revision);if(profile?.kind!=='settings'||profile.revision!==job.configSnapshot.revision||canonical(profile.value)!==canonical(Object.fromEntries(Object.entries(job.configSnapshot).filter(([key])=>key!=='revision'))))deny();if(job.outputAssetId){const asset=need(job.outputAssetId,'asset');if(asset.videoRecordId!==id||asset.mediaKind!=='video')deny()}}}
  if(kind==='settings'&&v.profiles)for(const [purpose,id] of Object.entries(v.profiles as Record<string,string>)){const model=need(id,'settings');if(!options.historicalModelCapabilities&&(model.assetType!=='media-provider'||model.protocol!==({image:'openai-images',tts:'openai-tts',asr:'openai-asr',embedding:'openai-embeddings',video:'runway-video'} as Record<string,string>)[purpose]))deny()}
  if(kind==='settings'&&v.jobId){const job=need(v.jobId,'mediaJob');if(job.type!=='asr')deny()}
  if(kind==='paragraph'&&v.forkOf){const source=v.forkOf as {paragraphId:string;offset:number},original=need(source.paragraphId,'paragraph');if(source.paragraphId===id||source.offset<1||source.offset>=(original.text as string).length||(original.text as string).slice(0,source.offset)!==v.text||original.chapterId!==v.chapterId)deny()}
  if(kind==='paragraph'&&v.writingRunId){const run=need(v.writingRunId,'settings') as unknown as WritingRun,chapter=run.chapters?.find(c=>c.id===v.chapterId);if(run.assetType!=='writing-run'||!chapter)deny();if(!v.forkOf){const index=chapter!.paragraphIds.indexOf(id),part=acceptedWritingParagraphs(chapter!)[index];if(!part||part.text!==v.text||part.sourceStart!==v.sourceStart||part.sourceEnd!==v.sourceEnd||chapter!.paragraphIds.length!==acceptedWritingParagraphs(chapter!).length)deny()}}
  if(kind==='branch'||kind==='settings'&&v.assetType==='writing-run'){
    const b=v as unknown as LocalBranch&WritingRun,cut=kind==='branch'?b.fork:b.anchor,sourceId=b.parentBranchId||b.storyId,sourceKind=b.parentBranchId?'branch':'story',source=lookup(sourceId,b.parentRevision)
    if(!source||source.kind!==sourceKind||source.revision!==b.parentRevision||b.parentBranchId&&(source.value as LocalBranch).storyId!==b.storyId||sourceId===id)deny()
    const chapters=(source!.value as {chapters:import('./local-contract').Chapter[]}).chapters,paragraph=need(cut.paragraphId,'paragraph'),text=paragraph.text as string
    const tail=b.prefixChapters.at(-1)?.paragraphIds.at(-1),fragment=!b.edition&&cut.offset>0&&cut.offset<text.length?tail||null:null
    if(!b.edition&&canonical(prefixAt(chapters,cut.paragraphId,cut.offset,text,fragment))!==canonical(b.prefixChapters))deny()
    if(fragment){const derived=need(fragment,'paragraph');if(!record(derived.forkOf)||derived.forkOf.paragraphId!==cut.paragraphId||derived.forkOf.offset!==cut.offset||derived.text!==text.slice(0,cut.offset))deny()}
    if(kind==='branch'){for(const chapter of b.chapters)for(const paragraphId of chapter.paragraphIds)if(need(paragraphId,'paragraph').chapterId!==chapter.id)deny()}
    else if(b.append){const parent=source!.value as LocalBranch,last=parent.paragraphIds.at(-1);if(cut.paragraphId!==last||cut.offset!==text.length||canonical(b.prefixChapters)!==canonical(parent.chapters))deny()}
    if(kind==='settings'){
      for(let i=0;i<b.chapters.length;i++){
        const chapter=b.chapters[i]!
        if(i>=b.acceptedCount){if(chapter.paragraphIds.length)deny();continue}
        const parts=acceptedWritingParagraphs(chapter)
        if(chapter.paragraphIds.length!==parts.length)deny()
        for(const [index,paragraphId] of chapter.paragraphIds.entries()){const p=need(paragraphId,'paragraph'),part=parts[index]!;if(p.chapterId!==chapter.id||p.writingRunId!==id||p.forkOf||p.text!==part.text||p.sourceStart!==part.sourceStart||p.sourceEnd!==part.sourceEnd)deny()}
        if(chapter.acceptedProof)for(const proof of chapter.acceptedProof.segments){const p=lookup(proof.paragraphId,proof.paragraphRevision);if(p?.kind!=='paragraph'||p.revision!==proof.paragraphRevision||(p.value as Paragraph).text!==proof.text||(p.value as Paragraph).textHash!==proof.paragraphHash)deny()}
      }
      if(b.acceptedCount){const targetRow=lookup(b.targetBranchId,b.acceptedRevision),target=targetRow?.value as LocalBranch|undefined,expected=[...b.prefixChapters,...b.chapters.slice(0,b.acceptedCount).map(c=>({id:c.id,title:c.title,paragraphIds:c.paragraphIds}))];if(targetRow?.kind!=='branch'||targetRow.revision!==b.acceptedRevision||!target||target.storyId!==b.storyId||canonical(target.chapters)!==canonical(expected)||!b.append&&(target.parentBranchId!==b.parentBranchId||target.parentRevision!==b.parentRevision||target.storyRevision!==b.storyRevision||canonical(target.fork)!==canonical(b.anchor)||canonical(target.prefixChapters)!==canonical(b.prefixChapters)))deny()}
    }
  }
  if(kind==='settings'&&v.customThemeId){const theme=need(v.customThemeId,'settings');if(theme.assetType!=='reader-theme')deny()}
  if(kind==='settings'&&v.assetType==='skill-draft'){if(need(v.skillId,'settings').assetType!=='agent-skill')deny();for(const ref of v.selected as string[]){const row=lookup(ref);if(!row||!skillReadableKinds.includes(row.kind))deny()}}
  if(kind==='settings'&&v.assetType==='agent-session'){for(const ref of v.referenceIds as string[]){const row=lookup(ref);if(!row||!agentReadableKinds.includes(row.kind))deny()}for(const grant of v.grants as {targetId:string}[])if(!(v.referenceIds as string[]).includes(grant.targetId))deny();for(const runId of v.runIds as string[])if(need(runId,'agentRun').sessionId!==id)deny();if(v.parentId&&need(v.parentId,'settings').assetType!=='agent-session')deny()}
  if(kind==='settings'&&v.agentSessionId&&need(v.agentSessionId,'settings').assetType!=='agent-session')deny()
  if(kind==='settings'&&v.connectorId&&need(v.connectorId,'settings').assetType!=='mcp-connector')deny()
  if(kind==='settings'&&v.assetType==='mcp-invocation'){const source=lookup(String(v.connectorId),Number(v.connectorRevision)),connector=source?.value as {assetType?:string;url?:string;tools?:unknown[]}|undefined;if(source?.kind!=='settings'||source.revision!==v.connectorRevision||connector?.assetType!=='mcp-connector'||connector.url!==v.sourceUrl||!connector.tools?.some(tool=>canonical(tool)===canonical(v.tool)))deny()}
  if(kind==='agentRun'){const session=need(v.sessionId,'settings');if(session.assetType!=='agent-session')deny();for(const ref of v.references as AgentReference[]){const historical=lookup(ref.id,ref.revision);if(!historical||historical.kind!==ref.kind||historical.revision!==ref.revision||canonical(historical.value)!==canonical(ref.value))deny();const root=(v.references as AgentReference[]).find(root=>root.id===ref.rootId);if(!root||root.rootId!==root.id)deny();if(ref.id!==ref.rootId){if(root!.kind==='story'){if(ref.kind!=='paragraph'||!(root!.value.paragraphIds as string[]).includes(ref.id))deny()}else if(root!.kind==='session'){if(ref.kind!=='message'||!(root!.value.messageIds as string[]).includes(ref.id)||ref.value.sessionId!==root!.id||ref.value.hidden!==false)deny()}else deny()}}}
  if(kind==='settings'&&v.assetType==='plugin-overrides')validateOverrideRelations(v as unknown as PluginOverrides,lookup)
  if(kind==='settings'&&v.assetType==='plugin-report')validateReportRelations(v as unknown as PluginReport,lookup)
  if(kind==='task'&&record(v.promptTrace)&&record(v.promptTrace.plugins))validateSnapshotRelations(v.promptTrace.plugins.snapshot as unknown as PluginSnapshot,lookup)
  if(kind==='settings'&&v.assetType==='agent-team'){const team=v as unknown as AgentTeam,parent=lookup(team.parentId,team.parentRevision);if(!parent||parent.kind!=='agentRun'||parent.revision!==team.parentRevision||(parent.value as AgentRun).delegation)deny();const root=parent!.value as AgentRun;if(team.configSnapshot){const original=lookup(team.configSnapshot.id,team.configSnapshot.revision);if(!original||original.kind!=='settings'||original.revision!==team.configSnapshot.revision||canonical(original.value)!==canonical(team.configSnapshot.value)||root.configSnapshot&&canonical(root.configSnapshot)!==canonical(team.configSnapshot))deny()}if(root.sessionId!==team.sessionId||canonical(root.policy.limits)!==canonical(team.limits)||root.plan.length!==team.rootSteps||(root.provider?1:0)!==team.rootCalls)deny();for(const [index,child] of team.children.entries()){const run=need(child.runId,'agentRun') as unknown as AgentRun;if(run.sessionId!==root.sessionId||run.instruction!==child.instruction||run.delegation?.teamId!==id||run.delegation.parentId!==team.parentId||run.delegation.index!==index||canonical(run.delegation.tools)!==canonical(child.tools)||child.tools.some(tool=>!parentTools(root).includes(tool))||canonical(run.references)!==canonical(childReferences(root,child.referenceIds))||child.calls===0&&(run.provider!==undefined||run.configSnapshot!==undefined||['queued','planning','streaming'].includes(run.status)||!run.plan.length)||child.calls===1&&canonical(run.configSnapshot??null)!==canonical(team.configSnapshot??null)||run.policy.limits.steps!==child.steps||run.policy.limits.calls!==1||run.policy.limits.parallel!==1||run.policy.mode!==(root.policy.mode==='read'?'read':'confirm')||run.policy.persona!==root.policy.persona||run.policy.recordPrompt!==root.policy.recordPrompt||run.policy.grants.some(g=>!child.referenceIds.includes(g.targetId)||!root.policy.grants.some(p=>p.targetId===g.targetId&&g.tools.every(tool=>p.tools.includes(tool)&&child.tools.includes(tool)))))deny()}}
  if(kind==='agentRun'){const run=v as unknown as AgentRun;if(run.configSnapshot){const original=lookup(run.configSnapshot.id,run.configSnapshot.revision);if(!original||original.kind!=='settings'||original.revision!==run.configSnapshot.revision||canonical(original.value)!==canonical(run.configSnapshot.value))deny()}if(run.delegation){const team=need(run.delegation.teamId,'settings') as unknown as AgentTeam,allocation=team.children?.[run.delegation.index];if(team.assetType!=='agent-team'||team.parentId!==run.delegation.parentId||allocation?.runId!==id||canonical(allocation.tools)!==canonical(run.delegation.tools))deny();const parent=need(team.parentId,'agentRun') as unknown as AgentRun;if(canonical(run.references)!==canonical(childReferences(parent,allocation.referenceIds))||allocation.calls===1&&canonical(run.configSnapshot??null)!==canonical(team.configSnapshot??null)||allocation.calls===0&&(run.provider!==undefined||run.configSnapshot!==undefined||['queued','planning','streaming'].includes(run.status)||!run.plan.length))deny()}}
  if(kind==='settings'&&v.assetType==='skill-run'){const run=v as unknown as SkillRun,skill=lookup(run.skillId,run.skillRevision);if(!skill||skill.kind!=='settings'||skill.revision!==run.skillRevision||(skill.value as InstalledSkill).assetType!=='agent-skill'||canonical((skill.value as InstalledSkill).manifest)!==canonical(run.manifest)||run.grants.some(c=>!(skill.value as InstalledSkill).grants.includes(c)))deny();for(const ref of run.references){const original=lookup(ref.id,ref.revision);if(!original||original.kind!==ref.kind||original.revision!==ref.revision||canonical(original.value)!==canonical(ref.value))deny();if(ref.kind==='message'&&ref.value.hidden!==false)deny()}for(const [slot,profile] of Object.entries(run.configSnapshot||{}) as [string,{id:string;kind:string;revision:number;value:Record<string,unknown>}][]){if(!['text','image'].includes(slot)||profile.kind!=='settings')deny();const original=lookup(profile.id,profile.revision);if(!original||original.kind!=='settings'||original.revision!==profile.revision||canonical(original.value)!==canonical(profile.value)||slot==='image'&&profile.value.assetType!=='media-provider'||slot==='text'&&!profile.id.startsWith('model:'))deny()}for(const resource of run.resourceIds){const child=lookup(resource);if(!child||child.kind!=='mediaJob'&&(child.value as {assetType?:string}).assetType!=='writing-run')deny()}for(const p of run.output?.proposals||[])validateLocalValue(p.kind,p.after,p.id)}
  if(kind==='changeSet'){const run=need(v.runId,'agentRun'),ref=(run.references as AgentReference[]).find(ref=>ref.id===v.targetId);if(run.sessionId!==v.sessionId||!ref||ref.kind!==v.targetKind||ref.revision!==v.expectedRevision||canonical(ref.value)!==canonical(v.before)||!(run.plan as {id:string;tool:string}[]).some(call=>call.id===v.callId&&call.tool===v.tool))deny();if(need(v.sessionId,'settings').assetType!=='agent-session')deny()}
  if(kind==='settings'&&v.assetType==='script-run'){const source=v.source as {setId:string;revision:number;buttonId:string},row=lookup(source.setId,source.revision),set=row?.value as QuickReplyRelation|undefined;if(!row||row.kind!=='settings'||set?.assetType!=='quick-replies'||row.revision!==source.revision||!set.buttons.some(button=>button.id===source.buttonId))deny()}
  if((kind==='session'||kind==='settings')&&Array.isArray(v.quickReplyIds))for(const reference of v.quickReplyIds)if(need(reference,'settings').assetType!=='quick-replies')deny()
  if(kind==='settings'&&v.assetType==='group-run'){const session=need(v.sessionId,'session');validateGroup(v.snapshot,(session.cards as {value:{identityId:string}}[]).map(row=>row.value.identityId))}
  if(kind==='session'&&v.promptPresetId&&need(v.promptPresetId,'settings').assetType!=='prompt-preset')deny()
  if((kind==='persona'||kind==='companion')&&v.sourceCard){const source=v.sourceCard as {id:string;revision:number},card=lookup(source.id);if(!card||card.kind!=='card'||card.revision===undefined||source.revision>card.revision)deny()}
  if(kind==='settings'&&v.defaultPresetId!==undefined){if(v.defaultPresetId&&need(v.defaultPresetId,'settings').assetType!=='prompt-preset')deny();for(const reference of Object.values((v.cardBindings||{}) as Record<string,string>))if(need(reference,'settings').assetType!=='prompt-preset')deny()}
  if((kind==='session'||kind==='settings')&&Array.isArray(v.regexIds))for(const reference of v.regexIds)if(need(reference,'settings').assetType!=='regex')deny()
  if(kind==='story'){for(const chapter of v.chapters as {id:string;paragraphIds:string[]}[])for(const paragraphId of chapter.paragraphIds)if(need(paragraphId,'paragraph').chapterId!==chapter.id)deny();if(v.sourceId&&need(v.sourceId,'source').sha256!==v.sourceHash)deny()}
  if(kind==='card'&&v.container){const container=v.container as {sourceId:string;sha256:string};if(need(container.sourceId,'source').sha256!==container.sha256)deny()}
  if(kind==='session'&&v.importFile){const source=v.importFile as {sourceId:string;sha256:string};if(need(source.sourceId,'source').sha256!==source.sha256)deny()}
  if(kind==='session')for(const card of v.cards as {value:{container?:{sourceId:string;sha256:string}}}[])if(card.value.container&&need(card.value.container.sourceId,'source').sha256!==card.value.container.sha256)deny()
  if(kind==='session'){for(const messageId of v.messageIds as string[])if(need(messageId,'message').sessionId!==id)deny();if(v.companionId&&((v.cards as unknown[]).length!==1||need(v.companionId,'companion').identityId!==((v.cards as {value:{identityId:string}}[])[0]!.value.identityId)))deny();if(v.parent){const parent=v.parent as {sessionId:string;messageId:string},session=need(parent.sessionId,'session'),message=need(parent.messageId,'message');if(message.sessionId!==parent.sessionId||!(session.messageIds as string[]).includes(parent.messageId)||parent.sessionId===id)deny()}}
  if(kind==='message'){const session=need(v.sessionId,'session');if(v.role==='assistant'&&!(session.cards as {value:{identityId:string}}[]).some(card=>card.value.identityId===v.speakerId))deny()}
  if(kind==='companion'&&v.sessionId&&need(v.sessionId,'session').companionId!==id)deny()
  if(kind==='memory'&&(v.source as Record<string,unknown>).kind==='message'){const source=v.source as Record<string,unknown>,message=need(source.messageId,'message'),session=need(source.sessionId,'session');if(message.sessionId!==source.sessionId||session.companionId!==v.companionId)deny()}
  if(kind==='relationship')for(const event of v.events as {type:string;sourceId:string}[])if(event.type==='conversation'){const message=need(event.sourceId,'message'),session=need(message.sessionId,'session');if(session.companionId!==v.companionId)deny()}
  if(kind==='settings'&&v.slots&&!Array.isArray(v.slots))for(const [slot,profileId] of Object.entries(v.slots as Record<string,string>)){const profile=need(profileId,'settings');if(!profileId.startsWith('model:')||!Array.isArray(profile.slots)||!options.historicalModelCapabilities&&!profile.slots.includes(slot))deny()}
  if(kind==='progress'||kind==='note'){const anchor=(kind==='note'?v.anchor:v) as Record<string,unknown>,story=need(anchor.storyId,'story'),line=historicalReadingAnchor(anchor as unknown as import('./local-contract').ProgressAnchor,lookup),paragraph=need(anchor.paragraphId,'paragraph');if(anchor.branchId&&line.storyId!==anchor.storyId||!(line.paragraphIds as string[]).includes(anchor.paragraphId as string)||(anchor.offset as number)>(paragraph.text as string).length||unicodeOffset(paragraph.text as string,anchor.offset as number)!==anchor.offset)deny()}
}
import {validateMainReadingLine,mainLineReferences,validateMainLineRelations,type MainReadingLine} from '../writing/main-line'
