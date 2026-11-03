from copy import deepcopy
import pytest
from app.play_controls.attribution import participants
from app.db import SessionLocal
from app.world.models import WorldCheckpoint
from .test_world_security import isolated_storage_and_mock_only
from .test_world_chat_lifecycle import settled
from .test_play_controls_world_chat import world,create_chat,API,WORLD,fingerprint
from .test_authoring_publications import post


@pytest.mark.parametrize('ending',['end_police','end_release','end_return'])
def test_confirmed_actions_distinguish_player_handoff_and_character_authority(ending):
    state,definition=settled(ending)
    value=participants(state,definition,'holmes')
    assert value['player']['name']=='华生' and not value['player']['isCharacter']
    handoff=value['itemTransfers'][-1]
    assert handoff['from']['name']=='华生' and not handoff['from']['isCharacter']
    assert '伯爵夫人' in handoff['to']['name']
    assert value['decisions'] and any('警' in d['authority']['name'] for d in value['decisions'])
    assert participants(state,definition,'watson')['itemTransfers'][-1]['from']['isCharacter']
    hidden=deepcopy(state);hidden['plot']['transfers'][-1]['visibleTo']=['ryder']
    assert len(participants(hidden,definition,'holmes')['itemTransfers'])==len(value['itemTransfers'])-1


def test_legacy_chat_enriches_original_checkpoint_without_advancing_source(client,reader,profile):
    from app.play_controls.models import WorldChatSession
    w,line=world(client);s=create_chat(client,w,line)
    with SessionLocal() as db:
        chat=db.get(WorldChatSession,s['id']);old=deepcopy(chat.snapshot);old.pop('participants');chat.snapshot=old;db.commit()
    before=fingerprint()
    sent=post(client,API+'/world-chats/'+s['id']+'/send',{'message':'这是你亲手做的吗？','turnId':'legacy'},'legacy')
    from app.models import Task
    with SessionLocal() as db:
        source=db.get(Task,sent['taskId']).payload['chatSnapshot']
        assert source['sourceCheckpointId']==s['checkpointId'] and source['participants']['character']['name']==s['characterName']
    assert fingerprint()==before


def test_evidence_handoff_never_confuses_the_sharing_player_with_chat_character():
    from types import SimpleNamespace
    state,definition=settled('end_return')
    events=[SimpleNamespace(type='knowledge_shared',entity_id='watson',visible_to=['player'],data={'description':'向警官说明供词'}),
            SimpleNamespace(type='knowledge_shared',entity_id='ryder',visible_to=['ryder'],data={'description':'私下密谈'})]
    facts=participants(state,definition,'holmes',events)['evidenceActions']
    assert len(facts)==1 and facts[0]['actor']=={'name':'华生','isPlayer':True,'isCharacter':False}


@pytest.mark.parametrize('review_passes',[True,False])
@pytest.mark.parametrize('generation_finish',['stop','length'])
def test_unverified_personal_claim_is_replaced_from_receipts_before_final_save(client,reader,profile,review_passes,generation_finish):
    import json
    from unittest.mock import patch
    from app.world.state import checkpoint_hash
    from app.world.worker import process
    w,line=world(client);state,definition=settled('end_police')
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line['headCheckpointId']);cp.state=state;cp.definition=definition;cp.content_hash=checkpoint_hash(state,definition,cp.text,cp.parent_checkpoint_id);db.commit()
    chat=post(client,API+'/world-chats',{'worldId':w,'lineId':line['id'],'checkpointId':line['headCheckpointId'],'characterId':'holmes'},'attribution-chat')
    before=fingerprint();sent=post(client,API+'/world-chats/'+chat['id']+'/send',{'message':'是谁归还宝石？','turnId':'claim'},'claim')
    def provider(task,stage,prompt,data,limit):
        return {'text':json.dumps({'consistent':review_passes,'reason':'injected semantic verdict'}) if stage=='validation' else '我亲手把蓝宝石交给了伯爵夫人。','finishReason':'stop' if stage=='validation' else generation_finish}
    with patch('app.worker.call_provider',side_effect=provider) as calls:process(sent['taskId'])
    assert calls.call_count==2
    result=client.get(API+'/world-chats/'+chat['id']).json()['messages'][-1]['text']
    assert '华生' in result and '我亲手把蓝宝石交给' not in result
    assert client.get(API+'/world-chats/'+chat['id']).json()['messages'][-1]['status']=='saved'
    from app.models import Task
    with SessionLocal() as db:assert db.get(Task,sent['taskId']).result_outcome=='complete'
    assert fingerprint()==before


def test_participation_questions_use_receipts_but_daily_and_hypothetical_chat_stays_free():
    from app.play_controls.attribution import requests_participation
    assert requests_participation('你亲手归还过吗？实际是谁交接的？')
    assert requests_participation('你本人说明过供词吗？')
    assert not requests_participation('如果你亲自归还，会有什么感受？')
    assert not requests_participation('你亲手做早餐吗？')
