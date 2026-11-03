import type {WorldGraph,WorldCheckpoint} from './types'
export interface GraphNode{ id:string;parent:string|null;title:string;x:number;y:number;lineId:string;sequence:number;kind:string;members:string[];checkpoint:WorldCheckpoint}
export function layoutWorldGraph(graph:WorldGraph,expanded:Set<string>=new Set()):GraphNode[]{
  const lineRows=new Map(graph.lines.map((line,i)=>[line.id,i])),groups=new Map<string,WorldCheckpoint[]>(),replacement=new Map<string,string>()
  const nodes:GraphNode[]=[]
  for(const cp of graph.nodes){const arr=groups.get(cp.lineId)||[];arr.push(cp);groups.set(cp.lineId,arr)}
  for(const [lineId,checkpoints] of groups){const ordered=checkpoints.sort((a,b)=>a.sequence-b.sequence||a.createdAt.localeCompare(b.createdAt));let i=0
    while(i<ordered.length){const cp=ordered[i]!;const pack=ordered.length>12&&i<ordered.length-6?ordered.slice(i,Math.min(i+6,ordered.length-6)):[];const groupId='group:'+lineId+':'+cp.id
      if(pack.length>1&&!expanded.has(groupId)){for(const member of pack)replacement.set(member.id,groupId);nodes.push({id:groupId,parent:cp.parentCheckpointId,title:`回合 ${pack[0]!.sequence} — ${pack.at(-1)!.sequence}`,x:0,y:(lineRows.get(lineId)||0)*190,lineId,sequence:cp.sequence,kind:'group',members:pack.map(c=>c.id),checkpoint:pack.at(-1)!});i+=pack.length}
      else{const unpack=pack.length>1?pack:[cp];for(const c of unpack)nodes.push({id:c.id,parent:c.parentCheckpointId,title:c.title,x:0,y:(lineRows.get(lineId)||0)*190,lineId,sequence:c.sequence,kind:c.kind,members:[c.id],checkpoint:c});i+=unpack.length}
    }
  }
  const lookup=new Map(nodes.map(n=>[n.id,n])),visiting=new Set<string>()
  function depth(node:GraphNode):number{if(node.x)return node.x;if(visiting.has(node.id))return 120;visiting.add(node.id);node.parent=node.parent?(replacement.get(node.parent)||node.parent):null;const parent=node.parent?lookup.get(node.parent):null;node.x=parent?depth(parent)+205:100;visiting.delete(node.id);return node.x}
  nodes.forEach(depth);return nodes
}
export function worldCurve(a:GraphNode,b:GraphNode){const mid=(a.x+b.x)/2;return `M ${a.x+68} ${a.y+48} C ${mid+40} ${a.y+48}, ${mid-40} ${b.y+48}, ${b.x-68} ${b.y+48}`}

