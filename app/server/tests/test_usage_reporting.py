"""Owner-scoped accounting display, never provider prose or private receipts."""
import json
from copy import deepcopy
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Task, UsageRecord, User, uid
from app.providers import ChatCompletionsAdapter
from .helpers import BASE
from .test_world_security import isolated_storage_and_mock_only


def records(client, reader, demo, profile):
    made = client.post(BASE+'/model-profiles', json={'name':'other-model', 'baseUrl':'mock://deterministic',
        'modelId':'other-v1','key':'SYNTHETIC-NOT-A-KEY','contextWindow':32768})
    assert made.status_code==201,made.text
    second=made.json()
    made=client.post('/api/world/v1/worlds', json={'name':'own-world','templateId':'original-mystery-v1'},
        headers={'Idempotency-Key':'usage-own-world'})
    assert made.status_code==202,made.text
    world=client.get('/api/world/v1/worlds/'+made.json()['worldId']).json()
    with SessionLocal() as db:
        owner = db.scalar(select(User.id).where(User.username=='reader_one'))
        values=[]
        for scope, pid, name, model in [('book',profile['id'],profile['name'],profile['modelId']),
                                       ('world',second['id'],'other-model','other-v1')]:
            task=Task(owner_id=owner,task_type='generation' if scope=='book' else 'world_turn',state='succeeded',
                persistence_state='saved',payload={'profileId':pid, **({'bookId':demo['bookId']} if scope=='book' else {'worldId':world['id']})},
                checkpoint={'providerConfig':{'provider':name,'modelId':model,'profileVersion':1},
                    'PRIVATE_GM':'NEVER-CLIENT-GM'}, result={'phase':'completed','text':'NEVER-CLIENT-BODY'})
            db.add(task);db.flush()
            row=UsageRecord(owner_id=owner,task_id=task.id,provider_call_id=uid(),profile_id=pid,consent_id=uid(),
                operation='generation',reserved_input=100,reserved_output=200,input_tokens=12,output_tokens=30,
                usage_state='known',status='completed')
            db.add(row);db.flush();values.append((row.id,task.id,pid))
        db.commit()
    return values, world


@pytest.mark.parametrize('filter_name,index', [('taskId',1),('profileId',1),('storyId',0)])
def test_actual_usage_endpoint_filters_authorized_task_story_and_model(client,reader,demo,profile,filter_name,index):
    values,world=records(client,reader,demo,profile)
    value = values[index][{'taskId':1,'profileId':2}.get(filter_name,0)] if filter_name!='storyId' else 'book-'+demo['bookId']
    response=client.get(BASE+'/usage',params={filter_name:value})
    assert response.status_code==200,response.text
    data=response.json()
    assert [r['id'] for r in data['items']]==[values[index][0]]
    assert data['summary']['calls']==1
    item=data['items'][0]
    assert item['task']['id']==values[index][1] and item['profile']['id']==values[index][2]
    assert item['story']['id']==('book-'+demo['bookId'] if index==0 else 'world-'+world['id'])
    assert item['cost'] is None and item['costState']=='unknown' and item['reasoningTokens'] is None
    assert 'NEVER-CLIENT' not in json.dumps(data)


def test_usage_query_foreign_owner_and_cursor_do_not_reveal_metadata(client,reader,demo,profile):
    values,_=records(client,reader,demo,profile)
    client.headers.clear()
    assert client.post(BASE+'/auth/register',json={'username':'usage_other','password':'usage-other-password-123'}).status_code==201
    token=client.post(BASE+'/auth/login',json={'username':'usage_other','password':'usage-other-password-123',
        'clientType':'web','deviceName':'isolated-other'}).json()['accessToken']
    client.headers['Authorization']='Bearer '+token
    for query in [{'taskId':values[0][1]},{'profileId':profile['id']},{'storyId':'book-'+demo['bookId']},{'cursor':values[0][0]}]:
        r=client.get(BASE+'/usage',params=query)
        assert r.status_code==404,r.text
        assert '雾港' not in r.text and 'NEVER-CLIENT' not in r.text


def test_usage_summary_is_full_filtered_scope_not_just_first_page(client,reader,demo,profile):
    values,_=records(client,reader,demo,profile)
    page=client.get(BASE+'/usage',params={'limit':1}).json()
    assert page['summary']['calls']==2 and page['summary']['outputTokens']==60
    assert len(page['items'])==1 and page['nextCursor']
    second=client.get(BASE+'/usage',params={'limit':1,'cursor':page['nextCursor']}).json()
    assert {page['items'][0]['id'],second['items'][0]['id']}=={v[0] for v in values}
    assert second['summary']==page['summary']


