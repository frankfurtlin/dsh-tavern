import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.domain import digest
from app.world.receipts import reusable_narrator, reusable_director


@pytest.mark.parametrize('mode',['complete','unknown','different-prompt','different-author-prompts','missing-prompt','sent-review','incomplete','bad-hash','conflicting-prose'])
def test_only_known_complete_prose_with_unsent_review_is_reusable(tmp_path,monkeypatch,mode):
    result={'text':'已收到完整正文。','finishReason':'stop'}
    frozen={'version':'world-narrator-v6','hash':'frozen'}
    old=SimpleNamespace(id='previous',owner_id='owner',error_code='PROVIDER_CONNECTION_FAILED',provider_state='not_sent',
        checkpoint={'executionPrompts':{'world_narrator':frozen},'calls':{'generation:scene:1':{'callId':'call'}}})
    task=SimpleNamespace(owner_id='owner',checkpoint={'executionPrompts':{'world_narrator':frozen}})
    review=SimpleNamespace(status='not_sent')
    if mode=='unknown':old.provider_state='unknown'
    if mode=='different-prompt':task.checkpoint={'executionPrompts':{'world_narrator':{'hash':'changed'}}}
    if mode=='different-author-prompts':task.checkpoint['authorPrompts']=[{'text':'使用不同风格','sha256':'different'}]
    if mode=='missing-prompt':old.checkpoint['executionPrompts']={};task.checkpoint={'executionPrompts':{}}
    if mode=='sent-review':review.status='unknown'
    if mode=='incomplete':result['finishReason']='length'
    if mode=='conflicting-prose':old.error_code='WORLD_NARRATIVE_CONFLICT'
    path=tmp_path/'received.json';path.write_text(json.dumps({'result':result,'hash':'bad' if mode=='bad-hash' else digest(json.dumps(result,ensure_ascii=False,sort_keys=True))}),'utf-8')
    monkeypatch.setattr('app.world.receipts.private_path',lambda *args:path)
    db=Mock();db.get.return_value=old;db.scalar.return_value=review
    assert reusable_narrator(db,task,'previous')==(result if mode=='complete' else None)


@pytest.mark.parametrize('mode',['valid','wrong-owner','wrong-parent','bad-receipt'])
def test_cached_director_retry_keeps_latest_prose_receipt_and_validates_origin(tmp_path,monkeypatch,mode):
    from app.world.schemas import DirectorOutput
    payload={'worldId':'world','lineId':'line','parentCheckpointId':'parent'}
    plan=DirectorOutput(title='登记结果',elapsedMinutes=30,timeLabel='上午',publicOutcome='办理结束。').model_dump()
    task=SimpleNamespace(id='current',owner_id='owner',task_type='world_turn',payload=payload)
    old=SimpleNamespace(id='previous',owner_id='owner',payload=payload,
        error_code='PROVIDER_CONNECTION_FAILED',provider_state='not_sent',checkpoint={})
    origin=SimpleNamespace(id='origin',owner_id='owner',payload=dict(payload),
        checkpoint={'calls':{'analysis:director:1':{'callId':'call'}}})
    if mode=='wrong-owner':origin.owner_id='another-owner'
    if mode=='wrong-parent':origin.payload['parentCheckpointId']='another-parent'
    turn=SimpleNamespace(world_id='world',line_id='line',action='办理登记',kind='action')
    old_turn=SimpleNamespace(task_id='previous',plan={'1':{'plan':plan,'reusedDirectorTaskId':'origin'}})
    result={'text':json.dumps(plan),'finishReason':'stop'}
    path=tmp_path/'received.json'
    path.write_text(json.dumps({'result':result,'hash':'bad' if mode=='bad-receipt' else digest(json.dumps(result,ensure_ascii=False,sort_keys=True))}),'utf-8')
    monkeypatch.setattr('app.world.receipts.private_path',lambda *args:path)
    db=Mock();db.scalars.return_value=[old_turn];db.scalar.return_value=old_turn
    db.get.side_effect=lambda model,key:{'previous':old,'origin':origin}[key]
    reusable=reusable_director(db,task,turn,SimpleNamespace(id='parent'),1)
    if mode=='valid':
        assert reusable['plan']==plan
        assert reusable['reusedDirectorTaskId']=='previous','The latest completed prose belongs to the retry, not the original director request'
    else:assert reusable is None
