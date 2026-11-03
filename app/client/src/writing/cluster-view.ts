export interface ClusterNode {id:string;lineId:string|null;index:number}
export interface ClusterPoint {x:number;y:number}
export interface TimelineCluster {id:string;lineId:string|null;ids:string[];first:number;last:number;point:ClusterPoint}
/** View-only grouping. Shared DAG nodes belong to one cluster and retain all original IDs. */
export function clusterTimeline(nodes:ClusterNode[],edges:{from:string;to:string}[],positions:Map<string,ClusterPoint>,zoom:number){
 if(!Number.isFinite(zoom)||zoom<=0)throw new Error('Invalid graph scale')
 const size=Math.max(1,Math.ceil(72/(180*zoom))),groups=new Map<string,TimelineCluster>(),membership=new Map<string,string>()
 for(const node of nodes){const key='cluster:'+JSON.stringify([node.lineId,Math.floor(node.index/size)]),p=positions.get(node.id);if(!p)throw new Error('Missing graph position');let group=groups.get(key)
  if(!group){group={id:key,lineId:node.lineId,ids:[],first:node.index,last:node.index,point:{...p}};groups.set(key,group)}group.ids.push(node.id);group.first=Math.min(group.first,node.index);group.last=Math.max(group.last,node.index);membership.set(node.id,key)
 }
 for(const group of groups.values()){let x=0,y=0;for(const id of group.ids){const p=positions.get(id)!;x+=p.x;y+=p.y}group.point={x:x/group.ids.length,y:y/group.ids.length}}
 // A short last interval must not overlap the preceding fixed-size touch target.
 for(const lineId of new Set(nodes.map(n=>n.lineId))){const row=[...groups.values()].filter(g=>g.lineId===lineId).sort((a,b)=>a.first-b.first);for(let i=row.length-1;i>0;i--){const last=row[i]!,prior=row[i-1]!;if(Math.hypot(last.point.x-prior.point.x,last.point.y-prior.point.y)*zoom>=64)continue;prior.ids.push(...last.ids);prior.last=Math.max(prior.last,last.last);for(const id of last.ids)membership.set(id,prior.id);groups.delete(last.id);let x=0,y=0;for(const id of prior.ids){const p=positions.get(id)!;x+=p.x;y+=p.y}prior.point={x:x/prior.ids.length,y:y/prior.ids.length}}}
 // Overview circles and labels stay readable even when timeline coordinates
 // are compressed. This changes display positions only, never graph ancestry.
 if(zoom<.25){
  const rows=[...new Set(nodes.map(n=>n.lineId))].map(lineId=>{
   const items=[...groups.values()].filter(g=>g.lineId===lineId)
   return {items,base:Math.min(...items.map(g=>g.point.y))}
  }).filter(row=>row.items.length).sort((a,b)=>a.base-b.base)
  let previous:number|undefined
  for(const row of rows){const y=previous===undefined?row.base:Math.max(row.base,previous+104/zoom);for(const group of row.items)group.point.y+=y-row.base;previous=y}
 }
 const links=new Map<string,{from:string;to:string}>();for(const edge of edges){const from=membership.get(edge.from),to=membership.get(edge.to);if(from&&to&&from!==to)links.set(JSON.stringify([from,to]),{from,to})}
 return {size,groups:[...groups.values()],edges:[...links.values()],membership}
}
