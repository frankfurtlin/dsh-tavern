import {describe,it,expect} from 'vitest'
import {MIN_SCALE,MAX_SCALE,zoomAround,fitGraph,focusGraph,panGraph,stepSpring} from './graph-camera'
import type {Camera,Point} from './graph-camera'

const worldAt=(camera:Camera,point:Point)=>({x:(point.x-camera.x)/camera.scale,y:(point.y-camera.y)/camera.scale})
const screenAt=(camera:Camera,point:Point)=>({x:point.x*camera.scale+camera.x,y:point.y*camera.scale+camera.y})
const allFinite=(value:object)=>Object.values(value).every(Number.isFinite)

describe('graph viewport mathematics',()=>{
  it('keeps the cursor world point fixed during zoom, including both clamped limits',()=>{
    const camera=Object.freeze({x:-220,y:80,scale:.9}),cursor={x:317,y:143},before=worldAt(camera,cursor)
    for(const factor of [1.2,.001,1000]){
      const result=zoomAround(camera,factor,cursor),after=worldAt(result,cursor)
      expect(after.x).toBeCloseTo(before.x,10);expect(after.y).toBeCloseTo(before.y,10)
      expect(result.scale).toBeGreaterThanOrEqual(MIN_SCALE);expect(result.scale).toBeLessThanOrEqual(MAX_SCALE)
    }
  })
  it('bounds scale and rejects invalid zoom factors without producing nonfinite coordinates',()=>{
    const camera={x:10,y:20,scale:1}
    expect(zoomAround(camera,1000,{x:0,y:0}).scale).toBe(MAX_SCALE)
    expect(zoomAround(camera,.0001,{x:0,y:0}).scale).toBe(MIN_SCALE)
    for(const factor of [NaN,Infinity,0,-1])expect(zoomAround(camera,factor,{x:25,y:35})).toEqual(camera)
    expect(allFinite(zoomAround({x:NaN,y:Infinity,scale:NaN},2,{x:Infinity,y:NaN}))).toBe(true)
  })
  it('fits an ordinary graph within padding and centers an extremely long graph at minimum scale',()=>{
    const graph={x:-100,y:25,width:1200,height:600},viewport={width:1000,height:700},fit=fitGraph(graph,viewport)
    const start=screenAt(fit,graph),end=screenAt(fit,{x:graph.x+graph.width,y:graph.y+graph.height})
    expect(start.x).toBeGreaterThanOrEqual(56);expect(start.y).toBeGreaterThanOrEqual(56)
    expect(end.x).toBeLessThanOrEqual(viewport.width-56);expect(end.y).toBeLessThanOrEqual(viewport.height-56)
    const long=fitGraph({x:30,y:-50,width:20000,height:1000},viewport)
    expect(long.scale).toBe(MIN_SCALE);expect(screenAt(long,{x:10030,y:450})).toEqual({x:500,y:350})
    expect(allFinite(fitGraph({x:NaN,y:Infinity,width:0,height:0},{width:0,height:NaN},Infinity))).toBe(true)
  })
  it('focuses the chosen world point at viewport center and clamps requested scale',()=>{
    const point={x:640,y:-120},view={width:390,height:740}
    expect(screenAt(focusGraph(point,view),point)).toEqual({x:195,y:370})
    expect(focusGraph(point,view,9).scale).toBe(MAX_SCALE)
  })
  it('keeps some canvas visible after dragging in any direction even with an offset world origin',()=>{
    const graph={x:-180,y:90,width:1400,height:600},viewport={width:390,height:740}
    for(const direction of [{x:1e6,y:1e6},{x:-1e6,y:-1e6},{x:1e6,y:-1e6}]){
      const moved=panGraph(fitGraph(graph,viewport),direction.x,direction.y,graph,viewport),start=screenAt(moved,graph),end=screenAt(moved,{x:graph.x+graph.width,y:graph.y+graph.height})
      expect(Math.min(viewport.width,end.x)-Math.max(0,start.x)).toBeGreaterThan(0)
      expect(Math.min(viewport.height,end.y)-Math.max(0,start.y)).toBeGreaterThan(0)
    }
    expect(allFinite(panGraph({x:NaN,y:Infinity,scale:0},NaN,Infinity,{x:0,y:0,width:0,height:0},{width:0,height:0}))).toBe(true)
  })
  it('returns a dragged node to its target with a small damped overshoot and settles at rest',()=>{
    let position={x:100,y:-70},velocity={x:0,y:0};const target={x:0,y:0};let minimum=100
    for(let frame=0;frame<240;frame++){const step=stepSpring(position,velocity,target,1/60);position=step.position;velocity=step.velocity;minimum=Math.min(minimum,position.x)}
    expect(minimum).toBeLessThan(0);expect(minimum).toBeGreaterThan(-10)
    expect(position).toEqual(target);expect(velocity).toEqual({x:0,y:0})
  })
  it('caps a long pause at 50ms and continues to converge instead of jumping or exploding',()=>{
    const point={x:120,y:-40},velocity={x:5,y:80},target={x:10,y:20}
    expect(stepSpring(point,velocity,target,60)).toEqual(stepSpring(point,velocity,target,.05))
    let step={position:point,velocity}
    for(let frame=0;frame<100;frame++){step=stepSpring(step.position,step.velocity,target,60);expect(allFinite(step.position)).toBe(true);expect(allFinite(step.velocity)).toBe(true)}
    expect(step.position).toEqual(target);expect(step.velocity).toEqual({x:0,y:0})
    expect(stepSpring(point,velocity,target,NaN)).toEqual({position:point,velocity})
    expect(stepSpring(point,velocity,target,-1)).toEqual({position:point,velocity})
    expect(allFinite(stepSpring({x:NaN,y:Infinity},{x:NaN,y:Infinity},{x:NaN,y:Infinity},1).position)).toBe(true)
  })
})
