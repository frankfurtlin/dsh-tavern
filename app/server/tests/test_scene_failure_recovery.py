"""Actual DB/API regressions for retained pixels, billing uncertainty and story isolation."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task
from app.world.models import WorldCheckpoint,WorldLine
from app.world.jobs import create_job
from app.scenes.models import SceneImage
from app.scenes.worker import process_one
from app.scenes.service import image_bytes
from .test_scenes import scene_world,generated,API
from .test_scene_pipeline import start


def stopped(client,w,diagnostic):
    parent_id,child_id=start(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id)
        task=create_job(db,w['owner'],'world_scene_binding',{'worldId':w['world'],'lineId':w['line'],'sceneId':child.id},['analysis'],max_calls=1)
        task.state='failed';task.provider_state='unknown';task.usage_state='unknown';task.error_code='PROVIDER_FAILED'
        task.checkpoint={**task.checkpoint,'providerFailure':diagnostic}
        child.image={**child.image,'bindingTaskId':task.id,'anchorStatus':'unavailable'}
        parent.status='unknown';parent.error_code='PROVIDER_FAILED'
        result=(parent_id,child_id,task.id,deepcopy(task.checkpoint),image_bytes(child))
        db.commit();return result


@pytest.mark.parametrize('status',[400,429,503,None])
def test_saved_picture_reports_inspection_failure_separately_from_unknown_billing(client,scene_world,profile,status):
    w=scene_world
    diag={'stage':'analysis:scene-binding','reason':'http_error' if status else 'timeout',
        'httpStatus':status,'rejectionCategory':'model_unavailable','rejectionCode':'model_coming_soon',
        'unsafeMessage':'PRIVATE-DO-NOT-EXPOSE'}
    parent,child,task_id,checkpoint,pixels=stopped(client,w,diag)
    with patch('app.scenes.worker.generate') as gen:
        response=client.get(API+'/scenes/'+parent+'?lineId='+w['line']);assert response.status_code==200
        result=response.json();gen.assert_not_called()
    assert result['status']==('failed' if status else 'unknown')
    assert result['recovery']['stage']=='inspection' and result['recovery']['imageRetained']
    assert result['recovery']['canInspect'] and result['recovery']['usageState']=='unknown'
    assert result['recovery']['responseRejected']==bool(status)
    assert result['imageUrl'] is None and 'PRIVATE-DO-NOT-EXPOSE' not in response.text
    with SessionLocal() as db:
        assert db.get(Task,task_id).checkpoint==checkpoint
        assert db.get(Task,task_id).provider_state=='unknown'
        assert db.get(SceneImage,parent).status=='unknown'
        assert image_bytes(db.get(SceneImage,child))==pixels
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']


def test_retry_400_reuses_original_pixels_preserves_failed_calls_and_whole_checkpoint(client,scene_world,profile):
    w=scene_world
    parent,child,task_id,checkpoint,pixels=stopped(client,w,{'reason':'http_error','httpStatus':400})
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);before=deepcopy((cp.state,cp.definition,cp.memory,cp.text,cp.content_hash))
        count=db.scalar(select(func.count()).select_from(SceneImage))
    body={'sceneId':parent,'expectedCheckpointId':w['cp'],'independent':True}
    with patch('app.scenes.worker.generate') as gen:
        response=client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'recover-400'})
        assert response.status_code==202,response.text
        assert client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'recover-400'}).json()==response.json()
        assert client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'second-click'}).status_code==409
        gen.assert_not_called()
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp'])
        assert (cp.state,cp.definition,cp.memory,cp.text,cp.content_hash)==before
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']
        assert db.get(Task,task_id).checkpoint==checkpoint
        assert image_bytes(db.get(SceneImage,child))==pixels
        assert db.scalar(select(func.count()).select_from(SceneImage))==count
        assert db.get(SceneImage,parent).status=='queued'


def test_tested_vision_release_preserves_personal_choices_old_profiles_defaults_and_checkpoints(client,scene_world,profile):
    from app.models import ModelProfile,User,UserSettings
    from app.scenes.models import SceneProfile
    from app.foundation.models import Config,VisionSettings
    from app.foundation.visual_release import install
    w=scene_world
    with SessionLocal() as db:
        image=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==w['owner']))
        policy={'text':{'model_id':'original-text','key_cipher':'opaque-old-text'},'image':{'key_cipher':image.key_cipher,'daily_limit':0}}
        db.add(Config(key='platform-models',value=deepcopy(policy)))
        row=db.get(VisionSettings,w['owner']);row.platform_profile_id=profile['id'];row.managed=True
        stranger=User(username='vision_personal',password_hash='synthetic-not-a-secret');db.add(stranger);db.flush()
        own=ModelProfile(owner_id=stranger.id,name='personal',model_id='own',base_url='mock://deterministic',
            key_cipher=image.key_cipher,key_tail='',context_window=32768)
        db.add(own);db.flush();db.add(VisionSettings(owner_id=stranger.id,profile_id=own.id,managed=False));db.flush()
        original={p.id:tuple(getattr(p,c.name) for c in ModelProfile.__table__.columns) for p in db.scalars(select(ModelProfile))}
        cp=db.get(WorldCheckpoint,w['cp']);before=deepcopy((cp.state,cp.definition,cp.memory,cp.text,cp.content_hash))
        default=db.get(UserSettings,w['owner']).default_profile_id
        result=install(db);db.flush();count=db.scalar(select(func.count()).select_from(ModelProfile))
        assert result['managedSelectionsUpdated']==1 and result['personalSelectionsPreserved']==1
        assert install(db)==result and db.scalar(select(func.count()).select_from(ModelProfile))==count
        assert db.get(VisionSettings,stranger.id).profile_id==own.id
        assert db.get(VisionSettings,w['owner']).profile_id!=profile['id']
        assert db.get(Config,'platform-models').value==policy
        assert db.get(UserSettings,w['owner']).default_profile_id==default
        assert all(tuple(getattr(db.get(ModelProfile,id),c.name) for c in ModelProfile.__table__.columns)==values for id,values in original.items())
        assert (cp.state,cp.definition,cp.memory,cp.text,cp.content_hash)==before
        db.rollback()


@pytest.mark.parametrize('change',[None,'wrong-line','wrong-checkpoint','pending-source'])
def test_recovery_can_copy_a_same_context_saved_environment_without_regenerating_or_replaying(client,scene_world,profile,change):
    from app.models import uid
    from app.scenes.pipeline import stage
    w=scene_world;source,source_child,old_task,old_checkpoint,pixels=stopped(client,w,{'reason':'http_error','httpStatus':400})
    with SessionLocal() as db:
        old=db.get(SceneImage,source)
        target=SceneImage(id=uid(),owner_id=old.owner_id,world_id=old.world_id,line_id=old.line_id,
            checkpoint_id=old.checkpoint_id,profile_id=old.profile_id,profile_version=old.profile_version,
            scene_hash=old.scene_hash,generation=2,status='unknown',descriptor=deepcopy(old.descriptor),image={})
        db.add(target);db.flush();stage(db,target,deepcopy(db.get(SceneImage,source_child).descriptor),received=db.get(SceneImage,source_child),inspect_received=False)
        target_id=target.id
        if change=='wrong-line':
            other=WorldLine(owner_id=old.owner_id,world_id=old.world_id,name='Other line',head_checkpoint_id=w['cp'],status='active')
            db.add(other);db.flush();old.line_id=other.id
        if change=='wrong-checkpoint':
            current=db.get(WorldCheckpoint,w['cp'])
            other=WorldCheckpoint(owner_id=old.owner_id,world_id=old.world_id,line_id=old.line_id,sequence=2,
                title='Other checkpoint',text=current.text,state=deepcopy(current.state),definition=deepcopy(current.definition),
                memory={},content_hash=current.content_hash,kind='start')
            db.add(other);db.flush();old.checkpoint_id=other.id
        if change=='pending-source':old.status='queued'
        before_images=db.scalar(select(func.count()).select_from(SceneImage));before_tasks=db.scalar(select(func.count()).select_from(Task))
        db.commit()
    body={'sceneId':target_id,'expectedCheckpointId':w['cp'],'independent':True,'sourceSceneId':source}
    with patch('app.scenes.worker.generate') as gen:
        response=client.post(API+w['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'select-retained'})
        gen.assert_not_called()
    if change:assert response.status_code==409,response.text
    else:assert response.status_code==202,response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SceneImage))==before_images+int(change is None)
        assert db.scalar(select(func.count()).select_from(Task))==before_tasks+int(change is None)
        assert db.get(Task,old_task).checkpoint==old_checkpoint
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']
        if change is None:
            target=db.get(SceneImage,target_id);copy=db.get(SceneImage,target.image['pipelineStageId'])
            assert copy.id!=source_child and image_bytes(copy)==pixels
            assert copy.descriptor['retainedSourceSceneId']==source


def test_binding_prompt_uses_the_declared_camera_dimensions_and_requires_the_output_unit_tag():
    from app.scenes.recovery import inspection_prompts
    prompt=inspection_prompts()['world_scene_binding']['text']
    assert 'EXACTLY 512' not in prompt and '0 and 512' not in prompt
    assert '"coordinateSystem":"normalized-1000-xy-v1"' in prompt
    assert 'Standing OUTSIDE an inn' in prompt
