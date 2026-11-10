// Tavern 2.0 client contract (docs/step6-native-tavern.md §4).
// All paths are relative to the shared /api/reader/v1 prefix handled by api().
import {api,authorizationHeaders,newKey} from '../api'
import {backendBase} from '../platform/bridge'

export const TAVERN='/tavern'

export interface TavernCharacter{
  id:string
  name:string
  description:string
  personality?:string
  scenario?:string
  firstMessage?:string
  systemPrompt?:string
  postHistoryInstructions?:string
  exampleDialogue?:string|null
  tags:string[]
  creator?:string|null
  hasAvatar:boolean
  avatarUrl?:string|null
  lastChatAt?:string|null
  chatCount?:number
  createdAt?:string
  updatedAt?:string
}

export interface CharacterPatch{
  name?:string
  description?:string
  personality?:string
  scenario?:string
  firstMessage?:string
  systemPrompt?:string
  postHistoryInstructions?:string
  tags?:string[]
}

export interface ChatSampling{
  temperature:number
  topP:number
  maxTokens:number
  stop:string[]
}

export interface TavernMember{
  cardId:string
  name:string
  order?:number
  avatarPath?:string|null
  deleted?:boolean
}

export interface TavernChat{
  id:string
  cardId:string
  mode?:'single'|'group'
  members?:TavernMember[]
  title:string
  modelName?:string|null
  characterName?:string|null
  aliveMemberCount?:number|null
  lastMessage?:string|null
  sampling?:ChatSampling|null
  createdAt?:string
  updatedAt?:string
  lastMessageAt?:string|null
}

export interface Swipe{content:string;createdAt?:string;model?:string|null}

export interface TavernMessage{
  id:string
  role:'user'|'assistant'
  sequence:number
  speakerCardId?:string|null
  speakerName?:string|null
  swipes:Swipe[]
  activeSwipe:number
  createdAt?:string
  updatedAt?:string
}

export interface ChatDetail{
  chat:TavernChat
  messages:TavernMessage[]
  cards?:Array<TavernCharacter|null>|null
  latestSequence?:number
  hasMore?:boolean
}

export type WorldInfoPosition='before_char'|'after_char'|'before_example'|'at_depth'

export interface WorldInfoEntry{
  id:string
  cardId?:string
  keys:string[]
  content:string
  position:WorldInfoPosition
  depth:number
  constant:boolean
  probability:number
  order:number
  enabled:boolean
  source:'import'|'manual'
  createdAt?:string
}

export interface WorldInfoInput{
  keys:string[]
  content:string
  position?:WorldInfoPosition
  depth?:number
  constant?:boolean
  probability?:number
  order?:number
  enabled?:boolean
}

export interface CharacterList{items:TavernCharacter[]}

function unwrap<T>(value:T|Record<string,T>|null,key:string):T{
  if(value&&typeof value==='object'&&!(Array.isArray(value))&&key in (value as Record<string,unknown>))return (value as Record<string,T>)[key]
  return value as T
}

function itemsOf<T>(value:{items?:T[]}|T[]|null):T[]{
  if(Array.isArray(value))return value
  return Array.isArray(value?.items)?(value as {items:T[]}).items:[]
}

export function tavernAvatarUrl(id:string):string{
  return backendBase()+'/api/reader/v1'+TAVERN+'/characters/'+encodeURIComponent(id)+'/avatar'
}
const avatarUrlFor=tavernAvatarUrl

export async function listCharacters(q='',sort:'recent'|'name'='recent'):Promise<TavernCharacter[]>{
  const query:Record<string,string>={sort}
  if(q)query.q=q
  const qs=new URLSearchParams(query).toString()
  const result=await api<CharacterList|TavernCharacter[]>(TAVERN+'/characters'+(qs?'?'+qs:''))
  return itemsOf(result).map(card=>({...card,avatarUrl:card.hasAvatar?avatarUrlFor(card.id):card.avatarUrl??null}))
}

