"""Client events are hints; facts are written by the committing domain transaction."""
import re
from sqlalchemy.dialects.postgresql import insert
from ..models import uid, now
from .models import Event

PRIVATE = re.compile(r"password|token|secret|api.?key|authorization|content|prompt|email|phone|card.?code|stack|message|text|quote", re.I)
SAFE_CLIENT_NAMES = {"frontend.runtime_error", "product.wallet.opened", "product.knowledge.opened"}
CLIENT_DIMENSIONS = {'worldId','lineId','checkpointId','taskId','characterId','routeId','scope','mode','source',
    'errorCode','outcome','state','feature','version','cacheHit','count','hasImage','hasMemory','modelId'}


def client_metadata(value):
    """No free-text field, conversation or user input is an analytics dimension."""
    return {key:item for key,item in sanitize(value or {}).items() if key in CLIENT_DIMENSIONS
        and (item is None or isinstance(item,(bool,int,float)) or
            isinstance(item,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',item))}


def sanitize(value, depth=0):
    if depth > 4 or value is None:
        return None
    if isinstance(value, dict):
        return {str(k)[:80]: sanitize(v, depth + 1) for k, v in list(value.items())[:40] if not PRIVATE.search(str(k))}
    if isinstance(value, list):
        return [sanitize(v, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return re.sub(r"Bearer\s+[\w.-]+|\bsk-[\w-]+", "[redacted]", value, flags=re.I)[:256]
    if isinstance(value, (bool, int, float)):
        return value
    return None


def emit(db, owner, key, name, *, category="fact", data=None, path="", session="", anon=""):
    db.execute(insert(Event).values(id=uid(), created_at=now(), owner_id=owner, event_key=key,
        name=name, category=category, data=sanitize(data or {}), path=path, session_id=session, anon_id=anon)
        .on_conflict_do_nothing(index_elements=[Event.event_key]))


def checkpoint_facts(db, checkpoint, parent):
    emit(db, checkpoint.owner_id, "checkpoint:" + checkpoint.id, "fact.action.success",
         data={"worldId": checkpoint.world_id, "lineId": checkpoint.line_id, "checkpointId": checkpoint.id, "kind": checkpoint.kind})
    emit(db, checkpoint.owner_id, "first-action:" + checkpoint.owner_id, "fact.first_action.success")
    # Match explicit state markers, never prose or intended plans. Generic worlds
    # still expose the first successful checkpoint without inventing an ending.
    current, previous = checkpoint.state, parent.state
    current_plot, prior_plot = current.get("plot", {}), previous.get("plot", {})
    current_case, prior_case = current_plot.get("resolution", {}), prior_plot.get("resolution", {})
    if current_plot.get('ending'):
        from sqlalchemy import select
        from ..world.models import WorldCheckpoint
        completed_endings = set(db.scalars(select(WorldCheckpoint.state['plot']['ending'].astext).where(
            WorldCheckpoint.owner_id == checkpoint.owner_id, WorldCheckpoint.world_id == checkpoint.world_id,
            WorldCheckpoint.line_id != checkpoint.line_id,
            WorldCheckpoint.state['plot']['resolution']['phase'].astext == 'resolved')))
        if completed_endings and current_plot['ending'] not in completed_endings:
            emit(db,checkpoint.owner_id,'second-ending:'+checkpoint.line_id,'fact.second_ending.started',
                 data={'worldId':checkpoint.world_id,'lineId':checkpoint.line_id,'ending':current_plot['ending']})
    objectives = checkpoint.definition.get("script", {}).get("objectives", [])
    objective_ids = {o["id"] for o in objectives}
    if objective_ids and objective_ids <= set(current_plot.get("completed", [])) and not objective_ids <= set(prior_plot.get("completed", [])):
        emit(db, checkpoint.owner_id, "investigation:" + checkpoint.line_id, "fact.investigation.completed",
             data={"worldId": checkpoint.world_id, "lineId": checkpoint.line_id})
    if isinstance(current_case, dict) and current_case.get("phase") == "resolved" and prior_case.get("phase") != "resolved":
        emit(db, checkpoint.owner_id, "aftermath:" + checkpoint.line_id, "fact.aftermath.resolved",
             data={"worldId": checkpoint.world_id, "lineId": checkpoint.line_id, "ending": current_case.get("endingId")})
