import type {Anchor} from './types'

export const TIMELINE_CHAPTER_PAGE=20
export const TIMELINE_BRANCH_PAGE=10
export function timelineChapterHitSize(scale:number){
  // Chromium quantizes layout to 1/64 CSS px before applying the camera.
  // A fractional 44/scale frame can round below the actual 44px target.
  const minimum=Math.ceil(44/scale)
  return {width:Math.max(168,minimum),height:Math.max(132,minimum)}
}
export function timelineOverviewTargets<T>(items:{id:string;x:number;y:number;item:T}[],scale:number){
  const cells=new Map<string,{id:string;x:number;y:number;width:number;height:number;items:typeof items}>()
  for(const point of items){const column=Math.floor(point.x*scale/56),row=Math.floor(point.y*scale/56),id=`chapter-overview:${column}:${row}`
    let target=cells.get(id);if(!target){target={id,x:column*56+28,y:row*56+28,width:44,height:44,items:[]};cells.set(id,target)}target.items.push(point)
  }
  return [...cells.values()]
}
export interface TimelineChapter {
  id:string
  title:string
  position:number
  source:'original'|'ai'
  readable:boolean
  observed?:boolean
  titleRevealed?:boolean
}
export interface TimelineBranch {
  id:string
  name:string
  parentBranchId:string|null
  forkAnchor:Anchor|null
  status:string
  headRevisionId:string
  version:number
  forkLabel:string|null
  lastReadLabel:string|null
  chapters:TimelineChapter[]
  chaptersTotal:number
  inheritedChapterCount?:number
  sharedThroughChapterId?:string|null
  nextChapterOffset:number|null
}
export interface StoryTimelineData {
  bookId:string
  originalBranchId:string
  currentBranchId:string
  branches:TimelineBranch[]
}
export interface TimelineRow {branch:TimelineBranch;depth:number;detached:boolean}

// Iterative traversal keeps deep branch chains safe and emits every saved line
// once, even if an old import has a missing parent or malformed cycle.
export function timelineRows(data:StoryTimelineData):TimelineRow[]{
  const byId=new Map(data.branches.map(branch=>[branch.id,branch]))
  const children=new Map<string,TimelineBranch[]>()
  for(const branch of data.branches){
    if(branch.id===data.originalBranchId||!branch.parentBranchId||!byId.has(branch.parentBranchId))continue
    const items=children.get(branch.parentBranchId)||[];items.push(branch);children.set(branch.parentBranchId,items)
  }
  const roots=data.branches.filter(branch=>branch.id===data.originalBranchId||!branch.parentBranchId||!byId.has(branch.parentBranchId))
  roots.sort((a,b)=>Number(b.id===data.originalBranchId)-Number(a.id===data.originalBranchId))
  const result:TimelineRow[]=[],visited=new Set<string>()
  function walk(root:TimelineBranch,detached:boolean){
    const stack=[{branch:root,depth:0,detached}]
    while(stack.length){
      const row=stack.pop()!;if(visited.has(row.branch.id))continue
      visited.add(row.branch.id);result.push(row)
      const descendants=children.get(row.branch.id)||[]
      for(let index=descendants.length-1;index>=0;index--)stack.push({branch:descendants[index],depth:row.depth+1,detached:false})
    }
  }
  for(const root of roots)walk(root,!!root.parentBranchId&&root.id!==data.originalBranchId)
  for(const branch of data.branches)if(!visited.has(branch.id))walk(branch,true)
  return result
}

export function timelineChapterLabel(chapter:TimelineChapter){
  return chapter.titleRevealed===false||chapter.observed===false?'第'+(chapter.position+1)+'章':chapter.title||'第'+(chapter.position+1)+'章'
}
export function timelineChapterPage(branch:TimelineBranch,offset:number){
  const start=Math.max(offset,timelineChapterStart(branch))
  return branch.chapters.filter(chapter=>chapter.position>=start&&chapter.position<start+TIMELINE_CHAPTER_PAGE).sort((a,b)=>a.position-b.position)
}
export function timelineChapterStart(branch:TimelineBranch){
  return Math.max(0,Math.min(branch.chaptersTotal,branch.inheritedChapterCount||0))
}
export function timelinePageLoaded(branch:TimelineBranch,offset:number){
  offset=Math.max(offset,timelineChapterStart(branch))
  const count=Math.max(0,Math.min(TIMELINE_CHAPTER_PAGE,branch.chaptersTotal-offset))
  const positions=new Set(timelineChapterPage(branch,offset).map(chapter=>chapter.position))
  return Array.from({length:count},(_,index)=>offset+index).every(position=>positions.has(position))
}
export interface TimelineLane {
  column:number
  fork?:{branchId:string;chapterId:string;before:boolean}
}
// Place each suffix after the visible shared chapter, even if that chapter is
// inherited through several ancestors. Off-page forks keep their explicit link
// instead of inventing a connection to a different chapter.
export function timelineLanes(rows:TimelineRow[],offsets:Record<string,number>):Map<string,TimelineLane>{
  const branches=new Map(rows.map(row=>[row.branch.id,row.branch]))
  const nodes=new Map<string,{chapter:TimelineChapter;column:number}>()
  const lanes=new Map<string,TimelineLane>()
  for(const {branch,detached} of rows){
    const start=timelineChapterStart(branch),offset=offsets[branch.id]??start
    let lane:TimelineLane={column:0}
    if(branch.parentBranchId&&!detached&&offset===start){
      const visited=new Set<string>();let parentId:string|null=branch.parentBranchId
      while(parentId&&!visited.has(parentId)){
        visited.add(parentId)
        const parent=branches.get(parentId);if(!parent)break
        const before=start===0
        const chapterId=before?timelineChapterPage(parent,offsets[parent.id]??timelineChapterStart(parent)).find(c=>c.position===0)?.id:branch.sharedThroughChapterId??branch.forkAnchor?.chapterId
        const shared=chapterId?nodes.get(parentId+':'+chapterId):undefined
        if(shared){lane={column:shared.column+(before?0:1),fork:{branchId:parentId,chapterId:shared.chapter.id,before}};break}
        parentId=parent.parentBranchId
      }
    }
    lanes.set(branch.id,lane)
    timelineChapterPage(branch,offset).forEach((chapter,index)=>nodes.set(branch.id+':'+chapter.id,{chapter,column:lane.column+index}))
  }
  return lanes
}
export function mergeTimelinePage(current:StoryTimelineData,incoming:StoryTimelineData):StoryTimelineData{
  if(current.bookId!==incoming.bookId)throw new Error('时间线所属书籍已变化，请重新打开')
  const old=new Map(current.branches.map(branch=>[branch.id,branch]))
  return {...incoming,branches:incoming.branches.map(branch=>{
    const previous=old.get(branch.id)
    if(!previous||previous.headRevisionId!==branch.headRevisionId)return {...branch,chapters:[...branch.chapters]}
    const chapters=new Map(previous.chapters.map(chapter=>[chapter.position,chapter]))
    for(const chapter of branch.chapters)chapters.set(chapter.position,chapter)
    return {...branch,chapters:[...chapters.values()].filter(chapter=>chapter.position<branch.chaptersTotal).sort((a,b)=>a.position-b.position)}
  })}
}
export function timelineReadLocation(bookId:string,branch:TimelineBranch,chapter?:TimelineChapter){
  return {path:`/books/${bookId}/read`,query:{branch:branch.id,revision:branch.headRevisionId,...(chapter?{chapter:chapter.id}:{})}}
}