export async function getCharacter(id:string):Promise<TavernCharacter>{
  const result=await api<TavernCharacter|{card:TavernCharacter}>(TAVERN+'/characters/'+encodeURIComponent(id))
  const card=unwrap<TavernCharacter>(result,'card')
  return {...card,avatarUrl:card.hasAvatar?avatarUrlFor(card.id):card.avatarUrl??null}
}

export async function updateCharacter(id:string,patch:CharacterPatch):Promise<TavernCharacter>{
  const result=await api<TavernCharacter|{card:TavernCharacter}>(TAVERN+'/characters/'+encodeURIComponent(id),{method:'PATCH',key:newKey(),body:patch})
  return unwrap<TavernCharacter>(result,'card')
}

export async function deleteCharacter(id:string):Promise<void>{
  await api<unknown>(TAVERN+'/characters/'+encodeURIComponent(id),{method:'DELETE',key:newKey()})
}

export async function importCharacter(file:File):Promise<TavernCharacter>{
  const form=new FormData()
  form.append('file',file)
  const result=await api<TavernCharacter|{card:TavernCharacter}>(TAVERN+'/characters/imports',{method:'POST',key:newKey(),body:form})
  return unwrap<TavernCharacter>(result,'card')
}

export async function characterAvatarBlob(id:string):Promise<Blob|null>{
  const response=await fetch(avatarUrlFor(id),{credentials:'include',headers:authorizationHeaders()})
  return response.ok?response.blob():null
}

export async function exportCharacterBlob(id:string,format:'png'|'json'='png'):Promise<Blob>{
  const query=format==='json'?'?format=json':''
  const response=await fetch(backendBase()+'/api/reader/v1'+TAVERN+'/characters/'+encodeURIComponent(id)+'/export'+query,{credentials:'include',headers:authorizationHeaders()})
  if(!response.ok){
    if(response.status===404)throw new Error('服务端暂不支持导出角色卡，请稍后重试。')
    throw new Error('导出角色卡失败（'+response.status+'），请稍后重试。')
  }
  return response.blob()
}

export async function listCharacterChats(cardId:string):Promise<TavernChat[]>{
  const result=await api<{items:TavernChat[]}|TavernChat[]>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/chats')
  return itemsOf(result)
}

export async function createGroupChat(cardIds:string[],title?:string):Promise<TavernChat>{
  const body:Record<string,unknown>={cardIds}
  if(title)body.title=title
  const result=await api<TavernChat|{chat:TavernChat}>(TAVERN+'/chats',{method:'POST',key:newKey(),body})
  return unwrap<TavernChat>(result,'chat')
}

export async function listChats():Promise<TavernChat[]>{
  const result=await api<{items:TavernChat[]}|TavernChat[]>(TAVERN+'/chats')
  return itemsOf(result)
}

export async function createCharacterChat(cardId:string,greetingId?:string):Promise<TavernChat>{
  const body:Record<string,unknown>=greetingId?{greetingId}:{}
  const result=await api<TavernChat|{chat:TavernChat}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/chats',{method:'POST',key:newKey(),body})
  return unwrap<TavernChat>(result,'chat')
}

export async function getChat(id:string,options:{limit?:number;before?:number}={}):Promise<ChatDetail>{
  const query:Record<string,string>={}
  if(options.limit)query.limit=String(options.limit)
  if(options.before!==undefined)query.before=String(options.before)
  const qs=new URLSearchParams(query).toString()
  const result=await api<ChatDetail|{chat:TavernChat;messages:TavernMessage[];latestSequence?:number;hasMore?:boolean}>(TAVERN+'/chats/'+encodeURIComponent(id)+(qs?'?'+qs:''))
  if(Array.isArray(result?.messages))return {chat:result.chat,messages:result.messages,latestSequence:result.latestSequence,hasMore:result.hasMore}
  return result as ChatDetail
}

