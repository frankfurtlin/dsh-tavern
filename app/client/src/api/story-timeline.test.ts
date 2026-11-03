import {describe,it,expect} from 'vitest'
import {timelineRows,timelineChapterLabel,timelineChapterPage,timelinePageLoaded,mergeTimelinePage,timelineReadLocation,timelineChapterStart,timelineLanes} from './story-timeline'
import type {StoryTimelineData,TimelineBranch,TimelineChapter} from './story-timeline'

const chapter=(position:number,changes:Partial<TimelineChapter>={}):TimelineChapter=>({id:'chapter-'+position,title:'真实章节 '+position,position,source:'original',readable:true,...changes})
const branch=(id:string,parentBranchId:string|null=null,changes:Partial<TimelineBranch>={}):TimelineBranch=>({id,name:id,parentBranchId,forkAnchor:null,status:'active',headRevisionId:id+'-revision',version:1,forkLabel:null,lastReadLabel:null,chapters:[chapter(0)],chaptersTotal:1,nextChapterOffset:null,...changes})
const data=(branches:TimelineBranch[]):StoryTimelineData=>({bookId:'book',originalBranchId:'original',currentBranchId:'child',branches})

describe('saved story timeline',()=>{
  it('draws three shared chapters once and places both fourth chapters after the same fork',()=>{
    const original=branch('original',null,{chapters:[0,1,2,3].map(i=>chapter(i)),chaptersTotal:4})
    const child=branch('child','original',{inheritedChapterCount:3,sharedThroughChapterId:'chapter-2',chapters:[0,1,2].map(i=>chapter(i)).concat(chapter(3,{id:'written-4',source:'ai'})),chaptersTotal:4})
    expect(timelineChapterPage(child,0).map(c=>c.id)).toEqual(['written-4'])
    const lanes=timelineLanes(timelineRows(data([original,child])),{})
    expect(lanes.get('original')?.column).toBe(0)
    expect(lanes.get('child')).toEqual({column:3,fork:{branchId:'original',chapterId:'chapter-2',before:false}})
    expect(child.chapters).toHaveLength(4)
  })
  it('shares inherited AI chapters and resolves a fork in inherited text to the visible ancestor',()=>{
    const original=branch('original',null,{chapters:[0,1,2,3].map(i=>chapter(i)),chaptersTotal:4})
    const child=branch('child','original',{inheritedChapterCount:3,sharedThroughChapterId:'chapter-2',chapters:[chapter(3,{id:'written-4',source:'ai'})],chaptersTotal:4})
    const grandchild=branch('grandchild','child',{inheritedChapterCount:4,sharedThroughChapterId:'written-4',chapters:[chapter(4,{id:'written-5',source:'ai'})],chaptersTotal:5})
    const early=branch('early','child',{inheritedChapterCount:2,sharedThroughChapterId:'chapter-1',chapters:[chapter(2,{id:'alternate-3',source:'ai'})],chaptersTotal:3})
    const lanes=timelineLanes(timelineRows(data([original,child,grandchild,early])),{})
    expect(lanes.get('grandchild')).toEqual({column:4,fork:{branchId:'child',chapterId:'written-4',before:false}})
    expect(lanes.get('early')).toEqual({column:2,fork:{branchId:'original',chapterId:'chapter-1',before:false}})
  })
  it('starts long-book branch pagination at its own suffix and keeps missing fork nodes unconnected',()=>{
    const original=branch('original',null,{chapters:Array.from({length:20},(_,i)=>chapter(i)),chaptersTotal:80})
    const child=branch('child','original',{inheritedChapterCount:25,sharedThroughChapterId:'chapter-24',chapters:Array.from({length:45},(_,i)=>chapter(i+25,{source:'ai'})),chaptersTotal:70})
    expect(timelineChapterStart(child)).toBe(25)
    expect(timelineChapterPage(child,0)).toHaveLength(20)
    expect(timelineChapterPage(child,0)[0].position).toBe(25)
    expect(timelinePageLoaded(child,25)).toBe(true)
    expect(timelineLanes(timelineRows(data([original,child])),{}).get('child')).toEqual({column:0})
    expect(timelineLanes(timelineRows(data([original,child])),{child:45}).get('child')).toEqual({column:0})
  })
  it('connects a fork before the first paragraph to the start of the original line',()=>{
    const original=branch('original'),child=branch('child','original',{inheritedChapterCount:0,sharedThroughChapterId:null,chapters:[chapter(0,{id:'alternate-1',source:'ai'})]})
    expect(timelineLanes(timelineRows(data([original,child])),{}).get('child')).toEqual({column:0,fork:{branchId:'original',chapterId:'chapter-0',before:true}})
  })
  it('orders the original trunk before children and preserves each distinct nested line',()=>{
    const rows=timelineRows(data([branch('grandchild','child'),branch('child','original'),branch('other','original'),branch('original')]))
    expect(rows.map(row=>[row.branch.id,row.depth])).toEqual([['original',0],['child',1],['grandchild',2],['other',1]])
    expect(rows.every(row=>!row.detached)).toBe(true)
  })
  it('keeps missing-parent and cyclic historical records accessible without looping or silently merging lines',()=>{
    const rows=timelineRows(data([branch('original'),branch('orphan','missing'),branch('cycle-a','cycle-b'),branch('cycle-b','cycle-a')]))
    expect(rows.map(row=>row.branch.id)).toEqual(['original','orphan','cycle-a','cycle-b'])
    expect(rows.find(row=>row.branch.id==='orphan')?.detached).toBe(true)
    expect(rows.find(row=>row.branch.id==='cycle-a')?.detached).toBe(true)
  })
  it('handles a deep 10000-line chain without recursive stack overflow',()=>{
    const branches=[branch('original')]
    for(let index=1;index<=10000;index++)branches.push(branch('line-'+index,index===1?'original':'line-'+(index-1)))
    const rows=timelineRows(data(branches))
    expect(rows).toHaveLength(10001);expect(rows.at(-1)?.depth).toBe(10000)
  })
  it('conceals unread titles while allowing the saved chapter to remain readable',()=>{
    const unread=chapter(4,{title:'尚未解锁的秘密',observed:false})
    expect(timelineChapterLabel(unread)).toBe('第5章');expect(unread.readable).toBe(true)
    expect(timelineChapterLabel({...unread,observed:true})).toBe('尚未解锁的秘密')
    expect(timelineChapterLabel({...unread,observed:true,titleRevealed:false})).toBe('第5章')
  })
  it('limits each rendered chapter group to 20 and distinguishes an incomplete cached page',()=>{
    const line=branch('original',null,{chapters:Array.from({length:45},(_,index)=>chapter(index)),chaptersTotal:45})
    expect(timelineChapterPage(line,20).map(item=>item.position)).toEqual(Array.from({length:20},(_,index)=>20+index))
    expect(timelinePageLoaded(line,40)).toBe(true)
    expect(timelinePageLoaded({...line,chapters:line.chapters.filter(item=>item.position!==25)},20)).toBe(false)
    expect(timelineChapterPage(line,40)).toHaveLength(5)
  })
  it('merges actual server chapter batches by position without duplicating nodes and accepts current title visibility',()=>{
    const current=data([branch('original',null,{chapters:[chapter(0,{observed:false}),chapter(1)],chaptersTotal:40,nextChapterOffset:20})])
    const incoming=data([branch('original',null,{chapters:[chapter(0,{observed:true}),chapter(20,{source:'ai'})],chaptersTotal:40,nextChapterOffset:40})])
    const merged=mergeTimelinePage(current,incoming)
    expect(merged.branches[0].chapters.map(item=>item.position)).toEqual([0,1,20])
    expect(merged.branches[0].chapters[0].observed).toBe(true)
    expect(current.branches[0].chapters[0].observed).toBe(false)
  })
  it('never attaches old chapter pages to a new saved revision or a different book',()=>{
    const current=data([branch('original',null,{chapters:[chapter(0),chapter(20)],chaptersTotal:40})])
    const changed=data([branch('original',null,{headRevisionId:'new-revision',chapters:[chapter(0)],chaptersTotal:21})])
    expect(mergeTimelinePage(current,changed).branches[0].chapters).toEqual([chapter(0)])
    expect(()=>mergeTimelinePage(current,{...changed,bookId:'other-book'})).toThrow('所属书籍')
  })
  it('opens the exact saved branch/revision/chapter, including read-only archived lines',()=>{
    const archived=branch('child','original',{status:'archived',headRevisionId:'pinned-head'})
    expect(timelineReadLocation('book',archived,chapter(3))).toEqual({path:'/books/book/read',query:{branch:'child',revision:'pinned-head',chapter:'chapter-3'}})
    expect(timelineReadLocation('book',archived).query).not.toHaveProperty('chapter')
  })
})
