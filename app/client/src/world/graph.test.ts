import {describe,it,expect} from 'vitest'
import {layoutWorldGraph,worldCurve,projectWorldGraph,worldTargetEdges} from './graph'
import type {WorldCheckpoint,WorldGraph} from './types'
import * as graphFunctions from './graph'
function cp(id:string,parent:string|null,lineId:string,sequence:number):WorldCheckpoint{return {id,parentCheckpointId:parent,lineId,sequence,worldId:'world',title:id,text:'text',action:'',options:[],changes:[],summary:'',kind:sequence===0?'start':'turn',createdAt:String(sequence).padStart(3,'0')}}
describe('world graph ancestry',()=>{
  it('draws the common history once, splitting from the actual checkpoint',()=>{
    const graph:WorldGraph={lines:[{id:'a',name:'主线',headCheckpointId:'a4',status:'active',version:1,camera:{}},{id:'b',name:'另一种可能',headCheckpointId:'b4',status:'active',version:1,camera:{}}],nodes:[cp('root',null,'a',0),cp('a1','root','a',1),cp('a2','a1','a',2),cp('a3','a2','a',3),cp('a4','a3','a',4),cp('b4','a3','b',4)]}
    const nodes=layoutWorldGraph(graph);expect(nodes.filter(n=>n.id==='a3')).toHaveLength(1)
    const a=nodes.find(n=>n.id==='a4')!,b=nodes.find(n=>n.id==='b4')!;expect(a.parent).toBe('a3');expect(b.parent).toBe('a3');expect(a.x).toBe(b.x);expect(a.y).not.toBe(b.y)
    expect(worldCurve(nodes.find(n=>n.id==='a3')!,b)).toContain('C')
  })
  it('collapses long histories while retaining the source of later forks',()=>{
    const graph:WorldGraph={lines:[{id:'a',name:'主线',headCheckpointId:'a29',status:'active',version:1,camera:{}},{id:'b',name:'分线',headCheckpointId:'b8',status:'active',version:1,camera:{}}],nodes:[...Array.from({length:30},(_,i)=>cp('a'+i,i?'a'+(i-1):null,'a',i)),cp('b8','a7','b',8)]}
    const nodes=layoutWorldGraph(graph),group=nodes.find(n=>n.members.includes('a7'))!,branch=nodes.find(n=>n.id==='b8')!;expect(group.kind).toBe('group');expect(branch.parent).toBe(group.id);expect(nodes.length).toBeLessThan(20)
    const expanded=layoutWorldGraph(graph,new Set([group.id]));expect(expanded.find(n=>n.id==='b8')?.parent).toBe('a7');expect(expanded.some(n=>n.id==='a7')).toBe(true)
  })
})

describe('large world graph viewport',()=>{
  function large(){return {lines:Array.from({length:50},(_,i)=>({id:'l'+i,name:'线'+i,headCheckpointId:'n'+i+'-99',status:'active',version:1,camera:{}})),nodes:Array.from({length:5000},(_,i)=>cp('n'+Math.floor(i/100)+'-'+i%100,i%100?'n'+Math.floor(i/100)+'-'+(i%100-1):null,'l'+Math.floor(i/100),i%100))} as WorldGraph}
  it('culls offscreen targets while retaining a crossing edge and every source checkpoint',()=>{
    const graph=large(),nodes=layoutWorldGraph(graph),targets=projectWorldGraph(nodes,1),edges=worldTargetEdges(nodes,targets)
    const viewport=(graphFunctions as any).viewportWorldGraph
    expect(typeof viewport).toBe('function')
    const visible=viewport(targets,edges,{x:-1500,y:-3500,zoom:1},{width:600,height:500})
    expect(visible.targets.length).toBeGreaterThan(0);expect(visible.targets.length).toBeLessThan(100)
    expect(nodes.flatMap(n=>n.members).sort()).toEqual(graph.nodes.map(n=>n.id).sort())
    const outside=[{id:'cross',path:'unchanged',bounds:{left:-100,top:200,right:900,bottom:200}}]
    expect(viewport([],outside,{x:0,y:0,zoom:1},{width:600,height:500}).edges).toEqual(outside)
    expect(viewport([],outside,{x:0,y:1000,zoom:1},{width:600,height:500}).edges).toEqual([])
  })
  it.each([.12,.28,.2801,1.8])('bounds visible targets at 500 and retains omitted nodes in screen clusters at %s',zoom=>{
    const nodes=layoutWorldGraph(large()),targets=projectWorldGraph(nodes,zoom),viewport=(graphFunctions as any).viewportWorldGraph
    expect(typeof viewport).toBe('function')
    const visible=viewport(targets,worldTargetEdges(nodes,targets),{x:0,y:0,zoom},{width:30000,height:30000})
    expect(visible.targets.length).toBeLessThanOrEqual(500)
    expect(visible.targets.flatMap((t:any)=>t.nodes.flatMap((n:any)=>n.members)).sort()).toEqual(nodes.flatMap(n=>n.members).sort())
    expect(visible.targets.every((t:any)=>t.width>=44&&t.height>=44)).toBe(true)
  })
})