export async function updateChatMembers(chatId:string,cardIds:string[]):Promise<TavernChat>{
  const result=await api<TavernChat|{chat:TavernChat}>(TAVERN+'/chats/'+encodeURIComponent(chatId),{method:'PATCH',key:newKey(),body:{cardIds}})
  return unwrap<TavernChat>(result,'chat')
}

export async function updateChatSampling(id:string,sampling:ChatSampling):Promise<TavernChat>{
  const result=await api<TavernChat|{chat:TavernChat}>(TAVERN+'/chats/'+encodeURIComponent(id),{method:'PATCH',key:newKey(),body:{sampling}})
  return unwrap<TavernChat>(result,'chat')
}

export async function updateChat(id:string,title:string):Promise<TavernChat>{
  const result=await api<TavernChat|{chat:TavernChat}>(TAVERN+'/chats/'+encodeURIComponent(id),{method:'PATCH',key:newKey(),body:{title}})
  return unwrap<TavernChat>(result,'chat')
}

export async function deleteChat(id:string):Promise<void>{
  await api<unknown>(TAVERN+'/chats/'+encodeURIComponent(id),{method:'DELETE',key:newKey()})
}

export async function selectSwipe(chatId:string,messageId:string,index:number):Promise<TavernMessage|null>{
  const result=await api<TavernMessage|{message:TavernMessage}|null>(TAVERN+'/chats/'+encodeURIComponent(chatId)+'/messages/'+encodeURIComponent(messageId)+'/swipes/'+index+'/select',{method:'POST',key:newKey()})
  return result?unwrap<TavernMessage>(result,'message'):null
}

export async function editMessage(chatId:string,messageId:string,text:string):Promise<TavernMessage|null>{
  const result=await api<TavernMessage|{message:TavernMessage}|null>(TAVERN+'/chats/'+encodeURIComponent(chatId)+'/messages/'+encodeURIComponent(messageId),{method:'PATCH',key:newKey(),body:{text}})
  return result?unwrap<TavernMessage>(result,'message'):null
}

export async function deleteMessage(chatId:string,messageId:string):Promise<void>{
  await api<unknown>(TAVERN+'/chats/'+encodeURIComponent(chatId)+'/messages/'+encodeURIComponent(messageId),{method:'DELETE',key:newKey()})
}

export interface WorldInfoPreview{matched:WorldInfoEntry[];injection:string}

// Preview is a read-only POST: the composer must not receive an idempotency key.
export async function previewWorldInfo(cardId:string,message:string):Promise<WorldInfoPreview>{
  const result=await api<WorldInfoPreview|{preview:WorldInfoPreview}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/world-info/preview',{method:'POST',body:{message}})
  if(result&&typeof result==='object'&&'preview' in result)return (result as {preview:WorldInfoPreview}).preview
  return result as WorldInfoPreview
}

export async function importChats(cardId:string,file:File):Promise<TavernChat>{
  const form=new FormData()
  form.append('file',file)
  const result=await api<TavernChat|{chat:TavernChat}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/chats/imports',{method:'POST',key:newKey(),body:form})
  return unwrap<TavernChat>(result,'chat')
}

export async function exportChatBlob(id:string,format:'jsonl'|'json'='jsonl'):Promise<Blob>{
  const response=await fetch(backendBase()+'/api/reader/v1'+TAVERN+'/chats/'+encodeURIComponent(id)+'/export?format='+format,{credentials:'include',headers:authorizationHeaders()})
  if(!response.ok){
    if(response.status===404)throw new Error('服务端暂不支持导出聊天，请稍后重试。')
    throw new Error('导出聊天失败（'+response.status+'），请稍后重试。')
  }
  return response.blob()
}

export interface TavernPrompt{
  systemPrompt:string
  mainPrompt:string
  jailbreakPrompt?:string
  postHistoryPrompt?:string
  temperature:number
  topP:number
  maxTokens:number
  stop:string[]
  worldInfoPosition?:WorldInfoPosition
}