export interface WorldGraphTarget{
  id:string;x:number;y:number;width:number;height:number;nodes:GraphNode[];overview:boolean
}
// Keep hit areas in screen pixels. At or below 28%, group nearby nodes on the
// 190px graph rows, so a world-anchored grid groups nearby nodes instead.
export function projectWorldGraph(nodes:GraphNode[],zoom:number):WorldGraphTarget[]{
  if(zoom>.28)return nodes.map(node=>({id:node.id,x:node.x*zoom,y:(node.y+51)*zoom,
    width:Math.max(44,148*zoom),height:Math.max(44,102*zoom),nodes:[node],overview:false}))
  const cells=new Map<string,WorldGraphTarget>()
  for(const node of nodes){
    const column=Math.floor(node.x*zoom/56),row=Math.floor((node.y+51)*zoom/56),id=`overview:${column}:${row}`
    let target=cells.get(id)
    if(!target){target={id,x:column*56+28,y:row*56+28,width:44,height:44,nodes:[],overview:true};cells.set(id,target)}
    target.nodes.push(node)
  }
  return [...cells.values()]
}
export function worldTargetCurve(a:WorldGraphTarget,b:WorldGraphTarget){
  if(a.x===b.x){
    const direction=Math.sign(b.y-a.y),from=a.y+direction*a.height/2,to=b.y-direction*b.height/2,mid=(from+to)/2
    return `M ${a.x} ${from} C ${a.x} ${mid}, ${b.x} ${mid}, ${b.x} ${to}`
  }
  const from=a.x+a.width/2,to=b.x-b.width/2,mid=(from+to)/2
  return `M ${from} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${to} ${b.y}`
}
export function worldTargetEdges(nodes:GraphNode[],targets:WorldGraphTarget[]){
  const lookup=new Map(targets.flatMap(target=>target.nodes.map(node=>[node.id,target] as const))),seen=new Set<string>()
  return nodes.flatMap(node=>{
    const a=node.parent?lookup.get(node.parent):null,b=lookup.get(node.id)
    if(!a||!b||a===b)return []
    const id=a.id+'>'+b.id;if(seen.has(id))return [];seen.add(id)
    return [{id,path:worldTargetCurve(a,b),from:a.id,to:b.id,bounds:{left:Math.min(a.x-a.width/2,b.x-b.width/2),top:Math.min(a.y-a.height/2,b.y-b.height/2),right:Math.max(a.x+a.width/2,b.x+b.width/2),bottom:Math.max(a.y+a.height/2,b.y+b.height/2)}}]
  })
}

export interface WorldGraphEdge{id:string;path:string;from?:string;to?:string;bounds:{left:number;top:number;right:number;bottom:number}}
// Coordinates already include zoom; the camera only translates them. Keep
// crossing edges even when both endpoints are outside the viewport.
export function viewportWorldGraph(targets:WorldGraphTarget[],edges:WorldGraphEdge[],camera:{x:number;y:number},size:{width:number;height:number}){
  const margin=44,left=-camera.x-margin,top=-camera.y-margin,right=left+size.width+margin*2,bottom=top+size.height+margin*2
  const intersects=(b:WorldGraphEdge['bounds'])=>b.right>=left&&b.left<=right&&b.bottom>=top&&b.top<=bottom
  let visible=targets.filter(t=>intersects({left:t.x-t.width/2,right:t.x+t.width/2,top:t.y-t.height/2,bottom:t.y+t.height/2}))
  let visibleEdges=edges.filter(e=>intersects(e.bounds))
  if(visible.length<=500)return {targets:visible,edges:visibleEdges}
  // Very large displays can contain >500 legitimate 44px targets. Aggregate
  // their screen cells instead of truncating nodes or restricting the zoom.
  const original=visible,remap=new Map<string,WorldGraphTarget>()
  let cell=56
  do{
    cell=Math.ceil(cell*1.25);const cells=new Map<string,WorldGraphTarget>();remap.clear()
    for(const target of original){const col=Math.floor(target.x/cell),row=Math.floor(target.y/cell),id=`viewport:${cell}:${col}:${row}`
      let group=cells.get(id);if(!group){group={id,x:col*cell+cell/2,y:row*cell+cell/2,width:44,height:44,nodes:[],overview:true};cells.set(id,group)}
      group.nodes.push(...target.nodes);remap.set(target.id,group)
    }
    visible=[...cells.values()]
  }while(visible.length>500)
  const lookup=new Map(targets.map(t=>[t.id,t])),seen=new Set<string>()
  visibleEdges=visibleEdges.flatMap(edge=>{
    const a=remap.get(edge.from||'')||lookup.get(edge.from||''),b=remap.get(edge.to||'')||lookup.get(edge.to||'')
    if(!a||!b)return [edge];if(a===b)return []
    const id=a.id+'>'+b.id;if(seen.has(id))return [];seen.add(id)
    return [{...edge,id,path:worldTargetCurve(a,b)}]
  })
  return {targets:visible,edges:visibleEdges}
}
