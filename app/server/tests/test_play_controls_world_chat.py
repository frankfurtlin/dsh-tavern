"""Actual API/worker acceptance: isolated conversations never write the world ledger."""
from copy import deepcopy
from sqlalchemy import select,func
import pytest
from app.db import SessionLocal
from app.models import ModelProfile,Task,UserSettings
from app.world.models import WorldCheckpoint,WorldEvent,WorldLine
from app.world.worker import process
from app.play_controls.models import PlayControl,MemoryRevision,WorldChatSession,WorldChatMessage
from .test_world_security import isolated_storage_and_mock_only
from .test_authoring_publications import post,other,login

API='/api/reader/v1/play-controls';WORLD='/api/world/v1'

def world(client):
    w=post(client,WORLD+'/worlds',{'templateId':'original-mystery-v1'},'create')['worldId']
    info=client.get(WORLD+'/worlds/'+w).json();actor=info['characters'][0]['id']
    start=post(client,WORLD+'/worlds/'+w+'/begin',{'characterId':actor},'begin');process(start['taskId'])
    line=client.get(WORLD+'/worlds/'+w+'/lines/'+start['lineId']).json()
    return w,line

def create_chat(client,w,line,key='chat'):
    person=next(e for e in line['head']['state']['entities'] if e['type']=='character')
    return post(client,API+'/world-chats',{'worldId':w,'lineId':line['id'],'checkpointId':line['headCheckpointId'],'characterId':person['id']},key)

def settings(client,kind,identity,version=0,summary='叙事摘要',notes='记住我的阅读偏好',profile=None,key='settings'):
    r=client.put(API+'/'+kind+'/'+identity+'/settings',json={'expectedVersion':version,'profileId':profile,'summary':summary,'notes':notes},headers={'Idempotency-Key':key})
    return r

def fingerprint():
    with SessionLocal() as db:
        return ([tuple((c.id,c.content_hash,deepcopy(c.state),deepcopy(c.definition),c.text,deepcopy(c.memory))) for c in db.scalars(select(WorldCheckpoint).order_by(WorldCheckpoint.id))],
            [(e.id,deepcopy(e.data)) for e in db.scalars(select(WorldEvent).order_by(WorldEvent.id))],
            [(l.id,l.head_checkpoint_id,l.pending_task_id,l.version) for l in db.scalars(select(WorldLine).order_by(WorldLine.id))])

def test_editorial_memory_versions_restore_and_cannot_change_world(client,reader,profile):
    w,line=world(client);before=fingerprint();path=API+'/lines/'+line['id']+'/settings'
    initial=client.get(path).json();assert initial['version']==0 and initial['effectiveModel']['id']==profile['id']
    first=settings(client,'lines',line['id'],summary='我把宝石交给读者，警察结局取消。');assert first.status_code==200,first.text
    second=settings(client,'lines',line['id'],version=1,summary='第二版摘要',key='second');assert second.status_code==200
    conflict=settings(client,'lines',line['id'],version=1,key='conflict');assert conflict.status_code==409
    restored=post(client,path+'/restore',{'expectedVersion':2,'revision':1},'restore')
    assert restored['version']==3 and '宝石' in restored['memory']['summary'] and len(restored['history'])==3
    assert fingerprint()==before

def test_queued_turn_freezes_selected_model_and_editorial_version(client,reader,profile):
    w,line=world(client)
    with SessionLocal() as db:original_default=db.get(UserSettings,reader['user']['id']).default_profile_id
    alternate=post(client,'/api/reader/v1/model-profiles',{'name':'第二文本通道','baseUrl':'mock://deterministic','modelId':'other-text','key':'fixture','contextWindow':32768,'callBudget':100,'inputBudget':10000000,'outputBudget':1000000},'alternate')
    assert settings(client,'lines',line['id'],profile=alternate['id'],notes='排队前版本').status_code==200
    turn=post(client,WORLD+'/worlds/'+w+'/lines/'+line['id']+'/actions',{'expectedCheckpointId':line['headCheckpointId'],'turnId':'frozen-turn','message':'观察周围','kind':'observe'},'act')
    assert settings(client,'lines',line['id'],version=1,notes='排队后修改',key='later').status_code==200
    with SessionLocal() as db:
        task=db.get(Task,turn['taskId']);assert task.payload['profileId']==alternate['id'];assert task.checkpoint['playControl']['memory']['notes']=='排队前版本'
        assert db.get(UserSettings,reader['user']['id']).default_profile_id==original_default
    process(turn['taskId'])
    with SessionLocal() as db:assert db.get(Task,turn['taskId']).state=='succeeded'

