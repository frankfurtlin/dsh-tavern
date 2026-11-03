"""Atomically retain head progress beside concurrent metered call intents."""
from copy import deepcopy
from sqlalchemy import select
from ..db import SessionLocal
from ..models import Task
from ..errors import Problem
from ..worker import assert_task_active
from ..world.jobs import lock_task_world
from .models import SceneImage


def save(task_id,image_hash,result,measured,targets):
    from .binding import inspection_context
    with SessionLocal() as db:
        lock_task_world(db,task_id)
        # call_provider reserves/journals another person's request under this
        # same Task lock. A plain get + JSON replacement could erase its intent
        # while saving a received sibling's head, causing a later paid replay.
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update().execution_options(populate_existing=True))
        assert_task_active(db,task)
        picture=db.get(SceneImage,task.payload['sceneId'])
        if (not picture or picture.owner_id!=task.owner_id or picture.image.get('bindingTaskId')!=task.id
                or picture.image.get('sha256')!=image_hash):
            raise Problem('TASK_CANCELLED','校验画面已变化')
        task.checkpoint={**task.checkpoint,'headInspectionProgress':{'sceneId':picture.id,'imageHash':image_hash,
            'context':inspection_context(picture,targets),'result':deepcopy(result)}}
        picture.image={**picture.image,**measured,'anchorStatus':'queued'}
        db.commit()
