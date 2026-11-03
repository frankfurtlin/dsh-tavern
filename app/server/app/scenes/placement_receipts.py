"""Reuse settled support observations only on identical owned scene pixels."""
from copy import deepcopy
from hashlib import sha256
import json
from sqlalchemy import select
from ..models import Task,UsageRecord
from ..domain import canonical,digest
from ..storage import private_path


def record(task_id,part,proof,stage,camera):
    return {'taskId':str(task_id),'stage':stage,'part':deepcopy(part),
        'partHash':digest(canonical(part)),'cameraHash':sha256(camera).hexdigest(),
        'observationHash':digest(proof.get('text',''))}


def restore(db,task,row,context,cameras):
    """Explicit independent reviews have no reusePlacementTaskId and stay fresh."""
    ident=task.checkpoint.get('reusePlacementTaskId')
    if not ident or row.owner_id!=task.owner_id or row.descriptor.get('inspectionReceiptVersion')!=1:return [],[]
    sources={}
    def source(source_id):
        if not isinstance(source_id,str) or not source_id:return None
        if source_id in sources:return sources[source_id]
        sources[source_id]=None
        prior=db.get(Task,source_id)
        if (not prior or prior.id==task.id or prior.owner_id!=task.owner_id
                or prior.task_type!='world_scene_binding' or prior.state!='succeeded'
                or prior.provider_state!='completed' or prior.cancel_requested or prior.result.get('deleted')
                or prior.payload.get('sceneId')!=row.id or prior.payload.get('worldId')!=row.world_id
                or prior.payload.get('lineId')!=row.line_id
                or prior.checkpoint.get('bindingInspection')!=task.checkpoint.get('bindingInspection')
                or prior.checkpoint.get('executionProfile')!=task.checkpoint.get('executionProfile')
                or prior.checkpoint.get('executionPrompts')!=task.checkpoint.get('executionPrompts')):return None
        results={}
        for stage,call in prior.checkpoint.get('calls',{}).items():
            if not isinstance(call,dict):return None
            usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call.get('callId')))
            if (not usage or usage.owner_id!=task.owner_id or usage.task_id!=prior.id
                    or usage.operation!=stage or usage.status!='completed'):return None
            try:
                journal=json.loads(private_path(task.owner_id,f"journal-{prior.id}-{call['callId']}.json").read_text('utf8'))
                result=journal['result']
                if (not isinstance(result,dict) or journal.get('taskId')!=prior.id or journal.get('callId')!=call['callId']
                        or journal.get('hash')!=digest(json.dumps(result,ensure_ascii=False,sort_keys=True))
                        or result.get('notSent') or result.get('finishReason') not in ('stop','completed')):return None
            except (OSError,ValueError,KeyError,TypeError):return None
            results[stage]=result
        sources[source_id]=(prior,results);return sources[source_id]
    selected=source(ident)
    if not selected:return [],[]
    snapshot=selected[0].checkpoint.get('completedPlacementReads',{})
    if (not isinstance(snapshot,dict) or snapshot.get('version')!=1 or snapshot.get('sceneId')!=row.id
            or snapshot.get('imageHash')!=row.image.get('sha256') or snapshot.get('context')!=context):return [],[]
    if not isinstance(snapshot.get('proofs'),list):return [],[]
    from .supports import valid
    from .pipeline import validate_placements
    from .placements import readable_slot
    cast={p['entityId']:p for p in row.descriptor.get('futureCast',[])}
    found={};records=[]
    for saved in snapshot['proofs'][:64]:
        if not isinstance(saved,dict):continue
        part=saved.get('part');stage=saved.get('stage')
        if not isinstance(part,dict) or not isinstance(stage,str) or not stage.startswith(('analysis:slot-check:','analysis:slot-detail:','analysis:initial-slot:')):continue
        entity=part.get('entityId');index=part.get('viewIndex')
        if (not isinstance(entity,str) or entity not in cast or entity in found or type(index) is not int or not 0<=index<len(cameras)
                or saved.get('partHash')!=digest(canonical(part))
                or saved.get('cameraHash')!=sha256(cameras[index]).hexdigest()):continue
        original=source(saved.get('taskId'))
        proof=original[1].get(stage) if original else None
        if (not proof or saved.get('observationHash')!=digest(proof.get('text',''))
                or not valid(proof,part.get('pose'))):continue
        if (not validate_placements({'placements':[part]},[cast[entity]])
                or not readable_slot(part,row.descriptor.get('readablePlacementVersion',1))):continue
        found[entity]=deepcopy(part);records.append(deepcopy(saved))
    return list(found.values()),records
