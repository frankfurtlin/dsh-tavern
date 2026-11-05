/**
 * Reads the chat context that the DSH panel asks for (docs/contract.md §3).
 *
 * The native tavern chat page publishes a live snapshot (chat + card + latest
 * messages) through tavern/context-snapshot, so the bridge answers without an
 * extra network round-trip and `hasContext` is synchronous.
 */
import type {Router} from 'vue-router'
import type {TavernChatContext,TavernContextKind,TavernRecentMessage} from './dsh-bridge'
import {getChatContextSnapshot,subscribeChatContext} from './tavern/context-snapshot'

const CHAT_PAGE=/^\/chats\/([^/]+)$/
export const RECENT_MESSAGE_LIMIT=12
export const MESSAGE_TEXT_LIMIT=1000

export {subscribeChatContext}

export function expectsChatContext(router:Router):boolean{
  return CHAT_PAGE.test(router.currentRoute.value.path)
}

export function hasChatContext(router:Router):boolean{
  const match=router.currentRoute.value.path.match(CHAT_PAGE)
  if(!match)return false
  const snapshot=getChatContextSnapshot()
  return !!snapshot&&snapshot.chatId===match[1]&&snapshot.loaded
}

function messageText(text:string):string{
  return text.length>MESSAGE_TEXT_LIMIT?text.slice(0,MESSAGE_TEXT_LIMIT):text
}

function recentMessages(messages:{role:'user'|'assistant';text:string}[]):TavernRecentMessage[]{
  return messages
    .slice(-RECENT_MESSAGE_LIMIT)
    .map(message=>({role:message.role,text:messageText(message.text)}))
}

export function readChatContext(router:Router,kind:TavernContextKind,target?:string):TavernChatContext|null{
  void kind
  void target
  const match=router.currentRoute.value.path.match(CHAT_PAGE)
  if(!match)return null
  const snapshot=getChatContextSnapshot()
  if(!snapshot||snapshot.chatId!==match[1]||!snapshot.loaded)return null
  const card=snapshot.character
  const characterName=card?.name||snapshot.characterName
  return{
    appPath:router.currentRoute.value.path,
    chatId:snapshot.chatId,
    cardId:snapshot.cardId,
    characterName,
    characterDescription:card?.description??'',
    personality:card?.personality??'',
    scenario:card?.scenario??'',
    firstMessage:card?.firstMessage??'',
    recentMessages:recentMessages(snapshot.messages),
    activeCharacter:characterName,
  }
}
