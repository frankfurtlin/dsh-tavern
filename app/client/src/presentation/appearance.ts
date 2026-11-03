import {canonical,ContractError,sha256,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import {validateReaderTheme,type ReaderTheme} from './themes'

export interface ThemePreset {id:string;mode:'light'|'dark';value:ReaderTheme}
// Original palettes: each day palette has a separately tuned night companion.
const palettes=[
 ['warm','暖纸','烛夜','#F6EFDF','#FFFCF5','#292419','#AD432B','#191814','#24211B','#F2EBDD','#F1A38B'],
 ['pine','松风','松影','#EDF3EE','#F8FCF8','#20352A','#1D6954','#131E19','#1D2B23','#E4F0E8','#8BD6B0'],
 ['blue','晴蓝','蓝时','#EFF3F7','#FAFCFF','#233348','#345A7D','#151D29','#202C3A','#E5EDF9','#A0C6F3'],
 ['lilac','丁香','紫暮','#F3EEF5','#FCF9FF','#362C3C','#76527C','#201923','#2E2332','#EFE5F4','#D1ADE0'],
 ['tea','茶褐','茶炉','#F2EDE5','#FEFAF4','#372C21','#745434','#201B15','#30271E','#F1E6D7','#DBB487'],
 ['graphite','石墨','墨夜','#EFEFEF','#FBFBFB','#292C31','#50555D','#181B1F','#252A30','#ECEFF3','#BEC8D4'],
 ['chestnut','榛栗','栗火','#F4ECE9','#FFF9F7','#3F2826','#8B4540','#251918','#352522','#F7E6E1','#EDA999'],
 ['sea','海盐','潮声','#ECF4F3','#F8FDFC','#1D3637','#246B70','#122021','#1D3031','#DFF2F1','#8DCED0'],
 ['indigo','靛墨','靛海','#EEEFF5','#F9FAFF','#2A3047','#465782','#171B2D','#252C42','#E7EAF8','#ACBDEA'],
 ['bamboo','青竹','竹月','#EEF1E6','#FCFDF6','#303721','#526B38','#1B2115','#2A3220','#EAF0D9','#BCD391'],
 ['mist','雾紫','暮霭','#F0EDF5','#FBF9FF','#312B43','#645484','#1D1928','#2B263A','#EDE6F8','#C2B2E6'],
 ['coral','珊瑚','玫夜','#F6ECEF','#FFF8FA','#402733','#97465E','#26191F','#38242D','#F8E4EC','#EDA7BF'],
 ['wheat','麦穗','金穗','#F3EFE3','#FFFCF3','#39301F','#735B29','#211D13','#322B1D','#F5EDDB','#E4C77F'],
 ['mountain','山岚','青岚','#EAF1F0','#F6FCFA','#253834','#3C6960','#14211F','#22332F','#E2F1EB','#A0CFC0'],
 ['moon','月白','月深','#F4F3EE','#FFFFFA','#333630','#555F4C','#1B1F19','#292F25','#ECF0E6','#C3D0B4']
] as const
export const themePresets:ThemePreset[]=palettes.flatMap(([id,day,night,paper,canvas,ink,accent,darkPaper,darkCanvas,darkInk,darkAccent])=>[
 {id:'builtin:'+id,mode:'light',value:{assetType:'reader-theme',name:day,enabled:true,colors:{paper,canvas,ink,accent,onAccent:'#FFFFFF'}}},
 {id:'builtin:'+id+'-night',mode:'dark',value:{assetType:'reader-theme',name:night,enabled:true,colors:{paper:darkPaper,canvas:darkCanvas,ink:darkInk,accent:darkAccent,onAccent:'#151410'}}}
])
export const presetTheme=(id:string)=>themePresets.find(p=>p.id===id)||null
export interface AppearanceSelection {theme:'light'|'dark';fontSize:number;builtinThemeId:string|null;customThemeId:string|null;motion:'system'|'reduced'}
export function validateAppearanceFields(value:Record<string,unknown>){
 if(value.builtinThemeId!==undefined&&value.builtinThemeId!==null&&(typeof value.builtinThemeId!=='string'||!presetTheme(value.builtinThemeId)))throw new ContractError('APPEARANCE_THEME','所选内置配色不存在，原设置保留')
 if(value.motion!==undefined&&!['system','reduced'].includes(value.motion as string))throw new ContractError('APPEARANCE_MOTION','动效设置无效，原设置保留')
}
export function appearanceSelection(value:Record<string,unknown>={}):AppearanceSelection{
 validateAppearanceFields(value)
 return {theme:value.theme==='dark'?'dark':'light',fontSize:typeof value.fontSize==='number'&&Number.isFinite(value.fontSize)?Math.max(14,Math.min(36,value.fontSize)):19,
  builtinThemeId:typeof value.builtinThemeId==='string'?value.builtinThemeId:null,customThemeId:typeof value.customThemeId==='string'?value.customThemeId:null,motion:value.motion==='reduced'?'reduced':'system'}
}
export function themeMode(theme:ReaderTheme):'light'|'dark'{validateReaderTheme(theme);const hex=theme.colors.paper;return [1,3,5].reduce((sum,i)=>sum+parseInt(hex.slice(i,i+2),16),0)<384?'dark':'light'}
export async function resolvedAppearance(vault:LocalVault){
 const preference=await vault.read<Record<string,unknown>>('settings:reading'),selection=appearanceSelection(preference?.value),preset=selection.builtinThemeId?presetTheme(selection.builtinThemeId):null
 let theme=preset?.value||null,warning=''
 if(selection.customThemeId){const row=await vault.read<ReaderTheme>(selection.customThemeId);if(!row||row.kind!=='settings')warning='所选主题无法读取，已使用基础配色';else{validateReaderTheme(row.value,row.id);if(row.value.enabled)theme=row.value;else warning='所选主题已停用，已使用基础配色'}}
 return {preference,selection,theme,mode:theme?themeMode(theme):selection.theme,warning}
}
export const themeFileLimit=64*1024
export function parseThemeJSON(input:string):ReaderTheme{
 if(new TextEncoder().encode(input).length>themeFileLimit)throw new ContractError('THEME_FILE_LIMIT','主题文件超过 64 KiB')
 let value:unknown;try{value=JSON.parse(input.replace(/^\uFEFF/,''))}catch{throw new ContractError('THEME_JSON','主题 JSON 格式无效，原主题保留')}
 validateReaderTheme(value);return value
}
export function exportThemeJSON(value:ReaderTheme){
 validateReaderTheme(value)
 const encoder=new TextEncoder(),pretty=JSON.stringify(value,null,2)+'\n'
 if(encoder.encode(pretty).length<=themeFileLimit)return pretty
 const compact=JSON.stringify(value)
 if(encoder.encode(compact).length<=themeFileLimit)return compact
 throw new ContractError('THEME_FILE_LIMIT','主题文件超过 64 KiB，无法导出')
}
/** Same parsed content has a stable local identity and one create receipt, including a lost ACK. */
export async function importReaderTheme(vault:LocalVault,input:string){
 const value=JSON.parse(canonical(parseThemeJSON(input))) as ReaderTheme,digest=await sha256(canonical(value)),id='theme:import:'+digest,row=await vault.read<ReaderTheme>(id)
 if(row){if(row.kind!=='settings'||canonical(row.value)!==canonical(value))throw new ContractError('THEME_IMPORT_CONFLICT','这份主题的已保存版本有变化；原主题保留');return row}
 await vault.commitBundle('theme-import:'+digest,[{kind:'settings',id,expectedRevision:0,value}])
 return (await vault.read<ReaderTheme>(id))!
}
export async function saveAppearance(vault:LocalVault,preference:Document<Record<string,unknown>>|null,input:AppearanceSelection,selected:Document<ReaderTheme>|null,operationId:string){
 const frozen=JSON.parse(canonical(input)) as AppearanceSelection,previous=preference?JSON.parse(canonical(preference)) as Document<Record<string,unknown>>:null,custom=frozen.customThemeId&&selected?JSON.parse(canonical(selected)) as Document<ReaderTheme>:null
 validateAppearanceFields(frozen as unknown as Record<string,unknown>)
 if(!['light','dark'].includes(frozen.theme)||!Number.isFinite(frozen.fontSize)||frozen.fontSize<14||frozen.fontSize>36||frozen.builtinThemeId&&frozen.customThemeId)throw new ContractError('APPEARANCE_INVALID','外观设置无效，原设置保留')
 let base:Record<string,unknown>={}
 if(previous){
  if(previous.id!=='settings:reading'||previous.kind!=='settings'||!Number.isSafeInteger(previous.revision)||previous.revision<1)throw new ContractError('APPEARANCE_AUTHORITY','阅读设置来源无效，原设置保留')
  const original=await vault.read<Record<string,unknown>>('settings:reading',previous.revision)
  if(!original||original.kind!=='settings'||canonical(original.value)!==canonical(previous.value))throw new ContractError('APPEARANCE_AUTHORITY','阅读设置来源已变化，请重新查看；原设置保留')
  base=JSON.parse(canonical(original.value)) as Record<string,unknown>
 }
 if(frozen.customThemeId){if(!custom||custom.id!==frozen.customThemeId||custom.kind!=='settings')throw new ContractError('READER_THEME','所选主题不存在');validateReaderTheme(custom.value,custom.id);if(!custom.value.enabled)throw new ContractError('READER_THEME','所选主题已停用');const actual=await vault.read<ReaderTheme>(custom.id,custom.revision);if(!actual||actual.revision!==custom.revision||canonical(actual.value)!==canonical(custom.value))throw new ContractError('REVISION_CONFLICT','主题版本已变化，请重新查看')}
 const chosen=frozen.builtinThemeId?presetTheme(frozen.builtinThemeId)?.value:custom?.value,mode=chosen?themeMode(chosen):frozen.theme
 const receipt=await vault.commitBundle(operationId,[{kind:'settings',id:'settings:reading',expectedRevision:previous?.revision||0,value:{...base,...frozen,theme:mode,...(chosen?{paper:'theme'}:{})}}],undefined,custom?[{kind:'settings',id:custom.id,revision:custom.revision}]:[])
 return receipt
}
