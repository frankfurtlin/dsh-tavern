from sqlalchemy import select
from ..errors import Problem
from ..world.models import WorldCheckpoint
from ..world.service import require_line,checked_checkpoint
from ..world.state import public_view,public_payload,checkpoint_hash
from .common import exposures

MAX_NODES=200000


def skeleton(db,owner,world_id):
    rows=db.execute(select(WorldCheckpoint.id,WorldCheckpoint.parent_checkpoint_id,WorldCheckpoint.line_id,
        WorldCheckpoint.state["minute"].as_integer()).where(WorldCheckpoint.owner_id==owner,
        WorldCheckpoint.world_id==world_id).limit(MAX_NODES+1)).all()
    if len(rows)>MAX_NODES:raise Problem("COMPARE_GRAPH_LIMIT","请比较更短的故事路径",422)
    return {row[0]:{"parent":row[1],"line":row[2],"minute":row[3]} for row in rows}


def path(graph,node):
    result=[];seen=set()
    while node is not None:
        if node in seen or node not in graph or len(result)>=10000:raise Problem("WORLD_CHECKPOINT_INVALID","故事路径校验未通过",409)
        seen.add(node);result.append(node);node=graph[node]["parent"]
    return result[::-1]


def side(db,owner,world_id,line_id,node_id,graph,data,offset):
    _,line=require_line(db,owner,world_id,line_id)
    full=path(graph,line.head_checkpoint_id)
    requested=node_id or line.head_checkpoint_id
    if requested not in full:raise Problem("RESOURCE_NOT_FOUND","比较节点不属于所选路径",404)
    target=full[:full.index(requested)+1]
    if data.alignment=="same-time":
        target=[node for node in target if graph[node]["minute"]<=data.atMinute]
        if not target:raise Problem("COMPARE_TIME_OUTSIDE_PATH","该时间点尚未开始这条路径",409)
    saved=line.reading_position.get("checkpointId")
    viewed=exposures(db,owner,"world",world_id,line_id)
    boundaries=[saved]+[row.anchor.get("checkpointId") for row in viewed]
    last=max((target.index(node) for node in boundaries if node in target),default=-1)
    # A read boundary later on this same path also authorizes its frozen ancestors.
    if any(node in full and full.index(node)>=len(target)-1 for node in boundaries):last=len(target)-1
    known=target[:last+1]
    page=known[offset:offset+data.limit]
    required=set(page)|({known[-1]} if known else set())
    records={row.id:row for row in db.scalars(select(WorldCheckpoint).where(WorldCheckpoint.owner_id==owner,
        WorldCheckpoint.world_id==world_id,WorldCheckpoint.id.in_(required)))}
    for point in records.values():
        if point.content_hash!=checkpoint_hash(point.state,point.definition,point.text,point.parent_checkpoint_id):
            raise Problem("WORLD_CHECKPOINT_INVALID","存档校验未通过",409)
    if set(records)!=required:raise Problem("RESOURCE_NOT_FOUND","故事路径不完整",404)
    items=[]
    for node in page:
        point=records[node]
        items.append(public_payload({"id":point.id,"title":point.title,"text":point.text,"action":point.action,
            "minute":point.state["minute"]},point.state,point.definition))
    state=None
    if known:
        point=records[known[-1]]
        view=public_view(point.state,point.definition)
        state={key:view[key] for key in ("playerId","minute","timeLabel","location","ended")}
        state["entities"]={entity["id"]:{k:v for k,v in entity.items() if k in {"name","type","state"}} for entity in view["entities"]}
        state["lore"]={entry["id"]:{k:v for k,v in entry.items() if k in {"title","text","entityIds"}} for entry in view["lore"]}
    exposure=next((row for row in viewed if row.node_id==target[-1]),None)
    value={"lineId":line.id,"name":line.name,"nodeId":target[-1],"requestedNodeId":requested,"viewVersion":exposure.version if exposure else 0,
        "observedNodeId":known[-1] if known else None,"hasUnseen":len(known)<len(target),"items":items,
        "nextOffset":offset+len(items) if offset+len(items)<len(known) else None,"state":state}
    return value,target,set(known)


def compare(db,owner,data):
    graph=skeleton(db,owner,data.worldId)
    left,left_path,left_known=side(db,owner,data.worldId,data.leftLineId,data.leftNodeId,graph,data,data.leftOffset)
    right,right_path,right_known=side(db,owner,data.worldId,data.rightLineId,data.rightNodeId,graph,data,data.rightOffset)
    common=None
    for a,b in zip(left_path,right_path):
        if a!=b:break
        common=a
    start={"nodeId":common,"visible":common in left_known and common in right_known,"title":None}
    if start["visible"]:
        point=checked_checkpoint(db,owner,data.worldId,common)
        start["title"]=public_payload(point.title,point.state,point.definition)
    return left,right,start
