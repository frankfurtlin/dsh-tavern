import type {LocalVault} from '../repositories/local-vault'
import type {ChatSession} from '../tavern/chat-contract'
import type {NovelCharacter} from '../novel-characters/contract'
import type {ProgressAnchor} from '../domain/local-contract'
export function readingAnchorLink(anchor:ProgressAnchor,revision=anchor.lineRevision){
  return {path:'/local/read/'+anchor.storyId,query:{branch:anchor.branchId||undefined,paragraph:anchor.paragraphId,offset:String(anchor.offset),revision:revision?String(revision):undefined}}
}
/** Legacy cards omitted lineRevision; their immutable character source still
 * supplies the exact version. Never silently navigate such a card to HEAD. */
export async function chatStoryLinks(vault:LocalVault,chat:ChatSession){
  const links:{key:string;label:string;to:ReturnType<typeof readingAnchorLink>}[]=[]
  for(const card of chat.cards){
    const origin=card.value.novelOrigin;if(!origin)continue
    const row=await vault.read<NovelCharacter>(origin.characterId,origin.characterRevision)
    if(!row||row.kind!=='settings'||row.value.assetType!=='novel-character'||!row.value.scope||row.value.scope.storyId!==origin.anchor.storyId||row.value.scope.branchId!==origin.anchor.branchId)continue
    const revision=origin.anchor.lineRevision||row.value.scope.branchRevision||row.value.scope.storyRevision
    links.push({key:card.value.identityId,label:'回到 '+card.value.name+' 的故事',to:readingAnchorLink(origin.anchor,revision)})
  }
  return links
}
