from fastapi import Depends, Query
from sqlalchemy import select, func
from ..db import get_db
from ..models import Task, TaskEvent, UsageRecord
from ..security import current_user
from ..usage_reporting import query, items, summary
from .api import router


@router.get('/usage-actions')
def usage_actions(offset:int=Query(0,ge=0),storyId:str|None=None,profileId:str|None=None,
                  user=Depends(current_user),db=Depends(get_db)):
    base=query(db,user.id,story_id=storyId,profile_id=profileId)
    ids=base.with_only_columns(UsageRecord.task_id).distinct()
    rows=list(db.scalars(select(Task).where(Task.owner_id==user.id,Task.id.in_(ids))
                         .order_by(Task.created_at.desc(),Task.id).offset(offset).limit(20)))
    result=[]
    for task in rows:
        statement=base.where(UsageRecord.task_id==task.id)
        calls=list(db.scalars(statement.order_by(UsageRecord.created_at,UsageRecord.id)))
        formatted=items(db,calls)
        first_call=min((r.created_at for r in calls),default=None)
        first_text=db.scalar(select(func.min(TaskEvent.created_at)).where(TaskEvent.task_id==task.id,TaskEvent.type=='text_delta'))
        finished=db.scalar(select(func.max(TaskEvent.created_at)).where(TaskEvent.task_id==task.id)) if task.state in ('succeeded','failed','cancelled') else None
        duration=lambda stamp:max(0,int((stamp-task.created_at).total_seconds()*1000)) if stamp else None
        result.append({'id':task.id,'kind':task.task_type,'state':task.state,'createdAt':task.created_at.isoformat(),
                       'story':formatted[0]['story'] if formatted else None,'summary':summary(db,statement),'calls':formatted,
                       'timings':{'beforeFirstCallMs':duration(first_call),'firstTextMs':duration(first_text),'totalMs':duration(finished)}})
    return {'items':result,'offset':offset}