@pytest.mark.parametrize('details,expected', [
    ({'reasoning_tokens':7},7), ({'reasoning_tokens':0},0), ({'reasoning_tokens':True},None),
    ({'reasoning_tokens':31},None), ({'reasoning_content':'PRIVATE-THOUGHT'},None),
])
def test_provider_usage_keeps_billed_reasoning_numeric_only_and_never_double_counts(details,expected):
    raw={'prompt_tokens':12,'completion_tokens':30,'completion_tokens_details':details}
    result=ChatCompletionsAdapter().normalizeUsage(raw)
    assert result.get('reasoningTokens')==expected and result['outputTokens']==30
    assert result.get('reasoningIncludedInOutput')==(True if expected is not None else None)
    assert 'PRIVATE-THOUGHT' not in json.dumps(result)


def test_provider_cost_requires_explicit_safe_amount_and_currency_unknown_is_not_zero():
    adapter=ChatCompletionsAdapter()
    known=adapter.normalizeUsage({'prompt_tokens':12,'completion_tokens':30,'cost':{'amount':0.125,'currency':'USD'}})
    assert known['cost']==0.125 and known['currency']=='USD' and known['costState']=='known'
    for raw in [None,{}, {'prompt_tokens':12,'completion_tokens':30},
            {'prompt_tokens':12,'completion_tokens':30,'cost':{'amount':True,'currency':'USD'}},
            {'prompt_tokens':12,'completion_tokens':30,'cost':{'amount':0.1}},
            {'prompt_tokens':12,'completion_tokens':30,'cost':{'amount':float('nan'),'currency':'USD'}}]:
        value=adapter.normalizeUsage(raw)
        assert value.get('cost') is None and value.get('currency') is None
        assert value.get('costState','unknown')=='unknown'


@pytest.mark.parametrize('fence',['deleted','lost-lease','accounting-only'])
def test_received_numeric_billing_settles_once_without_mutating_a_fenced_task(client,reader,profile,fence):
    from app.models import Consent, ModelProfile, TaskEvent, now
    from app.worker import settle_usage
    from app.task_lease import ClaimedTask, leased_task
    from datetime import timedelta
    owner=reader['user']['id']
    with SessionLocal() as db:
        task=Task(owner_id=owner,task_type='connection_test',payload={},state='cancelled',
            persistence_state='saved',lease_owner='new-lease',result={'deleted':True} if fence=='deleted' else {})
        db.add(task);db.flush()
        consent=Consent(owner_id=owner,plan_id=uid(),profile_id=profile['id'],profile_version=1,
            plan_hash='0'*64,max_calls=1,input_limit=100,output_limit=200,
            expires_at=now()+timedelta(minutes=1),task_id=task.id,calls_reserved=1,input_reserved=100,output_reserved=200)
        db.add(consent);db.flush()
        model=db.get(ModelProfile,profile['id']);model.calls_reserved=1;model.input_reserved=100;model.output_reserved=200
        row=UsageRecord(owner_id=owner,task_id=task.id,provider_call_id=uid(),profile_id=profile['id'],consent_id=consent.id,
            operation='generation',reserved_input=100,reserved_output=200,status='intent',usage_state='unknown')
        db.add(row);db.flush();ids=(task.id,row.provider_call_id,consent.id,row.id)
        before=(task.state,task.provider_state,task.usage_state,task.event_seq,deepcopy(task.result))
        db.commit()
    received={'usage':ChatCompletionsAdapter().normalizeUsage({'prompt_tokens':12,'completion_tokens':30,
        'completion_tokens_details':{'reasoning_tokens':7,'reasoning_content':'NEVER-CLIENT'},
        'cost':{'amount':0.125,'currency':'USD'}}),'latencyMs':8}
    @leased_task
    def old_executor(task_id):
        settle_usage(task_id,ids[1],received,accounting_only=fence=='accounting-only')
    token='old-lease' if fence=='lost-lease' else 'new-lease'
    old_executor(ClaimedTask(ids[0],token))
    old_executor(ClaimedTask(ids[0],token))
    with SessionLocal() as db:
        task=db.get(Task,ids[0]);row=db.get(UsageRecord,ids[3]);model=db.get(ModelProfile,profile['id']);consent=db.get(Consent,ids[2])
        assert before==(task.state,task.provider_state,task.usage_state,task.event_seq,task.result)
        assert list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==task.id)))==[]
        assert (row.status,row.input_tokens,row.output_tokens)==('completed',12,30)
        assert row.billing_metadata['billing']=={'reasoningTokens':7,'reasoningIncludedInOutput':True,
            'cost':0.125,'currency':'USD','costState':'known'}
        assert (model.calls_reserved,model.input_reserved,model.output_reserved)==(1,12,30)
        assert (consent.calls_reserved,consent.input_reserved,consent.output_reserved)==(1,12,30)