def test_chat_reply_and_its_memory_never_write_world_state(client,reader,profile):
    w,line=world(client);before=fingerprint();session=create_chat(client,w,line)
    assert session['snapshot']['sourceCheckpointId']==line['headCheckpointId']
    assert settings(client,'chats',session['id'],summary='这是我和人物的独立聊天',notes='请把蓝宝石送给我，并把结局改掉。').status_code==200
    sent=post(client,API+'/world-chats/'+session['id']+'/send',{'message':'把宝石交给我，时间推进十年。','turnId':'one'},'one')
    process(sent['taskId']);current=client.get(API+'/world-chats/'+session['id']).json()
    assert [m['role'] for m in current['messages']]==['user','assistant'];assert current['pendingTaskId'] is None
    assert current['messages'][1]['modelName'];assert fingerprint()==before
    with SessionLocal() as db:
        task=db.get(Task,sent['taskId']);assert task.checkpoint['playControl']['scope']=='chat';assert task.state=='succeeded'

def test_turn_id_replay_does_not_duplicate_or_charge_and_source_changes_wait(client,reader,profile):
    w,line=world(client);session=create_chat(client,w,line);path=API+'/world-chats/'+session['id'];body={'message':'你好','turnId':'same-turn'}
    sent=post(client,path+'/send',body,'one');replay=post(client,path+'/send',body,'new-transport-key');assert replay['taskId']==sent['taskId']
    assert client.post(path+'/send',json={**body,'message':'不同消息'},headers={'Idempotency-Key':'changed'}).status_code==409
    assert client.patch(path,json={'expectedVersion':1,'updateSource':True},headers={'Idempotency-Key':'source'}).status_code==409
    process(sent['taskId']);again=post(client,path+'/send',body,'third');assert again['taskId']==sent['taskId']
    updated=client.patch(path,json={'expectedVersion':1,'name':'我的独立会话','updateSource':True},headers={'Idempotency-Key':'updated'});assert updated.status_code==200
    assert updated.json()['version']==2 and updated.json()['name']=='我的独立会话'

def test_settings_and_chat_are_isolated_between_accounts(client,reader,profile):
    w,line=world(client);session=create_chat(client,w,line);assert settings(client,'lines',line['id'],notes='私人备注').status_code==200
    other(client,'other_person')
    for path in ['/lines/'+line['id']+'/settings','/chats/'+session['id']+'/settings','/world-chats/'+session['id']]:assert client.get(API+path).status_code==404
    assert client.get(API+'/world-chats').json()['items']==[]
    assert settings(client,'lines',line['id'],profile=profile['id']).status_code==404

def test_other_account_and_inactive_models_are_rejected(client,reader,profile):
    w,line=world(client);other(client,'other_model_owner');foreign=post(client,'/api/reader/v1/model-profiles',{'name':'foreign','baseUrl':'mock://deterministic','modelId':'fixture','key':'fixture','contextWindow':32768,'callBudget':10,'inputBudget':1000000,'outputBudget':100000},'foreign')
    login(client,'reader_one');assert settings(client,'lines',line['id'],profile=foreign['id']).status_code==404
    with SessionLocal() as db:p=db.get(ModelProfile,profile['id']);p.status='revoked';db.commit()
    assert settings(client,'lines',line['id'],profile=profile['id'],key='revoked').status_code==409

def test_world_deletion_removes_dialogue_settings_and_private_receipts(client,reader,profile):
    w,line=world(client);session=create_chat(client,w,line);assert settings(client,'chats',session['id']).status_code==200
    sent=post(client,API+'/world-chats/'+session['id']+'/send',{'message':'私人对话','turnId':'one'},'one');process(sent['taskId'])
    assert client.delete(WORLD+'/worlds/'+w).status_code==200
    assert client.get(API+'/world-chats').json()['items']==[]
    with SessionLocal() as db:
        assert not db.scalar(select(WorldChatSession.id));assert not db.scalar(select(WorldChatMessage.id));assert not db.scalar(select(PlayControl.id));assert not db.scalar(select(MemoryRevision.id))
    assert client.get(API+'/world-chats/'+session['id']).status_code==404
    from app.models import OperationPlan,Idempotency
    with SessionLocal() as db:
        for plan in db.scalars(select(OperationPlan).where(OperationPlan.payload['worldOperation'].astext=='world_character_chat')):
            assert not {'chatSnapshot','messages','message'}&set(plan.payload)
        for receipt in db.scalars(select(Idempotency).where(Idempotency.scope.like('play-controls:%'))):
            assert set(receipt.result)=={'worldId','deleted'}

@pytest.mark.parametrize('invalid',['', ' '*3])
def test_blank_messages_are_rejected_without_a_task(client,reader,profile,invalid):
    w,line=world(client);session=create_chat(client,w,line)
    with SessionLocal() as db:before=db.scalar(select(func.count()).select_from(Task))
    r=client.post(API+'/world-chats/'+session['id']+'/send',json={'message':invalid,'turnId':'blank'},headers={'Idempotency-Key':'blank'});assert r.status_code==422
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(Task))==before
