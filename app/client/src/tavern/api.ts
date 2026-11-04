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

export interface TavernChat{
  id:string
  cardId:string
  title:string
  modelName?:string|null
  characterName?:string|null
  lastMessage?:string|null
  createdAt?:string
  updatedAt?:string
  lastMessageAt?:string|null
}

export interface Swipe{content:string;createdAt?:string;model?:string|null}

export interface TavernMessage{
  id:string
  role:'user'|'assistant'
  sequence:number
  swipes:Swipe[]
  activeSwipe:number
  createdAt?:string
  updatedAt?:string
}

export interface ChatDetail{chat:TavernChat;messages:TavernMessage[];latestSequence?:number;hasMore?:boolean}

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

function avatarUrlFor(id:string){return backendBase()+'/api/reader/v1'+TAVERN+'/characters/'+encodeURIComponent(id)+'/avatar'}

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
