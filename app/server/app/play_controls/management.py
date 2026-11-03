from sqlalchemy import select, delete
from ..models import Task, TaskEvent, Idempotency, OperationPlan
from .models import WorldChatSession, PlayControl


def remove(db, owner, session):
    identity=session.id
    private={'chatSnapshot','messages','message','conversationMemory'}
    for task in db.scalars(select(Task).where(Task.owner_id==owner,Task.payload['chatId'].astext==identity).with_for_update()):
        task.payload={k:v for k,v in task.payload.items() if k not in private}
        task.checkpoint={'calls':{k:{'callId':v['callId']} for k,v in task.checkpoint.get('calls',{}).items() if 'callId' in v},'worldPrivateCleanupPending':True}
        task.result={'worldId':session.world_id,'deleted':True};task.cancel_requested=True
        db.execute(delete(TaskEvent).where(TaskEvent.task_id==task.id))
    for plan in db.scalars(select(OperationPlan).where(OperationPlan.owner_id==owner,OperationPlan.payload['chatId'].astext==identity)):
        plan.payload={k:v for k,v in plan.payload.items() if k not in private}
    for receipt in db.scalars(select(Idempotency).where(Idempotency.owner_id==owner,Idempotency.scope.like('play-controls:%'))):
        result=receipt.result or {}
        if identity in (result.get('id'),result.get('chatId'),(result.get('source') or {}).get('id')):
            receipt.result={'worldId':session.world_id,'chatId':identity,'deleted':True}
    db.execute(delete(PlayControl).where(PlayControl.owner_id==owner,PlayControl.scope_kind=='chat',PlayControl.scope_id==identity))
    db.delete(session)
