from copy import deepcopy
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import ModelProfile,UserSettings,User
from app.foundation.models import Account,Config,VisionSettings
from app.foundation.vision import selected,provision
from app.world.jobs import create_job
from app.errors import Problem
from .test_authoring_publications import other,login,post

BASE='/api/reader/v1';API=BASE+'/product'

def setup_policy(client,reader,profile):
    with SessionLocal() as db:
        account=db.get(Account,reader['user']['id'])
        if account:account.role='ADMIN'
        else:db.add(Account(owner_id=reader['user']['id'],role='ADMIN'))
        db.commit()
    response=client.post(BASE+'/model-profiles',json={'name':'vision-only','baseUrl':'mock://deterministic','modelId':'vision-fixture','key':'VISION-TEST-NEVER-REAL','contextWindow':131072,'callBudget':100,'inputBudget':10000000,'outputBudget':1000000});assert response.status_code==201
    vision=response.json()
    response=client.put(API+'/admin/vision-model',json={'expectedVersion':0,'profileId':vision['id']});assert response.status_code==200,response.text
    response=client.put(API+'/admin/default-models',json={'requestKey':'text-defaults','expectedVersion':0,'textProfileId':profile['id'],'imageEnabled':False});assert response.status_code==200,response.text
    return vision

def test_new_account_gets_distinct_text_and_vision_without_key_exposure(client,reader,profile):
    setup_policy(client,reader,profile);new=other(client)
    models=client.get(BASE+'/model-profiles').json();assert len(models['items'])==2
    assert next(m for m in models['items'] if m['isDefault'])['modelId']=='fixture-v1'
    assert next(m for m in models['items'] if m['purpose']=='vision')['modelId']=='vision-fixture'
    response=client.get(API+'/vision-model');assert response.status_code==200
    assert response.json()['enabled'] and response.json()['source']=='platform'
    assert 'key_cipher' not in response.text and 'VISION-TEST' not in response.text
    with SessionLocal() as db:
        row=db.get(VisionSettings,new['user']['id']);default=db.get(UserSettings,new['user']['id'])
        assert default.default_profile_id!=row.profile_id and row.platform_profile_id==row.profile_id
        count=db.scalar(select(func.count()).select_from(ModelProfile).where(ModelProfile.owner_id==new['user']['id']))
        assert not provision(db,new['user']['id'])
        assert db.scalar(select(func.count()).select_from(ModelProfile).where(ModelProfile.owner_id==new['user']['id']))==count

def test_personal_selection_platform_restore_and_queued_job_are_independent(client,reader,profile):
    setup_policy(client,reader,profile)
    response=client.get(API+'/vision-model');vision=response.json();assert vision['enabled']
    with SessionLocal() as db:
        default_before=db.get(UserSettings,reader['user']['id']).default_profile_id
        initial=selected(db,reader['user']['id']).id
        task=create_job(db,reader['user']['id'],'world_scene_binding',{'worldId':'fixture-world','lineId':'fixture-line'},['analysis']);db.commit();assert task.payload['profileId']==initial
        task_id=task.id
    response=client.put(API+'/vision-model',json={'expectedVersion':vision['version'],'profileId':profile['id']});assert response.status_code==200
    personal=response.json();assert personal['source']=='personal'
    assert client.patch(BASE+'/model-profiles/'+initial,json={'expectedVersion':1,'callBudget':999}).status_code==403
    response=client.put(API+'/vision-model',json={'expectedVersion':personal['version'],'usePlatform':True});assert response.status_code==200
    assert response.json()['profileId']==initial
    with SessionLocal() as db:
        from app.models import Task
        assert db.get(Task,task_id).payload['profileId']==initial
        assert db.get(UserSettings,reader['user']['id']).default_profile_id==default_before

def test_foreign_selection_and_stale_version_are_rejected(client,reader,profile):
    vision=setup_policy(client,reader,profile);other(client)
    current=client.get(API+'/vision-model').json()
    assert client.put(API+'/vision-model',json={'expectedVersion':current['version'],'profileId':vision['id']}).status_code==404
    assert client.put(API+'/vision-model',json={'expectedVersion':0,'usePlatform':True}).status_code==409
    assert client.put(API+'/admin/vision-model',json={'expectedVersion':1,'profileId':vision['id']}).status_code==403

def test_real_binding_does_not_fall_back_to_a_text_only_default(client,reader,profile,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'ai_mode','real')
    with SessionLocal() as db:
        with pytest.raises(Problem) as error:selected(db,reader['user']['id'])
        assert error.value.code=='SCENE_VISION_NOT_CONFIGURED'


