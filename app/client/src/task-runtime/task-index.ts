import type {Document,DomainKind} from '../domain/local-contract'
import type {TextTask} from './local-text'
import type {WritingRun} from '../writing/contract'
import type {MediaJob} from '../media/contract'
import type {VideoRecord} from '../media/video-contract'
import type {VectorRecord} from '../media/vector-contract'
import type {AgentRun} from '../agent/contract'
import type {SkillRun} from '../skills/contract'
import type {ImportEntry} from '../catalog/import-contract'
import type {OriginalStoryDraft} from '../catalog/original-story'
import type {CompanionCreationDraft} from '../companion/creation'
import type {PlayIntent} from '../playable/contract'
import type {CardDraft} from '../tavern/card-drafts'

export const taskModules={chat:'聊天',writing:'写作',media:'媒体',agent:'助手与技能',import:'导入',creation:'创建',automation:'自动操作'} as const
export type TaskModule=keyof typeof taskModules
export type TaskState='draft'|'file'|'queued'|'active'|'paused'|'approval'|'unknown'|'saving'|'completed'|'failed'|'cancelled'
export const taskStates:Record<TaskState,string>={draft:'草稿',file:'待选择文件',paused:'已暂停',queued:'待开始',active:'进行中',approval:'待确认',unknown:'待核对',saving:'待保存',completed:'已完成',failed:'未完成',cancelled:'已停止'}
export interface TaskDestination {path:string;query?:Record<string,string>}
export interface TaskIndexEntry {
 id:string;kind:DomainKind;revision:number;module:TaskModule;type:string;title:string;state:TaskState;status:string;
 createdAt:string;updatedAt:string;model:string|null;attempts:number|null;input:number|null;output:number|null;
 historical:boolean;hasText:boolean;error:string|null;destination:TaskDestination|null;sourceTitle:string|null;canStop:boolean;
}
type Lookup=(id:string)=>Document|undefined
type Obj=Record<string,unknown>
const object=(v:unknown):Obj=>v&&typeof v==='object'&&!Array.isArray(v)?v as Obj:{}
const text=(v:unknown)=>typeof v==='string'?v:''
function taskCleaner(v:Obj){
 const secrets=new Set<string>(),collect=(c:unknown)=>{const config=object(c),key=text(config.key)||text(object(config.value).key);if(key)secrets.add(key)}
 collect(v.configSnapshot);for(const config of Object.values(object(v.configSnapshot)))collect(config)
 return (s:string)=>[...secrets].reduce((value,key)=>value.split(key).join('[凭据已隐藏]'),s)
}
const state=(s:string):TaskState=>({editing:'draft',needs_file:'file',ready:'queued',queued:'queued',running:'active',calling:'active',planning:'active',streaming:'active',importing:'active',submitting:'active',submitted:'active',querying:'active',needs_approval:'approval',received:'saving',persistence_failed:'saving',unknown:'unknown',paused:'paused',partial:'failed',failed:'failed',rejected:'cancelled',cancelled:'cancelled',discarded:'cancelled',stopped:'cancelled',complete:'completed',completed:'completed',saved:'completed',applied:'completed',rolled_back:'completed'} as Record<string,TaskState>)[s]||'unknown'
const path=(base:string,id:string)=>base+encodeURIComponent(id)
function source(id:string,lookup:Lookup):{title:string|null;destination:TaskDestination|null}{
 const doc=lookup(id);if(!doc)return {title:null,destination:null};const v=object(doc.value),title=text(v.title)||text(v.name)||null
 if(doc.kind==='story')return {title,destination:{path:path('/local/read/',doc.id)}}
 if(doc.kind==='session')return {title,destination:{path:path('/local/chat/',doc.id)}}
 if(doc.kind==='world')return {title,destination:{path:path('/local/play/',doc.id)}}
 if(doc.kind==='companion')return {title,destination:{path:path('/local/companions/',doc.id)}}
 if(doc.kind==='card')return {title,destination:{path:'/local/characters',query:{card:doc.id}}}
 if(doc.kind==='settings'&&v.assetType==='agent-session')return {title,destination:{path:'/local/agent',query:{session:doc.id}}}
 return {title,destination:null}
}
/** Public projection only: provider credentials, prompt snapshots and tool data never enter the index. */
export function taskIndexEntry(doc:Document,lookup:Lookup):TaskIndexEntry|null{
 const v=object(doc.value),clean=taskCleaner(v)
 const base:TaskIndexEntry={id:doc.id,kind:doc.kind,revision:doc.revision,module:'creation',type:'',title:'',state:state(text(v.status)),status:text(v.status),createdAt:text(v.createdAt)||text(v.started)||doc.updatedAt,updatedAt:text(v.updatedAt)||doc.updatedAt,model:null,attempts:0,input:null,output:null,historical:v.historical===true,hasText:false,error:typeof v.error==='string'?v.error:typeof object(v.error).message==='string'?text(object(v.error).message):null,destination:null,sourceTitle:null,canStop:false}
 const from=(id:string)=>{const s=source(id,lookup);base.destination=s.destination;base.sourceTitle=s.title}
 if(doc.kind==='task'){
  const r=v as unknown as TextTask;if(!r.configSnapshot||!Array.isArray(r.events)||!r.usage)return null
  Object.assign(base,{module:'chat',type:'text',title:({chat:'角色回复',rewrite:'重新回复',continue:'继续回复',draft:'代写草稿'} as const)[r.kind]||'聊天请求',model:r.configSnapshot.modelId,attempts:r.attemptId?1:0,input:r.usage.input,output:r.usage.output,hasText:!!r.text,canStop:!r.historical&&['queued','running'].includes(r.status)});from(r.sessionId);if(base.destination)base.destination.query={task:doc.id}
 }else if(doc.kind==='mediaJob'){
  const r=v as unknown as MediaJob;if(!r.configSnapshot)return null
  Object.assign(base,{module:'media',type:'media',title:({image:'生成图片',edit:'改图',tts:'在线配音',asr:'语音转录'} as const)[r.type],model:r.configSnapshot.modelId,attempts:r.claimId?1:0,input:r.inputTokens,output:r.outputTokens,hasText:!!r.text,canStop:!r.historical&&['queued','calling','persistence_failed'].includes(r.status),destination:{path:'/local/gallery',query:{job:doc.id}},sourceTitle:r.source?source(r.source.targetId,lookup).title:null})
 }else if(doc.kind==='agentRun'){
  const r=v as unknown as AgentRun;if(!Array.isArray(r.plan)||!Array.isArray(r.results))return null
  Object.assign(base,{module:'agent',type:'agent',title:r.instruction||'助手任务',model:r.model?.modelId||r.configSnapshot?.value.modelId||null,attempts:r.provider?1:0,input:r.provider?.input??null,output:r.provider?.outputTokens??null,hasText:!!r.provider?.output||!!r.results.length,canStop:!r.historical&&!['completed','failed','cancelled','unknown'].includes(r.status)});from(r.sessionId)
 }else if(doc.kind!=='settings')return null
 else switch(v.assetType){
  case 'writing-run':{
   const r=v as unknown as WritingRun;if(!Array.isArray(r.chapters)||!r.configSnapshot)return null
   Object.assign(base,{module:'writing',type:'writing',title:r.purpose?({directions:'剧情方向',rewrite:'替换写法',expand:'扩写'} as const)[r.purpose.kind]:'从这里写',model:r.configSnapshot.modelId,attempts:r.chapters.filter(c=>c.attemptId).length,input:r.chapters.every(c=>c.attemptId===null||c.usage.input!==null)?r.chapters.reduce((n,c)=>n+(c.usage.input||0),0):null,output:r.chapters.every(c=>c.attemptId===null||c.usage.output!==null)?r.chapters.reduce((n,c)=>n+(c.usage.output||0),0):null,hasText:r.chapters.some(c=>!!c.text),canStop:!r.historical&&['queued','running'].includes(r.status)});from(r.storyId)
   if(base.destination){base.destination.query={...(r.parentBranchId?{branch:r.parentBranchId}:{}),paragraph:r.anchor.paragraphId,offset:String(r.anchor.offset),task:doc.id}}
   break
  }
  case 'video-record':{
   const r=v as unknown as VideoRecord;Object.assign(base,{module:'media',type:'video',title:'视频创作',model:r.configSnapshot.modelId,attempts:r.claimId?1:0,canStop:!r.historical&&['queued','submitting','submitted','querying','persistence_failed'].includes(r.status),destination:{path:'/local/video',query:{record:doc.id}},sourceTitle:r.source?source(r.source.targetId,lookup).title:null});break
  }
  case 'embedding-record':{
   const r=v as unknown as VectorRecord;Object.assign(base,{module:'media',type:'vector',title:r.purpose==='query'?'查找内容':'建立内容索引',model:r.configSnapshot.modelId,attempts:r.claimId?1:0,input:r.inputTokens,canStop:!r.historical&&['queued','calling','persistence_failed'].includes(r.status),destination:{path:'/local/vectors',query:{record:doc.id}},sourceTitle:r.source?source(r.source.targetId,lookup).title:null});break
  }
  case 'skill-run':{
   const r=v as unknown as SkillRun;Object.assign(base,{module:'agent',type:'skill',title:r.manifest.name,attempts:r.resourceIds.length?null:r.attemptId?1:0,input:r.provider?.input??null,output:r.provider?.output??null,hasText:!!r.output?.body||!!r.raw,destination:{path:'/local/agent/skills',query:{run:doc.id}},canStop:!r.historical&&['queued','running'].includes(r.status)});if(r.status==='ready')base.state='approval';break
  }
  case 'import-entry':{
   const r=v as unknown as ImportEntry;if(r.retired)return null
   Object.assign(base,{module:'import',type:'import',title:r.name,destination:r.storyId?source(r.storyId,lookup).destination:{path:'/local/library',query:{imports:'1'}},sourceTitle:r.storyId?source(r.storyId,lookup).title:null});break
  }
  case 'original-story-draft':{
   const r=v as unknown as OriginalStoryDraft;Object.assign(base,{type:'story-create',title:r.title||'新故事',hasText:!!r.text,sourceTitle:r.status==='completed'?source(r.storyId,lookup).title:null,destination:r.status==='completed'?source(r.storyId,lookup).destination:r.status==='discarded'?null:{path:'/local/new-story',query:{draft:doc.id}}});break
  }
  case 'companion-create-draft':{
   const r=v as unknown as CompanionCreationDraft;Object.assign(base,{type:'companion-create',title:r.name||'新伴侣',hasText:!!r.personality,sourceTitle:r.status==='completed'?source(r.companionId,lookup).title:null,destination:r.status==='completed'?source(r.companionId,lookup).destination:r.status==='discarded'?null:{path:'/local/companions',query:{draft:doc.id}}});break
  }
  case 'playable-intent':{
   const r=v as unknown as PlayIntent;Object.assign(base,{module:r.type==='create'?'creation':'automation',type:'playable',title:r.type==='create'?(r.definition?.name||'新文游'):r.type==='fork'?'新建文游存档':'保存文游行动',destination:source(r.worldId,lookup).destination||(r.type==='create'&&r.status==='ready'?{path:'/local/new-world',query:{intent:doc.id}}:null),sourceTitle:source(r.worldId,lookup).title});if(r.type!=='create'&&r.status==='ready')base.state='saving';break
  }
  case 'card-editor-draft':{
   const r=v as unknown as CardDraft;if(r.retired)return null
   Object.assign(base,{type:'card-create',title:r.fields.name||'角色草稿',state:'draft',status:'editing',hasText:!!r.fields.description,destination:{path:'/local/characters',query:{draft:doc.id}}});break
  }
  case 'group-run':Object.assign(base,{module:'automation',type:'group',title:'群聊编排',state:state(text(v.state)),status:text(v.state),attempts:null});from(text(v.sessionId));break
  case 'script-run':Object.assign(base,{module:'automation',type:'script',title:'快捷脚本',attempts:null});from(text(v.sessionId));break
  default:return null
 }
 base.title=clean(base.title).slice(0,200);base.error=base.error?clean(base.error):null;base.model=base.model?clean(base.model):null
 if(base.sourceTitle)base.sourceTitle=clean(base.sourceTitle).slice(0,200)
 return base
}
export function buildTaskIndex(documents:Document[]){
 const lookup=new Map(documents.map(d=>[d.id,d])),out:TaskIndexEntry[]=[]
 for(const doc of documents){const entry=taskIndexEntry(doc,id=>lookup.get(id));if(entry)out.push(entry)}
 return out.sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt)||a.id.localeCompare(b.id))
}
export function filterTaskIndex(rows:TaskIndexEntry[],query:string,module:TaskModule|'all',state:TaskState|'all'){
 const needle=query.trim().toLocaleLowerCase();return rows.filter(row=>(module==='all'||row.module===module)&&(state==='all'||row.state===state)&&(!needle||[row.title,row.sourceTitle,row.model,row.id].some(s=>s?.toLocaleLowerCase().includes(needle))))
}
export interface TaskBody {sections:{title:string;text:string}[];requestIds:string[];events:{at:string;label:string}[];assetId:string|null;children:string[]}
/** Readable received content stays separate from raw prompts and serialized configuration. */
export function taskBody(doc:Document):TaskBody{
 const v=object(doc.value),body:TaskBody={sections:[],requestIds:[],events:[],assetId:null,children:[]},add=(title:string,s:unknown)=>{if(typeof s==='string'&&s)body.sections.push({title,text:s})}
 if(doc.kind==='task'){const r=v as unknown as TextTask;add('已收到文字',r.text);if(r.providerRequestId)body.requestIds.push(r.providerRequestId);body.events=r.events.map(e=>({at:e.at,label:e.type}))}
 else if(doc.kind==='mediaJob'){const r=v as unknown as MediaJob;add('已收到文字',r.text);body.assetId=r.outputAssetId;if(r.requestId)body.requestIds.push(r.requestId)}
 else if(doc.kind==='agentRun'){const r=v as unknown as AgentRun;add('模型返回的计划',r.provider?.output);for(const result of r.results)add(result.tool,result.summary);if(r.provider?.requestId)body.requestIds.push(r.provider.requestId)}
 else switch(v.assetType){
  case 'writing-run':{const r=v as unknown as WritingRun;for(const c of r.chapters){add(c.title+' · '+({queued:'待写',running:'正在写',complete:'完整章节',partial:'已收到片段',failed:'未完成',unknown:'待核对'} as const)[c.status],c.text);if(c.providerRequestId)body.requestIds.push(c.providerRequestId)}break}
  case 'skill-run':{const r=v as unknown as SkillRun;add(r.output?.title||'已收到内容',r.output?.body||r.raw);body.children=[...r.resourceIds];if(r.provider?.requestId)body.requestIds.push(r.provider.requestId);break}
  case 'video-record':body.assetId=text(v.outputAssetId)||null;if(v.providerTaskId)body.requestIds.push(text(v.providerTaskId));if(v.requestId)body.requestIds.push(text(v.requestId));break
  case 'embedding-record':add('输入文字',v.input);if(Array.isArray(v.vector))add('索引',v.vector.length+' 维');if(v.requestId)body.requestIds.push(text(v.requestId));break
  case 'original-story-draft':add('已保存正文',v.text);break
  case 'companion-create-draft':add('性格与经历',v.personality);break
  case 'card-editor-draft':add('角色草稿',object(v.fields).description);break
 }
 const clean=taskCleaner(v)
 body.sections=body.sections.map(s=>({title:clean(s.title),text:clean(s.text)}));body.requestIds=[...new Set(body.requestIds.map(clean))];body.events=body.events.map(e=>({...e,label:clean(e.label)}));return body
}
