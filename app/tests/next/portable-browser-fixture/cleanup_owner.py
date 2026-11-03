"""Execute only a verified own account-deletion task via the real worker."""
import sys,json
import fixture_server as guard
from app.db import SessionLocal
from app.models import User,Task
from sqlalchemy import select
from app.worker import claim,process
owner,task_id=sys.argv[1:3]
assert len(owner)==len(task_id)==32
with SessionLocal() as db:
    user=db.get(User,owner);task=db.get(Task,task_id)
    assert user and user.username.startswith('pbu_') and user.status=='deleting'
    assert task and task.owner_id==owner and task.task_type=='delete_account' and task.state=='queued'
    pending=list(db.scalars(select(Task).where((Task.state.notin_(['succeeded','failed','cancelled']))|((Task.state=='failed')&(Task.persistence_state=='recoverable')))))
    assert len(pending)==1 and pending[0].id==task_id
claimed=claim('portable-browser-owned-cleanup')
assert claimed and str(claimed)==task_id
process(claimed)
with SessionLocal() as db:
    assert db.get(User,owner) is None
    assert not list(db.scalars(select(Task).where(Task.owner_id==owner)))
assert not (guard.settings.storage_path/owner).exists()
print(json.dumps({'ownerId':owner,'taskId':task_id,'actualWorkerCleanup':True,'ownerRowsGone':True,'ownerTasks':0,'providerAttempts':json.loads(guard.ATTEMPTS.read_text())['attempts']}))