describe('world graph screen targets',()=>{
  const graph:WorldGraph={lines:[{id:'a',name:'主线',headCheckpointId:'a3',status:'active',version:1,camera:{}},{id:'b',name:'分线',headCheckpointId:'b3',status:'active',version:1,camera:{}}],nodes:[cp('a0',null,'a',0),cp('a1','a0','a',1),cp('a2','a1','a',2),cp('a3','a2','a',3),cp('b3','a2','b',3)]}
  const nodes=layoutWorldGraph(graph)
  it.each([.2801,.4096,1,1.8])('keeps distinct 44px targets without restricting zoom at %s',zoom=>{
    const targets=projectWorldGraph(nodes,zoom)
    expect(targets).toHaveLength(nodes.length)
    for(const [i,target] of targets.entries()){
      expect(target.width).toBeGreaterThanOrEqual(44);expect(target.height).toBeGreaterThanOrEqual(44)
      expect(target.x).toBe(nodes[i]!.x*zoom);expect(target.y).toBe((nodes[i]!.y+51)*zoom)
      for(const other of targets.slice(i+1)){
        const gapX=Math.abs(target.x-other.x)-(target.width+other.width)/2
        const gapY=Math.abs(target.y-other.y)-(target.height+other.height)/2
        expect(Math.max(gapX,gapY)).toBeGreaterThanOrEqual(8)
      }
    }
    const fork=targets.find(t=>t.id==='b3')!,shared=targets.find(t=>t.id==='a2')!
    expect(worldTargetEdges(nodes,targets).some(edge=>edge.id===shared.id+'>'+fork.id)).toBe(true)
  })
  it.each([.12,.2799,.28])('groups dense targets at %s without losing history or overlapping tap areas',zoom=>{
    const targets=projectWorldGraph(nodes,zoom)
    expect(targets.flatMap(t=>t.nodes.map(n=>n.id)).sort()).toEqual(nodes.map(n=>n.id).sort())
    for(const [i,target] of targets.entries()){
      expect(target.width).toBe(44);expect(target.height).toBe(44);expect(target.overview).toBe(true)
      for(const other of targets.slice(i+1))expect(Math.max(Math.abs(target.x-other.x),Math.abs(target.y-other.y))).toBeGreaterThanOrEqual(56)
    }
    const edges=worldTargetEdges(nodes,targets)
    expect(new Set(edges.map(e=>e.id)).size).toBe(edges.length)
    expect(edges.every(e=>{const [a,b]=e.id.split('>');return a!==b})).toBe(true)
    if(zoom===.12)expect(targets.some(t=>t.nodes.length>1)).toBe(true)
  })
  it('preserves expanded long histories and shared fork ancestors through overview projection',()=>{
    const long:WorldGraph={...graph,nodes:[...Array.from({length:30},(_,i)=>cp('a'+i,i?'a'+(i-1):null,'a',i)),cp('b8','a7','b',8)]}
    const collapsed=layoutWorldGraph(long),group=collapsed.find(n=>n.members.includes('a7'))!
    const expanded=layoutWorldGraph(long,new Set([group.id])),targets=projectWorldGraph(expanded,.12)
    expect(targets.flatMap(t=>t.nodes).find(n=>n.id==='b8')?.parent).toBe('a7')
    expect(targets.flatMap(t=>t.nodes.map(n=>n.members)).flat().sort()).toEqual(long.nodes.map(n=>n.id).sort())
  })
})
