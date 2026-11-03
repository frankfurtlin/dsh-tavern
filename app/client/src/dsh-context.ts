/**
 * Reads the story context that the DSH panel asks for (docs/contract.md §3).
 *
 * World pages already keep the live world/line/history in the pinia world
 * store, so they are answered without new requests. Book reading pages hold
 * their data in page-local refs, so their context is rebuilt from the reader
 * API (same endpoints the page itself uses) and truncated to the tail.
 */
import type {Router} from 'vue-router'
import type {StoryCharacter,StoryContext,StoryContextKind} from './dsh-bridge'
import {useWorld} from './world/store'
import {api,allPages} from './api'
import type {Book,Branch,Chapter,ChapterInfo,Progress} from './api/types'

const RECENT_TEXT_LIMIT=4000
const WORLD_PAGE=/^\/worlds\/[^/]+\/(play|explore|read)$/
const BOOK_READ=/^\/books\/([^/]+)\/read$/

function tail(text:string,limit=RECENT_TEXT_LIMIT){
  return text.length>limit?text.slice(text.length-limit):text
}

export function hasStoryContext(router:Router):boolean{
  const path=router.currentRoute.value.path
  if(WORLD_PAGE.test(path)){
    const world=useWorld()
    return !!world.world&&!!world.line
  }
  return BOOK_READ.test(path)
}

function recentWorldText(line:{head:{text:string}},history:{text:string}[]):string{
  const parts:string[]=[]
  for(const checkpoint of history.slice(-8))if(checkpoint.text)parts.push(checkpoint.text)
  if(line.head.text&&parts[parts.length-1]!==line.head.text)parts.push(line.head.text)
  return tail(parts.join('\n\n'))
}

function worldCharacters(world:{characters?:{name:string;description:string;traits:string}[]},state:ReturnType<typeof worldState>):StoryCharacter[]{
  const characters:StoryCharacter[]=[];const seen=new Set<string>()
  const add=(name:string,identity:string,speech:string)=>{
    if(!name||seen.has(name))return
    seen.add(name)
    characters.push({name,identity:identity||'',speech:speech||''})
  }
  for(const entity of state?.entities??[])if(entity.type==='character')add(entity.name,entity.description||'',entity.traits||'')
  for(const card of world.characters??[])add(card.name,card.description||'',card.traits||'')
  return characters.slice(0,24)
}

function worldState(line:{head:{state?:unknown}}|null){
  return (line?.head.state??null) as {playerId:string;entities:{id:string;name:string;type:string;description:string;traits:string}[]}|null
}

function activeWorldCharacter(world:{recommendedCharacterId?:string;characters?:{id:string;name:string}[]},line:{head:{state?:unknown}}|null,target?:string){
  if(target)return target
  const state=worldState(line)
  if(!state)return ''
  const player=state.entities.find(entity=>entity.id===state.playerId)
  if(player?.name)return player.name
  const recommended=world.characters?.find(card=>card.id===world.recommendedCharacterId)
  return recommended?.name??''
}

export function readWorldContext(worldId:string,path:string,kind:StoryContextKind,target?:string):StoryContext|null{
  const world=useWorld()
  if(!world.world||!world.line||world.world.id!==worldId)return null
  const mode=`world-${path.slice(path.lastIndexOf('/')+1)}`
  return {
    appPath:path,
    worldTitle:world.world.name,
    lineTitle:world.line.name,
    mode,
    characters:worldCharacters(world.world,worldState(world.line)),
    activeCharacter:activeWorldCharacter(world.world,world.line,kind==='roleplay'?target:undefined),
    recentText:recentWorldText(world.line,world.history),
  }
}

export async function readBookContext(bookId:string,path:string):Promise<StoryContext|null>{
  const book=await api<Book>('/books/'+bookId,{cache:true})
  const branches=await allPages<Branch>(`/books/${bookId}/branches`)
  const line=branches.find(row=>row.id===book.lastProgress?.branchId)
    ??branches.find(row=>row.id===book.originalBranchId)
    ??branches[0]
  if(!line)return null
  const revision=String(book.lastProgress?.revisionId||line.headRevisionId)
  const directory=await allPages<ChapterInfo>(`/branches/${line.id}/chapters?revisionId=${revision}`)
  let chapterId=directory[0]?.id
  try{
    const progress=await api<Progress>(`/books/${bookId}/progress?branchId=${line.id}&revisionId=${revision}`,{cache:true})
    chapterId=progress.anchor?.chapterId??chapterId
  }catch{/* Progress is an optimisation; the first chapter still gives text. */}
  if(!chapterId)return null
  const chapter=await api<Chapter>(`/branches/${line.id}/chapters/${chapterId}?revisionId=${revision}`,{cache:true})
  return {
    appPath:path,
    worldTitle:book.title,
    lineTitle:chapter.title?`${line.name} · ${chapter.title}`:line.name,
    mode:'book-read',
    characters:[],
    activeCharacter:'',
    recentText:tail(chapter.blocks.map(block=>block.text).join('\n')),
  }
}

export async function readStoryContext(router:Router,kind:StoryContextKind,target?:string):Promise<StoryContext|null>{
  const path=router.currentRoute.value.path
  const world=path.match(WORLD_PAGE)
  if(world)return readWorldContext(String(router.currentRoute.value.params.worldId),path,kind,target)
  const book=path.match(BOOK_READ)
  if(book)return readBookContext(book[1]!,path)
  return null
}
