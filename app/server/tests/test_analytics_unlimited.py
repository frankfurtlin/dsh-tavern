"""Quota policy, telemetry isolation and analytics from durable receipts."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from app.config import settings
from app.db import SessionLocal
from app.models import ModelProfile,Task,TaskEvent,UsageRecord,uid,now
from app.foundation.models import Account,Config,Event,Hold
from app.foundation.analytics import report
from app.foundation.events import emit
from app.foundation.wallet import reserve_usage,reserve_scene
from app.model_budget import call_budget_exceeded,input_budget_exceeded,output_budget_exceeded,image_budget_exceeded
from .test_product_foundation import operator,new_user,telemetry_event
from .test_world_security import isolated_storage_and_mock_only
from .test_play_controls_world_chat import world

ROOT='/api/reader/v1/product'


def test_quota_mode_preserves_finite_rejections_and_bypasses_all_cumulative_limits(monkeypatch):
    p=SimpleNamespace(call_budget=1,calls_reserved=500,input_budget=10,input_reserved=500,output_budget=10,output_reserved=500,daily_limit=1)
    for enabled in (False,True):
        monkeypatch.setattr(settings,'product_unlimited_usage',enabled)
        assert call_budget_exceeded(p,2)==(not enabled)
        assert input_budget_exceeded(p,100)==(not enabled)
        assert output_budget_exceeded(p,100)==(not enabled)
        assert image_budget_exceeded(p,500)==(not enabled)


def test_unlimited_zero_wallet_regular_account_executes_with_exhausted_profile(client,reader,profile,monkeypatch):
    monkeypatch.setattr(settings,'product_unlimited_usage',True)
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);p.call_budget=1;p.calls_reserved=500;p.input_budget=p.output_budget=1;p.input_reserved=p.output_reserved=500
        db.add(Config(key='billing',value={'enabled':True,'prices':{'world_turn':999999,'scene_image':999999}}));db.commit()
    w,line=world(client)
    assert line['head']['sequence']==1
    balance=client.get(ROOT+'/wallet').json()
    assert balance['unlimitedUsage'] and not balance['billingEnabled'] and balance['available']==0
    assert client.get(ROOT+'/account').json()['role']=='USER'
    with SessionLocal() as db:
        assert db.get(ModelProfile,profile['id']).calls_reserved>500
        assert db.scalar(select(func.count()).select_from(UsageRecord))>0
        assert db.scalar(select(func.count()).select_from(Hold))==0
        p=db.get(ModelProfile,profile['id']);assert p.input_reserved>500 and p.output_reserved>500
    control=client.get('/api/reader/v1/play-controls/lines/'+line['id']+'/settings').json()
    assert control['models'][0]['remainingInputTokens'] is None
    assert control['models'][0]['remainingOutputTokens'] is None


def test_unlimited_no_image_hold_and_new_accounts_inherit_policy(client,reader,monkeypatch):
    monkeypatch.setattr(settings,'product_unlimited_usage',True)
    with SessionLocal() as db:
        db.add(Config(key='billing',value={'enabled':True,'prices':{'scene_image':9999}}));db.commit()
        reserve_scene(db,SimpleNamespace(owner_id=reader['user']['id'],id=uid()))
        assert db.scalar(select(func.count()).select_from(Hold))==0
    owner=new_user(client,'new_unlimited_user')
    login=client.post('/api/reader/v1/auth/login',json={'username':'new_unlimited_user','password':'product-password-123','clientType':'android'})
    client.headers['Authorization']='Bearer '+login.json()['accessToken']
    assert client.get(ROOT+'/wallet').json()['unlimitedUsage']
    assert client.get(ROOT+'/account').json()['role']=='USER'
    assert client.get(ROOT+'/analytics/overview').status_code==403


def test_analytics_guard_and_validated_window(client,reader):
    assert client.get(ROOT+'/analytics/overview').status_code==403
    client.headers.pop('Authorization')
    assert client.get(ROOT+'/analytics/overview').status_code==401


def test_public_events_cannot_spoof_accounts_or_facts(client):
    e={**telemetry_event(None),'path':'/login?next=secret','name':'fact.aftermath.resolved','metadata':{'prompt':'secret'}}
    body={'eventVersion':2,'expectedUserId':None,'events':[e]}
    for _ in range(2):assert client.post(ROOT+'/telemetry/public',json=body).status_code==200
    with SessionLocal() as db:
        events=list(db.scalars(select(Event)))
        assert len(events)==1 and events[0].owner_id is None
        assert events[0].name=='public.visit' and events[0].path=='/login' and 'secret' not in str(events[0].data)
    assert client.post(ROOT+'/telemetry/public',json={**body,'expectedUserId':'a'*32}).status_code==409


def test_client_ids_are_per_owner_and_metadata_has_no_free_text(client,operator):
    e={**telemetry_event(operator),'metadata':{'worldId':'a'*32,'notes':'private words','safe':'private prose','password':'secret'},'path':'/explore'}
    body={'eventVersion':2,'expectedUserId':operator,'events':[e]}
    for _ in range(2):assert client.post(ROOT+'/telemetry/track',json=body).status_code==200
    other=new_user(client)
    token=client.post('/api/reader/v1/auth/login',json={'username':'product_two','password':'product-password-123','clientType':'android'}).json()['accessToken']
    client.headers['Authorization']='Bearer '+token
    assert client.post(ROOT+'/telemetry/track',json={**body,'expectedUserId':other,'events':[{**e,'ownerUserId':other}]}).status_code==200
    with SessionLocal() as db:
        rows=list(db.scalars(select(Event).where(Event.category=='page_view')))
        assert len(rows)==2 and {r.owner_id for r in rows}=={operator,other}
        assert all(r.data['metadata']=={'worldId':'a'*32} and r.path=='/explore' for r in rows)


def test_report_exclusions_real_receipts_cost_unknowns_and_ordered_funnel(client,operator,profile):
    other=new_user(client)
    start=now()-timedelta(minutes=30)
    with SessionLocal() as db:
        for owner in (operator,other):
            for i,(name,category) in enumerate([('visit','page_view'),('play.enter','click')]):
                db.add(Event(owner_id=owner,event_key=uid(),name=name,category=category,path='/explore',data={},created_at=start+timedelta(seconds=i)))
            task=Task(id=uid(),owner_id=owner,task_type='world_turn',payload={},state='succeeded',created_at=start+timedelta(seconds=3));db.add(task);db.flush()
            db.add(TaskEvent(task_id=task.id,seq=1,type='text_delta',payload={},created_at=start+timedelta(seconds=4)))
            db.add(TaskEvent(task_id=task.id,seq=2,type='terminal',payload={},created_at=start+timedelta(seconds=7)))
            for cost in ({'costState':'known','currency':'CNY','cost':'1.20'},{}):
                db.add(UsageRecord(owner_id=owner,task_id=task.id,provider_call_id=uid(),profile_id=profile['id'],consent_id=uid(),operation='world_turn',reserved_input=0,reserved_output=0,input_tokens=100,output_tokens=20,status='completed',latency_ms=1000,billing_metadata={'billing':cost}))
        db.commit()
        r=report(db,7)
        assert r['summary']['accounts']==1 and r['summary']['tasks']==1 and r['summary']['successRate']==1
        assert [s['users'] for s in r['funnel']]==[1,1,1,1]
        assert r['timings']['firstText']=={'samples':1,'medianMs':1000,'p95Ms':1000}
        assert r['timings']['total']['medianMs']==4000
        assert r['usage']['costs']==[{'currency':'CNY','amount':'1.20'}]
        assert r['usage']['unknownCostRecords']==1
        assert report(db,7,True)['summary']['tasks']==2
        db.add(Config(key='beta-cohorts',value={'excludedUserIds':[other]}));db.commit()
        assert report(db,7)['summary']['accounts']==0
    response=client.get(ROOT+'/analytics/overview?days=7&includeAdmins=true')
    assert response.status_code==200,response.text
    assert response.json()['summary']['tasks']==1
    assert client.get(ROOT+'/analytics/overview?days=91').status_code==422


def test_report_does_not_claim_unordered_conversion_or_immature_retention(client,operator):
    with SessionLocal() as db:
        # An unrelated earlier task is not a conversion after a later page visit.
        t=Task(owner_id=operator,task_type='world_turn',payload={'message':'DO_NOT_EXPORT'},state='succeeded',created_at=now()-timedelta(minutes=10));db.add(t);db.flush()
        db.add(TaskEvent(task_id=t.id,seq=1,type='terminal',payload={},created_at=now()-timedelta(minutes=9)))
        db.add(Event(owner_id=operator,event_key=uid(),name='visit',category='page_view',path='/explore',data={},created_at=now()-timedelta(minutes=2)))
        db.add(Event(owner_id=operator,event_key=uid(),name='play.enter',category='click',path='/explore',data={},created_at=now()-timedelta(minutes=1)));db.commit()
        r=report(db,7,True)
        assert [s['users'] for s in r['funnel']]==[1,1,0,0]
        assert all(c['d1'] is None for c in r['retention'])
        assert 'DO_NOT_EXPORT' not in str(r)
