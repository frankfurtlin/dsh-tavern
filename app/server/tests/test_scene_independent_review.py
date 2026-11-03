"""An explicit new inspection may reuse pixels but cannot resume unknown calls."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task,User
from app.world.jobs import create_job
from app.scenes.models import SceneImage
from app.scenes.worker import process_one
from .test_scenes import scene_world,request,generated,API


def test_explicit_real_inventory_upgrade_reuses_a_completed_legacy_image_once(client,scene_world,profile,monkeypatch):
    from app.config import settings
    w=scene_world;scene,original,image=failed_review(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,scene['id']);row.image={**row.image,'anchorStatus':'complete'}
        row.descriptor={**row.descriptor,'renderPipeline':1}
        db.get(Task,original).provider_state='completed';db.commit()
    monkeypatch.setattr(settings,'ai_mode','real')
    body={'sceneId':scene['id'],'expectedCheckpointId':w['cp'],'independent':True}
    response=client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'legacy-inventory-upgrade'})
    assert response.status_code==202,response.text
    with SessionLocal() as db:
        row=db.get(SceneImage,scene['id']);task=db.get(Task,response.json()['inspectionTaskId'])
        assert row.image['sha256']==image['sha256'] and row.descriptor['peopleInventoryVersion']==1
        assert 'world_scene_people_inventory' in task.checkpoint['executionPrompts']
        assert db.get(Task,original).state=='failed' and db.get(Task,original).provider_state=='completed'
        task.state='succeeded';task.provider_state='completed';row.image={**row.image,'anchorStatus':'complete'};db.commit()
    duplicate=client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'no-repeat-upgrade'})
    assert duplicate.status_code==409 and duplicate.json()['code']=='SCENE_INSPECTION_COMPLETE'


def failed_review(client,w):
    scene=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        row=db.get(SceneImage,scene['id'])
        task=create_job(db,w['owner'],'world_scene_binding',{'worldId':w['world'],'lineId':w['line'],'sceneId':row.id},['analysis'],max_calls=1)
        task.state='failed';task.provider_state='unknown';task.error_code='PROVIDER_FAILED'
        row.image={**row.image,'bindingTaskId':task.id,'anchorStatus':'unavailable'}
        identity=task.id;pixels=deepcopy(row.image);db.commit()
    return scene,identity,pixels


def test_explicit_inspection_keeps_original_unknown_and_image_with_idempotency(client,scene_world,profile):
    w=scene_world;scene,original,image=failed_review(client,w)
    body={'sceneId':scene['id'],'expectedCheckpointId':w['cp'],'independent':True}
    with patch('app.scenes.worker.generate',side_effect=AssertionError('No image generation from inspection endpoint')):
        response=client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'new-independent'})
    assert response.status_code==202,response.text
    result=response.json();assert result['imageRegenerated'] is False and result['inspectionTaskId']!=original
    assert client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'new-independent'}).json()==result
    assert client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'while-pending'}).status_code==409
    with SessionLocal() as db:
        old=db.get(Task,original);new=db.get(Task,result['inspectionTaskId']);row=db.get(SceneImage,scene['id'])
        assert old.state=='failed' and old.provider_state=='unknown'
        assert new.state=='queued' and new.payload['purpose']=='explicit-independent-image-review'
        assert 'inspectionBaseReceipt' not in new.checkpoint
        assert row.image['sha256']==image['sha256'] and row.request_id==scene.get('requestId') if scene.get('requestId') else row.image['sha256']==image['sha256']
        assert db.scalar(select(func.count()).select_from(SceneImage))==1
        assert row.image['independentInspectionHistory'][0]['taskId']==original


@pytest.mark.parametrize('change',['not-explicit','wrong-checkpoint','foreign-scene'])
def test_independent_review_requires_explicit_current_owned_picture(client,scene_world,profile,change):
    w=scene_world;scene,original,image=failed_review(client,w)
    body={'sceneId':scene['id'],'expectedCheckpointId':w['cp'],'independent':True}
    if change=='not-explicit':body['independent']=False
    elif change=='wrong-checkpoint':body['expectedCheckpointId']='0'*32
    else:body['sceneId']='0'*32
    response=client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'invalid-inspection'})
    assert response.status_code in (404,409,422),response.text
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(Task))==1
