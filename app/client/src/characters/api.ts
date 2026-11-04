import {api,authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'

export interface CardOut {
  id:string
  name:string
  description:string
  personality?:string
  scenario?:string
  firstMessage?:string
  systemPrompt?:string
  postHistoryInstructions?:string
  tags:string[]
  creator?:string|null
  source:'png'|'json'
  hasAvatar:boolean
  presetId?:string|null
  companion?:boolean
  chatTarget?:{worldId:string;lineId:string;checkpointId:string;characterId:string}|null
  createdAt?:string
  updatedAt?:string
}

export interface CardUpdate {
  name?:string
  description?:string
  personality?:string
  scenario?:string
  firstMessage?:string
  systemPrompt?:string
  postHistoryInstructions?:string
  tags?:string[]
  presetId?:string|null
  companion?:boolean
}

export interface LoreEntry {
  id:string
  name:string
  keys:string[]
  content:string
  enabled:boolean
  order:number
  source:'import'|'manual'
}

export interface LoreInput {
  name?:string
  keys:string[]
  content:string
  enabled?:boolean
  order?:number
}

export interface Preset {
  id:string
  name:string
  systemSuffix:string
  enabled:boolean
  createdAt?:string
  updatedAt?:string
}

export interface PresetInput {name:string;systemSuffix:string;enabled?:boolean}

export interface RegexScript {
  id:string
  name:string
  pattern:string
  replacement:string
  enabled:boolean
  order:number
}

export interface RegexScriptInput {name:string;pattern:string;replacement:string;enabled?:boolean;order?:number}

export interface ContextPreview {
  loreMatched:{id:string;name:string;keys:string[]}[]
  preset:{id:string;name:string}|null
  systemSuffix:string
  memories?:{id:string;title:string;salience:number}[]
  memorySection?:string
}

export type MemoryKind='fact'|'preference'|'relationship'|'event'
export interface Memory {
  id:string
  kind:MemoryKind
  title:string
  content:string
  salience:number
  enabled:boolean
  source:'manual'|'auto'
  updatedAt?:string
}
export interface MemoryInput {kind:MemoryKind;title:string;content:string;salience?:number;enabled?:boolean}
export interface MemoryExtractResult {added:Memory[];skipped:number}
export interface MemoryPage {items:Memory[]}

export interface CardPage {items:CardOut[]}
export type CardListResponse = CardPage | CardOut[]
export interface CardEnvelope {card:CardOut}
export interface LorePage {items:LoreEntry[]}
export interface PresetPage {items:Preset[]}
export interface RegexPage {items:RegexScript[]}

const PREFIX='/character-cards'
const PRESETS='/presets'
const REGEX='/regex-scripts'

// The B-step contract wraps single resources in an envelope, but the first
// step returned bare card objects; unwrap either shape so a backend rollout
// in either direction keeps the UI readable.
function unwrap<T>(value:unknown,key:string):T{
  if(value&&typeof value==='object'&&key in (value as Record<string,unknown>))return (value as Record<string,unknown>)[key] as T
  return value as T
}

function itemsOf<T>(value:unknown):T[]{
  if(Array.isArray(value))return value as T[]
  const items=(value as {items?:unknown}|null)?.items
  return Array.isArray(items)?items as T[]:[]
}

export async function listCards(q=''):Promise<CardOut[]>{
  const result=await api<CardListResponse>(PREFIX+(q?'?q='+encodeURIComponent(q):''))
  // The contract returns {items}; the bare-array branch keeps older builds readable.
  if(Array.isArray(result))return result
  return Array.isArray(result?.items)?result.items:[]
}

export async function importCard(file:File):Promise<{card:CardOut}>{
  const form=new FormData()
  form.append('file',file)
  return api<{card:CardOut}>(PREFIX+'/imports',{method:'POST',body:form})
}

export async function getCard(id:string):Promise<CardOut>{
  const result=await api<CardOut|CardEnvelope>(PREFIX+'/'+encodeURIComponent(id))
  return unwrap<CardOut>(result,'card')
}

export async function updateCard(id:string,patch:CardUpdate):Promise<CardOut>{
  const result=await api<CardOut|CardEnvelope>(PREFIX+'/'+encodeURIComponent(id),{method:'PATCH',body:patch})
  return unwrap<CardOut>(result,'card')
}

export async function startCardChat(id:string):Promise<{sessionId:string;cardId:string}>{
  return api<{sessionId:string;cardId:string}>(PREFIX+'/'+encodeURIComponent(id)+'/chats',{method:'POST'})
}

export async function deleteCard(id:string):Promise<void>{
  await api<unknown>(PREFIX+'/'+encodeURIComponent(id),{method:'DELETE'})
}

export async function listLore(cardId:string):Promise<LoreEntry[]>{
  const result=await api<LorePage|LoreEntry[]>(PREFIX+'/'+encodeURIComponent(cardId)+'/lore')
  return itemsOf<LoreEntry>(result)
}

export async function createLore(cardId:string,input:LoreInput):Promise<LoreEntry>{
  const result=await api<LoreEntry|{lore:LoreEntry}>(PREFIX+'/'+encodeURIComponent(cardId)+'/lore',{method:'POST',body:input})
  return unwrap<LoreEntry>(result,'lore')
}

export async function updateLore(cardId:string,loreId:string,patch:Partial<LoreInput>):Promise<LoreEntry>{
  const result=await api<LoreEntry|{lore:LoreEntry}>(PREFIX+'/'+encodeURIComponent(cardId)+'/lore/'+encodeURIComponent(loreId),{method:'PATCH',body:patch})
  return unwrap<LoreEntry>(result,'lore')
}

export async function deleteLore(cardId:string,loreId:string):Promise<void>{
  await api<unknown>(PREFIX+'/'+encodeURIComponent(cardId)+'/lore/'+encodeURIComponent(loreId),{method:'DELETE'})
}

export async function listPresets():Promise<Preset[]>{
  const result=await api<PresetPage|Preset[]>(PRESETS)
  return itemsOf<Preset>(result)
}

export async function createPreset(input:PresetInput):Promise<Preset>{
  const result=await api<Preset|{preset:Preset}>(PRESETS,{method:'POST',body:input})
  return unwrap<Preset>(result,'preset')
}

export async function updatePreset(id:string,patch:Partial<PresetInput>):Promise<Preset>{
  const result=await api<Preset|{preset:Preset}>(PRESETS+'/'+encodeURIComponent(id),{method:'PATCH',body:patch})
  return unwrap<Preset>(result,'preset')
}

export async function deletePreset(id:string):Promise<void>{
  await api<unknown>(PRESETS+'/'+encodeURIComponent(id),{method:'DELETE'})
}

export async function listRegexScripts():Promise<RegexScript[]>{
  const result=await api<RegexPage|RegexScript[]>(REGEX)
  return itemsOf<RegexScript>(result)
}

export async function createRegexScript(input:RegexScriptInput):Promise<RegexScript>{
  const result=await api<RegexScript|{regex:RegexScript}>(REGEX,{method:'POST',body:input})
  return unwrap<RegexScript>(result,'regex')
}

export async function updateRegexScript(id:string,patch:Partial<RegexScriptInput>):Promise<RegexScript>{
  const result=await api<RegexScript|{regex:RegexScript}>(REGEX+'/'+encodeURIComponent(id),{method:'PATCH',body:patch})
  return unwrap<RegexScript>(result,'regex')
}

export async function deleteRegexScript(id:string):Promise<void>{
  await api<unknown>(REGEX+'/'+encodeURIComponent(id),{method:'DELETE'})
}

export async function contextPreview(cardId:string,message:string):Promise<ContextPreview>{
  return api<ContextPreview>(PREFIX+'/'+encodeURIComponent(cardId)+'/context-preview',{method:'POST',body:{message}})
}


export async function listMemories(cardId:string):Promise<Memory[]>{
  const result=await api<MemoryPage|Memory[]>(PREFIX+'/'+encodeURIComponent(cardId)+'/memories')
  return itemsOf<Memory>(result)
}

export async function createMemory(cardId:string,input:MemoryInput):Promise<Memory>{
  const result=await api<Memory|{memory:Memory}>(PREFIX+'/'+encodeURIComponent(cardId)+'/memories',{method:'POST',body:input})
  return unwrap<Memory>(result,'memory')
}

export async function updateMemory(cardId:string,memoryId:string,patch:Partial<MemoryInput>):Promise<Memory>{
  const result=await api<Memory|{memory:Memory}>(PREFIX+'/'+encodeURIComponent(cardId)+'/memories/'+encodeURIComponent(memoryId),{method:'PATCH',body:patch})
  return unwrap<Memory>(result,'memory')
}

export async function deleteMemory(cardId:string,memoryId:string):Promise<void>{
  await api<unknown>(PREFIX+'/'+encodeURIComponent(cardId)+'/memories/'+encodeURIComponent(memoryId),{method:'DELETE'})
}

// Synchronous model-assisted extraction; the server returns {added,skipped}
// and reports model/parse failures as 502 MEMORY_EXTRACT_FAILED.
export async function extractMemories(cardId:string):Promise<MemoryExtractResult>{
  return api<MemoryExtractResult>(PREFIX+'/'+encodeURIComponent(cardId)+'/memories/extract',{method:'POST'})
}

export async function setCompanion(cardId:string,companion:boolean):Promise<CardOut>{
  const result=await api<CardOut|CardEnvelope>(PREFIX+'/'+encodeURIComponent(cardId)+'/companion',{method:'POST',body:{companion}})
  return unwrap<CardOut>(result,'card')
}

// <img src> cannot carry the Bearer token, so avatars are fetched as blobs.
export async function cardAvatarBlob(id:string):Promise<Blob|null>{
  const response=await fetch(backendBase()+'/api/reader/v1'+PREFIX+'/'+encodeURIComponent(id)+'/avatar',{credentials:'include',headers:authorizationHeaders()})
  return response.ok?response.blob():null
}
