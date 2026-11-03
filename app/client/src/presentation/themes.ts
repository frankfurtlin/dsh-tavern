import {ContractError,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
export interface ReaderTheme {assetType:'reader-theme';name:string;colors:{accent:string;paper:string;canvas:string;ink:string;onAccent:string;quiet?:string;edge?:string;wash?:string;danger?:string;good?:string};enabled:boolean}
const hex=(v:unknown):v is string=>typeof v==='string'&&/^#[0-9a-f]{6}$/i.test(v)
export function colorContrast(a:string,b:string){const l=(s:string)=>[1,3,5].map(i=>{const n=parseInt(s.slice(i,i+2),16)/255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4}).reduce((total,n,i)=>total+n*[.2126,.7152,.0722][i]!,0),x=l(a),y=l(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)}
export function validateReaderTheme(value:unknown,id?:string):asserts value is ReaderTheme{
 const v=value as ReaderTheme
 if(!v||Array.isArray(v)||v.assetType!=='reader-theme'||id!==undefined&&!id.startsWith('theme:')||typeof v.name!=='string'||!v.name.trim()||v.name.length>100||!v.colors||Array.isArray(v.colors)||['accent','paper','canvas','ink','onAccent'].some(key=>!hex(v.colors[key as keyof ReaderTheme['colors']]))||typeof v.enabled!=='boolean'||colorContrast(v.colors.ink,v.colors.paper)<4.5||colorContrast(v.colors.ink,v.colors.canvas)<4.5||colorContrast(v.colors.onAccent,v.colors.accent)<4.5)throw new ContractError('READER_THEME','主题颜色或对比度无效；原主题保留')
}
function mix(a:string,b:string,weight:number){return '#'+[1,3,5].map(i=>Math.round(parseInt(a.slice(i,i+2),16)*(1-weight)+parseInt(b.slice(i,i+2),16)*weight).toString(16).padStart(2,'0')).join('')}
export function readerThemeStyle(theme:ReaderTheme|null):Record<string,string>{
 if(!theme)return {};validateReaderTheme(theme);const c=theme.colors
 const readable=(color:unknown)=>hex(color)&&colorContrast(color,c.paper)>=4.5&&colorContrast(color,c.canvas)>=4.5
 const semantic=(color:unknown,candidates:string[])=>readable(color)?color as string:candidates.find(readable)||c.ink
 const wash=hex(c.wash)&&colorContrast(c.ink,c.wash)>=4.5?c.wash:mix(c.canvas,c.accent,.12)
 return {'--accent':c.accent,'--paper':c.paper,'--canvas':c.canvas,'--ink':c.ink,'--on-accent':c.onAccent,
  '--quiet':semantic(c.quiet,[mix(c.ink,c.canvas,.28),c.ink]),'--edge':hex(c.edge)?c.edge:mix(c.canvas,c.ink,.24),
  '--wash':colorContrast(c.ink,wash)>=4.5?wash:c.canvas,
  '--danger':semantic(c.danger,['#A72932','#FF9CA5']), '--good':semantic(c.good,['#21684F','#9ED3B8'])}
}
const variables=['--accent','--paper','--canvas','--ink','--on-accent','--quiet','--edge','--wash','--danger','--good']
export function applyRootTheme(theme:ReaderTheme|null){const style=readerThemeStyle(theme);for(const variable of variables){if(style[variable])document.documentElement.style.setProperty(variable,style[variable]!);else document.documentElement.style.removeProperty(variable)}return style}
export async function selectedReaderTheme(vault:LocalVault):Promise<Document<ReaderTheme>|null>{const preference=await vault.read<{customThemeId?:string|null}>('settings:reading');if(!preference?.value.customThemeId)return null;const row=await vault.read<ReaderTheme>(preference.value.customThemeId);if(!row||row.kind!=='settings')throw new ContractError('READER_THEME','所选主题不存在');validateReaderTheme(row.value,row.id);return row}
