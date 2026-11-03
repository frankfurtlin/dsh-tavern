export interface Point {x:number;y:number}
export interface Camera extends Point {scale:number}
export interface Size {width:number;height:number}
export interface Bounds extends Point,Size {}
export interface SpringStep {position:Point;velocity:Point}

export const MIN_SCALE=.12
export const MAX_SCALE=1.8
const finite=(value:number,fallback=0)=>Number.isFinite(value)?value:fallback
const clamp=(value:number,min:number,max:number)=>Math.min(max,Math.max(min,value))
const scale=(value:number)=>clamp(finite(value,1),MIN_SCALE,MAX_SCALE)
const camera=(value:Camera):Camera=>({x:finite(value.x),y:finite(value.y),scale:scale(value.scale)})
const size=(value:Size):Size=>({width:Math.max(1,finite(value.width,1)),height:Math.max(1,finite(value.height,1))})
const bounds=(value:Bounds):Bounds=>({x:finite(value.x),y:finite(value.y),width:Math.max(0,finite(value.width)),height:Math.max(0,finite(value.height))})

// Camera x/y are viewport pixels: viewPoint = worldPoint * scale + camera.
// The cursor's world point stays fixed even when the requested zoom is clamped.
export function zoomAround(value:Camera,factor:number,origin:Point):Camera{
  const current=camera(value)
  if(!Number.isFinite(factor)||factor<=0)return current
  const next=clamp(current.scale*factor,MIN_SCALE,MAX_SCALE),ratio=next/current.scale
  const x=finite(origin.x),y=finite(origin.y)
  return {x:finite(x-(x-current.x)*ratio,current.x),y:finite(y-(y-current.y)*ratio,current.y),scale:next}
}

export function focusGraph(point:Point,viewport:Size,zoom=1):Camera{
  const view=size(viewport),next=scale(zoom)
  return {x:finite(view.width/2-finite(point.x)*next,view.width/2),y:finite(view.height/2-finite(point.y)*next,view.height/2),scale:next}
}

export function fitGraph(value:Bounds,viewport:Size,padding=56):Camera{
  const graph=bounds(value),view=size(viewport),inset=Math.max(0,finite(padding,56))
  const point={x:finite(graph.x+graph.width/2,graph.x),y:finite(graph.y+graph.height/2,graph.y)}
  if(!graph.width&&!graph.height)return focusGraph(point,view)
  const availableWidth=Math.max(1,view.width-2*inset),availableHeight=Math.max(1,view.height-2*inset)
  const zoom=Math.min(availableWidth/Math.max(1,graph.width),availableHeight/Math.max(1,graph.height))
  // A graph too large for MIN_SCALE remains centered and can be panned.
  return focusGraph(point,view,zoom)
}

export function panGraph(value:Camera,dx:number,dy:number,valueBounds:Bounds,viewport:Size):Camera{
  const current=camera(value),graph=bounds(valueBounds),view=size(viewport)
  const width=Math.max(1,graph.width)*current.scale,height=Math.max(1,graph.height)*current.scale
  const visibleX=Math.min(64,view.width/4,width),visibleY=Math.min(64,view.height/4,height)
  const left=graph.x*current.scale,top=graph.y*current.scale
  const minX=finite(visibleX-left-width,current.x),maxX=finite(view.width-visibleX-left,current.x)
  const minY=finite(visibleY-top-height,current.y),maxY=finite(view.height-visibleY-top,current.y)
  return {x:clamp(finite(current.x+finite(dx),current.x),Math.min(minX,maxX),Math.max(minX,maxX)),y:clamp(finite(current.y+finite(dy),current.y),Math.min(minY,maxY),Math.max(minY,maxY)),scale:current.scale}
}

// Exact damped-spring integration avoids Euler instability at low frame rates.
// A suspended tab advances at most 50ms; no huge resume-time jump is introduced.
export function stepSpring(current:Point,velocity:Point,target:Point,dt:number):SpringStep{
  const duration=clamp(finite(dt),0,.05),omega=18,damping=.74,decay=omega*damping,frequency=omega*Math.sqrt(1-damping*damping)
  const envelope=Math.exp(-decay*duration),cosine=Math.cos(frequency*duration),sine=Math.sin(frequency*duration)
  function axis(position:number,speed:number,destination:number){
    const from=finite(position),to=finite(destination,from),v=finite(speed),delta=from-to
    if(!duration)return {position:from,velocity:v}
    const next=finite(to+envelope*(delta*cosine+(v+decay*delta)/frequency*sine),to)
    const nextSpeed=finite(envelope*(v*cosine-(decay*v+omega*omega*delta)/frequency*sine))
    return Math.abs(next-to)<.001&&Math.abs(nextSpeed)<.01?{position:to,velocity:0}:{position:next,velocity:nextSpeed}
  }
  const x=axis(current.x,velocity.x,target.x),y=axis(current.y,velocity.y,target.y)
  return {position:{x:x.position,y:y.position},velocity:{x:x.velocity,y:y.velocity}}
}
