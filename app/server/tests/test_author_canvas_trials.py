from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func
from pydantic import ValidationError
from app.db import SessionLocal
from app.models import Task
from app.world.models import World,WorldLine,WorldCheckpoint
from app.world.worker import process
from app.authoring.schemas import CustomField,check_value
from app.authoring.trials import reserve
from app.errors import Problem
from .test_world_security import isolated_storage_and_mock_only
from .test_authoring_publications import post,draft,other,API,WORLD


@pytest.mark.parametrize('kind,value,options',[('multiselect',['侦察','记录'],['侦察','记录']),('list',['便笺','地图'],[]),('longtext','背景'*700,[])])
def test_canvas_new_values_roundtrip_and_reject_invalid_types(kind,value,options):
    f=CustomField(id='author_custom',label='设定',type=kind,defaultValue=value,options=options,group='人物',minHeight=120)
    assert CustomField.model_validate(f.model_dump()).defaultValue==value
    for invalid in ([['nested']],{'nested':'value'},[1],['x']*21):
        with pytest.raises((ValueError,ValidationError)):CustomField(id='author_custom',label='设定',type=kind,defaultValue=invalid,options=options)
    with pytest.raises(ValueError):check_value({**f.model_dump(),'required':True},[] if kind!='longtext' else '')


def configured(client):
    world,current=draft(client);v=current['value']
    v['playerFields']=[{'id':'author_abilities','label':'擅长','type':'multiselect','options':['侦察','记录'],'required':True,'defaultValue':['记录'],'group':'人物','width':'half','presentation':'dropdown','minHeight':120}]
    v['statusFields']=[{'id':'author_mementos','label':'纪念清单','type':'list','defaultValue':['旧便笺'],'group':'记事','width':'full'}]
    v.update(formTitle='你的旅人',statusTitle='旅人手记',statusTheme='terminal')
    result=client.put(API+'/worlds/'+world,json={'expectedVersion':current['version'],'value':v},headers={'Idempotency-Key':'canvas-save'})
    assert result.status_code==200,result.text
    return world,result.json()


def start(client,world,current,limit=2,key='trial-once',**extra):
    return post(client,API+'/worlds/'+world+'/trials',{'authorVersion':current['version'],'openingId':'shop','requestId':key,'maxGenerations':limit,**extra},key)


def test_author_trial_is_frozen_private_idempotent_and_budgeted_across_forks(client,reader,profile):
    world,current=configured(client)
    first=start(client,world,current);again=start(client,world,current)
    assert first==again
    with SessionLocal() as db:
        original=db.get(World,world);before=deepcopy(original.definition)
        assert original.active_line_id is None
        assert db.scalar(select(func.count()).select_from(Task))==1
        sandbox=db.get(World,first['worldId']);assert sandbox.definition['authoring']['statusTheme']=='terminal'
    process(first['taskId'])
    line=client.get(WORLD+'/worlds/'+first['worldId']+'/lines/'+first['lineId']).json()
    person=next(e for e in line['head']['state']['entities'] if e['id']==line['head']['state']['playerId'])
    assert person['state']['fields']['author_abilities']==['记录']
    assert person['state']['fields']['author_mementos']==['旧便笺']
    metadata=line['head']['state']['statusFields'];assert any(f.get('group')=='记事' and f.get('panelTheme')=='terminal' for f in metadata)
    root=WORLD+'/worlds/'+first['worldId']
    action=post(client,root+'/lines/'+first['lineId']+'/actions',{'expectedCheckpointId':line['headCheckpointId'],'message':'只观察周围，不转交便笺。','turnId':'second'},'second')
    process(action['taskId'])
    current_line=client.get(root+'/lines/'+first['lineId']).json()
    for suffix in ['/lines/'+first['lineId']+'/actions']:
        response=client.post(root+suffix,json={'expectedCheckpointId':current_line['headCheckpointId'],'message':'只观察。','turnId':'third'},headers={'Idempotency-Key':'third'})
        assert response.status_code==403,response.text
        assert response.json()['code']=='AUTHOR_TRIAL_BUDGET_EXCEEDED'
    branch=post(client,root+'/fork',{'checkpointId':current_line['headCheckpointId'],'mode':'continue','name':'测试分支'},'fork')
    response=client.post(root+'/lines/'+branch['id']+'/actions',json={'expectedCheckpointId':branch['headCheckpointId'],'message':'只观察。','turnId':'branch-third'},headers={'Idempotency-Key':'branch-third'})
    assert response.status_code==403,response.text
    records=client.get(API+'/worlds/'+world+'/trials').json()['items'];assert len(records)==1 and records[0]['reserved']['generations']==2
    assert len(records[0]['tasks'])==2
    no_image=client.post(root+'/lines/'+first['lineId']+'/scene',json={'expectedCheckpointId':current_line['headCheckpointId']},headers={'Idempotency-Key':'trial-image'})
    assert no_image.status_code==403,no_image.text
    publication=client.post(API+'/worlds/'+first['worldId']+'/submit',json={'authorVersion':1,'expectedVersion':0,'rightsConfirmed':True,'attribution':'测试不能公开'},headers={'Idempotency-Key':'private'})
    assert publication.status_code==409 and publication.json()['code']=='AUTHOR_TRIAL_PRIVATE'
    with SessionLocal() as db:
        original=db.get(World,world);assert original.active_line_id is None and original.definition==before
        assert db.scalar(select(func.count()).select_from(Task))==2
    other(client)
    assert client.get(API+'/worlds/'+world+'/trials').status_code==404


