import {authorizeTaskPlugins,persistTextPluginReport,pluginReportWrite} from '../plugins/task-hooks'
import type {GroupRun} from '../tavern/group'
import {scriptAuthority,type ScriptAuthority} from '../tavern/script-authority'
import {canonical,sha256,ContractError,type Document,type PromptMessage,type ProviderConfiguration,type ProviderSnapshot,type LocalTask} from '../domain/local-contract'
import {LocalVault,type WriteDocument} from '../repositories/local-vault'
import {streamText,ProviderFailure,endpoint,type Timeouts,type ProviderEvent} from '../provider-adapters/text'
import {observeRevocation} from './revocation'

export interface TextTask extends LocalTask {
  kind:'chat'|'rewrite'|'continue'|'draft';request:PromptMessage[];sessionId:string;messageId:string;messageRevision:number;variantId:string;speakerId:string;
  createdAt:string;updatedAt:string;finishReason:string|null;error:{code:string;message:string}|null;ownerId:string|null;leaseUntil:string|null;
  plan:{maxCalls:1;estimatedInput:number;outputLimit:number;dailyCalls:number;timeouts:Timeouts};historical:boolean;saveInputHash?:string;promptTrace?:Record<string,unknown>
}
const terminal=new Set(['received','saved','failed','cancelled','paused'])
export const estimateInput=(messages:PromptMessage[])=>messages.reduce((total,message)=>total+new TextEncoder().encode(message.content).length+16,256)
export async function prepareTextTask(config:Document<ProviderConfiguration>,request:PromptMessage[],targetId:string,targetRevision:number,kind:TextTask['kind'],messageId:string,variantId:string,speakerId:string,messageRevision=0,promptTrace?:Record<string,unknown>):Promise<TextTask>{
  const profile=config.value
  if(!profile.enabled||!profile.modelId.trim()||!profile.key.trim()&&!(new URL(profile.baseUrl).hostname==='127.0.0.1'||new URL(profile.baseUrl).hostname==='localhost'))throw new ContractError('MODEL_CONFIGURATION','请先配置可用的文本模型')
  if(!Number.isSafeInteger(profile.outputLimit)||profile.outputLimit<1||!Number.isSafeInteger(profile.contextLimit)||profile.contextLimit<=profile.outputLimit||!Number.isSafeInteger(profile.dailyCalls)||profile.dailyCalls<1||profile.dailyCalls>10000)throw new ContractError('MODEL_LIMITS','模型上下文、输出和日上限无效')
  if([profile.firstPacketMs,profile.idleMs,profile.totalMs].some(value=>!Number.isSafeInteger(value)||value<100||value>3600000))throw new ContractError('MODEL_TIMEOUT','模型等待时限无效')
  const snapshot:ProviderSnapshot={id:config.id,revision:config.revision,protocol:profile.protocol,baseUrl:profile.baseUrl,modelId:profile.modelId,key:profile.key,contextLimit:profile.contextLimit,outputLimit:profile.outputLimit,outputParameter:profile.outputParameter||'max_tokens',includeUsage:profile.includeUsage===true,queryStoredResponse:profile.queryStoredResponse===true}
  endpoint(snapshot)
  const messages=JSON.parse(canonical(request)) as PromptMessage[],estimatedInput=estimateInput(messages)
  if(estimatedInput+profile.outputLimit>profile.contextLimit)throw new ContractError('CONTEXT_LIMIT','上下文超过所设窗口。请减少历史或提高已确认的模型窗口，身份提示不会被悄悄裁掉')
  const trace=promptTrace?{promptTrace:JSON.parse(canonical(promptTrace)) as Record<string,unknown>}:{},operationId=crypto.randomUUID(),payloadHash=await sha256(canonical({operationId,targetId,targetRevision,kind,request:messages,configSnapshot:snapshot,messageId,variantId,speakerId,messageRevision,...trace}))
  const at=new Date().toISOString()
  return {operationId,payloadHash,targetId,targetRevision,configSnapshot:snapshot,status:'queued',providerRequestId:null,attemptId:null,text:'',usage:{input:null,output:null},events:[],kind,request:messages,sessionId:targetId,messageId,messageRevision,variantId,speakerId,createdAt:at,updatedAt:at,finishReason:null,error:null,ownerId:null,leaseUntil:null,plan:{maxCalls:1,estimatedInput,outputLimit:profile.outputLimit,dailyCalls:profile.dailyCalls,timeouts:{firstPacketMs:profile.firstPacketMs,idleMs:profile.idleMs,totalMs:profile.totalMs}},historical:false,...trace}
}
export class LocalTextExecution {
  private controller=new AbortController()
  constructor(private vault:LocalVault,private taskId:string,private changed:(task:Document<TextTask>)=>void=()=>{},private transport:typeof fetch=fetch){}
  stop(){this.controller.abort()}
  async execute():Promise<Document<TextTask>>{
    let document=await this.vault.read<TextTask>(this.taskId)
    if(!document||document.kind!=='task')throw new ContractError('TASK_NOT_FOUND','找不到这次请求')
    if(document.value.historical)throw new ContractError('HISTORICAL_TASK','恢复的历史请求只可阅读，不能执行')
    if(document.value.status!=='queued')throw new ContractError('TASK_ALREADY_SENT','这个请求已经启动，不能重复发送；可查看已收到的内容')
    if(document.value.attemptId||document.value.providerRequestId||document.value.events.length)throw new ContractError('TASK_ALREADY_SENT','这次请求已有执行记录，不能再次发送')
    const intent=document.value
    const scriptSources=intent.promptTrace?.scriptSources as ScriptAuthority[]|undefined,scriptConditions=scriptSources?await scriptAuthority(this.vault,intent.sessionId,scriptSources,this.controller.signal):[]
    if(intent.operationId!==this.taskId||intent.sessionId!==intent.targetId||await sha256(canonical({operationId:intent.operationId,targetId:intent.targetId,targetRevision:intent.targetRevision,kind:intent.kind,request:intent.request,configSnapshot:intent.configSnapshot,messageId:intent.messageId,variantId:intent.variantId,speakerId:intent.speakerId,messageRevision:intent.messageRevision,...(intent.promptTrace?{promptTrace:intent.promptTrace}:{})}))!==intent.payloadHash)throw new ContractError('TASK_INTENT_INVALID','请求意图校验失败，原记录保留')
    const groupIntent=intent.promptTrace?.groupRunId,group=typeof groupIntent==='string'?await this.vault.read<GroupRun>('group-run:'+intent.sessionId):null
    if(groupIntent&&(typeof groupIntent!=='string'||!group||group.kind!=='settings'||group.value.assetType!=='group-run'||group.value.intentId!==groupIntent||group.value.sessionId!==intent.sessionId||group.value.state!=='running'))throw new ContractError('GROUP_RUN_STOPPED','自动运行已停止，原请求保留且未发送')
    const target=await this.vault.read<{currentTaskId:string;cards:{value:{identityId:string}}[]}>(intent.targetId)
    if(!target||target.kind!=='session'||target.revision!==intent.targetRevision||target.value.currentTaskId!==this.taskId||!target.value.cards.some(card=>card.value.identityId===intent.speakerId))throw new ContractError('TASK_TARGET_CONFLICT','请求目标或角色已变化，未继续发送；请重新检查会话')
    const day=new Date().toISOString().slice(0,10),budgetId='budget:'+document.value.configSnapshot.id+':'+day
    const budget=await this.vault.read<{calls:number}>(budgetId),calls=budget?.value.calls||0
    if(calls>=document.value.plan.dailyCalls)throw new ContractError('DAILY_LIMIT','已达到此模型的设备日调用上限')
    const claimed=JSON.parse(canonical(document.value)) as TextTask
    claimed.status='running';claimed.attemptId=crypto.randomUUID();claimed.ownerId=crypto.randomUUID();claimed.leaseUntil=new Date(Date.now()+30000).toISOString();claimed.updatedAt=new Date().toISOString();claimed.events.push({seq:1,type:'request-intent-claimed',at:claimed.updatedAt})
    const receipts=await this.vault.commitBundle(crypto.randomUUID(),[{kind:'task',id:this.taskId,expectedRevision:document.revision,value:claimed},{kind:'settings',id:budgetId,expectedRevision:budget?.revision||0,value:{calls:calls+1,day,scope:'this-device-vault',profileId:claimed.configSnapshot.id}}],undefined,[{id:target.id,revision:target.revision,kind:'session'},...(group?[{id:group.id,revision:group.revision,kind:'settings' as const}]:[]),...scriptConditions])
    document={...document,revision:receipts[0]!.revision,value:claimed};this.changed(document)
    let persistence=Promise.resolve()
    const update=(mutator:(task:TextTask)=>void)=>{
      const work=persistence.then(async()=>{
      const next=JSON.parse(canonical(document!.value)) as TextTask;mutator(next);next.updatedAt=new Date().toISOString()
      next.leaseUntil=next.status==='running'?new Date(Date.now()+30000).toISOString():null
      const receipt=await this.vault.commit(crypto.randomUUID(),'task',this.taskId,document!.revision,next)
      document={...document!,revision:receipt.revision,value:next,updatedAt:receipt.committedAt};this.changed(document)
      });persistence=work.catch(()=>{});return work
    }
    const lock=()=>this.stop();window.addEventListener('reader-local-locked',lock);const forgetRevocation=observeRevocation(this.vault.id,claimed.targetId,lock)
    let heartbeat:ReturnType<typeof setInterval>|null=null
    try{
      if(!this.vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，请求没有继续发送')
      if(group){const current=await this.vault.read<GroupRun>(group.id);if(!current||current.value.intentId!==groupIntent||current.value.state!=='running')throw new ProviderFailure('REQUEST_STOPPED','自动运行在发送前已停止',false)}
      if(scriptSources)await scriptAuthority(this.vault,claimed.sessionId,scriptSources,this.controller.signal)
      await authorizeTaskPlugins(this.vault,claimed,this.controller.signal)
      if(this.controller.signal.aborted)throw new ProviderFailure('REQUEST_STOPPED','请求在发送前已停止',false)
      heartbeat=setInterval(()=>void update(()=>{}).catch(()=>this.stop()),5000)
      await streamText(claimed.configSnapshot,claimed.request,claimed.sessionId,async(event:ProviderEvent)=>{
        if(!this.vault.unlocked){this.stop();throw new ContractError('VAULT_LOCKED','私人库已锁定')}
        await update(task=>{if(event.type==='text'){task.text+=event.text;if(task.text.length>1_000_000)throw new ContractError('OUTPUT_LIMIT','输出超过本地上限，已收到的文字保留')}
          if(event.type==='request')task.providerRequestId=event.id
          if(event.type==='usage'){if(event.input!==null)task.usage.input=event.input;if(event.output!==null)task.usage.output=event.output}
          if(event.type==='finish')task.finishReason=event.reason
          task.events.push({seq:task.events.length+1,type:event.type,at:new Date().toISOString()})
        })
      },this.controller.signal,claimed.plan.timeouts,this.transport)
      if(heartbeat){clearInterval(heartbeat);heartbeat=null}
      await update(task=>{task.status=task.text?'received':'failed';if(!task.text)task.error={code:'NO_OUTPUT',message:'供应商结束了请求，但没有返回正文'};if(task.finishReason&&!['stop','end_turn'].includes(task.finishReason))task.error={code:'OUTPUT_PARTIAL',message:'模型在完整结束前停止；已收到的文字仍可保存'}})
    }catch(error){
      if(heartbeat){clearInterval(heartbeat);heartbeat=null}
      if(this.vault.unlocked){await update(task=>{task.status=error instanceof ProviderFailure&&!error.uncertain?'failed':'unknown';task.error={code:error instanceof ContractError?error.code:'EXECUTION_FAILED',message:error instanceof ContractError?error.message:'本地保存中断，已保存的片段仍在请求记录中'};task.events.push({seq:task.events.length+1,type:task.status,at:new Date().toISOString()})})}
      else throw error
    }finally{if(heartbeat)clearInterval(heartbeat);window.removeEventListener('reader-local-locked',lock);forgetRevocation();this.stop()}
    if(document!.value.status==='received')try{await persistTextPluginReport(this.vault,document!,AbortSignal.timeout(5000))}catch{if(this.vault.unlocked)await update(task=>{task.error={code:'PLUGIN_DIAGNOSTIC',message:'正文已收到；插件诊断未能保存'}}).catch(()=>{})}
    return document!
  }
}
/** Recover by GET only. This method cannot create a new supplier request. */
export async function queryStoredText(vault:LocalVault,id:string,signal:AbortSignal):Promise<Document<TextTask>>{
  const row=await vault.read<TextTask>(id)
  if(!row||row.kind!=='task')throw new ContractError('TASK_NOT_FOUND','找不到这次请求')
  const config=row.value.configSnapshot
  if(config.protocol!=='responses'||!config.queryStoredResponse||!row.value.providerRequestId)throw new ContractError('PROVIDER_QUERY_UNAVAILABLE','此配置没有可查询的供应商结果；可以保留片段或新建一次独立请求')
  const url=endpoint(config).replace(/\/responses$/,'/responses/'+encodeURIComponent(row.value.providerRequestId)),controller=new AbortController(),abort=()=>controller.abort(signal.reason),lock=()=>controller.abort()
  signal.addEventListener('abort',abort,{once:true});window.addEventListener('reader-local-locked',lock)
  if(signal.aborted)abort()
  try{
  if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，查询没有继续发送')
  const response=await fetch(url,{method:'GET',headers:{Authorization:'Bearer '+config.key},credentials:'omit',cache:'no-store',redirect:'error',signal:controller.signal})
  if(!response.ok)throw new ContractError('PROVIDER_QUERY_FAILED','供应商查询返回 HTTP '+response.status)
  const data=await response.json();if(!vault.unlocked)throw new ContractError('VAULT_LOCKED','私人库已锁定，查询没有继续');if(data.id!==row.value.providerRequestId)throw new ContractError('PROVIDER_QUERY_MISMATCH','返回结果与原请求不匹配')
  if(!['completed','incomplete','failed','cancelled'].includes(data.status))return row
  const value=JSON.parse(canonical(row.value)) as TextTask
  value.text=(data.output||[]).filter((item:any)=>item.type==='message'&&item.role==='assistant').flatMap((item:any)=>(item.content||[]).filter((part:any)=>part.type==='output_text'&&typeof part.text==='string').map((part:any)=>part.text)).join('')
  value.status=value.text?'received':'failed';value.finishReason=data.status==='completed'?'stop':data.status;value.updatedAt=new Date().toISOString();value.error=data.status==='completed'?null:{code:'OUTPUT_PARTIAL',message:'已找回供应商保存的结果；请求未完整结束'}
  if(data.usage)value.usage={input:typeof data.usage.input_tokens==='number'?data.usage.input_tokens:null,output:typeof data.usage.output_tokens==='number'?data.usage.output_tokens:null}
  await vault.commit(crypto.randomUUID(),'task',id,row.revision,value)
  return (await vault.read<TextTask>(id))!
  }finally{signal.removeEventListener('abort',abort);window.removeEventListener('reader-local-locked',lock);controller.abort()}
}
export async function markTextSaved(vault:LocalVault,task:Document<TextTask>,documents:WriteDocument[]){
  const saveInputHash=await sha256(canonical(documents))
  if(await vault.receipt('save-task:'+task.id)){
    const saved=await vault.read<TextTask>(task.id)
    if(saved?.value.status!=='saved'||saved.value.saveInputHash!==saveInputHash)throw new ContractError('IDEMPOTENCY_CONFLICT','这次保存内容与已确认的原保存不同；原结果保留')
    return
  }
  if(task.value.status==='saved')throw new ContractError('TASK_STATE_INVALID','保存回执缺失，原记录保留，请检查私人库')
  if(!task.value.text)throw new ContractError('NO_OUTPUT','此请求没有可保存的文字')
  const value=JSON.parse(canonical(task.value)) as TextTask;value.status='saved';value.saveInputHash=saveInputHash
  await vault.commitBundle('save-task:'+task.id,[...documents,{kind:'task',id:task.id,expectedRevision:task.revision,value}])
  try{const pluginWrite=await pluginReportWrite(vault,task,'afterCommit',AbortSignal.timeout(5000));if(pluginWrite)await vault.commitBundle('plugins-after-commit:'+task.id,[pluginWrite])}catch{/* Plugin diagnostics cannot roll back or block a saved main reply. */}
}
/** Expired local ownership becomes unknown; no supplier POST is permitted. */
export async function recoverExpiredExecution(vault:LocalVault,row:Document<TextTask>):Promise<Document<TextTask>>{
  if(row.value.status!=='running'||row.value.leaseUntil&&Date.parse(row.value.leaseUntil)>Date.now())return row
  const value=JSON.parse(canonical(row.value)) as TextTask;value.status='unknown';value.leaseUntil=null;value.updatedAt=new Date().toISOString();value.error={code:'EXECUTION_DISCONNECTED',message:'原执行页面已断开，结束与费用待确认。已收到的文字保留，不会自动重发'}
  try{await vault.commit(crypto.randomUUID(),'task',row.id,row.revision,value)}catch(error){if(!(error instanceof ContractError&&error.code==='REVISION_CONFLICT'))throw error}
  return (await vault.read<TextTask>(row.id))!
}
export function taskPublicRecord(task:Document<TextTask>){
  const value=task.value
  return {id:task.id,status:value.status,model:value.configSnapshot.modelId,protocol:value.configSnapshot.protocol,providerRequestId:value.providerRequestId,createdAt:value.createdAt,text:value.text,usage:value.usage,finishReason:value.finishReason,error:value.error,estimatedInput:value.plan.estimatedInput,outputLimit:value.plan.outputLimit,events:value.events,historical:value.historical}
}