export type TavernPromptPatch=Partial<Omit<TavernPrompt,'systemPrompt'>> & {systemPrompt?:string}

function promptEnvelope(value:TavernPrompt|{prompt:TavernPrompt}):TavernPrompt{
  const prompt='prompt' in value?(value as {prompt:TavernPrompt}).prompt:value as TavernPrompt
  return {systemPrompt:prompt.systemPrompt??'',mainPrompt:prompt.mainPrompt??'',
    jailbreakPrompt:prompt.jailbreakPrompt??'',postHistoryPrompt:prompt.postHistoryPrompt??'',
    temperature:prompt.temperature??0.8,topP:prompt.topP??0.95,maxTokens:prompt.maxTokens??1024,
    stop:Array.isArray(prompt.stop)?prompt.stop:[],worldInfoPosition:prompt.worldInfoPosition??'before_char'}
}

export async function getPrompt(cardId:string):Promise<TavernPrompt>{
  const result=await api<TavernPrompt|{prompt:TavernPrompt}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/prompt')
  return promptEnvelope(result)
}

export async function updatePrompt(cardId:string,patch:TavernPromptPatch):Promise<TavernPrompt>{
  const result=await api<TavernPrompt|{prompt:TavernPrompt}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/prompt',{method:'PATCH',key:newKey(),body:patch})
  return promptEnvelope(result)
}

export interface TavernPreset{
  id:string
  name:string
  systemPrompt?:string
  mainPrompt?:string
  jailbreakPrompt?:string
  postHistoryPrompt?:string
  temperature?:number
  topP?:number
  maxTokens?:number
  stop?:string[]
  isDefault?:boolean
  createdAt?:string
  updatedAt?:string
}

export interface TavernPresetInput{
  name:string
  systemPrompt?:string
  mainPrompt?:string
  jailbreakPrompt?:string
  postHistoryPrompt?:string
  temperature?:number
  topP?:number
  maxTokens?:number
  stop?:string[]
  isDefault?:boolean
}

// The tavern presets API uses bare SillyTavern field names (docs/contract.md §10:
// system/main/jailbreak/postHistory) while the settings UI speaks *Prompt names.
// Keep the translation in this one place so the panel never has to know the wire shape.
interface TavernPresetWire{
  id:string
  name:string
  system?:string|null
  main?:string|null
  jailbreak?:string|null
  postHistory?:string|null
  temperature?:number|null
  topP?:number|null
  maxTokens?:number|null
  stop?:string[]|null
  isDefault?:boolean
  createdAt?:string
  updatedAt?:string
}

interface TavernPresetWireInput{
  name?:string
  system?:string
  main?:string
  jailbreak?:string
  postHistory?:string
  temperature?:number
  topP?:number
  maxTokens?:number
  stop?:string[]
}

function presetFromWire(value:TavernPresetWire):TavernPreset{
  return {
    id:value.id, name:value.name,
    systemPrompt:value.system??'', mainPrompt:value.main??'',
    jailbreakPrompt:value.jailbreak??'', postHistoryPrompt:value.postHistory??'',
    temperature:value.temperature??undefined, topP:value.topP??undefined,
    maxTokens:value.maxTokens??undefined, stop:value.stop??[],
    isDefault:!!value.isDefault, createdAt:value.createdAt, updatedAt:value.updatedAt,
  }
}

