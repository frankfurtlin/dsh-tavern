"""Reuse only settled, same-image blind reads during automatic evidence repair.

An explicit independent review always performs fresh observations. A failed or
unknown original operation never supplies this cache. Coordinates, crops and
physical body proofs remain independently measured by the usual validators.
"""
from copy import deepcopy
import json
from sqlalchemy import select
from ..models import Task,UsageRecord
from ..domain import digest
from ..storage import private_path


def preflight(db,task,row,context):
    ident=task.checkpoint.get('reuseInspectionTaskId')
    if not ident or row.descriptor.get('inspectionReceiptVersion')!=1:return {}
    prior=db.get(Task,ident)
    if (not prior or prior.id==task.id or prior.owner_id!=task.owner_id
            or prior.task_type!='world_scene_binding' or prior.state!='succeeded'
            or prior.provider_state!='completed' or prior.cancel_requested or prior.result.get('deleted')
            or prior.payload.get('sceneId')!=row.id or prior.payload.get('worldId')!=row.world_id
            or prior.payload.get('lineId')!=row.line_id
            or prior.checkpoint.get('bindingInspection')!=task.checkpoint.get('bindingInspection')
            or prior.checkpoint.get('executionProfile')!=task.checkpoint.get('executionProfile')
            or prior.checkpoint.get('executionPrompts')!=task.checkpoint.get('executionPrompts')):return {}
    saved=prior.checkpoint.get('completedSceneReads',{})
    if not isinstance(saved,dict):return {}
    if (saved.get('sceneId')!=row.id or saved.get('imageHash')!=row.image.get('sha256')
            or saved.get('context')!=context):return {}
    calls=prior.checkpoint.get('calls',{})
    if not {'analysis:scene-review',*[f'analysis:people-view:{i}' for i in range(4)]}<=set(calls):return {}
    binding=None
    for stage,call in calls.items():
        usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call.get('callId')))
        if (not usage or usage.owner_id!=task.owner_id or usage.task_id!=prior.id
                or usage.operation!=stage or usage.status!='completed'):return {}
        try:
            journal=json.loads(private_path(row.owner_id,f"journal-{prior.id}-{call['callId']}.json").read_text('utf8'))
            response=journal['result']
            if (journal.get('taskId')!=prior.id or journal.get('callId')!=call['callId']
                    or journal.get('hash')!=digest(json.dumps(response,ensure_ascii=False,sort_keys=True))
                    or response.get('notSent') or response.get('finishReason') not in ('stop','completed')):return {}
            if stage=='analysis:scene-binding':binding=deepcopy(response)
        except (OSError,ValueError,KeyError,TypeError):return {}
    from .binding import composition_from_result,inspection_scene
    observed={'sceneReview':{'version':1,'imageHash':saved['imageHash'],'observation':saved.get('review')},
              'peopleInventory':saved.get('inventory'),'characters':[]}
    verdict=composition_from_result(observed,row.descriptor.get('sceneCast',[]),inspection_scene(row))
    if verdict.get('verified') is not True or verdict.get('issues'):return {}
    inventory=saved['inventory']
    if binding is not None and row.descriptor.get('coordinateInspectionVersion')==1:
        from .coordinates import response,SYSTEM
        binding=response(binding,{'coordinateProtocol':{'system':SYSTEM,'frameWidth':512,'frameHeight':512}})
    return {'review':{'text':json.dumps(saved['review'])},
            **{entry['viewIndex']:deepcopy(entry) for entry in inventory['views']},
            **({'binding':binding} if binding is not None else {})}
