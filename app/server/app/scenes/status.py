"""Expose the failed stage independently from uncertain supplier billing."""
from sqlalchemy.orm import object_session
from ..models import Task
from ..provider_rejection import CATEGORIES,CODES


def rejected(task):
    diagnostic=task.checkpoint.get('providerFailure',{}) if task else {}
    status=diagnostic.get('httpStatus')
    return (task is not None and task.state=='failed' and diagnostic.get('reason')=='http_error'
            and type(status) is int and 400<=status<600)


def recovery(row):
    from .pipeline import is_parent,owned_stage
    db=object_session(row)
    child=owned_stage(db,row,row.image.get('pipelineStageId')) if db and is_parent(row) else row
    from .recovery import stopped_image_received
    restored=stopped_image_received(row,child)
    retained=bool(child and child.status=='ready' and child.image.get('sha256') and child.image.get('file')) or restored
    task=db.get(Task,child.image.get('bindingTaskId')) if db and child and child.image.get('bindingTaskId') else None
    if task and (task.owner_id!=row.owner_id or task.task_type!='world_scene_binding'
            or task.payload.get('sceneId')!=child.id or task.payload.get('worldId')!=row.world_id
            or task.payload.get('lineId')!=row.line_id):task=None
    diagnostic=task.checkpoint.get('providerFailure',{}) if task else {}
    result={'stage':'inspection' if retained else 'generation','imageRetained':retained,
        'canInspect':bool(retained and (not task or task.state in ('failed','cancelled','succeeded'))
                          and (restored or child.image.get('anchorStatus')!='complete'))}
    if db and is_parent(row) and row.status in ('failed','unknown'):
        from .retained import choices
        saved=choices(db,row)
        if saved and (not task or task.state in ('failed','cancelled','succeeded')):
            result.update(stage='inspection',imageRetained=True,canInspect=True,savedImages=saved)
    if db and result['canInspect'] and 'savedImages' not in result:
        from .retained import choices
        result['savedImages']=choices(db,row)
    if task:
        from .cooldown import remaining
        result.update(remaining(task))
        result['usageState']=task.usage_state
        result['responseRejected']=rejected(task)
        status=diagnostic.get('httpStatus')
        if type(status) is int and 100<=status<=599:result['httpStatus']=status
        if diagnostic.get('rejectionCategory') in CATEGORIES:result['rejectionCategory']=diagnostic['rejectionCategory']
        if diagnostic.get('rejectionCode') in CODES:result['rejectionCode']=diagnostic['rejectionCode']
    return result
