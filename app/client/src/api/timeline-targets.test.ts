import {it,expect} from 'vitest'
import * as geometry from './graph-camera'
import * as timeline from './story-timeline'
it('keeps the complete 12%–180% camera range and centers clamped cursor zoom',()=>{
  expect(geometry.MIN_SCALE).toBe(.12);expect(geometry.MAX_SCALE).toBe(1.8)
  const start={x:42,y:18,scale:1},origin={x:100,y:80},a=geometry.zoomAround(start,.01,origin),b=geometry.zoomAround(start,50,origin)
  expect((origin.x-a.x)/a.scale).toBeCloseTo(origin.x-start.x,10);expect((origin.y-b.y)/b.scale).toBeCloseTo(origin.y-start.y,10)
})
it.each([.12,.28])('aggregates every loaded chapter into separate 44px cells at %s',scale=>{
  const project=(timeline as any).timelineOverviewTargets;expect(typeof project).toBe('function')
  const source=Array.from({length:200},(_,i)=>({id:'chapter'+i,x:(i%20)*202+164,y:Math.floor(i/20)*174+112,item:{contentId:'chapter'+i}}))
  const targets=project(source,scale)
  expect(targets.flatMap((t:any)=>t.items.map((p:any)=>p.id)).sort()).toEqual(source.map(p=>p.id).sort())
  for(const [i,t] of targets.entries()){expect(t.width).toBe(44);expect(t.height).toBe(44);for(const other of targets.slice(i+1))expect(Math.max(Math.abs(t.x-other.x),Math.abs(t.y-other.y))).toBeGreaterThanOrEqual(56)}
})
it('uses the known loaded IDs rather than a chapter title to identify choices',()=>{
  const project=(timeline as any).timelineOverviewTargets;expect(typeof project).toBe('function')
  const targets=project([{id:'a',x:10,y:10,item:{title:'同名'}},{id:'b',x:20,y:10,item:{title:'同名'}}],.12)
  expect(targets[0].items.map((p:any)=>p.id)).toEqual(['a','b'])
})

it.each([.2801,.29,.3,.4096,1,1.8])('keeps a real CSS layout-rounded chapter hit frame at least44px at %s',scale=>{
  const size=(timeline as any).timelineChapterHitSize;expect(typeof size).toBe('function');const value=size(scale)
  const layoutFloor=(pixels:number)=>Math.floor(pixels*64)/64
  expect(layoutFloor(value.width)*scale).toBeGreaterThanOrEqual(44);expect(layoutFloor(value.height)*scale).toBeGreaterThanOrEqual(44)
})
