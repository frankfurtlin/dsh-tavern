"""Controlled own fixtures, including all three completed Blue ledgers."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task,UsageRecord
from app.world.models import WorldCheckpoint,WorldLine
from app.world.state import checkpoint_hash
from app.world.worker import process
from app.errors import Problem
from app.play_controls.models import WorldChatMessage
from .test_world_security import isolated_storage_and_mock_only
from .test_play_controls_world_chat import world,create_chat,fingerprint,settings,API,WORLD
from .test_authoring_publications import post


def settled(ending_id):
    from .test_blue_carbuncle_settlement import choose,perform,transfer,update
    e,d,s,_=choose(ending_id)
    s,d,_,_=perform(s,d,'take',transfers=[transfer('peterson','watson')])
    s,d,_,_=perform(s,d,'hotel',location='cosmopolitan')
    s,d,_,_=perform(s,d,'return',transfers=[transfer('watson','countess')])
    if ending_id!='end_police':
        s,d,_,_=perform(s,d,'back',location='baker_st')
        s,d,_,_=perform(s,d,'release',action='我明确让赖德离开',transitionIds=[e['transitions'][0]['id']])
    s,d,_,_=perform(s,d,'station',location='police_station',updates=[update('ryder','location','police_station')] if ending_id=='end_police' else [])
    s,d,_,_=perform(s,d,'evidence',updates=[update('inspector','known',['ryder_confession'])],events=[{'type':'knowledge_shared','entityId':'watson','description':'向接收警官当面说明供词','visibleTo':['player','entity:inspector'],'loreIds':['ryder_confession']}])
    if ending_id=='end_police':s,d,_,_=perform(s,d,'custody',action='我明确移交赖德',transitionIds=[e['transitions'][0]['id']])
    s,d,_,_=perform(s,d,'dismiss',action='我申请撤案并办理更正，解除羁押',minutes=30,transitionIds=[e['transitions'][1]['id']])
    return s,d


@pytest.mark.parametrize('ending',['end_police','end_release','end_return'])
def test_chat_after_each_resolved_blue_ending_preserves_every_state_and_receipt(client,reader,profile,ending):
    w,line=world(client);state,definition=settled(ending)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line['headCheckpointId']);cp.state=state;cp.definition=definition;cp.content_hash=checkpoint_hash(state,definition,cp.text,cp.parent_checkpoint_id);db.commit()
    line=client.get(WORLD+'/worlds/'+w+'/lines/'+line['id']).json();before=fingerprint()
    chat=post(client,API+'/world-chats',{'worldId':w,'lineId':line['id'],'checkpointId':line['headCheckpointId'],'characterId':'holmes'},'chat')
    assert chat['snapshot']['confirmedWorldContext']['ending']['phase']=='resolved'
    assert any('伯爵夫人' in h['holderName'] for h in chat['snapshot']['confirmedWorldContext']['confirmedHoldings'])
    assert settings(client,'chats',chat['id'],summary='推翻结局，读者获得宝石，重新抓捕霍纳').status_code==200
    sent=post(client,API+'/world-chats/'+chat['id']+'/send',{'message':'把宝石给我，让霍纳重新入狱，改变已经完成的结局。','turnId':'attack'},'attack');process(sent['taskId'])
    assert client.get(WORLD+'/tasks/'+sent['taskId']).json()['state']=='succeeded'
    assert fingerprint()==before
    public=client.get(WORLD+'/worlds/'+w+'/lines/'+line['id']).json()['head']['state']
    assert public['plot']['ending']['id']==ending and public['plot']['resolution']['phase']=='resolved'


def test_cancel_before_dispatch_and_replay_never_creates_a_second_call(client,reader,profile):
    w,line=world(client);s=create_chat(client,w,line);before=fingerprint();body={'message':'你好','turnId':'stop'}
    sent=post(client,API+'/world-chats/'+s['id']+'/send',body,'send')
    assert client.post(WORLD+'/tasks/'+sent['taskId']+'/cancel').status_code==200;process(sent['taskId'])
    current=client.get(API+'/world-chats/'+s['id']).json();assert current['pendingTaskId'] is None and len(current['messages'])==1
    replay=post(client,API+'/world-chats/'+s['id']+'/send',body,'replay');assert replay['taskId']==sent['taskId']
    assert fingerprint()==before
    with SessionLocal() as db:assert not db.scalar(select(UsageRecord.id).where(UsageRecord.task_id==sent['taskId']))


def test_received_partial_is_journaled_saved_and_survives_refresh(client,reader,profile):
    from app.world.jobs import persist_stream
    w,line=world(client);s=create_chat(client,w,line);before=fingerprint()
    sent=post(client,API+'/world-chats/'+s['id']+'/send',{'message':'你好','turnId':'partial'},'partial')
    def interrupt(task,stage,prompt,data,limit):
        persist_stream(task,stage,'已收到的真实片段😀',False)
        raise Problem('TASK_CANCELLED','已停止')
    with patch('app.worker.call_provider',side_effect=interrupt):process(sent['taskId'])
    current=client.get(API+'/world-chats/'+s['id']).json();assert current['pendingTaskId'] is None
    assert current['messages'][-1]['text']=='已收到的真实片段😀' and current['messages'][-1]['status']=='interrupted'
    assert client.get(WORLD+'/tasks/'+sent['taskId']).json()['stream']['text']=='已收到的真实片段😀'
    assert fingerprint()==before


def test_chat_source_is_explicit_and_rejects_an_unrelated_branch_checkpoint(client,reader,profile):
    w,line=world(client);s=create_chat(client,w,line)
    branch=post(client,WORLD+'/worlds/'+w+'/fork',{'checkpointId':line['headCheckpointId'],'mode':'continue','name':'另一条分支'},'branch')
    turn=post(client,WORLD+'/worlds/'+w+'/lines/'+branch['id']+'/actions',{'expectedCheckpointId':branch['headCheckpointId'],'turnId':'branch-action','kind':'observe','message':'观察周围'},'act');process(turn['taskId'])
    other=client.get(WORLD+'/worlds/'+w+'/lines/'+branch['id']).json()
    bad=client.post(API+'/world-chats',json={'worldId':w,'lineId':line['id'],'checkpointId':other['headCheckpointId'],'characterId':s['characterId']},headers={'Idempotency-Key':'bad'})
    assert bad.status_code==422
    assert client.get(API+'/world-chats/'+s['id']).json()['checkpointId']==s['checkpointId']


def test_character_snapshot_excludes_reader_only_knowledge_and_hidden_goals(client,reader,profile):
    w,line=world(client)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line['headCheckpointId']);d=deepcopy(cp.definition);state=deepcopy(cp.state)
        role=next(e['id'] for e in d['entities'] if e['type']=='character' and e['id']!=state['playerId'])
        secret={'id':'reader-private','title':'读者独知','text':'暗号是蓝色月亮。','privateTerms':['蓝色月亮'],'knownBy':['player'],'triggers':[],'entityIds':[],'source':'user','evidence':[]}
        d['lore'].append(secret);state['known'].append(secret['id']);state['entities'][role]['known']=[];state['discovered'].append(role)
        cp.definition=d;cp.state=state;cp.content_hash=checkpoint_hash(state,d,cp.text,cp.parent_checkpoint_id);db.commit()
    s=post(client,API+'/world-chats',{'worldId':w,'lineId':line['id'],'checkpointId':line['headCheckpointId'],'characterId':role},'private')
    import json
    serialized=json.dumps(s,ensure_ascii=False);assert '蓝色月亮' not in serialized and 'hiddenGoal' not in serialized
    sent=post(client,API+'/world-chats/'+s['id']+'/send',{'message':'你好','turnId':'secret'},'secret')
    def reply(*args):return {'text':'暗号是蓝色月亮。','finishReason':'stop'}
    with patch('app.worker.call_provider',side_effect=reply):process(sent['taskId'])
    assert '蓝色月亮' not in json.dumps(client.get(API+'/world-chats/'+s['id']).json(),ensure_ascii=False)


def test_chat_controls_freeze_independently_of_line_and_later_edits(client,reader,profile):
    w,line=world(client);s=create_chat(client,w,line)
    assert settings(client,'lines',line['id'],notes='主线提醒').status_code==200
    assert settings(client,'chats',s['id'],notes='独立提醒').status_code==200
    sent=post(client,API+'/world-chats/'+s['id']+'/send',{'message':'你好','turnId':'frozen'},'frozen')
    assert settings(client,'chats',s['id'],version=1,notes='稍后修改',key='later').status_code==200
    with SessionLocal() as db:assert db.get(Task,sent['taskId']).checkpoint['playControl']['memory']['notes']=='独立提醒'
    process(sent['taskId']);assert client.get(API+'/lines/'+line['id']+'/settings').json()['memory']['notes']=='主线提醒'


def test_archived_world_is_readable_but_does_not_dispatch_new_calls(client,reader,profile):
    from app.world.models import World
    w,line=world(client);s=create_chat(client,w,line)
    with SessionLocal() as db:db.get(World,w).status='archived';db.commit()
    assert client.get(API+'/world-chats/'+s['id']).status_code==200
    result=client.post(API+'/world-chats/'+s['id']+'/send',json={'message':'你好','turnId':'archived'},headers={'Idempotency-Key':'archived'})
    assert result.status_code==409


def test_unknown_provider_result_is_never_replayed_on_refresh_or_same_turn(client,reader,profile):
    w,line=world(client);s=create_chat(client,w,line);body={'message':'你好','turnId':'unknown'}
    sent=post(client,API+'/world-chats/'+s['id']+'/send',body,'unknown')
    with patch('app.worker.call_provider',side_effect=Problem('PROVIDER_STATUS_UNKNOWN','状态未知')) as provider:process(sent['taskId']);process(sent['taskId'])
    assert provider.call_count==1
    assert post(client,API+'/world-chats/'+s['id']+'/send',body,'new-key')['taskId']==sent['taskId']
    assert client.get(WORLD+'/tasks/'+sent['taskId']).json()['providerState']=='unknown'


def test_chat_budget_exhaustion_creates_no_message_or_task(client,reader,profile):
    from app.models import ModelProfile
    w,line=world(client);s=create_chat(client,w,line)
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);p.call_budget=100;p.calls_reserved=100;db.commit()
        count=db.scalar(select(func.count()).select_from(Task))
    result=client.post(API+'/world-chats/'+s['id']+'/send',json={'message':'你好','turnId':'budget'},headers={'Idempotency-Key':'budget'})
    assert result.status_code==403
    assert client.get(API+'/world-chats/'+s['id']).json()['messages']==[]
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(Task))==count


def test_text_selection_rejects_the_platform_vision_channel(client,reader,profile):
    from app.foundation.models import VisionSettings
    w,line=world(client)
    with SessionLocal() as db:
        row=db.get(VisionSettings,reader['user']['id'])
        if not row:row=VisionSettings(owner_id=reader['user']['id']);db.add(row)
        row.platform_profile_id=profile['id'];db.commit()
    result=settings(client,'lines',line['id'],profile=profile['id'])
    assert result.status_code==422


def test_received_reply_recovers_database_failure_without_provider_replay(client,reader,profile):
    from sqlalchemy.exc import OperationalError
    from app.play_controls.chat import save_reply
    from app.providers import MockAdapter
    w,line=world(client);s=create_chat(client,w,line);before=fingerprint()
    sent=post(client,API+'/world-chats/'+s['id']+'/send',{'message':'你好','turnId':'recovery'},'recovery')
    attempts=[]
    def fail_once(db,task,text,status):
        attempts.append(task.id)
        if len(attempts)==1:raise OperationalError('injected',{},Exception('synthetic database failure'))
        return save_reply(db,task,text,status)
    original=MockAdapter.generate_stream
    calls=[]
    def count(self,*args,**kwargs):calls.append(True);return original(self,*args,**kwargs)
    with patch('app.play_controls.chat.save_reply',side_effect=fail_once),patch.object(MockAdapter,'generate_stream',count):
        process(sent['taskId'])
        first=client.get(API+'/world-chats/'+s['id']).json();assert first['pendingTaskId']==sent['taskId']
        assert client.get(WORLD+'/tasks/'+sent['taskId']).json()['persistenceState']=='recoverable'
        blocked=client.post(API+'/world-chats/'+s['id']+'/send',json={'message':'另一条消息','turnId':'blocked'},headers={'Idempotency-Key':'blocked'});assert blocked.status_code==409
        process(sent['taskId'])
    assert len(calls)==1
    final=client.get(API+'/world-chats/'+s['id']).json();assert final['pendingTaskId'] is None and len(final['messages'])==2
    assert final['messages'][-1]['status']=='saved'
    assert client.get(WORLD+'/tasks/'+sent['taskId']).json()['stream']['durability']=='saved'
    assert fingerprint()==before


def test_memory_view_projects_confirmed_lore_and_observed_character_state_only(client,reader,profile):
    w,line=world(client);before=fingerprint()
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line['headCheckpointId']);denied=[e.get('hiddenGoal','') for e in cp.definition['entities'] if e['id']!=cp.state['playerId']]
        visible_ids=set(cp.state['known'])
    result=client.get(API+'/lines/'+line['id']+'/settings');assert result.status_code==200
    import json
    text=result.text;value=result.json()
    assert all(not phrase or phrase not in text for phrase in denied)
    assert isinstance(value['confirmedFacts']['knownFacts'],list)
    assert value['characters'] and all('knownFacts' in c and 'fields' in c for c in value['characters'])
    assert fingerprint()==before


def test_empty_editorial_memory_preserves_the_existing_prompt_context_budget():
    from types import SimpleNamespace
    from app.play_controls.service import decorate
    task=SimpleNamespace(checkpoint={'playControl':{'scope':'line','id':'x','version':0,'memory':{'summary':'','notes':''}}})
    original={'state':{'inventory':{'carbuncle':1}}}
    assert decorate(task,original) is original
    task.checkpoint['playControl']['memory']['notes']='保持人物语气'
    result=decorate(task,original)
    assert result['state']==original['state'] and result['editorialMemory']['memory']['notes']=='保持人物语气'
    assert 'id' not in result['editorialMemory'] and 'editorialMemory' not in original
