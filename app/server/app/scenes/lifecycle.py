"""Scene-only progress and cancellation; never write a story checkpoint."""
from datetime import datetime,timezone
from sqlalchemy import select
from sqlalchemy.orm import object_session
from ..models import Task,now
from ..domain import owned
from ..errors import Problem
from .models import SceneImage


def progress(row):
    db=object_session(row) if hasattr(row,'_sa_instance_state') else None
    child=db.get(SceneImage,row.image.get('pipelineStageId')) if db and row.image.get('pipelineStageId') else row
    if db and child and (child.owner_id!=row.owner_id or child.world_id!=row.world_id):child=None
    started=getattr(row,'created_at',None)
    resumed=row.image.get('lastStartedAt')
    if isinstance(resumed,str):
        try:started=datetime.fromisoformat(resumed)
        except ValueError:pass
    if started and started.tzinfo is None:started=started.replace(tzinfo=timezone.utc)
    elapsed=max(0,int((now()-started).total_seconds())) if started else 0
    active=row.status in ('queued','running','saving') or row.status=='ready' and row.image.get('anchorStatus') in ('queued','repairing')
    phase='stopped' if getattr(row,'error_code',None)=='SCENE_STOPPED' else 'complete' if row.status=='ready' and row.image.get('anchorStatus')=='complete' else 'failed' if row.status in ('failed','unknown') else None
    if phase is None:
        actor=child and child.descriptor.get('renderStage')=='actor'
        checking=child and child.status=='ready'
        phase=('actor' if actor else 'environment')+('-review' if checking else '-image')
    labels={'environment-image':'绘制环境','environment-review':'确认环境与落点','actor-image':'绘制人物','actor-review':'确认人物与交互','complete':'场景已完成','stopped':'已停止场景生成','failed':'场景尚未完成'}
    task=db.get(Task,child.image.get('bindingTaskId')) if db and child and child.image.get('bindingTaskId') else None
    calls=len(task.checkpoint.get('calls',{})) if task and task.owner_id==row.owner_id else 0
    return {'startedAt':started.isoformat() if started else None,'elapsedSeconds':elapsed,'phase':phase,
        'label':'等待核验（队列中）' if task and task.state=='queued' and active else labels[phase],'canStop':bool(active),'slow':bool(active and elapsed>=60),
        'inspectionCalls':calls,'inspectionCallLimit':row.descriptor.get('inspectionMaxCalls')}


def stop_scene(db,owner,world_id,line_id,scene_id,checkpoint_id):
    from ..world.service import require_line
    from .cache import public_scene
    _,line=require_line(db,owner,world_id,line_id,lock=True)
    row=owned(db,SceneImage,scene_id,owner,lock=True)
    if row.world_id!=world_id or row.line_id!=line_id or not public_scene(row):
        raise Problem('SCENE_NOT_FOUND','场景不存在',404)
    if line.head_checkpoint_id!=checkpoint_id or row.checkpoint_id!=checkpoint_id:
        raise Problem('SCENE_CONTEXT_CHANGED','当前场景已变化',409)
    if row.error_code=='SCENE_STOPPED':return row
    if row.status=='ready' and row.image.get('anchorStatus')=='complete':return row
    # Lock the world before jobs, matching the existing scene worker order.
    children=list(db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,
        SceneImage.world_id==world_id,SceneImage.line_id==line_id,
        SceneImage.descriptor['pipelineParentId'].astext==row.id)))
    ids=[row.id,*[child.id for child in children]]
    tasks=db.scalars(select(Task).where(Task.owner_id==owner,Task.task_type=='world_scene_binding',
        Task.payload['sceneId'].astext.in_(ids)).order_by(Task.id).with_for_update())
    for task in tasks:
        if task.state not in ('succeeded','failed','cancelled'):task.cancel_requested=True
    for child in children:
        if child.status=='queued':child.status='failed';child.error_code='SCENE_STOPPED'
    row.status='failed';row.error_code='SCENE_STOPPED'
    row.image={**row.image,'pipelinePhase':'stopped','stoppedAt':now().isoformat()}
    return row