def test_actual_worker_captures_public_labels_and_billed_reasoning_without_leaking_reply(client,reader,profile,monkeypatch):
    from .test_regressions import queue_operation
    from app.worker import claim,process
    from app.providers import MockAdapter
    original=MockAdapter.generate
    def numeric_receipt(adapter,*args,**kwargs):
        value=original(adapter,*args,**kwargs)
        value['usage']=ChatCompletionsAdapter().normalizeUsage({'prompt_tokens':12,'completion_tokens':30,
            'completion_tokens_details':{'reasoning_tokens':7,'reasoning_content':'NEVER-CLIENT-THOUGHT'},
            'cost':{'amount':0.125,'currency':'USD'}})
        value['text']='NEVER-CLIENT-PROVIDER-BODY'
        return value
    monkeypatch.setattr(MockAdapter,'generate',numeric_receipt)
    task_id=queue_operation(client,profile,{'operation':'connection_test','profileId':profile['id']})
    process(claim('own-usage-worker'))
    value=client.get(BASE+'/usage',params={'taskId':task_id}).json()
    assert value['summary']['calls']==1 and len(value['items'])==1
    row=value['items'][0]
    assert row['profile']=={'id':profile['id'],'name':profile['name'],'modelId':profile['modelId'],'version':1}
    assert (row['outputTokens'],row['reasoningTokens'],row['cost'])==(30,7,0.125)
    assert value['summary']['knownCostByCurrency']=={'USD':0.125}
    assert 'NEVER-CLIENT' not in json.dumps(value)
    with SessionLocal() as db:
        stored=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id))
        assert stored.billing_metadata['profile']['modelId']==profile['modelId']


def test_usage_filter_options_are_read_only_owner_scoped_and_contain_no_task_payload(client,reader,demo,profile):
    values,world=records(client,reader,demo,profile)
    value=client.get(BASE+'/usage/filters').json()
    assert {t['id'] for t in value['tasks']}=={v[1] for v in values}
    assert {s['id'] for s in value['stories']}=={'book-'+demo['bookId'],'world-'+world['id']}
    assert 'NEVER-CLIENT' not in json.dumps(value) and 'payload' not in json.dumps(value)


def test_usage_metadata_migration_initializes_legacy_rows_and_refuses_lossy_downgrade(client,reader,demo,profile):
    from alembic import command
    from alembic.config import Config
    from app.db import engine
    from sqlalchemy import text
    values,_=records(client,reader,demo,profile)
    cfg=Config('alembic.ini')
    command.downgrade(cfg,'0013')
    assert client.get('/health/ready').status_code==503
    command.upgrade(cfg,'0014')
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM usage WHERE billing_metadata=\'{}\'::jsonb'))==2
    assert client.get('/health/ready').status_code==503  # Current app also requires product0015.
    command.downgrade(cfg,'0013');command.upgrade(cfg,'0014')
    with SessionLocal() as db:
        db.get(UsageRecord,values[0][0]).billing_metadata={'billing':{'cost':0.125,'currency':'USD'}}
        db.commit()
    with pytest.raises(RuntimeError,match='Cannot discard received accounting'):
        command.downgrade(cfg,'0013')
    with engine.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0014'
        assert db.scalar(text("SELECT billing_metadata FROM usage WHERE id=:id"),{'id':values[0][0]})=={'billing':{'cost':0.125,'currency':'USD'}}
    command.upgrade(cfg,'head')
    assert client.get('/health/ready').status_code==200


