import {api,authorizationHeaders} from '../api'
import {backendBase} from '../platform/bridge'

export interface CardOut {
  id:string
  name:string
  description:string
  personality?:string
  scenario?:string
  firstMessage?:string
  tags:string[]
  creator?:string|null
  source:'png'|'json'
  hasAvatar:boolean
  chatTarget?:{worldId:string;lineId:string;checkpointId:string;characterId:string}|null
  createdAt?:string
  updatedAt?:string
}

export interface CardPage {items:CardOut[]}
export type CardListResponse = CardPage | CardOut[]

const PREFIX='/character-cards'

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

export async function startCardChat(id:string):Promise<{sessionId:string;cardId:string}>{
  return api<{sessionId:string;cardId:string}>(PREFIX+'/'+encodeURIComponent(id)+'/chats',{method:'POST'})
}

export async function deleteCard(id:string):Promise<void>{
  await api<unknown>(PREFIX+'/'+encodeURIComponent(id),{method:'DELETE'})
}

// <img src> cannot carry the Bearer token, so avatars are fetched as blobs.
export async function cardAvatarBlob(id:string):Promise<Blob|null>{
  const response=await fetch(backendBase()+'/api/reader/v1'+PREFIX+'/'+encodeURIComponent(id)+'/avatar',{credentials:'include',headers:authorizationHeaders()})
  return response.ok?response.blob():null
}
