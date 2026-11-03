import {canonical,ContractError} from '../domain/local-contract'
export const pluginBytes=(value:string)=>new TextEncoder().encode(value).byteLength
export const hooks=['beforePrompt','afterText','beforeRender','afterCommit','onUserAction'] as const
export type PluginHook=typeof hooks[number]
export const pluginScopes=['messages','card','memory','lorebook'] as const
export type PluginScope=typeof pluginScopes[number]
export const pluginFields=['context','suggestions','displayText','confirmation'] as const
export type PluginField=typeof pluginFields[number]
export interface PluginManifest {schema:'reader-plugin-v1';id:string;name:string;version:string;description:string;hooks:PluginHook[];reads:PluginScope[];writes:PluginField[];modelCalls:0;budget:{steps:number;milliseconds:number;bytes:number};parameters:Record<string,string|number|boolean>}
export interface InstalledPlugin {assetType:'chat-plugin';manifest:PluginManifest;parameters?:Record<string,string|number|boolean>;source:string;builtin:string|null;enabled:boolean;grants:PluginScope[];deleted:boolean;createdAt:string}
export interface PluginOverrides {assetType:'plugin-overrides';targetId:string;targetKind:'session'|'story';entries:Record<string,{mode:'inherit'|'on'|'off';version?:string;parameters:Record<string,string|number|boolean>}>}
export interface PluginReference {id:string;revision:number;kind:PluginScope;title:string;text:string}
export interface PluginInput {hook:PluginHook;text:string;query:string;speaker:string;references:PluginReference[]}
export interface PluginSuggestion {title:string;text:string;sourceIds:string[]}
export interface PluginOutput {context?:string;suggestions?:PluginSuggestion[];displayText?:string;confirmation?:string;diagnostics?:string[]}
export interface FrozenPlugin {id:string;revision:number;value:InstalledPlugin;parameters:Record<string,string|number|boolean>;reason:'global'|'on'}
export interface PluginSnapshot {version:'plugins-v1';targetId:string;targetRevision:number;targetKind:'session'|'story';speakerId:string;plugins:FrozenPlugin[];references:PluginReference[];limits:{steps:number;calls:1;milliseconds:number;bytes:number};createdAt:string}
export interface PluginTrace {pluginId:string;revision:number;hook:PluginHook;state:'completed'|'failed'|'skipped';reason:string;milliseconds:number;modelCalls:0;output:PluginOutput|null}
export interface PluginReport {assetType:'plugin-report';targetId:string;taskId:string|null;snapshot:PluginSnapshot;trace:PluginTrace[];historical:boolean;createdAt:string}
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const text=(v:unknown,max:number)=>typeof v==='string'&&v.length<=max
const integer=(v:unknown,min:number,max:number)=>Number.isSafeInteger(v)&&Number(v)>=min&&Number(v)<=max
const unique=(value:unknown,allowed:readonly string[],max:number)=>Array.isArray(value)&&value.length<=max&&new Set(value).size===value.length&&value.every(item=>allowed.includes(item))
function fail(message:string):never{throw new ContractError('PLUGIN_CONTRACT',message+'；原内容保留')}
export function validateParameters(value:unknown):asserts value is Record<string,string|number|boolean>{if(!record(value)||Object.keys(value).length>24||Object.entries(value).some(([key,v])=>!text(key,80)||!key||!['string','number','boolean'].includes(typeof v)||typeof v==='string'&&v.length>4000||typeof v==='number'&&!Number.isFinite(v)))fail('插件参数无效')}
export function validatePluginManifest(value:unknown):asserts value is PluginManifest{
 if(!record(value)||value.schema!=='reader-plugin-v1'||typeof value.id!=='string'||!/^[a-z][a-z0-9-]{1,63}$/.test(value.id)||!text(value.name,100)||!String(value.name).trim()||!text(value.version,64)||!/^\d+\.\d+\.\d+$/.test(String(value.version))||!text(value.description,2000)||!unique(value.hooks,hooks,5)||!(value.hooks as unknown[]).length||!unique(value.reads,pluginScopes,4)||!unique(value.writes,pluginFields,4)||value.modelCalls!==0||!record(value.budget)||!integer(value.budget.steps,1,8)||!integer(value.budget.milliseconds,20,1000)||!integer(value.budget.bytes,256,65536))fail('插件 manifest 不符合版本化合同')
 validateParameters(value.parameters)
}
export function validateInstalledPlugin(value:unknown,id?:string):asserts value is InstalledPlugin{
 if(!record(value)||value.assetType!=='chat-plugin'||typeof value.enabled!=='boolean'||typeof value.deleted!=='boolean'||!text(value.source,128000)||value.builtin!==null&&(!text(value.builtin,64)||value.builtin!==String((value.manifest as PluginManifest)?.id))||!text(value.createdAt,100))fail('插件安装记录无效')
 validatePluginManifest(value.manifest)
 if(value.parameters!==undefined)validateParameters(value.parameters)
 if(id!==undefined&&id!=='plugin:'+value.manifest.id||!Array.isArray(value.grants)||!unique(value.grants,value.manifest.reads,4)||value.builtin===null&&!String(value.source).trim()||value.deleted&&value.enabled)fail('插件标识或权限无效')
}
export interface PluginEditorDraft {assetType:'plugin-editor-draft';manifest:string;source:string;conflict:boolean;createdAt:string}
export function validatePluginDraft(value:unknown):asserts value is PluginEditorDraft{if(!record(value)||value.assetType!=='plugin-editor-draft'||!text(value.manifest,20000)||!text(value.source,128000)||typeof value.conflict!=='boolean'||!text(value.createdAt,100))fail('插件草稿无效')}
export function validatePluginSnapshot(value:unknown):asserts value is PluginSnapshot{
 if(!record(value)||value.version!=='plugins-v1'||!text(value.targetId,160)||!value.targetId||!integer(value.targetRevision,1,1000000)||value.targetKind!=='session'||!text(value.speakerId,160)||!value.speakerId||!Array.isArray(value.plugins)||value.plugins.length>32||!Array.isArray(value.references)||value.references.length>1000||!record(value.limits)||value.limits.calls!==1||value.limits.steps!==8||value.limits.milliseconds!==5000||value.limits.bytes!==64000||!text(value.createdAt,100)||pluginBytes(canonical(value))>2*1024*1024)fail('插件快照无效')
 for(const plugin of value.plugins){if(!record(plugin)||!text(plugin.id,160)||!integer(plugin.revision,1,1000000)||!['global','on'].includes(String(plugin.reason)))fail('冻结插件无效');validateInstalledPlugin(plugin.value,String(plugin.id));validateParameters(plugin.parameters)}
 if(new Set(value.plugins.map(plugin=>plugin.id)).size!==value.plugins.length||value.references.some(ref=>!record(ref)||!text(ref.id,160)||!ref.id||!integer(ref.revision,1,1000000)||!pluginScopes.includes(ref.kind as PluginScope)||!text(ref.title,500)||!text(ref.text,1000000)))fail('插件引用无效')
}
export function validatePluginReport(value:unknown):asserts value is PluginReport{
 if(!record(value)||value.assetType!=='plugin-report'||!text(value.targetId,160)||value.taskId!==null&&!text(value.taskId,160)||typeof value.historical!=='boolean'||!text(value.createdAt,100)||!Array.isArray(value.trace)||value.trace.length>160)fail('插件诊断无效')
 validatePluginSnapshot(value.snapshot);if(value.targetId!==value.snapshot.targetId)fail('插件诊断目标不一致')
 for(const row of value.trace){if(!record(row)||!value.snapshot.plugins.some(plugin=>plugin.id===row.pluginId&&plugin.revision===row.revision)||!hooks.includes(row.hook as PluginHook)||!['completed','failed','skipped'].includes(String(row.state))||!text(row.reason,1000)||!integer(row.milliseconds,0,3600000)||row.modelCalls!==0)fail('插件诊断项无效');if(row.output!==null){const plugin=value.snapshot.plugins.find(plugin=>plugin.id===row.pluginId)!;validatePluginOutput(row.output,{hook:row.hook as PluginHook,text:'',query:'',speaker:'',references:value.snapshot.references.filter(ref=>plugin.value.grants.includes(ref.kind))},plugin.value)}}
}
export function validatePluginOverrides(value:unknown,id?:string):asserts value is PluginOverrides{
 if(!record(value)||value.assetType!=='plugin-overrides'||!text(value.targetId,160)||!value.targetId||!['session','story'].includes(String(value.targetKind))||!record(value.entries)||Object.keys(value.entries).length>32||id!==undefined&&id!=='plugin-overrides:'+value.targetId)fail('插件覆盖配置无效')
 for(const [id,v] of Object.entries(value.entries)){if(!id.startsWith('plugin:')||!record(v)||!['inherit','on','off'].includes(String(v.mode))||v.version!==undefined&&(!text(v.version,64)||!/^\d+\.\d+\.\d+$/.test(String(v.version))))fail('插件覆盖项无效');validateParameters(v.parameters)}
}
export function validatePluginOutput(value:unknown,input:PluginInput,plugin:InstalledPlugin):asserts value is PluginOutput{
 if(!record(value)||pluginBytes(canonical(value))>plugin.manifest.budget.bytes||Object.keys(value).some(key=>![...pluginFields,'diagnostics'].includes(key))||value.diagnostics!==undefined&&(!Array.isArray(value.diagnostics)||value.diagnostics.length>12||value.diagnostics.some(item=>!text(item,500))))fail('插件返回超出合同')
 for(const [field,item] of Object.entries(value)){if(field==='diagnostics')continue;if(!plugin.manifest.writes.includes(field as PluginField))fail('插件没有声明该写入能力');if(field==='suggestions'){if(!Array.isArray(item)||item.length>8||item.some(s=>!record(s)||!text(s.title,100)||!text(s.text,12000)||!Array.isArray(s.sourceIds)||s.sourceIds.length>64||s.sourceIds.some(id=>!input.references.some(ref=>ref.id===id))))fail('插件建议或来源无效')}else if(!text(item,32000))fail('插件文本超限')}
 if(value.context!==undefined&&input.hook!=='beforePrompt'||value.displayText!==undefined&&input.hook!=='beforeRender'||value.confirmation!==undefined&&input.hook!=='onUserAction')fail('插件不能在此 hook 改写字段')
}
