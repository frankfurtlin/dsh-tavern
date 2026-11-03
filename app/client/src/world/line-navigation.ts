import type {WorldGraph} from './types'

export function currentGraphLine(graph:WorldGraph,requested:string,active:string|null){
  const readable=graph.lines.filter(line=>line.status!=='deleted')
  return readable.find(line=>line.id===requested)?.id||readable.find(line=>line.id===active)?.id||
    readable.find(line=>line.status==='active')?.id||readable[0]?.id||''
}

export function checkpointReadingLine(graph:WorldGraph,checkpoint:string,preferred:string){
  const nodes=new Map(graph.nodes.map(node=>[node.id,node]))
  const contains=(head:string)=>{
    const seen=new Set<string>();let id:string|null=head
    while(id&&!seen.has(id)){
      if(id===checkpoint)return true
      seen.add(id);id=nodes.get(id)?.parentCheckpointId||null
    }
    return false
  }
  const lines=graph.lines.filter(line=>line.status!=='deleted'&&contains(line.headCheckpointId))
  return lines.find(line=>line.id===preferred)?.id||lines.find(line=>line.status==='active')?.id||lines[0]?.id||''
}