def test_trial_insufficient_budget_or_stale_version_does_not_create_world_or_task(client,reader,profile):
    world,current=configured(client)
    for i,(change,code) in enumerate([({'maxInputTokens':1000},403),({'authorVersion':99},409),({'playerValues':{'author_abilities':['不存在']}},422)]):
        payload={'authorVersion':current['version'],'openingId':'shop',**change}
        response=client.post(API+'/worlds/'+world+'/trials',json=payload,headers={'Idempotency-Key':'invalid-'+str(i)})
        assert response.status_code==code,response.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(World))==1
            assert db.scalar(select(func.count()).select_from(Task))==0


def test_trial_reservations_serialize_and_unknown_attempt_is_not_refunded(client,reader,profile):
    world,current=configured(client);first=start(client,world,current,limit=2)
    with SessionLocal() as db:
        task=db.get(Task,first['taskId']);task.state='failed';task.provider_state='unknown';db.commit()
    def request(i):
        with SessionLocal() as db:
            try:reserve(db,reader['user']['id'],{'worldId':first['worldId']},{'inputTokens':1,'outputTokens':1},str(i));db.commit();return True
            except Problem:db.rollback();return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(request,[1,2]))
    assert sorted(results)==[False,True]
    record=client.get(API+'/worlds/'+world+'/trials').json()['items'][0]
    assert record['reserved']['generations']==2 and record['tasks'][0]['providerState']=='unknown'


def test_memory_panel_generation_snapshot_stays_scoped_and_sources_are_linked(client,reader,profile):
    world,current=configured(client);first=start(client,world,current)
    process(first['taskId'])
    control='/api/reader/v1/play-controls/lines/'+first['lineId']+'/settings'
    value=client.get(control);assert value.status_code==200,value.text
    v=value.json();assert v['episodes'] and v['episodes'][-1]['href'].startswith('/worlds/'+first['worldId']+'/read?line=')
    assert v['generationContext']['memoryVersion']==0
    saved=client.put(control,json={'expectedVersion':0,'summary':'只作为摘要提醒','notes':'不可改物品或结局'},headers={'Idempotency-Key':'memory-edit'})
    assert saved.status_code==200,saved.text
    assert saved.json()['version']==1 and saved.json()['generationContext']['memoryVersion']==0
    other(client)
    assert client.get(control).status_code==404


def test_early_chat_memory_source_targets_real_chat_route_and_owned_message(client,reader,profile):
    from app.play_controls.models import WorldChatMessage
    from app.models import uid
    world,current=configured(client);first=start(client,world,current);process(first['taskId'])
    line=client.get(WORLD+'/worlds/'+first['worldId']+'/lines/'+first['lineId']).json()
    player=line['head']['state']['playerId'];person=next(e for e in line['head']['state']['entities'] if e['type']=='character' and e['id']!=player)
    chat=post(client,'/api/reader/v1/play-controls/world-chats',{'worldId':first['worldId'],'lineId':first['lineId'],'checkpointId':line['headCheckpointId'],'characterId':person['id']},'source-chat')
    with SessionLocal() as db:
        for i in range(1,34):
            db.add(WorldChatMessage(id=uid(),owner_id=reader['user']['id'],session_id=chat['id'],task_id=first['taskId'],turn_key='history-'+str(i),sequence=i,role='user',text='请记住此前的约定 '+str(i),status='saved',checkpoint_id=line['headCheckpointId'],model_name='synthetic'))
        db.commit()
    root='/api/reader/v1/play-controls/'
    panel=client.get(root+'chats/'+chat['id']+'/settings').json();assert panel['episodes']
    for episode in panel['episodes']:
        assert episode['href']=='/conversations/world/'+chat['id']+'?message='+str(episode['sequence'])
        messages=client.get(root+'world-chats/'+chat['id']+'/messages?around='+str(episode['sequence'])).json()['items']
        assert any(m['sequence']==episode['sequence'] for m in messages)
    assert client.get(WORLD+'/worlds/'+first['worldId']+'/lines/'+first['lineId']).json()['head']==line['head']
    other(client)
    assert client.get(root+'world-chats/'+chat['id']+'/messages?around=1').status_code==404
