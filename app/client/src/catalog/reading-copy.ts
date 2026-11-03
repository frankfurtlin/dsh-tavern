import {canonical,ContractError,sha256,type Document} from '../domain/local-contract'
import type {LocalVault} from '../repositories/local-vault'
import type {ChatMessage,ChatSession} from '../tavern/chat-contract'
import type {PlayCheckpoint,PlayWorld} from '../playable/contract'
import {newOriginalStoryDraft,saveOriginalStoryDraft,validateOriginalStoryDraft,type OriginalStoryDraft} from './original-story'

export interface ReadingCopy {
  origin:{kind:'session'|'world';id:string;revision:number;scope:string[]}
  title:string
  chapterTitle:string
  text:string
}
function invalid(message:string):never {throw new ContractError('READING_COPY',message)}
export function validateReadingCopy(copy:ReadingCopy){
  if(!copy||!['session','world'].includes(copy.origin?.kind)||!copy.origin.id||copy.origin.id.length>160||!Number.isSafeInteger(copy.origin.revision)||copy.origin.revision<1||!Array.isArray(copy.origin.scope)||!copy.origin.scope.length||copy.origin.scope.length>2000||copy.origin.scope.some(id=>typeof id!=='string'||!id||id.length>200)||new Set(copy.origin.scope).size!==copy.origin.scope.length)invalid('这份阅读稿的来源不完整')
  if(typeof copy.title!=='string'||!copy.title.trim()||copy.title.length>512||typeof copy.chapterTitle!=='string'||!copy.chapterTitle.trim()||copy.chapterTitle.length>512||typeof copy.text!=='string'||!copy.text.trim())invalid('没有可保存的正文')
  if(copy.text.length>200000||copy.text.split(/\r\n|\r|\n/).filter(line=>line.trim()).length>2000)invalid('这份记录超过单次阅读稿上限，请先选择较早的记录或缩短正文')
  if(copy.text.includes('\0')||/(?:[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF])/.test(copy.text))invalid('正文含无效字符，原记录仍保留')
}

/** A literal transcript, not an AI rewrite. Hidden, system and incomplete
 * alternatives stay in the conversation and never enter a reading copy. */
export function chatReadingCopy(session:Document<ChatSession>,messages:Document<ChatMessage>[]):ReadingCopy {
  if(session.kind!=='session'||session.value.archived)invalid('这段对话已归档或不可用')
  const byId=new Map(messages.map(row=>[row.id,row])),lines:string[]=[],scope:string[]=[]
  if(byId.size!==messages.length||new Set(session.value.messageIds).size!==session.value.messageIds.length)invalid('对话的消息引用重复')
  for(const id of session.value.messageIds){
    const row=byId.get(id)
    if(!row||row.kind!=='message'||row.value.sessionId!==session.id)invalid('部分对话记录缺失，原记录仍保留')
    if(row.value.hidden||row.value.role==='system')continue
    const variant=row.value.variants.find(value=>value.id===row.value.selectedVariantId)
    if(!variant)invalid('当前回复候选缺失')
    if(variant.state!=='complete'||!variant.text.trim())continue
    const person=row.value.role==='user'?row.value.personaName||session.value.persona.name:session.value.cards.find(card=>card.value.identityId===row.value.speakerId)?.value.name
    if(!person)invalid('回复的角色身份缺失，请先核对这段对话')
    lines.push(person+'：\n'+variant.text);scope.push(row.id+'@'+row.revision+':'+variant.id)
  }
  const copy:ReadingCopy={origin:{kind:'session',id:session.id,revision:session.revision,scope},title:session.value.title+' · 对话',chapterTitle:'对话记录',text:lines.join('\n\n')}
  validateReadingCopy(copy);return copy
}

/** Freeze only the actual ancestor path ending at the chosen checkpoint.
 * A fork repeats its parent's scene, so it contributes no duplicate prose. */
export function playReadingCopy(world:Document<PlayWorld>,history:Document<PlayCheckpoint>[],endId:string):ReadingCopy {
  if(world.kind!=='world')invalid('找不到这个文游作品')
  const end=history.findIndex(row=>row.id===endId)
  if(end<0)invalid('这个检查点不在当前存档中')
  const path=history.slice(0,end+1),seen=new Set<string>(),lines:string[]=[],scope:string[]=[]
  for(const [index,row] of path.entries()){
    if(row.kind!=='checkpoint'||row.value.worldId!==world.id||seen.has(row.id)||row.value.parentId!==(index?path[index-1]!.id:null))invalid('存档的前文不完整，原存档仍保留')
    seen.add(row.id);scope.push(row.id)
    if(row.value.kind==='fork')continue
    if(row.value.kind==='action'){
      const parent=path[index-1],scene=world.value.definition.scenes.find(value=>value.id===parent?.value.state.sceneId),action=scene?.actions.find(value=>value.id===row.value.actionId)
      if(!action)invalid('这条行动的原选项缺失')
      lines.push('选择：'+action.label)
    }
    lines.push(row.value.text)
  }
  const copy:ReadingCopy={origin:{kind:'world',id:world.id,revision:world.revision,scope},title:world.value.name+' · 旅程',chapterTitle:'旅程记录',text:lines.join('\n\n')}
  validateReadingCopy(copy);return copy
}

/** Repeated clicks and two tabs open the same durable creation draft.
 * Reopening preserves any edits; creation uses the original story journal. */
export async function saveReadingCopy(vault:LocalVault,input:ReadingCopy):Promise<Document<OriginalStoryDraft>> {
  const copy=JSON.parse(canonical(input)) as ReadingCopy;validateReadingCopy(copy)
  const id='story-draft:copy:'+await sha256(canonical(copy))
  const existing=await vault.read<OriginalStoryDraft>(id)
  if(existing){if(existing.kind!=='settings')invalid('阅读稿的保存位置不可用');validateOriginalStoryDraft(existing.value,id);if(existing.value.status==='discarded')invalid('这份阅读稿已取消，请选择新的记录');return existing}
  const value={...newOriginalStoryDraft().value,title:copy.title,chapterTitle:copy.chapterTitle,text:copy.text}
  try{return await saveOriginalStoryDraft(vault,id,0,value)}catch(error){
    if(!vault.unlocked)throw error
    const durable=await vault.read<OriginalStoryDraft>(id)
    if(!durable||durable.kind!=='settings')throw error
    validateOriginalStoryDraft(durable.value,id)
    if(durable.value.status==='discarded')throw error
    return durable
  }
}
