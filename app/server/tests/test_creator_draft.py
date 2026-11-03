from copy import deepcopy
from app.db import SessionLocal
from app.world.models import World
from app.models import Task
from sqlalchemy import select, func

API='/api/world/v1'


def test_draft_save_retry_preview_data_and_publish_boundary(client,reader):
    response=client.post(API+'/scripts/samples/blue-carbuncle/prepared',json={},headers={'Idempotency-Key':'private-creator-fixture'})
    assert response.status_code==201,response.text
    identity=response.json()['worldId']
    path=API+'/worlds/'+identity+'/script'
    initial=client.get(path).json()
    draft=deepcopy(initial['value']);draft['opening']+='\n测试校对注记。'
    body={'expectedVersion':initial['worldVersion'],'value':draft}
    with SessionLocal() as db:
        before=deepcopy(db.get(World,identity).definition)
        calls=db.scalar(select(func.count()).select_from(Task))
    saved=client.put(path+'/draft',json=body,headers={'Idempotency-Key':'creator-save-once'})
    assert saved.status_code==200,saved.text
    assert client.put(path+'/draft',json=body,headers={'Idempotency-Key':'creator-save-once'}).json()==saved.json()
    assert client.get(path).json()['value']['opening']==draft['opening']
    assert client.put(path+'/draft',json=body,headers={'Idempotency-Key':'creator-save-stale'}).status_code==409
    with SessionLocal() as db:
        assert db.get(World,identity).definition==before
        assert db.scalar(select(func.count()).select_from(Task))==calls
    current=client.get(path).json()
    published=client.post(path+'/publish',json={'expectedVersion':current['worldVersion'],'value':draft},headers={'Idempotency-Key':'creator-publish-test'})
    assert published.status_code==200,published.text
    assert client.put(path+'/draft',json={'expectedVersion':current['worldVersion']+1,'value':draft},headers={'Idempotency-Key':'creator-save-published'}).status_code==409
