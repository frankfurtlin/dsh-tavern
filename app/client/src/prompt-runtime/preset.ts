import {ContractError,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {ChatSession} from '../tavern/chat-contract'
export const promptSlots=['description','personality','scenario','systemPrompt','exampleMessages','persona','authorNote','history','postHistoryInstructions','custom'] as const
export type PromptSlot=typeof promptSlots[number]
export interface PromptPreset {name:string;enabled:boolean;slots:{id:string;name:string;source:PromptSlot;role:'system'|'user'|'assistant';text:string;enabled:boolean}[];source?:Record<string,unknown>}
export interface PresetAsset {assetType:'prompt-preset';name:string;preset:PromptPreset}
export interface PresetDefaults {defaultPresetId:string|null;cardBindings:Record<string,string>}
export function defaultPreset():PromptPreset{return {name:'标准叙事',enabled:true,slots:promptSlots.filter(source=>source!=='custom').map(source=>({id:source,name:({description:'角色描述',personality:'性格',scenario:'场景',systemPrompt:'卡片系统提示',exampleMessages:'对话示例',persona:'用户身份',authorNote:'作者注释',history:'历史',postHistoryInstructions:'后置指令'})[source],source,role:'system',text:'',enabled:true}))}}
export function validatePreset(value:unknown):asserts value is PromptPreset{
 const v=value as PromptPreset,invalid=()=>{throw new ContractError('PRESET_INVALID','提示预设无效；没有改变原设置')}
 if(!v||typeof v!=='object'||Array.isArray(v)||typeof v.name!=='string'||!v.name.trim()||v.name.length>200||typeof v.enabled!=='boolean'||!Array.isArray(v.slots)||!v.slots.length||v.slots.length>32)invalid()
 if(v.slots.some(row=>!row||typeof row!=='object'||Array.isArray(row)||typeof row.id!=='string'||!row.id||row.id.length>160||typeof row.name!=='string'||row.name.length>200||!promptSlots.includes(row.source)||!['system','user','assistant'].includes(row.role)||typeof row.text!=='string'||row.text.length>20000||typeof row.enabled!=='boolean')||new Set(v.slots.map(row=>row.id)).size!==v.slots.length||v.slots.filter(row=>row.source==='history'&&row.enabled).length!==1)invalid()
 if(v.source!==undefined&&(!v.source||typeof v.source!=='object'||Array.isArray(v.source)))invalid()
}
/** Only known text/order slots execute. Unknown supplier settings remain source metadata. */
export function importPreset(text:string):PromptPreset{
 if(new TextEncoder().encode(text).length>2*1024*1024)throw new ContractError('PRESET_LIMIT','预设超过 2 MiB')
 let raw:Record<string,unknown>;try{raw=JSON.parse(text)}catch{throw new ContractError('PRESET_JSON','预设 JSON 无效')}
 if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new ContractError('PRESET_JSON','预设需要 JSON 对象')
 if(raw.type==='reader-prompt-preset-v1'){const preset=raw.preset;validatePreset(preset);exportPreset(preset);return preset}
 if(!Array.isArray(raw.prompts))throw new ContractError('PRESET_FORMAT','此预设格式尚未支持，请保留原文件')
 const map:Record<string,PromptSlot>={charDescription:'description',charPersonality:'personality',scenario:'scenario',dialogueExamples:'exampleMessages',personaDescription:'persona',chatHistory:'history',postHistoryInstructions:'postHistoryInstructions'},prompts=raw.prompts as Record<string,unknown>[],order=Array.isArray(raw.prompt_order)?(raw.prompt_order as Record<string,unknown>[]).find(row=>Array.isArray(row.order))?.order as Record<string,unknown>[]|undefined:undefined
 const ordered=order?.map(row=>({row:prompts.find(p=>p.identifier===row.identifier),enabled:row.enabled!==false})).filter(item=>!!item.row)||prompts.map(row=>({row,enabled:row.enabled!==false})),slots:PromptPreset['slots']=[]
 for(const item of ordered){const row=item.row!,source=map[String(row.identifier)]||'custom';if(source==='custom'&&typeof row.content!=='string')continue;slots.push({id:String(row.identifier||crypto.randomUUID()),name:typeof row.name==='string'?row.name:String(row.identifier||'自定义'),source,role:['system','user','assistant'].includes(String(row.role))?row.role as 'system'|'user'|'assistant':'system',text:typeof row.content==='string'?row.content:'',enabled:item.enabled})}
 if(!slots.some(row=>row.source==='history'&&row.enabled))slots.push({id:'reader-history',name:'历史',source:'history',role:'system',text:'',enabled:true})
 const preset:PromptPreset={name:typeof raw.name==='string'?raw.name:'导入预设',enabled:true,slots,source:raw};validatePreset(preset);exportPreset(preset);return preset
}
export function exportPreset(preset:PromptPreset){validatePreset(preset);const envelope={type:'reader-prompt-preset-v1',preset},pretty=JSON.stringify(envelope,null,2);if(new TextEncoder().encode(pretty).length<=2*1024*1024)return pretty;const compact=JSON.stringify(envelope);if(new TextEncoder().encode(compact).length>2*1024*1024)throw new ContractError('PRESET_LIMIT','预设超过可再次导入的 2 MiB 上限');return compact}
export async function resolvePreset(vault:LocalVault,session:ChatSession,speakerId:string){
 const defaults=await vault.read<PresetDefaults>('settings:prompt-presets');if(defaults&&(defaults.kind!=='settings'||defaults.value.defaultPresetId===undefined||!defaults.value.cardBindings||typeof defaults.value.cardBindings!=='object'||Array.isArray(defaults.value.cardBindings)))throw new ContractError('PRESET_REFERENCE','预设绑定格式无效，未发送请求');const member=session.cards.find(row=>row.value.identityId===speakerId),id=session.promptPresetId!==undefined?session.promptPresetId:member?defaults?.value.cardBindings[member.id]||defaults?.value.defaultPresetId:null
 const preconditions=[{id:'settings:prompt-presets',revision:defaults?.revision||0,kind:'settings' as const}]
 if(!id)return {preset:null as PromptPreset|null,snapshot:null as {id:string;revision:number;value:PromptPreset}|null,preconditions}
 const row=await vault.read<PresetAsset>(id);if(!row||row.kind!=='settings'||row.value.assetType!=='prompt-preset')throw new ContractError('PRESET_REFERENCE','绑定预设已失效，未发送请求');validatePreset(row.value.preset)
 preconditions.push({id:row.id,revision:row.revision,kind:'settings'});return {preset:row.value.preset.enabled?row.value.preset:null,snapshot:{id:row.id,revision:row.revision,value:row.value.preset},preconditions}
}