def test_sql_summary_and_complete_story_model_options_do_not_depend_on_the_latest_200_tasks(client,reader,demo,profile):
    from sqlalchemy import event
    from app.db import engine
    from app.usage_reporting import query,summary
    values,world=records(client,reader,demo,profile)
    owner=reader['user']['id']
    with SessionLocal() as db:
        for i in range(205):
            task=Task(owner_id=owner,task_type='connection_test',payload={},state='succeeded',persistence_state='saved')
            db.add(task);db.flush()
            db.add(UsageRecord(owner_id=owner,task_id=task.id,provider_call_id=uid(),profile_id=profile['id'],consent_id=uid(),
                operation='connection_test',reserved_input=10,reserved_output=10,input_tokens=1,output_tokens=2,
                status='completed',usage_state='known',billing_metadata={'billing':{'reasoningTokens':1,'cost':0.1,'currency':'USD'}}))
        db.commit()
    selects=[]
    def observe(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'):selects.append(statement)
    event.listen(engine,'before_cursor_execute',observe)
    try:
        with SessionLocal() as db:value=summary(db,query(db,owner))
    finally:event.remove(engine,'before_cursor_execute',observe)
    assert len(selects)==2 and all('sum(' in s.lower() for s in selects)
    assert value['calls']==207 and value['outputTokens']==470 and value['reasoningTokens'] is None
    assert value['knownCostByCurrency']=={'USD':20.5} and value['unknownCostCalls']==2
    options=client.get(BASE+'/usage/filters').json()
    assert len(options['tasks'])==200 and not {v[1] for v in values}&{t['id'] for t in options['tasks']}
    assert {s['id'] for s in options['stories']}=={'book-'+demo['bookId'],'world-'+world['id']}
    assert {p['id'] for p in options['profiles']}=={v[2] for v in values}
    selects.clear();event.listen(engine,'before_cursor_execute',observe)
    try:
        page=client.get(BASE+'/usage',params={'limit':100})
    finally:event.remove(engine,'before_cursor_execute',observe)
    assert page.status_code==200,page.text
    assert len(page.json()['items'])==100 and len(selects)<=10


def test_deleted_world_usage_remains_owner_filterable_without_restoring_private_details(client,reader,demo,profile):
    from app.world.models import World
    from sqlalchemy import delete
    values,world=records(client,reader,demo,profile)
    with SessionLocal() as db:
        task=db.get(Task,values[1][1]);task.payload={};task.checkpoint={};task.result={'deleted':True,'worldId':world['id']}
        db.execute(delete(World).where(World.id==world['id']));db.commit()
    value=client.get(BASE+'/usage',params={'storyId':'world-'+world['id']})
    assert value.status_code==200,value.text
    data=value.json();assert [r['id'] for r in data['items']]==[values[1][0]]
    assert data['items'][0]['story']=={'id':'world-'+world['id'],'kind':'world','title':'已移除的世界','removed':True}
    assert 'own-world' not in value.text and 'NEVER-CLIENT' not in value.text


def test_book_story_filter_does_not_mix_a_derived_worlds_source_anchor_calls(client,reader,demo,profile):
    values,world=records(client,reader,demo,profile)
    with SessionLocal() as db:
        task=db.get(Task,values[1][1])
        task.payload={**task.payload,'anchor':{'bookId':demo['bookId']}}
        db.commit()
    book=client.get(BASE+'/usage',params={'storyId':'book-'+demo['bookId']}).json()
    assert [row['id'] for row in book['items']]==[values[0][0]]
    derived=client.get(BASE+'/usage',params={'storyId':'world-'+world['id']}).json()
    assert [row['id'] for row in derived['items']]==[values[1][0]]


@pytest.mark.parametrize('schema_present',[False,True])
def test_disabled_world_usage_filter_respects_missing_schema_and_retained_history(client,reader,demo,profile,monkeypatch,schema_present):
    from app.db import engine
    from app.config import settings
    from app.world.migrate import migrate
    from sqlalchemy import text
    world_id=uid();values=None
    if schema_present:
        values,world=records(client,reader,demo,profile)
        world_id=world['id']
    else:
        with engine.begin() as db:
            assert db.scalar(text('SELECT count(*) FROM world.instances'))==0
            db.execute(text('DROP SCHEMA world CASCADE'))
    original=settings.interactive_world_enabled
    monkeypatch.setattr(settings,'interactive_world_enabled',False)
    try:
        response=client.get(BASE+'/usage',params={'storyId':'world-'+world_id})
        if schema_present:
            assert response.status_code==200,response.text
            assert [r['id'] for r in response.json()['items']]==[values[1][0]]
            options=client.get(BASE+'/usage/filters')
            assert options.status_code==200,options.text
            assert 'world-'+world_id in {s['id'] for s in options.json()['stories']}
        else:
            assert response.status_code==404,response.text
            assert response.json()['code']=='RESOURCE_NOT_FOUND'
            assert client.get(BASE+'/usage/filters').status_code==200
    finally:
        monkeypatch.setattr(settings,'interactive_world_enabled',original)
        if not schema_present:migrate()
