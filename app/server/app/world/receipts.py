"""Reuse a received director proposal only for an explicit identical retry."""
import json
import re
from sqlalchemy import select
from ..models import Task, uid
from ..domain import digest
from ..storage import private_path
from ..errors import Problem
from .models import WorldTurn
from .schemas import DirectorOutput
from .state import parse_json


def reusable_director(db, task, turn, parent, index):
    if index!=1 or task.task_type!='world_turn':return None
    previous=db.scalars(select(WorldTurn).join(Task,Task.id==WorldTurn.task_id).where(
        WorldTurn.owner_id==task.owner_id,WorldTurn.world_id==turn.world_id,
        WorldTurn.line_id==turn.line_id,WorldTurn.parent_checkpoint_id==parent.id,
        WorldTurn.action==turn.action,WorldTurn.kind==turn.kind,WorldTurn.task_id!=task.id,
        Task.state=='failed',Task.error_code.in_(['WORLD_OUTPUT_INVALID','WORLD_OUTPUT_INCOMPLETE','WORLD_ENTITY_INVALID','WORLD_DOCUMENT_RECEIPT_INVALID','WORLD_SETTLEMENT_INVALID','WORLD_INVENTORY_INVALID','WORLD_RESOURCE_INVALID','WORLD_RELATION_INVALID','WORLD_STATE_INVALID','WORLD_KNOWLEDGE_INVALID','PROVIDER_CONNECTION_FAILED']),Task.cancel_requested.is_(False))
        .order_by(WorldTurn.created_at.desc()).limit(4))
    for old_turn in previous:
        old=db.get(Task,old_turn.task_id)
        if old.error_code=='PROVIDER_CONNECTION_FAILED' and old.provider_state!='not_sent':continue
        if old.error_code=='WORLD_SETTLEMENT_INVALID' and old.provider_state!='completed':continue
        if any(old.payload.get(key)!=task.payload.get(key) for key in ('selectedTarget','sceneIntent','interactionMode')):continue
        receipt_task=old
        saved=old_turn.plan.get(str(index),{}).get('plan')
        for _ in range(4):
            if receipt_task.checkpoint.get('calls',{}).get('analysis:director:1'):break
            cached=db.scalar(select(WorldTurn).where(WorldTurn.task_id==receipt_task.id))
            origin=(cached.plan.get(str(index),{}) if cached else {}).get('reusedDirectorTaskId')
            source=db.get(Task,origin) if origin else None
            if not source or source.owner_id!=task.owner_id or any(source.payload.get(k)!=task.payload.get(k) for k in ('worldId','lineId','parentCheckpointId','selectedTarget','sceneIntent','interactionMode')):break
            receipt_task=source
        call=receipt_task.checkpoint.get('calls',{}).get('analysis:director:1')
        if not call:continue
        path=private_path(task.owner_id,f"journal-{receipt_task.id}-{call['callId']}.json")
        try:
            receipt=json.loads(path.read_text('utf8'));result=receipt['result']
            if receipt['hash']!=digest(json.dumps(result,ensure_ascii=False,sort_keys=True)) or result.get('finishReason')!='stop':continue
            # The stored proposal has already resolved round-local references.
            # Raw E### references in the provider receipt belong to that old
            # request and must never be applied to the persistent ledger.
            if saved:plan=DirectorOutput.model_validate(saved).model_dump()
            else:
                # Older proposals used persistent IDs directly. Their known
                # receipts remain reusable only when no local wire ID occurs.
                if re.search(r'\bE\d{3}\b',result['text']):continue
                plan=parse_json(result['text'],DirectorOutput)
        except (OSError,ValueError,KeyError,TypeError,Problem):continue
        return {'plan':plan,'checkpointId':uid(),'reusedDirectorTaskId':old.id}
    return None


def reusable_narrator(db,task,previous_task_id):
    """Reuse complete prose only when its following review was never sent."""
    if not previous_task_id:return None
    old=db.get(Task,previous_task_id)
    if not old or old.owner_id!=task.owner_id or old.error_code!='PROVIDER_CONNECTION_FAILED' or old.provider_state!='not_sent':return None
    if old.checkpoint.get('authorPrompts',[])!=task.checkpoint.get('authorPrompts',[]):return None
    frozen=old.checkpoint.get('executionPrompts',{}).get('world_narrator')
    if not frozen or frozen!=task.checkpoint.get('executionPrompts',{}).get('world_narrator'):return None
    from ..models import UsageRecord
    review=db.scalar(select(UsageRecord).where(UsageRecord.task_id==old.id,UsageRecord.operation=='validation:world:1'))
    if not review or review.status!='not_sent':return None
    call=old.checkpoint.get('calls',{}).get('generation:scene:1')
    if not call:return None
    try:
        saved=json.loads(private_path(old.owner_id,f"journal-{old.id}-{call['callId']}.json").read_text('utf8'))
        result=saved['result']
        if saved['hash']!=digest(json.dumps(result,ensure_ascii=False,sort_keys=True)) or result.get('finishReason')!='stop' or not result.get('text'):return None
    except (OSError,ValueError,KeyError,TypeError):return None
    return result
