import json
from unittest.mock import Mock, patch
import pytest
from app.errors import Problem
from app.db import SessionLocal
from app.world.models import World, WorldCheckpoint
from app.world.schemas import DirectorOutput
from app.world.worker import process
from app.world.director_receipt import missing_document_receipts, director_proposal
from .test_scenes import scene_world


def fixture_state():
    return {'playerId':'p','entities':{'p':{'inventory':{}},'npc':{'inventory':{}}}}, {'entities':[
        {'id':'p','name':'萧炎','type':'character'}, {'id':'npc','name':'谷尼','type':'character'}]}


def test_delivered_document_requires_a_real_item_transfer():
    state, definition = fixture_state()
    plan = {'publicOutcome':'谷尼出具署名鉴定书交予萧炎，分文未取。'}
    assert missing_document_receipts(plan,state,definition)
    for value in ['谷尼尚未写好鉴定书交给你。','谷尼准备写好鉴定书交给你。','若谷尼写好鉴定书交给你，可以带回去。']:
        assert not missing_document_receipts({'publicOutcome':value},state,definition)
    plan['additions']=[{'id':'cert','type':'item','name':'筑基灵液鉴定书'}]
    assert missing_document_receipts(plan,state,definition)
    plan['transfers']=[{'itemId':'cert','fromId':None,'toId':'p','quantity':1}]
    assert not missing_document_receipts(plan,state,definition)


def test_existing_document_handoff_is_also_checked():
    state,definition=fixture_state()
    definition['entities'].append({'id':'cert','type':'item','name':'鉴定书'})
    state['entities']['p']['inventory']['cert']=1
    plan={'publicOutcome':'你把鉴定书交给谷尼。'}
    assert missing_document_receipts(plan,state,definition)
    plan['transfers']=[{'itemId':'cert','fromId':'p','toId':'npc','quantity':1}]
    assert not missing_document_receipts(plan,state,definition)


def test_complete_channel_inner_quotes_are_preserved_without_guessing_speakers_or_truncation():
    from app.scenes.dialogue import structured_segments
    raw='{"kind":"narration","text":"末尾落款"米特尔谷尼"并盖上私印。"}\n{"kind":"speech","speakerId":"g","text":"他说"拿好"。"}'
    parts=structured_segments(raw,[{'id':'g','name':'谷尼'}])
    assert parts[0]['text']=='末尾落款"米特尔谷尼"并盖上私印。'
    assert parts[1]['text']=='他说"拿好"。' and parts[1]['speakerId']=='g'
    for bad in ['{"kind":"narration","text":"未完成', '{"kind":"speech","speakerId":"absent","text":"你说"拿好"。"}', '{"kind":"narration","text":"正文", "hidden":"secret"} trailing']:
        with pytest.raises(Problem):structured_segments(bad,[{'id':'g','name':'谷尼'}])


def test_complete_invalid_schema_has_one_bounded_repair():
    state,definition=fixture_state()
    valid=DirectorOutput(title='决定',elapsedMinutes=1,timeLabel='清晨',publicOutcome='你决定回家。').model_dump()
    call=Mock(side_effect=[{'text':'{"publicOutcome":""}','finishReason':'stop'}, {'text':json.dumps(valid),'finishReason':'stop'}])
    result=director_proposal('t',1,{},definition,state,4096,call)
    assert result['repairAttempted'] and result['plan']['publicOutcome']=='你决定回家。'
    assert call.call_count==2
    assert call.call_args_list[1].args[1]=='analysis:director-repair:1'


def test_repair_identifies_invalid_option_kind_without_applying_it():
    state, definition = fixture_state()
    valid = DirectorOutput(title='返回', elapsedMinutes=1, timeLabel='清晨',
                           publicOutcome='办理已经结束。', options=[{'text':'返回寓所','kind':'action'}]).model_dump()
    invalid = {**valid, 'options':[{'text':'返回寓所','kind':'travel'}]}
    call = Mock(side_effect=[{'text':json.dumps(invalid),'finishReason':'stop'},
                            {'text':json.dumps(valid),'finishReason':'stop'}])
    result = director_proposal('t', 1, {}, definition, state, 4096, call)
    assert result['plan']['options'][0]['kind'] == 'action'
    repair = call.call_args_list[1].args[3]
    assert any('options.0.kind' in issue and 'action' in issue for issue in repair['repairIssues'])
    assert call.call_count == 2


@pytest.mark.parametrize('mode',['unknown','incomplete'])
def test_uncertain_or_incomplete_provider_response_is_not_retried(mode):
    state,definition=fixture_state()
    call=Mock(side_effect=Problem('PROVIDER_STATUS_UNKNOWN','未知')) if mode=='unknown' else Mock(return_value={'text':'{','finishReason':'length'})
    with pytest.raises(Problem):director_proposal('t',1,{},definition,state,4096,call)
    assert call.call_count==1


@pytest.mark.parametrize('repair_ok',[True,False])
def test_executor_repairs_receipt_before_narration_or_preserves_draft(client,scene_world,profile,repair_ok):
    w=scene_world
    with SessionLocal() as db:
        world=db.get(World,w['world']);world.source={**world.source,'blocks':[],'chapters':[],'title':'独立测试','characterRefs':[]};db.commit()
    root='/api/world/v1'+w['root']
    head=client.get(root).json()['head'];player=head['state']['playerId']
    response=client.post(root+'/actions',json={'expectedCheckpointId':w['cp'],'turnId':'document','kind':'action','message':'请写好鉴定书交给我'},headers={'Idempotency-Key':'document'})
    assert response.status_code==202,response.text
    initial=DirectorOutput(title='鉴定',elapsedMinutes=2,timeLabel='清晨',publicOutcome='谷尼写好署名鉴定书交给你。').model_dump()
    repaired={**initial,'additions':[{'id':'NEW_cert','name':'署名鉴定书','type':'item','source':'game'}],
              'transfers':[{'itemId':'NEW_cert','fromId':None,'toId':player,'quantity':1,'reason':'出具文书交付玩家','visibleTo':['player']}]} if repair_ok else initial
    def call(task,stage,prompt,data,limit):
        if prompt=='world_director':return {'text':json.dumps(repaired if 'repair' in stage else initial),'finishReason':'stop'}
        if prompt=='world_narrator':return {'text':'你收好鉴定书。','finishReason':'stop'}
        return {'text':'{"consistent":true,"issues":[]}','finishReason':'stop'}
    with patch('app.world.worker.call_provider',side_effect=call) as calls:process(response.json()['taskId'])
    task=client.get('/api/world/v1/tasks/'+response.json()['taskId']).json()
    final=client.get(root).json()['head']
    assert calls.call_count==(4 if repair_ok else 3)
    if repair_ok:
        assert task['state']=='succeeded',task
        cert=next(e for e in final['state']['entities'] if e['name']=='署名鉴定书')
        with SessionLocal() as db:
            assert db.get(WorldCheckpoint,final['id']).state['entities'][player]['inventory'][cert['id']]==1
        assert final['id']!=w['cp']
    else:
        assert task['errorCode']=='WORLD_DOCUMENT_RECEIPT_INVALID'
        assert final['id']==w['cp']
        assert task['stream']['text']=='你收好鉴定书。'