function presetToWire(input:Partial<TavernPresetInput>):TavernPresetWireInput{
  const out:TavernPresetWireInput={}
  if(input.name!==undefined)out.name=input.name
  if(input.systemPrompt!==undefined)out.system=input.systemPrompt
  if(input.mainPrompt!==undefined)out.main=input.mainPrompt
  if(input.jailbreakPrompt!==undefined)out.jailbreak=input.jailbreakPrompt
  if(input.postHistoryPrompt!==undefined)out.postHistory=input.postHistoryPrompt
  if(input.temperature!==undefined)out.temperature=input.temperature
  if(input.topP!==undefined)out.topP=input.topP
  if(input.maxTokens!==undefined)out.maxTokens=input.maxTokens
  if(input.stop!==undefined)out.stop=input.stop
  // isDefault is intentionally not part of PresetInput/PresetUpdate bodies: the
  // Strict server model rejects unknown fields. Use setDefaultPreset() for that.
  return out
}

export async function listPresets():Promise<TavernPreset[]>{
  const result=await api<{items:TavernPresetWire[]}|TavernPresetWire[]>(TAVERN+'/presets')
  return itemsOf(result).map(presetFromWire)
}

export async function createPreset(input:TavernPresetInput):Promise<TavernPreset>{
  const result=await api<TavernPresetWire|{preset:TavernPresetWire}>(TAVERN+'/presets',{method:'POST',key:newKey(),body:presetToWire(input)})
  return presetFromWire(unwrap<TavernPresetWire>(result,'preset'))
}

export async function updatePreset(id:string,input:Partial<TavernPresetInput>):Promise<TavernPreset>{
  const result=await api<TavernPresetWire|{preset:TavernPresetWire}>(TAVERN+'/presets/'+encodeURIComponent(id),{method:'PATCH',key:newKey(),body:presetToWire(input)})
  return presetFromWire(unwrap<TavernPresetWire>(result,'preset'))
}

export async function deletePreset(id:string):Promise<void>{
  await api<unknown>(TAVERN+'/presets/'+encodeURIComponent(id),{method:'DELETE',key:newKey()})
}

export async function setDefaultPreset(id:string):Promise<TavernPreset>{
  const result=await api<TavernPresetWire|{preset:TavernPresetWire}>(TAVERN+'/presets/'+encodeURIComponent(id),{method:'PATCH',key:newKey(),body:{isDefault:true}})
  return presetFromWire(unwrap<TavernPresetWire>(result,'preset'))
}

export async function listWorldInfo(cardId:string):Promise<WorldInfoEntry[]>{
  const result=await api<{items:WorldInfoEntry[]}|WorldInfoEntry[]>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/world-info')
  return itemsOf(result)
}

export async function createWorldInfo(cardId:string,input:WorldInfoInput):Promise<WorldInfoEntry>{
  const result=await api<WorldInfoEntry|{item:WorldInfoEntry}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/world-info',{method:'POST',key:newKey(),body:input})
  return unwrap<WorldInfoEntry>(result,'item')
}

export async function updateWorldInfo(cardId:string,entryId:string,patch:Partial<WorldInfoInput>):Promise<WorldInfoEntry>{
  const result=await api<WorldInfoEntry|{item:WorldInfoEntry}>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/world-info/'+encodeURIComponent(entryId),{method:'PATCH',key:newKey(),body:patch})
  return unwrap<WorldInfoEntry>(result,'item')
}

export async function deleteWorldInfo(cardId:string,entryId:string):Promise<void>{
  await api<unknown>(TAVERN+'/characters/'+encodeURIComponent(cardId)+'/world-info/'+encodeURIComponent(entryId),{method:'DELETE',key:newKey()})
}

export function messageText(message:TavernMessage):string{
  const swipe=message.swipes?.[message.activeSwipe]??message.swipes?.[0]
  return swipe?.content??''
}

// Keys may arrive as an array or a comma separated string depending on import source.
export function worldInfoKeys(entry:Pick<WorldInfoEntry,'keys'>):string[]{
  const raw=entry.keys as unknown
  if(Array.isArray(raw))return raw.filter((key):key is string=>typeof key==='string').map(key=>key.trim()).filter(Boolean)
  if(typeof raw==='string')return raw.split(/[,，;；\n]/).map(key=>key.trim()).filter(Boolean)
  return []
}
