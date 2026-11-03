"""Ten independently owned API submissions, guarded mock supplier only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json, os
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task, User, now
from app.config import settings
from app.world.worker import claim,run_claimed
from .test_world_security import isolated_storage_and_mock_only
from .test_authoring_publications import other,post


def test_ten_beta_users_drain_without_exceeding_two_global_slots(client,reader,monkeypatch):
    monkeypatch.setattr(settings,'world_task_concurrency',2)
    tasks=[]
    for i in range(10):
        player_api=TestClient(app,client=('192.0.2.'+str(i+1),50000+i))
        other(player_api,'queue_beta_'+str(i))
        configured=player_api.post('/api/reader/v1/model-profiles',json={'name':'fixture-only','baseUrl':'mock://deterministic','modelId':'fixture-v1','key':'TEST-KEY-NOT-REAL','contextWindow':32768,'callBudget':100,'inputBudget':10000000,'outputBudget':1000000})
        assert configured.status_code==201,configured.text
        w=post(player_api,'/api/world/v1/worlds',{'templateId':'original-mystery-v1'},'create-'+str(i))
        people=player_api.get('/api/world/v1/worlds/'+w['worldId']).json()['characters']
        tasks.append(post(player_api,'/api/world/v1/worlds/'+w['worldId']+'/begin',{'characterId':people[0]['id']},'start-'+str(i))['taskId'])
    peak=0
    with ThreadPoolExecutor(max_workers=8) as claims,ThreadPoolExecutor(max_workers=2) as workers:
        for _ in range(5):
            batch=[r for r in claims.map(claim,['competing-executor']*8) if r]
            assert len(batch)==2
            with SessionLocal() as db:
                active=db.scalar(select(func.count()).select_from(Task).where(Task.id.in_(tasks),Task.state=='preparing'))
                peak=max(peak,active);assert active==2
            list(workers.map(run_claimed,batch))
    with SessionLocal() as db:
        states=list(db.scalars(select(Task.state).where(Task.id.in_(tasks))))
        assert states==['succeeded']*10
    assert claim('empty') is None
    result={'accounts':10,'completedTasks':10,'observedPeakClaimed':peak,'configuredCapacity':2,'supplierMode':'guarded mock','realSupplierCapacityProven':False}
    if os.environ.get('TEST_ARTIFACTS_DIR'):
        (Path(os.environ['TEST_ARTIFACTS_DIR'])/'ten-user-queue.json').write_text(json.dumps(result,indent=2),'utf-8')


def test_busy_owner_cannot_hide_other_owners_behind_scan_limit(client,reader):
    owner=reader['user']['id'];other(client,'unblocked_beta')
    with SessionLocal() as db:
        other_id=db.scalar(select(User.id).where(User.username=='unblocked_beta'))
        db.add(Task(owner_id=owner,task_type='world_turn',state='preparing',lease_expires_at=now()+timedelta(seconds=30),lease_owner='busy',payload={},checkpoint={},result={}))
        db.add_all([Task(owner_id=owner,task_type='world_turn',state='queued',payload={},checkpoint={},result={},created_at=now()-timedelta(minutes=10)) for _ in range(25)])
        task=Task(owner_id=other_id,task_type='world_turn',state='queued',payload={},checkpoint={},result={});db.add(task);db.commit();identity=task.id
    assert str(claim('free-executor'))==identity
