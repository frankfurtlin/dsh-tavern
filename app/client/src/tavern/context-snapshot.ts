// Live snapshot of the chat the user is currently looking at.
//
// The DSH panel can ask for the chat context at any moment, but the chat page
// owns the loaded card/messages. Publishing a small snapshot keeps the bridge
// synchronous (hasContext) and avoids re-fetching inside the context reader.
import {ref} from 'vue'
import type {TavernCharacter} from './api'

export interface ChatContextMessage{role:'user'|'assistant';text:string}

export interface ChatContextSnapshot{
  chatId:string
  cardId:string
  characterName:string
  character:TavernCharacter|null
  messages:ChatContextMessage[]
  /** True once the chat itself has been loaded (messages/card may still be loading). */
  loaded:boolean
}

const snapshot=ref<ChatContextSnapshot|null>(null)
const listeners=new Set<()=>void>()

function notify(){
  for(const listener of [...listeners])listener()
}

export function getChatContextSnapshot():ChatContextSnapshot|null{
  return snapshot.value
}

export function setChatContextSnapshot(value:ChatContextSnapshot|null){
  snapshot.value=value
  notify()
}

export function patchChatContextSnapshot(chatId:string,patch:Partial<ChatContextSnapshot>){
  if(!snapshot.value||snapshot.value.chatId!==chatId)return
  snapshot.value={...snapshot.value,...patch}
  notify()
}

export function clearChatContextSnapshot(chatId?:string){
  if(chatId&&snapshot.value?.chatId!==chatId)return
  if(snapshot.value===null)return
  snapshot.value=null
  notify()
}

export function subscribeChatContext(listener:()=>void):()=>void{
  listeners.add(listener)
  return ()=>{listeners.delete(listener)}
}