def test_release_installs_new_visual_grants_without_rewriting_profiles_or_personal_selection(client,reader,profile):
    setup_policy(client,reader,profile)
    initial=client.get(API+'/vision-model').json()
    client.put(API+'/vision-model',json={'expectedVersion':initial['version'],'profileId':profile['id']})
    with SessionLocal() as db:
        policy=db.get(Config,'platform-models');value=deepcopy(policy.value)
        value['text'].update(model_id='mimo-v2.6-flash',base_url='https://opencode.ai/zen/go/v1')
        policy.value=value;db.commit()
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('release_defaults',Path(__file__).resolve().parents[2]/'scripts/install-p0p1-production-defaults.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with SessionLocal() as db:
        before=db.get(UserSettings,reader['user']['id']).default_profile_id
        result=module.install(db)
        assert result['passed'] and result['reviewedStoriesPublished']==3
        assert result['personalVisionSelectionsPreserved']==1
        count=db.scalar(select(func.count()).select_from(ModelProfile))
        row=db.get(VisionSettings,reader['user']['id'])
        assert row.profile_id==profile['id'] and not row.managed
        assert db.get(ModelProfile,row.platform_profile_id).model_id=='mimo-v2.6-flash'
        assert db.get(ModelProfile,initial['profileId']).model_id=='vision-fixture'
        assert db.get(UserSettings,reader['user']['id']).default_profile_id==before
        assert module.install(db)==result
        assert db.scalar(select(func.count()).select_from(ModelProfile))==count


def test_platform_vision_quota_cannot_be_selected_as_the_story_default(client,reader,profile):
    setup_policy(client,reader,profile);other(client)
    vision=client.get(API+'/vision-model').json()
    assert client.put(BASE+'/model-profiles/'+vision['profileId']+'/default').status_code==403
    models=client.get(BASE+'/model-profiles').json()
    assert models['defaultProfileId']!=vision['profileId']


def test_retired_platform_visual_grant_keeps_purpose_and_quota_guards(client,reader,profile):
    setup_policy(client,reader,profile);new=other(client)
    original=client.get(API+'/vision-model').json()['profileId']
    owner=new['user']['id']
    with SessionLocal() as db:
        old=db.get(ModelProfile,original);old.calls_reserved=9
        old.input_reserved=333;old.output_reserved=44
        row=db.get(VisionSettings,owner)
        replacement=ModelProfile(owner_id=owner,name='replacement vision',model_id='new-vision',base_url=old.base_url,
            key_cipher=old.key_cipher,key_tail='',context_window=old.context_window)
        db.add(replacement);db.flush()
        row.profile_id=row.platform_profile_id=replacement.id;row.version+=1
        db.add(Config(key='platform-vision-history',value={owner:[original]}));db.commit()
    models=client.get(BASE+'/model-profiles').json()
    retired=next(p for p in models['items'] if p['id']==original)
    assert retired['purpose']=='vision' and retired['source']=='platform'
    assert retired['callsReserved']==9 and retired['inputReserved']==333 and retired['outputReserved']==44
    assert models['defaultProfileId']!=original
    assert client.put(BASE+'/model-profiles/'+original+'/default').status_code==403
    assert client.patch(BASE+'/model-profiles/'+original,json={'expectedVersion':1,'callBudget':999}).status_code==403
    with SessionLocal() as db:
        from app.play_controls.service import text_profile,presentation
        with pytest.raises(Problem) as error:text_profile(db,owner,original)
        assert error.value.code=='TEXT_MODEL_REQUIRED'
        assert original not in {p['id'] for p in presentation(db,owner,'line','test')['models']}
        assert db.get(ModelProfile,original).model_id=='vision-fixture'


def test_agnes_visual_policy_reserves_reasoning_and_preserves_mimo_story_cap():
    from types import SimpleNamespace
    from app.completion_policy import completion_policy
    vision=SimpleNamespace(base_url='https://apihub.agnes-ai.com/v1',model_id='agnes-2.5-flash')
    text=SimpleNamespace(base_url='https://opencode.ai/zen/go/v1',model_id='mimo-v2.6-flash')
    assert completion_policy(vision,world=True)['stages']['analysis']['totalCompletionTokens']==16384
    assert completion_policy(text,world=True)['stages']['analysis']['totalCompletionTokens']==4096
    assert completion_policy(vision,world=False)['stages']['analysis']['totalCompletionTokens']==2048
