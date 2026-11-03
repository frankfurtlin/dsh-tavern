"""Long supplier backoff must not reserve a call or strand a world worker."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch
import asyncio,json,time
import httpx,pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task,ModelProfile,UsageRecord,OperationPlan,Consent
from app.foundation.models import Hold,Ledger
from app.providers import encrypt_key
from app.config import settings
from app.scenes import admission,cooldown
from app.errors import Problem
from app.scenes.models import SceneImage
from app.world.models import WorldCheckpoint,WorldLine
from .test_scenes import scene_world,API
from .test_scene_failure_recovery import stopped


@pytest.fixture
def real_cooldown(monkeypatch):
    monkeypatch.setattr(settings,'ai_mode','real')
    monkeypatch.setattr(admission,'_cooldowns',{})
    monkeypatch.setattr(admission,'_next_start',0.0)
    yield
    assert not admission._waiting


def test_long_numeric_and_http_date_backoff_are_not_truncated_or_retained_as_raw_supplier_prose():
    from app.provider_rejection import diagnostics
    from email.utils import format_datetime
    future=datetime.now(timezone.utc)+timedelta(seconds=6512)
    for header in ('6512',format_datetime(future,usegmt=True)):
        result=asyncio.run(diagnostics(httpx.Response(429,text='PRIVATE-ERROR',headers={'retry-after':header})))
        assert 6510<=result['retryAfterSeconds']<=6512
        assert 'PRIVATE' not in json.dumps(result)
    for header in ('9'*7000,'-1','private-token','not a date'):
        result=asyncio.run(diagnostics(httpx.Response(429,text='',headers={'retry-after':header}),credential='private-token'))
        assert 'retryAfterSeconds' not in result


def test_same_credential_shares_cooldown_across_profiles_but_different_credential_is_independent(real_cooldown):
    def profile(key):return SimpleNamespace(base_url='https://gateway.example/v1',key_cipher=encrypt_key(key))
    a,b,c=profile('synthetic-shared-key'),profile('synthetic-shared-key'),profile('synthetic-other-key')
    assert a.key_cipher!=b.key_cipher and cooldown.scope_of(a)==cooldown.scope_of(b)
    error=Problem('PROVIDER_FAILED','busy',502,{'httpStatus':429,'retryAfterSeconds':6512})
    metadata=cooldown.failure_metadata(error,a);error.details.update(metadata)
    admission.observe_failure(error,cooldown.scope_of(a))
    started=time.monotonic()
    with pytest.raises(Problem) as failure:admission.wait_for_start(lambda:None,cooldown.scope_of(b))
    assert failure.value.code=='SCENE_VISION_COOLDOWN' and failure.value.details['requestSent'] is False
    assert failure.value.details['retryAfterSeconds']>6500 and time.monotonic()-started<1
    admission.wait_for_start(lambda:None,cooldown.scope_of(c))
    assert 'synthetic-' not in json.dumps(metadata)


@pytest.mark.parametrize('later_timeout',[False,True])
def test_known_cooldown_survives_worker_memory_reset_and_rejects_recovery_without_new_task_usage_or_story_write(client,scene_world,profile,real_cooldown,later_timeout):
    w=scene_world
    # Build a real retained-image failure using the normal isolated DB fixtures.
    with patch.object(settings,'ai_mode','mock'):
        parent,child,task_id,old_checkpoint,pixels=stopped(client,w,{'reason':'http_error','httpStatus':429})
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);p.base_url='https://gateway.example/v1'
        metadata=cooldown.failure_metadata(Problem('PROVIDER_FAILED','busy',502,{'httpStatus':429,'retryAfterSeconds':6512}),p)
        task=db.get(Task,task_id)
        failure=({'reason':'timeout','stage':'analysis:sibling','errorCode':'PROVIDER_STATUS_UNKNOWN'} if later_timeout
                 else {**task.checkpoint['providerFailure'],**metadata})
        task.checkpoint={**task.checkpoint,'providerFailure':failure,'providerCooldown':metadata}
        db.commit();old_checkpoint=deepcopy(task.checkpoint)
        cp=db.get(WorldCheckpoint,w['cp']);canonical=deepcopy((cp.state,cp.definition,cp.memory,cp.text,cp.content_hash))
        tracked=(Task,UsageRecord,SceneImage,OperationPlan,Consent,Hold,Ledger)
        counts=tuple(db.scalar(select(func.count()).select_from(model)) for model in tracked)
    admission._cooldowns.clear()
    with patch('app.worker.call_provider',side_effect=AssertionError('No new paid call during cooldown')):
        result=client.post(API+w['root']+'/scene/inspect',json={'sceneId':parent,'expectedCheckpointId':w['cp'],'independent':True},headers={'Idempotency-Key':'cooldown-recover'})
        assert result.status_code==429,result.text
        assert result.json()['code']=='SCENE_VISION_COOLDOWN' and result.json()['details']['retryAfterSeconds']>6500
        fresh=client.post(API+w['root']+'/scene',json={'expectedCheckpointId':w['cp'],'regenerate':True},headers={'Idempotency-Key':'cooldown-regenerate'})
        assert fresh.status_code==429,fresh.text
    view=client.get(API+'/scenes/'+parent+'?lineId='+w['line']).json()
    assert view['recovery']['retryAfterSeconds']>6500 and view['recovery']['retryAt']==metadata['retryAt']
    assert 'cooldownScope' not in json.dumps(view['recovery'])
    with SessionLocal() as db:
        assert db.get(Task,task_id).checkpoint==old_checkpoint and db.get(Task,task_id).provider_state=='unknown'
        assert tuple(db.scalar(select(func.count()).select_from(model)) for model in tracked)==counts
        cp=db.get(WorldCheckpoint,w['cp'])
        assert (cp.state,cp.definition,cp.memory,cp.text,cp.content_hash)==canonical
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']
        from app.scenes.service import image_bytes
        assert image_bytes(db.get(SceneImage,child))==pixels


def test_admission_deferral_cannot_erase_an_actual_received_rejection_or_unknown_request(client,scene_world,profile,real_cooldown):
    with patch.object(settings,'ai_mode','mock'):
        parent,child,task_id,checkpoint,_=stopped(client,scene_world,{'reason':'timeout','requestSent':True,'stage':'analysis:original'})
    with SessionLocal() as db:
        task=db.get(Task,task_id);task.state='running';db.commit()
    details={'retryAt':(datetime.now(timezone.utc)+timedelta(seconds=6512)).isoformat()}
    cooldown.record_deferral(task_id,details)
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        assert task.checkpoint==checkpoint and task.provider_state=='unknown' and task.usage_state=='unknown'


def test_expired_retry_deadline_does_not_block_an_explicit_fresh_intent(real_cooldown):
    admission.remember('credential-scope',datetime.now(timezone.utc)-timedelta(seconds=1))
    admission.wait_for_start(lambda:None,'credential-scope')
    task=SimpleNamespace(checkpoint={'providerFailure':{'httpStatus':429,'retryAt':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}})
    assert cooldown.remaining(task)=={}


@pytest.mark.parametrize('superseded',[False,True])
def test_deferral_is_owned_current_image_metadata_and_never_becomes_a_paid_intent(client,scene_world,profile,real_cooldown,superseded):
    with patch.object(settings,'ai_mode','mock'):
        parent,child,task_id,_,_=stopped(client,scene_world,{})
    with SessionLocal() as db:
        task=db.get(Task,task_id);task.state='running';task.provider_state='not_started';task.usage_state='estimated'
        if superseded:
            image=db.get(SceneImage,child);image.image={**image.image,'bindingTaskId':'another-new-task'}
        cp=db.get(WorldCheckpoint,scene_world['cp']);before=deepcopy((cp.state,cp.definition,cp.memory,cp.text,cp.content_hash))
        usages=db.scalar(select(func.count()).select_from(UsageRecord));db.commit()
    details={'retryAt':(datetime.now(timezone.utc)+timedelta(seconds=6512)).isoformat(),'cooldownScope':'a'*64}
    if superseded:
        with pytest.raises(Problem) as failure:cooldown.record_deferral(task_id,details)
        assert failure.value.code=='TASK_CANCELLED'
    else:cooldown.record_deferral(task_id,details)
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        assert not task.checkpoint.get('calls') and db.scalar(select(func.count()).select_from(UsageRecord))==usages
        if superseded:assert not task.checkpoint.get('providerFailure')
        else:
            assert task.provider_state=='not_sent' and task.usage_state=='known'
            assert task.checkpoint['providerFailure']['requestSent'] is False
            assert cooldown.remaining(task)['retryAfterSeconds']>6500
        cp=db.get(WorldCheckpoint,scene_world['cp'])
        assert (cp.state,cp.definition,cp.memory,cp.text,cp.content_hash)==before


def test_worker_persists_received_backoff_without_replaying_an_original_uncertain_paid_call(client,reader,profile,monkeypatch):
    from app.providers import MockAdapter
    from app.worker import process
    from .test_regressions import queue_operation
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);p.base_url='https://gateway.example/v1';scope=cooldown.scope_of(p);db.commit()
    ident=queue_operation(client,profile,{'operation':'connection_test','profileId':profile['id']})
    calls=[]
    def reject(*args,**kwargs):
        calls.append(1)
        raise Problem('PROVIDER_FAILED','PRIVATE-DO-NOT-PERSIST',502,{'reason':'http_error','httpStatus':429,
            'responseReceived':True,'retryAfterSeconds':6512,'rejectionCategory':'rate_limit'})
    monkeypatch.setattr(MockAdapter,'generate',reject)
    process(ident);process(ident)
    assert calls==[1]
    with SessionLocal() as db:
        task=db.get(Task,ident);diagnostic=task.checkpoint['providerFailure']
        assert diagnostic['cooldownScope']==scope and diagnostic['retryAfterSeconds']==6512
        assert cooldown.remaining(task)['retryAfterSeconds']>6500
        assert task.provider_state=='unknown' and task.usage_state=='unknown'
        assert db.scalar(select(UsageRecord).where(UsageRecord.task_id==ident)).status=='intent'
        assert 'PRIVATE-DO-NOT-PERSIST' not in json.dumps(task.checkpoint)
