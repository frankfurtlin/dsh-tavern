"""Carry blind observations only across identical, owned camera pixels.

Every reused view names its ORIGINAL metered task and camera hash. No new usage
or supplier response is fabricated. Whole-scene review and changed views remain
fresh, and explicit independent reviews never use this path.
"""
from copy import deepcopy
from hashlib import sha256
import json
from sqlalchemy import select
from ..models import Task,UsageRecord
from ..domain import digest
from ..storage import private_path
from ..errors import Problem


def settled_results(db,prior,owner):
    results={}
    calls=prior.checkpoint.get('calls',{})
    if not isinstance(calls,dict) or not calls:return None
    for stage,call in calls.items():
        if not isinstance(call,dict):return None
        usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call.get('callId')))
        if (not usage or usage.owner_id!=owner or usage.task_id!=prior.id
                or usage.operation!=stage or usage.status!='completed'):return None
        try:
            journal=json.loads(private_path(owner,f"journal-{prior.id}-{call['callId']}.json").read_text('utf8'))
            result=journal['result']
            if (not isinstance(result,dict) or journal.get('taskId')!=prior.id or journal.get('callId')!=call['callId']
                    or journal.get('hash')!=digest(json.dumps(result,ensure_ascii=False,sort_keys=True))
                    or result.get('notSent') or result.get('finishReason') not in ('stop','completed')):return None
        except (OSError,ValueError,KeyError,TypeError):return None
        results[stage]=result
    return results


def unchanged_views(db,task,row,cameras):
    from .models import SceneImage
    from ..world.models import WorldCheckpoint
    from .service import image_bytes
    from .binding import inspection_scene,inspection_context,inspection_image,inspection_cameras,placement_narrative
    from .people_inventory import evaluate,candidate_boxes
    if (row.descriptor.get('inventoryContinuityVersion')!=1 or row.owner_id!=task.owner_id
            or row.descriptor.get('renderStage') not in ('actor','environment-edit')
            or not row.descriptor.get('pipelineParentId')
            or task.payload.get('purpose')=='explicit-independent-image-review'
            or task.payload.get('sceneId')!=row.id or task.payload.get('worldId')!=row.world_id
            or task.payload.get('lineId')!=row.line_id
            or not task.checkpoint.get('executionProfile') or not task.checkpoint.get('executionPrompts')):return {}
    current=inspection_scene(row)
    people_only={'sceneImageHash','headInspectionVersion','bodyInspectionVersion','identityInspectionVersion',
        'personInspectionVersion','headCameraSearchVersion','headCropPixelVersion'}
    contract=lambda scene:{k:v for k,v in scene.items() if k not in people_only}
    checkpoint=db.get(WorldCheckpoint,row.checkpoint_id)
    if (not checkpoint or checkpoint.owner_id!=row.owner_id or checkpoint.world_id!=row.world_id):return {}
    narrative=placement_narrative(checkpoint)
    ident=row.descriptor.get('inputSceneId');seen={row.id};found={}
    # Walk actual owned stage ancestry, not a library-wide image search. A
    # direct original observation can outlive several unrelated local edits.
    for _ in range(24):
        if not ident or ident in seen:break
        seen.add(ident);source=db.get(SceneImage,ident)
        if (not source or any(getattr(source,k)!=getattr(row,k) for k in
                ('owner_id','world_id','line_id','checkpoint_id','profile_id','profile_version'))
                or source.descriptor.get('pipelineParentId')!=row.descriptor['pipelineParentId']
                or source.status!='ready' or source.image.get('anchorStatus')!='complete'
                or source.image.get('bindingImageHash')!=source.image.get('sha256')):break
        ident=source.descriptor.get('inputSceneId')
        if contract(inspection_scene(source))!=contract(current):continue
        prior=db.get(Task,source.image.get('bindingTaskId'))
        if (not prior or prior.owner_id!=task.owner_id or prior.task_type!='world_scene_binding'
                or prior.state!='succeeded' or prior.provider_state!='completed' or prior.cancel_requested
                or prior.result.get('deleted') or prior.payload.get('sceneId')!=source.id
                or prior.payload.get('worldId')!=row.world_id or prior.payload.get('lineId')!=row.line_id
                or prior.checkpoint.get('executionProfile')!=task.checkpoint.get('executionProfile')
                or prior.checkpoint.get('executionPrompts')!=task.checkpoint.get('executionPrompts')):continue
        saved=prior.checkpoint.get('completedSceneReads',{})
        frozen=prior.checkpoint.get('bindingInspection',{})
        from .grounding import inspection_targets,VERSION
        from .service import scene_view
        targets=inspection_targets(scene_view(checkpoint,row.line_id),independent_interior=source.descriptor.get('exitInspectionVersion')==1)
        context=inspection_context(source,targets,narrative=narrative)
        if (not isinstance(saved,dict) or saved.get('sceneId')!=source.id
                or saved.get('imageHash')!=source.image.get('sha256') or saved.get('context')!=context
                or frozen!={'sceneId':source.id,'imageHash':source.image.get('sha256'),'context':context,'version':VERSION}):continue
        inventory=saved.get('inventory')
        if not evaluate(inventory,source.image.get('sha256'))['verified']:continue
        results=settled_results(db,prior,task.owner_id)
        if results is None:continue
        try:
            pixels,_=inspection_image(image_bytes(source));old=inspection_cameras(source,pixels)
        except (Problem,OSError,ValueError):
            # An unavailable encrypted source is not permission to infer its pixels.
            continue
        for entry in inventory['views']:
            index=entry['viewIndex'];stage='analysis:people-view:'+str(index)
            if index in found or index>=len(cameras) or old[index]!=cameras[index] or stage not in results:continue
            try:
                if json.loads(results[stage]['text'])!=entry['observation']:continue
                detail=entry.get('clarification')
                if detail:
                    prefix='analysis:people-conflict:' if detail.get('reason')=='independent-scene-count-conflict-v1' else 'analysis:people-check:'
                    answer=results.get(prefix+str(index))
                    if (not answer or detail.get('candidateRegions')!=candidate_boxes(entry['observation'])
                            or json.loads(answer['text'])!=detail.get('observation')):continue
            except (ValueError,KeyError,TypeError):continue
            found[index]={**deepcopy(entry),'continuity':{'version':1,'sourceSceneId':str(source.id),
                'sourceTaskId':str(prior.id),'sourceImageHash':source.image['sha256'],
                'targetImageHash':row.image.get('sha256'),'cameraSha256':sha256(cameras[index]).hexdigest()}}
        if len(found)==4:break
    return found
